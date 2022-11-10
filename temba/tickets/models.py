import logging
from abc import ABCMeta
from datetime import date

import openpyxl

from django.conf import settings
from django.db import models
from django.db.models import Q, Sum
from django.db.models.functions import Lower
from django.template import Engine
from django.urls import re_path
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from temba import mailroom
from temba.assets.models import register_asset_store
from temba.contacts.models import Contact
from temba.orgs.models import DependencyMixin, Org, User, UserSettings
from temba.utils import chunk_list
from temba.utils.dates import date_range
from temba.utils.export import BaseExportAssetStore, BaseItemWithContactExport, MultiSheetExporter
from temba.utils.models import DailyCountModel, DailyTimingModel, SquashableModel, TembaModel
from temba.utils.uuid import uuid4

logger = logging.getLogger(__name__)


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
        return re_path(r"^connect", self.connect_view.as_view(ticketer_type=self), name="connect")


class Ticketer(TembaModel, DependencyMixin):
    """
    A service that can open and close tickets
    """

    org = models.ForeignKey(Org, on_delete=models.PROTECT, related_name="ticketers")
    ticketer_type = models.CharField(max_length=16)
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

        return org.ticketers.create(
            uuid=uuid4(),
            ticketer_type=InternalType.slug,
            name=f"{brand['name']} Tickets",
            is_system=True,
            config={},
            created_by=org.created_by,
            modified_by=org.created_by,
        )

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

    def release(self, user):
        """
        Releases this, closing all associated tickets in the process
        """

        assert not (self.is_system and self.org.is_active), "can't release system ticketers"

        super().release(user)

        open_tickets = self.tickets.filter(status=Ticket.STATUS_OPEN)
        if open_tickets.exists():
            Ticket.bulk_close(self.org, user, open_tickets, force=True)

        self.is_active = False
        self.name = self._deleted_name()
        self.modified_by = user
        self.save(update_fields=("name", "is_active", "modified_by", "modified_on"))


class Topic(TembaModel, DependencyMixin):
    """
    The topic of a ticket which controls who can access that ticket.
    """

    DEFAULT_TOPIC = "General"

    org = models.ForeignKey(Org, on_delete=models.PROTECT, related_name="topics")
    is_default = models.BooleanField(default=False)

    org_limit_key = Org.LIMIT_TOPICS

    @classmethod
    def create_default_topic(cls, org):
        assert not org.topics.filter(is_default=True).exists(), "org already has default topic"

        org.topics.create(
            name=cls.DEFAULT_TOPIC,
            is_default=True,
            is_system=True,
            created_by=org.created_by,
            modified_by=org.modified_by,
        )

    @classmethod
    def create(cls, org, user, name: str):
        assert cls.is_valid_name(name), f"'{name}' is not a valid topic name"
        assert not org.topics.filter(name__iexact=name).exists()

        return org.topics.create(name=name, created_by=user, modified_by=user)

    @classmethod
    def create_from_import_def(cls, org, user, definition: dict):
        return cls.create(org, user, definition["name"])

    def release(self, user):
        assert not (self.is_system and self.org.is_active), "can't release system topics"

        super().release(user)

        self.is_active = False
        self.name = self._deleted_name()
        self.modified_by = user
        self.save(update_fields=("name", "is_active", "modified_by", "modified_on"))

    class Meta:
        constraints = [models.UniqueConstraint("org", Lower("name"), name="unique_topic_names")]


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

    opened_on = models.DateTimeField(default=timezone.now)
    opened_in = models.ForeignKey("flows.Flow", null=True, on_delete=models.PROTECT, related_name="opened_tickets")
    opened_by = models.ForeignKey(User, null=True, on_delete=models.PROTECT, related_name="opened_tickets")

    # when this ticket was first replied to, closed, modified
    replied_on = models.DateTimeField(null=True)
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
        return org.get_users(with_perm=cls.ASSIGNEE_PERMISSION)

    def delete(self):
        self.events.all().delete()
        self.broadcasts.update(ticket=None)
        super().delete()

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
            # used by message handling to find open tickets for contact
            models.Index(name="tickets_contact_open", fields=["contact", "-opened_on"], condition=Q(status="O")),
            # used by ticket handlers in mailroom to find tickets from their external IDs
            models.Index(
                name="tickets_ticketer_external_id",
                fields=["ticketer", "external_id"],
                condition=Q(external_id__isnull=False),
            ),
            # used by API tickets endpoint
            models.Index(name="tickets_modified_on", fields=["-modified_on"]),
            models.Index(name="tickets_contact_modified_on", fields=["contact", "-modified_on"]),
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
    verbose_name = None

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
    icon = "icon.tickets_mine"

    def get_queryset(self, org, user, ordered):
        return super().get_queryset(org, user, ordered).filter(assignee=user)


class UnassignedFolder(TicketFolder):
    """
    Tickets not assigned to any user
    """

    slug = "unassigned"
    name = _("Unassigned")
    verbose_name = _("Unassigned Tickets")
    icon = "icon.tickets_unassigned"

    def get_queryset(self, org, user, ordered):
        return super().get_queryset(org, user, ordered).filter(assignee=None)


class AllFolder(TicketFolder):
    """
    All tickets
    """

    slug = "all"
    name = _("All")
    verbose_name = _("All Tickets")
    icon = "icon.tickets_all"

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


class Team(TembaModel):
    """
    Every user can be a member of a ticketing team
    """

    org = models.ForeignKey(Org, on_delete=models.PROTECT, related_name="teams")
    topics = models.ManyToManyField(Topic, related_name="teams")

    org_limit_key = Org.LIMIT_TEAMS

    @classmethod
    def create(cls, org, user, name: str):
        assert cls.is_valid_name(name), f"'{name}' is not a valid team name"
        assert not org.teams.filter(name__iexact=name, is_active=True).exists()

        return org.teams.create(name=name, created_by=user, modified_by=user)

    def get_users(self):
        return User.objects.filter(usersettings__team=self)

    def release(self, user):
        # remove all users from this team
        UserSettings.objects.filter(team=self).update(team=None)

        self.name = self._deleted_name()
        self.is_active = False
        self.modified_by = user
        self.save(update_fields=("name", "is_active", "modified_by", "modified_on"))

    class Meta:
        constraints = [models.UniqueConstraint("org", Lower("name"), name="unique_team_names")]


class TicketDailyCount(DailyCountModel):
    """
    Ticket activity daily counts by who did it and when. Mailroom writes these.
    """

    TYPE_OPENING = "O"
    TYPE_ASSIGNMENT = "A"  # includes tickets opened with assignment but excludes re-assignments
    TYPE_REPLY = "R"

    @classmethod
    def get_by_org(cls, org, count_type: str, since=None, until=None):
        return cls._get_count_set(count_type, {f"o:{org.id}": org}, since, until)

    @classmethod
    def get_by_teams(cls, teams, count_type: str, since=None, until=None):
        return cls._get_count_set(count_type, {f"t:{t.id}": t for t in teams}, since, until)

    @classmethod
    def get_by_users(cls, org, users, count_type: str, since=None, until=None):
        return cls._get_count_set(count_type, {f"o:{org.id}:u:{u.id}": u for u in users}, since, until)

    class Meta:
        indexes = [
            models.Index(name="tickets_dailycount_type_scope", fields=("count_type", "scope", "day")),
            models.Index(
                name="tickets_dailycount_unsquashed",
                fields=("count_type", "scope", "day"),
                condition=Q(is_squashed=False),
            ),
        ]


class TicketDailyTiming(DailyTimingModel):
    """
    Ticket activity daily timings. Mailroom writes these.
    """

    TYPE_FIRST_REPLY = "R"
    TYPE_LAST_CLOSE = "C"

    @classmethod
    def get_by_org(cls, org, count_type: str, since=None, until=None):
        return cls._get_count_set(count_type, {f"o:{org.id}": org}, since, until)

    class Meta:
        indexes = [
            models.Index(name="tickets_dailytiming_type_scope", fields=("count_type", "scope", "day")),
            models.Index(
                name="tickets_dailytiming_unsquashed",
                fields=("count_type", "scope", "day"),
                condition=Q(is_squashed=False),
            ),
        ]


def export_ticket_stats(org: Org, since: date, until: date) -> openpyxl.Workbook:
    workbook = openpyxl.Workbook()
    sheet = workbook.active
    sheet.title = "Tickets"
    sheet.merge_cells("A1:A2")
    sheet.cell(row=1, column=2, value="Workspace")
    sheet.merge_cells("B1:D1")
    sheet.cell(row=2, column=2, value="Opened")
    sheet.cell(row=2, column=3, value="Replies")
    sheet.cell(row=2, column=4, value="Reply Time (Secs)")

    users = list(org.users.order_by("email"))

    user_col = 5
    for user in users:
        cell = sheet.cell(row=1, column=user_col, value=str(user))
        cell.hyperlink = f"mailto:{user.email}"
        cell.style = "Hyperlink"
        sheet.merge_cells(start_row=1, start_column=user_col, end_row=1, end_column=user_col + 1)

        sheet.cell(row=2, column=user_col, value="Assigned")
        sheet.cell(row=2, column=user_col + 1, value="Replies")
        user_col += 2

    def by_day(cs: list) -> dict:
        return {c[0]: c[1] for c in cs}

    org_openings = by_day(TicketDailyCount.get_by_org(org, TicketDailyCount.TYPE_OPENING, since, until).day_totals())
    org_replies = by_day(TicketDailyCount.get_by_org(org, TicketDailyCount.TYPE_REPLY, since, until).day_totals())
    org_avg_reply_time = by_day(
        TicketDailyTiming.get_by_org(org, TicketDailyTiming.TYPE_FIRST_REPLY, since, until).day_averages(rounded=True)
    )

    user_assignments = {}
    user_replies = {}
    for user in users:
        user_assignments[user] = by_day(
            TicketDailyCount.get_by_users(org, [user], TicketDailyCount.TYPE_ASSIGNMENT, since, until).day_totals()
        )
        user_replies[user] = by_day(
            TicketDailyCount.get_by_users(org, [user], TicketDailyCount.TYPE_REPLY, since, until).day_totals()
        )

    day_row = 3
    for day in date_range(since, until):
        sheet.cell(row=day_row, column=1, value=day)
        sheet.cell(row=day_row, column=2, value=org_openings.get(day, 0))
        sheet.cell(row=day_row, column=3, value=org_replies.get(day, 0))
        sheet.cell(row=day_row, column=4, value=org_avg_reply_time.get(day, ""))

        user_col = 5
        for user in users:
            sheet.cell(row=day_row, column=user_col, value=user_assignments[user].get(day, 0))
            sheet.cell(row=day_row, column=user_col + 1, value=user_replies[user].get(day, 0))
            user_col += 2

        day_row += 1

    return workbook


class ExportTicketsTask(BaseItemWithContactExport):
    analytics_key = "ticket_export"
    notification_export_type = "ticket"

    @classmethod
    def create(cls, org, user, start_date, end_date, with_fields=(), with_groups=()):
        export = cls.objects.create(
            org=org, start_date=start_date, end_date=end_date, created_by=user, modified_by=user
        )
        export.with_fields.add(*with_fields)
        export.with_groups.add(*with_groups)
        return export

    def write_export(self):
        headers = ["UUID", "Opened On", "Closed On", "Topic", "Assigned To"] + self._get_contact_headers()
        start_date, end_date = self._get_date_range()

        # get the ticket ids, filtered and ordered by opened on
        ticket_ids = (
            self.org.tickets.filter(opened_on__gte=start_date, opened_on__lte=end_date)
            .order_by("opened_on")
            .values_list("id", flat=True)
        )

        exporter = MultiSheetExporter("Tickets", headers, self.org.timezone)

        # add tickets to the export in batches of 1k to limit memory usage
        for batch_ids in chunk_list(ticket_ids, 1000):
            tickets = (
                Ticket.objects.filter(id__in=batch_ids)
                .order_by("opened_on")
                .prefetch_related("org", "contact", "contact__org", "contact__groups", "assignee", "topic")
                .using("readonly")
            )

            Contact.bulk_urn_cache_initialize([t.contact for t in tickets], using="readonly")

            for ticket in tickets:
                values = [
                    str(ticket.uuid),
                    ticket.opened_on,
                    ticket.closed_on,
                    ticket.topic.name,
                    ticket.assignee.email if ticket.assignee else None,
                ]
                values += self._get_contact_columns(ticket.contact)

                exporter.write_row(values)

            self.modified_on = timezone.now()
            self.save(update_fields=("modified_on",))

        return exporter.save_file()


@register_asset_store
class TicketExportAssetStore(BaseExportAssetStore):
    model = ExportTicketsTask
    key = "ticket_export"
    directory = "ticket_exports"
    permission = "tickets.ticket_export"
    extensions = ("xlsx",)
