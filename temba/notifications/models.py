from enum import Enum

from django.contrib.auth.models import User
from django.db import models
from django.utils import timezone

from temba.channels.models import Alert
from temba.contacts.models import ContactImport, ExportContactsTask
from temba.flows.models import ExportFlowResultsTask, FlowStart
from temba.msgs.models import Broadcast, ExportMessagesTask
from temba.orgs.models import Org
from temba.tickets.models import Ticket, TicketEvent


class NotifyWho(Enum):
    all = 1
    all_except_user = 2
    admins = 3
    admins_except_user = 4
    user = 5
    nobody = 6


class LogType:
    slug = None
    notify_who = None
    email_template = None


class BroadcastStartedLog(LogType):
    slug = "bcast:started"
    notify_who = NotifyWho.admins


class BroadcastCompletedLog(LogType):
    slug = "bcast:completed"
    notify_who = NotifyWho.user


class ChannelAlertLog(LogType):
    slug = "channel:alert"
    notify_who = NotifyWho.admins


class FlowStartStartedLog(LogType):
    slug = "start:started"
    notify_who = NotifyWho.admins


class FlowStartCompletedLog(LogType):
    slug = "start:completed"
    notify_who = NotifyWho.user


class ImportStartedLog(LogType):
    slug = "import:started"
    notify_who = NotifyWho.admins_except_user


class ImportCompletedLog(LogType):
    slug = "import:completed"
    notify_who = NotifyWho.user


class ExportStartedLog(LogType):
    slug = "export:started"
    notify_who = NotifyWho.admins_except_user


class ExportCompletedLog(LogType):
    slug = "export:completed"
    notify_who = NotifyWho.user


class TicketOpenedLog(LogType):
    slug = "ticket:opened"


class TicketNewMsgsLog(LogType):
    slug = "ticket:msgs"


class TicketAssignmentLog(LogType):
    slug = "ticket:assign"


class TicketNoteLog(LogType):
    slug = "ticket:note"


TYPES_BY_SLUG = {lt.slug: lt() for lt in LogType.__subclasses__()}


class Log(models.Model):
    """
    A log of something that happened in an org that can be turned into notifications for specific users
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

    EXPORT_TYPES = {
        ExportContactsTask: "contact_export",
        ExportMessagesTask: "message_export",
        ExportFlowResultsTask: "results_export",
    }

    @classmethod
    def _create(cls, org, user, log_type: str, **kwargs):
        log_type = TYPES_BY_SLUG[log_type]
        log = cls.objects.create(org=org, created_by=user, log_type=log_type.slug, **kwargs)

        Notification.create_for_log(log)

    @classmethod
    def channel_alert(cls, alert):
        cls._create(alert.channel.org, None, ChannelAlertLog.slug, alert=alert)

    @classmethod
    def import_started(cls, imp):
        cls._create(imp.org, imp.created_by, ImportStartedLog.slug, contact_import=imp)

    @classmethod
    def export_started(cls, export):
        cls._create(export.org, export.created_by, ExportStartedLog.slug, **{cls.EXPORT_TYPES[type(export)]: export})

    @classmethod
    def export_completed(cls, export):
        cls._create(export.org, export.created_by, ExportCompletedLog.slug, **{cls.EXPORT_TYPES[type(export)]: export})

    @property
    def type(self):
        return TYPES_BY_SLUG[self.log_type]

    def delete(self):
        for notification in self.notifications.all():
            notification.delete()

        super().delete()

    def __str__(self):  # pragma: no cover
        return f"Log[type={self.type.slug} created_on={self.created_on.isoformat()}]"

    class Meta:
        indexes = [models.Index(fields=["org", "-created_on"])]


class Notification(models.Model):
    """
    A user specific notification of a log instance
    """

    id = models.BigAutoField(primary_key=True)
    org = models.ForeignKey(Org, on_delete=models.PROTECT, related_name="notifications")
    user = models.ForeignKey(User, on_delete=models.PROTECT, related_name="notifications")
    log = models.ForeignKey(Log, on_delete=models.PROTECT, related_name="notifications")
    is_seen = models.BooleanField(default=False)

    @classmethod
    def create_for_log(cls, log: Log):
        org = log.org
        user = log.created_by
        who = log.type.notify_who

        if who == NotifyWho.all or who == NotifyWho.all_except_user:  # pragma: no cover
            notify_users = org.get_users()
            if who == NotifyWho.all_except_user and user:
                notify_users = notify_users.exclude(id=user.id)
        elif who == NotifyWho.admins or who == NotifyWho.admins_except_user:
            notify_users = org.get_admins()
            if who == NotifyWho.admins_except_user and user:
                notify_users = notify_users.exclude(id=user.id)
        else:  # log_type.notify_who == NotifyWho.user:
            notify_users = [user]

        for notify_user in notify_users:
            cls.objects.create(org=org, user=notify_user, log=log)

    class Meta:
        indexes = [models.Index(fields=["org", "user", "-id"])]
