import io
import tempfile
from datetime import date, datetime, timedelta, timezone as tzone
from decimal import Decimal
from unittest.mock import call, patch
from uuid import UUID
from zoneinfo import ZoneInfo

import iso8601
from openpyxl import load_workbook

from django.core.files.storage import default_storage
from django.core.validators import ValidationError
from django.db.models import Value as DbValue
from django.db.models.functions import Concat, Substr
from django.db.utils import IntegrityError
from django.test.utils import override_settings
from django.urls import reverse
from django.utils import timezone

from temba import mailroom
from temba.airtime.models import AirtimeTransfer
from temba.campaigns.models import Campaign, CampaignEvent, EventFire
from temba.channels.models import ChannelEvent
from temba.flows.models import Flow, FlowSession, FlowStart
from temba.ivr.models import Call
from temba.locations.models import AdminBoundary
from temba.mailroom import modifiers
from temba.msgs.models import Msg, SystemLabel
from temba.orgs.models import Export, Org, OrgRole
from temba.schedules.models import Schedule
from temba.tests import CRUDLTestMixin, MigrationTest, MockResponse, TembaTest, matchers, mock_mailroom
from temba.tests.engine import MockSessionWriter
from temba.tickets.models import Ticket, TicketCount, Topic
from temba.triggers.models import Trigger
from temba.utils import json, s3
from temba.utils.dates import datetime_to_timestamp
from temba.utils.views import TEMBA_MENU_SELECTION

from .models import (
    URN,
    Contact,
    ContactExport,
    ContactField,
    ContactGroup,
    ContactGroupCount,
    ContactImport,
    ContactImportBatch,
    ContactURN,
)
from .tasks import squash_group_counts
from .templatetags.contacts import contact_field, msg_status_badge


class ContactCRUDLTest(CRUDLTestMixin, TembaTest):
    def setUp(self):
        super().setUp()

        self.country = AdminBoundary.create(osm_id="171496", name="Rwanda", level=0)
        AdminBoundary.create(osm_id="1708283", name="Kigali", level=1, parent=self.country)

        self.create_field("age", "Age", value_type="N")
        self.create_field("home", "Home", value_type="S", priority=10)

        # sample flows don't actually get created by org initialization during tests because there are no users at that
        # point so create them explicitly here, so that we also get the sample groups
        self.org.create_sample_flows("https://api.rapidpro.io")

    def test_menu(self):
        menu_url = reverse("contacts.contact_menu")

        self.assertRequestDisallowed(menu_url, [None, self.agent])
        self.assertPageMenu(
            menu_url,
            self.admin,
            [
                "Active (0)",
                "Archived (0)",
                "Blocked (0)",
                "Stopped (0)",
                "Import",
                "Fields (2)",
                ("Groups", ["Open Tickets (0)", "Survey Audience (0)", "Unsatisfied Customers (0)"]),
            ],
        )

    @mock_mailroom
    def test_create(self, mr_mocks):
        create_url = reverse("contacts.contact_create")

        self.assertRequestDisallowed(create_url, [None, self.agent, self.user])
        self.assertCreateFetch(create_url, [self.editor, self.admin], form_fields=("name", "phone"))

        # simulate validation failing because phone number taken
        mr_mocks.contact_urns({"tel:+250781111111": 12345678})

        self.assertCreateSubmit(
            create_url,
            self.admin,
            {"name": "Joe", "phone": "+250781111111"},
            form_errors={"phone": "In use by another contact."},
        )

        # simulate validation failing because phone number isn't E164
        mr_mocks.contact_urns({"tel:+250781111111": False})

        self.assertCreateSubmit(
            create_url,
            self.admin,
            {"name": "Joe", "phone": "+250781111111"},
            form_errors={"phone": "Ensure number includes country code."},
        )

        # simulate validation failing because phone number isn't valid
        mr_mocks.contact_urns({"tel:xx": "URN 0 invalid"})

        self.assertCreateSubmit(
            create_url,
            self.admin,
            {"name": "Joe", "phone": "xx"},
            form_errors={"phone": "Invalid phone number."},
        )

        # try valid number
        self.assertCreateSubmit(
            create_url,
            self.admin,
            {"name": "Joe", "phone": "+250782222222"},
            new_obj_query=Contact.objects.filter(org=self.org, name="Joe", urns__identity="tel:+250782222222"),
            success_status=200,
        )

    @mock_mailroom
    def test_list(self, mr_mocks):
        self.login(self.user)
        list_url = reverse("contacts.contact_list")

        joe = self.create_contact("Joe", phone="123", fields={"age": "20", "home": "Kigali"})
        frank = self.create_contact("Frank", phone="124", fields={"age": "18"})

        mr_mocks.contact_search('name != ""', contacts=[])
        self.create_group("No Name", query='name = ""')

        with self.assertNumQueries(15):
            response = self.client.get(list_url)

        self.assertEqual([frank, joe], list(response.context["object_list"]))
        self.assertIsNone(response.context["search_error"])
        self.assertEqual([], list(response.context["actions"]))
        self.assertContentMenu(list_url, self.user, ["Export"])

        active_contacts = self.org.active_contacts_group

        # fetch with spa flag
        response = self.client.get(list_url, content_type="application/json", HTTP_TEMBA_SPA="1")
        self.assertEqual(response.context["base_template"], "spa.html")

        mr_mocks.contact_search("age = 18", contacts=[frank])

        response = self.client.get(list_url + "?search=age+%3D+18")
        self.assertEqual(list(response.context["object_list"]), [frank])
        self.assertEqual(response.context["search"], "age = 18")
        self.assertEqual(response.context["save_dynamic_search"], True)
        self.assertIsNone(response.context["search_error"])
        self.assertEqual(list(response.context["contact_fields"].values_list("name", flat=True)), ["Home", "Age"])

        mr_mocks.contact_search("age = 18", contacts=[frank], total=10020)

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

        with self.anonymous(self.org):
            mr_mocks.contact_search(f"{joe.id}", cleaned=f"id = {joe.id}", contacts=[joe])

            response = self.client.get(list_url + f"?search={joe.id}")
            self.assertEqual(list(response.context["object_list"]), [joe])
            self.assertIsNone(response.context["search_error"])
            self.assertEqual(response.context["search"], f"id = {joe.id}")
            self.assertEqual(response.context["save_dynamic_search"], False)

        # try with invalid search string
        mr_mocks.exception(mailroom.QueryValidationException("mismatched input at (((", "syntax"))

        response = self.client.get(list_url + "?search=(((")
        self.assertEqual(list(response.context["object_list"]), [])
        self.assertEqual(response.context["search_error"], "Invalid query syntax.")
        self.assertContains(response, "Invalid query syntax.")

        self.login(self.admin)

        # admins can see bulk actions
        age_query = "?search=age%20%3E%2050"
        response = self.client.get(list_url)
        self.assertEqual([frank, joe], list(response.context["object_list"]))
        self.assertEqual(["block", "archive", "send", "start-flow"], list(response.context["actions"]))

        self.assertContentMenu(
            list_url,
            self.admin,
            ["New Contact", "New Group", "Export"],
        )
        self.assertContentMenu(
            list_url + age_query,
            self.admin,
            ["Create Smart Group", "New Contact", "New Group", "Export"],
        )

        # TODO: group labeling as a feature is on probation
        # self.client.post(list_url, {"action": "label", "objects": frank.id, "label": survey_audience.id})
        # self.assertIn(frank, survey_audience.contacts.all())

        # try label bulk action against search results
        # self.client.post(list_url + "?search=Joe", {"action": "label", "objects": joe.id, "label": survey_audience.id})
        # self.assertIn(joe, survey_audience.contacts.all())

        # self.assertEqual(
        #    call(self.org.id, group_uuid=str(active_contacts.uuid), query="Joe", sort="", offset=0, exclude_ids=[]),
        #    mr_mocks.calls["contact_search"][-1],
        # )

        # try archive bulk action
        self.client.post(list_url + "?search=Joe", {"action": "archive", "objects": joe.id})

        # we re-run the search for the response, but exclude Joe
        self.assertEqual(
            call(self.org, active_contacts, "Joe", sort="", offset=0, exclude_ids=[joe.id]),
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

        self.assertRequestDisallowed(blocked_url, [None, self.agent])
        response = self.assertListFetch(blocked_url, [self.editor, self.admin], context_objects=[billy, frank, joe])
        self.assertEqual(["restore", "archive"], list(response.context["actions"]))
        self.assertContentMenu(blocked_url, self.admin, ["Export"])

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

        self.assertRequestDisallowed(stopped_url, [None, self.agent])
        response = self.assertListFetch(
            stopped_url, [self.user, self.editor, self.admin], context_objects=[billy, frank, joe]
        )
        self.assertEqual(["restore", "archive"], list(response.context["actions"]))
        self.assertContentMenu(stopped_url, self.admin, ["Export"])

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

        self.assertRequestDisallowed(archived_url, [None, self.agent])
        response = self.assertListFetch(
            archived_url, [self.user, self.editor, self.admin], context_objects=[billy, frank, joe]
        )
        self.assertEqual(["restore", "delete"], list(response.context["actions"]))
        self.assertContentMenu(archived_url, self.admin, ["Export", "Delete All"])

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
        open_tickets = self.org.groups.get(name="Open Tickets")
        joe = self.create_contact("Joe", phone="123")
        frank = self.create_contact("Frank", phone="124")
        self.create_contact("Bob", phone="125")

        mr_mocks.contact_search("age > 40", contacts=[frank], total=1)

        group1 = self.create_group("Testers", contacts=[joe, frank])  # static group
        group2 = self.create_group("Oldies", query="age > 40")  # smart group
        group2.contacts.add(frank)
        group3 = self.create_group("Other Org", org=self.org2)

        group1_url = reverse("contacts.contact_filter", args=[group1.uuid])
        group2_url = reverse("contacts.contact_filter", args=[group2.uuid])
        group3_url = reverse("contacts.contact_filter", args=[group3.uuid])
        open_tickets_url = reverse("contacts.contact_filter", args=[open_tickets.uuid])

        self.assertRequestDisallowed(group1_url, [None, self.agent, self.admin2])
        response = self.assertReadFetch(group1_url, [self.user, self.editor, self.admin])

        self.assertEqual([frank, joe], list(response.context["object_list"]))
        self.assertEqual(["block", "unlabel", "send", "start-flow"], list(response.context["actions"]))

        self.assertContentMenu(
            group1_url,
            self.admin,
            ["Edit", "Export", "Usages", "Delete"],
        )

        response = self.assertReadFetch(group2_url, [self.editor])

        self.assertEqual([frank], list(response.context["object_list"]))
        self.assertEqual(["block", "archive", "send", "start-flow"], list(response.context["actions"]))
        self.assertContains(response, "age &gt; 40")

        # can access system group like any other except no options to edit or delete
        response = self.assertReadFetch(open_tickets_url, [self.editor])
        self.assertEqual([], list(response.context["object_list"]))
        self.assertEqual(["block", "archive", "send", "start-flow"], list(response.context["actions"]))
        self.assertContains(response, "tickets &gt; 0")
        self.assertContentMenu(open_tickets_url, self.admin, ["Export", "Usages"])

        # if a user tries to access a non-existent group, that's a 404
        response = self.requestView(reverse("contacts.contact_filter", args=["21343253"]), self.admin)
        self.assertEqual(404, response.status_code)

        # if a user tries to access a group in another org, send them to the login page
        response = self.requestView(group3_url, self.admin)
        self.assertLoginRedirect(response)

        # if the user has access to that org, we redirect to the org choose page
        self.org2.add_user(self.admin, OrgRole.ADMINISTRATOR)
        response = self.requestView(group3_url, self.admin)
        self.assertRedirect(response, "/org/choose/")

    @mock_mailroom
    def test_read(self, mr_mocks):
        joe = self.create_contact("Joe", phone="123")

        read_url = reverse("contacts.contact_read", args=[joe.uuid])

        response = self.client.get(read_url)
        self.assertLoginRedirect(response)

        self.assertContentMenu(read_url, self.user, [])
        self.assertContentMenu(read_url, self.editor, ["Edit", "Start Flow", "Open Ticket"])
        self.assertContentMenu(read_url, self.admin, ["Edit", "Start Flow", "Open Ticket"])

        # if there's an open ticket already, don't show open ticket option
        self.create_ticket(joe)
        self.assertContentMenu(read_url, self.editor, ["Edit", "Start Flow"])

        # login as viewer
        self.login(self.user)

        response = self.client.get(read_url)
        self.assertContains(response, "Joe")

        # login as admin
        self.login(self.admin)

        response = self.client.get(read_url)
        self.assertContains(response, "Joe")
        self.assertEqual("/contact/active", response.headers[TEMBA_MENU_SELECTION])

        # block the contact
        joe.block(self.admin)
        self.assertTrue(Contact.objects.get(pk=joe.id, status="B"))

        self.assertContentMenu(read_url, self.admin, ["Edit"])

        response = self.client.get(read_url)
        self.assertContains(response, "Joe")
        self.assertEqual("/contact/blocked", response.headers[TEMBA_MENU_SELECTION])

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
    def test_update(self, mr_mocks):
        self.org.flow_languages = ["eng", "spa"]
        self.org.save(update_fields=("flow_languages",))

        self.create_field("gender", "Gender", value_type=ContactField.TYPE_TEXT)
        contact = self.create_contact(
            "Bob",
            urns=["tel:+593979111111", "tel:+593979222222", "telegram:5474754"],
            fields={"age": 41, "gender": "M"},
            language="eng",
        )
        testers = self.create_group("Testers", contacts=[contact])
        self.create_contact("Ann", urns=["tel:+593979444444"])

        update_url = reverse("contacts.contact_update", args=[contact.id])

        self.assertRequestDisallowed(update_url, [None, self.user, self.agent, self.admin2])
        self.assertUpdateFetch(
            update_url,
            [self.editor, self.admin],
            form_fields={
                "name": "Bob",
                "status": "A",
                "language": "eng",
                "groups": [testers],
                "new_scheme": None,
                "new_path": None,
                "urn__tel__0": "+593979111111",
                "urn__tel__1": "+593979222222",
                "urn__telegram__2": "5474754",
            },
        )

        # try to take URN in use by another contact
        mr_mocks.contact_urns({"tel:+593979444444": 12345678})

        self.assertUpdateSubmit(
            update_url,
            self.admin,
            {"name": "Bobby", "status": "B", "language": "spa", "groups": [testers.id], "urn__tel__0": "+593979444444"},
            form_errors={"urn__tel__0": "In use by another contact."},
            object_unchanged=contact,
        )

        # try to update to an invalid URN
        mr_mocks.contact_urns({"tel:++++": "invalid path component"})

        self.assertUpdateSubmit(
            update_url,
            self.admin,
            {"name": "Bobby", "status": "B", "language": "spa", "groups": [testers.id], "urn__tel__0": "++++"},
            form_errors={"urn__tel__0": "Invalid format."},
            object_unchanged=contact,
        )

        # try to add a new invalid phone URN
        mr_mocks.contact_urns({"tel:123": "not a valid phone number"})

        self.assertUpdateSubmit(
            update_url,
            self.admin,
            {
                "name": "Bobby",
                "status": "B",
                "language": "spa",
                "groups": [testers.id],
                "urn__tel__0": "+593979111111",
                "new_scheme": "tel",
                "new_path": "123",
            },
            form_errors={"new_path": "Invalid format."},
            object_unchanged=contact,
        )

        # try to add a new phone URN that isn't E164
        mr_mocks.contact_urns({"tel:123": False})

        self.assertUpdateSubmit(
            update_url,
            self.admin,
            {
                "name": "Bobby",
                "status": "B",
                "language": "spa",
                "groups": [testers.id],
                "urn__tel__0": "+593979111111",
                "new_scheme": "tel",
                "new_path": "123",
            },
            form_errors={"new_path": "Invalid phone number. Ensure number includes country code."},
            object_unchanged=contact,
        )

        # update all fields (removes second tel URN, adds a new Facebook URN)
        self.assertUpdateSubmit(
            update_url,
            self.admin,
            {
                "name": "Bobby",
                "status": "B",
                "language": "spa",
                "groups": [testers.id],
                "urn__tel__0": "+593979333333",
                "urn__telegram__2": "78686776",
                "new_scheme": "facebook",
                "new_path": "9898989",
            },
            success_status=200,
        )

        contact.refresh_from_db()
        self.assertEqual("Bobby", contact.name)
        self.assertEqual(Contact.STATUS_BLOCKED, contact.status)
        self.assertEqual("spa", contact.language)
        self.assertEqual({testers}, set(contact.get_groups()))
        self.assertEqual(
            ["tel:+593979333333", "telegram:78686776", "facebook:9898989"],
            [u.identity for u in contact.urns.order_by("-priority")],
        )

        # for non-active contacts, shouldn't see groups on form
        self.assertUpdateFetch(
            update_url,
            [self.editor, self.admin],
            form_fields={
                "name": "Bobby",
                "status": "B",
                "language": "spa",
                "new_scheme": None,
                "new_path": None,
                "urn__tel__0": "+593979333333",
                "urn__telegram__1": "78686776",
                "urn__facebook__2": "9898989",
            },
        )

        # try to update with invalid URNs
        mr_mocks.contact_urns({"tel:456": "invalid path component", "facebook:xxxxx": "invalid path component"})

        self.assertUpdateSubmit(
            update_url,
            self.admin,
            {
                "name": "Bobby",
                "status": "B",
                "language": "spa",
                "groups": [],
                "urn__tel__0": "456",
                "urn__facebook__2": "xxxxx",
            },
            form_errors={
                "urn__tel__0": "Invalid format.",
                "urn__facebook__2": "Invalid format.",
            },
            object_unchanged=contact,
        )

        # if contact has a language which is no longer a flow language, it should still be a valid option on the form
        contact.language = "kin"
        contact.save(update_fields=("language",))

        response = self.assertUpdateFetch(
            update_url,
            [self.admin],
            form_fields={
                "name": "Bobby",
                "status": "B",
                "language": "kin",
                "new_scheme": None,
                "new_path": None,
                "urn__tel__0": "+593979333333",
                "urn__telegram__1": "78686776",
                "urn__facebook__2": "9898989",
            },
        )
        self.assertContains(response, "Kinyarwanda")

        self.assertUpdateSubmit(
            update_url,
            self.admin,
            {
                "name": "Bobby",
                "status": "A",
                "language": "kin",
                "urn__tel__0": "+593979333333",
                "urn__telegram__1": "78686776",
                "urn__facebook__2": "9898989",
            },
            success_status=200,
        )

        contact.refresh_from_db()
        self.assertEqual("Bobby", contact.name)
        self.assertEqual(Contact.STATUS_ACTIVE, contact.status)
        self.assertEqual("kin", contact.language)

    @mock_mailroom
    def test_update_with_mailroom_error(self, mr_mocks):
        mr_mocks.exception(mailroom.RequestException("", "", MockResponse(400, '{"error": "Error updating contact"}')))

        contact = self.create_contact("Joe", phone="1234")

        self.login(self.admin)

        response = self.client.post(
            reverse("contacts.contact_update", args=[contact.id]),
            {"name": "Joe", "status": Contact.STATUS_ACTIVE, "language": "eng"},
        )

        self.assertFormError(
            response.context["form"], None, "An error occurred updating your contact. Please try again later."
        )

    @mock_mailroom
    def test_export(self, mr_mocks):
        export_url = reverse("contacts.contact_export")

        self.assertRequestDisallowed(export_url, [None, self.agent])
        response = self.assertUpdateFetch(export_url, [self.editor, self.admin], form_fields=("with_groups",))
        self.assertNotContains(response, "already an export in progress")

        # create a dummy export task so that we won't be able to export
        blocking_export = ContactExport.create(self.org, self.admin)

        response = self.client.get(export_url)
        self.assertContains(response, "already an export in progress")

        # check we can't submit in case a user opens the form and whilst another user is starting an export
        response = self.client.post(export_url, {})
        self.assertContains(response, "already an export in progress")
        self.assertEqual(1, Export.objects.count())

        # mark that one as finished so it's no longer a blocker
        blocking_export.status = Export.STATUS_COMPLETE
        blocking_export.save(update_fields=("status",))

        # try to export a group that is too big
        big_group = self.create_group("Big Group", contacts=[])
        mr_mocks.contact_export_preview(1_000_123)

        response = self.client.get(export_url + f"?g={big_group.uuid}")
        self.assertContains(response, "This group or search is too large to export.")

        response = self.client.post(
            export_url + f"?g={self.org.active_contacts_group.uuid}", {"with_groups": [big_group.id]}
        )
        self.assertEqual(200, response.status_code)

        export = Export.objects.exclude(id=blocking_export.id).get()
        self.assertEqual("contact", export.export_type)
        self.assertEqual(
            {"group_id": self.org.active_contacts_group.id, "search": None, "with_groups": [big_group.id]},
            export.config,
        )

    def test_scheduled(self):
        contact1 = self.create_contact("Joe", phone="+1234567890")
        contact2 = self.create_contact("Frank", phone="+1204567802")
        farmers = self.create_group("Farmers", contacts=[contact1, contact2])

        schedule_url = reverse("contacts.contact_scheduled", args=[contact1.uuid])

        self.assertRequestDisallowed(schedule_url, [None, self.agent, self.admin2])
        response = self.assertReadFetch(schedule_url, [self.user, self.editor, self.admin])
        self.assertEqual({"results": []}, response.json())

        # create a campaign and event fires for this contact
        campaign = Campaign.create(self.org, self.admin, "Reminders", farmers)
        joined = self.create_field("joined", "Joined On", value_type=ContactField.TYPE_DATETIME)
        event2_flow = self.create_flow("Reminder Flow")
        event1 = CampaignEvent.create_message_event(self.org, self.admin, campaign, joined, 2, unit="D", message="Hi")
        event2 = CampaignEvent.create_flow_event(self.org, self.admin, campaign, joined, 2, unit="D", flow=event2_flow)
        fire1 = EventFire.objects.create(event=event1, contact=contact1, scheduled=timezone.now() + timedelta(days=2))
        fire2 = EventFire.objects.create(event=event2, contact=contact1, scheduled=timezone.now() + timedelta(days=5))

        # create scheduled and regular broadcasts which send to both groups
        bcast1 = self.create_broadcast(
            self.admin,
            {"eng": {"text": "Hi again"}},
            contacts=[contact1, contact2],
            schedule=Schedule.create(self.org, timezone.now() + timedelta(days=3), Schedule.REPEAT_DAILY),
        )
        self.create_broadcast(self.admin, {"eng": {"text": "Bye"}}, contacts=[contact1, contact2])  # not scheduled

        # create scheduled trigger which this contact is explicitly added to
        trigger1_flow = self.create_flow("Favorites 1")
        trigger1 = Trigger.create(
            self.org,
            self.admin,
            trigger_type=Trigger.TYPE_SCHEDULE,
            flow=trigger1_flow,
            schedule=Schedule.create(self.org, timezone.now() + timedelta(days=4), Schedule.REPEAT_WEEKLY),
        )
        trigger1.contacts.add(contact1, contact2)

        # create scheduled trigger which this contact is added to via a group
        trigger2_flow = self.create_flow("Favorites 2")
        trigger2 = Trigger.create(
            self.org,
            self.admin,
            trigger_type=Trigger.TYPE_SCHEDULE,
            flow=trigger2_flow,
            schedule=Schedule.create(self.org, timezone.now() + timedelta(days=6), Schedule.REPEAT_MONTHLY),
        )
        trigger2.groups.add(farmers)

        # create scheduled trigger which this contact is explicitly added to... but also excluded from
        trigger3 = Trigger.create(
            self.org,
            self.admin,
            trigger_type=Trigger.TYPE_SCHEDULE,
            flow=self.create_flow("Favorites 3"),
            schedule=Schedule.create(self.org, timezone.now() + timedelta(days=4), Schedule.REPEAT_WEEKLY),
        )
        trigger3.contacts.add(contact1, contact2)
        trigger3.exclude_groups.add(farmers)

        response = self.requestView(schedule_url, self.admin)
        self.assertEqual(
            {
                "results": [
                    {
                        "type": "campaign_event",
                        "scheduled": fire1.scheduled.isoformat(),
                        "repeat_period": None,
                        "campaign": {"uuid": str(campaign.uuid), "name": "Reminders"},
                        "message": "Hi",
                    },
                    {
                        "type": "scheduled_broadcast",
                        "scheduled": bcast1.schedule.next_fire.astimezone(tzone.utc).isoformat(),
                        "repeat_period": "D",
                        "message": "Hi again",
                    },
                    {
                        "type": "scheduled_trigger",
                        "scheduled": trigger1.schedule.next_fire.astimezone(tzone.utc).isoformat(),
                        "repeat_period": "W",
                        "flow": {"uuid": str(trigger1_flow.uuid), "name": "Favorites 1"},
                    },
                    {
                        "type": "campaign_event",
                        "scheduled": fire2.scheduled.isoformat(),
                        "repeat_period": None,
                        "campaign": {"uuid": str(campaign.uuid), "name": "Reminders"},
                        "flow": {"uuid": str(event2_flow.uuid), "name": "Reminder Flow"},
                    },
                    {
                        "type": "scheduled_trigger",
                        "scheduled": trigger2.schedule.next_fire.astimezone(tzone.utc).isoformat(),
                        "repeat_period": "M",
                        "flow": {"uuid": str(trigger2_flow.uuid), "name": "Favorites 2"},
                    },
                ]
            },
            response.json(),
        )

        # fires for archived campaigns shouldn't appear
        campaign.archive(self.admin)

        response = self.requestView(schedule_url, self.admin)
        self.assertEqual(3, len(response.json()["results"]))

    @mock_mailroom
    def test_open_ticket(self, mr_mocks):
        contact = self.create_contact("Joe", phone="+593979000111")
        general = self.org.default_ticket_topic
        open_url = reverse("contacts.contact_open_ticket", args=[contact.id])

        self.assertRequestDisallowed(open_url, [None, self.user, self.agent, self.admin2])
        self.assertUpdateFetch(open_url, [self.editor, self.admin], form_fields=("topic", "assignee", "note"))

        # can submit with no assignee
        response = self.assertUpdateSubmit(open_url, self.admin, {"topic": general.id, "body": "Help", "assignee": ""})

        # should have new ticket
        ticket = contact.tickets.get()
        self.assertEqual(general, ticket.topic)
        self.assertIsNone(ticket.assignee)

        # and we're redirected to that ticket
        self.assertRedirect(response, f"/ticket/all/open/{ticket.uuid}/")

    @mock_mailroom
    def test_interrupt(self, mr_mocks):
        contact = self.create_contact("Joe", phone="+593979000111")
        other_org_contact = self.create_contact("Hans", phone="+593979123456", org=self.org2)

        read_url = reverse("contacts.contact_read", args=[contact.uuid])
        interrupt_url = reverse("contacts.contact_interrupt", args=[contact.id])

        self.login(self.admin)

        # no interrupt option if not in a flow
        response = self.client.get(read_url)
        self.assertNotContains(response, interrupt_url)

        MockSessionWriter(contact, self.create_flow("Test")).wait().save()
        MockSessionWriter(other_org_contact, self.create_flow("Test", org=self.org2)).wait().save()

        # now it's an option
        self.assertContentMenu(read_url, self.admin, ["Edit", "Start Flow", "Open Ticket", "Interrupt"])

        # can't interrupt if not logged in
        self.client.logout()
        response = self.client.post(interrupt_url, {"id": contact.id})
        self.assertLoginRedirect(response)

        self.login(self.user)

        # can't interrupt if just regular user
        response = self.client.post(interrupt_url, {"id": contact.id})
        self.assertLoginRedirect(response)

        self.login(self.admin)

        response = self.client.post(interrupt_url, {"id": contact.id})
        self.assertEqual(302, response.status_code)

        contact.refresh_from_db()
        self.assertIsNone(contact.current_flow)

        # can't interrupt contact in other org
        restore_url = reverse("contacts.contact_interrupt", args=[other_org_contact.id])
        response = self.client.post(restore_url, {"id": other_org_contact.id})
        self.assertLoginRedirect(response)

        # contact should be unchanged
        other_org_contact.refresh_from_db()
        self.assertIsNotNone(other_org_contact.current_flow)

    @mock_mailroom
    def test_delete(self, mr_mocks):
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

        self.assertEqual([call(self.org, [contact])], mr_mocks.calls["contact_deindex"])

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
        background_flow = self.create_flow("Background")
        archived_flow = self.create_flow("Archived")
        archived_flow.archive(self.admin)

        contact = self.create_contact("Joe", phone="+593979000111")
        start_url = f"{reverse('flows.flow_start', args=[])}?flow={sample_flows[0].id}&c={contact.uuid}"

        self.assertRequestDisallowed(start_url, [None, self.user, self.agent])
        response = self.assertUpdateFetch(start_url, [self.editor, self.admin], form_fields=["flow", "contact_search"])

        self.assertEqual([background_flow] + sample_flows, list(response.context["form"].fields["flow"].queryset))

        # try to submit without specifying a flow
        self.assertUpdateSubmit(
            start_url,
            self.admin,
            data={},
            form_errors={"flow": "This field is required.", "contact_search": "This field is required."},
            object_unchanged=contact,
        )

        # submit with flow...
        contact_search = dict(query=f"uuid='{contact.uuid}'", advanced=True)
        self.assertUpdateSubmit(
            start_url, self.admin, {"flow": background_flow.id, "contact_search": json.dumps(contact_search)}
        )

        # should now have a flow start
        start = FlowStart.objects.get()
        self.assertEqual(background_flow, start.flow)
        self.assertEqual(contact_search["query"], start.query)
        self.assertEqual({}, start.exclusions)

        # that has been queued to mailroom
        self.assertEqual("start_flow", mr_mocks.queued_batch_tasks[-1]["type"])


class ContactGroupTest(TembaTest):
    def setUp(self):
        super().setUp()

        self.joe = self.create_contact("Joe Blow", phone="123", fields={"age": "17", "gender": "male"})
        self.frank = self.create_contact("Frank Smith", phone="1234")
        self.mary = self.create_contact("Mary Mo", phone="345", fields={"age": "21", "gender": "female"})

    def test_create_manual(self):
        group = ContactGroup.create_manual(self.org, self.admin, "group one")

        self.assertEqual(group.org, self.org)
        self.assertEqual(group.name, "group one")
        self.assertEqual(group.created_by, self.admin)
        self.assertEqual(group.status, ContactGroup.STATUS_READY)

        # can't call update_query on a manual group
        self.assertRaises(AssertionError, group.update_query, "gender=M")

        # assert failure if group name is blank
        self.assertRaises(AssertionError, ContactGroup.create_manual, self.org, self.admin, "   ")

    @mock_mailroom
    def test_create_smart(self, mr_mocks):
        age = self.org.fields.get(key="age")
        gender = self.org.fields.get(key="gender")

        # create a dynamic group using a query
        query = '(Age < 18 and gender = "male") or (Age > 18 and gender = "female")'

        group = ContactGroup.create_smart(self.org, self.admin, "Group two", query)
        group.refresh_from_db()

        self.assertEqual(query, group.query)
        self.assertEqual({age, gender}, set(group.query_fields.all()))
        self.assertEqual(ContactGroup.STATUS_INITIALIZING, group.status)

        # update group query
        mr_mocks.contact_parse_query("age > 18 and name ~ Mary", cleaned='age > 18 AND name ~ "Mary"')
        group.update_query("age > 18 and name ~ Mary")
        group.refresh_from_db()

        self.assertEqual(group.query, 'age > 18 AND name ~ "Mary"')
        self.assertEqual(set(group.query_fields.all()), {age})
        self.assertEqual(group.status, ContactGroup.STATUS_INITIALIZING)

        # try to update group query to something invalid
        mr_mocks.exception(mailroom.QueryValidationException("no valid", "syntax"))
        with self.assertRaises(ValueError):
            group.update_query("age ~ Mary")

        # can't create a dynamic group with empty query
        self.assertRaises(AssertionError, ContactGroup.create_smart, self.org, self.admin, "Empty", "")

        # can't create a dynamic group with id attribute
        self.assertRaises(ValueError, ContactGroup.create_smart, self.org, self.admin, "Bose", "id = 123")

        # dynamic group should not have remove to group button
        self.login(self.admin)
        filter_url = reverse("contacts.contact_filter", args=[group.uuid])
        self.client.get(filter_url)

        # put group back into evaluation state
        group.status = ContactGroup.STATUS_EVALUATING
        group.save(update_fields=("status",))

        # dynamic groups should get their own icon
        self.assertEqual(group.get_attrs(), {"icon": "group_smart"})

        # can't update query again while it is in this state
        with self.assertRaises(AssertionError):
            group.update_query("age = 18")

    def test_get_or_create(self):
        group = ContactGroup.get_or_create(self.org, self.user, "first")
        self.assertEqual(group.name, "first")
        self.assertFalse(group.is_smart)

        # name look up is case insensitive
        self.assertEqual(ContactGroup.get_or_create(self.org, self.user, "FIRST"), group)

        # fetching by id shouldn't modify original group
        self.assertEqual(ContactGroup.get_or_create(self.org, self.user, "Kigali", uuid=group.uuid), group)

        group.refresh_from_db()
        self.assertEqual(group.name, "first")

    @mock_mailroom
    def test_get_groups(self, mr_mocks):
        manual = ContactGroup.create_manual(self.org, self.admin, "Static")
        deleted = ContactGroup.create_manual(self.org, self.admin, "Deleted")
        deleted.is_active = False
        deleted.save()

        open_tickets = self.org.groups.get(name="Open Tickets")
        females = ContactGroup.create_smart(self.org, self.admin, "Females", "gender=F")
        males = ContactGroup.create_smart(self.org, self.admin, "Males", "gender=M")
        ContactGroup.objects.filter(id=males.id).update(status=ContactGroup.STATUS_READY)

        self.assertEqual(set(ContactGroup.get_groups(self.org)), {open_tickets, manual, females, males})
        self.assertEqual(set(ContactGroup.get_groups(self.org, manual_only=True)), {manual})
        self.assertEqual(set(ContactGroup.get_groups(self.org, ready_only=True)), {open_tickets, manual, males})

    def test_get_unique_name(self):
        self.assertEqual("Testers", ContactGroup.get_unique_name(self.org, "Testers"))

        # ensure checking against existing groups is case-insensitive
        self.create_group("TESTERS", contacts=[])

        self.assertEqual("Testers 2", ContactGroup.get_unique_name(self.org, "Testers"))
        self.assertEqual("Testers", ContactGroup.get_unique_name(self.org2, "Testers"))  # different org

        self.create_group("Testers 2", contacts=[])

        self.assertEqual("Testers 3", ContactGroup.get_unique_name(self.org, "Testers"))

        # ensure we don't exceed the name length limit
        self.create_group("X" * 64, contacts=[])

        self.assertEqual(f"{'X' * 62} 2", ContactGroup.get_unique_name(self.org, "X" * 64))

    @mock_mailroom
    def test_member_count(self, mr_mocks):
        group = self.create_group("Cool kids")
        group.contacts.add(self.joe, self.frank)

        self.assertEqual(ContactGroup.objects.get(pk=group.pk).get_member_count(), 2)

        group.contacts.add(self.mary)

        self.assertEqual(ContactGroup.objects.get(pk=group.pk).get_member_count(), 3)

        group.contacts.remove(self.mary)

        self.assertEqual(ContactGroup.objects.get(pk=group.pk).get_member_count(), 2)

        # blocking a contact removes them from all user groups
        self.joe.block(self.user)

        group = ContactGroup.objects.get(pk=group.pk)
        self.assertEqual(group.get_member_count(), 1)
        self.assertEqual(set(group.contacts.all()), {self.frank})

        # releasing removes from all user groups
        self.frank.release(self.user)

        group = ContactGroup.objects.get(pk=group.pk)
        self.assertEqual(group.get_member_count(), 0)
        self.assertEqual(set(group.contacts.all()), set())

    @mock_mailroom
    def test_status_group_counts(self, mr_mocks):
        # start with no contacts
        for contact in Contact.objects.all():
            contact.release(self.admin)
            contact.delete()

        counts = Contact.get_status_counts(self.org)
        self.assertEqual(
            counts,
            {
                Contact.STATUS_ACTIVE: 0,
                Contact.STATUS_BLOCKED: 0,
                Contact.STATUS_STOPPED: 0,
                Contact.STATUS_ARCHIVED: 0,
            },
        )

        self.create_contact("Hannibal", phone="0783835001")
        face = self.create_contact("Face", phone="0783835002")
        ba = self.create_contact("B.A.", phone="0783835003")
        murdock = self.create_contact("Murdock", phone="0783835004")

        counts = Contact.get_status_counts(self.org)
        self.assertEqual(
            counts,
            {
                Contact.STATUS_ACTIVE: 4,
                Contact.STATUS_BLOCKED: 0,
                Contact.STATUS_STOPPED: 0,
                Contact.STATUS_ARCHIVED: 0,
            },
        )

        # call methods twice to check counts don't change twice
        murdock.block(self.user)
        murdock.block(self.user)
        face.block(self.user)
        ba.stop(self.user)
        ba.stop(self.user)

        counts = Contact.get_status_counts(self.org)
        self.assertEqual(
            counts,
            {
                Contact.STATUS_ACTIVE: 1,
                Contact.STATUS_BLOCKED: 2,
                Contact.STATUS_STOPPED: 1,
                Contact.STATUS_ARCHIVED: 0,
            },
        )

        murdock.release(self.user)
        murdock.release(self.user)
        face.restore(self.user)
        face.restore(self.user)
        ba.restore(self.user)
        ba.restore(self.user)

        # squash all our counts, this shouldn't affect our overall counts, but we should now only have 3
        squash_group_counts()
        self.assertEqual(ContactGroupCount.objects.all().count(), 3)

        counts = Contact.get_status_counts(self.org)
        self.assertEqual(
            counts,
            {
                Contact.STATUS_ACTIVE: 3,
                Contact.STATUS_BLOCKED: 0,
                Contact.STATUS_STOPPED: 0,
                Contact.STATUS_ARCHIVED: 0,
            },
        )

        # rebuild just our system contact group
        all_contacts = self.org.active_contacts_group
        ContactGroupCount.populate_for_group(all_contacts)

        # assert our count is correct
        self.assertEqual(all_contacts.get_member_count(), 3)
        self.assertEqual(ContactGroupCount.objects.filter(group=all_contacts).count(), 1)

    @mock_mailroom
    def test_release(self, mr_mocks):
        contact1 = self.create_contact("Bob", phone="+1234567111")
        contact2 = self.create_contact("Jim", phone="+1234567222")
        contact3 = self.create_contact("Jim", phone="+1234567333")
        group1 = self.create_group("Group One", contacts=[contact1, contact2])
        group2 = self.create_group("Group One", contacts=[contact2, contact3])

        t1 = timezone.now()

        # create a campaign based on group 1 - a hard dependency
        campaign = Campaign.create(self.org, self.admin, "Reminders", group1)
        joined = self.create_field("joined", "Joined On", value_type=ContactField.TYPE_DATETIME)
        event = CampaignEvent.create_message_event(self.org, self.admin, campaign, joined, 2, unit="D", message="Hi")
        EventFire.objects.create(event=event, contact=self.joe, scheduled=timezone.now() + timedelta(days=2))
        campaign.is_archived = True
        campaign.save()

        # create scheduled and regular broadcasts which send to both groups
        schedule = Schedule.create(self.org, timezone.now(), Schedule.REPEAT_DAILY)
        bcast1 = self.create_broadcast(self.admin, {"eng": {"text": "Hi"}}, groups=[group1, group2], schedule=schedule)
        bcast2 = self.create_broadcast(self.admin, {"eng": {"text": "Hi"}}, groups=[group1, group2])

        # group still has a hard dependency so can't be released
        with self.assertRaises(AssertionError):
            group1.release(self.admin)

        campaign.delete()

        group1.release(self.admin)
        group1.refresh_from_db()

        self.assertFalse(group1.is_active)
        self.assertTrue(group1.name.startswith("deleted-"))
        self.assertEqual(0, EventFire.objects.count())  # event fires will have been deleted
        self.assertEqual({group2}, set(bcast1.groups.all()))  # removed from scheduled broadcast
        self.assertEqual({group1, group2}, set(bcast2.groups.all()))  # regular broadcast unchanged

        self.assertEqual(set(), set(group1.contacts.all()))
        self.assertEqual({contact2, contact3}, set(group2.contacts.all()))  # unchanged

        # check that contacts who were in the group have had their modified_on times updated
        contact1.refresh_from_db()
        contact2.refresh_from_db()
        contact3.refresh_from_db()
        self.assertGreater(contact1.modified_on, t1)
        self.assertGreater(contact2.modified_on, t1)
        self.assertLess(contact3.modified_on, t1)  # unchanged


class ContactGroupCRUDLTest(TembaTest, CRUDLTestMixin):
    def setUp(self):
        super().setUp()

        self.joe = self.create_contact("Joe Blow", phone="123")
        self.frank = self.create_contact("Frank Smith", urns=["tel:1234", "twitter:hola"])

        self.joe_and_frank = self.create_group("Customers", [self.joe, self.frank])

        self.other_org_group = self.create_group("Customers", contacts=[], org=self.org2)

    @override_settings(ORG_LIMIT_DEFAULTS={"groups": 10})
    @mock_mailroom
    def test_create(self, mr_mocks):
        url = reverse("contacts.contactgroup_create")

        # can't create group as viewer
        self.login(self.user)
        response = self.client.post(url, {"name": "Spammers"})
        self.assertLoginRedirect(response)

        self.login(self.admin)

        # try to create a contact group whose name is only whitespace
        response = self.client.post(url, {"name": "  "})
        self.assertFormError(response.context["form"], "name", "This field is required.")

        # try to create a contact group whose name contains a disallowed character
        response = self.client.post(url, {"name": '"People"'})
        self.assertFormError(response.context["form"], "name", 'Cannot contain the character: "')

        # try to create a contact group whose name is too long
        response = self.client.post(url, {"name": "X" * 65})
        self.assertFormError(
            response.context["form"], "name", "Ensure this value has at most 64 characters (it has 65)."
        )

        # try to create with name that's already taken
        response = self.client.post(url, {"name": "Customers"})
        self.assertFormError(response.context["form"], "name", "Already used by another group.")

        # try to create with name that's already taken by a system group
        response = self.client.post(url, {"name": "blocked"})
        self.assertFormError(response.context["form"], "name", "Already used by another group.")

        # create with valid name (that will be trimmed)
        response = self.client.post(url, {"name": "first  "})
        self.assertNoFormErrors(response)
        ContactGroup.objects.get(org=self.org, name="first")

        # create a group with preselected contacts
        self.client.post(url, {"name": "Everybody", "preselected_contacts": f"{self.joe.id},{self.frank.id}"})
        group = ContactGroup.objects.get(org=self.org, name="Everybody")
        self.assertEqual(set(group.contacts.all()), {self.joe, self.frank})

        # create a dynamic group using a query
        self.client.post(url, {"name": "Frank", "group_query": "tel = 1234"})

        ContactGroup.objects.get(org=self.org, name="Frank", query="tel = 1234")

        for group in ContactGroup.objects.filter(is_system=False):
            group.release(self.admin)

        for i in range(10):
            ContactGroup.create_manual(self.org2, self.admin2, "group%d" % i)

        response = self.client.post(url, {"name": "People"})
        self.assertNoFormErrors(response)
        ContactGroup.objects.get(org=self.org, name="People")

        for group in ContactGroup.objects.filter(is_system=False):
            group.release(self.admin)

        for i in range(10):
            ContactGroup.create_manual(self.org, self.admin, "group%d" % i)

        self.assertEqual(10, ContactGroup.objects.filter(is_active=True, is_system=False).count())
        response = self.client.post(url, {"name": "People"})
        self.assertFormError(
            response.context["form"],
            "name",
            "This workspace has reached its limit of 10 groups. You must delete existing ones before you can create new ones.",
        )

    def test_create_disallow_duplicates(self):
        self.login(self.admin)

        self.client.post(reverse("contacts.contactgroup_create"), dict(name="First Group"))

        # assert it was created
        ContactGroup.objects.get(name="First Group")

        # try to create another group with the same name, but a dynamic query, should fail
        response = self.client.post(
            reverse("contacts.contactgroup_create"), dict(name="First Group", group_query="firsts")
        )
        self.assertFormError(response.context["form"], "name", "Already used by another group.")

        # try to create another group with same name, not dynamic, same thing
        response = self.client.post(
            reverse("contacts.contactgroup_create"), dict(name="First Group", group_query="firsts")
        )
        self.assertFormError(response.context["form"], "name", "Already used by another group.")

    @mock_mailroom
    def test_update(self, mr_mocks):
        url = reverse("contacts.contactgroup_update", args=[self.joe_and_frank.id])

        open_tickets = self.org.groups.get(name="Open Tickets")
        dynamic_group = self.create_group("Dynamic", query="tel is 1234")

        # can't create group as viewer
        self.login(self.user)
        response = self.client.post(url, dict(name="Spammers"))
        self.assertLoginRedirect(response)

        self.login(self.admin)

        # try to update name to only whitespace
        response = self.client.post(url, dict(name="   "))
        self.assertFormError(response.context["form"], "name", "This field is required.")

        # try to update name to contain a disallowed character
        response = self.client.post(url, dict(name='"People"'))
        self.assertFormError(response.context["form"], "name", 'Cannot contain the character: "')

        # update with valid name (that will be trimmed)
        response = self.client.post(url, dict(name="new name   "))
        self.assertNoFormErrors(response)

        self.joe_and_frank.refresh_from_db()
        self.assertEqual(self.joe_and_frank.name, "new name")

        # now try a dynamic group
        url = reverse("contacts.contactgroup_update", args=[dynamic_group.id])

        # mark our group as ready
        ContactGroup.objects.filter(id=dynamic_group.id).update(status=ContactGroup.STATUS_READY)

        # update both name and query, form should fail, because query is not parsable
        mr_mocks.exception(mailroom.QueryValidationException("error at !", "syntax"))
        response = self.client.post(url, dict(name="Frank", query="(!))!)"))
        self.assertFormError(response.context["form"], "query", "Invalid query syntax.")

        # try to update a group with an invalid query
        mr_mocks.exception(mailroom.QueryValidationException("error at >", "syntax"))
        response = self.client.post(url, dict(name="Frank", query="name <> some_name"))
        self.assertFormError(response.context["form"], "query", "Invalid query syntax.")

        # dependent on id
        response = self.client.post(url, dict(name="Frank", query="id = 123"))
        self.assertFormError(
            response.context["form"], "query", 'You cannot create a smart group based on "id" or "group".'
        )

        response = self.client.post(url, dict(name="Frank", query='twitter = "hola"'))

        self.assertNoFormErrors(response)

        dynamic_group.refresh_from_db()
        self.assertEqual(dynamic_group.query, 'twitter = "hola"')

        # mark our dynamic group as evaluating
        dynamic_group.status = ContactGroup.STATUS_EVALUATING
        dynamic_group.save(update_fields=("status",))

        # and check we can't change the query while that is the case
        response = self.client.post(url, dict(name="Frank", query='twitter = "hello"'))
        self.assertFormError(
            response.context["form"], "query", "You cannot update the query of a group that is evaluating."
        )

        # but can change the name
        response = self.client.post(url, dict(name="Frank2", query='twitter = "hola"'))
        self.assertNoFormErrors(response)

        dynamic_group.refresh_from_db()
        self.assertEqual(dynamic_group.name, "Frank2")

        # try to update a system group
        response = self.client.post(
            reverse("contacts.contactgroup_update", args=[open_tickets.id]), {"name": "new name"}
        )
        self.assertEqual(404, response.status_code)
        self.assertTrue(self.org.groups.filter(name="Open Tickets").exists())

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
        group = ContactGroup.objects.get(name="Cat Facts")

        campaign1 = Campaign.create(self.org, self.admin, "Planting Reminders", group)
        campaign2 = Campaign.create(self.org, self.admin, "Deleted", group)
        campaign2.is_active = False
        campaign2.save(update_fields=("is_active",))

        trigger1 = Trigger.create(
            self.org,
            self.admin,
            Trigger.TYPE_KEYWORD,
            flow,
            keywords=["test1"],
            match_type=Trigger.MATCH_FIRST_WORD,
            groups=[group],
        )
        trigger2 = Trigger.create(
            self.org,
            self.admin,
            Trigger.TYPE_KEYWORD,
            flow,
            keywords=["test2"],
            match_type=Trigger.MATCH_FIRST_WORD,
            exclude_groups=[group],
        )

        usages_url = reverse("contacts.contactgroup_usages", args=[group.uuid])

        self.assertRequestDisallowed(usages_url, [None, self.agent, self.admin2])
        response = self.assertReadFetch(usages_url, [self.user, self.editor, self.admin], context_object=group)

        self.assertEqual(
            {"flow": [flow], "campaign": [campaign1], "trigger": [trigger1, trigger2]},
            {t: list(qs) for t, qs in response.context["dependents"].items()},
        )

    def test_delete(self):
        # create a group which isn't used by anything
        group1 = self.create_group("Group 1", contacts=[])

        # create a group which is used only by a flow (soft dependency)
        group2 = self.create_group("Group 2", contacts=[])
        flow1 = self.create_flow("Flow 1")
        flow1.group_dependencies.add(group2)

        # create a group which is used by a flow (soft) and a scheduled trigger (soft)
        group3 = self.create_group("Group 3", contacts=[])
        flow2 = self.create_flow("Flow 2")
        flow2.group_dependencies.add(group3)
        schedule1 = Schedule.create(self.org, timezone.now() + timedelta(days=3), Schedule.REPEAT_DAILY)
        trigger1 = Trigger.create(
            self.org,
            self.admin,
            trigger_type=Trigger.TYPE_SCHEDULE,
            flow=flow2,
            keywords=["trigger1"],
            match_type=Trigger.MATCH_FIRST_WORD,
            groups=[group3.id],
            schedule=schedule1,
        )
        self.assertEqual(1, group3.triggers.count())
        self.assertEqual(trigger1, group3.triggers.get(is_active=True, keywords=trigger1.keywords))

        # create a group which is used by a flow (soft), a trigger (soft), and a campaign (hard dependency)
        group4 = self.create_group("Group 4", contacts=[])
        flow3 = self.create_flow("Flow 3")
        flow3.group_dependencies.add(group4)
        trigger2 = Trigger.create(
            self.org,
            self.admin,
            Trigger.TYPE_KEYWORD,
            flow3,
            keywords=["trigger2"],
            match_type=Trigger.MATCH_FIRST_WORD,
            groups=[group4],
        )
        campaign1 = Campaign.create(self.org, self.admin, "Planting Reminders", group4)

        delete_group1_url = reverse("contacts.contactgroup_delete", args=[group1.uuid])
        delete_group2_url = reverse("contacts.contactgroup_delete", args=[group2.uuid])
        delete_group3_url = reverse("contacts.contactgroup_delete", args=[group3.uuid])
        delete_group4_url = reverse("contacts.contactgroup_delete", args=[group4.uuid])

        self.assertRequestDisallowed(delete_group1_url, [None, self.user, self.agent, self.admin2])

        # a group with no dependents can be deleted
        response = self.assertDeleteFetch(delete_group1_url, [self.editor, self.admin])

        self.assertEqual({}, response.context["soft_dependents"])
        self.assertEqual({}, response.context["hard_dependents"])
        self.assertContains(response, "You are about to delete")
        self.assertContains(response, "There is no way to undo this. Are you sure?")

        self.assertDeleteSubmit(delete_group1_url, self.admin, object_deactivated=group1, success_status=200)

        # a group with only soft dependents can be deleted but we give warnings
        response = self.assertDeleteFetch(delete_group2_url, [self.editor])

        self.assertEqual({"flow"}, set(response.context["soft_dependents"].keys()))
        self.assertEqual({}, response.context["hard_dependents"])
        self.assertContains(response, "is used by the following items but can still be deleted:")
        self.assertContains(response, flow1.name)
        self.assertContains(response, "There is no way to undo this. Are you sure?")

        self.assertDeleteSubmit(delete_group2_url, self.admin, object_deactivated=group2, success_status=200)

        # check that the flow is now marked as having issues
        flow1.refresh_from_db()
        self.assertTrue(flow1.has_issues)
        self.assertNotIn(group2, flow1.field_dependencies.all())

        # a group with only soft dependents can be deleted but we give warnings
        response = self.assertDeleteFetch(delete_group3_url, [self.admin])

        self.assertEqual({"flow", "trigger"}, set(response.context["soft_dependents"].keys()))
        self.assertEqual({}, response.context["hard_dependents"])
        self.assertContains(response, "is used by the following items but can still be deleted:")
        self.assertContains(response, flow2.name)
        self.assertContains(response, f"Schedule → {flow2.name}")
        self.assertContains(response, "There is no way to undo this. Are you sure?")

        self.assertDeleteSubmit(delete_group3_url, self.admin, object_deactivated=group3, success_status=200)

        # check that the flow is now marked as having issues
        flow2.refresh_from_db()
        self.assertTrue(flow2.has_issues)
        self.assertNotIn(group3, flow2.field_dependencies.all())

        # check that the trigger is released
        trigger1.refresh_from_db()
        self.assertFalse(trigger1.is_active)

        # a group with hard dependents can't be deleted
        response = self.assertDeleteFetch(delete_group4_url, [self.admin])

        self.assertEqual({"flow", "trigger"}, set(response.context["soft_dependents"].keys()))
        self.assertEqual({"campaign"}, set(response.context["hard_dependents"].keys()))
        self.assertContains(response, "can't be deleted as it is still used by the following items:")
        self.assertContains(response, campaign1.name)
        self.assertNotContains(response, "Delete")

        # check that the flow is not deleted
        flow3.refresh_from_db()
        self.assertTrue(flow3.is_active)

        # check that the trigger is not released
        trigger2.refresh_from_db()
        self.assertTrue(trigger2.is_active)

        # check that the campaign is not deleted
        campaign1.refresh_from_db()
        self.assertTrue(campaign1.is_active)


class ContactTest(TembaTest, CRUDLTestMixin):
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
        self.jim.release(self.user, deindex=False)

        # create contact in other org
        self.other_org_contact = self.create_contact(name="Fred", phone="+250768111222", org=self.org2)

    def create_campaign(self):
        # create a campaign with a future event and add joe
        self.farmers = self.create_group("Farmers", [self.joe])
        self.reminder_flow = self.create_flow("Reminder Flow")
        self.planting_date = self.create_field("planting_date", "Planting Date", value_type=ContactField.TYPE_DATETIME)
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

    def test_contact_notes(self):
        note_text = "This is note"

        # create 10 notes
        for i in range(10):
            self.joe.set_note(self.user, f"{note_text} {i+1}")

        notes = self.joe.notes.all().order_by("id")

        # we should only have five notes after pruning
        self.assertEqual(5, notes.count())

        # check that the oldest notes are the ones that were pruned
        self.assertEqual("This is note 6", notes.first().text)

    @mock_mailroom
    def test_block_and_stop(self, mr_mocks):
        self.joe.block(self.admin)
        self.joe.stop(self.admin)
        self.joe.restore(self.admin)

        self.assertEqual(
            [
                call(self.org, self.admin, [self.joe], [modifiers.Status(status="blocked")]),
                call(self.org, self.admin, [self.joe], [modifiers.Status(status="stopped")]),
                call(self.org, self.admin, [self.joe], [modifiers.Status(status="active")]),
            ],
            mr_mocks.calls["contact_modify"],
        )

    @mock_mailroom
    def test_open_ticket(self, mock_contact_modify):
        mock_contact_modify.return_value = {self.joe.id: {"contact": {}, "events": []}}

        ticket = self.joe.open_ticket(
            self.admin, topic=self.org.default_ticket_topic, assignee=self.agent, note="Looks sus"
        )

        self.assertEqual(self.org.default_ticket_topic, ticket.topic)
        self.assertEqual("Looks sus", ticket.events.get(event_type="O").note)

    @mock_mailroom
    def test_interrupt(self, mr_mocks):
        # noop when contact not in a flow
        self.assertFalse(self.joe.interrupt(self.admin))

        flow = self.create_flow("Test")
        MockSessionWriter(self.joe, flow).wait().save()

        self.assertTrue(self.joe.interrupt(self.admin))

    @mock_mailroom
    def test_release(self, mr_mocks):
        # create a contact with a message
        old_contact = self.create_contact("Jose", phone="+12065552000")
        self.create_incoming_msg(old_contact, "hola mundo")
        urn = old_contact.get_urn()
        self.create_channel_event(self.channel, urn.identity, ChannelEvent.TYPE_CALL_IN_MISSED)

        self.create_ticket(old_contact)

        ivr_flow = self.get_flow("ivr")
        msg_flow = self.get_flow("favorites_v13")

        self.create_incoming_call(msg_flow, old_contact)

        # steal his urn into a new contact
        contact = self.create_contact("Joe", urns=["twitter:tweettweet"], fields={"gender": "Male", "age": 40})
        urn.contact = contact
        urn.save(update_fields=("contact",))
        group = self.create_group("Test Group", contacts=[contact])

        contact2 = self.create_contact("Billy", urns=["tel:1234567"])

        # create scheduled and regular broadcasts which send to both contacts
        schedule = Schedule.create(self.org, timezone.now(), Schedule.REPEAT_DAILY)
        bcast1 = self.create_broadcast(
            self.admin, {"eng": {"text": "Test"}}, contacts=[contact, contact2], schedule=schedule
        )
        bcast2 = self.create_broadcast(self.admin, {"eng": {"text": "Test"}}, contacts=[contact, contact2])

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
        joined = self.create_field("joined", "Joined On", value_type=ContactField.TYPE_DATETIME)
        event = CampaignEvent.create_message_event(self.org, self.admin, campaign, joined, 2, unit="D", message="Hi")
        EventFire.objects.create(event=event, contact=contact, scheduled=timezone.now() + timedelta(days=2))

        self.create_incoming_call(msg_flow, contact)

        # give contact an open and a closed ticket
        self.create_ticket(contact)
        self.create_ticket(contact, closed_on=timezone.now())

        self.assertEqual(1, group.contacts.all().count())
        self.assertEqual(1, contact.calls.all().count())
        self.assertEqual(2, contact.addressed_broadcasts.all().count())
        self.assertEqual(2, contact.urns.all().count())
        self.assertEqual(2, contact.runs.all().count())
        self.assertEqual(7, contact.msgs.all().count())
        self.assertEqual(2, len(contact.fields))
        self.assertEqual(1, contact.campaign_fires.count())

        self.assertEqual(2, TicketCount.get_all(self.org, Ticket.STATUS_OPEN))
        self.assertEqual(1, TicketCount.get_all(self.org, Ticket.STATUS_CLOSED))

        # first try releasing with _full_release patched so we can check the state of the contact before the task
        # to do a full release has kicked off
        with patch("temba.contacts.models.Contact._full_release"):
            contact.release(self.admin)

        self.assertEqual(2, contact.urns.all().count())
        for urn in contact.urns.all():
            UUID(urn.path, version=4)
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
        self.assertEqual(0, contact.calls.all().count())
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
        self.assertEqual(0, old_contact.calls.all().count())
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
        flow = self.create_flow("Test")
        msg1 = self.create_incoming_msg(self.joe, "Test 1")
        msg2 = self.create_incoming_msg(self.joe, "Test 2", flow=flow)
        msg3 = self.create_incoming_msg(self.joe, "Test 3", visibility="A")
        label = self.create_label("Interesting")
        label.toggle_label([msg1, msg2, msg3], add=True)
        static_group = self.create_group("Just Joe", [self.joe])

        msg_counts = SystemLabel.get_counts(self.org)
        self.assertEqual(1, msg_counts[SystemLabel.TYPE_INBOX])
        self.assertEqual(1, msg_counts[SystemLabel.TYPE_FLOWS])
        self.assertEqual(1, msg_counts[SystemLabel.TYPE_ARCHIVED])

        self.assertEqual(
            Contact.get_status_counts(self.org),
            {
                Contact.STATUS_ACTIVE: 4,
                Contact.STATUS_BLOCKED: 0,
                Contact.STATUS_STOPPED: 0,
                Contact.STATUS_ARCHIVED: 0,
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
            Contact.get_status_counts(self.org),
            {
                Contact.STATUS_ACTIVE: 3,
                Contact.STATUS_BLOCKED: 0,
                Contact.STATUS_STOPPED: 1,
                Contact.STATUS_ARCHIVED: 0,
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
            Contact.get_status_counts(self.org),
            {
                Contact.STATUS_ACTIVE: 3,
                Contact.STATUS_BLOCKED: 1,
                Contact.STATUS_STOPPED: 0,
                Contact.STATUS_ARCHIVED: 0,
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
            Contact.get_status_counts(self.org),
            {
                Contact.STATUS_ACTIVE: 3,
                Contact.STATUS_BLOCKED: 0,
                Contact.STATUS_STOPPED: 0,
                Contact.STATUS_ARCHIVED: 1,
            },
        )

        self.joe.restore(self.admin)

        # check that joe is now neither blocked or stopped
        self.joe = Contact.objects.get(pk=self.joe.pk)
        self.assertEqual(Contact.STATUS_ACTIVE, self.joe.status)
        self.assertTrue(self.joe.is_active)

        # and that he's been removed from the blocked group, and put back in the all and failed groups
        self.assertEqual(
            Contact.get_status_counts(self.org),
            {
                Contact.STATUS_ACTIVE: 4,
                Contact.STATUS_BLOCKED: 0,
                Contact.STATUS_STOPPED: 0,
                Contact.STATUS_ARCHIVED: 0,
            },
        )

        self.joe.release(self.user)

        # check that joe has been released (doesn't change his status)
        self.joe = Contact.objects.get(pk=self.joe.pk)
        self.assertEqual(Contact.STATUS_ACTIVE, self.joe.status)
        self.assertFalse(self.joe.is_active)

        self.assertEqual(
            Contact.get_status_counts(self.org),
            {
                Contact.STATUS_ACTIVE: 3,
                Contact.STATUS_BLOCKED: 0,
                Contact.STATUS_STOPPED: 0,
                Contact.STATUS_ARCHIVED: 0,
            },
        )

        # joe's messages should be inactive, blank and have no labels
        self.assertEqual(0, Msg.objects.filter(contact=self.joe, visibility="V").count())
        self.assertEqual(0, Msg.objects.filter(contact=self.joe).exclude(text="").count())
        self.assertEqual(0, label.msgs.count())

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
            Contact.get_status_counts(self.org),
            {
                Contact.STATUS_ACTIVE: 3,
                Contact.STATUS_BLOCKED: 0,
                Contact.STATUS_STOPPED: 0,
                Contact.STATUS_ARCHIVED: 0,
            },
        )

        # we don't let users undo releasing a contact... but if we have to do it for some reason
        self.joe.is_active = True
        self.joe.save(update_fields=("is_active",))

        # check joe goes into the appropriate groups
        self.assertEqual(
            Contact.get_status_counts(self.org),
            {
                Contact.STATUS_ACTIVE: 3,
                Contact.STATUS_BLOCKED: 0,
                Contact.STATUS_STOPPED: 1,
                Contact.STATUS_ARCHIVED: 0,
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

        with self.anonymous(self.org):
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
            self.assertEqual(["twitter:blow80", "tel:+250781111111"], [u.urn for u in getattr(self.joe, "_urns_cache")])
            self.assertEqual(["tel:+250782222222"], [u.urn for u in self.frank.get_urns()])
            self.assertEqual([], [u.urn for u in self.billy.get_urns()])

    @mock_mailroom
    def test_bulk_inspect(self, mr_mocks):
        self.assertEqual({}, Contact.bulk_inspect([]))
        self.assertEqual(
            {
                self.joe: {
                    "urns": [
                        {
                            "channel": {"uuid": str(self.channel.uuid), "name": "Test Channel"},
                            "scheme": "tel",
                            "path": "+250781111111",
                            "display": "",
                        },
                        {"channel": None, "scheme": "twitter", "path": "blow80", "display": ""},
                    ]
                },
                self.billy: {"urns": []},
            },
            Contact.bulk_inspect([self.joe, self.billy]),
        )

    @mock_mailroom
    def test_omnibox(self, mr_mocks):
        omnibox_url = reverse("contacts.contact_omnibox")

        # add a group with members and an empty group
        self.create_field("gender", "Gender")
        open_tickets = self.org.groups.get(name="Open Tickets")
        joe_and_frank = self.create_group("Joe and Frank", [self.joe, self.frank])
        nobody = self.create_group("Nobody", [])

        men = self.create_group("Men", query="gender=M")
        ContactGroup.objects.filter(id=men.id).update(status=ContactGroup.STATUS_READY)

        # a group which is being re-evaluated and shouldn't appear in any omnibox results
        unready = self.create_group("Group being re-evaluated...", query="gender=M")
        unready.status = ContactGroup.STATUS_EVALUATING
        unready.save(update_fields=("status",))

        # Postgres will defer to strcoll for ordering which even for en_US.UTF-8 will return different results on OSX
        # and Ubuntu. To keep ordering consistent for this test, we don't let URNs start with +
        # (see http://postgresql.nabble.com/a-strange-order-by-behavior-td4513038.html)
        ContactURN.objects.filter(path__startswith="+").update(
            path=Substr("path", 2), identity=Concat(DbValue("tel:"), Substr("path", 2))
        )

        self.login(self.admin)

        def omnibox_request(query: str):
            response = self.client.get(omnibox_url + query)
            return response.json()["results"]

        # mock mailroom to return an error
        mr_mocks.exception(mailroom.QueryValidationException("ooh that doesn't look right", "syntax"))

        # error is swallowed and we show no results
        self.assertEqual([], omnibox_request("?search=-123`213"))

        # lookup specific contacts
        self.assertEqual(
            [
                {"id": str(self.billy.uuid), "name": "Billy Nophone", "type": "contact", "urn": ""},
                {"id": str(self.joe.uuid), "name": "Joe Blow", "type": "contact", "urn": "blow80"},
            ],
            omnibox_request(f"?c={self.joe.uuid},{self.billy.uuid}"),
        )

        # lookup specific groups
        self.assertEqual(
            [
                {"id": str(joe_and_frank.uuid), "name": "Joe and Frank", "type": "group", "count": 2},
                {"id": str(men.uuid), "name": "Men", "type": "group", "count": 0},
            ],
            omnibox_request(f"?g={joe_and_frank.uuid},{men.uuid}"),
        )

        # empty query just returns up to 25 groups A-Z
        with self.assertNumQueries(10):
            self.assertEqual(
                [
                    {"id": str(joe_and_frank.uuid), "name": "Joe and Frank", "type": "group", "count": 2},
                    {"id": str(men.uuid), "name": "Men", "type": "group", "count": 0},
                    {"id": str(nobody.uuid), "name": "Nobody", "type": "group", "count": 0},
                    {"id": str(open_tickets.uuid), "name": "Open Tickets", "type": "group", "count": 0},
                ],
                omnibox_request(""),
            )

        with self.assertNumQueries(13):
            mr_mocks.contact_search(query='name ~ "250" OR urn ~ "250"', total=2, contacts=[self.billy, self.frank])

            self.assertEqual(
                [
                    {"id": str(self.billy.uuid), "name": "Billy Nophone", "type": "contact", "urn": ""},
                    {"id": str(self.frank.uuid), "name": "Frank Smith", "type": "contact", "urn": "250782222222"},
                ],
                omnibox_request("?search=250"),
            )

        with self.assertNumQueries(14):
            mr_mocks.contact_search(query='name ~ "FRA" OR urn ~ "FRA"', total=1, contacts=[self.frank])

            self.assertEqual(
                [
                    {"id": str(joe_and_frank.uuid), "name": "Joe and Frank", "type": "group", "count": 2},
                    {"id": str(self.frank.uuid), "name": "Frank Smith", "type": "contact", "urn": "250782222222"},
                ],
                omnibox_request("?search=FRA"),
            )

        # specify type filter g (all groups)
        self.assertEqual(
            [
                {"id": str(joe_and_frank.uuid), "name": "Joe and Frank", "type": "group", "count": 2},
                {"id": str(men.uuid), "name": "Men", "type": "group", "count": 0},
                {"id": str(nobody.uuid), "name": "Nobody", "type": "group", "count": 0},
                {"id": str(open_tickets.uuid), "name": "Open Tickets", "type": "group", "count": 0},
            ],
            omnibox_request("?types=g"),
        )

        # specify type filter s (non-query groups)
        self.assertEqual(
            [
                {"id": str(joe_and_frank.uuid), "name": "Joe and Frank", "type": "group", "count": 2},
                {"id": str(nobody.uuid), "name": "Nobody", "type": "group", "count": 0},
            ],
            omnibox_request("?types=s"),
        )

        with self.anonymous(self.org):
            self.assertEqual(
                [
                    {"id": str(joe_and_frank.uuid), "name": "Joe and Frank", "type": "group", "count": 2},
                    {"id": str(men.uuid), "name": "Men", "type": "group", "count": 0},
                    {"id": str(nobody.uuid), "name": "Nobody", "type": "group", "count": 0},
                    {"id": str(open_tickets.uuid), "name": "Open Tickets", "type": "group", "count": 0},
                ],
                omnibox_request(""),
            )

            mr_mocks.contact_search(query='name ~ "Billy"', total=1, contacts=[self.billy])

            self.assertEqual(
                [
                    {"id": str(self.billy.uuid), "name": "Billy Nophone", "type": "contact"},
                ],
                omnibox_request("?search=Billy"),
            )

        # exclude blocked and stopped contacts
        self.joe.block(self.admin)
        self.frank.stop(self.admin)

        # lookup by contact uuids
        self.assertEqual(omnibox_request("?c=%s,%s" % (self.joe.uuid, self.frank.uuid)), [])

    def test_history(self):
        url = reverse("contacts.contact_history", args=[self.joe.uuid])

        kurt = self.create_contact("Kurt", phone="123123")
        self.joe.created_on = timezone.now() - timedelta(days=1000)
        self.joe.save(update_fields=("created_on",))

        self.create_broadcast(self.user, {"eng": {"text": "A beautiful broadcast"}}, contacts=[self.joe])
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

        # create an airtime transfer
        AirtimeTransfer.objects.create(
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
        sales = Topic.create(self.org, self.admin, "Sales")
        self.create_ticket(self.joe, opened_on=timezone.now(), closed_on=timezone.now())
        ticket = self.create_ticket(self.joe, topic=sales)

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

        # add a failed call
        Call.objects.create(
            contact=self.joe,
            status=Call.STATUS_ERRORED,
            error_reason=Call.ERROR_NOANSWER,
            channel=self.channel,
            org=self.org,
            contact_urn=self.joe.urns.all().first(),
            error_count=0,
        )

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
        s3.client().put_object(
            Bucket="test-sessions", Key="c/session.json", Body=io.BytesIO(json.dumps(s.output).encode())
        )
        FlowSession.objects.filter(id=s.id).update(output_url="http://minio:9000/test-sessions/c/session.json")

        # fetch our contact history
        self.login(self.admin)
        with self.assertNumQueries(27):
            response = self.client.get(url + "?limit=100")

        # history should include all messages in the last 90 days, the channel event, the call, and the flow run
        history = response.json()["events"]
        self.assertEqual(96, len(history))

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
        assertHistoryEvent(history, 4, "ticket_opened", ticket__topic__name="Sales")
        assertHistoryEvent(history, 5, "ticket_closed", ticket__topic__name="General")
        assertHistoryEvent(history, 6, "ticket_opened", ticket__topic__name="General")
        assertHistoryEvent(history, 7, "airtime_transferred", actual_amount="100.00")
        assertHistoryEvent(history, 8, "msg_created", msg__text="What is your favorite color?")
        assertHistoryEvent(history, 9, "flow_entered", flow__name="Colors")
        assertHistoryEvent(history, 10, "msg_received", msg__text="Message caption")
        assertHistoryEvent(
            history, 11, "msg_created", msg__text="A beautiful broadcast", created_by__email="viewer@nyaruka.com"
        )
        assertHistoryEvent(history, 12, "campaign_fired", campaign__name="Planting Reminders")
        assertHistoryEvent(history, -1, "msg_received", msg__text="Inbound message 11")

        # revert back to reading only from DB
        FlowSession.objects.filter(id=s.id).update(output_url=None)

        # can filter by ticket to only all ticket events from that ticket rather than some events from all tickets
        response = self.client.get(url + f"?ticket={ticket.uuid}&limit=100")
        history = response.json()["events"]
        assertHistoryEvent(history, 0, "ticket_assigned", assignee__id=self.admin.id)
        assertHistoryEvent(history, 1, "ticket_note_added", note="I have a bad feeling about this")
        assertHistoryEvent(history, 5, "channel_event", channel_event_type="mt_miss")
        assertHistoryEvent(history, 6, "ticket_opened", ticket__topic__name="Sales")
        assertHistoryEvent(history, 7, "airtime_transferred", actual_amount="100.00")

        # fetch next page
        before = datetime_to_timestamp(timezone.now() - timedelta(days=90))
        response = self.fetch_protected(url + "?limit=100&before=%d" % before, self.admin)
        self.assertFalse(response.json()["has_older"])

        # activity should include 11 remaining messages and the event fire
        history = response.json()["events"]
        self.assertEqual(12, len(history))
        assertHistoryEvent(history, 0, "msg_received", msg__text="Inbound message 10")
        assertHistoryEvent(history, 10, "msg_received", msg__text="Inbound message 0")
        assertHistoryEvent(history, 11, "msg_received", msg__text="Very old inbound message")

        response = self.fetch_protected(url + "?limit=100", self.admin)
        history = response.json()["events"]

        self.assertEqual(96, len(history))
        assertHistoryEvent(history, 8, "msg_created", msg__text="What is your favorite color?")

        # if a new message comes in
        self.create_incoming_msg(self.joe, "Newer message")
        response = self.fetch_protected(url, self.admin)

        # now we'll see the message that just came in first, followed by the call event
        history = response.json()["events"]
        assertHistoryEvent(history, 0, "msg_received", msg__text="Newer message")
        assertHistoryEvent(history, 1, "call_started", status="E", status_display="Errored (No Answer)")

        recent_start = datetime_to_timestamp(timezone.now() - timedelta(days=1))
        response = self.fetch_protected(url + "?limit=100&after=%s" % recent_start, self.admin)

        # with our recent flag on, should not see the older messages
        events = response.json()["events"]
        self.assertEqual(13, len(events))
        self.assertContains(response, "file.mp4")

        # can't view history of contact in another org
        hans = self.create_contact("Hans", urns=["twitter:hans"], org=self.org2)
        response = self.client.get(reverse("contacts.contact_history", args=[hans.uuid]))
        self.assertLoginRedirect(response)

        # invalid UUID should return 404
        response = self.client.get(reverse("contacts.contact_history", args=["bad-uuid"]))
        self.assertEqual(response.status_code, 404)

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
        history = response.json()["events"]
        self.assertEqual(100, len(history))

        # before date should not match our last activity, that only happens when we truncate
        resp_json = response.json()
        self.assertNotEqual(
            resp_json["next_before"],
            datetime_to_timestamp(iso8601.parse_date(resp_json["events"][-1]["created_on"])),
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
        assertHistoryEvent(history, 12, "msg_created", msg__text="What is your favorite color?")
        assertHistoryEvent(history, 13, "flow_entered")

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
        assertHistoryEvent(response.json()["events"], 0, "campaign_fired", campaign_event__id=self.message_event.id)

        # now try the proper max history to test truncation
        response = self.fetch_protected(url + "?before=%d" % datetime_to_timestamp(timezone.now()), self.admin)

        # our before should be the same as the last item
        resp_json = response.json()
        last_item_date = datetime_to_timestamp(iso8601.parse_date(resp_json["events"][-1]["created_on"]))
        self.assertEqual(resp_json["next_before"], last_item_date)

        # and our after should be 90 days earlier
        self.assertEqual(resp_json["next_after"], last_item_date - (90 * 24 * 60 * 60 * 1000 * 1000))
        self.assertEqual(50, len(resp_json["events"]))

        # and we should have a marker for older items
        self.assertTrue(resp_json["has_older"])

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

        history_url = reverse("contacts.contact_history", args=[self.joe.uuid])
        self.login(self.user)

        response = self.client.get(history_url)
        self.assertEqual(200, response.status_code)

        resp_json = response.json()
        self.assertEqual(9, len(resp_json["events"]))
        self.assertEqual(
            [
                "flow_exited",
                "contact_name_changed",
                "contact_name_changed",
                "contact_language_changed",
                "contact_language_changed",
                "contact_field_changed",
                "contact_field_changed",
                "contact_urns_changed",
                "flow_entered",
            ],
            [e["type"] for e in resp_json["events"]],
        )

    def test_msg_status_badge(self):
        msg = self.create_outgoing_msg(self.joe, "This is an outgoing message")

        # wired has a primary color check
        msg.status = Msg.STATUS_WIRED
        self.assertIn('"check"', msg_status_badge(msg))
        self.assertIn("--color-primary-dark", msg_status_badge(msg))

        # delivered has a success check
        msg.status = Msg.STATUS_DELIVERED
        self.assertIn('"check"', msg_status_badge(msg))
        self.assertIn("--success-rgb", msg_status_badge(msg))

        # errored show retrying icon
        msg.status = Msg.STATUS_ERRORED
        self.assertIn('"retry"', msg_status_badge(msg))

        # failed messages show an x
        msg.status = Msg.STATUS_FAILED
        self.assertIn('"x"', msg_status_badge(msg))

    def test_get_scheduled_messages(self):
        just_joe = self.create_group("Just Joe", [self.joe])

        self.assertEqual(0, self.joe.get_scheduled_broadcasts().count())

        broadcast = self.create_broadcast(self.admin, {"eng": {"text": "Hello"}}, contacts=[self.frank])
        self.assertEqual(0, self.joe.get_scheduled_broadcasts().count())

        broadcast.contacts.add(self.joe)

        self.assertEqual(0, self.joe.get_scheduled_broadcasts().count())

        schedule_time = timezone.now() + timedelta(days=2)
        broadcast.schedule = Schedule.create(self.org, schedule_time, Schedule.REPEAT_NEVER)
        broadcast.save(update_fields=("schedule",))

        self.assertEqual(self.joe.get_scheduled_broadcasts().count(), 1)
        self.assertIn(broadcast, self.joe.get_scheduled_broadcasts())

        broadcast.contacts.remove(self.joe)
        self.assertEqual(0, self.joe.get_scheduled_broadcasts().count())

        broadcast.groups.add(just_joe)
        self.assertEqual(self.joe.get_scheduled_broadcasts().count(), 1)
        self.assertIn(broadcast, self.joe.get_scheduled_broadcasts())

        broadcast.groups.remove(just_joe)
        self.assertEqual(0, self.joe.get_scheduled_broadcasts().count())

    def test_update_urns_field(self):
        update_url = reverse("contacts.contact_update", args=[self.joe.pk])

        # we have a field to add new urns
        response = self.fetch_protected(update_url, self.admin)
        self.assertEqual(self.joe, response.context["object"])
        self.assertContains(response, "Add Connection")

        # no field to add new urns for anon org
        with self.anonymous(self.org):
            response = self.fetch_protected(update_url, self.admin)
            self.assertEqual(self.joe, response.context["object"])
            self.assertNotContains(response, "Add Connection")

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

        mr_mocks.exception(mailroom.QueryValidationException("mismatched input at <EOF>", "syntax"))

        # bogus query
        response = self.client.get(search_url + '?search=name="notclosed')
        results = response.json()
        self.assertEqual("Invalid query syntax.", results["error"])
        self.assertEqual(0, results["total"])

        # if we query a field, it should show up in our field dict
        age = self.create_field("age", "Age", ContactField.TYPE_NUMBER)

        mr_mocks.contact_search("age>32", cleaned='age > 32"', contacts=[self.frank], fields=[age])

        response = self.client.get(search_url + "?search=age>32")
        results = response.json()
        self.assertEqual("Age", results["fields"][str(age.uuid)]["label"])

    @mock_mailroom
    def test_update_status(self, mr_mocks):
        self.login(self.admin)

        self.assertEqual(Contact.STATUS_ACTIVE, self.joe.status)

        for status, _ in Contact.STATUS_CHOICES:
            self.client.post(reverse("contacts.contact_update", args=[self.joe.id]), {"status": status})

            self.joe.refresh_from_db()
            self.assertEqual(status, self.joe.status)

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

    @mock_mailroom
    def test_bulk_modify_with_no_contacts(self, mr_mocks):
        Contact.bulk_modify(self.admin, [], [modifiers.Language(language="spa")])

        # just a NOOP
        self.assertEqual([], mr_mocks.calls["contact_modify"])

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
        self.assertEqual(self.joe.fields, {dog_uuid: {"text": "23.00", "number": 23}})

        # numeric field value
        self.set_contact_field(self.joe, "dog", "37.27903")
        self.joe.refresh_from_db()
        self.assertEqual(self.joe.fields, {dog_uuid: {"text": "37.27903", "number": Decimal("37.27903")}})

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
        self.assertEqual(self.joe.fields, {cat_uuid: {"text": "Rwanda > Kigali City", "state": "Rwanda > Kigali City"}})
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

    def test_field_values(self):
        self.setUpLocations()

        registration_field = self.create_field(
            "registration_date", "Registration Date", value_type=ContactField.TYPE_DATETIME
        )
        weight_field = self.create_field("weight", "Weight", value_type=ContactField.TYPE_NUMBER)
        color_field = self.create_field("color", "Color", value_type=ContactField.TYPE_TEXT)
        state_field = self.create_field("state", "State", value_type=ContactField.TYPE_STATE)

        # none value instances
        self.assertEqual(self.joe.get_field_serialized(weight_field), None)
        self.assertEqual(self.joe.get_field_display(weight_field), "")
        self.assertEqual(self.joe.get_field_serialized(registration_field), None)
        self.assertEqual(self.joe.get_field_display(registration_field), "")

        self.set_contact_field(self.joe, "registration_date", "2014-12-31T01:04:00Z")
        self.set_contact_field(self.joe, "weight", "75.888888")
        self.set_contact_field(self.joe, "color", "green")
        self.set_contact_field(self.joe, "state", "kigali city")

        self.assertEqual(self.joe.get_field_serialized(registration_field), "2014-12-31T03:04:00+02:00")

        self.assertEqual(self.joe.get_field_serialized(weight_field), "75.888888")
        self.assertEqual(self.joe.get_field_display(weight_field), "75.888888")

        self.set_contact_field(self.joe, "weight", "0")
        self.assertEqual(self.joe.get_field_serialized(weight_field), "0")
        self.assertEqual(self.joe.get_field_display(weight_field), "0")

        # passing something non-numeric to a decimal field
        self.set_contact_field(self.joe, "weight", "xxx")
        self.assertEqual(self.joe.get_field_serialized(weight_field), None)
        self.assertEqual(self.joe.get_field_display(weight_field), "")

        self.assertEqual(self.joe.get_field_serialized(state_field), "Rwanda > Kigali City")
        self.assertEqual(self.joe.get_field_display(state_field), "Kigali City")

        self.assertEqual(self.joe.get_field_serialized(color_field), "green")
        self.assertEqual(self.joe.get_field_display(color_field), "green")

        # can fetch proxy fields too
        created_on = self.org.fields.get(key="created_on")
        last_seen_on = self.org.fields.get(key="last_seen_on")

        self.assertEqual(self.joe.get_field_display(created_on), self.org.format_datetime(self.joe.created_on))
        self.assertEqual(self.joe.get_field_display(last_seen_on), "")

    def test_set_location_fields(self):
        self.setUpLocations()

        district_field = self.create_field("district", "District", value_type=ContactField.TYPE_DISTRICT)
        not_state_field = self.create_field("not_state", "Not State", value_type=ContactField.TYPE_TEXT)

        # add duplicate district in different states
        east_province = AdminBoundary.create(osm_id="R005", name="East Province", level=1, parent=self.country)
        AdminBoundary.create(osm_id="R004", name="Remera", level=2, parent=east_province)
        kigali = AdminBoundary.objects.get(name="Kigali City")
        AdminBoundary.create(osm_id="R003", name="Remera", level=2, parent=kigali)

        joe = Contact.objects.get(pk=self.joe.pk)
        self.set_contact_field(joe, "district", "Remera")

        # empty because it is ambiguous
        self.assertFalse(joe.get_field_value(district_field))

        state_field = self.create_field("state", "State", value_type=ContactField.TYPE_STATE)

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
        AdminBoundary.create(osm_id="3710377", name="Bichi", level=3, parent=district)

        self.create_field("state", "State", value_type=ContactField.TYPE_STATE)
        self.create_field("district", "District", value_type=ContactField.TYPE_DISTRICT)
        ward = self.create_field("ward", "Ward", value_type=ContactField.TYPE_WARD)

        jemila = self.create_contact(
            name="Jemila Alley",
            urns=["tel:123", "twitter:fulani_p"],
            fields={"state": "kano", "district": "bichi", "ward": "bichi"},
        )
        self.assertEqual(jemila.get_field_serialized(ward), "Rwanda > Kano > Bichi > Bichi")


class ContactURNTest(TembaTest):
    def setUp(self):
        super().setUp()

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

    def test_ensure_normalization(self):
        contact1 = self.create_contact("Bob", urns=["tel:+250788111111"])
        contact2 = self.create_contact("Jim", urns=["tel:+0788222222"])

        self.org.normalize_contact_tels()

        self.assertEqual("+250788111111", contact1.urns.get().path)
        self.assertEqual("+250788222222", contact2.urns.get().path)


class ContactFieldTest(TembaTest):
    def setUp(self):
        super().setUp()

        self.joe = self.create_contact(name="Joe Blow", phone="123")
        self.frank = self.create_contact(name="Frank Smith", phone="1234")

        self.contactfield_1 = self.create_field("first", "First", priority=10)
        self.contactfield_2 = self.create_field("second", "Second")
        self.contactfield_3 = self.create_field("third", "Third", priority=20)

        self.other_org_field = self.create_field("other", "Other", priority=10, org=self.org2)

    def test_get_or_create(self):
        # name can be generated
        field1 = ContactField.get_or_create(self.org, self.admin, "join_date")
        self.assertEqual("join_date", field1.key)
        self.assertEqual("Join Date", field1.name)
        self.assertEqual(ContactField.TYPE_TEXT, field1.value_type)
        self.assertFalse(field1.is_system)

        # or passed explicitly along with type
        field2 = ContactField.get_or_create(
            self.org, self.admin, "another", name="My Label", value_type=ContactField.TYPE_NUMBER
        )
        self.assertEqual("another", field2.key)
        self.assertEqual("My Label", field2.name)
        self.assertEqual(ContactField.TYPE_NUMBER, field2.value_type)

        # if there's an existing key with this key we get that with name and type updated
        field3 = ContactField.get_or_create(
            self.org, self.admin, "another", name="Updated Label", value_type=ContactField.TYPE_DATETIME
        )
        self.assertEqual(field2, field3)
        self.assertEqual("another", field3.key)
        self.assertEqual("Updated Label", field3.name)
        self.assertEqual(ContactField.TYPE_DATETIME, field3.value_type)

        field4 = ContactField.get_or_create(self.org, self.admin, "another", name="Updated Again Label")
        self.assertEqual(field3, field4)
        self.assertEqual("another", field4.key)
        self.assertEqual("Updated Again Label", field4.name)
        self.assertEqual(ContactField.TYPE_DATETIME, field4.value_type)  # unchanged

        # can't create with an invalid key
        for key in ContactField.RESERVED_KEYS:
            with self.assertRaises(ValueError):
                ContactField.get_or_create(self.org, self.admin, key, key, value_type=ContactField.TYPE_TEXT)

        # provided names are made unique
        field5 = ContactField.get_or_create(self.org, self.admin, "date_joined", name="join date")
        self.assertEqual("date_joined", field5.key)
        self.assertEqual("join date 2", field5.name)

        # and ignored if not valid
        field6 = ContactField.get_or_create(self.org, self.admin, "date_joined", name="  ")
        self.assertEqual(field5, field6)
        self.assertEqual("date_joined", field6.key)
        self.assertEqual("join date 2", field6.name)  # unchanged

        # same for creating a new field
        field7 = ContactField.get_or_create(self.org, self.admin, "new_key", name="  ")
        self.assertEqual("new_key", field7.key)
        self.assertEqual("New Key", field7.name)  # generated

    def test_contact_templatetag(self):
        ContactField.get_or_create(
            self.org, self.admin, "date_joined", name="join date", value_type=ContactField.TYPE_DATETIME
        )

        self.set_contact_field(self.joe, "first", "Starter")
        self.set_contact_field(self.joe, "date_joined", "01-01-2022 8:30")

        self.assertEqual(contact_field(self.joe, "first"), "Starter")
        self.assertEqual(
            contact_field(self.joe, "date_joined"),
            "<temba-date value='2022-01-01T08:30:00+02:00' display='date'></temba-date>",
        )
        self.assertEqual(contact_field(self.joe, "not_there"), "--")

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
        self.assertFalse(ContactField.is_valid_key("has"))  # can't be reserved key
        self.assertFalse(ContactField.is_valid_key("is"))
        self.assertFalse(ContactField.is_valid_key("fields"))
        self.assertFalse(ContactField.is_valid_key("urns"))
        self.assertFalse(ContactField.is_valid_key("a" * 37))  # too long

    def test_is_valid_name(self):
        self.assertTrue(ContactField.is_valid_name("Age"))
        self.assertTrue(ContactField.is_valid_name("Age Now 2"))
        self.assertFalse(ContactField.is_valid_name("Age_Now"))  # can't have punctuation
        self.assertFalse(ContactField.is_valid_name("âge"))  # a-z only

    @mock_mailroom
    def test_contact_field_list_sort_fields(self, mr_mocks):
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
        post_data = json.dumps({cf.key: index for index, cf in enumerate(org_fields.order_by("id"))})

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


class ContactFieldCRUDLTest(TembaTest, CRUDLTestMixin):
    def setUp(self):
        super().setUp()

        self.age = self.create_field("age", "Age", value_type="N", show_in_table=True)
        self.gender = self.create_field("gender", "Gender", value_type="T")
        self.state = self.create_field("state", "State", value_type="S")

        self.deleted = self.create_field("foo", "Foo")
        self.deleted.is_active = False
        self.deleted.save(update_fields=("is_active",))

        self.other_org_field = self.create_field("other", "Other", org=self.org2)

    def test_create(self):
        create_url = reverse("contacts.contactfield_create")

        self.assertRequestDisallowed(create_url, [None, self.user, self.agent])

        # for a deploy that doesn't have locations feature, don't show location field types
        with override_settings(FEATURES={}):
            response = self.assertCreateFetch(
                create_url,
                [self.editor, self.admin],
                form_fields=["name", "value_type", "show_in_table", "agent_access"],
            )
            self.assertEqual(
                [("T", "Text"), ("N", "Number"), ("D", "Date & Time")],
                response.context["form"].fields["value_type"].choices,
            )

        response = self.assertCreateFetch(
            create_url,
            [self.editor, self.admin],
            form_fields=["name", "value_type", "show_in_table", "agent_access"],
        )
        self.assertEqual(
            [("T", "Text"), ("N", "Number"), ("D", "Date & Time"), ("S", "State"), ("I", "District"), ("W", "Ward")],
            response.context["form"].fields["value_type"].choices,
        )

        # try to submit with empty name
        self.assertCreateSubmit(
            create_url,
            self.admin,
            {"name": "", "value_type": "T", "show_in_table": True, "agent_access": "E"},
            form_errors={"name": "This field is required."},
        )

        # try to submit with invalid name
        self.assertCreateSubmit(
            create_url,
            self.admin,
            {"name": "???", "value_type": "T", "show_in_table": True, "agent_access": "E"},
            form_errors={"name": "Can only contain letters, numbers and hypens."},
        )

        # try to submit with something that would be an invalid key
        self.assertCreateSubmit(
            create_url,
            self.admin,
            {"name": "HAS", "value_type": "T", "show_in_table": True, "agent_access": "E"},
            form_errors={"name": "Can't be a reserved word."},
        )

        # try to submit with name of existing field
        self.assertCreateSubmit(
            create_url,
            self.admin,
            {"name": "AGE", "value_type": "N", "show_in_table": True, "agent_access": "E"},
            form_errors={"name": "Must be unique."},
        )

        # submit with valid data
        self.assertCreateSubmit(
            create_url,
            self.admin,
            {"name": "Goats", "value_type": "N", "show_in_table": True, "agent_access": "E"},
            new_obj_query=ContactField.user_fields.filter(
                org=self.org, name="Goats", value_type="N", show_in_table=True, agent_access="E"
            ),
            success_status=200,
        )

        # it's also ok to create a field with the same name as a deleted field
        ContactField.user_fields.get(key="age").release(self.admin)

        self.assertCreateSubmit(
            create_url,
            self.admin,
            {"name": "Age", "value_type": "N", "show_in_table": True, "agent_access": "N"},
            new_obj_query=ContactField.user_fields.filter(
                org=self.org, name="Age", value_type="N", show_in_table=True, agent_access="N", is_active=True
            ),
            success_status=200,
        )

        # simulate an org which has reached the limit for fields
        with override_settings(ORG_LIMIT_DEFAULTS={"fields": 2}):
            self.assertCreateSubmit(
                create_url,
                self.admin,
                {"name": "Sheep", "value_type": "T", "show_in_table": True, "agent_access": "E"},
                form_errors={
                    "__all__": "This workspace has reached its limit of 2 fields. You must delete existing ones before you can create new ones."
                },
            )

    def test_update(self):
        update_url = reverse("contacts.contactfield_update", args=[self.age.key])

        self.assertRequestDisallowed(update_url, [None, self.user, self.agent, self.admin2])

        # for a deploy that doesn't have locations feature, don't show location field types
        with override_settings(FEATURES={}):
            response = self.assertUpdateFetch(
                update_url,
                [self.editor, self.admin],
                form_fields={"name": "Age", "value_type": "N", "show_in_table": True, "agent_access": "V"},
            )
            self.assertEqual(3, len(response.context["form"].fields["value_type"].choices))

        response = self.assertUpdateFetch(
            update_url,
            [self.editor, self.admin],
            form_fields={"name": "Age", "value_type": "N", "show_in_table": True, "agent_access": "V"},
        )
        self.assertEqual(6, len(response.context["form"].fields["value_type"].choices))

        # try submit without change
        self.assertUpdateSubmit(
            update_url,
            self.admin,
            {"name": "Age", "value_type": "N", "show_in_table": True, "agent_access": "V"},
            success_status=200,
        )

        # try to submit with empty name
        self.assertUpdateSubmit(
            update_url,
            self.admin,
            {"name": "", "value_type": "N", "show_in_table": True, "agent_access": "V"},
            form_errors={"name": "This field is required."},
            object_unchanged=self.age,
        )

        # try to submit with invalid name
        self.assertUpdateSubmit(
            update_url,
            self.admin,
            {"name": "???", "value_type": "N", "show_in_table": True, "agent_access": "V"},
            form_errors={"name": "Can only contain letters, numbers and hypens."},
            object_unchanged=self.age,
        )

        # try to submit with a name that is used by another field
        self.assertUpdateSubmit(
            update_url,
            self.admin,
            {"name": "GENDER", "value_type": "N", "show_in_table": True, "agent_access": "V"},
            form_errors={"name": "Must be unique."},
            object_unchanged=self.age,
        )

        # submit with different name, type and agent access
        self.assertUpdateSubmit(
            update_url,
            self.admin,
            {"name": "Age In Years", "value_type": "T", "show_in_table": False, "agent_access": "E"},
            success_status=200,
        )

        self.age.refresh_from_db()
        self.assertEqual("Age In Years", self.age.name)
        self.assertEqual("T", self.age.value_type)
        self.assertFalse(self.age.show_in_table)
        self.assertEqual("E", self.age.agent_access)

        # simulate an org which has reached the limit for fields - should still be able to update a field
        with override_settings(ORG_LIMIT_DEFAULTS={"fields": 2}):
            self.assertUpdateSubmit(
                update_url,
                self.admin,
                {"name": "Age 2", "value_type": "T", "show_in_table": True, "agent_access": "E"},
                success_status=200,
            )

        self.age.refresh_from_db()
        self.assertEqual("Age 2", self.age.name)

        # create a date field used in a campaign event
        registered = self.create_field("registered", "Registered", value_type="D")
        campaign = Campaign.create(self.org, self.admin, "Reminders", self.create_group("Farmers"))
        CampaignEvent.create_flow_event(
            self.org, self.admin, campaign, registered, offset=1, unit="W", flow=self.create_flow("Test")
        )

        update_url = reverse("contacts.contactfield_update", args=[registered.key])

        self.assertUpdateFetch(
            update_url,
            [self.editor, self.admin],
            form_fields={"name": "Registered", "value_type": "D", "show_in_table": False, "agent_access": "V"},
        )

        # try to submit with different type
        self.assertUpdateSubmit(
            update_url,
            self.admin,
            {"name": "Registered", "value_type": "T", "show_in_table": False, "agent_access": "V"},
            form_errors={"value_type": "Can't change type of date field being used by campaign events."},
            object_unchanged=registered,
        )

        # submit with only a different name
        self.assertUpdateSubmit(
            update_url,
            self.admin,
            {"name": "Registered On", "value_type": "D", "show_in_table": False, "agent_access": "V"},
            success_status=200,
        )

        registered.refresh_from_db()
        self.assertEqual("Registered On", registered.name)
        self.assertEqual("D", registered.value_type)
        self.assertFalse(registered.show_in_table)

    def test_list(self):
        list_url = reverse("contacts.contactfield_list")

        self.assertRequestDisallowed(list_url, [None, self.agent])
        self.assertListFetch(
            list_url, [self.user, self.editor, self.admin], context_objects=[self.age, self.gender, self.state]
        )
        self.assertContentMenu(list_url, self.user, [])
        self.assertContentMenu(list_url, self.admin, ["New Field"])

    def test_create_warnings(self):
        self.login(self.admin)
        create_url = reverse("contacts.contactfield_create")
        response = self.client.get(create_url)

        self.assertEqual(3, response.context["total_count"])
        self.assertEqual(250, response.context["total_limit"])
        self.assertNotContains(response, "You have reached the limit")
        self.assertNotContains(response, "You are approaching the limit")

        with override_settings(ORG_LIMIT_DEFAULTS={"fields": 10}):
            response = self.requestView(create_url, self.admin)

            self.assertContains(response, "You are approaching the limit")

        with override_settings(ORG_LIMIT_DEFAULTS={"fields": 3}):
            response = self.requestView(create_url, self.admin)

            self.assertContains(response, "You have reached the limit")

    @mock_mailroom
    def test_usages(self, mr_mocks):
        flow = self.get_flow("dependencies", name="Dependencies")
        field = ContactField.user_fields.filter(is_active=True, org=self.org, key="favorite_cat").get()
        field.value_type = ContactField.TYPE_DATETIME
        field.save(update_fields=("value_type",))

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

        usages_url = reverse("contacts.contactfield_usages", args=[field.key])

        self.assertRequestDisallowed(usages_url, [None, self.agent, self.admin2])
        response = self.assertReadFetch(usages_url, [self.user, self.editor, self.admin], context_object=field)

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

        delete_gender_url = reverse("contacts.contactfield_delete", args=[self.gender.key])
        delete_joined_url = reverse("contacts.contactfield_delete", args=[joined_on.key])
        delete_age_url = reverse("contacts.contactfield_delete", args=[self.age.key])

        self.assertRequestDisallowed(delete_gender_url, [None, self.user, self.agent, self.admin2])

        # a field with no dependents can be deleted
        response = self.assertDeleteFetch(delete_gender_url, [self.editor, self.admin])
        self.assertEqual({}, response.context["soft_dependents"])
        self.assertEqual({}, response.context["hard_dependents"])
        self.assertContains(response, "You are about to delete")
        self.assertContains(response, "There is no way to undo this. Are you sure?")

        self.assertDeleteSubmit(delete_gender_url, self.admin, object_deactivated=self.gender, success_status=200)

        # create the same field again
        self.gender = self.create_field("gender", "Gender", value_type="T")

        # since fields are queried by key name, try and delete it again
        # to make sure we aren't deleting the previous deleted field again
        self.assertDeleteSubmit(delete_gender_url, self.admin, object_deactivated=self.gender, success_status=200)
        self.gender.refresh_from_db()
        self.assertFalse(self.gender.is_active)

        # a field with only soft dependents can also be deleted but we give warnings
        response = self.assertDeleteFetch(delete_joined_url, [self.admin])
        self.assertEqual({"flow", "campaign_event"}, set(response.context["soft_dependents"].keys()))
        self.assertEqual({}, response.context["hard_dependents"])
        self.assertContains(response, "is used by the following items but can still be deleted:")
        self.assertContains(response, "Amazing Flow")
        self.assertContains(response, "There is no way to undo this. Are you sure?")

        self.assertDeleteSubmit(delete_joined_url, self.admin, object_deactivated=joined_on, success_status=200)

        # check that flow is now marked as having issues
        flow.refresh_from_db()
        self.assertTrue(flow.has_issues)
        self.assertNotIn(joined_on, flow.field_dependencies.all())

        # and that the campaign event is gone
        campaign_event.refresh_from_db()
        self.assertFalse(campaign_event.is_active)

        # a field with hard dependents can't be deleted
        response = self.assertDeleteFetch(delete_age_url, [self.admin])
        self.assertEqual({"flow"}, set(response.context["soft_dependents"].keys()))
        self.assertEqual({"group"}, set(response.context["hard_dependents"].keys()))
        self.assertContains(response, "can't be deleted as it is still used by the following items:")
        self.assertContains(response, "Amazing Group")
        self.assertNotContains(response, "Delete")


class URNTest(TembaTest):
    def test_facebook_urn(self):
        self.assertTrue(URN.validate("facebook:ref:asdf"))

    def test_instagram_urn(self):
        self.assertTrue(URN.validate("instagram:12345678901234567"))

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
        self.assertEqual(URN.to_parts("instagram:12345"), ("instagram", "12345", None, None))

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

        # facebook, telegram, vk and instagram URN paths must be integers
        self.assertTrue(URN.validate("telegram:12345678901234567"))
        self.assertFalse(URN.validate("telegram:abcdef"))
        self.assertTrue(URN.validate("facebook:12345678901234567"))
        self.assertFalse(URN.validate("facebook:abcdef"))
        self.assertTrue(URN.validate("vk:12345678901234567"))
        self.assertTrue(URN.validate("instagram:12345678901234567"))
        self.assertFalse(URN.validate("instagram:abcdef"))


class ContactImportTest(TembaTest):
    def test_parse_errors(self):
        # try to open an import that is completely empty
        with self.assertRaisesRegex(ValidationError, "Import file appears to be empty."):
            path = "media/test_imports/empty_all_rows.xlsx"  # No header row present either
            with open(path, "rb") as f:
                ContactImport.try_to_parse(self.org, f, path)

        def try_to_parse(name):
            path = f"media/test_imports/{name}"
            with open(path, "rb") as f:
                ContactImport.try_to_parse(self.org, f, path)

        # try to open an import that exceeds the record limit
        with patch("temba.contacts.models.ContactImport.MAX_RECORDS", 2):
            with self.assertRaisesRegex(ValidationError, r"Import files can contain a maximum of 2 records\."):
                try_to_parse("simple.xlsx")

        bad_files = [
            ("empty.xlsx", "Import file doesn't contain any records."),
            ("empty_header.xlsx", "Import file contains an empty header."),
            ("duplicate_urn.xlsx", "Import file contains duplicated contact URN 'tel:+250788382382' on row 4."),
            (
                "duplicate_uuid.xlsx",
                "Import file contains duplicated contact UUID 'f519ca1f-8513-49ba-8896-22bf0420dec7' on row 4.",
            ),
            ("invalid_scheme.xlsx", "Header 'URN:XXX' is not a valid URN type."),
            ("invalid_field_key.xlsx", "Header 'Field: #$^%' is not a valid field name."),
            ("reserved_field_key.xlsx", "Header 'Field:HAS' is not a valid field name."),
            ("no_urn_or_uuid.xlsx", "Import files must contain either UUID or a URN header."),
            ("uuid_only.xlsx", "Import files must contain columns besides UUID."),
            ("invalid.txt.xlsx", "Import file appears to be corrupted."),
        ]

        for imp_file, imp_error in bad_files:
            with self.assertRaises(ValidationError, msg=f"expected error in {imp_file}") as e:
                try_to_parse(imp_file)
            self.assertEqual(imp_error, e.exception.messages[0], f"error mismatch for {imp_file}")

    def test_extract_mappings(self):
        # try simple import in different formats
        for ext in ("xlsx",):
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
        imp = self.create_contact_import("media/test_imports/twitter_and_phone.xlsx")
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

        imp = self.create_contact_import("media/test_imports/missing_name_header.xlsx")
        self.assertEqual([{"header": "URN:Tel", "mapping": {"type": "scheme", "scheme": "tel"}}], imp.mappings)

        self.create_field("goats", "Num Goats", ContactField.TYPE_NUMBER)

        imp = self.create_contact_import("media/test_imports/extra_fields_and_group.xlsx")
        self.assertEqual(
            [
                {"header": "URN:Tel", "mapping": {"type": "scheme", "scheme": "tel"}},
                {"header": "Name", "mapping": {"type": "attribute", "name": "name"}},
                {"header": "language", "mapping": {"type": "attribute", "name": "language"}},
                {"header": "Status", "mapping": {"type": "attribute", "name": "status"}},
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
            imp.mappings[5],
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
                {
                    "_import_row": 2,
                    "name": "Eric Newcomer",
                    "urns": ["tel:+250788382382"],
                    "groups": [str(imp.group.uuid)],
                },
                {
                    "_import_row": 3,
                    "name": "NIC POTTIER",
                    "urns": ["tel:+250788383383"],
                    "groups": [str(imp.group.uuid)],
                },
                {
                    "_import_row": 4,
                    "name": "jen newcomer",
                    "urns": ["tel:+250788383385"],
                    "groups": [str(imp.group.uuid)],
                },
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
                "time_taken": matchers.Int(min=0),
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
                "time_taken": matchers.Int(min=0),
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
                "time_taken": matchers.Int(min=0),
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
                "time_taken": matchers.Int(min=0),
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
                    "_import_row": 2,
                    "name": "John Doe",
                    "language": "eng",
                    "status": "archived",
                    "urns": ["tel:+250788123123"],
                    "fields": {"goats": "1", "sheep": "0"},
                    "groups": [str(imp.group.uuid)],
                },
                {
                    "_import_row": 3,
                    "name": "Mary Smith",
                    "language": "spa",
                    "status": "blocked",
                    "urns": ["tel:+250788456456"],
                    "fields": {"goats": "3", "sheep": "5"},
                    "groups": [str(imp.group.uuid)],
                },
                {
                    "_import_row": 4,
                    "urns": ["tel:+250788456678"],
                    "groups": [str(imp.group.uuid)],
                },  # blank values ignored
            ],
            batch.specs,
        )

        imp = self.create_contact_import("media/test_imports/with_empty_rows.xlsx")
        imp.start()
        batch = imp.batches.get()  # single batch

        # row 2 nad 3 is skipped
        self.assertEqual(
            [
                {
                    "_import_row": 2,
                    "name": "John Doe",
                    "language": "eng",
                    "urns": ["tel:+250788123123"],
                    "fields": {"goats": "1", "sheep": "0"},
                    "groups": [str(imp.group.uuid)],
                },
                {
                    "_import_row": 5,
                    "name": "Mary Smith",
                    "language": "spa",
                    "urns": ["tel:+250788456456"],
                    "fields": {"goats": "3", "sheep": "5"},
                    "groups": [str(imp.group.uuid)],
                },
                {
                    "_import_row": 6,
                    "urns": ["tel:+250788456678"],
                    "groups": [str(imp.group.uuid)],
                },  # blank values ignored
            ],
            batch.specs,
        )

        imp = self.create_contact_import("media/test_imports/with_uuid.xlsx")
        imp.start()
        batch = imp.batches.get()
        self.assertEqual(
            [
                {
                    "_import_row": 2,
                    "uuid": "f519ca1f-8513-49ba-8896-22bf0420dec7",
                    "name": "Joe",
                    "groups": [str(imp.group.uuid)],
                },
                {
                    "_import_row": 3,
                    "uuid": "989975f0-3bff-43d6-82c8-a6bbc201c938",
                    "name": "Frank",
                    "groups": [str(imp.group.uuid)],
                },
            ],
            batch.specs,
        )

        # cells with -- mean explicit clearing of those values
        imp = self.create_contact_import("media/test_imports/explicit_clearing.xlsx")
        imp.start()
        batch = imp.batches.get()  # single batch

        self.assertEqual(
            {
                "_import_row": 4,
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
                    "_import_row": 2,
                    "uuid": "92faa753-6faa-474a-a833-788032d0b757",
                    "name": "Eric Newcomer",
                    "language": "eng",
                    "groups": [str(imp.group.uuid)],
                },
                {
                    "_import_row": 3,
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
                {"_import_row": 2, "name": "Eric Newcomer", "urns": ["tel:+%3F"], "groups": [str(imp.group.uuid)]},
                {
                    "_import_row": 3,
                    "name": "Nic Pottier",
                    "urns": ["tel:2345678901234567890"],
                    "groups": [str(imp.group.uuid)],
                },
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
                    "_import_row": 2,
                    "name": "Bob",
                    "urns": ["tel:+250788382001", "tel:+250788382002", "tel:+250788382003"],
                    "groups": [str(imp.group.uuid)],
                },
                {
                    "_import_row": 3,
                    "name": "Jim",
                    "urns": ["tel:+250788382004", "tel:+250788382005"],
                    "groups": [str(imp.group.uuid)],
                },
            ],
            batch.specs,
        )

    @mock_mailroom
    def test_batches_from_xlsx(self, mr_mocks):
        imp = self.create_contact_import("media/test_imports/simple.xlsx")
        imp.start()
        batch = imp.batches.get()

        self.assertEqual(
            [
                {
                    "_import_row": 2,
                    "name": "Eric Newcomer",
                    "urns": ["tel:+250788382382"],
                    "groups": [str(imp.group.uuid)],
                },
                {
                    "_import_row": 3,
                    "name": "NIC POTTIER",
                    "urns": ["tel:+250788383383"],
                    "groups": [str(imp.group.uuid)],
                },
                {
                    "_import_row": 4,
                    "name": "jen newcomer",
                    "urns": ["tel:+250788383385"],
                    "groups": [str(imp.group.uuid)],
                },
            ],
            batch.specs,
        )

    @mock_mailroom
    def test_batches_from_xlsx_with_formulas(self, mr_mocks):
        imp = self.create_contact_import("media/test_imports/formula_data.xlsx")
        imp.start()
        batch = imp.batches.get()

        self.assertEqual(
            [
                {
                    "_import_row": 2,
                    "fields": {"team": "Managers"},
                    "name": "John Smith",
                    "urns": ["tel:+12025550199"],
                    "groups": [str(imp.group.uuid)],
                },
                {
                    "_import_row": 3,
                    "fields": {"team": "Advisors"},
                    "name": "Mary Green",
                    "urns": ["tel:+14045550178"],
                    "groups": [str(imp.group.uuid)],
                },
            ],
            batch.specs,
        )

    @mock_mailroom
    def test_detect_spamminess(self, mr_mocks):
        imp = self.create_contact_import("media/test_imports/sequential_tels.xlsx")
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

        imp = self.create_contact_import("media/test_imports/sequential_tels.xlsx")
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
                    "_import_row": 2,
                    "uuid": "17c4388a-024f-4e67-937a-13be78a70766",
                    "fields": {
                        "a_number": "1234.5678",
                        "a_date": "2020-10-19T00:00:00+02:00",
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
        kgl = ZoneInfo("Africa/Kigali")

        tests = [
            ("", ""),
            (" Yes ", "Yes"),
            (1234, "1234"),
            (123.456, "123.456"),
            (date(2020, 9, 18), "2020-09-18"),
            (datetime(2020, 9, 18, 15, 45, 30, 0), "2020-09-18T15:45:30+02:00"),
            (datetime(2020, 9, 18, 15, 45, 30, 0).replace(tzinfo=kgl), "2020-09-18T15:45:30+02:00"),
        ]
        for test in tests:
            self.assertEqual(test[1], imp._parse_value(test[0], tz=kgl))

    def test_get_default_group_name(self):
        self.create_group("Testers", contacts=[])
        tests = [
            ("simple.xlsx", "Simple"),
            ("testers.xlsx", "Testers 2"),  # group called Testers already exists
            ("contact-imports.xlsx", "Contact Imports"),
            ("abc_@@é.xlsx", "Abc É"),
            ("a_@@é.xlsx", "Import"),  # would be too short
            (f"{'x' * 100}.xlsx", "Xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx"),  # truncated
        ]
        for test in tests:
            self.assertEqual(test[1], ContactImport(org=self.org, original_filename=test[0]).get_default_group_name())

    @mock_mailroom
    def test_delete(self, mr_mocks):
        imp = self.create_contact_import("media/test_imports/simple.xlsx")
        imp.start()
        imp.delete()

        self.assertEqual(0, ContactImport.objects.count())
        self.assertEqual(0, ContactImportBatch.objects.count())


class ContactImportCRUDLTest(TembaTest, CRUDLTestMixin):
    def test_create_and_preview(self):
        create_url = reverse("contacts.contactimport_create")

        self.assertRequestDisallowed(create_url, [None, self.user, self.agent])
        self.assertCreateFetch(create_url, [self.editor, self.admin], form_fields=["file"])

        # try posting with nothing
        response = self.client.post(create_url, {})
        self.assertFormError(response.context["form"], "file", "This field is required.")

        # try uploading an empty file
        response = self.client.post(create_url, {"file": self.upload("media/test_imports/empty.xlsx")})
        self.assertFormError(response.context["form"], "file", "Import file doesn't contain any records.")

        # try uploading a valid XLSX file
        response = self.client.post(create_url, {"file": self.upload("media/test_imports/simple.xlsx")})
        self.assertEqual(302, response.status_code)

        imp = ContactImport.objects.get()
        self.assertEqual(self.org, imp.org)
        self.assertEqual(3, imp.num_records)
        self.assertRegex(imp.file.name, rf"orgs/{self.org.id}/contact_imports/[\w-]{{36}}.xlsx$")
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
        imp = self.create_contact_import("media/test_imports/simple.xlsx")
        preview_url = reverse("contacts.contactimport_preview", args=[imp.id])
        read_url = reverse("contacts.contactimport_read", args=[imp.id])

        # create some groups
        self.create_group("Testers", contacts=[])
        doctors = self.create_group("Doctors", contacts=[])

        # try creating new group but not providing a name
        response = self.client.post(preview_url, {"add_to_group": True, "group_mode": "N", "new_group_name": "  "})
        self.assertFormError(response.context["form"], "new_group_name", "Required.")

        # try creating new group but providing an invalid name
        response = self.client.post(preview_url, {"add_to_group": True, "group_mode": "N", "new_group_name": '"Foo"'})
        self.assertFormError(response.context["form"], "new_group_name", "Invalid group name.")

        # try creating new group but providing a name of an existing group
        response = self.client.post(preview_url, {"add_to_group": True, "group_mode": "N", "new_group_name": "testERs"})
        self.assertFormError(response.context["form"], "new_group_name", "Already exists.")

        # try creating new group when we've already reached our group limit
        with override_settings(ORG_LIMIT_DEFAULTS={"groups": 2}):
            response = self.client.post(
                preview_url, {"add_to_group": True, "group_mode": "N", "new_group_name": "Import"}
            )
            self.assertFormError(response.context["form"], None, "This workspace has reached its limit of 2 groups.")

        # finally create new group...
        response = self.client.post(preview_url, {"add_to_group": True, "group_mode": "N", "new_group_name": "Import"})
        self.assertRedirect(response, read_url)

        new_group = ContactGroup.objects.get(name="Import")
        imp.refresh_from_db()
        self.assertEqual(new_group, imp.group)

        # existing group should not check for workspace limit
        imp = self.create_contact_import("media/test_imports/simple.xlsx")
        preview_url = reverse("contacts.contactimport_preview", args=[imp.id])
        read_url = reverse("contacts.contactimport_read", args=[imp.id])
        with override_settings(ORG_LIMIT_DEFAULTS={"groups": 2}):
            response = self.client.post(
                preview_url, {"add_to_group": True, "group_mode": "E", "existing_group": doctors.id}
            )
            self.assertRedirect(response, read_url)
            imp.refresh_from_db()
            self.assertEqual(doctors, imp.group)

    @mock_mailroom
    def test_using_existing_group(self, mr_mocks):
        self.login(self.admin)
        imp = self.create_contact_import("media/test_imports/simple.xlsx")
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
        self.assertFormError(response.context["form"], "existing_group", "Required.")

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

        self.assertRequestDisallowed(preview_url, [None, self.user, self.agent, self.admin2])

        # columns 4 and 5 are a non-existent field so will have controls to create a new one
        self.assertUpdateFetch(
            preview_url,
            [self.editor, self.admin],
            form_fields=[
                "add_to_group",
                "group_mode",
                "new_group_name",
                "existing_group",
                "column_5_include",
                "column_5_name",
                "column_5_value_type",
                "column_6_include",
                "column_6_name",
                "column_6_value_type",
            ],
        )

        # if including a new fields, can't use existing field name
        response = self.client.post(
            preview_url,
            {
                "column_5_include": True,
                "column_5_name": "Goats",
                "column_5_value_type": "N",
                "column_6_include": True,
                "column_6_name": "age",
                "column_6_value_type": "N",
                "add_to_group": False,
            },
        )
        self.assertEqual(1, len(response.context["form"].errors))
        self.assertFormError(response.context["form"], None, "Field name for 'Field:Sheep' matches an existing field.")

        # if including a new fields, can't repeat names
        response = self.client.post(
            preview_url,
            {
                "column_5_include": True,
                "column_5_name": "Goats",
                "column_5_value_type": "N",
                "column_6_include": True,
                "column_6_name": "goats",
                "column_6_value_type": "N",
                "add_to_group": False,
            },
        )
        self.assertEqual(1, len(response.context["form"].errors))
        self.assertFormError(response.context["form"], None, "Field name 'goats' is repeated.")

        # if including a new field, name can't be invalid
        response = self.client.post(
            preview_url,
            {
                "column_5_include": True,
                "column_5_name": "Goats",
                "column_5_value_type": "N",
                "column_6_include": True,
                "column_6_name": "#$%^@",
                "column_6_value_type": "N",
                "add_to_group": False,
            },
        )
        self.assertEqual(1, len(response.context["form"].errors))
        self.assertFormError(
            response.context["form"], None, "Field name for 'Field:Sheep' is invalid or a reserved word."
        )

        # or empty
        response = self.client.post(
            preview_url,
            {
                "column_5_include": True,
                "column_5_name": "Goats",
                "column_5_value_type": "N",
                "column_6_include": True,
                "column_6_name": "",
                "column_6_value_type": "T",
                "add_to_group": False,
            },
        )
        self.assertEqual(1, len(response.context["form"].errors))
        self.assertFormError(response.context["form"], None, "Field name for 'Field:Sheep' can't be empty.")

        # unless you're ignoring it
        response = self.client.post(
            preview_url,
            {
                "column_5_include": True,
                "column_5_name": "Goats",
                "column_5_value_type": "N",
                "column_6_include": False,
                "column_6_name": "",
                "column_6_value_type": "T",
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
                {"header": "Status", "mapping": {"type": "attribute", "name": "status"}},
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

        self.assertRequestDisallowed(read_url, [None, self.agent, self.admin2])
        self.assertReadFetch(read_url, [self.user, self.editor, self.admin], context_object=imp)


class ContactExportTest(TembaTest):
    def setUp(self):
        super().setUp()

        self.joe = self.create_contact(name="Joe Blow", phone="123")
        self.frank = self.create_contact(name="Frank Smith", phone="1234")

        self.contactfield_1 = self.create_field("first", "First", priority=10)
        self.contactfield_2 = self.create_field("second", "Second")
        self.contactfield_3 = self.create_field("third", "Third", priority=20)

    def _export(self, group, search="", with_groups=()):
        export = ContactExport.create(self.org, self.admin, group, search, with_groups=with_groups)
        with self.mockReadOnly(assert_models={Contact, ContactURN, ContactField}):
            export.perform()

        workbook = load_workbook(
            filename=default_storage.open(f"orgs/{self.org.id}/contact_exports/{export.uuid}.xlsx")
        )
        return workbook.worksheets, export

    @mock_mailroom
    def test_export(self, mr_mocks):
        # archive all our current contacts
        Contact.apply_action_block(self.admin, self.org.contacts.all())

        # make third a datetime
        self.contactfield_3.value_type = ContactField.TYPE_DATETIME
        self.contactfield_3.save()

        # start one of our contacts down it
        contact = self.create_contact(
            "Be\02n Haggerty",
            phone="+12067799294",
            fields={"first": "On\02e", "third": "20/12/2015 08:30"},
            last_seen_on=datetime(2020, 1, 1, 12, 0, 0, 0, tzinfo=tzone.utc),
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

        group1 = self.create_group("Poppin Tags", [contact, contact2])
        group2 = self.create_group("Dynamic", query="tel is 1234")
        group2.status = ContactGroup.STATUS_EVALUATING
        group2.save()

        # create orphaned URN in scheme that no contacts have a URN for
        ContactURN.objects.create(org=self.org, identity="line:12345", scheme="line", path="12345")

        def assertReimport(export):
            with default_storage.open(f"orgs/{self.org.id}/contact_exports/{export.uuid}.xlsx") as exp:
                with tempfile.NamedTemporaryFile(delete=False) as tmp:
                    tmp.write(exp.read())
                    tmp.close()

                    self.create_contact_import(tmp.name)

        with self.assertNumQueries(22):
            sheets, export = self._export(self.org.active_contacts_group, with_groups=[group1])
            self.assertEqual(2, export.num_records)
            self.assertEqual("C", export.status)
            self.assertExcelSheet(
                sheets[0],
                [
                    [
                        "Contact UUID",
                        "Name",
                        "Language",
                        "Status",
                        "Created On",
                        "Last Seen On",
                        "URN:Mailto",
                        "URN:Tel",
                        "URN:Telegram",
                        "URN:Twitter",
                        "Field:Third",
                        "Field:First",
                        "Field:Second",
                        "Group:Poppin Tags",
                    ],
                    [
                        contact.uuid,
                        "Ben Haggerty",
                        "",
                        "Active",
                        contact.created_on,
                        datetime(2020, 1, 1, 12, 0, 0, 0, tzinfo=tzone.utc),
                        "",
                        "+12067799294",
                        "",
                        "",
                        "20-12-2015 08:30",
                        "One",
                        "",
                        True,
                    ],
                    [
                        contact2.uuid,
                        "Adam Sumner",
                        "eng",
                        "Active",
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
                ],
                tz=self.org.timezone,
            )

        assertReimport(export)

        # check that notifications were created
        export = Export.objects.filter(export_type=ContactExport.slug).order_by("id").last()
        self.assertEqual(1, self.admin.notifications.filter(notification_type="export:finished", export=export).count())

        # change the order of the fields
        self.contactfield_2.priority = 15
        self.contactfield_2.save()

        with self.assertNumQueries(21):
            sheets, export = self._export(self.org.active_contacts_group, with_groups=[group1])
            self.assertEqual(2, export.num_records)
            self.assertEqual("C", export.status)
            self.assertExcelSheet(
                sheets[0],
                [
                    [
                        "Contact UUID",
                        "Name",
                        "Language",
                        "Status",
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
                        contact.uuid,
                        "Ben Haggerty",
                        "",
                        "Active",
                        contact.created_on,
                        datetime(2020, 1, 1, 12, 0, 0, 0, tzinfo=tzone.utc),
                        "",
                        "+12067799294",
                        "",
                        "",
                        "20-12-2015 08:30",
                        "",
                        "One",
                        True,
                    ],
                    [
                        contact2.uuid,
                        "Adam Sumner",
                        "eng",
                        "Active",
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
                ],
                tz=self.org.timezone,
            )

        assertReimport(export)

        # more contacts do not increase the queries
        contact3 = self.create_contact("Luol Deng", urns=["tel:+12078776655", "twitter:deng"])
        contact4 = self.create_contact("Stephen", urns=["tel:+12078778899", "twitter:stephen"])
        contact.urns.create(org=self.org, identity="tel:+12062233445", scheme="tel", path="+12062233445")

        # but should have additional Twitter and phone columns
        with self.assertNumQueries(21):
            sheets, export = self._export(self.org.active_contacts_group, with_groups=[group1])
            self.assertEqual(4, export.num_records)
            self.assertExcelSheet(
                sheets[0],
                [
                    [
                        "Contact UUID",
                        "Name",
                        "Language",
                        "Status",
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
                        "Group:Poppin Tags",
                    ],
                    [
                        contact.uuid,
                        "Ben Haggerty",
                        "",
                        "Active",
                        contact.created_on,
                        datetime(2020, 1, 1, 12, 0, 0, 0, tzinfo=tzone.utc),
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
                        contact2.uuid,
                        "Adam Sumner",
                        "eng",
                        "Active",
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
                        contact3.uuid,
                        "Luol Deng",
                        "",
                        "Active",
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
                        "Active",
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

        assertReimport(export)

        # export a specified group of contacts (only Ben and Adam are in the group)
        with self.assertNumQueries(21):
            sheets, export = self._export(group1, with_groups=[group1])
            self.assertExcelSheet(
                sheets[0],
                [
                    [
                        "Contact UUID",
                        "Name",
                        "Language",
                        "Status",
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
                        "Group:Poppin Tags",
                    ],
                    [
                        contact.uuid,
                        "Ben Haggerty",
                        "",
                        "Active",
                        contact.created_on,
                        datetime(2020, 1, 1, 12, 0, 0, 0, tzinfo=tzone.utc),
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
                        contact2.uuid,
                        "Adam Sumner",
                        "eng",
                        "Active",
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
                ],
                tz=self.org.timezone,
            )

        assertReimport(export)

        contact5 = self.create_contact("George", urns=["tel:+1234567777"], status=Contact.STATUS_STOPPED)

        # export a specified status group of contacts (Stopped)
        sheets, export = self._export(self.org.groups.get(name="Stopped"), with_groups=[group1])
        self.assertExcelSheet(
            sheets[0],
            [
                [
                    "Contact UUID",
                    "Name",
                    "Language",
                    "Status",
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
                    "Group:Poppin Tags",
                ],
                [
                    contact5.uuid,
                    "George",
                    "",
                    "Stopped",
                    contact5.created_on,
                    "",
                    "",
                    "1234567777",
                    "",
                    "",
                    "",
                    "",
                    "",
                    "",
                    False,
                ],
            ],
            tz=self.org.timezone,
        )

        # export a search
        mr_mocks.contact_export([contact2.id, contact3.id])
        with self.assertNumQueries(22):
            sheets, export = self._export(
                self.org.active_contacts_group, "name has adam or name has deng", with_groups=[group1]
            )
            self.assertExcelSheet(
                sheets[0],
                [
                    [
                        "Contact UUID",
                        "Name",
                        "Language",
                        "Status",
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
                        "Group:Poppin Tags",
                    ],
                    [
                        contact2.uuid,
                        "Adam Sumner",
                        "eng",
                        "Active",
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
                        contact3.uuid,
                        "Luol Deng",
                        "",
                        "Active",
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
                ],
                tz=self.org.timezone,
            )

            assertReimport(export)

        # export a search within a specified group of contacts
        mr_mocks.contact_export([contact.id])
        with self.assertNumQueries(20):
            sheets, export = self._export(group1, search="Hagg", with_groups=[group1])
            self.assertExcelSheet(
                sheets[0],
                [
                    [
                        "Contact UUID",
                        "Name",
                        "Language",
                        "Status",
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
                        "Group:Poppin Tags",
                    ],
                    [
                        contact.uuid,
                        "Ben Haggerty",
                        "",
                        "Active",
                        contact.created_on,
                        datetime(2020, 1, 1, 12, 0, 0, 0, tzinfo=tzone.utc),
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
                ],
                tz=self.org.timezone,
            )

        assertReimport(export)

        # now try with an anonymous org
        with self.anonymous(self.org):
            sheets, export = self._export(self.org.active_contacts_group, with_groups=[group1])
            self.assertExcelSheet(
                sheets[0],
                [
                    [
                        "ID",
                        "Scheme",
                        "Contact UUID",
                        "Name",
                        "Language",
                        "Status",
                        "Created On",
                        "Last Seen On",
                        "Field:Third",
                        "Field:Second",
                        "Field:First",
                        "Group:Poppin Tags",
                    ],
                    [
                        str(contact.id),
                        "tel",
                        contact.uuid,
                        "Ben Haggerty",
                        "",
                        "Active",
                        contact.created_on,
                        datetime(2020, 1, 1, 12, 0, 0, 0, tzinfo=tzone.utc),
                        "20-12-2015 08:30",
                        "",
                        "One",
                        True,
                    ],
                    [
                        str(contact2.id),
                        "tel",
                        contact2.uuid,
                        "Adam Sumner",
                        "eng",
                        "Active",
                        contact2.created_on,
                        "",
                        "",
                        "",
                        "",
                        True,
                    ],
                    [
                        str(contact3.id),
                        "tel",
                        contact3.uuid,
                        "Luol Deng",
                        "",
                        "Active",
                        contact3.created_on,
                        "",
                        "",
                        "",
                        "",
                        False,
                    ],
                    [
                        str(contact4.id),
                        "tel",
                        contact4.uuid,
                        "Stephen",
                        "",
                        "Active",
                        contact4.created_on,
                        "",
                        "",
                        "",
                        "",
                        False,
                    ],
                ],
                tz=self.org.timezone,
            )
            assertReimport(export)


class BackfillProxyFieldsTest(MigrationTest):
    app = "contacts"
    migrate_from = "0188_contactfield_is_proxy_alter_contactfield_is_system"
    migrate_to = "0189_backfill_proxy_fields"

    OLD_SYSTEM_FIELDS = [
        {"key": "id", "name": "ID", "value_type": "N"},
        {"key": "name", "name": "Name", "value_type": "T"},
        {"key": "created_on", "name": "Created On", "value_type": "D"},
        {"key": "language", "name": "Language", "value_type": "T"},
        {"key": "last_seen_on", "name": "Last Seen On", "value_type": "D"},
    ]

    def setUpBeforeMigration(self, apps):
        # make org 1 look like an org with the old system fields
        self.org.fields.all().delete()

        for spec in self.OLD_SYSTEM_FIELDS:
            self.org.fields.create(
                is_system=True,
                key=spec["key"],
                name=spec["name"],
                value_type=spec["value_type"],
                show_in_table=False,
                created_by=self.org.created_by,
                modified_by=self.org.modified_by,
            )

    def test_migration(self):
        self.assertEqual(
            {"created_on", "last_seen_on"}, set(self.org.fields.filter(is_system=True).values_list("key", flat=True))
        )
        self.assertEqual(
            {"created_on", "last_seen_on"}, set(self.org.fields.filter(is_proxy=True).values_list("key", flat=True))
        )
        self.assertEqual(
            {"created_on", "last_seen_on"}, set(self.org2.fields.filter(is_system=True).values_list("key", flat=True))
        )
        self.assertEqual(
            {"created_on", "last_seen_on"}, set(self.org2.fields.filter(is_proxy=True).values_list("key", flat=True))
        )
