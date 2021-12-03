import io
import subprocess
import time
import uuid
from datetime import date, datetime, timedelta
from decimal import Decimal
from unittest.mock import PropertyMock, call, patch

import iso8601
import pytz
from openpyxl import load_workbook

from django.conf import settings
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.validators import ValidationError
from django.db import connection
from django.db.models import Value as DbValue
from django.db.models.functions import Concat, Substr
from django.db.utils import IntegrityError
from django.test.utils import override_settings
from django.urls import reverse
from django.utils import timezone

from temba.airtime.models import AirtimeTransfer
from temba.campaigns.models import Campaign, CampaignEvent, EventFire
from temba.channels.models import Channel, ChannelEvent, ChannelLog
from temba.contacts.search import SearchException, SearchResults, search_contacts
from temba.contacts.views import ContactListView
from temba.flows.models import Flow, FlowSession, FlowStart
from temba.ivr.models import IVRCall
from temba.locations.models import AdminBoundary
from temba.mailroom import MailroomException, modifiers
from temba.msgs.models import Broadcast, Label, Msg, SystemLabel
from temba.orgs.models import Org
from temba.schedules.models import Schedule
from temba.tests import (
    AnonymousOrg,
    CRUDLTestMixin,
    ESMockWithScroll,
    TembaNonAtomicTest,
    TembaTest,
    matchers,
    mock_mailroom,
)
from temba.tests.engine import MockSessionWriter
from temba.tests.s3 import MockS3Client
from temba.tickets.models import Ticket, TicketCount, Ticketer
from temba.triggers.models import Trigger
from temba.utils import json
from temba.utils.dates import datetime_to_str, datetime_to_timestamp

from .models import (
    URN,
    Contact,
    ContactField,
    ContactGroup,
    ContactGroupCount,
    ContactImport,
    ContactImportBatch,
    ContactURN,
    ExportContactsTask,
)
from .tasks import check_elasticsearch_lag, squash_contactgroupcounts
from .templatetags.contacts import contact_field, history_class, history_icon


class ContactCRUDLTest(CRUDLTestMixin, TembaTest):
    def setUp(self):
        super().setUp()

        self.country = AdminBoundary.create(osm_id="171496", name="Rwanda", level=0)
        AdminBoundary.create(osm_id="1708283", name="Kigali", level=1, parent=self.country)

        ContactField.get_or_create(self.org, self.user, "age", "Age", value_type="N")
        ContactField.get_or_create(self.org, self.user, "home", "Home", value_type="S", priority=10)

        # sample flows don't actually get created by org initialization during tests because there are no users at that
        # point so create them explicitly here, so that we also get the sample groups
        self.org.create_sample_flows("https://api.rapidpro.io")

    def test_menu(self):
        menu_url = reverse("contacts.contact_menu")
        response = self.assertListFetch(menu_url, allow_viewers=True, allow_editors=True, allow_agents=False)
        menu = response.json()["results"]
        self.assertEqual(
            [
                {"id": "active", "count": 0, "name": "Active", "href": "/contact/"},
                {"id": "blocked", "count": 0, "name": "Blocked", "href": "/contact/blocked/"},
                {"id": "stopped", "count": 0, "name": "Stopped", "href": "/contact/stopped/"},
                {"id": "archived", "count": 0, "name": "Archived", "href": "/contact/archived/"},
                {
                    "id": "smart",
                    "icon": "atom",
                    "name": "Smart Groups",
                    "href": "/contactgroup/?type=smart",
                    "count": 0,
                },
                {"id": "groups", "icon": "users", "name": "Groups", "href": "/contactgroup/?type=static", "count": 2},
                {
                    "id": "fields",
                    "icon": "layers",
                    "count": 2,
                    "name": "Fields",
                    "href": "/contactfield/",
                    "endpoint": "/contactfield/menu/",
                    "inline": True,
                },
                {"id": "import", "icon": "upload-cloud", "href": "/contactimport/create/", "name": "Import"},
            ],
            menu,
        )

    @mock_mailroom
    def test_list(self, mr_mocks):
        self.login(self.user)
        list_url = reverse("contacts.contact_list")

        joe = self.create_contact("Joe", phone="123", fields={"age": "20", "home": "Kigali"})
        frank = self.create_contact("Frank", phone="124", fields={"age": "18"})

        creating = ContactGroup.create_static(
            self.org, self.user, "Group being created", status=ContactGroup.STATUS_INITIALIZING
        )

        with self.assertNumQueries(60):
            response = self.client.get(list_url)

        self.assertEqual([frank, joe], list(response.context["object_list"]))
        self.assertIsNone(response.context["search_error"])
        self.assertEqual([], list(response.context["actions"]))

        active_contacts = ContactGroup.system_groups.get(org=self.org, group_type="A")
        survey_audience = ContactGroup.user_groups.get(org=self.org, name="Survey Audience")
        unsatisfied = ContactGroup.user_groups.get(org=self.org, name="Unsatisfied Customers")

        self.assertEqual(
            response.context["groups"],
            [
                {
                    "uuid": str(creating.uuid),
                    "pk": creating.id,
                    "label": "Group being created",
                    "is_dynamic": False,
                    "is_ready": False,
                    "count": 0,
                },
                {
                    "uuid": str(survey_audience.uuid),
                    "pk": survey_audience.id,
                    "label": "Survey Audience",
                    "is_dynamic": False,
                    "is_ready": True,
                    "count": 0,
                },
                {
                    "uuid": str(unsatisfied.uuid),
                    "pk": unsatisfied.id,
                    "label": "Unsatisfied Customers",
                    "is_dynamic": False,
                    "is_ready": True,
                    "count": 0,
                },
            ],
        )

        # fetch with spa flag
        response = self.client.get(list_url, content_type="application/json", HTTP_TEMBA_SPA="1")
        self.assertEqual(response.context["base_template"], "spa.html")

        mr_mocks.contact_search("age = 18", contacts=[frank], allow_as_group=True)

        response = self.client.get(list_url + "?search=age+%3D+18")
        self.assertEqual(list(response.context["object_list"]), [frank])
        self.assertEqual(response.context["search"], "age = 18")
        self.assertEqual(response.context["save_dynamic_search"], True)
        self.assertIsNone(response.context["search_error"])
        self.assertEqual(list(response.context["contact_fields"].values_list("label", flat=True)), ["Home", "Age"])

        mr_mocks.contact_search("age = 18", contacts=[frank], total=10020, allow_as_group=True)

        # we return up to 10000 contacts when searching with ES, so last page is 200
        url = f'{reverse("contacts.contact_list")}?{"search=age+%3D+18&page=200"}'
        response = self.client.get(url)

        self.assertEqual(response.status_code, 200)

        # when user requests page 201, we return a 404, page not found
        url = f'{reverse("contacts.contact_list")}?{"search=age+%3D+18&page=201"}'
        response = self.client.get(url)

        self.assertEqual(response.status_code, 404)

        mr_mocks.contact_search('age > 18 and home = "Kigali"', cleaned='age > 18 AND home = "Kigali"', contacts=[joe])

        response = self.client.get(list_url + '?search=age+>+18+and+home+%3D+"Kigali"')
        self.assertEqual(list(response.context["object_list"]), [joe])
        self.assertEqual(response.context["search"], 'age > 18 AND home = "Kigali"')
        self.assertEqual(response.context["save_dynamic_search"], True)
        self.assertIsNone(response.context["search_error"])

        mr_mocks.contact_search("Joe", cleaned='name ~ "Joe"', contacts=[joe])

        response = self.client.get(list_url + "?search=Joe")
        self.assertEqual(list(response.context["object_list"]), [joe])
        self.assertEqual(response.context["search"], 'name ~ "Joe"')
        self.assertEqual(response.context["save_dynamic_search"], True)
        self.assertIsNone(response.context["search_error"])

        with AnonymousOrg(self.org):
            mr_mocks.contact_search(f"{joe.id}", cleaned=f"id = {joe.id}", contacts=[joe], allow_as_group=False)

            response = self.client.get(list_url + f"?search={joe.id}")
            self.assertEqual(list(response.context["object_list"]), [joe])
            self.assertIsNone(response.context["search_error"])
            self.assertEqual(response.context["search"], f"id = {joe.id}")
            self.assertEqual(response.context["save_dynamic_search"], False)

        # try with invalid search string
        mr_mocks.error("mismatched input at (((", code="unexpected_token", extra={"token": "((("})

        response = self.client.get(list_url + "?search=(((")
        self.assertEqual(list(response.context["object_list"]), [])
        self.assertEqual(response.context["search_error"], "Invalid query syntax at '((('")

        self.login(self.admin)

        # admins can see bulk actions
        response = self.client.get(list_url)
        self.assertEqual([frank, joe], list(response.context["object_list"]))
        self.assertEqual(["label", "block", "archive"], list(response.context["actions"]))

        # try label bulk action
        self.client.post(list_url, {"action": "label", "objects": frank.id, "label": survey_audience.id})
        self.assertIn(frank, survey_audience.contacts.all())

        # try label bulk action against search results
        self.client.post(list_url + "?search=Joe", {"action": "label", "objects": joe.id, "label": survey_audience.id})
        self.assertIn(joe, survey_audience.contacts.all())

        self.assertEqual(
            call(self.org.id, group_uuid=str(active_contacts.uuid), query="Joe", sort="", offset=0, exclude_ids=[]),
            mr_mocks.calls["contact_search"][-1],
        )

        # try archive bulk action
        self.client.post(list_url + "?search=Joe", {"action": "archive", "objects": joe.id})

        # we re-run the search for the response, but exclude Joe
        self.assertEqual(
            call(
                self.org.id, group_uuid=str(active_contacts.uuid), query="Joe", sort="", offset=0, exclude_ids=[joe.id]
            ),
            mr_mocks.calls["contact_search"][-1],
        )

        response = self.client.get(list_url)
        self.assertEqual([frank], list(response.context["object_list"]))

        joe.refresh_from_db()
        self.assertEqual(Contact.STATUS_ARCHIVED, joe.status)

    @mock_mailroom
    def test_blocked(self, mr_mocks):
        joe = self.create_contact("Joe", urns=["twitter:joe"])
        frank = self.create_contact("Frank", urns=["twitter:frank"])
        billy = self.create_contact("Billy", urns=["twitter:billy"])
        self.create_contact("Mary", urns=["twitter:mary"])

        joe.block(self.admin)
        frank.block(self.admin)
        billy.block(self.admin)

        self.login(self.user)

        blocked_url = reverse("contacts.contact_blocked")

        response = self.client.get(blocked_url)
        self.assertEqual([billy, frank, joe], list(response.context["object_list"]))
        self.assertEqual([], list(response.context["actions"]))

        self.login(self.admin)

        # admin users see bulk actions
        response = self.client.get(blocked_url)
        self.assertEqual(["restore", "archive"], list(response.context["actions"]))

        # try restore bulk action
        self.client.post(blocked_url, {"action": "restore", "objects": billy.id})

        response = self.client.get(blocked_url)
        self.assertEqual([frank, joe], list(response.context["object_list"]))

        billy.refresh_from_db()
        self.assertEqual(Contact.STATUS_ACTIVE, billy.status)

        # try archive bulk action
        self.client.post(blocked_url, {"action": "archive", "objects": frank.id})

        response = self.client.get(blocked_url)
        self.assertEqual([joe], list(response.context["object_list"]))

        frank.refresh_from_db()
        self.assertEqual(Contact.STATUS_ARCHIVED, frank.status)

    @mock_mailroom
    def test_stopped(self, mr_mocks):
        joe = self.create_contact("Joe", urns=["twitter:joe"])
        frank = self.create_contact("Frank", urns=["twitter:frank"])
        billy = self.create_contact("Billy", urns=["twitter:billy"])
        self.create_contact("Mary", urns=["twitter:mary"])

        joe.stop(self.admin)
        frank.stop(self.admin)
        billy.stop(self.admin)

        self.login(self.user)

        stopped_url = reverse("contacts.contact_stopped")

        response = self.client.get(stopped_url)
        self.assertEqual([billy, frank, joe], list(response.context["object_list"]))
        self.assertEqual([], list(response.context["actions"]))

        self.login(self.admin)

        # admin users see bulk actions
        response = self.client.get(stopped_url)
        self.assertEqual(["restore", "archive"], list(response.context["actions"]))

        # try restore bulk action
        self.client.post(stopped_url, {"action": "restore", "objects": billy.id})

        response = self.client.get(stopped_url)
        self.assertEqual([frank, joe], list(response.context["object_list"]))

        billy.refresh_from_db()
        self.assertEqual(Contact.STATUS_ACTIVE, billy.status)

        # try archive bulk action
        self.client.post(stopped_url, {"action": "archive", "objects": frank.id})

        response = self.client.get(stopped_url)
        self.assertEqual([joe], list(response.context["object_list"]))

        frank.refresh_from_db()
        self.assertEqual(Contact.STATUS_ARCHIVED, frank.status)

    @patch("temba.contacts.models.Contact.BULK_RELEASE_IMMEDIATELY_LIMIT", 5)
    @mock_mailroom
    def test_archived(self, mr_mocks):
        joe = self.create_contact("Joe", urns=["twitter:joe"])
        frank = self.create_contact("Frank", urns=["twitter:frank"])
        billy = self.create_contact("Billy", urns=["twitter:billy"])
        self.create_contact("Mary", urns=["twitter:mary"])

        joe.archive(self.admin)
        frank.archive(self.admin)
        billy.archive(self.admin)

        self.login(self.user)

        archived_url = reverse("contacts.contact_archived")

        response = self.client.get(archived_url)
        self.assertEqual([billy, frank, joe], list(response.context["object_list"]))
        self.assertEqual([], list(response.context["actions"]))

        self.login(self.admin)

        # admin users see bulk actions
        response = self.client.get(archived_url)
        self.assertEqual(["restore", "delete"], list(response.context["actions"]))

        # try restore bulk action
        self.client.post(archived_url, {"action": "restore", "objects": billy.id})

        response = self.client.get(archived_url)
        self.assertEqual([frank, joe], list(response.context["object_list"]))

        billy.refresh_from_db()
        self.assertEqual(Contact.STATUS_ACTIVE, billy.status)

        # try delete bulk action
        self.client.post(archived_url, {"action": "delete", "objects": frank.id})

        response = self.client.get(archived_url)
        self.assertEqual([joe], list(response.context["object_list"]))

        frank.refresh_from_db()
        self.assertFalse(frank.is_active)

        # the archived view also supports deleting all
        self.client.post(archived_url, {"action": "delete", "all": "true"})

        response = self.client.get(archived_url)
        self.assertEqual([], list(response.context["object_list"]))

        # only archived contacts affected
        self.assertEqual(2, Contact.objects.filter(is_active=False, status=Contact.STATUS_ARCHIVED).count())
        self.assertEqual(2, Contact.objects.filter(is_active=False).count())

        # for larger numbers of contacts, a background task is used
        for c in range(6):
            contact = self.create_contact(f"Bob{c}", urns=[f"twitter:bob{c}"])
            contact.archive(self.user)

        response = self.client.get(archived_url)
        self.assertEqual(6, len(response.context["object_list"]))

        self.client.post(archived_url, {"action": "delete", "all": "true"})

        response = self.client.get(archived_url)
        self.assertEqual(0, len(response.context["object_list"]))

    @mock_mailroom
    def test_filter(self, mr_mocks):
        joe = self.create_contact("Joe", phone="123")
        frank = self.create_contact("Frank", phone="124")
        self.create_contact("Bob", phone="125")

        mr_mocks.contact_search("age > 40", contacts=[frank], total=1, allow_as_group=True)

        group1 = self.create_group("Testers", contacts=[joe, frank])  # static group
        group2 = self.create_group("Oldies", query="age > 40")  # smart group
        group2.contacts.add(frank)
        group3 = self.create_group("Other Org", org=self.org2)

        group1_url = reverse("contacts.contact_filter", args=[group1.uuid])
        group2_url = reverse("contacts.contact_filter", args=[group2.uuid])
        group3_url = reverse("contacts.contact_filter", args=[group3.uuid])

        response = self.assertReadFetch(group1_url, allow_viewers=True, allow_editors=True)

        self.assertEqual([frank, joe], list(response.context["object_list"]))
        self.assertEqual(["block", "label", "unlabel"], list(response.context["actions"]))

        response = self.assertReadFetch(group2_url, allow_viewers=True, allow_editors=True)

        self.assertEqual([frank], list(response.context["object_list"]))
        self.assertEqual(["block", "archive"], list(response.context["actions"]))
        self.assertContains(response, "age &gt; 40")

        # if a user tries to access a non-existent group, that's a 404
        response = self.requestView(reverse("contacts.contact_filter", args=["21343253"]), self.admin)
        self.assertEqual(404, response.status_code)

        # if a user tries to access a group in another org, send them to the login page
        response = self.requestView(group3_url, self.admin)
        self.assertLoginRedirect(response)

        # if the user has access to that org, we redirect to the org choose page
        self.org2.administrators.add(self.admin)
        response = self.requestView(group3_url, self.admin)
        self.assertRedirect(response, "/org/choose/")

    @mock_mailroom
    def test_read(self, mr_mocks):
        joe = self.create_contact("Joe", phone="123")
        other_org_contact = self.create_contact("Hans", phone="+593979123456", org=self.org2)

        read_url = reverse("contacts.contact_read", args=[joe.uuid])
        block_url = reverse("contacts.contact_block", args=[joe.id])

        response = self.client.get(read_url)
        self.assertLoginRedirect(response)

        # login as viewer
        self.login(self.user)

        response = self.client.get(read_url)
        self.assertContains(response, "Joe")

        # make sure the block link is not present
        self.assertNotContains(response, block_url)

        # login as admin
        self.login(self.admin)

        # make sure the block link is present now
        response = self.client.get(read_url)
        self.assertContains(response, block_url)

        # and that it works
        self.client.post(block_url, dict(id=joe.id))
        self.assertTrue(Contact.objects.get(pk=joe.id, status="B"))

        # try unblocking now
        response = self.client.get(read_url)
        restore_url = reverse("contacts.contact_restore", args=[joe.id])
        self.assertContains(response, restore_url)

        self.client.post(restore_url, dict(id=joe.id))
        self.assertTrue(Contact.objects.get(pk=joe.id, status="A"))

        # can't block contacts from other orgs
        response = self.client.post(reverse("contacts.contact_block", args=[other_org_contact.id]))
        self.assertLoginRedirect(response)

        # contact should be unchanged
        other_org_contact.refresh_from_db()
        self.assertEqual(Contact.STATUS_ACTIVE, other_org_contact.status)

        # or restore...
        other_org_contact.block(self.admin2)

        response = self.client.post(reverse("contacts.contact_restore", args=[other_org_contact.id]))
        self.assertLoginRedirect(response)

        # contact should be unchanged
        other_org_contact.refresh_from_db()
        self.assertEqual(Contact.STATUS_BLOCKED, other_org_contact.status)

        delete_url = reverse("contacts.contact_archive", args=[joe.id])

        response = self.client.get(read_url)

        self.assertNotContains(response, restore_url)
        self.assertContains(response, delete_url)

        # unstop option available for stopped contacts
        joe.stop(self.user)
        response = self.client.get(read_url)

        self.assertContains(response, restore_url)
        self.assertContains(response, delete_url)

        # can't access a deleted contact
        joe.release(self.admin)

        response = self.client.get(read_url)
        self.assertEqual(response.status_code, 404)

        # contact with only a urn
        nameless = self.create_contact("", urns=["twitter:bobby_anon"])
        response = self.client.get(reverse("contacts.contact_read", args=[nameless.uuid]))
        self.assertContains(response, "bobby_anon")

        # contact without name or urn
        nameless = Contact.objects.create(org=self.org)
        response = self.client.get(reverse("contacts.contact_read", args=[nameless.uuid]))
        self.assertContains(response, "Contact Details")

        # invalid uuid should return 404
        response = self.client.get(reverse("contacts.contact_read", args=["invalid-uuid"]))
        self.assertEqual(response.status_code, 404)

    @mock_mailroom
    def test_archive(self, mr_mocks):
        contact = self.create_contact("Joe", phone="+593979000111")
        other_org_contact = self.create_contact("Hans", phone="+593979123456", org=self.org2)

        archive_url = reverse("contacts.contact_archive", args=[contact.id])

        # can't archive if not logged in
        response = self.client.post(archive_url, {"id": contact.id})
        self.assertLoginRedirect(response)

        self.login(self.user)

        # can't archive if just regular user
        response = self.client.post(archive_url, {"id": contact.id})
        self.assertLoginRedirect(response)

        self.login(self.admin)

        response = self.client.post(archive_url, {"id": contact.id})
        self.assertEqual(302, response.status_code)

        contact.refresh_from_db()
        self.assertEqual(Contact.STATUS_ARCHIVED, contact.status)

        # can't archive contact in other org
        archive_url = reverse("contacts.contact_restore", args=[other_org_contact.id])
        response = self.client.post(archive_url, {"id": other_org_contact.id})
        self.assertLoginRedirect(response)

        # contact should be unchanged
        other_org_contact.refresh_from_db()
        self.assertEqual(Contact.STATUS_ACTIVE, other_org_contact.status)

    @mock_mailroom
    def test_restore(self, mr_mocks):
        contact = self.create_contact("Joe", phone="+593979000111")
        contact.stop(self.admin)
        other_org_contact = self.create_contact("Hans", phone="+593979123456", org=self.org2)
        other_org_contact.stop(self.admin2)

        restore_url = reverse("contacts.contact_restore", args=[contact.id])

        # can't restore if not logged in
        response = self.client.post(restore_url, {"id": contact.id})
        self.assertLoginRedirect(response)

        self.login(self.user)

        # can't restore if just regular user
        response = self.client.post(restore_url, {"id": contact.id})
        self.assertLoginRedirect(response)

        self.login(self.admin)

        response = self.client.post(restore_url, {"id": contact.id})
        self.assertEqual(302, response.status_code)

        contact.refresh_from_db()
        self.assertEqual(Contact.STATUS_ACTIVE, contact.status)

        # can't restore contact in other org
        restore_url = reverse("contacts.contact_restore", args=[other_org_contact.id])
        response = self.client.post(restore_url, {"id": other_org_contact.id})
        self.assertLoginRedirect(response)

        # contact should be unchanged
        other_org_contact.refresh_from_db()
        self.assertEqual(Contact.STATUS_STOPPED, other_org_contact.status)

    def test_delete(self):
        contact = self.create_contact("Joe", phone="+593979000111")
        other_org_contact = self.create_contact("Hans", phone="+593979123456", org=self.org2)

        delete_url = reverse("contacts.contact_delete", args=[contact.id])

        # can't delete if not logged in
        response = self.client.post(delete_url, {"id": contact.id})
        self.assertLoginRedirect(response)

        self.login(self.user)

        # can't delete if just regular user
        response = self.client.post(delete_url, {"id": contact.id})
        self.assertLoginRedirect(response)

        self.login(self.admin)

        response = self.client.post(delete_url, {"id": contact.id})
        self.assertEqual(302, response.status_code)

        contact.refresh_from_db()
        self.assertFalse(contact.is_active)

        # can't delete contact in other org
        delete_url = reverse("contacts.contact_delete", args=[other_org_contact.id])
        response = self.client.post(delete_url, {"id": other_org_contact.id})
        self.assertLoginRedirect(response)

        # contact should be unchanged
        other_org_contact.refresh_from_db()
        self.assertTrue(other_org_contact.is_active)

    @mock_mailroom
    def test_start(self, mr_mocks):
        sample_flows = list(self.org.flows.order_by("name"))
        background_flow = self.get_flow("background")
        self.get_flow("media_survey")
        archived_flow = self.get_flow("color")
        archived_flow.archive(self.admin)

        contact = self.create_contact("Joe", phone="+593979000111")
        start_url = reverse("contacts.contact_start", args=[contact.id])

        response = self.assertUpdateFetch(start_url, allow_viewers=False, allow_editors=True, form_fields=["flow"])
        self.assertEqual([background_flow] + sample_flows, list(response.context["form"].fields["flow"].queryset))

        # try to submit without specifying a flow
        self.assertUpdateSubmit(
            start_url, data={}, form_errors={"flow": "This field is required."}, object_unchanged=contact
        )

        # submit with flow...
        self.assertUpdateSubmit(start_url, data={"flow": background_flow.id})

        # should now have a flow start
        start = FlowStart.objects.get()
        self.assertEqual(background_flow, start.flow)
        self.assertEqual({contact}, set(start.contacts.all()))
        self.assertTrue(start.restart_participants)
        self.assertTrue(start.include_active)

        # that has been queued to mailroom
        self.assertEqual("start_flow", mr_mocks.queued_batch_tasks[-1]["type"])


class ContactGroupTest(TembaTest):
    def setUp(self):
        super().setUp()

        self.joe = self.create_contact("Joe Blow", phone="123", fields={"age": "17", "gender": "male"})
        self.frank = self.create_contact("Frank Smith", phone="1234")
        self.mary = self.create_contact("Mary Mo", phone="345", fields={"age": "21", "gender": "female"})

    def test_create_static(self):
        group = ContactGroup.create_static(self.org, self.admin, " group one ")

        self.assertEqual(group.org, self.org)
        self.assertEqual(group.name, "group one")
        self.assertEqual(group.created_by, self.admin)
        self.assertEqual(group.status, ContactGroup.STATUS_READY)

        # can't call update_query on a static group
        self.assertRaises(ValueError, group.update_query, "gender=M")

        # exception if group name is blank
        self.assertRaises(ValueError, ContactGroup.create_static, self.org, self.admin, "   ")

    @mock_mailroom
    def test_create_dynamic(self, mr_mocks):
        age = ContactField.get_or_create(self.org, self.admin, "age", value_type=ContactField.TYPE_NUMBER)
        gender = ContactField.get_or_create(self.org, self.admin, "gender", priority=10)

        # create a dynamic group using a query
        query = '(Age < 18 and gender = "male") or (Age > 18 and gender = "female")'
        mr_mocks.parse_query(query, fields=[age, gender])

        group = ContactGroup.create_dynamic(self.org, self.admin, "Group two", query)
        group.refresh_from_db()

        self.assertEqual(group.query, query)
        self.assertEqual(set(group.query_fields.all()), {age, gender})
        self.assertEqual(group.status, ContactGroup.STATUS_INITIALIZING)

        # update group query
        mr_mocks.parse_query("age > 18 and name ~ Mary", cleaned='age > 18 AND name ~ "Mary"', fields=[age])
        group.update_query("age > 18 and name ~ Mary")
        group.refresh_from_db()

        self.assertEqual(group.query, 'age > 18 AND name ~ "Mary"')
        self.assertEqual(set(group.query_fields.all()), {age})
        self.assertEqual(group.status, ContactGroup.STATUS_INITIALIZING)

        # try to update group query to something invalid
        mr_mocks.error("no valid")
        with self.assertRaises(ValueError):
            group.update_query("age ~ Mary")

        # can't create a dynamic group with empty query
        self.assertRaises(ValueError, ContactGroup.create_dynamic, self.org, self.admin, "Empty", "")

        # can't create a dynamic group with id attribute
        mr_mocks.parse_query("id = 123", allow_as_group=False)
        self.assertRaises(ValueError, ContactGroup.create_dynamic, self.org, self.admin, "Bose", "id = 123")

        # dynamic group should not have remove to group button
        self.login(self.admin)
        filter_url = reverse("contacts.contact_filter", args=[group.uuid])
        response = self.client.get(filter_url)
        self.assertEqual(list(response.context["contact_fields"].values_list("key", flat=True)), ["gender", "age"])
        # put group back into evaluation state
        group.status = ContactGroup.STATUS_EVALUATING
        group.save(update_fields=("status",))

        # dynamic groups should get their own icon
        self.assertEqual(group.get_icon(), "atom")

        # can't update query again while it is in this state
        with self.assertRaises(ValueError):
            group.update_query("age = 18")

    def test_get_or_create(self):
        group = ContactGroup.get_or_create(self.org, self.user, " first ")
        self.assertEqual(group.name, "first")
        self.assertFalse(group.is_dynamic)

        # name look up is case insensitive
        self.assertEqual(ContactGroup.get_or_create(self.org, self.user, "  FIRST"), group)

        # fetching by id shouldn't modify original group
        self.assertEqual(ContactGroup.get_or_create(self.org, self.user, "Kigali", uuid=group.uuid), group)

        group.refresh_from_db()
        self.assertEqual(group.name, "first")

    @mock_mailroom
    def test_get_user_groups(self, mr_mocks):
        self.create_field("gender", "Gender")
        static = ContactGroup.create_static(self.org, self.admin, "Static")
        deleted = ContactGroup.create_static(self.org, self.admin, "Deleted")
        deleted.is_active = False
        deleted.save()

        dynamic = ContactGroup.create_dynamic(self.org, self.admin, "Dynamic", "gender=M")
        ContactGroup.user_groups.filter(id=dynamic.id).update(status=ContactGroup.STATUS_READY)

        self.assertEqual(set(ContactGroup.get_user_groups(self.org)), {static, dynamic})
        self.assertEqual(set(ContactGroup.get_user_groups(self.org, dynamic=False)), {static})
        self.assertEqual(set(ContactGroup.get_user_groups(self.org, dynamic=True)), {dynamic})

    def test_is_valid_name(self):
        self.assertTrue(ContactGroup.is_valid_name("x"))
        self.assertTrue(ContactGroup.is_valid_name("1"))
        self.assertTrue(ContactGroup.is_valid_name("x" * 64))
        self.assertFalse(ContactGroup.is_valid_name(" "))
        self.assertFalse(ContactGroup.is_valid_name(" x"))
        self.assertFalse(ContactGroup.is_valid_name("x "))
        self.assertFalse(ContactGroup.is_valid_name("+x"))
        self.assertFalse(ContactGroup.is_valid_name("@x"))
        self.assertFalse(ContactGroup.is_valid_name("x" * 65))

    @mock_mailroom
    def test_member_count(self, mr_mocks):
        group = self.create_group("Cool kids")
        group.contacts.add(self.joe, self.frank)

        self.assertEqual(ContactGroup.user_groups.get(pk=group.pk).get_member_count(), 2)

        group.contacts.add(self.mary)

        self.assertEqual(ContactGroup.user_groups.get(pk=group.pk).get_member_count(), 3)

        group.contacts.remove(self.mary)

        self.assertEqual(ContactGroup.user_groups.get(pk=group.pk).get_member_count(), 2)

        # blocking a contact removes them from all user groups
        self.joe.block(self.user)

        group = ContactGroup.user_groups.get(pk=group.pk)
        self.assertEqual(group.get_member_count(), 1)
        self.assertEqual(set(group.contacts.all()), {self.frank})

        # releasing removes from all user groups
        self.frank.release(self.user)

        group = ContactGroup.user_groups.get(pk=group.pk)
        self.assertEqual(group.get_member_count(), 0)
        self.assertEqual(set(group.contacts.all()), set())

    @mock_mailroom
    def test_system_group_counts(self, mr_mocks):
        # start with no contacts
        for contact in Contact.objects.all():
            contact.release(self.admin)
            contact.delete()

        counts = ContactGroup.get_system_group_counts(self.org)
        self.assertEqual(
            counts,
            {
                ContactGroup.TYPE_ACTIVE: 0,
                ContactGroup.TYPE_BLOCKED: 0,
                ContactGroup.TYPE_STOPPED: 0,
                ContactGroup.TYPE_ARCHIVED: 0,
            },
        )

        self.create_contact("Hannibal", phone="0783835001")
        face = self.create_contact("Face", phone="0783835002")
        ba = self.create_contact("B.A.", phone="0783835003")
        murdock = self.create_contact("Murdock", phone="0783835004")

        counts = ContactGroup.get_system_group_counts(self.org)
        self.assertEqual(
            counts,
            {
                ContactGroup.TYPE_ACTIVE: 4,
                ContactGroup.TYPE_BLOCKED: 0,
                ContactGroup.TYPE_STOPPED: 0,
                ContactGroup.TYPE_ARCHIVED: 0,
            },
        )

        # call methods twice to check counts don't change twice
        murdock.block(self.user)
        murdock.block(self.user)
        face.block(self.user)
        ba.stop(self.user)
        ba.stop(self.user)

        counts = ContactGroup.get_system_group_counts(self.org)
        self.assertEqual(
            counts,
            {
                ContactGroup.TYPE_ACTIVE: 1,
                ContactGroup.TYPE_BLOCKED: 2,
                ContactGroup.TYPE_STOPPED: 1,
                ContactGroup.TYPE_ARCHIVED: 0,
            },
        )

        murdock.release(self.user)
        murdock.release(self.user)
        face.restore(self.user)
        face.restore(self.user)
        ba.restore(self.user)
        ba.restore(self.user)

        # squash all our counts, this shouldn't affect our overall counts, but we should now only have 3
        squash_contactgroupcounts()
        self.assertEqual(ContactGroupCount.objects.all().count(), 3)

        counts = ContactGroup.get_system_group_counts(self.org)
        self.assertEqual(
            counts,
            {
                ContactGroup.TYPE_ACTIVE: 3,
                ContactGroup.TYPE_BLOCKED: 0,
                ContactGroup.TYPE_STOPPED: 0,
                ContactGroup.TYPE_ARCHIVED: 0,
            },
        )

        # rebuild just our system contact group
        all_contacts = ContactGroup.all_groups.get(org=self.org, group_type=ContactGroup.TYPE_ACTIVE)
        ContactGroupCount.populate_for_group(all_contacts)

        # assert our count is correct
        self.assertEqual(all_contacts.get_member_count(), 3)
        self.assertEqual(ContactGroupCount.objects.filter(group=all_contacts).count(), 1)

    @mock_mailroom
    def test_release(self, mr_mocks):
        group1 = self.create_group("Group One")
        group2 = self.create_group("Group One")
        flow = self.create_flow()

        # create a campaign based on group 1
        campaign = Campaign.create(self.org, self.admin, "Reminders", group1)
        joined = ContactField.get_or_create(
            self.org, self.admin, "joined", "Joined On", value_type=ContactField.TYPE_DATETIME
        )
        event = CampaignEvent.create_message_event(self.org, self.admin, campaign, joined, 2, unit="D", message="Hi")
        EventFire.objects.create(event=event, contact=self.joe, scheduled=timezone.now() + timedelta(days=2))
        campaign.is_archived = True
        campaign.save()

        # create scheduled and regular broadcasts which send to both groups
        schedule = Schedule.create_schedule(self.org, self.admin, timezone.now(), Schedule.REPEAT_DAILY)
        bcast1 = self.create_broadcast(self.admin, "Hi", groups=[group1, group2], schedule=schedule)
        bcast2 = self.create_broadcast(self.admin, "Hi", groups=[group1, group2])
        bcast2.send_async()

        group1.release(self.admin)
        group1.refresh_from_db()

        self.assertFalse(group1.is_active)
        self.assertEqual(0, EventFire.objects.count())  # event fires will have been deleted
        self.assertEqual({group2}, set(bcast1.groups.all()))  # removed from scheduled broadcast
        self.assertEqual({group1, group2}, set(bcast2.groups.all()))  # regular broadcast unchanged

        self.login(self.admin)

        group = self.create_group("Group One")
        delete_url = reverse("contacts.contactgroup_delete", args=[group.id])

        trigger = Trigger.objects.create(
            org=self.org, flow=flow, keyword="join", created_by=self.admin, modified_by=self.admin
        )
        trigger.groups.add(group)

        second_trigger = Trigger.objects.create(
            org=self.org, flow=flow, keyword="register", created_by=self.admin, modified_by=self.admin
        )
        second_trigger.groups.add(group)

        response = self.client.get(delete_url, dict(), HTTP_X_PJAX=True)
        self.assertContains(response, 'This group is used by <a href="/trigger/">2 triggers<a>')

        response = self.client.post(delete_url, dict())
        self.assertEqual(302, response.status_code)
        response = self.client.post(delete_url, dict(), follow=True)
        self.assertTrue(ContactGroup.user_groups.get(pk=group.pk).is_active)
        self.assertEqual(response.request["PATH_INFO"], reverse("contacts.contact_filter", args=[group.uuid]))

        # archive a trigger
        second_trigger.is_archived = True
        second_trigger.save()

        response = self.client.post(delete_url, dict())
        self.assertEqual(302, response.status_code)
        response = self.client.post(delete_url, dict(), follow=True)
        self.assertTrue(ContactGroup.user_groups.get(pk=group.pk).is_active)
        self.assertEqual(response.request["PATH_INFO"], reverse("contacts.contact_filter", args=[group.uuid]))

        trigger.is_archived = True
        trigger.save()

        self.client.post(delete_url, {})

        # group should have is_active = False and all its triggers
        self.assertIsNone(ContactGroup.user_groups.filter(pk=group.pk).first())
        self.assertFalse(ContactGroup.all_groups.get(pk=group.pk).is_active)
        self.assertFalse(Trigger.objects.get(pk=trigger.pk).is_active)
        self.assertFalse(Trigger.objects.get(pk=second_trigger.pk).is_active)

    def test_group_release_deactivates_campaign(self):
        a_group = self.create_group("one")
        delete_url = reverse("contacts.contactgroup_delete", args=[a_group.pk])

        self.get_flow("favorites")

        self.login(self.admin)

        post_data = dict(name="YAC - Yet another campaign", group=a_group.pk)
        self.client.post(reverse("campaigns.campaign_create"), post_data)

        a_campaign = Campaign.objects.first()

        response = self.client.get(delete_url, dict(), HTTP_X_PJAX=True)
        self.assertContains(response, "There is an active campaign using this group.")

        # archive the campaign
        self.client.post(reverse("campaigns.campaign_archive", args=(a_campaign.pk,)))

        response = self.client.get(delete_url, dict(), HTTP_X_PJAX=True)
        self.assertContains(response, "Are you sure?")

        response = self.client.post(delete_url, dict(), HTTP_X_PJAX=True)
        self.assertContains(response, "document.location.href = '/contact/';")

        # group and campaign are no longer active
        self.assertFalse(ContactGroup.all_groups.get(pk=a_group.pk).is_active)
        self.assertFalse(Campaign.objects.get(pk=a_campaign.pk).is_active)

    def test_delete_fail_with_dependencies(self):
        self.login(self.admin)

        self.get_flow("dependencies")

        from temba.flows.models import Flow

        flow = Flow.objects.filter(name="Dependencies").first()
        cats = ContactGroup.user_groups.filter(name="Cat Facts").first()
        delete_url = reverse("contacts.contactgroup_delete", args=[cats.pk])

        # can't delete if it is a dependency
        response = self.client.post(delete_url, dict())
        self.assertEqual(302, response.status_code)
        self.assertTrue(ContactGroup.user_groups.get(id=cats.id).is_active)

        # get the dependency details
        response = self.client.get(delete_url, dict(), HTTP_X_PJAX=True)
        self.assertEqual(200, response.status_code)
        self.assertContains(response, "Dependencies")

        # remove it from our list of dependencies
        flow.group_dependencies.remove(cats)

        # now it should be gone
        response = self.client.get(delete_url, dict(), HTTP_X_PJAX=True)
        self.assertNotContains(response, "Dependencies")

        response = self.client.post(delete_url, dict(), HTTP_X_PJAX=True)
        self.assertIsNone(ContactGroup.user_groups.filter(id=cats.id).first())

    def test_delete_with_campaign_dependencies(self):
        block_group = self.create_group("one that blocks")

        self.login(self.admin)

        post_data = dict(name="Don't forget to ...", group=block_group.pk)
        self.client.post(reverse("campaigns.campaign_create"), post_data)

        delete_url = reverse("contacts.contactgroup_delete", args=[block_group.pk])

        # users are notified that a group cannot be deleted
        response = self.client.get(delete_url, dict(), HTTP_X_PJAX=True)
        self.assertContains(response, "There is an active campaign using this group")

        # can't delete if it is a dependency
        response = self.client.post(delete_url, dict())
        self.assertRedirect(response, f"/contact/filter/{block_group.uuid}/")
        self.assertTrue(ContactGroup.user_groups.get(id=block_group.id).is_active)


class ElasticSearchLagTest(TembaTest):
    def test_lag(self):
        mock_es_data = [
            {
                "_type": "_doc",
                "_index": "dummy_index",
                "_source": {"id": 10, "modified_on": timezone.now().isoformat()},
            }
        ]
        with ESMockWithScroll(data=mock_es_data):
            self.assertFalse(check_elasticsearch_lag())

        frank = self.create_contact("Frank Smith", urns=["tel:1234", "twitter:hola"])

        mock_es_data = [
            {
                "_type": "_doc",
                "_index": "dummy_index",
                "_source": {"id": frank.id, "modified_on": frank.modified_on.isoformat()},
            }
        ]
        with ESMockWithScroll(data=mock_es_data):
            self.assertTrue(check_elasticsearch_lag())

        mock_es_data = [
            {
                "_type": "_doc",
                "_index": "dummy_index",
                "_source": {"id": frank.id, "modified_on": (frank.modified_on - timedelta(minutes=10)).isoformat()},
            }
        ]
        with ESMockWithScroll(data=mock_es_data):
            self.assertFalse(check_elasticsearch_lag())

        Contact.objects.filter(id=frank.id).update(modified_on=timezone.now() - timedelta(minutes=6))
        with ESMockWithScroll():
            self.assertFalse(check_elasticsearch_lag())


class ContactGroupCRUDLTest(TembaTest, CRUDLTestMixin):
    def setUp(self):
        super().setUp()

        self.joe = self.create_contact("Joe Blow", phone="123")
        self.frank = self.create_contact("Frank Smith", urns=["tel:1234", "twitter:hola"])

        self.joe_and_frank = self.create_group("Customers", [self.joe, self.frank])

        self.other_org_group = self.create_group("Customers", contacts=[], org=self.org2)

    @mock_mailroom
    def test_list(self, mr_mocks):

        list_url = reverse("contacts.contactgroup_list")
        response = self.assertListFetch(list_url, allow_viewers=True, allow_editors=True, allow_agents=False)
        self.assertEqual(
            [
                "delete",
            ],
            list(response.context["actions"]),
        )

        group = ContactGroup.create_static(self.org, self.admin, "My New Group")

        # let's delete it and make sure it's gone
        self.client.post(list_url, {"action": "delete", "objects": group.id})
        self.assertFalse(ContactGroup.user_groups.filter(id=group.id).exists())

        query = "name ~ Joe"
        mr_mocks.parse_query(query, fields=[])

        smart_group = ContactGroup.create_dynamic(self.org, self.admin, "Smart Group", "name ~ Joe")

        # fetch only smart groups
        list_url = f"{reverse('contacts.contactgroup_list')}?type=smart"
        response = self.assertListFetch(list_url, allow_viewers=True, allow_editors=True, allow_agents=False)

        self.assertEqual(1, len(response.context["object_list"]))
        self.assertContains(response, smart_group.name)

        # fetch with spa flag
        response = self.client.get(list_url, content_type="application/json", HTTP_TEMBA_SPA="1")
        self.assertEqual(response.context["base_template"], "spa.html")

    @override_settings(ORG_LIMIT_DEFAULTS={"groups": 10})
    @mock_mailroom
    def test_create(self, mr_mocks):
        url = reverse("contacts.contactgroup_create")

        # can't create group as viewer
        self.login(self.user)
        response = self.client.post(url, dict(name="Spammers"))
        self.assertLoginRedirect(response)

        self.login(self.admin)

        # try to create a contact group whose name is only whitespace
        response = self.client.post(url, dict(name="  "))
        self.assertFormError(response, "form", "name", "This field is required.")

        # try to create a contact group whose name begins with reserved character
        response = self.client.post(url, dict(name="+People"))
        self.assertFormError(response, "form", "name", "Group name must not be blank or begin with + or -")

        # try to create with name that's already taken
        response = self.client.post(url, dict(name="Customers"))
        self.assertFormError(response, "form", "name", "Name is used by another group")

        # create with valid name (that will be trimmed)
        response = self.client.post(url, dict(name="first  "))
        self.assertNoFormErrors(response)
        ContactGroup.user_groups.get(org=self.org, name="first")

        # create a group with preselected contacts
        self.client.post(url, dict(name="Everybody", preselected_contacts="%d,%d" % (self.joe.pk, self.frank.pk)))
        group = ContactGroup.user_groups.get(org=self.org, name="Everybody")
        self.assertEqual(set(group.contacts.all()), {self.joe, self.frank})

        # create a dynamic group using a query
        self.client.post(url, dict(name="Frank", group_query="tel = 1234"))

        ContactGroup.user_groups.get(org=self.org, name="Frank", query="tel = 1234")

        for group in ContactGroup.user_groups.all():
            group.release(self.admin)

        for i in range(10):
            ContactGroup.create_static(self.org2, self.admin2, "group%d" % i)

        response = self.client.post(url, dict(name="People"))
        self.assertNoFormErrors(response)
        ContactGroup.user_groups.get(org=self.org, name="People")

        for group in ContactGroup.user_groups.all():
            group.release(self.admin)

        for i in range(10):
            ContactGroup.create_static(self.org, self.admin, "group%d" % i)

        self.assertEqual(10, ContactGroup.user_groups.all().count())
        response = self.client.post(url, dict(name="People"))
        self.assertFormError(
            response,
            "form",
            "name",
            "This org has 10 groups and the limit is 10. "
            "You must delete existing ones before you can create new ones.",
        )

    def test_create_disallow_duplicates(self):
        self.login(self.admin)

        self.client.post(reverse("contacts.contactgroup_create"), dict(name="First Group"))

        # assert it was created
        ContactGroup.user_groups.get(name="First Group")

        # try to create another group with the same name, but a dynamic query, should fail
        response = self.client.post(
            reverse("contacts.contactgroup_create"), dict(name="First Group", group_query="firsts")
        )
        self.assertFormError(response, "form", "name", "Name is used by another group")

        # try to create another group with same name, not dynamic, same thing
        response = self.client.post(
            reverse("contacts.contactgroup_create"), dict(name="First Group", group_query="firsts")
        )
        self.assertFormError(response, "form", "name", "Name is used by another group")

    @mock_mailroom
    def test_update(self, mr_mocks):
        url = reverse("contacts.contactgroup_update", args=[self.joe_and_frank.id])

        dynamic_group = self.create_group("Dynamic", query="tel is 1234")

        # can't create group as viewer
        self.login(self.user)
        response = self.client.post(url, dict(name="Spammers"))
        self.assertLoginRedirect(response)

        self.login(self.admin)

        # try to update name to only whitespace
        response = self.client.post(url, dict(name="   "))
        self.assertFormError(response, "form", "name", "This field is required.")

        # try to update name to start with reserved character
        response = self.client.post(url, dict(name="+People"))
        self.assertFormError(response, "form", "name", "Group name must not be blank or begin with + or -")

        # update with valid name (that will be trimmed)
        response = self.client.post(url, dict(name="new name   "))
        self.assertNoFormErrors(response)

        self.joe_and_frank.refresh_from_db()
        self.assertEqual(self.joe_and_frank.name, "new name")

        # now try a dynamic group
        url = reverse("contacts.contactgroup_update", args=[dynamic_group.id])

        # mark our group as ready
        ContactGroup.user_groups.filter(id=dynamic_group.id).update(status=ContactGroup.STATUS_READY)

        # update both name and query, form should fail, because query is not parsable
        mr_mocks.error("error at !", code="unexpected_token", extra={"token": "!"})
        response = self.client.post(url, dict(name="Frank", query="(!))!)"))
        self.assertFormError(response, "form", "query", "Invalid query syntax at '!'")

        # try to update a group with an invalid query
        mr_mocks.error("error at >", code="unexpected_token", extra={"token": ">"})
        response = self.client.post(url, dict(name="Frank", query="name <> some_name"))
        self.assertFormError(response, "form", "query", "Invalid query syntax at '>'")

        # dependent on id
        response = self.client.post(url, dict(name="Frank", query="id = 123"))
        self.assertFormError(response, "form", "query", 'You cannot create a smart group based on "id" or "group".')

        response = self.client.post(url, dict(name="Frank", query='twitter = "hola"'))

        self.assertNoFormErrors(response)

        dynamic_group.refresh_from_db()
        self.assertEqual(dynamic_group.query, 'twitter = "hola"')

        # mark our dynamic group as evaluating
        dynamic_group.status = ContactGroup.STATUS_EVALUATING
        dynamic_group.save(update_fields=("status",))

        # and check we can't change the query while that is the case
        response = self.client.post(url, dict(name="Frank", query='twitter = "hello"'))
        self.assertFormError(response, "form", "query", "You cannot update the query of a group that is evaluating.")

        # but can change the name
        response = self.client.post(url, dict(name="Frank2", query='twitter = "hola"'))
        self.assertNoFormErrors(response)

        dynamic_group.refresh_from_db()
        self.assertEqual(dynamic_group.name, "Frank2")

        # try to update group in other org
        response = self.client.post(
            reverse("contacts.contactgroup_update", args=[self.other_org_group.id]), {"name": "new name"}
        )
        self.assertLoginRedirect(response)

        # check group is unchanged
        self.other_org_group.refresh_from_db()
        self.assertEqual("Customers", self.other_org_group.name)

    def test_usages(self):
        flow = self.get_flow("dependencies", name="Dependencies")
        group = ContactGroup.user_groups.get(name="Cat Facts")

        campaign1 = Campaign.create(self.org, self.admin, "Planting Reminders", group)
        campaign2 = Campaign.create(self.org, self.admin, "Deleted", group)
        campaign2.is_active = False
        campaign2.save(update_fields=("is_active",))

        usages_url = reverse("contacts.contactgroup_usages", args=[group.uuid])

        response = self.assertReadFetch(usages_url, allow_viewers=True, allow_editors=True, context_object=group)

        self.assertEqual(
            {"flow": [flow], "campaign": [campaign1]},
            {t: list(qs) for t, qs in response.context["dependents"].items()},
        )

    def test_delete(self):
        url = reverse("contacts.contactgroup_delete", args=[self.joe_and_frank.pk])

        # can't delete group as viewer
        self.login(self.user)
        response = self.client.post(url)
        self.assertLoginRedirect(response)

        # can as admin user
        self.login(self.admin)
        response = self.client.post(url, HTTP_X_PJAX=True)
        self.assertEqual(200, response.status_code)
        self.assertContains(response, "/contact/")

        self.joe_and_frank.refresh_from_db()
        self.assertFalse(self.joe_and_frank.is_active)
        self.assertFalse(self.joe_and_frank.contacts.all())

        # can't delete group from other org
        response = self.client.post(reverse("contacts.contactgroup_delete", args=[self.other_org_group.id]))
        self.assertLoginRedirect(response)

        # check group is unchanged
        self.other_org_group.refresh_from_db()
        self.assertTrue(self.other_org_group.is_active)


class ContactTest(TembaTest):
    def setUp(self):
        super().setUp()

        self.user1 = self.create_user("nash")

        self.joe = self.create_contact(name="Joe Blow", urns=["twitter:blow80", "tel:+250781111111"])
        self.frank = self.create_contact(name="Frank Smith", phone="+250782222222")
        self.billy = self.create_contact(name="Billy Nophone")
        self.voldemort = self.create_contact(phone="+250768383383")

        # create an orphaned URN
        ContactURN.objects.create(
            org=self.org, scheme="tel", path="+250788888888", identity="tel:+250788888888", priority=50
        )

        # create an deleted contact
        self.jim = self.create_contact(name="Jim")
        self.jim.release(self.user)

        # create contact in other org
        self.other_org_contact = self.create_contact(name="Fred", phone="+250768111222", org=self.org2)

        self.mock_s3 = MockS3Client()

    def create_campaign(self):
        # create a campaign with a future event and add joe
        self.farmers = self.create_group("Farmers", [self.joe])
        self.reminder_flow = self.get_flow("color")
        self.planting_date = ContactField.get_or_create(
            self.org, self.admin, "planting_date", "Planting Date", value_type=ContactField.TYPE_DATETIME
        )
        self.campaign = Campaign.create(self.org, self.admin, "Planting Reminders", self.farmers)

        # create af flow event
        self.planting_reminder = CampaignEvent.create_flow_event(
            self.org,
            self.admin,
            self.campaign,
            relative_to=self.planting_date,
            offset=0,
            unit="D",
            flow=self.reminder_flow,
            delivery_hour=17,
        )

        # and a message event
        self.message_event = CampaignEvent.create_message_event(
            self.org,
            self.admin,
            self.campaign,
            relative_to=self.planting_date,
            offset=7,
            unit="D",
            message="Sent 7 days after planting date",
        )

    @mock_mailroom
    def test_contact_create(self, mr_mocks):
        self.login(self.admin)

        # try creating a contact with a number that belongs to another contact
        response = self.client.post(
            reverse("contacts.contact_create"), data=dict(name="Ben Haggerty", urn__tel__0="+250781111111")
        )
        self.assertFormError(response, "form", "urn__tel__0", "Used by another contact")

        # now repost with a unique phone number
        response = self.client.post(
            reverse("contacts.contact_create"), data=dict(name="Ben Haggerty", urn__tel__0="+250 783-835665")
        )
        self.assertNoFormErrors(response)

        # repost with the phone number of an orphaned URN
        response = self.client.post(
            reverse("contacts.contact_create"), data=dict(name="Ben Haggerty", urn__tel__0="+250788888888")
        )
        self.assertNoFormErrors(response)

        # check that the orphaned URN has been associated with the contact
        self.assertEqual("Ben Haggerty", Contact.from_urn(self.org, "tel:+250788888888").name)

        # check we display error for invalid input
        response = self.client.post(
            reverse("contacts.contact_create"), data=dict(name="Ben Haggerty", urn__tel__0="=")
        )
        self.assertFormError(response, "form", "urn__tel__0", "Invalid input")

    @patch("temba.mailroom.client.MailroomClient.contact_modify")
    def test_block_and_stop(self, mock_contact_modify):
        mock_contact_modify.return_value = {self.joe.id: {"contact": {}, "events": []}}

        self.joe.block(self.admin)

        mock_contact_modify.assert_called_once_with(
            self.org.id, self.admin.id, [self.joe.id], [modifiers.Status(status="blocked")]
        )
        mock_contact_modify.reset_mock()

        self.joe.stop(self.admin)

        mock_contact_modify.assert_called_once_with(
            self.org.id, self.admin.id, [self.joe.id], [modifiers.Status(status="stopped")]
        )
        mock_contact_modify.reset_mock()

        self.joe.restore(self.admin)

        mock_contact_modify.assert_called_once_with(
            self.org.id, self.admin.id, [self.joe.id], [modifiers.Status(status="active")]
        )
        mock_contact_modify.reset_mock()

    @mock_mailroom
    def test_release(self, mr_mocks):
        # create a contact with a message
        old_contact = self.create_contact("Jose", phone="+12065552000")
        self.create_incoming_msg(old_contact, "hola mundo")
        urn = old_contact.get_urn()

        self.create_ticket(self.org.ticketers.get(), old_contact, "Hi")

        ivr_flow = self.get_flow("ivr")
        msg_flow = self.get_flow("favorites_v13")

        self.create_incoming_call(msg_flow, old_contact)

        # steal his urn into a new contact
        contact = self.create_contact("Joe", urns=["twitter:tweettweet"], fields={"gender": "Male", "age": 40})
        urn.contact = contact
        urn.save(update_fields=("contact",))
        group = self.create_group("Test Group", contacts=[contact])

        contact2 = self.create_contact("Billy", urns=["twitter:billy"])

        # create scheduled and regular broadcasts which send to both contacts
        schedule = Schedule.create_schedule(self.org, self.admin, timezone.now(), Schedule.REPEAT_DAILY)
        bcast1 = self.create_broadcast(self.admin, "Test", contacts=[contact, contact2], schedule=schedule)
        bcast2 = self.create_broadcast(self.admin, "Test", contacts=[contact, contact2])

        flow_nodes = msg_flow.get_definition()["nodes"]
        color_prompt = flow_nodes[0]
        color_split = flow_nodes[2]
        beer_prompt = flow_nodes[3]
        beer_split = flow_nodes[5]
        name_prompt = flow_nodes[6]
        name_split = flow_nodes[7]

        (
            MockSessionWriter(contact, msg_flow)
            .visit(color_prompt)
            .send_msg("What is your favorite color?", self.channel)
            .visit(color_split)
            .wait()
            .resume(msg=self.create_incoming_msg(contact, "red"))
            .visit(beer_prompt)
            .send_msg("Good choice, I like Red too! What is your favorite beer?", self.channel)
            .visit(beer_split)
            .wait()
            .resume(msg=self.create_incoming_msg(contact, "primus"))
            .visit(name_prompt)
            .send_msg("Lastly, what is your name?", self.channel)
            .visit(name_split)
            .wait()
            .save()
        )

        campaign = Campaign.create(self.org, self.admin, "Reminders", group)
        joined = ContactField.get_or_create(
            self.org, self.admin, "joined", "Joined On", value_type=ContactField.TYPE_DATETIME
        )
        event = CampaignEvent.create_message_event(self.org, self.admin, campaign, joined, 2, unit="D", message="Hi")
        EventFire.objects.create(event=event, contact=contact, scheduled=timezone.now() + timedelta(days=2))

        self.create_incoming_call(msg_flow, contact)

        # give contact an open and a closed ticket
        self.create_ticket(self.org.ticketers.get(), contact, "Hi")
        self.create_ticket(self.org.ticketers.get(), contact, "Hi", closed_on=timezone.now())

        self.assertEqual(1, group.contacts.all().count())
        self.assertEqual(1, contact.connections.all().count())
        self.assertEqual(2, contact.addressed_broadcasts.all().count())
        self.assertEqual(2, contact.urns.all().count())
        self.assertEqual(2, contact.runs.all().count())
        self.assertEqual(7, contact.msgs.all().count())
        self.assertEqual(2, len(contact.fields))
        self.assertEqual(1, contact.campaign_fires.count())

        self.assertEqual(2, TicketCount.get_all(self.org, Ticket.STATUS_OPEN))
        self.assertEqual(1, TicketCount.get_all(self.org, Ticket.STATUS_CLOSED))

        # first try a regular release and make sure our urns are anonymized
        contact.release(self.admin, full=False)
        self.assertEqual(2, contact.urns.all().count())
        for urn in contact.urns.all():
            uuid.UUID(urn.path, version=4)
            self.assertEqual(URN.DELETED_SCHEME, urn.scheme)

        # tickets unchanged
        self.assertEqual(2, contact.tickets.count())

        # a new contact arrives with those urns
        new_contact = self.create_contact("URN Thief", urns=["tel:+12065552000", "twitter:tweettweet"])
        self.assertEqual(2, new_contact.urns.all().count())

        self.assertEqual({contact2}, set(bcast1.contacts.all()))
        self.assertEqual({contact, contact2}, set(bcast2.contacts.all()))

        # now lets go for a full release
        contact.release(self.admin)

        contact.refresh_from_db()
        self.assertEqual(0, group.contacts.all().count())
        self.assertEqual(0, contact.connections.all().count())
        self.assertEqual(0, contact.addressed_broadcasts.all().count())
        self.assertEqual(0, contact.urns.all().count())
        self.assertEqual(0, contact.runs.all().count())
        self.assertEqual(0, contact.msgs.all().count())
        self.assertEqual(0, contact.campaign_fires.count())

        # tickets deleted (only for this contact)
        self.assertEqual(0, contact.tickets.count())
        self.assertEqual(1, TicketCount.get_all(self.org, Ticket.STATUS_OPEN))
        self.assertEqual(0, TicketCount.get_all(self.org, Ticket.STATUS_CLOSED))

        # contact who used to own our urn had theirs released too
        self.assertEqual(0, old_contact.connections.all().count())
        self.assertEqual(0, old_contact.msgs.all().count())

        self.assertIsNone(contact.fields)
        self.assertIsNone(contact.name)

        # nope, we aren't paranoid or anything
        Org.objects.get(id=self.org.id)
        Flow.objects.get(id=msg_flow.id)
        Flow.objects.get(id=ivr_flow.id)
        self.assertEqual(1, Ticket.objects.count())

    @mock_mailroom
    def test_status_changes_and_release(self, mr_mocks):
        msg1 = self.create_incoming_msg(self.joe, "Test 1", msg_type="I")
        msg2 = self.create_incoming_msg(self.joe, "Test 2", msg_type="F")
        msg3 = self.create_incoming_msg(self.joe, "Test 3", msg_type="I", visibility="A")
        label = Label.get_or_create(self.org, self.user, "Interesting")
        label.toggle_label([msg1, msg2, msg3], add=True)
        static_group = self.create_group("Just Joe", [self.joe])

        self.clear_cache()

        msg_counts = SystemLabel.get_counts(self.org)
        self.assertEqual(1, msg_counts[SystemLabel.TYPE_INBOX])
        self.assertEqual(1, msg_counts[SystemLabel.TYPE_FLOWS])
        self.assertEqual(1, msg_counts[SystemLabel.TYPE_ARCHIVED])

        self.assertEqual(
            ContactGroup.get_system_group_counts(self.org),
            {
                ContactGroup.TYPE_ACTIVE: 4,
                ContactGroup.TYPE_BLOCKED: 0,
                ContactGroup.TYPE_STOPPED: 0,
                ContactGroup.TYPE_ARCHIVED: 0,
            },
        )

        self.assertEqual(set(label.msgs.all()), {msg1, msg2, msg3})
        self.assertEqual(set(static_group.contacts.all()), {self.joe})

        self.joe.stop(self.user)

        # check that joe is now stopped
        self.joe = Contact.objects.get(pk=self.joe.pk)
        self.assertEqual(Contact.STATUS_STOPPED, self.joe.status)
        self.assertTrue(self.joe.is_active)

        # and added to stopped group
        self.assertEqual(
            ContactGroup.get_system_group_counts(self.org),
            {
                ContactGroup.TYPE_ACTIVE: 3,
                ContactGroup.TYPE_BLOCKED: 0,
                ContactGroup.TYPE_STOPPED: 1,
                ContactGroup.TYPE_ARCHIVED: 0,
            },
        )
        self.assertEqual(set(static_group.contacts.all()), set())

        self.joe.block(self.user)

        # check that joe is now blocked instead of stopped
        self.joe.refresh_from_db()
        self.assertEqual(Contact.STATUS_BLOCKED, self.joe.status)
        self.assertTrue(self.joe.is_active)

        # and that he's been removed from the all and failed groups, and added to the blocked group
        self.assertEqual(
            ContactGroup.get_system_group_counts(self.org),
            {
                ContactGroup.TYPE_ACTIVE: 3,
                ContactGroup.TYPE_BLOCKED: 1,
                ContactGroup.TYPE_STOPPED: 0,
                ContactGroup.TYPE_ARCHIVED: 0,
            },
        )

        # and removed from all groups
        self.assertEqual(set(static_group.contacts.all()), set())

        # but his messages are unchanged
        self.assertEqual(2, Msg.objects.filter(contact=self.joe, visibility="V").count())
        msg_counts = SystemLabel.get_counts(self.org)
        self.assertEqual(1, msg_counts[SystemLabel.TYPE_INBOX])
        self.assertEqual(1, msg_counts[SystemLabel.TYPE_FLOWS])
        self.assertEqual(1, msg_counts[SystemLabel.TYPE_ARCHIVED])

        self.joe.archive(self.admin)

        # check that joe is now archived
        self.joe.refresh_from_db()
        self.assertEqual(Contact.STATUS_ARCHIVED, self.joe.status)
        self.assertTrue(self.joe.is_active)

        self.assertEqual(
            ContactGroup.get_system_group_counts(self.org),
            {
                ContactGroup.TYPE_ACTIVE: 3,
                ContactGroup.TYPE_BLOCKED: 0,
                ContactGroup.TYPE_STOPPED: 0,
                ContactGroup.TYPE_ARCHIVED: 1,
            },
        )

        self.joe.restore(self.admin)

        # check that joe is now neither blocked or stopped
        self.joe = Contact.objects.get(pk=self.joe.pk)
        self.assertEqual(Contact.STATUS_ACTIVE, self.joe.status)
        self.assertTrue(self.joe.is_active)

        # and that he's been removed from the blocked group, and put back in the all and failed groups
        self.assertEqual(
            ContactGroup.get_system_group_counts(self.org),
            {
                ContactGroup.TYPE_ACTIVE: 4,
                ContactGroup.TYPE_BLOCKED: 0,
                ContactGroup.TYPE_STOPPED: 0,
                ContactGroup.TYPE_ARCHIVED: 0,
            },
        )

        self.joe.release(self.user)

        # check that joe has been released (doesn't change his status)
        self.joe = Contact.objects.get(pk=self.joe.pk)
        self.assertEqual(Contact.STATUS_ACTIVE, self.joe.status)
        self.assertFalse(self.joe.is_active)

        self.assertEqual(
            ContactGroup.get_system_group_counts(self.org),
            {
                ContactGroup.TYPE_ACTIVE: 3,
                ContactGroup.TYPE_BLOCKED: 0,
                ContactGroup.TYPE_STOPPED: 0,
                ContactGroup.TYPE_ARCHIVED: 0,
            },
        )

        # joe's messages should be inactive, blank and have no labels
        self.assertEqual(0, Msg.objects.filter(contact=self.joe, visibility="V").count())
        self.assertEqual(0, Msg.objects.filter(contact=self.joe).exclude(text="").count())
        self.assertEqual(0, Label.label_objects.get(pk=label.pk).msgs.count())

        msg_counts = SystemLabel.get_counts(self.org)
        self.assertEqual(0, msg_counts[SystemLabel.TYPE_INBOX])
        self.assertEqual(0, msg_counts[SystemLabel.TYPE_FLOWS])
        self.assertEqual(0, msg_counts[SystemLabel.TYPE_ARCHIVED])

        # and he shouldn't be in any groups
        self.assertEqual(set(static_group.contacts.all()), set())

        # or have any URNs
        self.assertEqual(0, ContactURN.objects.filter(contact=self.joe).count())

        # blocking and failing an inactive contact won't change groups
        self.joe.block(self.user)
        self.joe.stop(self.user)

        self.assertEqual(
            ContactGroup.get_system_group_counts(self.org),
            {
                ContactGroup.TYPE_ACTIVE: 3,
                ContactGroup.TYPE_BLOCKED: 0,
                ContactGroup.TYPE_STOPPED: 0,
                ContactGroup.TYPE_ARCHIVED: 0,
            },
        )

        # we don't let users undo releasing a contact... but if we have to do it for some reason
        self.joe.is_active = True
        self.joe.save(update_fields=("is_active",))

        # check joe goes into the appropriate groups
        self.assertEqual(
            ContactGroup.get_system_group_counts(self.org),
            {
                ContactGroup.TYPE_ACTIVE: 3,
                ContactGroup.TYPE_BLOCKED: 0,
                ContactGroup.TYPE_STOPPED: 1,
                ContactGroup.TYPE_ARCHIVED: 0,
            },
        )

    def test_contact_display(self):
        self.assertEqual("Joe Blow", self.joe.get_display(org=self.org, formatted=False))
        self.assertEqual("Joe Blow", self.joe.get_display())
        self.assertEqual("+250768383383", self.voldemort.get_display(org=self.org, formatted=False))
        self.assertEqual("0768 383 383", self.voldemort.get_display())
        self.assertEqual("Billy Nophone", self.billy.get_display())

        self.assertEqual("0781 111 111", self.joe.get_urn_display(scheme=URN.TEL_SCHEME))
        self.assertEqual("blow80", self.joe.get_urn_display(org=self.org, formatted=False))
        self.assertEqual("blow80", self.joe.get_urn_display())
        self.assertEqual("+250768383383", self.voldemort.get_urn_display(org=self.org, formatted=False))
        self.assertEqual(
            "+250768383383", self.voldemort.get_urn_display(org=self.org, formatted=False, international=True)
        )
        self.assertEqual("+250 768 383 383", self.voldemort.get_urn_display(org=self.org, international=True))
        self.assertEqual("0768 383 383", self.voldemort.get_urn_display())
        self.assertEqual("", self.billy.get_urn_display())

        self.assertEqual("Joe Blow", str(self.joe))
        self.assertEqual("0768 383 383", str(self.voldemort))
        self.assertEqual("Billy Nophone", str(self.billy))

        with AnonymousOrg(self.org):
            self.assertEqual("Joe Blow", self.joe.get_display(org=self.org, formatted=False))
            self.assertEqual("Joe Blow", self.joe.get_display())
            self.assertEqual("%010d" % self.voldemort.pk, self.voldemort.get_display())
            self.assertEqual("Billy Nophone", self.billy.get_display())

            self.assertEqual(ContactURN.ANON_MASK, self.joe.get_urn_display(org=self.org, formatted=False))
            self.assertEqual(ContactURN.ANON_MASK, self.joe.get_urn_display())
            self.assertEqual(ContactURN.ANON_MASK, self.voldemort.get_urn_display())
            self.assertEqual("", self.billy.get_urn_display())
            self.assertEqual("", self.billy.get_urn_display(scheme=URN.TEL_SCHEME))

            self.assertEqual("Joe Blow", str(self.joe))
            self.assertEqual("%010d" % self.voldemort.pk, str(self.voldemort))
            self.assertEqual("Billy Nophone", str(self.billy))

    def test_bulk_urn_cache_initialize(self):
        self.joe.refresh_from_db()
        self.billy.refresh_from_db()

        contacts = (self.joe, self.frank, self.billy)
        Contact.bulk_urn_cache_initialize(contacts)

        with self.assertNumQueries(0):
            self.assertEqual(["twitter:blow80", "tel:+250781111111"], [u.urn for u in self.joe.get_urns()])
            self.assertEqual(
                ["twitter:blow80", "tel:+250781111111"], [u.urn for u in getattr(self.joe, "_urns_cache")]
            )
            self.assertEqual(["tel:+250782222222"], [u.urn for u in self.frank.get_urns()])
            self.assertEqual([], [u.urn for u in self.billy.get_urns()])

    @patch("temba.contacts.search.omnibox.search_contacts")
    @mock_mailroom
    def test_omnibox(self, mr_mocks, mock_search_contacts):
        # add a group with members and an empty group
        self.create_field("gender", "Gender")
        joe_and_frank = self.create_group("Joe and Frank", [self.joe, self.frank])
        nobody = self.create_group("Nobody", [])

        men = self.create_group("Men", query="gender=M")
        ContactGroup.user_groups.filter(id=men.id).update(status=ContactGroup.STATUS_READY)

        # a group which is being re-evaluated and shouldn't appear in any omnibox results
        unready = self.create_group("Group being re-evaluated...", query="gender=M")
        unready.status = ContactGroup.STATUS_EVALUATING
        unready.save(update_fields=("status",))

        joe_tel = self.joe.get_urn(URN.TEL_SCHEME)
        joe_twitter = self.joe.get_urn(URN.TWITTER_SCHEME)
        frank_tel = self.frank.get_urn(URN.TEL_SCHEME)
        voldemort_tel = self.voldemort.get_urn(URN.TEL_SCHEME)

        # Postgres will defer to strcoll for ordering which even for en_US.UTF-8 will return different results on OSX
        # and Ubuntu. To keep ordering consistent for this test, we don't let URNs start with +
        # (see http://postgresql.nabble.com/a-strange-order-by-behavior-td4513038.html)
        ContactURN.objects.filter(path__startswith="+").update(
            path=Substr("path", 2), identity=Concat(DbValue("tel:"), Substr("path", 2))
        )

        self.admin.set_org(self.org)
        self.login(self.admin)

        def omnibox_request(query, version="1"):
            path = reverse("contacts.contact_omnibox")
            response = self.client.get(f"{path}?{query}&v={version}")
            return response.json()["results"]

        # omnibox view will try to search it as a contact then as a URN so 2 calls to mailroom search endpoint
        mr_mocks.error("ooh that doesn't look right")
        mr_mocks.error("ooh that doesn't look right again")

        # for this one test we want to call the actual search method..
        mock_search_contacts.side_effect = search_contacts

        # error is swallowed and we show no results
        self.assertEqual([], omnibox_request("search=-123`213"))

        with self.assertNumQueries(17):
            mock_search_contacts.side_effect = [
                SearchResults(
                    query="", total=4, contact_ids=[self.billy.id, self.frank.id, self.joe.id, self.voldemort.id]
                ),
                SearchResults(query="", total=3, contact_ids=[]),
            ]

            self.assertEqual(
                [
                    # all 3 groups A-Z
                    {"id": joe_and_frank.uuid, "name": "Joe and Frank", "type": "group", "count": 2},
                    {"id": men.uuid, "name": "Men", "type": "group", "count": 0},
                    {"id": nobody.uuid, "name": "Nobody", "type": "group", "count": 0},
                    # all 4 contacts A-Z
                    {"id": self.billy.uuid, "name": "Billy Nophone", "type": "contact", "urn": ""},
                    {"id": self.frank.uuid, "name": "Frank Smith", "type": "contact", "urn": "250782222222"},
                    {"id": self.joe.uuid, "name": "Joe Blow", "type": "contact", "urn": "blow80"},
                    {"id": self.voldemort.uuid, "name": "250768383383", "type": "contact", "urn": "250768383383"},
                ],
                omnibox_request(query="", version="2"),
            )

        with self.assertNumQueries(19):
            mock_search_contacts.side_effect = [
                SearchResults(query="", total=2, contact_ids=[self.billy.id, self.frank.id]),
                SearchResults(query="", total=2, contact_ids=[self.voldemort.id, self.frank.id]),
            ]

            self.assertEqual(
                [
                    # 2 contacts
                    {"id": self.billy.uuid, "name": "Billy Nophone", "type": "contact", "urn": ""},
                    {"id": self.frank.uuid, "name": "Frank Smith", "type": "contact", "urn": "250782222222"},
                    # 2 sendable URNs with contact names
                    {
                        "id": "tel:250768383383",
                        "name": "250768383383",
                        "contact": None,
                        "scheme": "tel",
                        "type": "urn",
                    },
                    {
                        "id": "tel:250782222222",
                        "name": "250782222222",
                        "type": "urn",
                        "contact": "Frank Smith",
                        "scheme": "tel",
                    },
                ],
                omnibox_request(query="search=250", version="2"),
            )

        with self.assertNumQueries(17):
            mock_search_contacts.side_effect = [
                SearchResults(query="", total=2, contact_ids=[self.billy.id, self.frank.id]),
                SearchResults(query="", total=0, contact_ids=[]),
            ]

            self.assertEqual(
                [
                    # all 3 groups A-Z
                    {"id": f"g-{joe_and_frank.uuid}", "text": "Joe and Frank", "extra": 2},
                    {"id": f"g-{men.uuid}", "text": "Men", "extra": 0},
                    {"id": f"g-{nobody.uuid}", "text": "Nobody", "extra": 0},
                    # 2 contacts A-Z
                    {"id": f"c-{self.billy.uuid}", "text": "Billy Nophone", "extra": ""},
                    {"id": f"c-{self.frank.uuid}", "text": "Frank Smith", "extra": "250782222222"},
                ],
                omnibox_request(query=""),
            )

        # apply type filters...

        # g = just the 3 groups
        self.assertEqual(
            [
                {"id": f"g-{joe_and_frank.uuid}", "text": "Joe and Frank", "extra": 2},
                {"id": f"g-{men.uuid}", "text": "Men", "extra": 0},
                {"id": f"g-{nobody.uuid}", "text": "Nobody", "extra": 0},
            ],
            omnibox_request("types=g"),
        )

        # s = just the 2 non-dynamic (static) groups
        self.assertEqual(
            [
                {"id": f"g-{joe_and_frank.uuid}", "text": "Joe and Frank", "extra": 2},
                {"id": f"g-{nobody.uuid}", "text": "Nobody", "extra": 0},
            ],
            omnibox_request("types=s"),
        )

        mock_search_contacts.side_effect = [
            SearchResults(
                query="", total=4, contact_ids=[self.billy.id, self.frank.id, self.joe.id, self.voldemort.id]
            ),
            SearchResults(query="", total=3, contact_ids=[self.voldemort.id, self.joe.id, self.frank.id]),
        ]
        self.assertEqual(
            [
                {"id": f"c-{self.billy.uuid}", "text": "Billy Nophone", "extra": ""},
                {"id": f"c-{self.frank.uuid}", "text": "Frank Smith", "extra": "250782222222"},
                {"id": f"c-{self.joe.uuid}", "text": "Joe Blow", "extra": "blow80"},
                {"id": f"c-{self.voldemort.uuid}", "text": "250768383383", "extra": "250768383383"},
                {"id": f"u-{voldemort_tel.id}", "text": "250768383383", "extra": None, "scheme": "tel"},
                {"id": f"u-{joe_tel.id}", "text": "250781111111", "extra": "Joe Blow", "scheme": "tel"},
                {"id": f"u-{frank_tel.id}", "text": "250782222222", "extra": "Frank Smith", "scheme": "tel"},
            ],
            omnibox_request("search=250&types=c,u"),
        )

        # search for Frank by phone
        mock_search_contacts.side_effect = [
            SearchResults(query="name ~ 222", total=0, contact_ids=[]),
            SearchResults(query="urn ~ 222", total=1, contact_ids=[self.frank.id]),
        ]
        self.assertEqual(
            [{"id": f"u-{frank_tel.id}", "text": "250782222222", "extra": "Frank Smith", "scheme": "tel"}],
            omnibox_request("search=222"),
        )

        # create twitter channel
        self.create_channel("TT", "Twitter", "nyaruka")

        # add add an external channel so numbers get normalized
        Channel.create(self.org, self.user, "RW", "EX", schemes=[URN.TEL_SCHEME])

        # search for Joe - match on last name and twitter handle
        mock_search_contacts.side_effect = [
            SearchResults(query="name ~ blow", total=1, contact_ids=[self.joe.id]),
            SearchResults(query="urn ~ blow", total=1, contact_ids=[self.joe.id]),
        ]
        self.assertEqual(
            [
                dict(id="c-%s" % self.joe.uuid, text="Joe Blow", extra="blow80"),
                dict(id="u-%d" % joe_tel.pk, text="0781 111 111", extra="Joe Blow", scheme="tel"),
                dict(id="u-%d" % joe_twitter.pk, text="blow80", extra="Joe Blow", scheme="twitter"),
            ],
            omnibox_request("search=BLOW"),
        )

        # lookup by group id
        self.assertEqual(
            [dict(id="g-%s" % joe_and_frank.uuid, text="Joe and Frank", extra=2)],
            omnibox_request(f"g={joe_and_frank.uuid}"),
        )

        # lookup by URN ids
        urn_query = "u=%d,%d" % (self.joe.get_urn(URN.TWITTER_SCHEME).id, self.frank.get_urn(URN.TEL_SCHEME).id)
        self.assertEqual(
            [
                dict(id="u-%d" % frank_tel.pk, text="0782 222 222", extra="Frank Smith", scheme="tel"),
                dict(id="u-%d" % joe_twitter.pk, text="blow80", extra="Joe Blow", scheme="twitter"),
            ],
            omnibox_request(urn_query),
        )

        # lookup by message ids
        msg = self.create_incoming_msg(self.joe, "some message")
        self.assertEqual(
            [dict(id="c-%s" % self.joe.uuid, text="Joe Blow", extra="blow80")], omnibox_request(f"m={msg.id}")
        )

        # lookup by label ids
        label = Label.get_or_create(self.org, self.user, "msg label")
        self.assertEqual([], omnibox_request(f"l={label.id}"))

        msg.labels.add(label)
        self.assertEqual(
            [dict(id="c-%s" % self.joe.uuid, text="Joe Blow", extra="blow80")], omnibox_request(f"l={label.id}")
        )

        with AnonymousOrg(self.org):
            mock_search_contacts.side_effect = [SearchResults(query="", total=1, contact_ids=[self.billy.id])]
            self.assertEqual(
                [
                    # all 3 groups...
                    {"id": f"g-{joe_and_frank.uuid}", "text": "Joe and Frank", "extra": 2},
                    {"id": f"g-{men.uuid}", "text": "Men", "extra": 0},
                    {"id": f"g-{nobody.uuid}", "text": "Nobody", "extra": 0},
                    # 1 contact
                    {"id": f"c-{self.billy.uuid}", "text": "Billy Nophone"},
                    # no urns
                ],
                omnibox_request(""),
            )

            # same search but with v2 format
            mock_search_contacts.side_effect = [SearchResults(query="", total=1, contact_ids=[self.billy.id])]
            self.assertEqual(
                [
                    # all 3 groups A-Z
                    {"id": joe_and_frank.uuid, "name": "Joe and Frank", "type": "group", "count": 2},
                    {"id": men.uuid, "name": "Men", "type": "group", "count": 0},
                    {"id": nobody.uuid, "name": "Nobody", "type": "group", "count": 0},
                    # 1 contact
                    {"id": self.billy.uuid, "name": "Billy Nophone", "type": "contact"},
                ],
                omnibox_request("", version="2"),
            )

        # exclude blocked and stopped contacts
        self.joe.block(self.admin)
        self.frank.stop(self.admin)

        # lookup by contact uuids
        self.assertEqual(omnibox_request("c=%s,%s" % (self.joe.uuid, self.frank.uuid)), [])

        # but still lookup by URN ids
        urn_query = "u=%d,%d" % (self.joe.get_urn(URN.TWITTER_SCHEME).pk, self.frank.get_urn(URN.TEL_SCHEME).pk)
        self.assertEqual(
            [
                {"id": f"u-{frank_tel.id}", "text": "0782 222 222", "extra": "Frank Smith", "scheme": "tel"},
                {"id": f"u-{joe_twitter.id}", "text": "blow80", "extra": "Joe Blow", "scheme": "twitter"},
            ],
            omnibox_request(urn_query),
        )

    def test_history(self):
        url = reverse("contacts.contact_history", args=[self.joe.uuid])

        kurt = self.create_contact("Kurt", phone="123123")
        self.joe.created_on = timezone.now() - timedelta(days=1000)
        self.joe.save(update_fields=("created_on",))

        self.create_broadcast(self.user, "A beautiful broadcast", contacts=[self.joe])
        self.create_campaign()

        # add a message with some attachments
        self.create_incoming_msg(
            self.joe,
            "Message caption",
            created_on=timezone.now(),
            attachments=[
                "audio/mp3:http://blah/file.mp3",
                "video/mp4:http://blah/file.mp4",
                "geo:47.5414799,-122.6359908",
            ],
        )

        # create some messages
        for i in range(94):
            self.create_incoming_msg(
                self.joe, "Inbound message %d" % i, created_on=timezone.now() - timedelta(days=(100 - i))
            )

        # because messages are stored with timestamps from external systems, possible to have initial message
        # which is little bit older than the contact itself
        self.create_incoming_msg(
            self.joe, "Very old inbound message", created_on=self.joe.created_on - timedelta(seconds=10)
        )

        flow = self.get_flow("color_v13")
        nodes = flow.get_definition()["nodes"]
        color_prompt = nodes[0]
        color_split = nodes[4]

        (
            MockSessionWriter(self.joe, flow)
            .visit(color_prompt)
            .send_msg("What is your favorite color?", self.channel)
            .call_webhook("POST", "https://example.com/", "1234")  # pretend that flow run made a webhook request
            .visit(color_split)
            .set_result("Color", "green", "Green", "I like green")
            .wait()
            .save()
        )
        (
            MockSessionWriter(kurt, flow)
            .visit(color_prompt)
            .send_msg("What is your favorite color?", self.channel)
            .visit(color_split)
            .wait()
            .save()
        )

        # mark an outgoing message as failed
        failed = Msg.objects.filter(direction="O", contact=self.joe).last()
        failed.status = "F"
        failed.save(update_fields=("status",))
        log = ChannelLog.objects.create(
            channel=failed.channel, msg=failed, is_error=True, description="It didn't send!!"
        )

        # create an airtime transfer
        transfer = AirtimeTransfer.objects.create(
            org=self.org,
            status="S",
            contact=self.joe,
            currency="RWF",
            desired_amount=Decimal("100"),
            actual_amount=Decimal("100"),
        )

        # create an event from the past
        scheduled = timezone.now() - timedelta(days=5)
        EventFire.objects.create(event=self.planting_reminder, contact=self.joe, scheduled=scheduled, fired=scheduled)

        # two tickets for joe
        ticketer = Ticketer.create(self.org, self.user, "internal", "Internal", {})
        self.create_ticket(ticketer, self.joe, "Question 1", closed_on=timezone.now())
        ticket = self.create_ticket(ticketer, self.joe, "Question 2")

        # create missed incoming and outgoing calls
        self.create_channel_event(
            self.channel, str(self.joe.get_urn(URN.TEL_SCHEME)), ChannelEvent.TYPE_CALL_OUT_MISSED, extra={}
        )
        self.create_channel_event(
            self.channel, str(self.joe.get_urn(URN.TEL_SCHEME)), ChannelEvent.TYPE_CALL_IN_MISSED, extra={}
        )

        # and a referral event
        self.create_channel_event(
            self.channel, str(self.joe.get_urn(URN.TEL_SCHEME)), ChannelEvent.TYPE_NEW_CONVERSATION, extra={}
        )

        # try adding some failed calls
        call = IVRCall.objects.create(
            contact=self.joe,
            status=IVRCall.STATUS_ERRORED,
            error_reason=IVRCall.ERROR_NOANSWER,
            channel=self.channel,
            org=self.org,
            contact_urn=self.joe.urns.all().first(),
            error_count=0,
        )

        # create a channel log for this call
        ChannelLog.objects.create(channel=self.channel, description="Its an ivr call", is_error=False, connection=call)

        # add a note to our open ticket
        ticket.events.create(
            org=self.org,
            contact=ticket.contact,
            event_type="N",
            note="I have a bad feeling about this",
            created_by=self.admin,
        )

        # create an assignment
        ticket.events.create(
            org=self.org,
            contact=ticket.contact,
            event_type="A",
            created_by=self.admin,
            assignee=self.admin,
        )

        # set an output URL on our session so we fetch from there
        s = FlowSession.objects.get(contact=self.joe)
        FlowSession.objects.filter(id=s.id).update(
            output_url="https://temba-sessions.s3.aws.amazon.com/c/session.json"
        )
        self.mock_s3.objects[("temba-sessions", "c/session.json")] = io.StringIO(json.dumps(s.output))

        # fetch our contact history
        self.login(self.admin)
        with patch("temba.utils.s3.s3.client", return_value=self.mock_s3):
            with self.assertNumQueries(46):
                response = self.client.get(url + "?limit=100")

        # history should include all messages in the last 90 days, the channel event, the call, and the flow run
        history = response.context["events"]
        self.assertEqual(98, len(history))

        def assertHistoryEvent(events, index, expected_type, **kwargs):
            item = events[index]
            self.assertEqual(expected_type, item["type"], f"event type mismatch for item {index}")
            self.assertTrue(iso8601.parse_date(item["created_on"]))  # check created_on exists and is ISO string

            for path, expected in kwargs.items():
                self.assertPathValue(item, path, expected, f"item {index}")

        assertHistoryEvent(history, 0, "call_started", status="E", status_display="Errored (No Answer)")
        assertHistoryEvent(history, 1, "channel_event", channel_event_type="new_conversation")
        assertHistoryEvent(history, 2, "channel_event", channel_event_type="mo_miss")
        assertHistoryEvent(history, 3, "channel_event", channel_event_type="mt_miss")
        assertHistoryEvent(history, 4, "ticket_opened", ticket__body="Question 2")
        assertHistoryEvent(history, 5, "ticket_closed", ticket__body="Question 1")
        assertHistoryEvent(history, 6, "ticket_opened", ticket__body="Question 1")
        assertHistoryEvent(history, 7, "airtime_transferred", actual_amount=Decimal("100.00"))
        assertHistoryEvent(history, 8, "run_result_changed", value="green")
        assertHistoryEvent(history, 9, "webhook_called", url="https://example.com/")
        assertHistoryEvent(history, 10, "msg_created", msg__text="What is your favorite color?")
        assertHistoryEvent(history, 11, "flow_entered", flow__name="Colors")
        assertHistoryEvent(history, 12, "msg_received", msg__text="Message caption")
        assertHistoryEvent(
            history, 13, "msg_created", msg__text="A beautiful broadcast", msg__created_by__email="User@nyaruka.com"
        )
        assertHistoryEvent(history, 14, "campaign_fired", campaign__name="Planting Reminders")
        assertHistoryEvent(history, -1, "msg_received", msg__text="Inbound message 11")

        self.assertContains(response, "<audio ")
        self.assertContains(response, '<source type="audio/mp3" src="http://blah/file.mp3" />')
        self.assertContains(response, "<video ")
        self.assertContains(response, '<source type="video/mp4" src="http://blah/file.mp4" />')
        self.assertContains(
            response,
            "http://www.openstreetmap.org/?mlat=47.5414799&amp;mlon=-122.6359908#map=18/47.5414799/-122.6359908",
        )
        self.assertContains(response, reverse("channels.channellog_read", args=[log.id]))
        self.assertContains(response, reverse("channels.channellog_connection", args=[call.id]))
        self.assertContains(response, "Transferred <b>100.00</b> <b>RWF</b> of airtime")
        self.assertContains(response, reverse("airtime.airtimetransfer_read", args=[transfer.id]))

        # revert back to reading only from DB
        FlowSession.objects.filter(id=s.id).update(output_url=None)

        # can filter by ticket to only all ticket events from that ticket rather than some events from all tickets
        response = self.client.get(url + f"?ticket={ticket.uuid}&limit=100")
        history = response.context["events"]
        assertHistoryEvent(history, 0, "ticket_assigned", assignee__id=self.admin.id)
        assertHistoryEvent(history, 1, "ticket_note_added", note="I have a bad feeling about this")
        assertHistoryEvent(history, 5, "channel_event", channel_event_type="mt_miss")
        assertHistoryEvent(history, 6, "ticket_opened", ticket__body="Question 2")
        assertHistoryEvent(history, 7, "airtime_transferred", actual_amount=Decimal("100.00"))

        # can also fetch same page as JSON
        response_json = self.client.get(url + "?limit=100&_format=json").json()
        self.assertEqual(98, len(response_json["events"]))

        # fetch next page
        before = datetime_to_timestamp(timezone.now() - timedelta(days=90))
        response = self.fetch_protected(url + "?limit=100&before=%d" % before, self.admin)
        self.assertFalse(response.context["has_older"])

        # none of our messages have a failed status yet
        self.assertNotContains(response, "icon-bubble-notification")

        # activity should include 11 remaining messages and the event fire
        history = response.context["events"]
        self.assertEqual(12, len(history))
        assertHistoryEvent(history, 0, "msg_received", msg__text="Inbound message 10")
        assertHistoryEvent(history, 10, "msg_received", msg__text="Inbound message 0")
        assertHistoryEvent(history, 11, "msg_received", msg__text="Very old inbound message")

        response = self.fetch_protected(url + "?limit=100", self.admin)
        history = response.context["events"]

        self.assertEqual(98, len(history))
        assertHistoryEvent(history, 10, "msg_created", msg__text="What is your favorite color?")

        # if a new message comes in
        self.create_incoming_msg(self.joe, "Newer message")
        response = self.fetch_protected(url, self.admin)

        # now we'll see the message that just came in first, followed by the call event
        history = response.context["events"]
        assertHistoryEvent(history, 0, "msg_received", msg__text="Newer message")
        assertHistoryEvent(history, 1, "call_started", status="E", status_display="Errored (No Answer)")

        recent_start = datetime_to_timestamp(timezone.now() - timedelta(days=1))
        response = self.fetch_protected(url + "?limit=100&after=%s" % recent_start, self.admin)

        # with our recent flag on, should not see the older messages
        events = response.context["events"]
        self.assertEqual(15, len(events))
        self.assertContains(response, "file.mp4")

        # can't view history of contact in another org
        hans = self.create_contact("Hans", urns=["twitter:hans"], org=self.org2)
        response = self.client.get(reverse("contacts.contact_history", args=[hans.uuid]))
        self.assertLoginRedirect(response)

        # invalid UUID should return 404
        response = self.client.get(reverse("contacts.contact_history", args=["bad-uuid"]))
        self.assertEqual(response.status_code, 404)

        # super users can view history of any contact
        response = self.fetch_protected(url + "?limit=90", self.superuser)
        self.assertEqual(90, len(response.context["events"]))

        response = self.fetch_protected(reverse("contacts.contact_history", args=[hans.uuid]), self.superuser)
        self.assertEqual(0, len(response.context["events"]))

        # add a new run
        (
            MockSessionWriter(self.joe, flow)
            .visit(color_prompt)
            .send_msg("What is your favorite color?", self.channel)
            .visit(color_split)
            .wait()
            .save()
        )

        response = self.fetch_protected(url + "?limit=200", self.admin)
        history = response.context["events"]
        self.assertEqual(102, len(history))

        # before date should not match our last activity, that only happens when we truncate
        self.assertNotEqual(
            response.context["next_before"],
            datetime_to_timestamp(iso8601.parse_date(response.context["events"][-1]["created_on"])),
        )

        assertHistoryEvent(history, 0, "msg_created", msg__text="What is your favorite color?")
        assertHistoryEvent(history, 1, "flow_entered")
        assertHistoryEvent(history, 2, "flow_exited")
        assertHistoryEvent(history, 3, "msg_received", msg__text="Newer message")
        assertHistoryEvent(history, 4, "call_started")
        assertHistoryEvent(history, 5, "channel_event")
        assertHistoryEvent(history, 6, "channel_event")
        assertHistoryEvent(history, 7, "channel_event")
        assertHistoryEvent(history, 8, "ticket_opened")
        assertHistoryEvent(history, 9, "ticket_closed")
        assertHistoryEvent(history, 10, "ticket_opened")
        assertHistoryEvent(history, 11, "airtime_transferred")
        assertHistoryEvent(history, 12, "run_result_changed")
        assertHistoryEvent(history, 13, "webhook_called")
        assertHistoryEvent(history, 14, "msg_created", msg__text="What is your favorite color?")
        assertHistoryEvent(history, 15, "flow_entered")

        # make our message event older than our planting reminder
        self.message_event.created_on = self.planting_reminder.created_on - timedelta(days=1)
        self.message_event.save()

        # but fire it immediately
        scheduled = timezone.now()
        EventFire.objects.create(event=self.message_event, contact=self.joe, scheduled=scheduled, fired=scheduled)

        # when fetched with limit of 1, it should be the only event we see
        response = self.fetch_protected(
            url + "?limit=1&before=%d" % datetime_to_timestamp(scheduled + timedelta(minutes=5)), self.admin
        )
        assertHistoryEvent(response.context["events"], 0, "campaign_fired", campaign_event__id=self.message_event.id)

        # now try the proper max history to test truncation
        response = self.fetch_protected(url + "?before=%d" % datetime_to_timestamp(timezone.now()), self.admin)

        # our before should be the same as the last item
        last_item_date = datetime_to_timestamp(iso8601.parse_date(response.context["events"][-1]["created_on"]))
        self.assertEqual(response.context["next_before"], last_item_date)

        # and our after should be 90 days earlier
        self.assertEqual(response.context["next_after"], last_item_date - (90 * 24 * 60 * 60 * 1000 * 1000))
        self.assertEqual(50, len(response.context["events"]))

        # and we should have a marker for older items
        self.assertTrue(response.context["has_older"])

        # can't view history of contact in other org
        response = self.client.get(reverse("contacts.contact_history", args=[self.other_org_contact.uuid]))
        self.assertLoginRedirect(response)

    def test_history_session_events(self):
        flow = self.get_flow("color_v13")
        nodes = flow.get_definition()["nodes"]
        (
            MockSessionWriter(self.joe, flow)
            .visit(nodes[0])
            .add_contact_urn("twitter", "joey")
            .set_contact_field("gender", "Gender", "M")
            .set_contact_field("age", "Age", "")
            .set_contact_language("spa")
            .set_contact_language("")
            .set_contact_name("Joe")
            .set_contact_name("")
            .set_result("Color", "red", "Red", "it's red")
            .send_email(["joe@nyaruka.com"], "Test", "Hello there Joe")
            .error("unable to send email")
            .fail("this is a failure")
            .save()
        )

        url = reverse("contacts.contact_history", args=[self.joe.uuid])
        self.login(self.user)

        response = self.client.get(url)
        self.assertEqual(200, response.status_code)
        self.assertContains(response, "URNs updated to")
        self.assertContains(response, "<b>blow80</b>, ")
        self.assertContains(response, "<b>+250 781 111 111</b>, and ")
        self.assertContains(response, "<b>joey</b>")
        self.assertContains(response, "Field <b>Gender</b> updated to <b>M</b>")
        self.assertContains(response, "Field <b>Age</b> cleared")
        self.assertContains(response, "Language updated to <b>spa</b>")
        self.assertContains(response, "Language cleared")
        self.assertContains(response, "Name updated to <b>Joe</b>")
        self.assertContains(response, "Name cleared")
        self.assertContains(response, "Run result <b>Color</b> updated to <b>red</b> with category <b>Red</b>")
        self.assertContains(
            response,
            "Email sent to\n        \n          <b>joe@nyaruka.com</b>\n        \n        with subject\n        <b>Test</b>",
        )
        self.assertContains(response, "unable to send email")
        self.assertContains(response, "this is a failure")

    def test_history_templatetags(self):
        item = {"type": "webhook_called", "url": "http://test.com", "status": "success"}
        self.assertEqual(history_class(item), "non-msg detail-event")

        item = {"type": "webhook_called", "url": "http://test.com", "status": "response_error"}
        self.assertEqual(history_class(item), "non-msg warning detail-event")

        item = {"type": "call_started", "status": "D"}
        self.assertEqual(history_class(item), "non-msg")

        item = {"type": "call_started", "status": "F"}
        self.assertEqual(history_class(item), "non-msg warning")

        # inbound
        item = {"type": "msg_received", "msg": {"text": "Hi"}, "msg_type": "I"}
        self.assertEqual(history_icon(item), '<span class="glyph icon-bubble-user"></span>')

        # outgoing sent
        item = {"type": "msg_created", "msg": {"text": "Hi"}, "status": "S"}
        self.assertEqual(history_icon(item), '<span class="glyph icon-bubble-right"></span>')

        # outgoing delivered
        item = {"type": "msg_created", "msg": {"text": "Hi"}, "status": "D"}
        self.assertEqual(history_icon(item), '<span class="glyph icon-bubble-check"></span>')

        # failed
        item = {"type": "msg_created", "msg": {"text": "Hi"}, "status": "F"}
        self.assertEqual(history_icon(item), '<span class="glyph icon-bubble-notification"></span>')
        self.assertEqual(history_class(item), "msg warning")

        # outgoing voice
        item = {"type": "ivr_created", "msg": {"text": "Hi"}, "status": "F"}
        self.assertEqual(history_icon(item), '<span class="glyph icon-call-outgoing"></span>')
        self.assertEqual(history_class(item), "msg warning")

        # incoming voice
        item = {"type": "msg_received", "msg": {"text": "Hi"}, "msg_type": "V"}
        self.assertEqual(history_icon(item), '<span class="glyph icon-call-incoming"></span>')
        self.assertEqual(history_class(item), "msg")

        # simulate a broadcast to 2 people
        item = {"type": "broadcast_created", "recipient_count": 2}
        self.assertEqual(history_icon(item), '<span class="glyph icon-bullhorn"></span>')

        item = {"type": "flow_entered", "flow": {"uuid": "1234", "name": "Survey"}}
        self.assertEqual(history_icon(item), '<span class="glyph icon-flow"></span>')

        item = {"type": "flow_exited", "flow": {"uuid": "1234", "name": "Survey"}, "status": "C"}
        self.assertEqual(history_icon(item), '<span class="glyph icon-checkmark"></span>')

        item = {"type": "flow_exited", "flow": {"uuid": "1234", "name": "Survey"}, "status": "I"}
        self.assertEqual(history_icon(item), '<span class="glyph icon-cancel-circle"></span>')

        item = {"type": "flow_exited", "flow": {"uuid": "1234", "name": "Survey"}, "status": "X"}
        self.assertEqual(history_icon(item), '<span class="glyph icon-clock"></span>')

        item = {"type": "campaign_fired", "campaign": {}, "fired_result": "F"}
        self.assertEqual(history_icon(item), '<span class="glyph icon-clock"></span>')
        self.assertEqual(history_class(item), "non-msg")

        item = {"type": "campaign_fired", "campaign": {}, "fired_result": "S"}
        self.assertEqual(history_icon(item), '<span class="glyph icon-clock"></span>')
        self.assertEqual(history_class(item), "non-msg skipped")

        item = {"type": "airtime_transferred", "currency": "RWF", "actual_amount": "100"}
        self.assertEqual(history_icon(item), '<span class="glyph icon-cash"></span>')
        self.assertEqual(history_class(item), "non-msg detail-event")

    def test_get_scheduled_messages(self):
        self.just_joe = self.create_group("Just Joe", [self.joe])

        self.assertFalse(self.joe.get_scheduled_messages())

        broadcast = Broadcast.create(self.org, self.admin, "Hello", contacts=[self.frank])
        self.assertFalse(self.joe.get_scheduled_messages())

        broadcast.contacts.add(self.joe)

        self.assertFalse(self.joe.get_scheduled_messages())

        schedule_time = timezone.now() + timedelta(days=2)
        broadcast.schedule = Schedule.create_schedule(self.org, self.admin, schedule_time, Schedule.REPEAT_NEVER)
        broadcast.save()

        self.assertEqual(self.joe.get_scheduled_messages().count(), 1)
        self.assertTrue(broadcast in self.joe.get_scheduled_messages())

        broadcast.contacts.remove(self.joe)
        self.assertFalse(self.joe.get_scheduled_messages())

        broadcast.groups.add(self.just_joe)
        self.assertEqual(self.joe.get_scheduled_messages().count(), 1)
        self.assertTrue(broadcast in self.joe.get_scheduled_messages())

        broadcast.groups.remove(self.just_joe)
        self.assertFalse(self.joe.get_scheduled_messages())

        broadcast.urns.add(self.joe.get_urn())
        self.assertEqual(self.joe.get_scheduled_messages().count(), 1)
        self.assertTrue(broadcast in self.joe.get_scheduled_messages())

        broadcast.schedule.next_fire = None
        broadcast.schedule.save(update_fields=["next_fire"])
        self.assertFalse(self.joe.get_scheduled_messages())

    def test_update_urns_field(self):
        update_url = reverse("contacts.contact_update", args=[self.joe.pk])

        # we have a field to add new urns
        response = self.fetch_protected(update_url, self.admin)
        self.assertEqual(self.joe, response.context["object"])
        self.assertContains(response, "Add Connection")

        # no field to add new urns for anon org
        with AnonymousOrg(self.org):
            response = self.fetch_protected(update_url, self.admin)
            self.assertEqual(self.joe, response.context["object"])
            self.assertNotContains(response, "Add Connection")

    @mock_mailroom
    def test_read(self, mr_mocks):
        read_url = reverse("contacts.contact_read", args=[self.joe.uuid])

        for i in range(5):
            self.create_incoming_msg(self.joe, f"some msg no {i} 2 send in sms language if u wish")
            i += 1

        self.create_campaign()

        # create more events
        for i in range(5):
            msg = "Sent %d days after planting date" % (i + 10)
            self.message_event = CampaignEvent.create_message_event(
                self.org,
                self.admin,
                self.campaign,
                relative_to=self.planting_date,
                offset=i + 10,
                unit="D",
                message=msg,
            )

        planters = self.create_group("Planters", query='planting_date != ""')
        planters.contacts.add(self.joe)

        now = timezone.now()
        joe_planting_date = now + timedelta(days=1)
        self.set_contact_field(self.joe, "planting_date", joe_planting_date.isoformat())

        # should have seven fires, one for each campaign event
        self.assertEqual(7, EventFire.objects.filter(event__is_active=True).count())

        # visit a contact detail page as a user but not belonging to this organization
        self.login(self.user1)
        response = self.client.get(read_url)
        self.assertEqual(302, response.status_code)

        # visit a contact detail page as a manager but not belonging to this organisation
        self.login(self.non_org_user)
        response = self.client.get(read_url)
        self.assertEqual(302, response.status_code)

        # visit a contact detail page as a manager within the organization
        response = self.fetch_protected(read_url, self.admin)
        self.assertEqual(self.joe, response.context["object"])

        with patch("temba.orgs.models.Org.get_schemes") as mock_get_schemes:
            mock_get_schemes.return_value = []

            response = self.fetch_protected(read_url, self.admin)
            self.assertEqual(self.joe, response.context["object"])
            self.assertFalse(response.context["has_sendable_urn"])

            mock_get_schemes.return_value = ["tel"]

            response = self.fetch_protected(read_url, self.admin)
            self.assertEqual(self.joe, response.context["object"])
            self.assertTrue(response.context["has_sendable_urn"])

        response = self.fetch_protected(read_url, self.admin)
        self.assertEqual(self.joe, response.context["object"])
        self.assertTrue(response.context["has_sendable_urn"])
        upcoming = response.context["upcoming_events"]

        # should show the next seven events to fire in reverse order
        self.assertEqual(7, len(upcoming))

        self.assertEqual(upcoming[4]["message"], "Sent 10 days after planting date")
        self.assertEqual(upcoming[5]["message"], "Sent 7 days after planting date")
        self.assertEqual(upcoming[6]["message"], None)
        self.assertEqual(upcoming[6]["flow_uuid"], self.reminder_flow.uuid)
        self.assertEqual(upcoming[6]["flow_name"], self.reminder_flow.name)

        self.assertGreater(upcoming[4]["scheduled"], upcoming[5]["scheduled"])

        # add a scheduled broadcast
        broadcast = Broadcast.create(self.org, self.admin, "Hello", contacts=[self.joe])
        schedule_time = now + timedelta(days=5)
        broadcast.schedule = Schedule.create_schedule(self.org, self.admin, schedule_time, Schedule.REPEAT_NEVER)
        broadcast.save()

        response = self.fetch_protected(read_url, self.admin)
        self.assertEqual(self.joe, response.context["object"])
        upcoming = response.context["upcoming_events"]

        # should show the next 2 events to fire and the scheduled broadcast in reverse order by schedule time
        self.assertEqual(8, len(upcoming))

        self.assertEqual(upcoming[5]["message"], "Sent 7 days after planting date")
        self.assertEqual(upcoming[6]["message"], "Hello")
        self.assertEqual(upcoming[7]["message"], None)
        self.assertEqual(upcoming[7]["flow_uuid"], self.reminder_flow.uuid)
        self.assertEqual(upcoming[7]["flow_name"], self.reminder_flow.name)

        self.assertGreater(upcoming[6]["scheduled"], upcoming[7]["scheduled"])

        contact_no_name = self.create_contact(name=None, phone="678")
        read_url = reverse("contacts.contact_read", args=[contact_no_name.uuid])
        response = self.fetch_protected(read_url, self.superuser)
        self.assertEqual(contact_no_name, response.context["object"])
        self.client.logout()

        # login as a manager from out of this organization
        self.login(self.non_org_user)

        # create kLab group, and add joe to the group
        klab = self.create_group("kLab", [self.joe])

        # post with remove_from_group action to read url, with joe's contact and kLab group
        response = self.client.post(read_url + "?action=remove_from_group", {"contact": self.joe.id, "group": klab.id})

        # this manager cannot operate on this organization
        self.assertEqual(302, response.status_code)
        self.assertEqual(3, self.joe.user_groups.count())
        self.client.logout()

        # login as a manager of kLab
        self.login(self.admin)

        # remove this contact form kLab group
        response = self.client.post(read_url + "?action=remove_from_group", {"contact": self.joe.id, "group": klab.id})

        self.assertEqual(200, response.status_code)
        self.assertEqual(2, self.joe.user_groups.count())

        # try removing it again, should noop
        response = self.client.post(read_url + "?action=remove_from_group", {"contact": self.joe.id, "group": klab.id})
        self.assertEqual(200, response.status_code)
        self.assertEqual(2, self.joe.user_groups.count())

        # try removing from non-existent group
        response = self.client.post(read_url + "?action=remove_from_group", {"contact": self.joe.id, "group": 2341533})
        self.assertEqual(200, response.status_code)
        self.assertEqual(2, self.joe.user_groups.count())

        # try removing from dynamic group (shouldnt happen, UI doesnt allow this)
        with self.assertRaises(AssertionError):
            self.client.post(read_url + "?action=remove_from_group", {"contact": self.joe.id, "group": planters.id})

        # can't view contact in another org
        response = self.client.get(reverse("contacts.contact_read", args=[self.other_org_contact.uuid]))
        self.assertLoginRedirect(response)

        # invalid UUID should return 404
        response = self.client.get(reverse("contacts.contact_read", args=["bad-uuid"]))
        self.assertEqual(response.status_code, 404)

        # super users can view history of any contact
        response = self.fetch_protected(reverse("contacts.contact_read", args=[self.joe.uuid]), self.superuser)
        self.assertEqual(response.status_code, 200)
        response = self.fetch_protected(
            reverse("contacts.contact_read", args=[self.other_org_contact.uuid]), self.superuser
        )
        self.assertEqual(response.status_code, 200)

    def test_read_with_customer_support(self):
        self.customer_support.is_staff = True
        self.customer_support.save()

        self.login(self.customer_support)

        response = self.client.get(reverse("contacts.contact_read", args=[self.joe.uuid]))

        gear_links = response.context["view"].get_gear_links()
        self.assertListEqual([gl["title"] for gl in gear_links], ["Service"])
        self.assertEqual(
            gear_links[-1]["href"],
            f"/org/service/?organization={self.joe.org_id}&redirect_url=/contact/read/{self.joe.uuid}/",
        )

    def test_read_language(self):

        # this is a bogus
        self.joe.language = "zzz"
        self.joe.save(update_fields=("language",))
        response = self.fetch_protected(reverse("contacts.contact_read", args=[self.joe.uuid]), self.admin)

        # should just show the language code instead of the language name
        self.assertContains(response, "zzz")

        self.joe.language = "fra"
        self.joe.save(update_fields=("language",))
        response = self.fetch_protected(reverse("contacts.contact_read", args=[self.joe.uuid]), self.admin)

        # with a proper code, we should see the language
        self.assertContains(response, "French")

    @mock_mailroom
    def test_contacts_search(self, mr_mocks):
        search_url = reverse("contacts.contact_search")
        self.login(self.admin)

        mr_mocks.contact_search("Frank", cleaned='name ~ "Frank"', contacts=[self.frank])

        response = self.client.get(search_url + "?search=Frank")
        self.assertEqual(200, response.status_code)
        results = response.json()

        # check that we get a total and a sample
        self.assertEqual(1, results["total"])
        self.assertEqual(1, len(results["sample"]))
        self.assertEqual("+250 782 222 222", results["sample"][0]["primary_urn_formatted"])

        # our query should get expanded into a proper query
        self.assertEqual('name ~ "Frank"', results["query"])

        # check no primary urn
        self.frank.urns.all().delete()
        response = self.client.get(search_url + "?search=Frank")
        self.assertEqual(200, response.status_code)
        results = response.json()
        self.assertEqual("--", results["sample"][0]["primary_urn_formatted"])

        # no query, no results
        response = self.client.get(search_url)
        results = response.json()
        self.assertEqual(0, results["total"])

        mr_mocks.error("mismatched input at <EOF>", code="unexpected_token", extra={"token": "<EOF>"})

        # bogus query
        response = self.client.get(search_url + '?search=name="notclosed')
        results = response.json()
        self.assertEqual("Invalid query syntax at '<EOF>'", results["error"])
        self.assertEqual(0, results["total"])

        # if we query a field, it should show up in our field dict
        age = self.create_field("age", "Age", ContactField.TYPE_NUMBER)

        mr_mocks.contact_search("age>32", cleaned='age > 32"', contacts=[self.frank], fields=[age])

        response = self.client.get(search_url + "?search=age>32")
        results = response.json()
        self.assertEqual("Age", results["fields"][str(age.uuid)]["label"])

    @mock_mailroom
    def test_update_and_list(self, mr_mocks):
        self.setUpLocations()

        list_url = reverse("contacts.contact_list")

        self.just_joe = self.create_group("Just Joe", [self.joe])
        self.joe_and_frank = self.create_group("Joe and Frank", [self.joe, self.frank])

        self.joe_and_frank = ContactGroup.user_groups.get(pk=self.joe_and_frank.pk)

        # try to list contacts as a user not in the organization
        self.login(self.user1)
        response = self.client.get(list_url)
        self.assertEqual(302, response.status_code)

        # login as an org viewer
        self.login(self.user)

        response = self.client.get(list_url)
        self.assertContains(response, "Joe Blow")
        self.assertContains(response, "Frank Smith")
        self.assertContains(response, "Billy Nophone")
        self.assertContains(response, "Joe and Frank")

        # make sure Joe's preferred URN is in the list
        self.assertContains(response, "blow80")

        # this just_joe group has one contact and joe_and_frank group has two contacts
        self.assertEqual(len(self.just_joe.contacts.all()), 1)
        self.assertEqual(len(self.joe_and_frank.contacts.all()), 2)

        # viewer cannot remove Joe from the group
        post_data = {"action": "label", "label": self.just_joe.id, "objects": self.joe.id, "add": False}

        # no change
        self.client.post(list_url, post_data, follow=True)
        self.assertEqual(len(self.just_joe.contacts.all()), 1)
        self.assertEqual(len(self.joe_and_frank.contacts.all()), 2)

        # viewer also can't block
        post_data["action"] = "block"
        self.client.post(list_url, post_data, follow=True)
        self.assertEqual(Contact.STATUS_ACTIVE, Contact.objects.get(pk=self.joe.id).status)

        # list the contacts as a manager of the organization
        self.login(self.admin)
        response = self.client.get(list_url)
        self.assertEqual(list(response.context["object_list"]), [self.voldemort, self.billy, self.frank, self.joe])
        self.assertEqual(response.context["actions"], ("label", "block", "archive"))

        # this just_joe group has one contact and joe_and_frank group has two contacts
        self.assertEqual(len(self.just_joe.contacts.all()), 1)
        self.assertEqual(len(self.joe_and_frank.contacts.all()), 2)

        # add a new group
        group = self.create_group("Test", [self.joe])

        # view our test group
        filter_url = reverse("contacts.contact_filter", args=[group.uuid])
        response = self.client.get(filter_url)
        self.assertEqual(1, len(response.context["object_list"]))
        self.assertEqual(self.joe, response.context["object_list"][0])

        # should have the export link
        export_url = "%s?g=%s" % (reverse("contacts.contact_export"), group.uuid)
        self.assertContains(response, export_url)

        # should have an edit button
        update_url = reverse("contacts.contactgroup_update", args=[group.pk])
        delete_url = reverse("contacts.contactgroup_delete", args=[group.pk])

        self.assertContains(response, update_url)
        response = self.client.get(update_url)
        self.assertIn("name", response.context["form"].fields)

        response = self.client.post(update_url, dict(name="New Test"))
        self.assertRedirect(response, filter_url)

        group = ContactGroup.user_groups.get(pk=group.pk)
        self.assertEqual("New Test", group.name)

        # post to our delete url
        response = self.client.post(delete_url, dict(), HTTP_X_PJAX=True)
        self.assertEqual(200, response.status_code)

        # make sure it is inactive
        self.assertIsNone(ContactGroup.user_groups.filter(name="New Test").first())
        self.assertFalse(ContactGroup.all_groups.get(name="New Test").is_active)

        # remove Joe from the group
        self.client.post(
            list_url, {"action": "label", "label": self.just_joe.id, "objects": self.joe.id, "add": False}, follow=True
        )

        # check the Joe is only removed from just_joe only and is still in joe_and_frank
        self.assertEqual(len(self.just_joe.contacts.all()), 0)
        self.assertEqual(len(self.joe_and_frank.contacts.all()), 2)

        # now add back Joe to the group
        self.client.post(
            list_url, {"action": "label", "label": self.just_joe.id, "objects": self.joe.id, "add": True}, follow=True
        )

        self.assertEqual(len(self.just_joe.contacts.all()), 1)
        self.assertEqual(self.just_joe.contacts.all()[0].pk, self.joe.pk)
        self.assertEqual(len(self.joe_and_frank.contacts.all()), 2)

        # test on a secondary org
        other = self.create_group("Other Org", org=self.org2)
        response = self.client.get(reverse("contacts.contact_filter", args=[other.uuid]))
        self.assertLoginRedirect(response)

        # test filtering by group
        joe_and_frank_filter_url = reverse("contacts.contact_filter", args=[self.joe_and_frank.uuid])

        # now test when the action with some data missing
        self.assertEqual(self.joe.user_groups.filter(is_active=True).count(), 2)

        self.client.post(joe_and_frank_filter_url, {"action": "label", "objects": self.joe.id, "add": True})

        self.assertEqual(self.joe.user_groups.filter(is_active=True).count(), 2)

        self.client.post(joe_and_frank_filter_url, {"action": "label", "objects": self.joe.id, "add": False})

        self.assertEqual(self.joe.user_groups.filter(is_active=True).count(), 2)

        # now block Joe
        self.client.post(list_url, {"action": "block", "objects": self.joe.id}, follow=True)

        self.joe = Contact.objects.filter(pk=self.joe.pk)[0]
        self.assertEqual(Contact.STATUS_BLOCKED, self.joe.status)
        self.assertEqual(len(self.just_joe.contacts.all()), 0)
        self.assertEqual(len(self.joe_and_frank.contacts.all()), 1)

        # shouldn't be any contacts on the stopped page
        response = self.client.get(reverse("contacts.contact_stopped"))
        self.assertEqual(0, len(response.context["object_list"]))

        # mark frank as stopped
        self.frank.stop(self.user)

        stopped_url = reverse("contacts.contact_stopped")

        response = self.client.get(stopped_url)
        self.assertEqual(1, len(response.context["object_list"]))
        self.assertEqual(1, response.context["object_list"].count())  # from ContactGroupCount

        # have the user unstop them
        self.client.post(stopped_url, {"action": "restore", "objects": self.frank.id}, follow=True)

        response = self.client.get(stopped_url)
        self.assertEqual(0, len(response.context["object_list"]))
        self.assertEqual(0, response.context["object_list"].count())  # from ContactGroupCount

        self.frank.refresh_from_db()
        self.assertEqual(Contact.STATUS_ACTIVE, self.frank.status)

        # add him back to joe and frank
        self.joe_and_frank.contacts.add(self.frank)

        # Now let's visit the blocked contacts page
        blocked_url = reverse("contacts.contact_blocked")

        self.billy.block(self.admin)

        response = self.client.get(blocked_url)
        self.assertEqual(list(response.context["object_list"]), [self.billy, self.joe])

        mr_mocks.contact_search("Joe", cleaned="name ~ Joe", contacts=[self.joe])

        response = self.client.get(blocked_url + "?search=Joe")
        self.assertEqual(list(response.context["object_list"]), [self.joe])

        # can unblock contacts from this page
        self.client.post(blocked_url, {"action": "restore", "objects": self.joe.id}, follow=True)

        # and check that Joe is restored to the contact list but the group not restored
        response = self.client.get(list_url)
        self.assertContains(response, "Joe Blow")
        self.assertContains(response, "Frank Smith")
        self.assertEqual(response.context["actions"], ("label", "block", "archive"))
        self.assertEqual(len(self.just_joe.contacts.all()), 0)
        self.assertEqual(len(self.joe_and_frank.contacts.all()), 1)

        # now let's test removing a contact from a group
        post_data = dict()
        post_data["action"] = "label"
        post_data["label"] = self.joe_and_frank.id
        post_data["objects"] = self.frank.id
        post_data["add"] = False
        self.client.post(joe_and_frank_filter_url, post_data, follow=True)
        self.assertEqual(len(self.joe_and_frank.contacts.all()), 0)

        # add an extra field to the org
        ContactField.get_or_create(
            self.org, self.user, "state", label="Home state", value_type=ContactField.TYPE_STATE
        )
        self.set_contact_field(self.joe, "state", " kiGali   citY ")  # should match "Kigali City"

        # check that the field appears on the update form
        response = self.client.get(reverse("contacts.contact_update", args=[self.joe.id]))

        self.assertEqual(
            list(response.context["form"].fields.keys()), ["name", "groups", "urn__twitter__0", "urn__tel__1", "loc"]
        )
        self.assertEqual(response.context["form"].initial["name"], "Joe Blow")
        self.assertEqual(response.context["form"].fields["urn__tel__1"].initial, "+250781111111")

        contact_field = ContactField.user_fields.filter(key="state").first()
        response = self.client.get(
            "%s?field=%s" % (reverse("contacts.contact_update_fields", args=[self.joe.id]), contact_field.id)
        )
        self.assertEqual("Home state", response.context["contact_field"].label)

        # grab our input field which is loaded async
        response = self.client.get(
            "%s?field=%s" % (reverse("contacts.contact_update_fields_input", args=[self.joe.id]), contact_field.id)
        )
        self.assertContains(response, "Kigali City")

        # update it to something else
        self.set_contact_field(self.joe, "state", "eastern province")

        # check the read page
        response = self.client.get(reverse("contacts.contact_read", args=[self.joe.uuid]))
        self.assertContains(response, "Eastern Province")

        # update joe - change his tel URN
        data = dict(name="Joe Blow", urn__tel__1="+250 783835665", order__urn__tel__1="0")
        self.client.post(reverse("contacts.contact_update", args=[self.joe.id]), data)

        # update the state contact field to something invalid
        self.client.post(
            reverse("contacts.contact_update_fields", args=[self.joe.id]),
            dict(contact_field=contact_field.id, field_value="newyork"),
        )

        # check that old URN is detached, new URN is attached, and Joe still exists
        self.joe = Contact.objects.get(pk=self.joe.id)
        self.assertEqual(self.joe.get_urn_display(scheme=URN.TEL_SCHEME), "0783 835 665")
        self.assertIsNone(
            self.joe.get_field_serialized(ContactField.get_by_key(self.org, "state"))
        )  # raw user input as location wasn't matched
        self.assertIsNone(Contact.from_urn(self.org, "tel:+250781111111"))  # original tel is nobody now

        # update joe, change his number back
        data = dict(name="Joe Blow", urn__tel__0="+250781111111", order__urn__tel__0="0", __field__location="Kigali")
        self.client.post(reverse("contacts.contact_update", args=[self.joe.id]), data)

        # check that old URN is re-attached
        self.assertIsNone(ContactURN.objects.get(identity="tel:+250783835665").contact)
        self.assertEqual(self.joe, ContactURN.objects.get(identity="tel:+250781111111").contact)

        # add another URN to joe
        ContactURN.create(self.org, self.joe, "tel:+250786666666")

        # assert that our update form has the extra URN
        response = self.client.get(reverse("contacts.contact_update", args=[self.joe.id]))
        self.assertEqual(response.context["form"].fields["urn__tel__0"].initial, "+250781111111")
        self.assertEqual(response.context["form"].fields["urn__tel__1"].initial, "+250786666666")

        # try to add joe to a group in another org
        other_org_group = self.create_group("Nerds", org=self.org2)
        response = self.client.post(
            reverse("contacts.contact_update", args=[self.joe.id]),
            {
                "name": "Joe Gashyantare",
                "groups": [other_org_group.id],
                "urn__tel__0": "+250781111111",
                "urn__tel__1": "+250786666666",
            },
        )
        self.assertFormError(
            response,
            "form",
            "groups",
            f"Select a valid choice. {other_org_group.id} is not one of the available choices.",
        )

        # update joe, add him to "Just Joe" group
        post_data = dict(
            name="Joe Gashyantare", groups=[self.just_joe.id], urn__tel__0="+250781111111", urn__tel__1="+250786666666"
        )

        self.client.post(reverse("contacts.contact_update", args=[self.joe.id]), post_data, follow=True)

        self.assertEqual(set(self.joe.user_groups.all()), {self.just_joe})
        self.assertTrue(ContactURN.objects.filter(contact=self.joe, path="+250781111111"))
        self.assertTrue(ContactURN.objects.filter(contact=self.joe, path="+250786666666"))

        # remove him from this group "Just joe", and his second number
        post_data = dict(name="Joe Gashyantare", urn__tel__0="+250781111111", groups=[])

        self.client.post(reverse("contacts.contact_update", args=[self.joe.id]), post_data, follow=True)

        self.assertEqual(set(self.joe.user_groups.all()), set())
        self.assertTrue(ContactURN.objects.filter(contact=self.joe, path="+250781111111"))
        self.assertFalse(ContactURN.objects.filter(contact=self.joe, path="+250786666666"))

        # should no longer be in our update form either
        response = self.client.get(reverse("contacts.contact_update", args=[self.joe.id]))
        self.assertEqual(response.context["form"].fields["urn__tel__0"].initial, "+250781111111")
        self.assertNotIn("urn__tel__1", response.context["form"].fields)

        # check that groups field isn't displayed when contact is blocked
        self.joe.block(self.user)
        response = self.client.get(reverse("contacts.contact_update", args=[self.joe.id]))
        self.assertNotIn("groups", response.context["form"].fields)

        # and that we can still update the contact
        post_data = dict(name="Joe Bloggs", urn__tel__0="+250781111111")
        self.client.post(reverse("contacts.contact_update", args=[self.joe.id]), post_data, follow=True)

        self.joe = Contact.objects.get(pk=self.joe.pk)
        self.joe.restore(self.user)

        # add new urn for joe
        self.client.post(
            reverse("contacts.contact_update", args=[self.joe.id]),
            dict(name="Joey", urn__tel__0="+250781111111", new_scheme="ext", new_path="EXT123"),
        )

        urn = ContactURN.objects.filter(contact=self.joe, scheme="ext").first()
        self.assertIsNotNone(urn)
        self.assertEqual("EXT123", urn.path)

        # now try adding one that is invalid
        self.client.post(
            reverse("contacts.contact_update", args=[self.joe.id]),
            dict(name="Joey", urn__tel__0="+250781111111", new_scheme="mailto", new_path="malformed"),
        )
        self.assertIsNone(ContactURN.objects.filter(contact=self.joe, scheme="mailto").first())

        # update our language to something not on the org
        self.joe.refresh_from_db()
        self.joe.language = "fra"
        self.joe.save(update_fields=("language",))

        # add some languages to our org, but not french
        self.client.post(
            reverse("orgs.org_languages"),
            dict(
                primary_lang='{"name":"Haitian", "value":"hat"}',
                languages=['{"name":"Kinyarwanda", "value":"kin"}', '{"name":"Spanish", "value":"spa"}'],
            ),
        )

        response = self.client.get(reverse("contacts.contact_update", args=[self.joe.id]))
        self.assertContains(response, "French (Missing)")

        # update our contact with some locations
        state = ContactField.get_or_create(self.org, self.admin, "state", "Home State", value_type="S")
        district = ContactField.get_or_create(self.org, self.admin, "home", "Home District", value_type="I")

        self.client.post(
            reverse("contacts.contact_update_fields", args=[self.joe.id]),
            dict(contact_field=state.id, field_value="eastern province"),
        )
        self.client.post(
            reverse("contacts.contact_update_fields", args=[self.joe.id]),
            dict(contact_field=district.id, field_value="rwamagana"),
        )

        response = self.client.get(reverse("contacts.contact_read", args=[self.joe.uuid]))

        self.assertContains(response, "Eastern Province")
        self.assertContains(response, "Rwamagana")

        # change the name of the Rwamagana boundary, our display should change appropriately as well
        rwamagana = AdminBoundary.objects.get(name="Rwamagana")
        rwamagana.update(name="Rwa-magana")
        self.assertEqual("Rwa-magana", rwamagana.name)

        # assert our read page is correct
        response = self.client.get(reverse("contacts.contact_read", args=[self.joe.uuid]))
        self.assertContains(response, "Eastern Province")
        self.assertContains(response, "Rwa-magana")

        # change our field to a text field
        state.value_type = ContactField.TYPE_TEXT
        state.save()
        self.set_contact_field(self.joe, "state", "Rwama Value")

        # should now be using stored string_value instead of state name
        response = self.client.get(reverse("contacts.contact_read", args=[self.joe.uuid]))
        self.assertContains(response, "Rwama Value")

        # bad field
        contact_field = ContactField.user_fields.create(
            org=self.org, key="language", label="User Language", created_by=self.admin, modified_by=self.admin
        )

        response = self.client.post(
            reverse("contacts.contact_update_fields", args=[self.joe.id]),
            dict(contact_field=contact_field.id, field_value="Kinyarwanda"),
        )

        self.assertFormError(
            response, "form", None, "Field key language has invalid characters or is a reserved field name"
        )

        # try to push into a dynamic group
        self.login(self.admin)
        group = self.create_group("Dynamo", query="tel = 325423")
        self.client.post(list_url, {"action": "label", "label": group.id, "objects": self.frank.id, "add": True})

        self.assertEqual(0, group.contacts.count())

        # check updating when org is anon
        self.org.is_anon = True
        self.org.save()

        post_data = dict(name="Joe X", groups=[self.just_joe.id])
        self.client.post(reverse("contacts.contact_update", args=[self.joe.id]), post_data, follow=True)

        self.joe.refresh_from_db()
        self.assertEqual({str(u) for u in self.joe.urns.all()}, {"tel:+250781111111", "ext:EXT123"})  # urns unaffected

        # remove all of joe's URNs
        ContactURN.objects.filter(contact=self.joe).update(contact=None)
        response = self.client.get(list_url)

        # no more URN listed
        self.assertNotContains(response, "blow80")

        self.frank.block(self.admin)

        # try archive action
        event = self.create_channel_event(
            self.channel, str(self.frank.get_urn(URN.TEL_SCHEME)), ChannelEvent.TYPE_CALL_OUT_MISSED, extra={}
        )

        self.client.post(blocked_url, {"action": "archive", "objects": self.frank.id})

        archived_url = reverse("contacts.contact_archived")

        response = self.client.get(archived_url)
        self.assertEqual(list(response.context["object_list"]), [self.frank])

        # and from there we can really delete a contact
        self.client.post(archived_url, {"action": "delete", "objects": self.frank.id})

        self.assertFalse(ChannelEvent.objects.filter(contact=self.frank))
        self.assertFalse(ChannelEvent.objects.filter(id=event.id))

    @patch("temba.mailroom.client.MailroomClient.contact_modify")
    def test_update_with_mailroom_error(self, mock_modify):
        mock_modify.side_effect = MailroomException("", "", {"error": "Error updating contact"})

        self.login(self.admin)

        response = self.client.post(
            reverse("contacts.contact_update", args=[self.joe.id]),
            dict(language="fra", name="Muller Awesome", urn__tel__0="+250781111111", urn__twitter__1="blow80"),
        )

        self.assertFormError(
            response, "form", None, "An error occurred updating your contact. Please try again later."
        )

    def test_contact_read_with_contactfields(self):
        self.login(self.admin)

        response = self.client.get(reverse("contacts.contact_read", args=[self.joe.uuid]))

        self.assertEqual(len(response.context_data["all_contact_fields"]), 0)

        # create some contact fields
        ContactField.get_or_create(self.org, self.admin, "first", "First", priority=10)
        ContactField.get_or_create(self.org, self.admin, "second", "Second")
        ContactField.get_or_create(self.org, self.admin, "third", "Third", priority=20)

        # update ContactField data
        self.set_contact_field(self.joe, "first", "a simple value")

        response = self.client.get(reverse("contacts.contact_read", args=[self.joe.uuid]))

        # there should be one 'normal' field
        self.assertEqual(len(response.context_data["all_contact_fields"]), 1)

        # make 'third' field a featured field, but don't assign a value (it should still be visible on the page)
        ContactField.get_or_create(self.org, self.admin, "third", "Third", priority=20, show_in_table=True)

        response = self.client.get(reverse("contacts.contact_read", args=[self.joe.uuid]))

        # there should be one 'normal' field and one 'featured' contact field
        self.assertEqual(len(response.context_data["all_contact_fields"]), 2)

        # assign a value to the 'third' field
        self.set_contact_field(self.joe, "third", "a simple value")

        response = self.client.get(reverse("contacts.contact_read", args=[self.joe.uuid]))

        # there should be one 'normal' field and one 'featured' contact field
        self.assertEqual(len(response.context_data["all_contact_fields"]), 2)

    def test_update(self):
        # if new values don't differ from current values.. no modifications
        self.assertEqual([], self.joe.update(name="Joe Blow", language=""))

        # change language
        self.assertEqual([modifiers.Language(language="eng")], self.joe.update(name="Joe Blow", language="eng"))

        self.joe.language = "eng"
        self.joe.save(update_fields=("language",))

        # change name
        self.assertEqual([modifiers.Name(name="Joseph Blow")], self.joe.update(name="Joseph Blow", language="eng"))

        # change both name and language
        self.assertEqual(
            [modifiers.Name(name="Joseph Blower"), modifiers.Language(language="spa")],
            self.joe.update(name="Joseph Blower", language="spa"),
        )

    @mock_mailroom
    def test_update_static_groups(self, mr_mocks):
        # create some static groups
        spammers = self.create_group("Spammers", [])
        testers = self.create_group("Testers", [])
        customers = self.create_group("Customers", [])

        self.assertEqual(set(spammers.contacts.all()), set())
        self.assertEqual(set(testers.contacts.all()), set())
        self.assertEqual(set(customers.contacts.all()), set())

        # add to 2 static groups
        mods = self.joe.update_static_groups([spammers, testers])
        self.assertEqual(
            [
                modifiers.Groups(
                    modification="add",
                    groups=[
                        modifiers.GroupRef(uuid=spammers.uuid, name="Spammers"),
                        modifiers.GroupRef(uuid=testers.uuid, name="Testers"),
                    ],
                ),
            ],
            mods,
        )

        self.joe.modify(self.admin, mods)

        # remove from one and add to another
        mods = self.joe.update_static_groups([testers, customers])

        self.assertEqual(
            [
                modifiers.Groups(
                    modification="remove", groups=[modifiers.GroupRef(uuid=spammers.uuid, name="Spammers")]
                ),
                modifiers.Groups(
                    modification="add", groups=[modifiers.GroupRef(uuid=customers.uuid, name="Customers")]
                ),
            ],
            mods,
        )

    @patch("temba.mailroom.client.MailroomClient.contact_modify")
    def test_bulk_modify_with_no_contacts(self, mock_contact_modify):
        mock_contact_modify.return_value = {}

        # just a NOOP
        Contact.bulk_modify(self.admin, [], [modifiers.Language(language="spa")])

    @mock_mailroom
    def test_contact_model(self, mr_mocks):
        contact = self.create_contact(name="Boy", phone="12345")
        self.assertEqual(contact.get_display(), "Boy")

        contact3 = self.create_contact(name=None, phone="0788111222")
        self.channel.country = "RW"
        self.channel.save()

        normalized = contact3.get_urn(URN.TEL_SCHEME).ensure_number_normalization(self.channel)
        self.assertEqual(normalized.path, "+250788111222")

        contact4 = self.create_contact(name=None, phone="0788333444")
        normalized = contact4.get_urn(URN.TEL_SCHEME).ensure_number_normalization(self.channel)
        self.assertEqual(normalized.path, "+250788333444")

        contact5 = self.create_contact(name="Jimmy", phone="+250788333555")
        mods = contact5.update_urns(["twitter:jimmy_woot", "tel:0788333666"])
        contact5.modify(self.user, mods)

        # check old phone URN still existing but was detached
        self.assertIsNone(ContactURN.objects.get(identity="tel:+250788333555").contact)

        # check new URNs were created and attached
        self.assertEqual(contact5, ContactURN.objects.get(identity="tel:+250788333666").contact)
        self.assertEqual(contact5, ContactURN.objects.get(identity="twitter:jimmy_woot").contact)

        # check twitter URN takes priority if you don't specify scheme
        self.assertEqual("twitter:jimmy_woot", str(contact5.get_urn()))
        self.assertEqual("twitter:jimmy_woot", str(contact5.get_urn(schemes=[URN.TWITTER_SCHEME])))
        self.assertEqual("tel:+250788333666", str(contact5.get_urn(schemes=[URN.TEL_SCHEME])))
        self.assertIsNone(contact5.get_urn(schemes=["email"]))
        self.assertIsNone(contact5.get_urn(schemes=["facebook"]))

    def test_from_urn(self):
        self.assertEqual(Contact.from_urn(self.org, "tel:+250781111111"), self.joe)  # URN with contact
        self.assertIsNone(Contact.from_urn(self.org, "tel:+250788888888"))  # URN with no contact
        self.assertIsNone(Contact.from_urn(self.org, "snoop@dogg.com"))  # URN with no scheme

    def test_field_json(self):
        self.setUpLocations()

        # simple text field
        self.set_contact_field(self.joe, "dog", "Chef")
        self.joe.refresh_from_db()
        dog_uuid = str(ContactField.user_fields.get(key="dog").uuid)

        self.assertEqual(self.joe.fields, {dog_uuid: {"text": "Chef"}})

        self.set_contact_field(self.joe, "dog", "")
        self.joe.refresh_from_db()
        self.assertEqual(self.joe.fields, {})

        # numeric field value
        self.set_contact_field(self.joe, "dog", "23.00")
        self.joe.refresh_from_db()
        self.assertEqual(self.joe.fields, {dog_uuid: {"text": "23.00", "number": "23"}})

        # numeric field value
        self.set_contact_field(self.joe, "dog", "37.27903")
        self.joe.refresh_from_db()
        self.assertEqual(self.joe.fields, {dog_uuid: {"text": "37.27903", "number": "37.27903"}})

        # numeric field values that could turn into shite due to normalization
        self.set_contact_field(self.joe, "dog", "2300")
        self.joe.refresh_from_db()
        self.assertEqual(self.joe.fields, {dog_uuid: {"text": "2300", "number": "2300"}})

        # numeric field values that could be NaN, we don't support that
        self.set_contact_field(self.joe, "dog", "NaN")
        self.joe.refresh_from_db()
        self.assertEqual(self.joe.fields, {dog_uuid: {"text": "NaN"}})

        # datetime instead
        self.set_contact_field(self.joe, "dog", "2018-03-05T02:31:00.000Z")
        self.joe.refresh_from_db()
        self.assertEqual(
            self.joe.fields, {dog_uuid: {"text": "2018-03-05T02:31:00.000Z", "datetime": "2018-03-05T04:31:00+02:00"}}
        )

        # setting another field doesn't ruin anything
        self.set_contact_field(self.joe, "cat", "Rando")
        self.joe.refresh_from_db()
        cat_uuid = str(ContactField.user_fields.get(key="cat").uuid)
        self.assertEqual(
            self.joe.fields,
            {
                dog_uuid: {"text": "2018-03-05T02:31:00.000Z", "datetime": "2018-03-05T04:31:00+02:00"},
                cat_uuid: {"text": "Rando"},
            },
        )

        # setting a fully qualified path parses to that level, regardless of field type
        self.set_contact_field(self.joe, "cat", "Rwanda > Kigali City")
        self.joe.refresh_from_db()
        self.assertEqual(
            self.joe.fields,
            {
                dog_uuid: {"text": "2018-03-05T02:31:00.000Z", "datetime": "2018-03-05T04:31:00+02:00"},
                cat_uuid: {"text": "Rwanda > Kigali City", "state": "Rwanda > Kigali City"},
            },
        )

        # clear our previous fields
        self.set_contact_field(self.joe, "dog", "")
        self.assertEqual(
            self.joe.fields, {cat_uuid: {"text": "Rwanda > Kigali City", "state": "Rwanda > Kigali City"}}
        )
        self.joe.refresh_from_db()

        self.set_contact_field(self.joe, "cat", "")
        self.joe.refresh_from_db()

        # change a field to an invalid field value type
        self.set_contact_field(self.joe, "cat", "xx")
        ContactField.user_fields.filter(key="cat").update(value_type="Z")
        bad_field = ContactField.user_fields.get(key="cat")

        with self.assertRaises(KeyError):
            self.joe.get_field_serialized(bad_field)

        with self.assertRaises(KeyError):
            self.joe.get_field_value(bad_field)

    def test_date_field(self):
        # create a new date field
        birth_date = ContactField.get_or_create(
            self.org, self.admin, "birth_date", label="Birth Date", value_type=ContactField.TYPE_TEXT
        )

        # set a field on our contact
        urn = "urn:uuid:0f73262c-0623-3f0a-8651-1855e755d2ef"
        self.set_contact_field(self.joe, "birth_date", urn)

        # check that this field has been set
        self.assertEqual(self.joe.get_field_value(birth_date), urn)
        self.assertIsNone(self.joe.get_field_json(birth_date).get("number"))
        self.assertIsNone(self.joe.get_field_json(birth_date).get("datetime"))

    def test_field_values(self):
        self.setUpLocations()

        registration_field = ContactField.get_or_create(
            self.org, self.admin, "registration_date", "Registration Date", None, ContactField.TYPE_DATETIME
        )

        weight_field = ContactField.get_or_create(
            self.org, self.admin, "weight", "Weight", None, ContactField.TYPE_NUMBER
        )
        color_field = ContactField.get_or_create(self.org, self.admin, "color", "Color", None, ContactField.TYPE_TEXT)
        state_field = ContactField.get_or_create(self.org, self.admin, "state", "State", None, ContactField.TYPE_STATE)

        joe = Contact.objects.get(pk=self.joe.pk)
        joe.language = "eng"
        joe.save(update_fields=("language",))

        # none value instances
        self.assertEqual(joe.get_field_serialized(weight_field), None)
        self.assertEqual(joe.get_field_display(weight_field), "")
        self.assertEqual(joe.get_field_serialized(registration_field), None)
        self.assertEqual(joe.get_field_display(registration_field), "")

        self.set_contact_field(joe, "registration_date", "2014-12-31T01:04:00Z")
        self.set_contact_field(joe, "weight", "75.888888")
        self.set_contact_field(joe, "color", "green")
        self.set_contact_field(joe, "state", "kigali city")

        self.assertEqual(joe.get_field_serialized(registration_field), "2014-12-31T03:04:00+02:00")

        self.assertEqual(joe.get_field_serialized(weight_field), "75.888888")
        self.assertEqual(joe.get_field_display(weight_field), "75.888888")

        self.set_contact_field(joe, "weight", "0")
        self.assertEqual(joe.get_field_serialized(weight_field), "0")
        self.assertEqual(joe.get_field_display(weight_field), "0")

        # passing something non-numeric to a decimal field
        self.set_contact_field(joe, "weight", "xxx")
        self.assertEqual(joe.get_field_serialized(weight_field), None)
        self.assertEqual(joe.get_field_display(weight_field), "")

        self.assertEqual(joe.get_field_serialized(state_field), "Rwanda > Kigali City")
        self.assertEqual(joe.get_field_display(state_field), "Kigali City")

        self.assertEqual(joe.get_field_serialized(color_field), "green")
        self.assertEqual(joe.get_field_display(color_field), "green")

        field_created_on = self.org.contactfields.get(key="created_on")
        field_language = self.org.contactfields.get(key="language")
        field_name = self.org.contactfields.get(key="name")

        self.assertEqual(joe.get_field_display(field_created_on), self.org.format_datetime(joe.created_on))
        self.assertEqual(joe.get_field_display(field_language), "eng")
        self.assertEqual(joe.get_field_display(field_name), "Joe Blow")

        # create a system field that is not supported
        field_iban = ContactField.system_fields.create(
            org_id=self.org.id, key="iban", label="IBAN", created_by_id=self.admin.id, modified_by_id=self.admin.id
        )

        self.assertRaises(AssertionError, joe.get_field_serialized, field_iban)
        self.assertRaises(ValueError, joe.get_field_display, field_iban)

    def test_set_location_fields(self):
        self.setUpLocations()

        district_field = ContactField.get_or_create(
            self.org, self.admin, "district", "District", None, ContactField.TYPE_DISTRICT
        )
        not_state_field = ContactField.get_or_create(
            self.org, self.admin, "not_state", "Not State", None, ContactField.TYPE_TEXT
        )

        # add duplicate district in different states
        east_province = AdminBoundary.create(osm_id="R005", name="East Province", level=1, parent=self.country)
        AdminBoundary.create(osm_id="R004", name="Remera", level=2, parent=east_province)
        kigali = AdminBoundary.objects.get(name="Kigali City")
        AdminBoundary.create(osm_id="R003", name="Remera", level=2, parent=kigali)

        joe = Contact.objects.get(pk=self.joe.pk)
        self.set_contact_field(joe, "district", "Remera")

        # empty because it is ambiguous
        self.assertFalse(joe.get_field_value(district_field))

        state_field = ContactField.get_or_create(self.org, self.admin, "state", "State", None, ContactField.TYPE_STATE)

        self.set_contact_field(joe, "state", "Kigali city")
        self.assertEqual("Kigali City", joe.get_field_display(state_field))
        self.assertEqual("Rwanda > Kigali City", joe.get_field_serialized(state_field))

        # test that we don't normalize non-location fields
        self.set_contact_field(joe, "not_state", "kigali city")
        self.assertEqual("kigali city", joe.get_field_display(not_state_field))
        self.assertEqual("kigali city", joe.get_field_serialized(not_state_field))

        self.set_contact_field(joe, "district", "Remera")
        self.assertEqual("Remera", joe.get_field_display(district_field))
        self.assertEqual("Rwanda > Kigali City > Remera", joe.get_field_serialized(district_field))

    def test_set_location_ward_fields(self):
        self.setUpLocations()

        state = AdminBoundary.create(osm_id="3710302", name="Kano", level=1, parent=self.country)
        district = AdminBoundary.create(osm_id="3710307", name="Bichi", level=2, parent=state)
        ward = AdminBoundary.create(osm_id="3710377", name="Bichi", level=3, parent=district)
        user1 = self.create_user("mcren")

        ContactField.get_or_create(self.org, user1, "state", "State", None, ContactField.TYPE_STATE)
        ContactField.get_or_create(self.org, user1, "district", "District", None, ContactField.TYPE_DISTRICT)
        ward = ContactField.get_or_create(self.org, user1, "ward", "Ward", None, ContactField.TYPE_WARD)

        jemila = self.create_contact(
            name="Jemila Alley",
            urns=["tel:123", "twitter:fulani_p"],
            fields={"state": "kano", "district": "bichi", "ward": "bichi"},
        )
        self.assertEqual(jemila.get_field_serialized(ward), "Rwanda > Kano > Bichi > Bichi")


class ContactURNTest(TembaTest):
    def setUp(self):
        super().setUp()

    def test_get_or_create(self):
        urn = ContactURN.create(self.org, None, "tel:1234")
        self.assertEqual(urn.org, self.org)
        self.assertEqual(urn.contact, None)
        self.assertEqual(urn.identity, "tel:1234")
        self.assertEqual(urn.scheme, "tel")
        self.assertEqual(urn.path, "1234")
        self.assertEqual(urn.priority, 1000)

        urn = ContactURN.get_or_create(self.org, None, "twitterid:12345#fooman")
        self.assertEqual("twitterid:12345", urn.identity)
        self.assertEqual("fooman", urn.display)

        urn2 = ContactURN.get_or_create(self.org, None, "twitter:fooman")
        self.assertEqual(urn, urn2)

        with patch("temba.contacts.models.ContactURN.lookup") as mock_lookup:
            mock_lookup.side_effect = [None, urn]
            ContactURN.get_or_create(self.org, None, "twitterid:12345#fooman")

    def test_get_display(self):
        urn = ContactURN.objects.create(
            org=self.org, scheme="tel", path="+250788383383", identity="tel:+250788383383", priority=50
        )
        self.assertEqual(urn.get_display(self.org), "0788 383 383")
        self.assertEqual(urn.get_display(self.org, formatted=False), "+250788383383")
        self.assertEqual(urn.get_display(self.org, international=True), "+250 788 383 383")
        self.assertEqual(urn.get_display(self.org, formatted=False, international=True), "+250788383383")

        # friendly tel formatting for whatsapp too
        urn = ContactURN.objects.create(
            org=self.org, scheme="whatsapp", path="12065551212", identity="whatsapp:12065551212", priority=50
        )
        self.assertEqual(urn.get_display(self.org), "(206) 555-1212")

        # use path for other schemes
        urn = ContactURN.objects.create(
            org=self.org, scheme="twitter", path="billy_bob", identity="twitter:billy_bob", priority=50
        )
        self.assertEqual(urn.get_display(self.org), "billy_bob")

        # unless there's a display property
        urn = ContactURN.objects.create(
            org=self.org,
            scheme="twitter",
            path="jimmy_john",
            identity="twitter:jimmy_john",
            priority=50,
            display="JIM",
        )
        self.assertEqual(urn.get_display(self.org), "JIM")

    def test_empty_scheme_disallowed(self):
        with self.assertRaises(IntegrityError):
            ContactURN.objects.create(org=self.org, scheme="", path="1234", identity=":1234")

    def test_empty_path_disallowed(self):
        with self.assertRaises(IntegrityError):
            ContactURN.objects.create(org=self.org, scheme="ext", path="", identity="ext:")

    def test_identity_mismatch_disallowed(self):
        with self.assertRaises(IntegrityError):
            ContactURN.objects.create(org=self.org, scheme="ext", path="1234", identity="ext:5678")

    def test_api_urn(self):
        urn = ContactURN.objects.create(
            org=self.org, scheme="tel", path="+250788383383", identity="tel:+250788383383", priority=50
        )
        self.assertEqual(urn.api_urn(), "tel:+250788383383")

        with AnonymousOrg(self.org):
            self.assertEqual(urn.api_urn(), "tel:********")


class ContactFieldTest(TembaTest):
    def setUp(self):
        super().setUp()

        self.joe = self.create_contact(name="Joe Blow", phone="123")
        self.frank = self.create_contact(name="Frank Smith", phone="1234")

        self.contactfield_1 = ContactField.get_or_create(self.org, self.admin, "first", "First", priority=10)
        self.contactfield_2 = ContactField.get_or_create(self.org, self.admin, "second", "Second")
        self.contactfield_3 = ContactField.get_or_create(self.org, self.admin, "third", "Third", priority=20)

        self.other_org_field = ContactField.get_or_create(self.org2, self.admin, "other", "Other", priority=10)

    def test_get_or_create(self):
        join_date = ContactField.get_or_create(self.org, self.admin, "join_date")
        self.assertEqual(join_date.key, "join_date")
        self.assertEqual(join_date.label, "Join Date")
        self.assertEqual(join_date.value_type, ContactField.TYPE_TEXT)

        another = ContactField.get_or_create(
            self.org, self.admin, "another", "My Label", value_type=ContactField.TYPE_NUMBER
        )
        self.assertEqual(another.key, "another")
        self.assertEqual(another.label, "My Label")
        self.assertEqual(another.value_type, ContactField.TYPE_NUMBER)

        another = ContactField.get_or_create(
            self.org, self.admin, "another", "Updated Label", value_type=ContactField.TYPE_DATETIME
        )
        self.assertEqual(another.key, "another")
        self.assertEqual(another.label, "Updated Label")
        self.assertEqual(another.value_type, ContactField.TYPE_DATETIME)

        another = ContactField.get_or_create(
            self.org, self.admin, "another", "Updated Label", show_in_table=True, value_type=ContactField.TYPE_DATETIME
        )
        self.assertTrue(another.show_in_table)

        for key in Contact.RESERVED_FIELD_KEYS:
            with self.assertRaises(ValueError):
                ContactField.get_or_create(self.org, self.admin, key, key, value_type=ContactField.TYPE_TEXT)

        groups_field = ContactField.get_or_create(self.org, self.admin, "groups_field", "Groups Field")
        self.assertEqual(groups_field.key, "groups_field")
        self.assertEqual(groups_field.label, "Groups Field")

        groups_field.label = "Groups"
        groups_field.save()

        groups_field.refresh_from_db()

        self.assertEqual(groups_field.key, "groups_field")
        self.assertEqual(groups_field.label, "Groups")

        # we should lookup the existing field by label
        label_field = ContactField.get_or_create(self.org, self.admin, key=None, label="Groups")

        self.assertEqual(label_field.key, "groups_field")
        self.assertEqual(label_field.label, "Groups")
        self.assertFalse(ContactField.user_fields.filter(key="groups"))
        self.assertEqual(label_field.pk, groups_field.pk)

        # existing field by label has invalid key we should try to create a new field
        groups_field.key = "groups"
        groups_field.save()

        groups_field.refresh_from_db()

        # we throw since the key is a reserved word
        with self.assertRaises(ValueError):
            ContactField.get_or_create(self.org, self.admin, "name", "Groups")

        # don't look up by label if we have a key
        created_field = ContactField.get_or_create(self.org, self.admin, "list", "Groups")
        self.assertEqual(created_field.key, "list")
        self.assertEqual(created_field.label, "Groups 2")

        # this should be a different field
        self.assertFalse(created_field.pk == groups_field.pk)

        # check it is not possible to create two field with the same label
        self.assertFalse(ContactField.user_fields.filter(key="sport"))
        self.assertFalse(ContactField.user_fields.filter(key="play"))

        field1 = ContactField.get_or_create(self.org, self.admin, "sport", "Games")
        self.assertEqual(field1.key, "sport")
        self.assertEqual(field1.label, "Games")

        # should modify label to make it unique
        field2 = ContactField.get_or_create(self.org, self.admin, "play", "Games")

        self.assertEqual(field2.key, "play")
        self.assertEqual(field2.label, "Games 2")
        self.assertNotEqual(field1.id, field2.id)

    def test_contact_templatetag(self):
        self.set_contact_field(self.joe, "First", "Starter")
        self.assertEqual(contact_field(self.joe, "First"), "Starter")
        self.assertEqual(contact_field(self.joe, "Not there"), "--")

    def test_make_key(self):
        self.assertEqual("first_name", ContactField.make_key("First Name"))
        self.assertEqual("second_name", ContactField.make_key("Second   Name  "))
        self.assertEqual("caf", ContactField.make_key("café"))
        self.assertEqual(
            "323_ffsn_slfs_ksflskfs_fk_anfaddgas",
            ContactField.make_key("  ^%$# %$$ $##323 ffsn slfs ksflskfs!!!! fk$%%%$$$anfaDDGAS ))))))))) "),
        )

    def test_is_valid_key(self):
        self.assertTrue(ContactField.is_valid_key("age"))
        self.assertTrue(ContactField.is_valid_key("age_now_2"))
        self.assertTrue(ContactField.is_valid_key("email"))
        self.assertFalse(ContactField.is_valid_key("Age"))  # must be lowercase
        self.assertFalse(ContactField.is_valid_key("age!"))  # can't have punctuation
        self.assertFalse(ContactField.is_valid_key("âge"))  # a-z only
        self.assertFalse(ContactField.is_valid_key("2up"))  # can't start with a number
        self.assertFalse(ContactField.is_valid_key("name"))  # can't be a contact attribute
        self.assertFalse(ContactField.is_valid_key("uuid"))
        self.assertFalse(ContactField.is_valid_key("tel"))  # can't be URN scheme
        self.assertFalse(ContactField.is_valid_key("mailto"))
        self.assertFalse(ContactField.is_valid_key("a" * 37))  # too long

    def test_is_valid_label(self):
        self.assertTrue(ContactField.is_valid_label("Age"))
        self.assertTrue(ContactField.is_valid_label("Age Now 2"))
        self.assertFalse(ContactField.is_valid_label("Age_Now"))  # can't have punctuation
        self.assertFalse(ContactField.is_valid_label("âge"))  # a-z only

    @mock_mailroom
    def test_contact_export(self, mr_mocks):
        export_url = reverse("contacts.contact_export")

        self.clear_storage()
        self.login(self.admin)

        # archive all our current contacts
        Contact.apply_action_block(self.admin, self.org.contacts.all())

        # make third a datetime
        self.contactfield_3.value_type = ContactField.TYPE_DATETIME
        self.contactfield_3.save()

        # start one of our contacts down it
        contact = self.create_contact(
            "Be\02n Haggerty",
            phone="+12067799294",
            fields={"First": "On\02e", "Third": "20/12/2015 08:30"},
            last_seen_on=datetime(2020, 1, 1, 12, 0, 0, 0, tzinfo=pytz.UTC),
        )

        flow = self.get_flow("color_v13")
        nodes = flow.get_definition()["nodes"]
        color_prompt = nodes[0]
        color_split = nodes[4]

        (
            MockSessionWriter(self.joe, flow)
            .visit(color_prompt)
            .send_msg("What is your favorite color?", self.channel)
            .visit(color_split)
            .wait()
            .save()
        )

        # create another contact, this should sort before Ben
        contact2 = self.create_contact("Adam Sumner", urns=["tel:+12067799191", "twitter:adam"], language="eng")
        urns = [str(urn) for urn in contact2.get_urns()]
        urns.append("mailto:adam@sumner.com")
        urns.append("telegram:1234")
        contact2.modify(self.admin, contact2.update_urns(urns))

        group = self.create_group("Poppin Tags", [contact, contact2])
        group2 = self.create_group("Dynamic", query="tel is 1234")
        group2.status = ContactGroup.STATUS_EVALUATING
        group2.save()

        # create a dummy export task so that we won't be able to export
        blocking_export = ExportContactsTask.create(self.org, self.admin)

        response = self.client.post(export_url, {}, follow=True)
        self.assertContains(response, "already an export in progress")

        # ok, mark that one as finished and try again
        blocking_export.update_status(ExportContactsTask.STATUS_COMPLETE)

        # make sure we can't redirect to places we shouldn't
        with self.mockReadOnly():
            response = self.client.post(export_url + "?redirect=http://foo.me/", {"group_memberships": (group.id,)})
            self.assertEqual(302, response.status_code)
            self.assertEqual("/contact/", response.url)

        # create orphaned URN in scheme that no contacts have a URN for
        ContactURN.create(self.org, None, "line:12345")

        def request_export(query=""):
            with self.mockReadOnly(assert_models={Contact, ContactURN, ContactField}):
                self.client.post(export_url + query, {"group_memberships": (group.id,)})
            task = ExportContactsTask.objects.all().order_by("-id").first()
            filename = "%s/test_orgs/%d/contact_exports/%s.xlsx" % (settings.MEDIA_ROOT, self.org.id, task.uuid)
            workbook = load_workbook(filename=filename)
            return workbook.worksheets

        def assertImportExportedFile(query=""):
            # test an export can be imported back
            with self.mockReadOnly():
                self.client.post(export_url + query, {"group_memberships": (group.id,)})
            task = ExportContactsTask.objects.all().order_by("-id").first()
            path = "%s/test_orgs/%d/contact_exports/%s.xlsx" % (settings.MEDIA_ROOT, self.org.id, task.uuid)
            self.create_contact_import(path)

        # no group specified, so will default to 'Active'
        with self.assertNumQueries(41):
            export = request_export()
            self.assertExcelSheet(
                export[0],
                [
                    [
                        "Contact UUID",
                        "Name",
                        "Language",
                        "Created On",
                        "Last Seen On",
                        "URN:Mailto",
                        "URN:Tel",
                        "URN:Telegram",
                        "URN:Twitter",
                        "Field:Third",
                        "Field:First",
                        "Field:Second",
                    ],
                    [
                        contact2.uuid,
                        "Adam Sumner",
                        "eng",
                        contact2.created_on,
                        "",
                        "adam@sumner.com",
                        "+12067799191",
                        "1234",
                        "adam",
                        "",
                        "",
                        "",
                    ],
                    [
                        contact.uuid,
                        "Ben Haggerty",
                        "",
                        contact.created_on,
                        datetime(2020, 1, 1, 12, 0, 0, 0, tzinfo=pytz.UTC),
                        "",
                        "+12067799294",
                        "",
                        "",
                        "20-12-2015 08:30",
                        "One",
                        "",
                    ],
                ],
                tz=self.org.timezone,
            )

        assertImportExportedFile()

        # check that notifications were created
        export = ExportContactsTask.objects.order_by("id").last()
        self.assertEqual(
            1, self.admin.notifications.filter(notification_type="export:finished", contact_export=export).count()
        )

        # change the order of the fields
        self.contactfield_2.priority = 15
        self.contactfield_2.save()
        with self.assertNumQueries(41):
            export = request_export()
            self.assertExcelSheet(
                export[0],
                [
                    [
                        "Contact UUID",
                        "Name",
                        "Language",
                        "Created On",
                        "Last Seen On",
                        "URN:Mailto",
                        "URN:Tel",
                        "URN:Telegram",
                        "URN:Twitter",
                        "Field:Third",
                        "Field:Second",
                        "Field:First",
                        "Group:Poppin Tags",
                    ],
                    [
                        contact2.uuid,
                        "Adam Sumner",
                        "eng",
                        contact2.created_on,
                        "",
                        "adam@sumner.com",
                        "+12067799191",
                        "1234",
                        "adam",
                        "",
                        "",
                        "",
                        True,
                    ],
                    [
                        contact.uuid,
                        "Ben Haggerty",
                        "",
                        contact.created_on,
                        datetime(2020, 1, 1, 12, 0, 0, 0, tzinfo=pytz.UTC),
                        "",
                        "+12067799294",
                        "",
                        "",
                        "20-12-2015 08:30",
                        "",
                        "One",
                        True,
                    ],
                ],
                tz=self.org.timezone,
            )
        assertImportExportedFile()

        # more contacts do not increase the queries
        contact3 = self.create_contact("Luol Deng", urns=["tel:+12078776655", "twitter:deng"])
        contact4 = self.create_contact("Stephen", urns=["tel:+12078778899", "twitter:stephen"])
        ContactURN.create(self.org, contact, "tel:+12062233445")

        # but should have additional Twitter and phone columns
        with self.assertNumQueries(41):
            export = request_export()
            self.assertExcelSheet(
                export[0],
                [
                    [
                        "Contact UUID",
                        "Name",
                        "Language",
                        "Created On",
                        "Last Seen On",
                        "URN:Mailto",
                        "URN:Tel",
                        "URN:Tel",
                        "URN:Telegram",
                        "URN:Twitter",
                        "Field:Third",
                        "Field:Second",
                        "Field:First",
                    ],
                    [
                        contact2.uuid,
                        "Adam Sumner",
                        "eng",
                        contact2.created_on,
                        "",
                        "adam@sumner.com",
                        "+12067799191",
                        "",
                        "1234",
                        "adam",
                        "",
                        "",
                        "",
                        True,
                    ],
                    [
                        contact.uuid,
                        "Ben Haggerty",
                        "",
                        contact.created_on,
                        datetime(2020, 1, 1, 12, 0, 0, 0, tzinfo=pytz.UTC),
                        "",
                        "+12067799294",
                        "+12062233445",
                        "",
                        "",
                        "20-12-2015 08:30",
                        "",
                        "One",
                        True,
                    ],
                    [
                        contact3.uuid,
                        "Luol Deng",
                        "",
                        contact3.created_on,
                        "",
                        "",
                        "+12078776655",
                        "",
                        "",
                        "deng",
                        "",
                        "",
                        "",
                        False,
                    ],
                    [
                        contact4.uuid,
                        "Stephen",
                        "",
                        contact4.created_on,
                        "",
                        "",
                        "+12078778899",
                        "",
                        "",
                        "stephen",
                        "",
                        "",
                        "",
                        False,
                    ],
                ],
                tz=self.org.timezone,
            )
        assertImportExportedFile()

        # export a specified group of contacts (only Ben and Adam are in the group)
        with self.assertNumQueries(42):
            self.assertExcelSheet(
                request_export("?g=%s" % group.uuid)[0],
                [
                    [
                        "Contact UUID",
                        "Name",
                        "Language",
                        "Created On",
                        "Last Seen On",
                        "URN:Mailto",
                        "URN:Tel",
                        "URN:Tel",
                        "URN:Telegram",
                        "URN:Twitter",
                        "Field:Third",
                        "Field:Second",
                        "Field:First",
                    ],
                    [
                        contact2.uuid,
                        "Adam Sumner",
                        "eng",
                        contact2.created_on,
                        "",
                        "adam@sumner.com",
                        "+12067799191",
                        "",
                        "1234",
                        "adam",
                        "",
                        "",
                        "",
                    ],
                    [
                        contact.uuid,
                        "Ben Haggerty",
                        "",
                        contact.created_on,
                        datetime(2020, 1, 1, 12, 0, 0, 0, tzinfo=pytz.UTC),
                        "",
                        "+12067799294",
                        "+12062233445",
                        "",
                        "",
                        "20-12-2015 08:30",
                        "",
                        "One",
                    ],
                ],
                tz=self.org.timezone,
            )

        assertImportExportedFile("?g=%s" % group.uuid)

        # export a search
        mock_es_data = [
            {"_type": "_doc", "_index": "dummy_index", "_source": {"id": contact2.id}},
            {"_type": "_doc", "_index": "dummy_index", "_source": {"id": contact3.id}},
        ]
        with self.assertLogs("temba.contacts.models", level="INFO") as captured_logger:
            with patch(
                "temba.contacts.models.ExportContactsTask.LOG_PROGRESS_PER_ROWS", new_callable=PropertyMock
            ) as log_info_threshold:
                # make sure that we trigger logger
                log_info_threshold.return_value = 1

                with ESMockWithScroll(data=mock_es_data):
                    with self.assertNumQueries(43):
                        self.assertExcelSheet(
                            request_export("?s=name+has+adam+or+name+has+deng")[0],
                            [
                                [
                                    "Contact UUID",
                                    "Name",
                                    "Language",
                                    "Created On",
                                    "Last Seen On",
                                    "URN:Mailto",
                                    "URN:Tel",
                                    "URN:Tel",
                                    "URN:Telegram",
                                    "URN:Twitter",
                                    "Field:Third",
                                    "Field:Second",
                                    "Field:First",
                                ],
                                [
                                    contact2.uuid,
                                    "Adam Sumner",
                                    "eng",
                                    contact2.created_on,
                                    "",
                                    "adam@sumner.com",
                                    "+12067799191",
                                    "",
                                    "1234",
                                    "adam",
                                    "",
                                    "",
                                    "",
                                ],
                                [
                                    contact3.uuid,
                                    "Luol Deng",
                                    "",
                                    contact3.created_on,
                                    "",
                                    "",
                                    "+12078776655",
                                    "",
                                    "",
                                    "deng",
                                    "",
                                    "",
                                    "",
                                ],
                            ],
                            tz=self.org.timezone,
                        )

                    self.assertEqual(len(captured_logger.output), 2)
                    self.assertTrue("contacts - 50% (1/2)" in captured_logger.output[0])
                    self.assertTrue("contacts - 100% (2/2)" in captured_logger.output[1])

                    assertImportExportedFile("?s=name+has+adam+or+name+has+deng")

        # export a search within a specified group of contacts
        mock_es_data = [{"_type": "_doc", "_index": "dummy_index", "_source": {"id": contact.id}}]
        with ESMockWithScroll(data=mock_es_data):
            with self.assertNumQueries(42):
                self.assertExcelSheet(
                    request_export("?g=%s&s=Hagg" % group.uuid)[0],
                    [
                        [
                            "Contact UUID",
                            "Name",
                            "Language",
                            "Created On",
                            "Last Seen On",
                            "URN:Mailto",
                            "URN:Tel",
                            "URN:Tel",
                            "URN:Telegram",
                            "URN:Twitter",
                            "Field:Third",
                            "Field:Second",
                            "Field:First",
                        ],
                        [
                            contact.uuid,
                            "Ben Haggerty",
                            "",
                            contact.created_on,
                            datetime(2020, 1, 1, 12, 0, 0, 0, tzinfo=pytz.UTC),
                            "",
                            "+12067799294",
                            "+12062233445",
                            "",
                            "",
                            "20-12-2015 08:30",
                            "",
                            "One",
                        ],
                    ],
                    tz=self.org.timezone,
                )

            assertImportExportedFile("?g=%s&s=Hagg" % group.uuid)

        # now try with an anonymous org
        with AnonymousOrg(self.org):
            self.assertExcelSheet(
                request_export()[0],
                [
                    [
                        "ID",
                        "Contact UUID",
                        "Name",
                        "Language",
                        "Created On",
                        "Last Seen On",
                        "Field:Third",
                        "Field:Second",
                        "Field:First",
                    ],
                    [str(contact2.id), contact2.uuid, "Adam Sumner", "eng", contact2.created_on, "", "", "", ""],
                    [
                        str(contact.id),
                        contact.uuid,
                        "Ben Haggerty",
                        "",
                        contact.created_on,
                        datetime(2020, 1, 1, 12, 0, 0, 0, tzinfo=pytz.UTC),
                        "20-12-2015 08:30",
                        "",
                        "One",
                    ],
                    [str(contact3.id), contact3.uuid, "Luol Deng", "", contact3.created_on, "", "", "", ""],
                    [str(contact4.id), contact4.uuid, "Stephen", "", contact4.created_on, "", "", "", ""],
                ],
                tz=self.org.timezone,
            )
            assertImportExportedFile()

    def test_prepare_sort_field_struct(self):
        ward = ContactField.get_or_create(self.org, self.admin, "ward", "Home Ward", value_type=ContactField.TYPE_WARD)
        district = ContactField.get_or_create(
            self.org, self.admin, "district", "Home District", value_type=ContactField.TYPE_DISTRICT
        )
        state = ContactField.get_or_create(
            self.org, self.admin, "state", "Home Stat", value_type=ContactField.TYPE_STATE
        )

        self.assertEqual(
            ContactListView.prepare_sort_field_struct(sort_on="created_on"),
            ("created_on", "asc", {"field_type": "attribute", "sort_direction": "asc", "field_name": "created_on"}),
        )

        self.assertEqual(
            ContactListView.prepare_sort_field_struct(sort_on="-created_on"),
            ("created_on", "desc", {"field_type": "attribute", "sort_direction": "desc", "field_name": "created_on"}),
        )

        self.assertEqual(
            ContactListView.prepare_sort_field_struct(sort_on="last_seen_on"),
            (
                "last_seen_on",
                "asc",
                {"field_type": "attribute", "sort_direction": "asc", "field_name": "last_seen_on"},
            ),
        )

        self.assertEqual(
            ContactListView.prepare_sort_field_struct(sort_on="-last_seen_on"),
            (
                "last_seen_on",
                "desc",
                {"field_type": "attribute", "sort_direction": "desc", "field_name": "last_seen_on"},
            ),
        )

        self.assertEqual(
            ContactListView.prepare_sort_field_struct(sort_on="{}".format(str(self.contactfield_1.uuid))),
            (
                str(self.contactfield_1.uuid),
                "asc",
                {
                    "field_type": "field",
                    "sort_direction": "asc",
                    "field_path": "fields.text",
                    "field_uuid": str(self.contactfield_1.uuid),
                },
            ),
        )

        self.assertEqual(
            ContactListView.prepare_sort_field_struct(sort_on="-{}".format(str(self.contactfield_1.uuid))),
            (
                str(self.contactfield_1.uuid),
                "desc",
                {
                    "field_type": "field",
                    "sort_direction": "desc",
                    "field_path": "fields.text",
                    "field_uuid": str(self.contactfield_1.uuid),
                },
            ),
        )

        self.assertEqual(
            ContactListView.prepare_sort_field_struct(sort_on="-{}".format(str(self.contactfield_1.uuid))),
            (
                str(self.contactfield_1.uuid),
                "desc",
                {
                    "field_type": "field",
                    "sort_direction": "desc",
                    "field_path": "fields.text",
                    "field_uuid": str(self.contactfield_1.uuid),
                },
            ),
        )

        self.assertEqual(
            ContactListView.prepare_sort_field_struct(sort_on="-{}".format(str(ward.uuid))),
            (
                str(ward.uuid),
                "desc",
                {
                    "field_type": "field",
                    "sort_direction": "desc",
                    "field_path": "fields.ward_keyword",
                    "field_uuid": str(ward.uuid),
                },
            ),
        )

        self.assertEqual(
            ContactListView.prepare_sort_field_struct(sort_on="-{}".format(str(district.uuid))),
            (
                str(district.uuid),
                "desc",
                {
                    "field_type": "field",
                    "sort_direction": "desc",
                    "field_path": "fields.district_keyword",
                    "field_uuid": str(district.uuid),
                },
            ),
        )

        self.assertEqual(
            ContactListView.prepare_sort_field_struct(sort_on="{}".format(str(state.uuid))),
            (
                str(state.uuid),
                "asc",
                {
                    "field_type": "field",
                    "sort_direction": "asc",
                    "field_path": "fields.state_keyword",
                    "field_uuid": str(state.uuid),
                },
            ),
        )

        # test with nullish values
        self.assertEqual(ContactListView.prepare_sort_field_struct(sort_on=None), (None, None, None))

        self.assertEqual(ContactListView.prepare_sort_field_struct(sort_on=""), (None, None, None))

        # test with non uuid value
        self.assertEqual(ContactListView.prepare_sort_field_struct(sort_on="abc"), (None, None, None))

        # test with unknown contact field
        self.assertEqual(
            ContactListView.prepare_sort_field_struct(sort_on="22084b5a-3ad3-4dc6-a857-91fb3f20eb57"),
            (None, None, None),
        )

    @mock_mailroom
    def test_contact_field_list_sort_contactfields(self, mr_mocks):
        url = reverse("contacts.contact_list")
        self.login(self.admin)

        mr_mocks.contact_search("", contacts=[self.joe])
        mr_mocks.contact_search("Joe", contacts=[self.joe])

        response = self.client.get("%s?sort_on=%s" % (url, str(self.contactfield_1.key)))

        self.assertEqual(response.context["sort_field"], str(self.contactfield_1.key))
        self.assertEqual(response.context["sort_direction"], "asc")
        self.assertNotIn("search", response.context)

        response = self.client.get("%s?sort_on=-%s" % (url, str(self.contactfield_1.key)))

        self.assertEqual(response.context["sort_field"], str(self.contactfield_1.key))
        self.assertEqual(response.context["sort_direction"], "desc")
        self.assertNotIn("search", response.context)

        response = self.client.get("%s?sort_on=%s" % (url, "created_on"))

        self.assertEqual(response.context["sort_field"], "created_on")
        self.assertEqual(response.context["sort_direction"], "asc")
        self.assertNotIn("search", response.context)

        response = self.client.get("%s?sort_on=-%s&search=Joe" % (url, "created_on"))

        self.assertEqual(response.context["sort_field"], "created_on")
        self.assertEqual(response.context["sort_direction"], "desc")
        self.assertIn("search", response.context)

    def test_list(self):
        manage_fields_url = reverse("contacts.contactfield_list")

        self.login(self.non_org_user)
        response = self.client.get(manage_fields_url)

        # redirect to login because of no access to org
        self.assertEqual(302, response.status_code)

        self.login(self.admin)

        response = self.client.get(manage_fields_url)
        self.assertEqual(len(response.context["object_list"]), 3)

        # deactivate a field
        a_contactfield = ContactField.user_fields.order_by("id").first()
        a_contactfield.is_active = False
        a_contactfield.save(update_fields=("is_active",))

        response = self.client.get(manage_fields_url)
        self.assertEqual(len(response.context["object_list"]), 2)

    def test_view_featured(self):
        featured1 = ContactField.user_fields.get(key="first")
        featured1.show_in_table = True
        featured1.save(update_fields=["show_in_table"])

        featured2 = ContactField.user_fields.get(key="second")
        featured2.show_in_table = True
        featured2.save(update_fields=["show_in_table"])

        self.login(self.admin)

        featured_cf_url = reverse("contacts.contactfield_featured")

        response = self.client.get(featured_cf_url)
        self.assertEqual(response.status_code, 200)

        # there are 2 featured fields
        self.assertEqual(len(response.context_data["object_list"]), 2)

        self.assertEqual(len(response.context_data["cf_categories"]), 2)
        self.assertEqual(len(response.context_data["cf_types"]), 1)

        self.assertTrue(response.context_data["is_featured_category"])

    def test_view_filter_by_type(self):
        self.login(self.admin)

        # an invalid type
        featured_cf_url = reverse("contacts.contactfield_filter_by_type", args=("xXx",))

        response = self.client.get(featured_cf_url)
        self.assertEqual(response.status_code, 200)

        # there are no contact fields
        self.assertEqual(len(response.context_data["object_list"]), 0)

        # a type that is valid
        featured_cf_url = reverse("contacts.contactfield_filter_by_type", args=("T"))

        response = self.client.get(featured_cf_url)
        self.assertEqual(response.status_code, 200)

        # there are some contact fields of type text
        self.assertEqual(len(response.context_data["object_list"]), 3)

        self.assertEqual(response.context_data["selected_value_type"], "T")

    def test_view_updatepriority_valid(self):
        org_fields = ContactField.user_fields.filter(org=self.org, is_active=True)

        self.assertListEqual([10, 0, 20], [cf.priority for cf in org_fields.order_by("id")])

        self.login(self.admin)
        updatepriority_cf_url = reverse("contacts.contactfield_update_priority")

        # there should be no updates because CFs with ids do not exist
        post_data = json.dumps({123_123: 1000, 123_124: 999, 123_125: 998})

        response = self.client.post(updatepriority_cf_url, post_data, content_type="application/json")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["status"], "OK")

        self.assertListEqual([10, 0, 20], [cf.priority for cf in org_fields.order_by("id")])

        # build valid post data
        post_data = json.dumps({cf.id: index for index, cf in enumerate(org_fields.order_by("id"))})

        # try to update as admin2
        self.login(self.admin2)
        response = self.client.post(updatepriority_cf_url, post_data, content_type="application/json")

        # nothing changed
        self.assertListEqual([10, 0, 20], [cf.priority for cf in org_fields.order_by("id")])

        # then as real admin
        self.login(self.admin)
        response = self.client.post(updatepriority_cf_url, post_data, content_type="application/json")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["status"], "OK")

        self.assertListEqual([0, 1, 2], [cf.priority for cf in org_fields.order_by("id")])

    def test_view_updatepriority_invalid(self):
        org_fields = ContactField.user_fields.filter(org=self.org, is_active=True)

        self.assertListEqual([10, 0, 20], [cf.priority for cf in org_fields.order_by("id")])

        self.login(self.admin)
        updatepriority_cf_url = reverse("contacts.contactfield_update_priority")

        post_data = '{invalid_json": 123}'

        response = self.client.post(updatepriority_cf_url, post_data, content_type="application/json")
        self.assertEqual(response.status_code, 400)
        response_json = response.json()
        self.assertEqual(response_json["status"], "ERROR")
        self.assertEqual(
            response_json["err_detail"], "Expecting property name enclosed in double quotes: line 1 column 2 (char 1)"
        )

    def test_contactfield_priority(self):
        fields = ContactField.user_fields.filter(org=self.org).order_by("-priority", "id")

        self.assertEqual(["Third", "First", "Second"], list(fields.values_list("label", flat=True)))

        # change field priority
        ContactField.get_or_create(org=self.org, user=self.user, key="first", priority=25)

        self.assertEqual(["First", "Third", "Second"], list(fields.values_list("label", flat=True)))


class ContactFieldCRUDLTest(TembaTest, CRUDLTestMixin):
    def setUp(self):
        super().setUp()

        self.age = ContactField.get_or_create(self.org, self.admin, "age", "Age", value_type="N", show_in_table=True)
        self.gender = ContactField.get_or_create(self.org, self.admin, "gender", "Gender", value_type="T")
        self.state = ContactField.get_or_create(self.org, self.admin, "state", "State", value_type="S")

        self.deleted = ContactField.get_or_create(self.org, self.admin, "foo", "Foo")
        self.deleted.is_active = False
        self.deleted.save(update_fields=("is_active",))

        self.other_org_field = ContactField.get_or_create(self.org2, self.admin2, "other", "Other")

    def test_menu(self):
        menu_url = reverse("contacts.contactfield_menu")
        response = self.assertListFetch(menu_url, allow_viewers=False, allow_editors=True, allow_agents=False)
        menu = response.json()["results"]
        self.assertEqual(
            [
                {
                    "icon": "bookmark",
                    "id": "featured",
                    "name": "Featured",
                    "count": 1,
                    "href": "/contactfield/featured/",
                },
            ],
            menu,
        )

    def test_create(self):
        create_url = reverse("contacts.contactfield_create")

        self.assertCreateFetch(
            create_url, allow_viewers=False, allow_editors=True, form_fields=["label", "value_type", "show_in_table"]
        )

        # try to submit with empty name
        self.assertCreateSubmit(
            create_url,
            {"label": "", "value_type": "T", "show_in_table": True},
            form_errors={"label": "This field is required."},
        )

        # try to submit with invalid name
        self.assertCreateSubmit(
            create_url,
            {"label": "???", "value_type": "T", "show_in_table": True},
            form_errors={"label": "Can only contain letters, numbers and hypens."},
        )

        # try to submit with something that would be an invalid key
        self.assertCreateSubmit(
            create_url,
            {"label": "UUID", "value_type": "T", "show_in_table": True},
            form_errors={"label": "Can't be a reserved word."},
        )

        # try to submit with name of existing field
        self.assertCreateSubmit(
            create_url,
            {"label": "AGE", "value_type": "N", "show_in_table": True},
            form_errors={"label": "Must be unique."},
        )

        # submit with valid data
        self.assertCreateSubmit(
            create_url,
            {"label": "Goats", "value_type": "N", "show_in_table": True},
            new_obj_query=ContactField.user_fields.filter(
                org=self.org, label="Goats", value_type="N", show_in_table=True
            ),
            success_status=200,
        )

        # it's also ok to create a field with the same name as a deleted field
        ContactField.user_fields.get(key="age").release(self.admin)

        self.assertCreateSubmit(
            create_url,
            {"label": "Age", "value_type": "N", "show_in_table": True},
            new_obj_query=ContactField.user_fields.filter(
                org=self.org, label="Age", value_type="N", show_in_table=True, is_active=True
            ),
            success_status=200,
        )

        # simulate an org which has reached the limit for fields
        with override_settings(ORG_LIMIT_DEFAULTS={"fields": 2}):
            self.assertCreateSubmit(
                create_url,
                {"label": "Sheep", "value_type": "T", "show_in_table": True},
                form_errors={"__all__": "Cannot create a new field as limit is 2."},
            )

    def test_update(self):
        update_url = reverse("contacts.contactfield_update", args=[self.age.id])

        self.assertUpdateFetch(
            update_url,
            allow_viewers=False,
            allow_editors=True,
            form_fields={"label": "Age", "value_type": "N", "show_in_table": True},
        )

        # try submit without change
        self.assertUpdateSubmit(
            update_url, {"label": "Age", "value_type": "N", "show_in_table": True}, success_status=200
        )

        # try to submit with empty name
        self.assertUpdateSubmit(
            update_url,
            {"label": "", "value_type": "N", "show_in_table": True},
            form_errors={"label": "This field is required."},
            object_unchanged=self.age,
        )

        # try to submit with invalid name
        self.assertUpdateSubmit(
            update_url,
            {"label": "???", "value_type": "N", "show_in_table": True},
            form_errors={"label": "Can only contain letters, numbers and hypens."},
            object_unchanged=self.age,
        )

        # try to submit with a name that is used by another field
        self.assertUpdateSubmit(
            update_url,
            {"label": "GENDER", "value_type": "N", "show_in_table": True},
            form_errors={"label": "Must be unique."},
            object_unchanged=self.age,
        )

        # submit with different name and type
        self.assertUpdateSubmit(
            update_url, {"label": "Age In Years", "value_type": "T", "show_in_table": False}, success_status=200
        )

        self.age.refresh_from_db()
        self.assertEqual("Age In Years", self.age.label)
        self.assertEqual("T", self.age.value_type)
        self.assertFalse(self.age.show_in_table)

        # simulate an org which has reached the limit for fields - should still be able to update a field
        with override_settings(ORG_LIMIT_DEFAULTS={"fields": 2}):
            self.assertUpdateSubmit(
                update_url, {"label": "Age 2", "value_type": "T", "show_in_table": True}, success_status=200
            )

        self.age.refresh_from_db()
        self.assertEqual("Age 2", self.age.label)

    def test_list(self):
        list_url = reverse("contacts.contactfield_list")

        response = self.assertListFetch(
            list_url, allow_viewers=False, allow_editors=True, context_objects=[self.age, self.gender, self.state]
        )
        self.assertEqual(3, response.context["total_count"])
        self.assertEqual(250, response.context["total_limit"])
        self.assertNotContains(response, "You have reached the limit")
        self.assertNotContains(response, "You are approaching the limit")

        with override_settings(ORG_LIMIT_DEFAULTS={"fields": 10}):
            response = self.requestView(list_url, self.admin)

            self.assertContains(response, "You are approaching the limit")

        with override_settings(ORG_LIMIT_DEFAULTS={"fields": 3}):
            response = self.requestView(list_url, self.admin)

            self.assertContains(response, "You have reached the limit")

    @mock_mailroom
    def test_usages(self, mr_mocks):
        flow = self.get_flow("dependencies", name="Dependencies")
        field = ContactField.user_fields.filter(is_active=True, org=self.org, key="favorite_cat").get()
        field.value_type = ContactField.TYPE_DATETIME
        field.save(update_fields=("value_type",))

        mr_mocks.parse_query('favorite_cat != ""', fields=[field])

        group = self.create_group("Farmers", query='favorite_cat != ""')
        campaign = Campaign.create(self.org, self.admin, "Planting Reminders", group)

        # create flow events
        event1 = CampaignEvent.create_flow_event(
            self.org,
            self.admin,
            campaign,
            relative_to=field,
            offset=0,
            unit="D",
            flow=flow,
            delivery_hour=17,
        )
        inactive_campaignevent = CampaignEvent.create_flow_event(
            self.org,
            self.admin,
            campaign,
            relative_to=field,
            offset=0,
            unit="D",
            flow=flow,
            delivery_hour=20,
        )
        inactive_campaignevent.is_active = False
        inactive_campaignevent.save(update_fields=("is_active",))

        usages_url = reverse("contacts.contactfield_usages", args=[field.uuid])

        response = self.assertReadFetch(usages_url, allow_viewers=True, allow_editors=True, context_object=field)

        self.assertEqual(
            {"flow": [flow], "group": [group], "campaign_event": [event1]},
            {t: list(qs) for t, qs in response.context["dependents"].items()},
        )

    def test_delete(self):
        # create new field 'Joined On' which is used by a campaign event (soft) and a flow (soft)
        group = self.create_group("Amazing Group", contacts=[])
        joined_on = self.create_field("joined_on", "Joined On", value_type=ContactField.TYPE_DATETIME)
        campaign = Campaign.create(self.org, self.admin, Campaign.get_unique_name(self.org, "Reminders"), group)
        flow = self.create_flow("Amazing Flow")
        flow.field_dependencies.add(joined_on)
        campaign_event = CampaignEvent.create_flow_event(
            self.org, self.admin, campaign, joined_on, offset=1, unit="W", flow=flow, delivery_hour=13
        )

        # make 'Age' appear to be used by a flow (soft) and a group (hard)
        flow.field_dependencies.add(self.age)
        group.query_fields.add(self.age)

        delete_gender_url = reverse("contacts.contactfield_delete", args=[self.gender.uuid])
        delete_joined_url = reverse("contacts.contactfield_delete", args=[joined_on.uuid])
        delete_age_url = reverse("contacts.contactfield_delete", args=[self.age.uuid])

        # a field with no dependents can be deleted
        response = self.assertDeleteFetch(delete_gender_url, allow_editors=True)
        self.assertEqual({}, response.context["soft_dependents"])
        self.assertEqual({}, response.context["hard_dependents"])
        self.assertContains(response, "You are about to delete")
        self.assertContains(response, "There is no way to undo this. Are you sure?")

        self.assertDeleteSubmit(delete_gender_url, object_deactivated=self.gender, success_status=200)

        # a field with only soft dependents can also be deleted but we give warnings
        response = self.assertDeleteFetch(delete_joined_url, allow_editors=True)
        self.assertEqual({"flow", "campaign_event"}, set(response.context["soft_dependents"].keys()))
        self.assertEqual({}, response.context["hard_dependents"])
        self.assertContains(response, "is used by the following items but can still be deleted:")
        self.assertContains(response, "Amazing Flow")
        self.assertContains(response, "There is no way to undo this. Are you sure?")

        self.assertDeleteSubmit(delete_joined_url, object_deactivated=joined_on, success_status=200)

        # check that flow is now marked as having issues
        flow.refresh_from_db()
        self.assertTrue(flow.has_issues)
        self.assertNotIn(joined_on, flow.field_dependencies.all())

        # and that the campaign event is gone
        campaign_event.refresh_from_db()
        self.assertFalse(campaign_event.is_active)

        response = self.assertDeleteFetch(delete_age_url, allow_editors=True)
        self.assertEqual({"flow"}, set(response.context["soft_dependents"].keys()))
        self.assertEqual({"group"}, set(response.context["hard_dependents"].keys()))
        self.assertContains(response, "can't be deleted as it is still used by the following items:")
        self.assertContains(response, "Amazing Group")
        self.assertNotContains(response, "Delete")


class URNTest(TembaTest):
    def test_facebook_urn(self):
        self.assertTrue(URN.validate("facebook:ref:asdf"))

    def test_discord_urn(self):
        self.assertEqual("discord:750841288886321253", URN.from_discord("750841288886321253"))
        self.assertTrue(URN.validate(URN.from_discord("750841288886321253")))
        self.assertFalse(URN.validate(URN.from_discord("not-a-discord-id")))

    def test_whatsapp_urn(self):
        self.assertTrue(URN.validate("whatsapp:12065551212"))
        self.assertFalse(URN.validate("whatsapp:+12065551212"))

    def test_freshchat_urn(self):
        self.assertTrue(
            URN.validate("freshchat:c0534f78-b6e9-4f79-8853-11cedfc1f35b/c0534f78-b6e9-4f79-8853-11cedfc1f35b")
        )
        self.assertFalse(URN.validate("freshchat:+12065551212"))

    def test_from_parts(self):

        self.assertEqual(URN.from_parts("deleted", "12345"), "deleted:12345")
        self.assertEqual(URN.from_parts("tel", "12345"), "tel:12345")
        self.assertEqual(URN.from_parts("tel", "+12345"), "tel:+12345")
        self.assertEqual(URN.from_parts("tel", "(917) 992-5253"), "tel:(917) 992-5253")
        self.assertEqual(URN.from_parts("mailto", "a_b+c@d.com"), "mailto:a_b+c@d.com")
        self.assertEqual(URN.from_parts("twitterid", "2352362611", display="bobby"), "twitterid:2352362611#bobby")
        self.assertEqual(
            URN.from_parts("twitterid", "2352362611", query="foo=ba?r", display="bobby"),
            "twitterid:2352362611?foo=ba%3Fr#bobby",
        )

        self.assertEqual(URN.from_tel("+12345"), "tel:+12345")

        self.assertRaises(ValueError, URN.from_parts, "", "12345")
        self.assertRaises(ValueError, URN.from_parts, "tel", "")
        self.assertRaises(ValueError, URN.from_parts, "xxx", "12345")

    def test_to_parts(self):
        self.assertEqual(URN.to_parts("deleted:12345"), ("deleted", "12345", None, None))
        self.assertEqual(URN.to_parts("tel:12345"), ("tel", "12345", None, None))
        self.assertEqual(URN.to_parts("tel:+12345"), ("tel", "+12345", None, None))
        self.assertEqual(URN.to_parts("twitter:abc_123"), ("twitter", "abc_123", None, None))
        self.assertEqual(URN.to_parts("mailto:a_b+c@d.com"), ("mailto", "a_b+c@d.com", None, None))
        self.assertEqual(URN.to_parts("facebook:12345"), ("facebook", "12345", None, None))
        self.assertEqual(URN.to_parts("vk:12345"), ("vk", "12345", None, None))
        self.assertEqual(URN.to_parts("telegram:12345"), ("telegram", "12345", None, None))
        self.assertEqual(URN.to_parts("telegram:12345#foobar"), ("telegram", "12345", None, "foobar"))
        self.assertEqual(URN.to_parts("ext:Aa0()+,-.:=@;$_!*'"), ("ext", "Aa0()+,-.:=@;$_!*'", None, None))

        self.assertRaises(ValueError, URN.to_parts, "tel")
        self.assertRaises(ValueError, URN.to_parts, "tel:")  # missing scheme
        self.assertRaises(ValueError, URN.to_parts, ":12345")  # missing path
        self.assertRaises(ValueError, URN.to_parts, "x_y:123")  # invalid scheme
        self.assertRaises(ValueError, URN.to_parts, "xyz:{abc}")  # invalid path

    def test_normalize(self):
        # valid tel numbers
        self.assertEqual(URN.normalize("tel:0788383383", "RW"), "tel:+250788383383")
        self.assertEqual(URN.normalize("tel: +250788383383 ", "KE"), "tel:+250788383383")
        self.assertEqual(URN.normalize("tel:+250788383383", None), "tel:+250788383383")
        self.assertEqual(URN.normalize("tel:250788383383", None), "tel:+250788383383")
        self.assertEqual(URN.normalize("tel:2.50788383383E+11", None), "tel:+250788383383")
        self.assertEqual(URN.normalize("tel:2.50788383383E+12", None), "tel:+250788383383")
        self.assertEqual(URN.normalize("tel:(917)992-5253", "US"), "tel:+19179925253")
        self.assertEqual(URN.normalize("tel:19179925253", None), "tel:+19179925253")
        self.assertEqual(URN.normalize("tel:+62877747666", None), "tel:+62877747666")
        self.assertEqual(URN.normalize("tel:62877747666", "ID"), "tel:+62877747666")
        self.assertEqual(URN.normalize("tel:0877747666", "ID"), "tel:+62877747666")
        self.assertEqual(URN.normalize("tel:07531669965", "GB"), "tel:+447531669965")
        self.assertEqual(URN.normalize("tel:22658125926", ""), "tel:+22658125926")
        self.assertEqual(URN.normalize("tel:263780821000", "ZW"), "tel:+263780821000")
        self.assertEqual(URN.normalize("tel:+2203693333", ""), "tel:+2203693333")

        # un-normalizable tel numbers
        self.assertEqual(URN.normalize("tel:12345", "RW"), "tel:12345")
        self.assertEqual(URN.normalize("tel:0788383383", None), "tel:0788383383")
        self.assertEqual(URN.normalize("tel:0788383383", "ZZ"), "tel:0788383383")
        self.assertEqual(URN.normalize("tel:MTN", "RW"), "tel:mtn")

        # twitter handles remove @
        self.assertEqual(URN.normalize("twitter: @jimmyJO"), "twitter:jimmyjo")
        self.assertEqual(URN.normalize("twitterid:12345#@jimmyJO"), "twitterid:12345#jimmyjo")

        # email addresses
        self.assertEqual(URN.normalize("mailto: nAme@domAIN.cOm "), "mailto:name@domain.com")

        # external ids are case sensitive
        self.assertEqual(URN.normalize("ext: eXterNAL123 "), "ext:eXterNAL123")

    def test_validate(self):
        self.assertFalse(URN.validate("xxxx", None))  # un-parseable URNs don't validate

        # valid tel numbers
        self.assertTrue(URN.validate("tel:0788383383", "RW"))
        self.assertTrue(URN.validate("tel:+250788383383", "KE"))
        self.assertTrue(URN.validate("tel:+23761234567", "CM"))  # old Cameroon format
        self.assertTrue(URN.validate("tel:+237661234567", "CM"))  # new Cameroon format
        self.assertTrue(URN.validate("tel:+250788383383", None))

        # invalid tel numbers
        self.assertFalse(URN.validate("tel:0788383383", "ZZ"))  # invalid country
        self.assertFalse(URN.validate("tel:0788383383", None))  # no country
        self.assertFalse(URN.validate("tel:MTN", "RW"))
        self.assertFalse(URN.validate("tel:5912705", "US"))

        # twitter handles
        self.assertTrue(URN.validate("twitter:jimmyjo"))
        self.assertTrue(URN.validate("twitter:billy_bob"))
        self.assertFalse(URN.validate("twitter:jimmyjo!@"))
        self.assertFalse(URN.validate("twitter:billy bob"))

        # twitterid urns
        self.assertTrue(URN.validate("twitterid:12345#jimmyjo"))
        self.assertTrue(URN.validate("twitterid:12345#1234567"))
        self.assertFalse(URN.validate("twitterid:jimmyjo#1234567"))
        self.assertFalse(URN.validate("twitterid:123#a.!f"))

        # email addresses
        self.assertTrue(URN.validate("mailto:abcd+label@x.y.z.com"))
        self.assertFalse(URN.validate("mailto:@@@"))

        # viber urn
        self.assertTrue(URN.validate("viber:dKPvqVrLerGrZw15qTuVBQ=="))

        # facebook, telegram vk URN paths must be integers
        self.assertTrue(URN.validate("telegram:12345678901234567"))
        self.assertFalse(URN.validate("telegram:abcdef"))
        self.assertTrue(URN.validate("facebook:12345678901234567"))
        self.assertFalse(URN.validate("facebook:abcdef"))
        self.assertTrue(URN.validate("vk:12345678901234567"))


class ESIntegrationTest(TembaNonAtomicTest):
    def test_ES_contacts_index(self):
        self.create_anonymous_user()
        self.admin = self.create_user("Administrator")
        self.user = self.admin

        self.country = AdminBoundary.create(osm_id="171496", name="Rwanda", level=0)
        self.state1 = AdminBoundary.create(osm_id="1708283", name="Kigali City", level=1, parent=self.country)
        self.state2 = AdminBoundary.create(osm_id="171591", name="Eastern Province", level=1, parent=self.country)
        self.district1 = AdminBoundary.create(osm_id="1711131", name="Gatsibo", level=2, parent=self.state2)
        self.district2 = AdminBoundary.create(osm_id="1711163", name="Kayônza", level=2, parent=self.state2)
        self.district3 = AdminBoundary.create(osm_id="3963734", name="Nyarugenge", level=2, parent=self.state1)
        self.district4 = AdminBoundary.create(osm_id="1711142", name="Rwamagana", level=2, parent=self.state2)
        self.ward1 = AdminBoundary.create(osm_id="171113181", name="Kageyo", level=3, parent=self.district1)
        self.ward2 = AdminBoundary.create(osm_id="171116381", name="Kabare", level=3, parent=self.district2)
        self.ward3 = AdminBoundary.create(osm_id="171114281", name="Bukure", level=3, parent=self.district4)

        self.org = Org.objects.create(
            name="Temba",
            timezone=pytz.timezone("Africa/Kigali"),
            country=self.country,
            brand=settings.DEFAULT_BRAND,
            created_by=self.admin,
            modified_by=self.admin,
        )

        self.org.initialize(topup_size=1000)
        self.admin.set_org(self.org)
        self.org.administrators.add(self.admin)

        self.client.login(username=self.admin.username, password=self.admin.username)

        age = ContactField.get_or_create(self.org, self.admin, "age", "Age", value_type="N")
        ContactField.get_or_create(self.org, self.admin, "join_date", "Join Date", value_type="D")
        ContactField.get_or_create(self.org, self.admin, "state", "Home State", value_type="S")
        ContactField.get_or_create(self.org, self.admin, "home", "Home District", value_type="I")
        ward = ContactField.get_or_create(self.org, self.admin, "ward", "Home Ward", value_type="W")
        ContactField.get_or_create(self.org, self.admin, "profession", "Profession", value_type="T")
        ContactField.get_or_create(self.org, self.admin, "isureporter", "Is UReporter", value_type="T")
        ContactField.get_or_create(self.org, self.admin, "hasbirth", "Has Birth", value_type="T")

        names = ["Trey", "Mike", "Paige", "Fish", "", None]
        districts = ["Gatsibo", "Kayônza", "Rwamagana", None]
        wards = ["Kageyo", "Kabara", "Bukure", None]
        date_format = self.org.get_datetime_formats()[0]

        # reset contact ids so we don't get unexpected collisions with phone numbers
        with connection.cursor() as cursor:
            cursor.execute("""SELECT setval(pg_get_serial_sequence('"contacts_contact"','id'), 900)""")

        # create some contacts
        for i in range(90):
            name = names[i % len(names)]

            number = "0188382%s" % str(i).zfill(3)
            twitter = ("tweep_%d" % (i + 1)) if (i % 3 == 0) else None  # 1 in 3 have twitter URN
            join_date = datetime_to_str(date(2014, 1, 1) + timezone.timedelta(days=i), date_format, tz=pytz.utc)

            # create contact with some field data so we can do some querying
            fields = {
                "age": str(i + 10),
                "join_date": str(join_date),
                "state": "Eastern Province",
                "home": districts[i % len(districts)],
                "ward": wards[i % len(wards)],
                "isureporter": "yes" if i % 2 == 0 else "no" if i % 3 == 0 else None,
                "hasbirth": "no",
            }

            if i % 3 == 0:
                fields["profession"] = "Farmer"  # only some contacts have any value for this

            urns = [f"tel:{number}"]
            if twitter:
                urns.append(f"twitter:{twitter}")

            self.create_contact(name, urns=urns, fields=fields)

        def q(query):
            results = search_contacts(self.org, query, group=self.org.active_contacts_group)
            return results.total

        db_config = connection.settings_dict
        database_url = (
            f"postgres://{db_config['USER']}:{db_config['PASSWORD']}@{db_config['HOST']}:{db_config['PORT']}/"
            f"{db_config['NAME']}?sslmode=disable"
        )
        print(f"Using database: {database_url}")

        result = subprocess.run(
            ["./rp-indexer", "-elastic-url", settings.ELASTICSEARCH_URL, "-db", database_url, "-rebuild"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        self.assertEqual(result.returncode, 0, "Command failed: %s\n\n%s" % (result.stdout, result.stderr))

        # give ES some time to publish the results
        time.sleep(5)

        self.assertEqual(q("trey"), 15)
        self.assertEqual(q("MIKE"), 15)
        self.assertEqual(q("  paige  "), 15)
        self.assertEqual(q("0188382011"), 1)
        self.assertEqual(q("trey 0188382"), 15)

        # name as property
        self.assertEqual(q('name is "trey"'), 15)
        self.assertEqual(q("name is mike"), 15)
        self.assertEqual(q("name = paige"), 15)
        self.assertEqual(q('name != ""'), 60)
        self.assertEqual(q('NAME = ""'), 30)
        self.assertEqual(q("name ~ Mi"), 15)
        self.assertEqual(q('name != "Mike"'), 75)

        # URN as property
        self.assertEqual(q("tel is +250188382011"), 1)
        self.assertEqual(q("tel has 0188382011"), 1)
        self.assertEqual(q("twitter = tweep_13"), 1)
        self.assertEqual(q('twitter = ""'), 60)
        self.assertEqual(q('twitter != ""'), 30)
        self.assertEqual(q("TWITTER has tweep"), 30)

        # contact field as property
        self.assertEqual(q("age > 30"), 69)
        self.assertEqual(q("age >= 30"), 70)
        self.assertEqual(q("age > 30 and age <= 40"), 10)
        self.assertEqual(q("AGE < 20"), 10)
        self.assertEqual(q('age != ""'), 90)
        self.assertEqual(q('age = ""'), 0)

        self.assertEqual(q("join_date = 1-1-14"), 1)
        self.assertEqual(q("join_date < 30/1/2014"), 29)
        self.assertEqual(q("join_date <= 30/1/2014"), 30)
        self.assertEqual(q("join_date > 30/1/2014"), 60)
        self.assertEqual(q("join_date >= 30/1/2014"), 61)
        self.assertEqual(q('join_date != ""'), 90)
        self.assertEqual(q('join_date = ""'), 0)

        self.assertEqual(q('state is "Eastern Province"'), 90)
        self.assertEqual(q("HOME is Kayônza"), 23)
        self.assertEqual(q("ward is kageyo"), 23)
        self.assertEqual(q("ward != kageyo"), 67)  # includes objects with empty ward

        self.assertEqual(q('home is ""'), 22)
        self.assertEqual(q('profession = ""'), 60)
        self.assertEqual(q('profession is ""'), 60)
        self.assertEqual(q('profession != ""'), 30)

        # contact fields beginning with 'is' or 'has'
        self.assertEqual(q('isureporter = "yes"'), 45)
        self.assertEqual(q("isureporter = yes"), 45)
        self.assertEqual(q("isureporter = no"), 15)
        self.assertEqual(q("isureporter != no"), 75)  # includes objects with empty isureporter

        self.assertEqual(q('hasbirth = "no"'), 90)
        self.assertEqual(q("hasbirth = no"), 90)
        self.assertEqual(q("hasbirth = yes"), 0)

        # boolean combinations
        self.assertEqual(q("name is trey or name is mike"), 30)
        self.assertEqual(q("name is trey and age < 20"), 2)
        self.assertEqual(q('(home is gatsibo or home is "Rwamagana")'), 45)
        self.assertEqual(q('(home is gatsibo or home is "Rwamagana") and name is trey'), 15)
        self.assertEqual(q('name is MIKE and profession = ""'), 15)
        self.assertEqual(q("profession = doctor or profession = farmer"), 30)  # same field
        self.assertEqual(q("age = 20 or age = 21"), 2)
        self.assertEqual(q("join_date = 30/1/2014 or join_date = 31/1/2014"), 2)

        # create contact with no phone number, we'll try searching for it by id
        contact = self.create_contact(name="Id Contact")

        # a new contact was created, execute the rp-indexer again
        result = subprocess.run(
            ["./rp-indexer", "-elastic-url", settings.ELASTICSEARCH_URL, "-db", database_url, "-rebuild"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        self.assertEqual(result.returncode, 0, "Command failed: %s\n\n%s" % (result.stdout, result.stderr))

        # give ES some time to publish the results
        time.sleep(5)

        # NOTE: when this fails with `AssertionError: 90 != 0`, check if contact phone numbers might match
        # NOTE: for example id=2507, tel=250788382011 ... will match
        # non-anon orgs can't search by id (because they never see ids), but they match on tel
        self.assertEqual(q("%d" % contact.pk), 0)

        with AnonymousOrg(self.org):
            # give mailroom time to clear its org cache
            time.sleep(5)

            # still allow name and field searches
            self.assertEqual(q("trey"), 15)
            self.assertEqual(q("name is mike"), 15)
            self.assertEqual(q("age > 30"), 69)

            # don't allow matching on URNs
            self.assertEqual(q("0188382011"), 0)
            self.assertRaises(SearchException, q, "tel is +250188382011")
            self.assertRaises(SearchException, q, "twitter has tweep")

            # anon orgs can search by id, with or without zero padding
            self.assertEqual(q("%d" % contact.pk), 1)
            self.assertEqual(q("%010d" % contact.pk), 1)

        # give mailroom time to clear its org cache
        time.sleep(5)

        # invalid queries
        self.assertRaises(SearchException, q, "((")
        self.assertRaises(SearchException, q, 'name = "trey')  # unterminated string literal
        self.assertRaises(SearchException, q, "name > trey")  # unrecognized non-field operator   # ValueError
        self.assertRaises(SearchException, q, "profession > trey")  # unrecognized text-field operator   # ValueError
        self.assertRaises(SearchException, q, "age has 4")  # unrecognized decimal-field operator   # ValueError
        self.assertRaises(SearchException, q, "age = x")  # unparseable decimal-field comparison
        self.assertRaises(
            SearchException, q, "join_date has 30/1/2014"
        )  # unrecognized date-field operator   # ValueError
        self.assertRaises(SearchException, q, "join_date > xxxxx")  # unparseable date-field comparison
        self.assertRaises(SearchException, q, "home > kigali")  # unrecognized location-field operator
        self.assertRaises(SearchException, q, "credits > 10")  # non-existent field or attribute
        self.assertRaises(SearchException, q, "tel < +250188382011")  # unsupported comparator for a URN   # ValueError
        self.assertRaises(SearchException, q, 'tel < ""')  # unsupported comparator for an empty string
        self.assertRaises(SearchException, q, "data=“not empty”")  # unicode “,” are not accepted characters

        # test contact_search_list
        url = reverse("contacts.contact_list")
        self.login(self.admin)

        response = self.client.get("%s?sort_on=%s" % (url, "created_on"))
        self.assertEqual(response.context["object_list"][0].name, "Trey")  # first contact in the set
        self.assertEqual(response.context["object_list"][0].fields[str(age.uuid)], {"text": "10", "number": "10"})

        response = self.client.get("%s?sort_on=-%s" % (url, "created_on"))
        self.assertEqual(response.context["object_list"][0].name, "Id Contact")  # last contact in the set
        self.assertEqual(response.context["object_list"][0].fields, None)

        response = self.client.get("%s?sort_on=-%s" % (url, str(ward.key)))
        self.assertEqual(
            response.context["object_list"][0].fields[str(ward.uuid)],
            {
                "district": "Rwanda > Eastern Province > Gatsibo",
                "state": "Rwanda > Eastern Province",
                "text": "Kageyo",
                "ward": "Rwanda > Eastern Province > Gatsibo > Kageyo",
            },
        )

        response = self.client.get("%s?sort_on=%s" % (url, str(ward.key)))
        self.assertEqual(
            response.context["object_list"][0].fields[str(ward.uuid)],
            {
                "district": "Rwanda > Eastern Province > Rwamagana",
                "state": "Rwanda > Eastern Province",
                "text": "Bukure",
                "ward": "Rwanda > Eastern Province > Rwamagana > Bukure",
            },
        )

        now = timezone.now()
        next_two_days = timezone.now() + timezone.timedelta(days=2)

        self.create_contact(name="James", urns=["tel:+250188382999"], last_seen_on=next_two_days)
        self.create_contact(name="Chris", urns=["tel:+250188382888"], last_seen_on=now)

        # new contacts were created, execute the rp-indexer again
        result = subprocess.run(
            ["./rp-indexer", "-elastic-url", settings.ELASTICSEARCH_URL, "-db", database_url, "-rebuild"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        self.assertEqual(result.returncode, 0, "Command failed: %s\n\n%s" % (result.stdout, result.stderr))

        # give ES some time to publish the results
        time.sleep(5)

        response = self.client.get("%s?sort_on=%s" % (url, "last_seen_on"))
        self.assertEqual(response.context["object_list"][0].name, "Chris")  # oldest contact last seen

        response = self.client.get("%s?sort_on=-%s" % (url, "last_seen_on"))
        self.assertEqual(response.context["object_list"][0].name, "James")  # recent contact last seen

        # create a dynamic group on age
        self.login(self.admin)
        url = reverse("contacts.contactgroup_create")
        self.client.post(url, dict(name="Adults", group_query="age > 30"))

        time.sleep(5)

        # check that it was created with the right counts
        adults = ContactGroup.user_groups.get(org=self.org, name="Adults")
        self.assertEqual(69, adults.get_member_count())

        # create a campaign and event on this group
        campaign = Campaign.create(self.org, self.admin, "Cake Day", adults)
        created_on = ContactField.all_fields.get(org=self.org, key="created_on")
        event = CampaignEvent.create_message_event(
            self.org, self.admin, campaign, relative_to=created_on, offset=12, unit="M", message="Happy One Year!"
        )

        # mailroom creation of event fires
        event.schedule_async()

        # should have 69 events
        EventFire.objects.filter(event=event, fired=None).count()

        # update the query
        url = reverse("contacts.contactgroup_update", args=[adults.id])
        self.client.post(url, dict(name="Adults", query="age > 18"))

        # need to wait at least 10 seconds because mailroom will wait that long to give indexer time to catch up if it
        # sees recently modified contacts
        time.sleep(13)

        # should have updated count
        self.assertEqual(81, adults.get_member_count())


class ContactImportTest(TembaTest):
    def test_parse_errors(self):
        # try to open an import that is completely empty
        with self.assertRaisesRegexp(ValidationError, "Import file appears to be empty."):
            ContactImport.try_to_parse(self.org, io.BytesIO(b""), "foo.csv")

        def try_to_parse(name):
            path = f"media/test_imports/{name}"
            with open(path, "rb") as f:
                ContactImport.try_to_parse(self.org, f, path)

        # try to open an import that exceeds the record limit
        with patch("temba.contacts.models.ContactImport.MAX_RECORDS", 2):
            with self.assertRaisesRegexp(ValidationError, r"Import files can contain a maximum of 2 records\."):
                try_to_parse("simple.xlsx")

        bad_files = [
            ("empty.csv", "Import file doesn't contain any records."),
            ("empty_header.xls", "Import file contains an empty header."),
            ("duplicate_urn.xlsx", "Import file contains duplicated contact URN 'tel:+250788382382'."),
            (
                "duplicate_uuid.xlsx",
                "Import file contains duplicated contact UUID 'f519ca1f-8513-49ba-8896-22bf0420dec7'.",
            ),
            ("invalid_scheme.xlsx", "Header 'URN:XXX' is not a valid URN type."),
            ("invalid_field_key.xlsx", "Header 'Field: #$^%' is not a valid field name."),
            ("reserved_field_key.xlsx", "Header 'Field:id' is not a valid field name."),
            ("no_urn_or_uuid.xlsx", "Import files must contain either UUID or a URN header."),
            ("uuid_only.csv", "Import files must contain columns besides UUID."),
        ]

        for imp_file, imp_error in bad_files:
            with self.assertRaises(ValidationError, msg=f"expected error in {imp_file}") as e:
                try_to_parse(imp_file)
            self.assertEqual(imp_error, e.exception.messages[0], f"error mismatch for {imp_file}")

    def test_extract_mappings(self):
        # try simple import in different formats
        for ext in ("csv", "xls", "xlsx"):
            imp = self.create_contact_import(f"media/test_imports/simple.{ext}")
            self.assertEqual(3, imp.num_records)
            self.assertEqual(
                [
                    {"header": "URN:Tel", "mapping": {"type": "scheme", "scheme": "tel"}},
                    {"header": "name", "mapping": {"type": "attribute", "name": "name"}},
                ],
                imp.mappings,
            )

        # try import with 2 URN types
        imp = self.create_contact_import("media/test_imports/twitter_and_phone.xls")
        self.assertEqual(
            [
                {"header": "URN:Tel", "mapping": {"type": "scheme", "scheme": "tel"}},
                {"header": "name", "mapping": {"type": "attribute", "name": "name"}},
                {"header": "URN:Twitter", "mapping": {"type": "scheme", "scheme": "twitter"}},
            ],
            imp.mappings,
        )

        # or with 3 URN columns
        imp = self.create_contact_import("media/test_imports/multiple_tel_urns.xlsx")
        self.assertEqual(
            [
                {"header": "Name", "mapping": {"type": "attribute", "name": "name"}},
                {"header": "URN:Tel", "mapping": {"type": "scheme", "scheme": "tel"}},
                {"header": "URN:Tel", "mapping": {"type": "scheme", "scheme": "tel"}},
                {"header": "URN:Tel", "mapping": {"type": "scheme", "scheme": "tel"}},
            ],
            imp.mappings,
        )

        imp = self.create_contact_import("media/test_imports/missing_name_header.xls")
        self.assertEqual([{"header": "URN:Tel", "mapping": {"type": "scheme", "scheme": "tel"}}], imp.mappings)

        self.create_field("goats", "Num Goats", ContactField.TYPE_NUMBER)

        imp = self.create_contact_import("media/test_imports/extra_fields_and_group.xlsx")
        self.assertEqual(
            [
                {"header": "URN:Tel", "mapping": {"type": "scheme", "scheme": "tel"}},
                {"header": "Name", "mapping": {"type": "attribute", "name": "name"}},
                {"header": "language", "mapping": {"type": "attribute", "name": "language"}},
                {"header": "Created On", "mapping": {"type": "ignore"}},
                {
                    "header": "field: goats",
                    "mapping": {"type": "field", "key": "goats", "name": "Num Goats"},  # matched by key
                },
                {
                    "header": "Field:Sheep",
                    "mapping": {"type": "new_field", "key": "sheep", "name": "Sheep", "value_type": "T"},
                },
                {"header": "Group:Testers", "mapping": {"type": "ignore"}},
            ],
            imp.mappings,
        )

        # it's possible for field keys and labels to be out of sync, in which case we match by label first because
        # that's how we export contacts
        self.create_field("num_goats", "Goats", ContactField.TYPE_NUMBER)

        imp = self.create_contact_import("media/test_imports/extra_fields_and_group.xlsx")
        self.assertEqual(
            {
                "header": "field: goats",
                "mapping": {"type": "field", "key": "num_goats", "name": "Goats"},  # matched by label
            },
            imp.mappings[4],
        )

        # a header can be a number but it will be ignored
        imp = self.create_contact_import("media/test_imports/numerical_header.xlsx")
        self.assertEqual(
            [
                {"header": "URN:Tel", "mapping": {"type": "scheme", "scheme": "tel"}},
                {"header": "Name", "mapping": {"name": "name", "type": "attribute"}},
                {"header": "123", "mapping": {"type": "ignore"}},
            ],
            imp.mappings,
        )

        self.create_field("a_number", "A-Number", ContactField.TYPE_NUMBER)

        imp = self.create_contact_import("media/test_imports/header_chars.xlsx")
        self.assertEqual(
            [
                {"header": "URN:Tel", "mapping": {"type": "scheme", "scheme": "tel"}},
                {"header": "Name", "mapping": {"type": "attribute", "name": "name"}},
                {"header": "Field: A-Number", "mapping": {"type": "field", "key": "a_number", "name": "A-Number"}},
            ],
            imp.mappings,
        )

    @mock_mailroom
    def test_batches(self, mr_mocks):
        imp = self.create_contact_import("media/test_imports/simple.xlsx")
        self.assertEqual(3, imp.num_records)
        self.assertIsNone(imp.started_on)

        # info can be fetched but it's empty
        self.assertEqual(
            {"status": "P", "num_created": 0, "num_updated": 0, "num_errored": 0, "errors": [], "time_taken": 0},
            imp.get_info(),
        )

        imp.start()
        batches = list(imp.batches.order_by("id"))

        self.assertIsNotNone(imp.started_on)
        self.assertEqual(1, len(batches))
        self.assertEqual(0, batches[0].record_start)
        self.assertEqual(3, batches[0].record_end)
        self.assertEqual(
            [
                {"name": "Eric Newcomer", "urns": ["tel:+250788382382"], "groups": [str(imp.group.uuid)]},
                {"name": "NIC POTTIER", "urns": ["tel:+250788383383"], "groups": [str(imp.group.uuid)]},
                {"name": "jen newcomer", "urns": ["tel:+250788383385"], "groups": [str(imp.group.uuid)]},
            ],
            batches[0].specs,
        )

        # check batch was queued for import by mailroom
        self.assertEqual(
            [
                {
                    "type": "import_contact_batch",
                    "org_id": self.org.id,
                    "task": {"contact_import_batch_id": batches[0].id},
                    "queued_on": matchers.Datetime(),
                },
            ],
            mr_mocks.queued_batch_tasks,
        )

        # records are batched if they exceed batch size
        with patch("temba.contacts.models.ContactImport.BATCH_SIZE", 2):
            imp = self.create_contact_import("media/test_imports/simple.xlsx")
            imp.start()

        batches = list(imp.batches.order_by("id"))
        self.assertEqual(2, len(batches))
        self.assertEqual(0, batches[0].record_start)
        self.assertEqual(2, batches[0].record_end)
        self.assertEqual(2, batches[1].record_start)
        self.assertEqual(3, batches[1].record_end)

        # info is calculated across all batches
        self.assertEqual(
            {
                "status": "O",
                "num_created": 0,
                "num_updated": 0,
                "num_errored": 0,
                "errors": [],
                "time_taken": matchers.Int(),
            },
            imp.get_info(),
        )

        # simulate mailroom starting to process first batch
        imp.batches.filter(id=batches[0].id).update(
            status="O", num_created=2, num_updated=1, errors=[{"record": 1, "message": "that's wrong"}]
        )

        self.assertEqual(
            {
                "status": "O",
                "num_created": 2,
                "num_updated": 1,
                "num_errored": 0,
                "errors": [{"record": 1, "message": "that's wrong"}],
                "time_taken": matchers.Int(),
            },
            imp.get_info(),
        )

        # simulate mailroom completing first batch, starting second
        imp.batches.filter(id=batches[0].id).update(status="C", finished_on=timezone.now())
        imp.batches.filter(id=batches[1].id).update(
            status="O", num_created=3, num_updated=5, errors=[{"record": 3, "message": "that's not right"}]
        )

        self.assertEqual(
            {
                "status": "O",
                "num_created": 5,
                "num_updated": 6,
                "num_errored": 0,
                "errors": [{"record": 1, "message": "that's wrong"}, {"record": 3, "message": "that's not right"}],
                "time_taken": matchers.Int(),
            },
            imp.get_info(),
        )

        # simulate mailroom completing second batch
        imp.batches.filter(id=batches[1].id).update(status="C", finished_on=timezone.now())
        imp.status = "C"
        imp.finished_on = timezone.now()
        imp.save(update_fields=("finished_on", "status"))

        self.assertEqual(
            {
                "status": "C",
                "num_created": 5,
                "num_updated": 6,
                "num_errored": 0,
                "errors": [{"record": 1, "message": "that's wrong"}, {"record": 3, "message": "that's not right"}],
                "time_taken": matchers.Int(),
            },
            imp.get_info(),
        )

    @mock_mailroom
    def test_batches_with_fields(self, mr_mocks):
        self.create_field("goats", "Goats", ContactField.TYPE_NUMBER)

        imp = self.create_contact_import("media/test_imports/extra_fields_and_group.xlsx")
        imp.start()
        batch = imp.batches.get()  # single batch

        self.assertEqual(
            [
                {
                    "name": "John Doe",
                    "language": "eng",
                    "urns": ["tel:+250788123123"],
                    "fields": {"goats": "1", "sheep": "0"},
                    "groups": [str(imp.group.uuid)],
                },
                {
                    "name": "Mary Smith",
                    "language": "spa",
                    "urns": ["tel:+250788456456"],
                    "fields": {"goats": "3", "sheep": "5"},
                    "groups": [str(imp.group.uuid)],
                },
                {"urns": ["tel:+250788456678"], "groups": [str(imp.group.uuid)]},  # blank values ignored
            ],
            batch.specs,
        )

        imp = self.create_contact_import("media/test_imports/with_uuid.xlsx")
        imp.start()
        batch = imp.batches.get()
        self.assertEqual(
            [
                {"uuid": "f519ca1f-8513-49ba-8896-22bf0420dec7", "name": "Joe", "groups": [str(imp.group.uuid)]},
                {"uuid": "989975f0-3bff-43d6-82c8-a6bbc201c938", "name": "Frank", "groups": [str(imp.group.uuid)]},
            ],
            batch.specs,
        )

        # cells with -- mean explicit clearing of those values
        imp = self.create_contact_import("media/test_imports/explicit_clearing.xlsx")
        imp.start()
        batch = imp.batches.get()  # single batch

        self.assertEqual(
            {
                "name": "",
                "language": "",
                "urns": ["tel:+250788456678"],
                "fields": {"goats": "", "sheep": ""},
                "groups": [str(imp.group.uuid)],
            },
            batch.specs[2],
        )

        # uuids and languages converted to lowercase, case in names is preserved
        imp = self.create_contact_import("media/test_imports/uppercase.xlsx")
        imp.start()
        batch = imp.batches.get()
        self.assertEqual(
            [
                {
                    "uuid": "92faa753-6faa-474a-a833-788032d0b757",
                    "name": "Eric Newcomer",
                    "language": "eng",
                    "groups": [str(imp.group.uuid)],
                },
                {
                    "uuid": "3c11ac1f-c869-4247-a73c-9b97bff61659",
                    "name": "NIC POTTIER",
                    "language": "spa",
                    "groups": [str(imp.group.uuid)],
                },
            ],
            batch.specs,
        )

    @mock_mailroom
    def test_batches_with_invalid_urn(self, mr_mocks):
        imp = self.create_contact_import("media/test_imports/invalid_urn.xlsx")
        imp.start()
        batch = imp.batches.get()

        # invalid looking urns still passed to mailroom to decide how to handle them
        self.assertEqual(
            [
                {"name": "Eric Newcomer", "urns": ["tel:+%3F"], "groups": [str(imp.group.uuid)]},
                {"name": "Nic Pottier", "urns": ["tel:2345678901234567890"], "groups": [str(imp.group.uuid)]},
            ],
            batch.specs,
        )

    @mock_mailroom
    def test_batches_with_multiple_tels(self, mr_mocks):
        imp = self.create_contact_import("media/test_imports/multiple_tel_urns.xlsx")
        imp.start()
        batch = imp.batches.get()

        self.assertEqual(
            [
                {
                    "name": "Bob",
                    "urns": ["tel:+250788382001", "tel:+250788382002", "tel:+250788382003"],
                    "groups": [str(imp.group.uuid)],
                },
                {"name": "Jim", "urns": ["tel:+250788382004", "tel:+250788382005"], "groups": [str(imp.group.uuid)]},
            ],
            batch.specs,
        )

    @mock_mailroom
    def test_batches_from_csv(self, mr_mocks):
        imp = self.create_contact_import("media/test_imports/simple.csv")
        imp.start()
        batch = imp.batches.get()

        self.assertEqual(
            [
                {"name": "Eric Newcomer", "urns": ["tel:+250788382382"], "groups": [str(imp.group.uuid)]},
                {"name": "NIC POTTIER", "urns": ["tel:+250788383383"], "groups": [str(imp.group.uuid)]},
                {"name": "jen newcomer", "urns": ["tel:+250788383385"], "groups": [str(imp.group.uuid)]},
            ],
            batch.specs,
        )

        # check that we correctly detect different encodings
        enc_tests = [
            ("utf16-le", "Drazen"),
            ("utf16-be", "Drazen"),
            ("iso-8859-1", "Dràzen"),
        ]
        for test in enc_tests:
            imp = self.create_contact_import(f"media/test_imports/encoding_{test[0]}.csv")
            imp.start()
            batch = imp.batches.get()
            self.assertEqual(test[1], batch.specs[0]["name"])

    @mock_mailroom
    def test_detect_spamminess(self, mr_mocks):
        imp = self.create_contact_import("media/test_imports/sequential_tels.xls")
        imp.start()

        self.org.refresh_from_db()
        self.assertTrue(self.org.is_flagged)

        with patch("temba.contacts.models.ContactImport.SEQUENTIAL_URNS_THRESHOLD", 3):
            self.assertFalse(ContactImport._detect_spamminess(["tel:+593979000001", "tel:+593979000002"]))
            self.assertFalse(
                ContactImport._detect_spamminess(
                    ["tel:+593979000001", "tel:+593979000003", "tel:+593979000005", "tel:+593979000007"]
                )
            )

            self.assertTrue(
                ContactImport._detect_spamminess(["tel:+593979000001", "tel:+593979000002", "tel:+593979000003"])
            )

            # order not important
            self.assertTrue(
                ContactImport._detect_spamminess(["tel:+593979000003", "tel:+593979000001", "tel:+593979000002"])
            )

            # non-numeric paths ignored
            self.assertTrue(
                ContactImport._detect_spamminess(
                    ["tel:+593979000001", "tel:ABC", "tel:+593979000002", "tel:+593979000003"]
                )
            )

    @mock_mailroom
    def test_detect_spamminess_verified_org(self, mr_mocks):
        # if an org is verified, no flagging occurs
        self.org.verify()

        imp = self.create_contact_import("media/test_imports/sequential_tels.xls")
        imp.start()

        self.org.refresh_from_db()
        self.assertFalse(self.org.is_flagged)

    def test_data_types(self):
        imp = self.create_contact_import("media/test_imports/data_formats.xlsx")
        imp.start()
        batch = imp.batches.get()
        self.assertEqual(
            [
                {
                    "uuid": "17c4388a-024f-4e67-937a-13be78a70766",
                    "fields": {
                        "a_number": "1234.5678",
                        "a_date": "2020-10-19",
                        "a_time": "13:17:00",
                        "a_datetime": "2020-10-19T13:18:00+02:00",
                        "price": "123.45",
                    },
                    "groups": [str(imp.group.uuid)],
                }
            ],
            batch.specs,
        )

    def test_parse_value(self):
        imp = self.create_contact_import("media/test_imports/simple.xlsx")
        kgl = pytz.timezone("Africa/Kigali")

        tests = [
            ("", ""),
            (" Yes ", "Yes"),
            (1234, "1234"),
            (123.456, "123.456"),
            (date(2020, 9, 18), "2020-09-18"),
            (datetime(2020, 9, 18, 15, 45, 30, 0), "2020-09-18T15:45:30+02:00"),
            (kgl.localize(datetime(2020, 9, 18, 15, 45, 30, 0)), "2020-09-18T15:45:30+02:00"),
        ]
        for test in tests:
            self.assertEqual(test[1], imp._parse_value(test[0], tz=kgl))

    def test_get_default_group_name(self):
        self.create_group("Testers", contacts=[])
        tests = [
            ("simple.csv", "Simple"),
            ("testers.csv", "Testers 1"),  # group called Testers already exists
            ("contact-imports.csv", "Contact Imports"),
            ("abc_@@é.csv", "Abc É"),
            ("a_@@é.csv", "Import"),  # would be too short
            (f"{'x' * 100}.csv", "Xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx"),  # truncated
        ]
        for test in tests:
            self.assertEqual(test[1], ContactImport(org=self.org, original_filename=test[0]).get_default_group_name())

    @mock_mailroom
    def test_delete(self, mr_mocks):
        imp = self.create_contact_import("media/test_imports/simple.csv")
        imp.start()
        imp.delete()

        self.assertEqual(0, ContactImport.objects.count())
        self.assertEqual(0, ContactImportBatch.objects.count())


class ContactImportCRUDLTest(TembaTest, CRUDLTestMixin):
    def test_create_and_preview(self):
        create_url = reverse("contacts.contactimport_create")

        self.assertCreateFetch(create_url, allow_viewers=False, allow_editors=True, form_fields=["file"])

        # try posting with nothing
        response = self.client.post(create_url, {})
        self.assertFormError(response, "form", "file", "This field is required.")

        def upload(path):
            with open(path, "rb") as f:
                return SimpleUploadedFile(path, content=f.read())

        # try uploading an empty CSV file
        response = self.client.post(create_url, {"file": upload("media/test_imports/empty.csv")})
        self.assertFormError(response, "form", "file", "Import file doesn't contain any records.")

        # try uploading a valid XLSX file
        response = self.client.post(create_url, {"file": upload("media/test_imports/simple.xlsx")})
        self.assertEqual(302, response.status_code)

        imp = ContactImport.objects.get()
        self.assertEqual(self.org, imp.org)
        self.assertEqual(3, imp.num_records)
        self.assertRegexpMatches(imp.file.name, rf"^contact_imports/{self.org.id}/[\w-]{{36}}.xlsx$")
        self.assertEqual("simple.xlsx", imp.original_filename)
        self.assertIsNone(imp.started_on)
        self.assertIsNone(imp.group)

        preview_url = reverse("contacts.contactimport_preview", args=[imp.id])
        read_url = reverse("contacts.contactimport_read", args=[imp.id])

        # will have been redirected to the preview view for the new import
        self.assertEqual(preview_url, response.url)

        response = self.client.get(preview_url)
        self.assertContains(response, "URN:Tel")
        self.assertContains(response, "name")

        response = self.client.post(preview_url, {})
        self.assertEqual(302, response.status_code)
        self.assertEqual(read_url, response.url)

        imp.refresh_from_db()
        self.assertIsNotNone(imp.started_on)

        # can no longer access preview URL.. will be redirected to read
        response = self.client.get(preview_url)
        self.assertEqual(302, response.status_code)
        self.assertEqual(read_url, response.url)

    @mock_mailroom
    def test_creating_new_group(self, mr_mocks):
        self.login(self.admin)
        imp = self.create_contact_import("media/test_imports/simple.csv")
        preview_url = reverse("contacts.contactimport_preview", args=[imp.id])
        read_url = reverse("contacts.contactimport_read", args=[imp.id])

        # create some groups
        self.create_group("Testers", contacts=[])
        self.create_group("Doctors", contacts=[])

        # try creating new group but not providing a name
        response = self.client.post(preview_url, {"add_to_group": True, "group_mode": "N", "new_group_name": "  "})
        self.assertFormError(response, "form", "new_group_name", "Required.")

        # try creating new group but providing an invalid name
        response = self.client.post(preview_url, {"add_to_group": True, "group_mode": "N", "new_group_name": "????"})
        self.assertFormError(response, "form", "new_group_name", "Invalid group name.")

        # try creating new group but providing a name of an existing group
        response = self.client.post(
            preview_url, {"add_to_group": True, "group_mode": "N", "new_group_name": "testERs"}
        )
        self.assertFormError(response, "form", "new_group_name", "Already exists.")

        # try creating new group when we've already reached our group limit
        with override_settings(ORG_LIMIT_DEFAULTS={"groups": 2}):
            response = self.client.post(
                preview_url, {"add_to_group": True, "group_mode": "N", "new_group_name": "Import"}
            )
            self.assertFormError(response, "form", "__all__", "This workspace has reached the limit of 2 groups.")

        # finally create new group...
        response = self.client.post(preview_url, {"add_to_group": True, "group_mode": "N", "new_group_name": "Import"})
        self.assertRedirect(response, read_url)

        new_group = ContactGroup.user_groups.get(name="Import")
        imp.refresh_from_db()
        self.assertEqual(new_group, imp.group)

    @mock_mailroom
    def test_using_existing_group(self, mr_mocks):
        self.login(self.admin)
        imp = self.create_contact_import("media/test_imports/simple.csv")
        preview_url = reverse("contacts.contactimport_preview", args=[imp.id])
        read_url = reverse("contacts.contactimport_read", args=[imp.id])

        # create some groups
        self.create_field("age", "Age", ContactField.TYPE_NUMBER)
        testers = self.create_group("Testers", contacts=[])
        doctors = self.create_group("Doctors", contacts=[])
        self.create_group("No Age", query='age = ""')

        # only static groups appear as options
        response = self.client.get(preview_url)
        self.assertEqual([doctors, testers], list(response.context["form"].fields["existing_group"].queryset))

        # try submitting without group
        response = self.client.post(preview_url, {"add_to_group": True, "group_mode": "E", "existing_group": ""})
        self.assertFormError(response, "form", "existing_group", "Required.")

        # finally try with actual group...
        response = self.client.post(
            preview_url, {"add_to_group": True, "group_mode": "E", "existing_group": doctors.id}
        )
        self.assertRedirect(response, read_url)

        imp.refresh_from_db()
        self.assertEqual(doctors, imp.group)

    def test_preview_with_mappings(self):
        self.create_field("age", "Age", ContactField.TYPE_NUMBER)

        imp = self.create_contact_import("media/test_imports/extra_fields_and_group.xlsx")
        preview_url = reverse("contacts.contactimport_preview", args=[imp.id])

        # columns 4 and 5 are a non-existent field so will have controls to create a new one
        self.assertUpdateFetch(
            preview_url,
            allow_viewers=False,
            allow_editors=True,
            form_fields=[
                "add_to_group",
                "group_mode",
                "new_group_name",
                "existing_group",
                "column_4_include",
                "column_4_name",
                "column_4_value_type",
                "column_5_include",
                "column_5_name",
                "column_5_value_type",
            ],
        )

        # if including a new fields, can't use existing field name
        response = self.client.post(
            preview_url,
            {
                "column_4_include": True,
                "column_4_name": "Goats",
                "column_4_value_type": "N",
                "column_5_include": True,
                "column_5_name": "age",
                "column_5_value_type": "N",
                "add_to_group": False,
            },
        )
        self.assertEqual(1, len(response.context["form"].errors))
        self.assertFormError(response, "form", "__all__", "Field name for 'Field:Sheep' matches an existing field.")

        # if including a new fields, can't repeat names
        response = self.client.post(
            preview_url,
            {
                "column_4_include": True,
                "column_4_name": "Goats",
                "column_4_value_type": "N",
                "column_5_include": True,
                "column_5_name": "goats",
                "column_5_value_type": "N",
                "add_to_group": False,
            },
        )
        self.assertEqual(1, len(response.context["form"].errors))
        self.assertFormError(response, "form", "__all__", "Field name 'goats' is repeated.")

        # if including a new field, name can't be invalid
        response = self.client.post(
            preview_url,
            {
                "column_4_include": True,
                "column_4_name": "Goats",
                "column_4_value_type": "N",
                "column_5_include": True,
                "column_5_name": "#$%^@",
                "column_5_value_type": "N",
                "add_to_group": False,
            },
        )
        self.assertEqual(1, len(response.context["form"].errors))
        self.assertFormError(
            response, "form", "__all__", "Field name for 'Field:Sheep' is invalid or a reserved word."
        )

        # or empty
        response = self.client.post(
            preview_url,
            {
                "column_4_include": True,
                "column_4_name": "Goats",
                "column_4_value_type": "N",
                "column_5_include": True,
                "column_5_name": "",
                "column_5_value_type": "T",
                "add_to_group": False,
            },
        )
        self.assertEqual(1, len(response.context["form"].errors))
        self.assertFormError(response, "form", "__all__", "Field name for 'Field:Sheep' can't be empty.")

        # unless you're ignoring it
        response = self.client.post(
            preview_url,
            {
                "column_4_include": True,
                "column_4_name": "Goats",
                "column_4_value_type": "N",
                "column_5_include": False,
                "column_5_name": "",
                "column_5_value_type": "T",
                "add_to_group": False,
            },
        )
        self.assertEqual(302, response.status_code)

        # mappings will have been updated
        imp.refresh_from_db()
        self.assertEqual(
            [
                {"header": "URN:Tel", "mapping": {"type": "scheme", "scheme": "tel"}},
                {"header": "Name", "mapping": {"type": "attribute", "name": "name"}},
                {"header": "language", "mapping": {"type": "attribute", "name": "language"}},
                {"header": "Created On", "mapping": {"type": "ignore"}},
                {
                    "header": "field: goats",
                    "mapping": {"type": "new_field", "key": "goats", "name": "Goats", "value_type": "N"},
                },
                {"header": "Field:Sheep", "mapping": {"type": "ignore"}},
                {"header": "Group:Testers", "mapping": {"type": "ignore"}},
            ],
            imp.mappings,
        )

    @patch("temba.contacts.models.ContactImport.BATCH_SIZE", 2)
    def test_read(self):
        imp = self.create_contact_import("media/test_imports/simple.xlsx")
        imp.start()

        read_url = reverse("contacts.contactimport_read", args=[imp.id])

        self.assertReadFetch(read_url, allow_viewers=True, allow_editors=True, context_object=imp)
