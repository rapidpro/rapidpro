import functools
import re
from collections import defaultdict
from datetime import timedelta
from functools import wraps
from typing import Dict, List
from unittest.mock import call, patch

from django.conf import settings
from django.contrib.auth.models import User
from django.db import connection
from django.utils import timezone

from temba.campaigns.models import CampaignEvent, EventFire
from temba.contacts.models import URN, Contact, ContactField, ContactGroup, ContactURN
from temba.locations.models import AdminBoundary
from temba.mailroom.client import ContactSpec, MailroomClient, MailroomException
from temba.mailroom.modifiers import Modifier
from temba.orgs.models import Org
from temba.tickets.models import Ticket
from temba.utils import format_number, get_anonymous_user, json

event_units = {
    CampaignEvent.UNIT_MINUTES: "minutes",
    CampaignEvent.UNIT_HOURS: "hours",
    CampaignEvent.UNIT_DAYS: "days",
    CampaignEvent.UNIT_WEEKS: "weeks",
}


class Mocks:
    def __init__(self):
        self.calls = defaultdict(list)
        self._parse_query = {}
        self._contact_search = {}
        self._errors = []

        self.queued_batch_tasks = []

    @staticmethod
    def _parse_query_response(query: str, elastic: Dict, fields: List, allow_as_group: bool):
        def field_ref(f):
            return {"key": f.key, "name": f.label} if isinstance(f, ContactField) else {"key": f}

        return {
            "query": query,
            "elastic_query": elastic,
            "metadata": {
                "attributes": [],
                "schemes": [],
                "fields": [field_ref(f) for f in fields],
                "groups": [],
                "allow_as_group": allow_as_group,
            },
        }

    def parse_query(self, query, *, cleaned=None, fields=(), allow_as_group=True, elastic_query=None):
        def mock():
            elastic = elastic_query or {"term": {"is_active": True}}
            return self._parse_query_response(cleaned or query, elastic, fields, allow_as_group)

        self._parse_query[query] = mock

    def contact_search(self, query, *, cleaned=None, contacts=(), total=0, fields=(), allow_as_group=True):
        def mock(offset, sort):
            return {
                "query": cleaned or query,
                "contact_ids": [c.id for c in contacts],
                "total": total or len(contacts),
                "offset": offset,
                "sort": sort,
                "metadata": {
                    "attributes": [],
                    "schemes": [],
                    "fields": [{"key": f.key, "name": f.label} for f in fields],
                    "groups": [],
                    "allow_as_group": allow_as_group,
                },
            }

        self._contact_search[query] = mock

    def error(self, msg: str, code: str = None, extra: Dict = None):
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
    def contact_modify(self, org_id, user_id, contact_ids, modifiers: List[Modifier]):
        org = Org.objects.get(id=org_id)
        user = User.objects.get(id=user_id)
        contacts = org.contacts.filter(id__in=contact_ids)

        apply_modifiers(org, user, contacts, modifiers)

        return {c.id: {"contact": {}, "events": []} for c in contacts}

    @_client_method
    def contact_resolve(self, org_id: int, channel_id: int, urn: str):
        org = Org.objects.get(id=org_id)
        user = get_anonymous_user()

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
    def parse_query(self, org_id, query, group_uuid=""):
        # if there's a mock for this query we use that
        mock = self.mocks._parse_query.get(query)
        if mock:
            return mock()

        # otherwise just approximate what mailroom would do
        tokens = [t.lower() for t in re.split(r"\W+", query) if t]
        fields = ContactField.all_fields.filter(org_id=org_id, key__in=tokens)
        allow_as_group = "id" not in tokens and "group" not in tokens

        return Mocks._parse_query_response(query, {"term": {"is_active": True}}, fields, allow_as_group)

    @_client_method
    def contact_search(self, org_id, group_uuid, query, sort, offset=0, exclude_ids=()):
        mock = self.mocks._contact_search.get(query or "")

        assert mock, f"missing contact_search mock for query '{query}'"

        return mock(offset, sort)

    @_client_method
    def ticket_close(self, org_id, ticket_ids):
        tickets = Ticket.objects.filter(org_id=org_id, status=Ticket.STATUS_OPEN, id__in=ticket_ids)
        tickets.update(status=Ticket.STATUS_CLOSED)

        return {"changed_ids": [t.id for t in tickets]}

    @_client_method
    def ticket_reopen(self, org_id, ticket_ids):
        tickets = Ticket.objects.filter(org_id=org_id, status=Ticket.STATUS_CLOSED, id__in=ticket_ids)
        tickets.update(status=Ticket.STATUS_OPEN)

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


def apply_modifiers(org, user, contacts, modifiers: List):
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
                update_field_locally(user, c, mod.field.key, mod.value, label=mod.field.name)

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

        elif mod.type == "urns":
            assert len(contacts) == 1, "should never be trying to bulk update contact URNs"
            assert mod.modification == "set", "should only be setting URNs from here"

            update_urns_locally(contacts[0], mod.urns)

        contacts.update(modified_by=user, modified_on=timezone.now(), **fields)
        if clear_groups:
            for c in contacts:
                for g in c.user_groups.all():
                    g.contacts.remove(c)


def create_contact_locally(org, user, name, language, urns, fields, group_uuids, last_seen_on=None):
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
        last_seen_on=last_seen_on,
    )
    update_urns_locally(contact, urns)
    update_fields_locally(user, contact, fields)
    update_groups_locally(contact, group_uuids, add=True)
    return contact


def update_fields_locally(user, contact, fields):
    for key, val in fields.items():
        update_field_locally(user, contact, key, val)


def update_field_locally(user, contact, key, value, label=None):
    org = contact.org
    field = ContactField.get_or_create(contact.org, user, key, label=label)

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
    events = CampaignEvent.objects.filter(relative_to=field, campaign__group__in=contact.user_groups.all())
    for event in events:
        EventFire.objects.filter(contact=contact, event=event).delete()
        date_value = org.parse_datetime(value)
        if date_value:
            scheduled = date_value + timedelta(**{event_units[event.unit]: event.offset})
            if scheduled > timezone.now():
                EventFire.objects.create(contact=contact, event=event, scheduled=scheduled)


def update_urns_locally(contact, urns: List[str]):
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
    groups = ContactGroup.user_groups.filter(uuid__in=group_uuids)
    for group in groups:
        assert not group.is_dynamic, "can't add/remove contacts from smart groups"
        if add:
            group.contacts.add(contact)
        else:
            group.contacts.remove(contact)


def serialize_field_value(contact, field, value):
    org = contact.org

    # parse as all value data types
    str_value = str(value)[:640]
    dt_value = org.parse_datetime(value)
    num_value = org.parse_number(value)
    loc_value = None

    # for locations, if it has a '>' then it is explicit, look it up that way
    if AdminBoundary.PATH_SEPARATOR in str_value:
        loc_value = contact.org.parse_location_path(str_value)

    # otherwise, try to parse it as a name at the appropriate level
    else:
        if field.value_type == ContactField.TYPE_WARD:
            district_field = ContactField.get_location_field(org, ContactField.TYPE_DISTRICT)
            district_value = contact.get_field_value(district_field)
            if district_value:
                loc_value = org.parse_location(str_value, AdminBoundary.LEVEL_WARD, district_value)

        elif field.value_type == ContactField.TYPE_DISTRICT:
            state_field = ContactField.get_location_field(org, ContactField.TYPE_STATE)
            if state_field:
                state_value = contact.get_field_value(state_field)
                if state_value:
                    loc_value = org.parse_location(str_value, AdminBoundary.LEVEL_DISTRICT, state_value)

        elif field.value_type == ContactField.TYPE_STATE:
            loc_value = org.parse_location(str_value, AdminBoundary.LEVEL_STATE)

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
        field_dict["number"] = format_number(num_value)

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
