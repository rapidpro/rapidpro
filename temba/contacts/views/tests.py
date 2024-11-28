from datetime import timedelta, timezone as tzone
from unittest.mock import call, patch

from django.test.utils import override_settings
from django.urls import reverse
from django.utils import timezone

from temba import mailroom
from temba.campaigns.models import Campaign, CampaignEvent, EventFire
from temba.flows.models import FlowStart
from temba.locations.models import AdminBoundary
from temba.orgs.models import Export, OrgRole
from temba.schedules.models import Schedule
from temba.tests import CRUDLTestMixin, MockResponse, TembaTest, mock_mailroom
from temba.tests.engine import MockSessionWriter
from temba.triggers.models import Trigger
from temba.utils import json
from temba.utils.views.mixins import TEMBA_MENU_SELECTION

from ..models import Contact, ContactExport, ContactField, ContactGroup, ContactImport


class ContactCRUDLTest(CRUDLTestMixin, TembaTest):
    def setUp(self):
        super().setUp()

        self.country = AdminBoundary.create(osm_id="171496", name="Rwanda", level=0)
        AdminBoundary.create(osm_id="1708283", name="Kigali", level=1, parent=self.country)

        self.create_field("age", "Age", value_type="N", show_in_table=True)
        self.create_field("home", "Home", value_type="S", show_in_table=True, priority=10)

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

        with self.assertNumQueries(16):
            response = self.client.get(list_url)

        self.assertEqual([frank, joe], list(response.context["object_list"]))
        self.assertIsNone(response.context["search_error"])
        self.assertEqual([], list(response.context["actions"]))
        self.assertContentMenu(list_url, self.user, ["Export"])

        active_contacts = self.org.active_contacts_group

        # fetch with spa flag
        response = self.client.get(list_url, content_type="application/json", HTTP_X_TEMBA_SPA="1")
        self.assertEqual(response.context["base_template"], "spa.html")

        mr_mocks.contact_search("age = 18", contacts=[frank])

        response = self.client.get(list_url + "?search=age+%3D+18")
        self.assertEqual(list(response.context["object_list"]), [frank])
        self.assertEqual(response.context["search"], "age = 18")
        self.assertEqual(response.context["save_dynamic_search"], True)
        self.assertIsNone(response.context["search_error"])
        self.assertEqual(
            [f.name for f in response.context["contact_fields"]], ["Home", "Age", "Last Seen On", "Created On"]
        )

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
    def test_group(self, mr_mocks):
        open_tickets = self.org.groups.get(name="Open Tickets")
        joe = self.create_contact("Joe", phone="123")
        frank = self.create_contact("Frank", phone="124")
        self.create_contact("Bob", phone="125")

        mr_mocks.contact_search("age > 40", contacts=[frank], total=1)

        group1 = self.create_group("Testers", contacts=[joe, frank])  # static group
        group2 = self.create_group("Oldies", query="age > 40")  # smart group
        group2.contacts.add(frank)
        group3 = self.create_group("Other Org", org=self.org2)

        group1_url = reverse("contacts.contact_group", args=[group1.uuid])
        group2_url = reverse("contacts.contact_group", args=[group2.uuid])
        group3_url = reverse("contacts.contact_group", args=[group3.uuid])
        open_tickets_url = reverse("contacts.contact_group", args=[open_tickets.uuid])

        self.assertRequestDisallowed(group1_url, [None, self.agent, self.admin2])
        response = self.assertReadFetch(group1_url, [self.user, self.editor, self.admin])

        self.assertEqual([frank, joe], list(response.context["object_list"]))
        self.assertEqual(["block", "unlabel", "send", "start-flow"], list(response.context["actions"]))
        self.assertEqual(
            [f.name for f in response.context["contact_fields"]], ["Home", "Age", "Last Seen On", "Created On"]
        )

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
        response = self.requestView(reverse("contacts.contact_group", args=["21343253"]), self.admin)
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

        self.assertRequestDisallowed(read_url, [None, self.agent])

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
        interrupt_url = reverse("contacts.contact_interrupt", args=[contact.uuid])

        self.login(self.admin)

        # shoud see start flow option
        response = self.client.get(read_url)
        self.assertContentMenu(read_url, self.admin, ["Edit", "Start Flow", "Open Ticket"])

        MockSessionWriter(contact, self.create_flow("Test")).wait().save()
        MockSessionWriter(other_org_contact, self.create_flow("Test", org=self.org2)).wait().save()

        # start option should be gone
        self.assertContentMenu(read_url, self.admin, ["Edit", "Open Ticket"])

        # can't interrupt if not logged in
        self.client.logout()
        response = self.client.post(interrupt_url)
        self.assertLoginRedirect(response)

        self.login(self.user)

        # can't interrupt if just regular user
        response = self.client.post(interrupt_url)
        self.assertLoginRedirect(response)

        self.login(self.admin)

        response = self.client.post(interrupt_url)
        self.assertEqual(302, response.status_code)

        contact.refresh_from_db()
        self.assertIsNone(contact.current_flow)

        # can't interrupt contact in other org
        other_contact_interrupt = reverse("contacts.contact_interrupt", args=[other_org_contact.uuid])
        response = self.client.post(other_contact_interrupt)
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
        manual = self.create_group("Customers", [self.joe, self.frank])
        smart = self.create_group("Dynamic", query="tel is 1234")
        open_tickets = self.org.groups.get(name="Open Tickets")

        update_url = reverse("contacts.contactgroup_update", args=[manual.id])

        self.assertRequestDisallowed(update_url, [None, self.user, self.agent, self.admin2])

        self.assertUpdateFetch(update_url, [self.editor, self.admin], form_fields=("name",))

        # try to update name to only whitespace
        self.assertUpdateSubmit(
            update_url,
            self.admin,
            {"name": "    "},
            form_errors={"name": "This field is required."},
            object_unchanged=manual,
        )

        # try to update name to contain a disallowed character
        self.assertUpdateSubmit(
            update_url,
            self.admin,
            {"name": '"People"'},
            form_errors={"name": 'Cannot contain the character: "'},
            object_unchanged=manual,
        )

        # update with valid name (that will be trimmed)
        self.assertUpdateSubmit(update_url, self.admin, {"name": "new name   "})

        manual.refresh_from_db()
        self.assertEqual(manual.name, "new name")

        # now try a smart group
        update_url = reverse("contacts.contactgroup_update", args=[smart.id])

        # mark our group as ready
        smart.status = ContactGroup.STATUS_READY
        smart.save(update_fields=("status",))

        self.assertUpdateFetch(update_url, [self.editor, self.admin], form_fields=("name", "query"))

        # simulate submitting an unparseable query
        mr_mocks.exception(mailroom.QueryValidationException("error at !", "syntax"))

        self.assertUpdateSubmit(
            update_url,
            self.admin,
            {"name": "Frank", "query": "(!))!)"},
            form_errors={"query": "Invalid query syntax."},
            object_unchanged=smart,
        )

        # or a query that depends on id
        self.assertUpdateSubmit(
            update_url,
            self.admin,
            {"name": "Frank", "query": "id = 123"},
            form_errors={"query": 'You cannot create a smart group based on "id" or "group".'},
            object_unchanged=smart,
        )

        # update with valid query
        self.assertUpdateSubmit(update_url, self.admin, {"name": "Frank", "query": 'twitter = "hola"'})

        smart.refresh_from_db()
        self.assertEqual(smart.query, 'twitter = "hola"')

        # mark our dynamic group as evaluating
        smart.status = ContactGroup.STATUS_EVALUATING
        smart.save(update_fields=("status",))

        # and check we can't change the query while that is the case
        self.assertUpdateSubmit(
            update_url,
            self.admin,
            {"name": "Frank", "query": 'twitter = "hello"'},
            form_errors={"query": "You cannot update the query of a group that is populating."},
            object_unchanged=smart,
        )

        # but can change the name
        self.assertUpdateSubmit(update_url, self.admin, {"name": "Frank2", "query": 'twitter = "hola"'})

        smart.refresh_from_db()
        self.assertEqual(smart.name, "Frank2")

        # try to update a system group
        response = self.requestView(reverse("contacts.contactgroup_update", args=[open_tickets.id]), self.admin)
        self.assertEqual(404, response.status_code)

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
        self.assertContentMenu(list_url, self.admin, ["New"])

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
