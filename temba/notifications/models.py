from django.contrib.auth.models import User
from django.db import models
from django.utils import timezone

from temba.contacts.models import ContactImport, ExportContactsTask
from temba.flows.models import ExportFlowResultsTask, FlowStart
from temba.msgs.models import Broadcast, ExportMessagesTask
from temba.orgs.models import Org
from temba.tickets.models import Ticket, TicketEvent


class Log(models.Model):
    TYPE_BROADCAST_STARTED = "bcast:started"
    TYPE_BROADCAST_COMPLETES = "bcast:completed"
    TYPE_FLOWSTART_STARTED = "start:started"
    TYPE_FLOWSTART_COMPLETED = "start:completed"
    TYPE_TICKET_NEW = "ticket:new"
    TYPE_TICKET_NEW_MSGS = "ticket:msgs"
    TYPE_TICKET_ASSIGNMENT = "ticket:assign"
    TYPE_TICKET_NOTE = "ticket:notes"
    TYPE_IMPORT_STARTED = "import:started"
    TYPE_IMPORT_COMPLETED = "import:completed"
    TYPE_EXPORT_STARTED = "export:started"
    TYPE_EXPORT_COMPLETED = "export:completed"

    id = models.BigAutoField(primary_key=True)
    org = models.ForeignKey(Org, on_delete=models.PROTECT, related_name="logs")
    type = models.CharField(max_length=16)
    created_on = models.DateTimeField(default=timezone.now)

    broadcast = models.ForeignKey(Broadcast, on_delete=models.PROTECT, related_name="logs")
    flow_start = models.ForeignKey(FlowStart, on_delete=models.PROTECT, related_name="logs")
    ticket = models.ForeignKey(Ticket, on_delete=models.PROTECT, related_name="logs")
    ticket_event = models.ForeignKey(TicketEvent, on_delete=models.PROTECT, related_name="logs")
    contact_import = models.ForeignKey(ContactImport, on_delete=models.PROTECT, related_name="logs")
    contact_export = models.ForeignKey(ExportContactsTask, on_delete=models.PROTECT, related_name="logs")
    messages_export = models.ForeignKey(ExportMessagesTask, on_delete=models.PROTECT, related_name="logs")
    results_export = models.ForeignKey(ExportFlowResultsTask, on_delete=models.PROTECT, related_name="logs")


class Notification(models.Model):
    id = models.BigAutoField(primary_key=True)
    org = models.ForeignKey(Org, on_delete=models.PROTECT, related_name="notifications")
    user = models.ForeignKey(User, on_delete=models.PROTECT, related_name="notifications")
    log = models.ForeignKey(Log, on_delete=models.PROTECT, related_name="notifications")
