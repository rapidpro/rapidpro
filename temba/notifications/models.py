import logging
from abc import abstractmethod

from django.contrib.auth.models import User
from django.db import models
from django.db.models import Q
from django.urls import reverse
from django.utils import timezone
from django.utils.functional import cached_property

from temba.channels.models import Channel
from temba.contacts.models import ContactImport, ExportContactsTask
from temba.flows.models import ExportFlowResultsTask
from temba.msgs.models import ExportMessagesTask
from temba.orgs.models import Org
from temba.utils.email import send_template_email
from temba.utils.models import SquashableModel

logger = logging.getLogger(__name__)


class NotificationType:
    slug = None
    email_subject = None
    email_template = None

    @abstractmethod
    def get_target_url(self, notification) -> str:  # pragma: no cover
        pass

    def get_email_template(self, notification) -> tuple:  # pragma: no cover
        """
        For types that support sending as email, this should return subject and template name
        """
        return ("", "")

    def get_email_context(self, notification):
        return {
            "org": notification.org,
            "user": notification.user,
            "target_url": self.get_target_url(notification),
        }

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


class ExportFinishedNotificationType(NotificationType):
    slug = "export:finished"

    def get_target_url(self, notification) -> str:
        return notification.export.get_download_url()

    def get_email_template(self, notification) -> tuple:
        export_type = notification.export.notification_export_type
        return f"Your {export_type} export is ready", f"notifications/email/export_finished.{export_type}"

    def get_email_context(self, notification):
        context = super().get_email_context(notification)
        if notification.results_export:
            context["flows"] = notification.results_export.flows.order_by("name")
        return context

    def as_json(self, notification) -> dict:
        json = super().as_json(notification)
        json["export"] = {"type": notification.export.notification_export_type}
        return json


class ImportFinishedNotificationType(NotificationType):
    slug = "import:finished"

    def get_target_url(self, notification) -> str:
        return reverse("contacts.contactimport_read", args=[notification.contact_import.id])

    def as_json(self, notification) -> dict:
        json = super().as_json(notification)
        json["import"] = {"type": "contact", "num_records": notification.contact_import.num_records}
        return json


class TicketsOpenedNotificationType(NotificationType):
    slug = "tickets:opened"

    def get_target_url(self, notification) -> str:
        return "/ticket/unassigned/"


class TicketActivityNotificationType(NotificationType):
    slug = "tickets:activity"

    def get_target_url(self, notification) -> str:
        return "/ticket/mine/"


TYPES_BY_SLUG = {lt.slug: lt() for lt in NotificationType.__subclasses__()}


class Notification(models.Model):
    """
    A user specific notification
    """

    EMAIL_STATUS_PENDING = "P"
    EMAIL_STATUS_SENT = "S"
    EMAIL_STATUS_NONE = "N"
    EMAIL_STATUS_CHOICES = (
        (EMAIL_STATUS_PENDING, "Pending"),
        (EMAIL_STATUS_SENT, "Sent"),
        (EMAIL_STATUS_NONE, "None"),
    )

    id = models.BigAutoField(primary_key=True)
    org = models.ForeignKey(Org, on_delete=models.PROTECT, related_name="notifications")
    notification_type = models.CharField(max_length=16, null=True)

    # The scope is what we maintain uniqueness of unseen notifications for within an org. For some notification types,
    # user can only have one unseen of that type per org, and so this will be an empty string. For other notification
    # types like channel alerts, it will be the UUID of an object.
    scope = models.CharField(max_length=36)

    user = models.ForeignKey(User, on_delete=models.PROTECT, related_name="notifications")
    is_seen = models.BooleanField(default=False)
    email_status = models.CharField(choices=EMAIL_STATUS_CHOICES, max_length=1, default=EMAIL_STATUS_NONE)
    created_on = models.DateTimeField(default=timezone.now)

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

    @classmethod
    def channel_alert(cls, alert):
        """
        Creates a new channel alert notification for each org admin if there they don't already have an unread one for
        the channel.
        """
        org = alert.channel.org
        cls._create_all(
            org,
            ChannelAlertNotificationType.slug,
            scope=str(alert.channel.uuid),
            users=org.get_admins(),
            channel=alert.channel,
        )

    @classmethod
    def export_finished(cls, export):
        """
        Creates an export finished notification for the creator of the given export.
        """

        cls._create_all(
            export.org,
            ExportFinishedNotificationType.slug,
            scope=export.get_notification_scope(),
            users=[export.created_by],
            email_status=cls.EMAIL_STATUS_PENDING,
            **{export.notification_export_type + "_export": export},
        )

    @classmethod
    def _create_all(cls, org, notification_type: str, *, scope: str, users, **kwargs):
        for user in users:
            cls.objects.get_or_create(
                org=org,
                notification_type=notification_type,
                scope=scope,
                user=user,
                is_seen=False,
                defaults=kwargs,
            )

    def send_email(self):
        subject, template = self.type.get_email_template(self)
        context = self.type.get_email_context(self)

        if subject and template:
            send_template_email(
                self.user.email,
                f"[{self.org.name}] {subject}",
                template,
                context,
                self.org.get_branding(),
            )
        else:  # pragma: no cover
            logger.warning(f"skipping email send for notification type {self.type.slug} not configured for email")

        self.email_status = Notification.EMAIL_STATUS_SENT
        self.save(update_fields=("email_status",))

    @classmethod
    def mark_seen(cls, org, notification_type: str, *, scope: str, user):
        cls.objects.filter(
            org_id=org.id, notification_type=notification_type, scope=scope, user=user, is_seen=False
        ).update(is_seen=True)

    @classmethod
    def get_unseen_count(cls, org: Org, user: User) -> int:
        return NotificationCount.get_total(org, user)

    @cached_property
    def export(self):
        return self.contact_export or self.message_export or self.results_export

    @property
    def type(self):
        return TYPES_BY_SLUG[self.notification_type]

    def as_json(self) -> dict:
        return self.type.as_json(self)

    class Meta:
        indexes = [
            # used to list org specific notifications for a user
            models.Index(fields=["org", "user", "-created_on"]),
            # used to find notifications with pending email sends
            models.Index(name="notifications_email_pending", fields=["created_on"], condition=Q(email_status="P")),
        ]
        constraints = [
            # used to check if we already have existing unseen notifications for something or to clear unseen
            # notifications when visiting their target URL
            models.UniqueConstraint(
                name="notifications_unseen_of_type",
                fields=["org", "notification_type", "scope", "user"],
                condition=Q(is_seen=False),
            ),
        ]


class NotificationCount(SquashableModel):
    """
    A count of a user's unseen notifications in a specific org
    """

    squash_over = ("org_id", "user_id")

    org = models.ForeignKey(Org, on_delete=models.PROTECT, related_name="notification_counts")
    user = models.ForeignKey(User, on_delete=models.PROTECT, related_name="notification_counts")
    count = models.IntegerField(default=0)

    @classmethod
    def get_squash_query(cls, distinct_set):
        sql = """
            WITH deleted as (
                DELETE FROM %(table)s WHERE "org_id" = %%s AND "user_id" = %%s RETURNING "count"
            )
            INSERT INTO %(table)s("org_id", "user_id", "count", "is_squashed")
            VALUES (%%s, %%s, GREATEST(0, (SELECT SUM("count") FROM deleted)), TRUE);
            """ % {
            "table": cls._meta.db_table
        }

        return sql, (distinct_set.org_id, distinct_set.user_id) * 2

    @classmethod
    def get_total(cls, org: Org, user: User) -> int:
        return cls.sum(cls.objects.filter(org=org, user=user))
