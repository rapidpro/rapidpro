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
from temba.orgs.models import Export, Org
from temba.tickets.models import ExportTicketsTask
from temba.utils.email import send_template_email
from temba.utils.models import SquashableModel

logger = logging.getLogger(__name__)


class IncidentType:
    slug: str
    title: str

    def get_notification_scope(self, incident) -> str:
        return str(incident.id)

    def get_notification_target_url(self, incident) -> str:
        return reverse("notifications.incident_list")

    def as_json(self, incident) -> dict:
        return {
            "type": incident.incident_type,
            "started_on": incident.started_on.isoformat(),
            "ended_on": incident.ended_on.isoformat() if incident.ended_on else None,
        }


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
    def get_or_create(cls, org, incident_type: str, *, scope: str, **kwargs):
        from .types.builtin import IncidentStartedNotificationType

        incident, created = cls.objects.get_or_create(
            org=org,
            incident_type=incident_type,
            scope=scope,
            ended_on=None,
            defaults=kwargs,
        )
        if created:
            IncidentStartedNotificationType.create(incident)
        return incident

    def end(self):
        """
        Ends this incident
        """
        self.ended_on = timezone.now()
        self.save(update_fields=("ended_on",))

    @property
    def template(self) -> str:
        return f"notifications/incidents/{self.incident_type.replace(':', '_')}.html"

    @property
    def email_template(self) -> str:
        return f"notifications/email/incident_started.{self.incident_type.replace(':', '_')}"

    @property
    def type(self) -> IncidentType:
        from .incidents import TYPES  # noqa

        return TYPES[self.incident_type]

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

    def get_email_subject(self, notification) -> str:  # pragma: no cover
        """
        For types that support sending as email, this is the subject of the email
        """
        return ""

    def get_email_template(self, notification) -> str:  # pragma: no cover
        """
        For types that support sending as email, this is the template to use
        """
        return ""

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


class Notification(models.Model):
    """
    A user specific notification
    """

    MEDIUM_UI = "U"
    MEDIUM_EMAIL = "E"

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
    medium = models.CharField(max_length=2)

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
    export = models.ForeignKey(Export, null=True, on_delete=models.PROTECT, related_name="notifications")

    contact_import = models.ForeignKey(ContactImport, null=True, on_delete=models.PROTECT, related_name="notifications")

    incident = models.ForeignKey(Incident, null=True, on_delete=models.PROTECT, related_name="notifications")

    @classmethod
    def create_all(cls, org, notification_type: str, *, scope: str, users, **kwargs):
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
        subject = self.type.get_email_subject(self)
        template = self.type.get_email_template(self)
        context = self.type.get_email_context(self)

        if subject and template:
            send_template_email(
                self.user.email,
                f"[{self.org.name}] {subject}",
                template,
                context,
                self.org.branding,
            )
        else:  # pragma: no cover
            logger.error(f"pending emails for notification type {self.type.slug} not configured for email")

        self.email_status = Notification.EMAIL_STATUS_SENT
        self.save(update_fields=("email_status",))

    @classmethod
    def mark_seen(cls, org, notification_type: str, *, scope: str, user):
        notifications = cls.objects.filter(org_id=org.id, notification_type=notification_type, user=user, is_seen=False)

        if scope is not None:
            notifications = notifications.filter(scope=scope)

        notifications.update(is_seen=True)

    @classmethod
    def get_unseen_count(cls, org: Org, user: User) -> int:
        return NotificationCount.get_total(org, user)

    @cached_property
    def export_obj(self):
        # TODO remove once everything is an orgs.Export
        return self.contact_export or self.message_export or self.results_export or self.ticket_export or self.export

    @property
    def type(self):
        from .types import TYPES  # noqa

        return TYPES[self.notification_type]

    def as_json(self) -> dict:
        return self.type.as_json(self)

    class Meta:
        indexes = [
            # used to list org specific notifications for a user in the UI
            models.Index(
                name="notifications_user_ui",
                fields=["org", "user", "-created_on", "-id"],
                condition=Q(medium__contains="U"),
            ),
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
