import functools
import re
from collections import defaultdict
from datetime import timedelta
from decimal import Decimal
from functools import wraps
from unittest.mock import call, patch

from django.conf import settings
from django.contrib.auth.models import User
from django.db import connection
from django.utils import timezone

from temba import mailroom
from temba.campaigns.models import CampaignEvent, EventFire
from temba.channels.models import Channel
from temba.contacts.models import URN, Contact, ContactField, ContactGroup, ContactURN
from temba.flows.models import FlowRun, FlowSession
from temba.locations.models import AdminBoundary
from temba.mailroom.client import ContactSpec, MailroomClient, MailroomException
from temba.mailroom.modifiers import Modifier
from temba.msgs.models import Msg
from temba.orgs.models import Org
from temba.tests.dates import parse_datetime
from temba.tickets.models import Ticket, TicketEvent, Topic
from temba.utils import get_anonymous_user, json

event_units = {
    CampaignEvent.UNIT_MINUTES: "minutes",
    CampaignEvent.UNIT_HOURS: "hours",
    CampaignEvent.UNIT_DAYS: "days",
    CampaignEvent.UNIT_WEEKS: "weeks",
}


def mock_inspect_query(org, query: str, fields=None) -> mailroom.QueryMetadata:
    def field_ref(f):
        return {"key": f.key, "name": f.name} if isinstance(f, ContactField) else {"key": f}

    tokens = [t.lower() for t in re.split(r"\W+", query) if t]
    attributes = list(sorted({"id", "uuid", "flow", "group", "created_on"}.intersection(tokens)))
    fields = fields if fields is not None else org.fields.filter(is_system=False, key__in=tokens)
    schemes = list(sorted(URN.VALID_SCHEMES.intersection(tokens)))

    return mailroom.QueryMetadata(
        attributes=attributes,
        fields=[field_ref(f) for f in fields],
        groups=[],
        schemes=schemes,
        allow_as_group=not {"id", "flow", "group", "history", "status"}.intersection(tokens),
    )


class Mocks:
    def __init__(self):
        self.calls = defaultdict(list)
        self._parse_query = {}
        self._contact_search = {}
        self._flow_start_preview = []
        self._msg_broadcast_preview = []
        self._errors = []

        self.queued_batch_tasks = []

    def parse_query(self, query, *, cleaned=None, elastic_query=None, fields=None):
        def mock(org):
            return mailroom.ParsedQuery(
                query=cleaned or query,
                elastic_query=elastic_query or {"term": {"is_active": True}},
                metadata=mock_inspect_query(org, cleaned or query, fields),
            )

        self._parse_query[query] = mock

    def contact_search(self, query, *, cleaned=None, contacts=(), total=0, fields=()):
        def mock(org, offset, sort):
            return mailroom.SearchResults(
                query=cleaned or query,
                total=total or len(contacts),
                contact_ids=[c.id for c in contacts],
                metadata=mock_inspect_query(org, cleaned or query, fields),
            )

        self._contact_search[query] = mock

    def flow_start_preview(self, query, total):
        def mock(org):
            return mailroom.StartPreview(query=query, total=total)

        self._flow_start_preview.append(mock)

    def msg_broadcast_preview(self, query, total):
        def mock(org):
            return mailroom.BroadcastPreview(query=query, total=total)

        self._msg_broadcast_preview.append(mock)

    def error(self, msg: str, code: str = None, extra: dict = None):
        """
        Queues an error which will become a mailroom exception at the next client call
        """
        err = {"error": msg}
        if code:
            err["code"] = code
        if extra:
            err["extra"] = extra

        self._errors.append(err)

    def _check_error(self, endpoint: str):
        if self._errors:
            raise MailroomException(endpoint, None, self._errors.pop(0))


def _client_method(func):
    @functools.wraps(func)
    def wrap(self, *args, **kwargs):
        self.mocks.calls[func.__name__].append(call(*args, **kwargs))
        self.mocks._check_error(func.__name__)

        return func(self, *args, **kwargs)

    return wrap


class TestClient(MailroomClient):
    def __init__(self, mocks: Mocks):
        self.mocks = mocks

        super().__init__(settings.MAILROOM_URL, settings.MAILROOM_AUTH_TOKEN)

    @_client_method
    def contact_create(self, org_id: int, user_id: int, contact: ContactSpec):
        org = Org.objects.get(id=org_id)
        user = User.objects.get(id=user_id)

        obj = create_contact_locally(
            org, user, contact.name, contact.language, contact.urns, contact.fields, contact.groups
        )

        return {"contact": {"id": obj.id, "uuid": str(obj.uuid), "name": obj.name}}

    @_client_method
    def contact_modify(self, org_id, user_id, contact_ids, modifiers: list[Modifier]):
        org = Org.objects.get(id=org_id)
        user = User.objects.get(id=user_id)
        contacts = org.contacts.filter(id__in=contact_ids)

        apply_modifiers(org, user, contacts, modifiers)

        return {str(c.id): {"contact": {}, "events": []} for c in contacts}

    @_client_method
    def contact_resolve(self, org_id: int, channel_id: int, urn: str):
        org = Org.objects.get(id=org_id)
        user = get_anonymous_user()

        try:
            urn = URN.normalize(urn, org.default_country_code)
            if not URN.validate(urn, org.default_country_code):
                raise ValueError()
        except ValueError:
            raise MailroomException("contact/resolve", None, {"error": "invalid URN"})

        contact_urn = ContactURN.lookup(org, urn)
        if contact_urn:
            contact = contact_urn.contact
        else:
            contact = create_contact_locally(org, user, name="", language="", urns=[urn], fields={}, group_uuids=[])
            contact_urn = ContactURN.lookup(org, urn)

        return {
            "contact": {"id": contact.id, "uuid": str(contact.uuid), "name": contact.name},
            "urn": {"id": contact_urn.id, "identity": contact_urn.identity},
        }

    @_client_method
    def contact_inspect(self, org_id: int, contact_ids: list[int]):
        org = Org.objects.get(id=org_id)
        contacts = org.contacts.filter(id__in=contact_ids)

        def inspect(c) -> dict:
            sendable = []
            unsendable = []
            for urn in c.get_urns():
                channel = urn.channel or org.channels.filter(schemes__contains=[urn.scheme]).first()
                if channel:
                    sendable.append(
                        {
                            "channel": {"uuid": str(channel.uuid), "name": channel.name},
                            "scheme": urn.scheme,
                            "path": urn.path,
                            "display": urn.display or "",
                        }
                    )
                else:
                    unsendable.append(
                        {"channel": None, "scheme": urn.scheme, "path": urn.path, "display": urn.display or ""}
                    )

            return {"urns": sendable + unsendable}

        return {str(c.id): inspect(c) for c in contacts}

    @_client_method
    def contact_interrupt(self, org_id: int, user_id: int, contact_id: int):
        contact = Contact.objects.get(id=contact_id)

        # get the waiting session IDs
        session_ids = list(contact.sessions.filter(status=FlowSession.STATUS_WAITING).values_list("id", flat=True))

        exit_sessions(session_ids, FlowSession.STATUS_INTERRUPTED)

        return {"sessions": len(session_ids)}

    @_client_method
    def parse_query(self, org_id: int, query: str, parse_only: bool = False, group_uuid: str = ""):
        org = Org.objects.get(id=org_id)

        # if there's a mock for this query we use that
        mock = self.mocks._parse_query.get(query)
        if mock:
            return mock(org)

        return mailroom.ParsedQuery(
            query=query,
            elastic_query={"term": {"is_active": True}},
            metadata=mock_inspect_query(org, query),
        )

    @_client_method
    def contact_search(self, org_id, group_id, query, sort, offset=0, exclude_ids=()):
        mock = self.mocks._contact_search.get(query or "")

        assert mock, f"missing contact_search mock for query '{query}'"

        org = Org.objects.get(id=org_id)
        return mock(org, offset, sort)

    @_client_method
    def flow_start_preview(self, org_id: int, flow_id: int, include, exclude):
        assert self.mocks._flow_start_preview, "missing flow_start_preview mock"

        mock = self.mocks._flow_start_preview.pop(0)
        org = Org.objects.get(id=org_id)

        return mock(org)

    @_client_method
    def msg_broadcast_preview(self, org_id: int, include, exclude):
        assert self.mocks._msg_broadcast_preview, "missing msg_broadcast_preview mock"

        mock = self.mocks._msg_broadcast_preview.pop(0)
        org = Org.objects.get(id=org_id)

        return mock(org)

    @_client_method
    def msg_send(self, org_id: int, user_id: int, contact_id: int, text: str, attachments: list[str], ticket_id: int):
        org = Org.objects.get(id=org_id)
        contact = Contact.objects.get(org=org, id=contact_id)
        msg = send_to_contact(org, contact, text, attachments)

        return {
            "id": msg.id,
            "channel": {"uuid": str(msg.channel.uuid), "name": msg.channel.name} if msg.channel else None,
            "contact": {"uuid": str(msg.contact.uuid), "name": msg.contact.name},
            "urn": str(msg.contact_urn) if msg.contact_urn else "",
            "text": msg.text,
            "attachments": msg.attachments,
            "status": msg.status,
            "created_on": msg.created_on.isoformat(),
            "modified_on": msg.modified_on.isoformat(),
        }

    @_client_method
    def ticket_assign(self, org_id, user_id, ticket_ids, assignee_id):
        now = timezone.now()
        tickets = Ticket.objects.filter(org_id=org_id, id__in=ticket_ids).exclude(assignee_id=assignee_id)

        for ticket in tickets:
            ticket.events.create(
                org_id=org_id,
                contact=ticket.contact,
                event_type=TicketEvent.TYPE_ASSIGNED,
                assignee_id=assignee_id,
                created_by_id=user_id,
            )

        tickets.update(assignee_id=assignee_id, modified_on=now, last_activity_on=now)

        return {"changed_ids": [t.id for t in tickets]}

    @_client_method
    def ticket_add_note(self, org_id, user_id, ticket_ids, note):
        now = timezone.now()
        tickets = Ticket.objects.filter(org_id=org_id, id__in=ticket_ids)
        tickets.update(modified_on=now, last_activity_on=now)

        for ticket in tickets:
            ticket.events.create(
                org_id=org_id,
                contact=ticket.contact,
                event_type=TicketEvent.TYPE_NOTE_ADDED,
                note=note,
                created_by_id=user_id,
            )

        return {"changed_ids": [t.id for t in tickets]}

    @_client_method
    def ticket_change_topic(self, org_id, user_id, ticket_ids, topic_id):
        now = timezone.now()
        topic = Topic.objects.get(id=topic_id)
        tickets = Ticket.objects.filter(org_id=org_id, id__in=ticket_ids)
        tickets.update(topic=topic, modified_on=now, last_activity_on=now)

        for ticket in tickets:
            ticket.events.create(
                org_id=org_id,
                contact=ticket.contact,
                event_type=TicketEvent.TYPE_TOPIC_CHANGED,
                topic=topic,
                created_by_id=user_id,
            )

        return {"changed_ids": [t.id for t in tickets]}

    @_client_method
    def ticket_close(self, org_id: int, user_id: int, ticket_ids: list, force: bool):
        tickets = Ticket.objects.filter(org_id=org_id, status=Ticket.STATUS_OPEN, id__in=ticket_ids)

        for ticket in tickets:
            ticket.events.create(
                org_id=org_id, contact=ticket.contact, event_type=TicketEvent.TYPE_CLOSED, created_by_id=user_id
            )

        tickets.update(status=Ticket.STATUS_CLOSED, closed_on=timezone.now())

        return {"changed_ids": [t.id for t in tickets]}

    @_client_method
    def ticket_reopen(self, org_id, user_id, ticket_ids):
        tickets = Ticket.objects.filter(org_id=org_id, status=Ticket.STATUS_CLOSED, id__in=ticket_ids)

        for ticket in tickets:
            ticket.events.create(
                org_id=org_id, contact=ticket.contact, event_type=TicketEvent.TYPE_REOPENED, created_by_id=user_id
            )

        tickets.update(status=Ticket.STATUS_OPEN, closed_on=None)

        return {"changed_ids": [t.id for t in tickets]}


def mock_mailroom(method=None, *, client=True, queue=True):
    """
    Convenience decorator to make a test method use a mocked version of the mailroom client
    """

    def actual_decorator(f):
        @wraps(f)
        def wrapper(instance, *args, **kwargs):
            _wrap_test_method(f, client, queue, instance, *args, **kwargs)

        return wrapper

    return actual_decorator(method) if method else actual_decorator


def _wrap_test_method(f, mock_client: bool, mock_queue: bool, instance, *args, **kwargs):
    mocks = Mocks()

    patch_get_client = None
    patch_queue_batch_task = None

    try:
        if mock_client:
            patch_get_client = patch("temba.mailroom.get_client")
            mock_get_client = patch_get_client.start()
            mock_get_client.return_value = TestClient(mocks)

        if mock_queue:
            patch_queue_batch_task = patch("temba.mailroom.queue._queue_batch_task")
            mock_queue_batch_task = patch_queue_batch_task.start()

            def queue_batch_task(org_id, task_type, task, priority):
                mocks.queued_batch_tasks.append(
                    {"type": task_type.value, "org_id": org_id, "task": task, "queued_on": timezone.now()}
                )

            mock_queue_batch_task.side_effect = queue_batch_task

        return f(instance, mocks, *args, **kwargs)
    finally:
        if patch_get_client:
            patch_get_client.stop()
        if patch_queue_batch_task:
            patch_queue_batch_task.stop()


def apply_modifiers(org, user, contacts, modifiers: list):
    """
    Approximates mailroom applying modifiers but doesn't do dynamic group re-evaluation.
    """

    for mod in modifiers:
        fields = dict()
        clear_groups = False

        if mod.type == "name":
            fields = dict(name=mod.name)

        if mod.type == "language":
            fields = dict(language=mod.language)

        if mod.type == "field":
            for c in contacts:
                update_field_locally(user, c, mod.field.key, mod.value, name=mod.field.name)

        elif mod.type == "status":
            if mod.status == "blocked":
                fields = dict(status=Contact.STATUS_BLOCKED)
                clear_groups = True
            elif mod.status == "stopped":
                fields = dict(status=Contact.STATUS_STOPPED)
                clear_groups = True
            elif mod.status == "archived":
                fields = dict(status=Contact.STATUS_ARCHIVED)
                clear_groups = True
            else:
                fields = dict(status=Contact.STATUS_ACTIVE)

        elif mod.type == "groups":
            add = mod.modification == "add"
            for contact in contacts:
                update_groups_locally(contact, [g.uuid for g in mod.groups], add=add)

        elif mod.type == "ticket":
            topic = org.topics.get(uuid=mod.topic.uuid, is_active=True)
            assignee = org.users.get(email=mod.assignee.email, is_active=True) if mod.assignee else None
            for contact in contacts:
                contact.tickets.create(
                    org=org,
                    topic=topic,
                    status=Ticket.STATUS_OPEN,
                    body=mod.body,
                    assignee=assignee,
                )

        elif mod.type == "urns":
            assert len(contacts) == 1, "should never be trying to bulk update contact URNs"
            assert mod.modification == "set", "should only be setting URNs from here"

            update_urns_locally(contacts[0], mod.urns)

        contacts.update(modified_by=user, modified_on=timezone.now(), **fields)
        if clear_groups:
            for c in contacts:
                for g in c.get_groups():
                    g.contacts.remove(c)


def create_contact_locally(
    org, user, name, language, urns, fields, group_uuids, status=Contact.STATUS_ACTIVE, last_seen_on=None
):
    orphaned_urns = {}

    for urn in urns:
        existing = ContactURN.lookup(org, urn)
        if existing:
            if existing.contact_id:
                raise MailroomException("contact/create", None, {"error": "URNs in use by other contacts"})
            else:
                orphaned_urns[urn] = existing

    contact = Contact.objects.create(
        org=org,
        name=name,
        language=language,
        created_by=user,
        modified_by=user,
        created_on=timezone.now(),
        status=status,
        last_seen_on=last_seen_on,
    )
    update_urns_locally(contact, urns)
    update_fields_locally(user, contact, fields)
    update_groups_locally(contact, group_uuids, add=True)
    return contact


def update_fields_locally(user, contact, fields):
    for key, val in fields.items():
        update_field_locally(user, contact, key, val)


def update_field_locally(user, contact, key, value, name=None):
    org = contact.org
    field = ContactField.get_or_create(contact.org, user, key, name=name)

    field_uuid = str(field.uuid)
    if contact.fields is None:
        contact.fields = {}

    if not value:
        value = None
        if field_uuid in contact.fields:
            del contact.fields[field_uuid]

    else:
        field_dict = serialize_field_value(contact, field, value)

        if contact.fields.get(field_uuid) != field_dict:
            contact.fields[field_uuid] = field_dict

    # update our JSONB on our contact
    with connection.cursor() as cursor:
        if value is None:
            # delete the field
            cursor.execute("UPDATE contacts_contact SET fields = fields - %s WHERE id = %s", [field_uuid, contact.id])
        else:
            # update the field
            cursor.execute(
                "UPDATE contacts_contact SET fields = COALESCE(fields,'{}'::jsonb) || %s::jsonb WHERE id = %s",
                [json.dumps({field_uuid: contact.fields[field_uuid]}), contact.id],
            )

    # very simplified version of mailroom's campaign event scheduling
    events = CampaignEvent.objects.filter(relative_to=field, campaign__group__in=contact.groups.all())
    for event in events:
        EventFire.objects.filter(contact=contact, event=event).delete()
        date_value = parse_datetime(org, value)
        if date_value:
            scheduled = date_value + timedelta(**{event_units[event.unit]: event.offset})
            if scheduled > timezone.now():
                EventFire.objects.create(contact=contact, event=event, scheduled=scheduled)


def update_urns_locally(contact, urns: list[str]):
    country = contact.org.default_country_code
    priority = ContactURN.PRIORITY_HIGHEST

    urns_created = []  # new URNs created
    urns_attached = []  # existing orphan URNs attached
    urns_retained = []  # existing URNs retained

    for urn_as_string in urns:
        normalized = URN.normalize(urn_as_string, country)
        urn = ContactURN.lookup(contact.org, normalized)

        if not urn:
            urn = ContactURN.create(contact.org, contact, normalized, priority=priority)
            urns_created.append(urn)

        # unassigned URN or different contact
        elif not urn.contact or urn.contact != contact:
            urn.contact = contact
            urn.priority = priority
            urn.save()
            urns_attached.append(urn)

        else:
            if urn.priority != priority:
                urn.priority = priority
                urn.save()
            urns_retained.append(urn)

        # step down our priority
        priority -= 1

    # detach any existing URNs that weren't included
    urn_ids = [u.pk for u in (urns_created + urns_attached + urns_retained)]
    urns_detached = ContactURN.objects.filter(contact=contact).exclude(id__in=urn_ids)
    urns_detached.update(contact=None)


def update_groups_locally(contact, group_uuids, add: bool):
    groups = ContactGroup.objects.filter(uuid__in=group_uuids)
    for group in groups:
        assert group.group_type == ContactGroup.TYPE_MANUAL, "can only add/remove contacts to/from manual groups"
        if add:
            group.contacts.add(contact)
        else:
            group.contacts.remove(contact)


def serialize_field_value(contact, field, value):
    org = contact.org

    # parse as all value data types
    str_value = str(value)[:640]
    dt_value = parse_datetime(org, value)
    num_value = parse_number(value)
    loc_value = None

    # for locations, if it has a '>' then it is explicit, look it up that way
    if AdminBoundary.PATH_SEPARATOR in str_value:
        loc_value = parse_location_path(contact.org, str_value)

    # otherwise, try to parse it as a name at the appropriate level
    else:
        if field.value_type == ContactField.TYPE_WARD:
            district_field = org.fields.filter(value_type=ContactField.TYPE_DISTRICT).first()
            district_value = contact.get_field_value(district_field)
            if district_value:
                loc_value = parse_location(org, str_value, AdminBoundary.LEVEL_WARD, district_value)

        elif field.value_type == ContactField.TYPE_DISTRICT:
            state_field = org.fields.filter(value_type=ContactField.TYPE_STATE).first()
            if state_field:
                state_value = contact.get_field_value(state_field)
                if state_value:
                    loc_value = parse_location(org, str_value, AdminBoundary.LEVEL_DISTRICT, state_value)

        elif field.value_type == ContactField.TYPE_STATE:
            loc_value = parse_location(org, str_value, AdminBoundary.LEVEL_STATE)

        if loc_value is not None and len(loc_value) > 0:
            loc_value = loc_value[0]
        else:
            loc_value = None

    # all fields have a text value
    field_dict = {"text": str_value}

    # set all the other fields that have a non-zero value
    if dt_value is not None:
        field_dict["datetime"] = timezone.localtime(dt_value, org.timezone).isoformat()

    if num_value is not None:
        num_as_int = num_value.to_integral_value()
        field_dict["number"] = int(num_as_int) if num_value == num_as_int else num_value

    if loc_value:
        if loc_value.level == AdminBoundary.LEVEL_STATE:
            field_dict["state"] = loc_value.path
        elif loc_value.level == AdminBoundary.LEVEL_DISTRICT:
            field_dict["district"] = loc_value.path
            field_dict["state"] = AdminBoundary.strip_last_path(loc_value.path)
        elif loc_value.level == AdminBoundary.LEVEL_WARD:
            field_dict["ward"] = loc_value.path
            field_dict["district"] = AdminBoundary.strip_last_path(loc_value.path)
            field_dict["state"] = AdminBoundary.strip_last_path(field_dict["district"])

    return field_dict


def parse_number(s):
    parsed = None
    try:
        parsed = Decimal(s)

        if not parsed.is_finite() or parsed > Decimal("999999999999999999999999"):
            parsed = None
    except Exception:
        pass
    return parsed


def parse_location(org, location_string, level, parent=None):
    """
    Simplified version of mailroom's location parsing
    """
    # no country? bail
    if not org.country_id or not isinstance(location_string, str):
        return []

    boundary = None

    # try it as a path first if it looks possible
    if level == AdminBoundary.LEVEL_COUNTRY or AdminBoundary.PATH_SEPARATOR in location_string:
        boundary = parse_location_path(org, location_string)
        if boundary:
            boundary = [boundary]

    # try to look up it by full name
    if not boundary:
        boundary = find_boundary_by_name(org, location_string, level, parent)

    # try removing punctuation and try that
    if not boundary:
        bare_name = re.sub(r"\W+", " ", location_string, flags=re.UNICODE).strip()
        boundary = find_boundary_by_name(org, bare_name, level, parent)

    return boundary


def parse_location_path(org, location_string):
    """
    Parses a location path into a single location, returning None if not found
    """
    return (
        AdminBoundary.objects.filter(path__iexact=location_string.strip()).first()
        if org.country_id and isinstance(location_string, str)
        else None
    )


def find_boundary_by_name(org, name, level, parent):
    # first check if we have a direct name match
    if parent:
        boundary = parent.children.filter(name__iexact=name, level=level)
    else:
        query = dict(name__iexact=name, level=level)
        query["__".join(["parent"] * level)] = org.country
        boundary = AdminBoundary.objects.filter(**query)

    return boundary


def exit_sessions(session_ids: list, status: str):
    FlowRun.objects.filter(session_id__in=session_ids).update(
        status=status, exited_on=timezone.now(), modified_on=timezone.now()
    )
    FlowSession.objects.filter(id__in=session_ids).update(
        status=status,
        ended_on=timezone.now(),
        wait_started_on=None,
        wait_expires_on=None,
        timeout_on=None,
        current_flow_id=None,
    )

    for session in FlowSession.objects.filter(id__in=session_ids):
        session.contact.current_flow = None
        session.contact.modified_on = timezone.now()
        session.contact.save(update_fields=("current_flow", "modified_on"))


def send_to_contact(org, contact, text, attachments) -> Msg:
    contact_urn = contact.get_urn()
    channel = Channel.objects.filter(org=org).first()

    if contact_urn and channel:
        status = "Q"
        failed_reason = None
    else:
        contact_urn = None
        channel = None
        status = "F"
        failed_reason = Msg.FAILED_NO_DESTINATION

    return Msg.objects.create(
        org=org,
        channel=channel,
        contact=contact,
        contact_urn=contact_urn,
        status=status,
        failed_reason=failed_reason,
        text=text or "",
        attachments=attachments or [],
        msg_type=Msg.TYPE_TEXT,
        created_on=timezone.now(),
        modified_on=timezone.now(),
    )
