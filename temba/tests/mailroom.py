import re
from typing import List
from unittest.mock import patch

from django.conf import settings
from django.contrib.auth.models import User
from django.utils import timezone

from temba.contacts.models import URN, Contact, ContactField, ContactGroup, ContactURN
from temba.mailroom.client import MailroomClient, MailroomException
from temba.mailroom.modifiers import Modifier


class Mocks:
    def __init__(self):
        self._parse_query = {}

    def parse_query(self, query, *, fields=None, allow_as_group=True, elastic_query=None, error: str = None):
        assert not (fields and error), "can't mock with both fields and error"

        def mock():
            if error:
                raise MailroomException("mr/parse_query", None, {"error": error})

            return {
                "query": query,
                "fields": list(fields),
                "allow_as_group": allow_as_group,
                "elastic_query": elastic_query or {"term": {"is_active": True}},
            }

        self._parse_query[query] = mock


class TestClient(MailroomClient):
    def __init__(self, mocks: Mocks):
        self.mocks = mocks

        super().__init__(settings.MAILROOM_URL, settings.MAILROOM_AUTH_TOKEN)

    def contact_modify(self, org_id, user_id, contact_ids, modifiers: List[Modifier]):
        user = User.objects.get(id=user_id)
        contacts = Contact.objects.filter(org_id=org_id, id__in=contact_ids)

        apply_modifiers(user, contacts, modifiers)

        return {c.id: {"contact": {}, "events": []} for c in contacts}

    def parse_query(self, org_id, query, group_uuid=""):
        # if there's a mock for this query we use that
        mock = self.mocks._parse_query.get(query)
        if mock:
            return mock()

        # otherwise just approximate what mailroom would do
        tokens = [t.lower() for t in re.split(r"\W+", query) if t]
        fields = ContactField.all_fields.filter(org_id=org_id, key__in=tokens)

        return {
            "query": query,
            "fields": [f.key for f in fields],
            "allow_as_group": "id" not in tokens and "group" not in tokens,
            "elastic_query": {"term": {"is_active": True}},
        }


def mock_mailroom(f):
    """
    Convenience decorator to make a test method use a mocked version of the mailroom client
    """

    def wrapped(instance, *args, **kwargs):
        with patch("temba.mailroom.get_client") as mock_get_client:
            mocks = Mocks()
            mock_get_client.return_value = TestClient(mocks)
            return f(instance, mocks, *args, **kwargs)

    return wrapped


def apply_modifiers(user, contacts, modifiers: List):
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

        elif mod.type == "status":
            if mod.status == "blocked":
                fields = dict(is_blocked=True, is_stopped=False)
                clear_groups = True
            elif mod.status == "stopped":
                fields = dict(is_blocked=False, is_stopped=True)
                clear_groups = True
            else:
                fields = dict(is_blocked=False, is_stopped=False)

        elif mod.type == "groups":
            add = mod.modification == "add"
            groups = ContactGroup.user_groups.filter(query=None, uuid__in=[g.uuid for g in mod.groups])
            for group in groups:
                group.update_contacts(user, contacts, add=add)

        elif mod.type == "urns":
            assert len(contacts) == 1, "should never be trying to bulk update contact URNs"
            assert mod.modification == "set", "should only be setting URNs from here"

            update_urns_locally(contacts[0], mod.urns)

        contacts.update(modified_by=user, modified_on=timezone.now(), **fields)
        if clear_groups:
            for c in contacts:
                Contact.objects.get(id=c.id).clear_all_groups(user)


def update_urns_locally(contact, urns: List[str]):
    country = contact.org.get_country_code()
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
