from abc import ABCMeta

import regex
from smartmin.models import SmartModel

from django.conf import settings
from django.conf.urls import url
from django.contrib.auth.models import User
from django.db import models
from django.db.models import Q, Sum
from django.template import Engine
from django.utils import timezone
from django.utils.translation import ugettext_lazy as _

from temba import mailroom
from temba.contacts.models import Contact
from temba.orgs.models import DependencyMixin, Org
from temba.utils.models import SquashableModel
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
    config = models.JSONField()

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
    def create_internal_ticketer(cls, org, brand: dict):
        """
        Every org gets a single internal ticketer
        """

        from .types.internal import InternalType

        assert not org.ticketers.filter(ticketer_type=InternalType.slug).exists(), "org already has internal tickteter"

        return cls.create(org, org.created_by, InternalType.slug, f"{brand['name']} Tickets", {})

    @classmethod
    def get_types(cls):
        """
        Returns the possible types available for ticketers
        """
        from .types import TYPES

        return TYPES.values()

    @property
    def type(self):
        """
        Returns the type instance
        """
        from .types import TYPES

        return TYPES[self.ticketer_type]

    @property
    def is_internal(self):
        from .types.internal import InternalType

        return self.type == InternalType

    def release(self, user):
        """
        Releases this, closing all associated tickets in the process
        """

        assert not self.is_internal, "can't release internal ticketers"

        super().release(user)

        open_tickets = self.tickets.filter(status=Ticket.STATUS_OPEN)
        if open_tickets.exists():
            Ticket.bulk_close(self.org, user, open_tickets, force=True)

        self.is_active = False
        self.modified_by = user
        self.save(update_fields=("is_active", "modified_by", "modified_on"))

    def __str__(self):
        return f"Ticketer[uuid={self.uuid}, name={self.name}]"


class Topic(SmartModel, DependencyMixin):
    """
    The topic of a ticket which controls who can access that ticket.
    """

    MAX_NAME_LEN = 64
    DEFAULT_TOPIC = "General"

    uuid = models.UUIDField(unique=True, default=uuid4)
    org = models.ForeignKey(Org, on_delete=models.PROTECT, related_name="topics")
    name = models.CharField(max_length=MAX_NAME_LEN)
    is_default = models.BooleanField(default=False)

    @classmethod
    def create_default_topic(cls, org):
        assert not org.topics.filter(is_default=True).exists(), "org already has default topic"

        org.topics.create(
            name=cls.DEFAULT_TOPIC, is_default=True, created_by=org.created_by, modified_by=org.modified_by
        )

    @classmethod
    def get_or_create(cls, org, user, name):
        assert cls.is_valid_name(name), f"{name} is not a valid topic name"

        existing = org.topics.filter(name__iexact=name).first()
        if existing:
            return existing
        return org.topics.create(name=name, created_by=user, modified_by=user)

    @classmethod
    def is_valid_name(cls, name):
        # don't allow empty strings, blanks, initial or trailing whitespace
        if not name or name.strip() != name:
            return False

        if len(name) > cls.MAX_NAME_LEN:
            return False

        return regex.match(r"\w[\w- ]*", name, flags=regex.UNICODE)

    def __str__(self):
        return f"Topic[uuid={self.uuid}, topic={self.name}]"


class Ticket(models.Model):
    """
    A ticket represents a period of human interaction with a contact.
    """

    STATUS_OPEN = "O"
    STATUS_CLOSED = "C"
    STATUS_CHOICES = ((STATUS_OPEN, _("Open")), (STATUS_CLOSED, _("Closed")))

    # permission that users need to have a ticket assigned to them
    ASSIGNEE_PERMISSION = "tickets.ticket_assignee"

    MAX_NOTE_LEN = 4096

    uuid = models.UUIDField(unique=True, default=uuid4)
    org = models.ForeignKey(Org, on_delete=models.PROTECT, related_name="tickets")
    ticketer = models.ForeignKey(Ticketer, on_delete=models.PROTECT, related_name="tickets")
    contact = models.ForeignKey(Contact, on_delete=models.PROTECT, related_name="tickets")

    # ticket content
    topic = models.ForeignKey(Topic, on_delete=models.PROTECT, related_name="tickets")
    body = models.TextField()

    # the external id of the ticket
    external_id = models.CharField(null=True, max_length=255)

    # any configuration attributes for this ticket
    config = models.JSONField(null=True)

    # the status of this ticket and who it's currently assigned to
    status = models.CharField(max_length=1, choices=STATUS_CHOICES)
    assignee = models.ForeignKey(User, on_delete=models.PROTECT, null=True, related_name="assigned_tickets")

    # when this ticket was opened, closed, modified
    opened_on = models.DateTimeField(default=timezone.now)
    closed_on = models.DateTimeField(null=True)
    modified_on = models.DateTimeField(default=timezone.now)

    # when this ticket last had activity which includes messages being sent and received, and is used for ordering
    last_activity_on = models.DateTimeField(default=timezone.now)

    def assign(self, user: User, *, assignee: User, note: str):
        self.bulk_assign(self.org, user, [self], assignee=assignee, note=note)

    def add_note(self, user: User, *, note: str):
        self.bulk_add_note(self.org, user, [self], note=note)

    @classmethod
    def bulk_assign(cls, org, user: User, tickets: list, assignee: User, note: str = None):
        ticket_ids = [t.id for t in tickets if t.ticketer.is_active]
        assignee_id = assignee.id if assignee else None
        return mailroom.get_client().ticket_assign(org.id, user.id, ticket_ids, assignee_id, note)

    @classmethod
    def bulk_add_note(cls, org, user: User, tickets: list, note: str):
        ticket_ids = [t.id for t in tickets if t.ticketer.is_active]
        return mailroom.get_client().ticket_add_note(org.id, user.id, ticket_ids, note)

    @classmethod
    def bulk_change_topic(cls, org, user: User, tickets: list, topic: Topic):
        ticket_ids = [t.id for t in tickets if t.ticketer.is_active]
        return mailroom.get_client().ticket_change_topic(org.id, user.id, ticket_ids, topic.id)

    @classmethod
    def bulk_close(cls, org, user, tickets, *, force: bool = False):
        ticket_ids = [t.id for t in tickets if t.ticketer.is_active]
        return mailroom.get_client().ticket_close(org.id, user.id, ticket_ids, force=force)

    @classmethod
    def bulk_reopen(cls, org, user, tickets):
        ticket_ids = [t.id for t in tickets if t.ticketer.is_active]
        return mailroom.get_client().ticket_reopen(org.id, user.id, ticket_ids)

    @classmethod
    def get_allowed_assignees(cls, org):
        return org.get_users_with_perm(cls.ASSIGNEE_PERMISSION)

    def __str__(self):
        return f"Ticket[uuid={self.uuid}, topic={self.topic.name}]"

    class Meta:
        indexes = [
            # used by the All folder
            models.Index(name="tickets_org_status", fields=["org", "status", "-last_activity_on", "-id"]),
            # used by the Unassigned and Mine folders
            models.Index(
                name="tickets_org_assignee_status",
                fields=["org", "assignee", "status", "-last_activity_on", "-id"],
            ),
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
    TYPE_NOTE_ADDED = "N"
    TYPE_TOPIC_CHANGED = "T"
    TYPE_CLOSED = "C"
    TYPE_REOPENED = "R"
    TYPE_CHOICES = (
        (TYPE_OPENED, "Opened"),
        (TYPE_ASSIGNED, "Assigned"),
        (TYPE_NOTE_ADDED, "Note Added"),
        (TYPE_TOPIC_CHANGED, "Topic Changed"),
        (TYPE_CLOSED, "Closed"),
        (TYPE_REOPENED, "Reopened"),
    )

    org = models.ForeignKey(Org, on_delete=models.PROTECT, related_name="ticket_events")
    ticket = models.ForeignKey(Ticket, on_delete=models.PROTECT, related_name="events")
    contact = models.ForeignKey(Contact, on_delete=models.PROTECT, related_name="ticket_events")
    event_type = models.CharField(max_length=1, choices=TYPE_CHOICES)
    note = models.TextField(null=True, max_length=Ticket.MAX_NOTE_LEN)
    topic = models.ForeignKey(Topic, on_delete=models.PROTECT, null=True, related_name="ticket_events")
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


class TicketFolder(metaclass=ABCMeta):
    slug = None
    name = None
    icon = None

    def get_queryset(self, org, user, ordered):
        qs = Ticket.objects.filter(org=org)

        if ordered:
            qs = qs.order_by("-last_activity_on", "-id")

        return qs.select_related("topic", "assignee").prefetch_related("contact")

    @classmethod
    def from_slug(cls, slug: str):
        return FOLDERS[slug]

    @classmethod
    def all(cls):
        return FOLDERS


class MineFolder(TicketFolder):
    """
    Tickets assigned to the current user
    """

    slug = "mine"
    name = _("My Tickets")
    icon = "coffee"

    def get_queryset(self, org, user, ordered):
        return super().get_queryset(org, user, ordered).filter(assignee=user)


class UnassignedFolder(TicketFolder):
    """
    Tickets not assigned to any user
    """

    slug = "unassigned"
    name = _("Unassigned")
    icon = "mail"

    def get_queryset(self, org, user, ordered):
        return super().get_queryset(org, user, ordered).filter(assignee=None)


class AllFolder(TicketFolder):
    """
    All tickets
    """

    slug = "all"
    name = _("All")
    icon = "archive"

    def get_queryset(self, org, user, ordered):
        return super().get_queryset(org, user, ordered)


FOLDERS = {f.slug: f() for f in TicketFolder.__subclasses__()}


class TicketCount(SquashableModel):
    """
    Counts of tickets by assignment and status
    """

    SQUASH_OVER = ("org_id", "assignee_id", "status")

    org = models.ForeignKey(Org, on_delete=models.PROTECT, related_name="ticket_counts")
    assignee = models.ForeignKey(User, null=True, on_delete=models.PROTECT, related_name="ticket_counts")
    status = models.CharField(max_length=1, choices=Ticket.STATUS_CHOICES)
    count = models.IntegerField(default=0)

    @classmethod
    def get_squash_query(cls, distinct_set) -> tuple:
        if distinct_set.assignee_id:
            sql = """
            WITH removed as (
                DELETE FROM %(table)s WHERE "org_id" = %%s AND "assignee_id" = %%s AND "status" = %%s RETURNING "count"
            )
            INSERT INTO %(table)s("org_id", "assignee_id", "status", "count", "is_squashed")
            VALUES (%%s, %%s, %%s, GREATEST(0, (SELECT SUM("count") FROM removed)), TRUE);
            """ % {
                "table": cls._meta.db_table
            }

            params = (distinct_set.org_id, distinct_set.assignee_id, distinct_set.status) * 2
        else:
            sql = """
            WITH removed as (
                DELETE FROM %(table)s WHERE "org_id" = %%s AND "assignee_id" IS NULL AND "status" = %%s RETURNING "count"
            )
            INSERT INTO %(table)s("org_id", "assignee_id", "status", "count", "is_squashed")
            VALUES (%%s, NULL, %%s, GREATEST(0, (SELECT SUM("count") FROM removed)), TRUE);
            """ % {
                "table": cls._meta.db_table
            }

            params = (distinct_set.org_id, distinct_set.status) * 2

        return sql, params

    @classmethod
    def get_by_assignees(cls, org, assignees: list, status: str) -> dict:
        """
        Gets counts for a set of assignees (None means no assignee)
        """
        counts = cls.objects.filter(org=org, status=status)
        counts = counts.values_list("assignee").annotate(count_sum=Sum("count"))
        counts_by_assignee = {c[0]: c[1] for c in counts}

        return {a: counts_by_assignee.get(a.id if a else None, 0) for a in assignees}

    @classmethod
    def get_all(cls, org, status: str) -> int:
        """
        Gets count for org and status regardless of assignee
        """
        return cls.sum(cls.objects.filter(org=org, status=status))

    class Meta:
        indexes = [
            models.Index(fields=("org", "status")),
            models.Index(fields=("org", "assignee", "status")),
            # for squashing task
            models.Index(
                name="ticket_count_unsquashed", fields=("org", "assignee", "status"), condition=Q(is_squashed=False)
            ),
        ]
