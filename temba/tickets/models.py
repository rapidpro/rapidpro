from abc import ABCMeta

from smartmin.models import SmartModel

from django.conf.urls import url
from django.contrib.postgres.fields import JSONField
from django.db import models
from django.db.models import Q
from django.template import Engine
from django.utils import timezone
from django.utils.translation import ugettext_lazy as _

from temba import mailroom
from temba.contacts.models import Contact
from temba.orgs.models import Org
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

    def is_available(self):
        """
        Determines whether this ticketer type is available
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


class Ticketer(SmartModel):
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
    def create(cls, org, user, ticketer_type, name, config):
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

    def release(self):
        """
        Releases this, closing all associated tickets in the process
        """
        assert not self.dependent_flows.exists(), "can't delete ticketer currently in use by flows"

        open_tickets = self.tickets.filter(status=Ticket.STATUS_OPEN)
        if open_tickets.exists():
            Ticket.bulk_close(self.org, open_tickets)

        self.is_active = False
        self.save(update_fields=("is_active", "modified_on"))

    def __str__(self):
        return f"Ticketer[uuid={self.uuid}, name={self.name}]"


class Ticket(models.Model):
    """
    A ticket represents a contact-initiated question or dialog.
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

    # when this ticket was opened
    opened_on = models.DateTimeField(default=timezone.now)

    # when this ticket was last modified
    modified_on = models.DateTimeField(default=timezone.now)

    # when this ticket was closed
    closed_on = models.DateTimeField(null=True)

    @classmethod
    def bulk_close(cls, org, tickets):
        return mailroom.get_client().ticket_close(org.id, [t.id for t in tickets if t.ticketer.is_active])

    @classmethod
    def bulk_reopen(cls, org, tickets):
        return mailroom.get_client().ticket_reopen(org.id, [t.id for t in tickets if t.ticketer.is_active])

    @classmethod
    def apply_action_close(cls, user, tickets):
        cls.bulk_close(tickets[0].org, tickets)

    @classmethod
    def apply_action_reopen(cls, user, tickets):
        cls.bulk_reopen(tickets[0].org, tickets)

    def __str__(self):
        return f"Ticket[uuid={self.uuid}, subject={self.subject}]"

    class Meta:
        indexes = [
            # used by the open tickets view
            models.Index(name="tickets_org_open", fields=["org", "-opened_on"], condition=Q(status="O")),
            # used by the closed tickets view
            models.Index(name="tickets_org_closed", fields=["org", "-opened_on"], condition=Q(status="C")),
            # used by the tickets filtered by ticketer view
            models.Index(name="tickets_org_ticketer", fields=["ticketer", "-opened_on"]),
            # used by the list of tickets on contact page and also message handling to find open tickets for contact
            models.Index(name="tickets_contact_open", fields=["contact", "-opened_on"], condition=Q(status="O")),
            # used by ticket handlers in mailroom to find tickets from their external IDs
            models.Index(name="tickets_ticketer_external_id", fields=["ticketer", "external_id"]),
        ]
