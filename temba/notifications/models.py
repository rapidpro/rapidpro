from django.contrib.auth.models import User
from django.db import models
from django.db.models import Q
from django.urls import reverse
from django.utils import timezone

from temba.channels.models import Alert, Channel
from temba.contacts.models import ContactImport, ExportContactsTask
from temba.flows.models import ExportFlowResultsTask, FlowStart
from temba.msgs.models import Broadcast, ExportMessagesTask
from temba.orgs.models import Org
from temba.tickets.models import Ticket, TicketEvent


class NotificationType:
    slug = None

    def get_target_url(self, notification) -> str:
        return ""

    def as_json(self, notification) -> dict:
        return {
            "type": notification.type.slug,
            "created_on": notification.created_on.isoformat(),
            "target_url": self.get_target_url(notification),
            "is_seen": notification.is_seen,
        }


class ChannelAlertNotificationType(NotificationType):
    slug = "channel:alert"

    def get_target_url(self, notification) -> str:
        return reverse("channels.channel_read", kwargs={"uuid": notification.channel.uuid})

    def as_json(self, notification) -> dict:
        json = super().as_json(notification)
        json["channel"] = {"uuid": str(notification.channel.uuid), "name": notification.channel.name}
        return json


class ExportCompletedNotificationType(NotificationType):
    slug = "export:completed"

    def get_target_url(self, notification) -> str:
        return self._get_export(notification).get_download_url()

    def as_json(self, notification) -> dict:
        export = self._get_export(notification)

        json = super().as_json(notification)
        json["export"] = {"type": Notification.EXPORT_TYPES[type(export)]}
        return json

    @staticmethod
    def _get_export(notification):
        return notification.contact_export or notification.message_export or notification.results_export


class ImportCompletedNotificationType(NotificationType):
    slug = "import:completed"

    def get_target_url(self, notification) -> str:
        return reverse("contacts.contactimport_read", args=[notification.contact_import.id])

    def as_json(self, notification) -> dict:
        json = super().as_json(notification)
        json["import"] = {"num_records": notification.contact_import.num_records}
        return json


class TicketsOpenedNotificationType(NotificationType):
    slug = "tickets:opened"


class TicketActivityNotificationType(NotificationType):
    slug = "tickets:activity"


TYPES_BY_SLUG = {lt.slug: lt() for lt in NotificationType.__subclasses__()}


class Log(models.Model):
    """
    TODO drop
    """

    id = models.BigAutoField(primary_key=True)
    org = models.ForeignKey(Org, on_delete=models.PROTECT, related_name="logs")
    log_type = models.CharField(max_length=16)
    created_on = models.DateTimeField(default=timezone.now)
    created_by = models.ForeignKey(User, null=True, on_delete=models.PROTECT, related_name="logs")
    alert = models.ForeignKey(Alert, null=True, on_delete=models.PROTECT, related_name="logs")
    broadcast = models.ForeignKey(Broadcast, null=True, on_delete=models.PROTECT, related_name="logs")
    flow_start = models.ForeignKey(FlowStart, null=True, on_delete=models.PROTECT, related_name="logs")
    contact_import = models.ForeignKey(ContactImport, null=True, on_delete=models.PROTECT, related_name="logs")
    contact_export = models.ForeignKey(ExportContactsTask, null=True, on_delete=models.PROTECT, related_name="logs")
    message_export = models.ForeignKey(ExportMessagesTask, null=True, on_delete=models.PROTECT, related_name="logs")
    results_export = models.ForeignKey(ExportFlowResultsTask, null=True, on_delete=models.PROTECT, related_name="logs")
    ticket = models.ForeignKey(Ticket, null=True, on_delete=models.PROTECT, related_name="logs")
    ticket_event = models.ForeignKey(TicketEvent, null=True, on_delete=models.PROTECT, related_name="logs")


class Notification(models.Model):
    """
    A user specific notification
    """

    id = models.BigAutoField(primary_key=True)
    org = models.ForeignKey(Org, on_delete=models.PROTECT, related_name="notifications")
    user = models.ForeignKey(User, on_delete=models.PROTECT, related_name="notifications")
    notification_type = models.CharField(max_length=16, null=True)
    is_seen = models.BooleanField(default=False)
    created_on = models.DateTimeField(default=timezone.now)

    target_id = models.BigIntegerField(default=0)  # has to be zero if not used as PG treats each NULL as distinct
    channel = models.ForeignKey(Channel, null=True, on_delete=models.PROTECT, related_name="notifications")
    contact_export = models.ForeignKey(
        ExportContactsTask, null=True, on_delete=models.PROTECT, related_name="notifications"
    )
    message_export = models.ForeignKey(
        ExportMessagesTask, null=True, on_delete=models.PROTECT, related_name="notifications"
    )
    results_export = models.ForeignKey(
        ExportFlowResultsTask, null=True, on_delete=models.PROTECT, related_name="notifications"
    )
    contact_import = models.ForeignKey(
        ContactImport, null=True, on_delete=models.PROTECT, related_name="notifications"
    )

    EXPORT_TYPES = {ExportContactsTask: "contact", ExportMessagesTask: "message", ExportFlowResultsTask: "results"}

    @classmethod
    def channel_alert(cls, alert):
        """
        Creates a new channel alert notification for each org admin if there they don't already have an unread one for
        the channel.
        """
        org = alert.channel.org
        cls._create_all(
            org, ChannelAlertNotificationType.slug, org.get_admins(), target_id=alert.channel.id, channel=alert.channel
        )

    @classmethod
    def export_completed(cls, export):
        """
        Creates an export completed notification for the creator of the given export.
        """
        field_name = cls.EXPORT_TYPES[type(export)] + "_export"
        cls._create_all(
            export.org,
            ExportCompletedNotificationType.slug,
            [export.created_by],
            target_id=export.id,
            **{field_name: export},
        )

    @classmethod
    def _create_all(cls, org, notification_type: str, users, *, target_id: int, **kwargs):
        for user in users:
            cls.objects.get_or_create(
                org=org,
                user=user,
                notification_type=notification_type,
                target_id=target_id,
                is_seen=False,
                defaults=kwargs,
            )

    @property
    def type(self):
        return TYPES_BY_SLUG[self.notification_type]

    def as_json(self) -> dict:
        return self.type.as_json(self)

    class Meta:
        indexes = [
            # used to list org specific notifications for a user
            models.Index(fields=["org", "user", "-created_on"]),
            # used to check if we already have existing unseen notifications for something
            models.Index(
                name="notifications_unseen_of_type",
                fields=["org", "user", "notification_type", "target_id"],
                condition=Q(is_seen=False),
            ),
        ]
