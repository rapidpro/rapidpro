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
from temba.tickets.models import ExportTicketsTask
from temba.utils.email import send_template_email
from temba.utils.models import SquashableModel

logger = logging.getLogger(__name__)


class IncidentType:
    slug: str = None

    def as_json(self, incident) -> dict:
        return {
            "type": incident.incident_type,
            "started_on": incident.started_on.isoformat(),
            "ended_on": incident.ended_on.isoformat() if incident.ended_on else None,
        }


class OrgFlaggedIncidentType(IncidentType):
    """
    Org has been flagged due to suspicious activity
    """

    slug = "org:flagged"


class WebhooksUnhealthyIncidentType(IncidentType):
    """
    Webhook calls from flows have been taking too long to respond for a period of time.
    """

    slug = "webhooks:unhealthy"


INCIDENT_TYPES_BY_SLUG = {t.slug: t() for t in IncidentType.__subclasses__()}


class Incident(models.Model):
    """
    Models a problem with something in a workspace - e.g. a channel experiencing high error rates, webhooks in a flow
    experiencing poor response times.
    """

    id = models.BigAutoField(primary_key=True)
    org = models.ForeignKey(Org, on_delete=models.PROTECT, related_name="incidents")
    incident_type = models.CharField(max_length=20)

    # The scope is what we maintain uniqueness of ongoing incidents for within an org. For incident types with an
    # associated object, this will be the UUID of the object.
    scope = models.CharField(max_length=36)

    started_on = models.DateTimeField(default=timezone.now)
    ended_on = models.DateTimeField(null=True)

    channel = models.ForeignKey(Channel, null=True, on_delete=models.PROTECT, related_name="incidents")

    @classmethod
    def flagged(cls, org):
        """
        Creates a flagged incident if one is not already ongoing
        """
        return cls._create(org, OrgFlaggedIncidentType.slug, scope="")

    @classmethod
    def _create(cls, org, incident_type: str, *, scope: str, **kwargs):
        incident, created = cls.objects.get_or_create(
            org=org,
            incident_type=incident_type,
            scope=scope,
            ended_on=None,
            defaults=kwargs,
        )
        if created:
            Notification.incident_started(incident)
        return incident

    def end(self):
        """
        Ends this incident
        """
        self.ended_on = timezone.now()
        self.save(update_fields=("ended_on",))

    @property
    def template(self):
        return f"notifications/incidents/{self.incident_type.replace(':', '_')}.haml"

    @property
    def type(self):
        return INCIDENT_TYPES_BY_SLUG[self.incident_type]

    def as_json(self) -> dict:
        return self.type.as_json(self)

    class Meta:
        indexes = [
            # used to find ongoing incidents which may be ended
            models.Index(name="incidents_ongoing", fields=("incident_type",), condition=Q(ended_on=None)),
            # used to list an org's ongoing and ended incidents in the UI
            models.Index(name="incidents_org_ongoing", fields=("org", "-started_on"), condition=Q(ended_on=None)),
            models.Index(
                name="incidents_org_ended", fields=("org", "-started_on"), condition=Q(ended_on__isnull=False)
            ),
        ]
        constraints = [
            # used to check if we already have an existing ongoing incident for something
            models.UniqueConstraint(
                name="incidents_ongoing_scoped", fields=["org", "incident_type", "scope"], condition=Q(ended_on=None)
            ),
        ]


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


class IncidentStartedNotificationType(NotificationType):
    slug = "incident:started"

    def get_target_url(self, notification) -> str:
        return reverse("notifications.incident_list")

    def as_json(self, notification) -> dict:
        json = super().as_json(notification)
        json["incident"] = notification.incident.as_json()
        return json


class TicketsOpenedNotificationType(NotificationType):
    slug = "tickets:opened"

    def get_target_url(self, notification) -> str:
        return "/ticket/unassigned/"


class TicketActivityNotificationType(NotificationType):
    slug = "tickets:activity"

    def get_target_url(self, notification) -> str:
        return "/ticket/mine/"


NOTIFICATION_TYPES_BY_SLUG = {lt.slug: lt() for lt in NotificationType.__subclasses__()}


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
    notification_type = models.CharField(max_length=16)

    # The scope is what we maintain uniqueness of unseen notifications for within an org. For some notification types,
    # user can only have one unseen of that type per org, and so this will be an empty string. For other notification
    # types like channel alerts, it will be the UUID of an object.
    scope = models.CharField(max_length=36)

    user = models.ForeignKey(User, on_delete=models.PROTECT, related_name="notifications")
    is_seen = models.BooleanField(default=False)
    email_status = models.CharField(choices=EMAIL_STATUS_CHOICES, max_length=1, default=EMAIL_STATUS_NONE)
    created_on = models.DateTimeField(default=timezone.now)

    contact_export = models.ForeignKey(
        ExportContactsTask, null=True, on_delete=models.PROTECT, related_name="notifications"
    )
    message_export = models.ForeignKey(
        ExportMessagesTask, null=True, on_delete=models.PROTECT, related_name="notifications"
    )
    results_export = models.ForeignKey(
        ExportFlowResultsTask, null=True, on_delete=models.PROTECT, related_name="notifications"
    )
    ticket_export = models.ForeignKey(
        ExportTicketsTask, null=True, on_delete=models.PROTECT, related_name="notifications"
    )

    contact_import = models.ForeignKey(
        ContactImport, null=True, on_delete=models.PROTECT, related_name="notifications"
    )

    incident = models.ForeignKey(Incident, null=True, on_delete=models.PROTECT, related_name="notifications")

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
    def incident_started(cls, incident):
        """
        Creates an incident started notification for all admins in the workspace.
        """

        cls._create_all(
            incident.org,
            IncidentStartedNotificationType.slug,
            scope=str(incident.id),
            users=incident.org.get_admins(),
            email_status=cls.EMAIL_STATUS_NONE,  # TODO add email support
            incident=incident,
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
        notifications = cls.objects.filter(
            org_id=org.id, notification_type=notification_type, user=user, is_seen=False
        )

        if scope is not None:
            notifications = notifications.filter(scope=scope)

        notifications.update(is_seen=True)

    @classmethod
    def get_unseen_count(cls, org: Org, user: User) -> int:
        return NotificationCount.get_total(org, user)

    @cached_property
    def export(self):
        return self.contact_export or self.message_export or self.results_export or self.ticket_export

    @property
    def type(self):
        return NOTIFICATION_TYPES_BY_SLUG[self.notification_type]

    def as_json(self) -> dict:
        return self.type.as_json(self)

    class Meta:
        indexes = [
            # used to list org specific notifications for a user
            models.Index(fields=["org", "user", "-created_on"]),
            # used to find notifications with pending email sends
            models.Index(name="notifications_email_pending", fields=["created_on"], condition=Q(email_status="P")),
            # used for notification types where the target URL clears all of that type (e.g. incident_started)
            models.Index(
                name="notifications_unseen_of_type",
                fields=["org", "notification_type", "user"],
                condition=Q(is_seen=False),
            ),
        ]
        constraints = [
            # used to check if we already have existing unseen notifications for something or to clear unseen
            # notifications when visiting their target URL
            models.UniqueConstraint(
                name="notifications_unseen_scoped",
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
