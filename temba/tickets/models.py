from abc import ABCMeta

from smartmin.models import SmartModel

from django.conf import settings
from django.conf.urls import url
from django.contrib.auth.models import User
from django.contrib.postgres.fields import JSONField
from django.db import models
from django.db.models import Q
from django.template import Engine
from django.utils import timezone
from django.utils.translation import ugettext_lazy as _

from temba import mailroom
from temba.contacts.models import Contact
from temba.orgs.models import DependencyMixin, Org
from temba.utils.uuid import uuid4


class TicketerType(metaclass=ABCMeta):
    """
    TicketerType is our abstract base type for ticketers.
    """

    # the verbose name for this ticketer type
    name = None

    # the short code for this ticketer type (< 16 chars, lowercase)
    slug = None

    # the icon to show for this ticketer type
    icon = "icon-channel-external"

    # the blurb to show on the main connect page
    connect_blurb = None

    # the view that handles connection of a new service
    connect_view = None

    def is_available_to(self, user):
        """
        Determines whether this ticketer type is available to the given user
        """
        return True  # pragma: no cover

    def get_connect_blurb(self):
        """
        Gets the blurb for use on the connect page
        """
        return Engine.get_default().from_string(str(self.connect_blurb))

    def get_urls(self):
        """
        Returns all the URLs this ticketer exposes to Django, the URL should be relative.
        """
        return [self.get_connect_url()]

    def get_connect_url(self):
        """
        Gets the URL/view configuration for this ticketer's connect page
        """
        return url(r"^connect", self.connect_view.as_view(ticketer_type=self), name="connect")


class Ticketer(SmartModel, DependencyMixin):
    """
    A service that can open and close tickets
    """

    # our UUID
    uuid = models.UUIDField(default=uuid4)

    # the type of this ticketer
    ticketer_type = models.CharField(max_length=16)

    # the org this ticketer is connected to
    org = models.ForeignKey(Org, on_delete=models.PROTECT, related_name="ticketers")

    # a name for this ticketer
    name = models.CharField(max_length=64)

    # the configuration options
    config = JSONField()

    @classmethod
    def create(cls, org, user, ticketer_type: str, name: str, config: dict):
        return cls.objects.create(
            uuid=uuid4(),
            ticketer_type=ticketer_type,
            name=name,
            config=config,
            org=org,
            created_by=user,
            modified_by=user,
        )

    @classmethod
    def get_types(cls):
        """
        Returns the possible types available for ticketers
        """
        from .types import TYPES

        return TYPES.values()

    def get_type(self):
        """
        Returns the type instance
        """
        from .types import TYPES

        return TYPES[self.ticketer_type]

    def release(self, user):
        """
        Releases this, closing all associated tickets in the process
        """
        super().release(user)

        open_tickets = self.tickets.filter(status=Ticket.STATUS_OPEN)
        if open_tickets.exists():
            Ticket.bulk_close(self.org, user, open_tickets)

        self.is_active = False
        self.modified_by = user
        self.save(update_fields=("is_active", "modified_by", "modified_on"))

    def __str__(self):
        return f"Ticketer[uuid={self.uuid}, name={self.name}]"


class Ticket(models.Model):
    """
    A ticket represents a period of human interaction with a contact.
    """

    STATUS_OPEN = "O"
    STATUS_CLOSED = "C"
    STATUS_CHOICES = ((STATUS_OPEN, _("Open")), (STATUS_CLOSED, _("Closed")))

    # our UUID
    uuid = models.UUIDField(unique=True, default=uuid4)

    # the organization this ticket belongs to
    org = models.ForeignKey(Org, on_delete=models.PROTECT, related_name="tickets")

    # the ticketer that manages this ticket
    ticketer = models.ForeignKey(Ticketer, on_delete=models.PROTECT, related_name="tickets")

    # the contact this ticket is tied to
    contact = models.ForeignKey(Contact, on_delete=models.PROTECT, related_name="tickets")

    # the subject of the ticket
    subject = models.TextField()

    # the body of the ticket
    body = models.TextField()

    # the external id of the ticket
    external_id = models.CharField(null=True, max_length=255)

    # any configuration attributes for this ticket
    config = JSONField(null=True)

    # the status of this ticket, one of open, closed, expired
    status = models.CharField(max_length=1, choices=STATUS_CHOICES)

    # when this ticket was opened, closed, modified
    opened_on = models.DateTimeField(default=timezone.now)
    closed_on = models.DateTimeField(null=True)
    modified_on = models.DateTimeField(default=timezone.now)

    # when this ticket last had activity which includes messages being sent and received, and is used for ordering
    last_activity_on = models.DateTimeField(default=timezone.now)

    assignee = models.ForeignKey(User, on_delete=models.PROTECT, null=True, related_name="assigned_tickets")

    def assign(self, user: User, *, assignee: User, note: str):
        now = timezone.now()
        self.assignee = assignee
        self.modified_on = now
        self.last_activity_on = now
        self.save(update_fields=("assignee", "modified_on", "last_activity_on"))

        self.events.create(
            org=self.org,
            contact=self.contact,
            event_type=TicketEvent.TYPE_ASSIGNED,
            assignee=assignee,
            note=note,
            created_by=user,
        )

    def add_note(self, user: User, *, note: str):
        now = timezone.now()
        self.modified_on = timezone.now()
        self.last_activity_on = now
        self.save(update_fields=("modified_on", "last_activity_on"))

        self.events.create(
            org=self.org, contact=self.contact, event_type=TicketEvent.TYPE_NOTE, note=note, created_by=user
        )

    @classmethod
    def bulk_close(cls, org, user, tickets):
        ticket_ids = [t.id for t in tickets if t.ticketer.is_active]
        return mailroom.get_client().ticket_close(org.id, user.id, ticket_ids)

    @classmethod
    def bulk_reopen(cls, org, user, tickets):
        ticket_ids = [t.id for t in tickets if t.ticketer.is_active]
        return mailroom.get_client().ticket_reopen(org.id, user.id, ticket_ids)

    def __str__(self):
        return f"Ticket[uuid={self.uuid}, subject={self.subject}]"

    class Meta:
        indexes = [
            # used by the open tickets view
            models.Index(name="tickets_org_open", fields=["org", "-last_activity_on", "-id"], condition=Q(status="O")),
            # used by the closed tickets view
            models.Index(
                name="tickets_org_closed", fields=["org", "-last_activity_on", "-id"], condition=Q(status="C")
            ),
            # used by the tickets filtered by ticketer view
            models.Index(name="tickets_org_ticketer", fields=["ticketer", "-opened_on"]),
            # used by the list of tickets on contact page and also message handling to find open tickets for contact
            models.Index(name="tickets_contact_open", fields=["contact", "-opened_on"], condition=Q(status="O")),
            # used by ticket handlers in mailroom to find tickets from their external IDs
            models.Index(name="tickets_ticketer_external_id", fields=["ticketer", "external_id"]),
        ]


class TicketEvent(models.Model):
    """
    Models the history of a ticket.
    """

    TYPE_OPENED = "O"
    TYPE_ASSIGNED = "A"
    TYPE_NOTE = "N"
    TYPE_CLOSED = "C"
    TYPE_REOPENED = "R"
    TYPE_CHOICES = (
        (TYPE_OPENED, "Opened"),
        (TYPE_ASSIGNED, "Assigned"),
        (TYPE_NOTE, "Note"),
        (TYPE_CLOSED, "Closed"),
        (TYPE_REOPENED, "Reopened"),
    )

    org = models.ForeignKey(Org, on_delete=models.PROTECT, related_name="ticket_events")
    ticket = models.ForeignKey(Ticket, on_delete=models.PROTECT, related_name="events")
    contact = models.ForeignKey(Contact, on_delete=models.PROTECT, related_name="ticket_events")
    event_type = models.CharField(max_length=1, choices=TYPE_CHOICES)
    note = models.TextField(null=True)
    assignee = models.ForeignKey(User, on_delete=models.PROTECT, null=True, related_name="ticket_assignee_events")

    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, null=True, related_name="ticket_events"
    )
    created_on = models.DateTimeField(default=timezone.now)

    class Meta:
        indexes = [
            # used for contact history
            models.Index(name="ticketevents_contact_created", fields=["contact", "created_on"])
        ]
