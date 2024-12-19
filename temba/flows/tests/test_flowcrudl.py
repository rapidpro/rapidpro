import io
from datetime import date, datetime, timedelta, timezone as tzone
from unittest.mock import patch

from django_redis import get_redis_connection

from django.test.utils import override_settings
from django.urls import reverse

from temba import mailroom
from temba.contacts.models import URN
from temba.flows.models import Flow, FlowLabel, FlowStart, FlowUserConflictException, ResultsExport
from temba.msgs.models import SystemLabel
from temba.orgs.models import Export
from temba.templates.models import TemplateTranslation
from temba.tests import CRUDLTestMixin, TembaTest, matchers, mock_mailroom
from temba.tests.base import get_contact_search
from temba.triggers.models import Trigger
from temba.utils.uuid import uuid4
from temba.utils.views.mixins import TEMBA_MENU_SELECTION


class FlowCRUDLTest(TembaTest, CRUDLTestMixin):
    def test_menu(self):
        menu_url = reverse("flows.flow_menu")

        FlowLabel.create(self.org, self.admin, "Important")

        self.assertRequestDisallowed(menu_url, [None, self.agent])
        self.assertPageMenu(
            menu_url,
            self.admin,
            [
                "Active",
                "Archived",
                "Globals",
                ("History", ["Starts", "Webhooks"]),
                ("Labels", ["Important (0)"]),
            ],
        )

    def test_create(self):
        create_url = reverse("flows.flow_create")
        self.create_flow("Registration")

        self.assertRequestDisallowed(create_url, [None, self.user, self.agent])
        response = self.assertCreateFetch(
            create_url,
            [self.editor, self.admin],
            form_fields=["name", "keyword_triggers", "flow_type", "base_language"],
        )

        # check flow type options
        self.assertEqual(
            [
                (Flow.TYPE_MESSAGE, "Messaging"),
                (Flow.TYPE_VOICE, "Phone Call"),
                (Flow.TYPE_BACKGROUND, "Background"),
            ],
            response.context["form"].fields["flow_type"].choices,
        )

        # try to submit without name or language
        self.assertCreateSubmit(
            create_url,
            self.admin,
            {"flow_type": "M"},
            form_errors={"name": "This field is required.", "base_language": "This field is required."},
        )

        # try to submit with a name that contains disallowed characters
        self.assertCreateSubmit(
            create_url,
            self.admin,
            {"name": '"Registration"', "flow_type": "M", "base_language": "eng"},
            form_errors={"name": 'Cannot contain the character: "'},
        )

        # try to submit with a name that is too long
        self.assertCreateSubmit(
            create_url,
            self.admin,
            {"name": "X" * 65, "flow_type": "M", "base_language": "eng"},
            form_errors={"name": "Ensure this value has at most 64 characters (it has 65)."},
        )

        # try to submit with a name that is already used
        self.assertCreateSubmit(
            create_url,
            self.admin,
            {"name": "Registration", "flow_type": "M", "base_language": "eng"},
            form_errors={"name": "Already used by another flow."},
        )

        response = self.assertCreateSubmit(
            create_url,
            self.admin,
            {"name": "Flow 1", "flow_type": "M", "base_language": "eng"},
            new_obj_query=Flow.objects.filter(org=self.org, flow_type="M", name="Flow 1"),
        )

        flow1 = Flow.objects.get(name="Flow 1")
        self.assertEqual(1, flow1.revisions.all().count())

        self.assertRedirect(response, reverse("flows.flow_editor", args=[flow1.uuid]))

    def test_create_with_keywords(self):
        create_url = reverse("flows.flow_create")

        # try creating a flow with invalid keywords
        self.assertCreateSubmit(
            create_url,
            self.admin,
            {
                "name": "Flow #1",
                "base_language": "eng",
                "keyword_triggers": ["toooooooooooooolong", "test"],
                "flow_type": Flow.TYPE_MESSAGE,
            },
            form_errors={
                "keyword_triggers": "Must be single words, less than 16 characters, containing only letters and numbers."
            },
        )

        # submit with valid keywords
        self.assertCreateSubmit(
            create_url,
            self.admin,
            {
                "name": "Flow 1",
                "base_language": "eng",
                "keyword_triggers": ["testing", "test"],
                "flow_type": Flow.TYPE_MESSAGE,
            },
            new_obj_query=Flow.objects.filter(org=self.org, name="Flow 1", flow_type="M"),
        )

        # check the created keyword trigger
        flow1 = Flow.objects.get(name="Flow 1")
        self.assertEqual(1, flow1.triggers.count())
        self.assertEqual(1, flow1.triggers.filter(trigger_type="K", keywords=["testing", "test"]).count())

        # try to create another flow with one of the same keywords
        self.assertCreateSubmit(
            create_url,
            self.admin,
            {
                "name": "Flow 2",
                "base_language": "eng",
                "keyword_triggers": ["test"],
                "flow_type": Flow.TYPE_MESSAGE,
            },
            form_errors={"keyword_triggers": '"test" is already used for another flow.'},
        )

        # add a group to the existing trigger
        group = self.create_group("Testers", contacts=[])
        flow1.triggers.get().groups.add(group)

        # and now it's no longer a conflict
        self.assertCreateSubmit(
            create_url,
            self.admin,
            {
                "name": "Flow 2",
                "base_language": "eng",
                "keyword_triggers": ["test"],
                "flow_type": Flow.TYPE_MESSAGE,
            },
            new_obj_query=Flow.objects.filter(org=self.org, name="Flow 2", flow_type="M"),
        )

        # check the created keyword triggers
        flow2 = Flow.objects.get(name="Flow 2")
        self.assertEqual([["test"]], list(flow2.triggers.order_by("id").values_list("keywords", flat=True)))

    def test_views(self):
        create_url = reverse("flows.flow_create")

        self.create_contact("Eric", phone="+250788382382")
        flow = self.create_flow("Test")

        # create a flow for another org
        other_flow = Flow.create(self.org2, self.admin2, "Flow2")

        # no login, no list
        response = self.client.get(reverse("flows.flow_list"))
        self.assertLoginRedirect(response)

        user = self.admin
        user.first_name = "Test"
        user.last_name = "Contact"
        user.save()
        self.login(user)

        self.assertContentMenu(reverse("flows.flow_list"), self.user, ["Export"])

        self.assertContentMenu(
            reverse("flows.flow_list"),
            self.admin,
            ["New Flow", "New Label", "Import", "Export"],
        )

        # list, should have only one flow (the one created in setUp)
        response = self.client.get(reverse("flows.flow_list"))
        self.assertEqual(1, len(response.context["object_list"]))

        # inactive list shouldn't have any flows
        response = self.client.get(reverse("flows.flow_archived"))
        self.assertEqual(0, len(response.context["object_list"]))

        # also shouldn't be able to view other flow
        response = self.client.get(reverse("flows.flow_editor", args=[other_flow.uuid]))
        self.assertEqual(404, response.status_code)

        # get our create page
        response = self.client.get(create_url)
        self.assertTrue(response.context["has_flows"])

        # create a new regular flow
        response = self.client.post(
            create_url, {"name": "Flow 1", "flow_type": Flow.TYPE_MESSAGE, "base_language": "eng"}
        )
        self.assertEqual(302, response.status_code)

        # check we've been redirected to the editor and we have a revision
        flow1 = Flow.objects.get(org=self.org, name="Flow 1")
        self.assertEqual(f"/flow/editor/{flow1.uuid}/", response.url)
        self.assertEqual(1, flow1.revisions.all().count())
        self.assertEqual(Flow.TYPE_MESSAGE, flow1.flow_type)
        self.assertEqual(10080, flow1.expires_after_minutes)

        # add a trigger on this flow
        trigger = Trigger.create(
            self.org,
            self.admin,
            Trigger.TYPE_KEYWORD,
            flow1,
            keywords=["unique"],
            match_type=Trigger.MATCH_FIRST_WORD,
        )

        # create a new voice flow
        response = self.client.post(
            create_url, {"name": "Voice Flow", "flow_type": Flow.TYPE_VOICE, "base_language": "eng"}
        )
        voice_flow = Flow.objects.get(org=self.org, name="Voice Flow")
        self.assertEqual(response.status_code, 302)
        self.assertEqual(voice_flow.flow_type, "V")

        # default expiration for voice is shorter
        self.assertEqual(voice_flow.expires_after_minutes, 5)

        # test flows with triggers
        # create a new flow with one unformatted keyword
        response = self.client.post(
            create_url,
            {
                "name": "Flow With Unformated Keyword Triggers",
                "keyword_triggers": ["this is", "it"],
                "base_language": "eng",
            },
        )
        self.assertFormError(
            response.context["form"],
            "keyword_triggers",
            "Must be single words, less than 16 characters, containing only letters and numbers.",
        )

        # create a new flow with one existing keyword
        response = self.client.post(
            create_url, {"name": "Flow With Existing Keyword Triggers", "keyword_triggers": ["this", "is", "unique"]}
        )
        self.assertFormError(response.context["form"], "keyword_triggers", '"unique" is already used for another flow.')

        # create another trigger so there are two in the way
        trigger = Trigger.create(
            self.org,
            self.admin,
            Trigger.TYPE_KEYWORD,
            flow1,
            keywords=["this"],
            match_type=Trigger.MATCH_FIRST_WORD,
        )

        response = self.client.post(
            create_url, {"name": "Flow With Existing Keyword Triggers", "keyword_triggers": ["this", "is", "unique"]}
        )
        self.assertFormError(
            response.context["form"], "keyword_triggers", '"this", "unique" are already used for another flow.'
        )
        trigger.delete()

        # create a new flow with keywords
        response = self.client.post(
            create_url,
            {
                "name": "Flow With Good Keyword Triggers",
                "base_language": "eng",
                "keyword_triggers": ["this", "is", "it"],
                "flow_type": Flow.TYPE_MESSAGE,
                "expires_after_minutes": 30,
            },
        )
        flow3 = Flow.objects.get(name="Flow With Good Keyword Triggers")

        # check we're being redirected to the editor view
        self.assertRedirect(response, reverse("flows.flow_editor", args=[flow3.uuid]))

        # can see results for a flow
        response = self.client.get(reverse("flows.flow_results", args=[flow.id]))
        self.assertEqual(200, response.status_code)

        # check flow listing
        response = self.client.get(reverse("flows.flow_list"))
        self.assertEqual(list(response.context["object_list"]), [flow3, voice_flow, flow1, flow])  # by saved_on

        # test update view
        response = self.client.post(reverse("flows.flow_update", args=[flow.id]))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.context["form"].fields), 5)
        self.assertIn("name", response.context["form"].fields)
        self.assertIn("keyword_triggers", response.context["form"].fields)
        self.assertIn("ignore_triggers", response.context["form"].fields)

        # test ivr flow creation
        self.channel.role = "SRCA"
        self.channel.save()

        response = self.client.post(
            create_url,
            {
                "name": "Message flow",
                "base_language": "eng",
                "expires_after_minutes": 5,
                "flow_type": Flow.TYPE_MESSAGE,
            },
        )
        msg_flow = Flow.objects.get(name="Message flow")

        self.assertEqual(302, response.status_code)
        self.assertEqual(msg_flow.flow_type, Flow.TYPE_MESSAGE)

        response = self.client.post(
            create_url,
            {"name": "Call flow", "base_language": "eng", "expires_after_minutes": 5, "flow_type": Flow.TYPE_VOICE},
        )
        call_flow = Flow.objects.get(name="Call flow")

        self.assertEqual(302, response.status_code)
        self.assertEqual(call_flow.flow_type, Flow.TYPE_VOICE)

        # test creating a flow with base language
        self.org.set_flow_languages(self.admin, ["eng"])

        response = self.client.post(
            create_url,
            {
                "name": "Language Flow",
                "expires_after_minutes": 5,
                "base_language": "eng",
                "flow_type": Flow.TYPE_MESSAGE,
            },
        )

        language_flow = Flow.objects.get(name="Language Flow")

        self.assertEqual(302, response.status_code)
        self.assertEqual(language_flow.base_language, "eng")

    def test_update_messaging_flow(self):
        flow = self.create_flow("Test")
        update_url = reverse("flows.flow_update", args=[flow.id])

        def assert_triggers(expected: list):
            actual = list(flow.triggers.filter(trigger_type="K", is_active=True).values("keywords", "is_archived"))
            self.assertCountEqual(actual, expected)

        self.assertRequestDisallowed(update_url, [None, self.user, self.agent, self.admin2])
        self.assertUpdateFetch(
            update_url,
            [self.editor, self.admin],
            form_fields={
                "name": "Test",
                "keyword_triggers": [],
                "expires_after_minutes": 10080,
                "ignore_triggers": False,
            },
        )

        # try to update with empty name
        self.assertUpdateSubmit(
            update_url,
            self.admin,
            {"name": "", "expires_after_minutes": 10, "ignore_triggers": True},
            form_errors={"name": "This field is required."},
            object_unchanged=flow,
        )

        # update all fields
        self.assertUpdateSubmit(
            update_url,
            self.admin,
            {
                "name": "New Name",
                "keyword_triggers": ["test", "help"],
                "expires_after_minutes": 10,
                "ignore_triggers": True,
            },
        )

        flow.refresh_from_db()
        self.assertEqual("New Name", flow.name)
        self.assertEqual(10, flow.expires_after_minutes)
        self.assertTrue(flow.ignore_triggers)

        assert_triggers([{"keywords": ["test", "help"], "is_archived": False}])

        # remove one keyword and add another
        self.assertUpdateSubmit(
            update_url,
            self.admin,
            {
                "name": "New Name",
                "keyword_triggers": ["help", "support"],
                "expires_after_minutes": 10,
                "ignore_triggers": True,
            },
        )

        assert_triggers(
            [
                {"keywords": ["test", "help"], "is_archived": True},
                {"keywords": ["help", "support"], "is_archived": False},
            ]
        )

        # put "test" keyword back and remove "support"
        self.assertUpdateSubmit(
            update_url,
            self.admin,
            {
                "name": "New Name",
                "keyword_triggers": ["test", "help"],
                "expires_after_minutes": 10,
                "ignore_triggers": True,
            },
        )

        assert_triggers(
            [
                {"keywords": ["test", "help"], "is_archived": False},
                {"keywords": ["help", "support"], "is_archived": True},
            ]
        )

        # add channel filter to active trigger
        support = flow.triggers.get(is_archived=False)
        support.channel = self.channel
        support.save(update_fields=("channel",))

        # re-adding "support" will now restore that trigger
        self.assertUpdateSubmit(
            update_url,
            self.admin,
            {
                "name": "New Name",
                "keyword_triggers": ["test", "help", "support"],
                "expires_after_minutes": 10,
                "ignore_triggers": True,
            },
        )

        assert_triggers(
            [
                {"keywords": ["test", "help"], "is_archived": False},
                {"keywords": ["help", "support"], "is_archived": False},
            ]
        )

    def test_update_voice_flow(self):
        flow = self.create_flow("IVR Test", flow_type=Flow.TYPE_VOICE)
        update_url = reverse("flows.flow_update", args=[flow.id])

        self.assertRequestDisallowed(update_url, [None, self.user, self.agent, self.admin2])
        self.assertUpdateFetch(
            update_url,
            [self.editor, self.admin],
            form_fields=["name", "keyword_triggers", "expires_after_minutes", "ignore_triggers", "ivr_retry"],
        )

        # try to update with an expires value which is only for messaging flows and an invalid retry value
        self.assertUpdateSubmit(
            update_url,
            self.admin,
            {"name": "New Name", "expires_after_minutes": 720, "ignore_triggers": True, "ivr_retry": 1234},
            form_errors={
                "expires_after_minutes": "Select a valid choice. 720 is not one of the available choices.",
                "ivr_retry": "Select a valid choice. 1234 is not one of the available choices.",
            },
            object_unchanged=flow,
        )

        # update name and contact creation option to be per login
        self.assertUpdateSubmit(
            update_url,
            self.admin,
            {
                "name": "New Name",
                "keyword_triggers": ["test", "help"],
                "expires_after_minutes": 10,
                "ignore_triggers": True,
                "ivr_retry": 30,
            },
        )

        flow.refresh_from_db()
        self.assertEqual("New Name", flow.name)
        self.assertEqual(10, flow.expires_after_minutes)
        self.assertTrue(flow.ignore_triggers)
        self.assertEqual(30, flow.metadata.get("ivr_retry"))
        self.assertEqual(1, flow.triggers.count())
        self.assertEqual(1, flow.triggers.filter(keywords=["test", "help"]).count())

        # check we still have that value after saving a new revision
        flow.save_revision(self.admin, flow.get_definition())
        self.assertEqual(30, flow.metadata["ivr_retry"])

    def test_update_surveyor_flow(self):
        flow = self.create_flow("Survey", flow_type=Flow.TYPE_SURVEY)
        update_url = reverse("flows.flow_update", args=[flow.id])

        # we should only see name and contact creation option on form
        self.assertRequestDisallowed(update_url, [None, self.user, self.agent, self.admin2])
        self.assertUpdateFetch(update_url, [self.editor, self.admin], form_fields=["name", "contact_creation"])

        # update name and contact creation option to be per login
        self.assertUpdateSubmit(update_url, self.admin, {"name": "New Name", "contact_creation": "login"})

        flow.refresh_from_db()
        self.assertEqual("New Name", flow.name)
        self.assertEqual("login", flow.metadata.get("contact_creation"))

    def test_update_background_flow(self):
        flow = self.create_flow("Background", flow_type=Flow.TYPE_BACKGROUND)
        update_url = reverse("flows.flow_update", args=[flow.id])

        # we should only see name on form
        self.assertRequestDisallowed(update_url, [None, self.user, self.agent, self.admin2])
        self.assertUpdateFetch(update_url, [self.editor, self.admin], form_fields=["name"])

        # update name and contact creation option to be per login
        self.assertUpdateSubmit(update_url, self.admin, {"name": "New Name"})

        flow.refresh_from_db()
        self.assertEqual("New Name", flow.name)

    def test_list_views(self):
        flow1 = self.create_flow("Flow 1")
        flow2 = self.create_flow("Flow 2")

        # archive second flow
        flow2.is_archived = True
        flow2.save(update_fields=("is_archived",))

        flow3 = self.create_flow("Flow 3")

        self.login(self.admin)

        # see our trigger on the list page
        response = self.client.get(reverse("flows.flow_list"))
        self.assertContains(response, flow1.name)
        self.assertContains(response, flow3.name)

        # archive it
        response = self.client.post(reverse("flows.flow_list"), {"action": "archive", "objects": flow1.id})
        self.assertEqual(200, response.status_code)

        # flow should no longer appear in list
        response = self.client.get(reverse("flows.flow_list"))
        self.assertNotContains(response, flow1.name)
        self.assertContains(response, flow3.name)

        self.assertEqual(("archive", "label", "export-results"), response.context["actions"])

        # but does appear in archived list
        response = self.client.get(reverse("flows.flow_archived"))
        self.assertContains(response, flow1.name)

        # flow2 should appear before flow since it was created later
        self.assertTrue(flow2, response.context["object_list"][0])
        self.assertTrue(flow1, response.context["object_list"][1])

        # unarchive it
        response = self.client.post(reverse("flows.flow_archived"), {"action": "restore", "objects": flow1.id})
        self.assertEqual(200, response.status_code)

        # flow should no longer appear in archived list
        response = self.client.get(reverse("flows.flow_archived"))
        self.assertNotContains(response, flow1.name)
        self.assertEqual(("restore",), response.context["actions"])

        # but does appear in normal list
        response = self.client.get(reverse("flows.flow_list"))
        self.assertContains(response, flow1.name)
        self.assertContains(response, flow3.name)

        # can label flows
        label1 = FlowLabel.create(self.org, self.admin, "Important")

        response = self.client.post(
            reverse("flows.flow_list"), {"action": "label", "objects": flow1.id, "label": label1.id}
        )

        self.assertEqual(200, response.status_code)
        self.assertEqual({label1}, set(flow1.labels.all()))
        self.assertEqual({flow1}, set(label1.flows.all()))

        # and unlabel
        response = self.client.post(
            reverse("flows.flow_list"), {"action": "label", "objects": flow1.id, "label": label1.id, "add": False}
        )

        self.assertEqual(200, response.status_code)

        flow1.refresh_from_db()
        self.assertEqual(set(), set(flow1.labels.all()))

        # voice flows should be included in the count
        Flow.objects.filter(id=flow1.id).update(flow_type=Flow.TYPE_VOICE)

        response = self.client.get(reverse("flows.flow_list"))
        self.assertContains(response, flow1.name)

        # single message flow (flom campaign) should not be included in counts and not even on this list
        Flow.objects.filter(id=flow1.id).update(is_system=True)

        response = self.client.get(reverse("flows.flow_list"))
        self.assertNotContains(response, flow1.name)

        # single message flow should not be even in the archived list
        Flow.objects.filter(id=flow1.id).update(is_system=True, is_archived=True)

        response = self.client.get(reverse("flows.flow_archived"))
        self.assertNotContains(response, flow1.name)

    def test_filter(self):
        flow1 = self.create_flow("Flow 1")
        flow2 = self.create_flow("Flow 2")

        label1 = FlowLabel.create(self.org, self.admin, "Important")
        label2 = FlowLabel.create(self.org, self.admin, "Very Important")

        label1.toggle_label([flow1, flow2], add=True)
        label2.toggle_label([flow2], add=True)

        self.login(self.admin)

        response = self.client.get(reverse("flows.flow_filter", args=[label1.uuid]))
        self.assertEqual([flow2, flow1], list(response.context["object_list"]))
        self.assertEqual(("label", "export-results"), response.context["actions"])

        response = self.client.get(reverse("flows.flow_filter", args=[label2.uuid]))
        self.assertEqual([flow2], list(response.context["object_list"]))

        response = self.client.get(reverse("flows.flow_filter", args=[label2.uuid]))
        self.assertEqual(f"/flow/labels/{label2.uuid}", response.headers.get(TEMBA_MENU_SELECTION))

    def test_get_definition(self):
        flow = self.get_flow("color_v13")

        # if definition is outdated, metadata values are updated from db object
        flow.name = "Amazing Flow"
        flow.save(update_fields=("name",))

        self.assertEqual("Amazing Flow", flow.get_definition()["name"])

        # make a flow that looks like a legacy flow
        flow = self.get_flow("color_v11")
        original_def = self.load_json("test_flows/color_v11.json")["flows"][0]

        flow.version_number = "11.12"
        flow.save(update_fields=("version_number",))

        revision = flow.revisions.get()
        revision.definition = original_def
        revision.spec_version = "11.12"
        revision.save(update_fields=("definition", "spec_version"))

        self.assertIn("metadata", flow.get_definition())

        # if definition is outdated, metadata values are updated from db object
        flow.name = "Amazing Flow 2"
        flow.save(update_fields=("name",))

        self.assertEqual("Amazing Flow 2", flow.get_definition()["metadata"]["name"])

        # metadata section can be missing too
        del original_def["metadata"]
        revision.definition = original_def
        revision.save(update_fields=("definition",))

        self.assertEqual("Amazing Flow 2", flow.get_definition()["metadata"]["name"])

    def test_revisions(self):
        flow = self.get_flow("color_v11")

        revisions_url = reverse("flows.flow_revisions", args=[flow.uuid])

        original_def = self.load_json("test_flows/color_v11.json")["flows"][0]

        # rewind definition to legacy spec
        revision = flow.revisions.get()
        revision.definition = original_def
        revision.spec_version = "11.12"
        revision.save(update_fields=("definition", "spec_version"))

        # create a new migrated revision
        flow_def = revision.get_migrated_definition()
        flow.save_revision(self.admin, flow_def)

        revisions = list(flow.revisions.all().order_by("-created_on"))

        # now we should have two revisions
        self.assertEqual(2, len(revisions))
        self.assertEqual(2, revisions[0].revision)
        self.assertEqual(Flow.CURRENT_SPEC_VERSION, revisions[0].spec_version)
        self.assertEqual(1, revisions[1].revision)
        self.assertEqual("11.12", revisions[1].spec_version)

        self.assertRequestDisallowed(revisions_url, [None, self.agent, self.admin2])
        response = self.assertReadFetch(revisions_url, [self.user, self.editor, self.admin])
        self.assertEqual(
            [
                {
                    "user": {"email": "admin@textit.com", "name": "Andy"},
                    "created_on": matchers.ISODate(),
                    "id": revisions[0].id,
                    "version": Flow.CURRENT_SPEC_VERSION,
                    "revision": 2,
                },
                {
                    "user": {"email": "admin@textit.com", "name": "Andy"},
                    "created_on": matchers.ISODate(),
                    "id": revisions[1].id,
                    "version": "11.12",
                    "revision": 1,
                },
            ],
            response.json()["results"],
        )

        # fetch a specific revision
        response = self.assertReadFetch(f"{revisions_url}{revisions[0].id}/", [self.user, self.editor, self.admin])

        # make sure we can read the definition
        definition = response.json()["definition"]
        self.assertEqual("und", definition["language"])

        # fetch the legacy revision
        response = self.client.get(f"{revisions_url}{revisions[1].id}/")

        # should automatically migrate to latest spec
        self.assertEqual(Flow.CURRENT_SPEC_VERSION, response.json()["definition"]["spec_version"])

        # but we can also limit how far it is migrated
        response = self.client.get(f"{revisions_url}{revisions[1].id}/?version=13.0.0")

        # should only have been migrated to that version
        self.assertEqual("13.0.0", response.json()["definition"]["spec_version"])

        # check 404 for invalid revision number
        response = self.requestView(f"{revisions_url}12345678/", self.admin)
        self.assertEqual(404, response.status_code)

    def test_save_revisions(self):
        flow = self.create_flow("Go Flow")
        revisions_url = reverse("flows.flow_revisions", args=[flow.uuid])

        self.login(self.admin)
        response = self.client.get(revisions_url)
        self.assertEqual(1, len(response.json()))

        definition = flow.revisions.all().first().definition

        # viewers can't save flows
        self.login(self.user)
        response = self.client.post(revisions_url, definition, content_type="application/json")
        self.assertEqual(403, response.status_code)

        # check that we can create a new revision
        self.login(self.admin)
        response = self.client.post(revisions_url, definition, content_type="application/json")
        new_revision = response.json()
        self.assertEqual(2, new_revision["revision"][Flow.DEFINITION_REVISION])

        # but we can't save our old revision
        response = self.client.post(revisions_url, definition, content_type="application/json")
        self.assertResponseError(
            response, "description", "Your changes will not be saved until you refresh your browser"
        )

        # or save an old version
        definition = flow.revisions.all().first().definition
        definition[Flow.DEFINITION_SPEC_VERSION] = "11.12"
        response = self.client.post(revisions_url, definition, content_type="application/json")
        self.assertResponseError(response, "description", "Your flow has been upgraded to the latest version")

    def test_inactive_flow(self):
        flow = self.create_flow("Deleted")
        flow.release(self.admin)

        self.login(self.admin)

        response = self.client.get(reverse("flows.flow_revisions", args=[flow.uuid]))

        self.assertEqual(404, response.status_code)

        response = self.client.get(reverse("flows.flow_activity", args=[flow.uuid]))

        self.assertEqual(404, response.status_code)

    @mock_mailroom
    def test_preview_start(self, mr_mocks):
        flow = self.create_flow("Test Flow")
        self.create_field("age", "Age")
        self.create_contact("Ann", phone="+16302222222", fields={"age": 40})
        self.create_contact("Bob", phone="+16303333333", fields={"age": 33})

        mr_mocks.flow_start_preview(query='age > 30 AND status = "active" AND history != "Test Flow"', total=100)

        preview_url = reverse("flows.flow_preview_start", args=[flow.id])

        self.login(self.editor)

        response = self.client.post(
            preview_url,
            {
                "query": "age > 30",
                "exclusions": {"non_active": True, "started_previously": True},
            },
            content_type="application/json",
        )
        self.assertEqual(
            {
                "query": 'age > 30 AND status = "active" AND history != "Test Flow"',
                "total": 100,
                "send_time": 10.0,
                "warnings": [],
                "blockers": [],
            },
            response.json(),
        )

        mr_mocks.flow_start_preview(query='age > 30 AND status = "active" AND history != "Test Flow"', total=100)
        self.login(self.customer_support, choose_org=self.org)

        response = self.client.post(
            preview_url,
            {
                "query": "age > 30",
                "exclusions": {"non_active": True, "started_previously": True},
            },
            content_type="application/json",
        )
        self.assertEqual(
            {
                "query": 'age > 30 AND status = "active" AND history != "Test Flow"',
                "total": 100,
                "send_time": 10.0,
                "warnings": [],
                "blockers": [],
            },
            response.json(),
        )

        mr_mocks.flow_start_preview(
            query='age > 30 AND status = "active" AND history != "Test Flow" AND flow = ""', total=100
        )
        preview_url = reverse("flows.flow_preview_start", args=[flow.id])

        self.login(self.editor)

        response = self.client.post(
            preview_url,
            {
                "query": "age > 30",
                "exclusions": {"non_active": True, "started_previously": True, "in_a_flow": True},
            },
            content_type="application/json",
        )
        self.assertEqual(
            {
                "query": 'age > 30 AND status = "active" AND history != "Test Flow" AND flow = ""',
                "total": 100,
                "send_time": 10.0,
                "warnings": [],
                "blockers": [],
            },
            response.json(),
        )

        # try with a bad query
        mr_mocks.exception(mailroom.QueryValidationException("mismatched input at (((", "syntax"))

        response = self.client.post(
            preview_url,
            {
                "query": "(((",
                "exclusions": {"non_active": True, "started_previously": True},
            },
            content_type="application/json",
        )
        self.assertEqual(400, response.status_code)
        self.assertEqual({"query": "", "total": 0, "error": "Invalid query syntax."}, response.json())

        # suspended orgs should block
        self.org.suspend()
        mr_mocks.flow_start_preview(query="age > 30", total=2)
        response = self.client.post(preview_url, {"query": "age > 30"}, content_type="application/json")
        self.assertEqual(
            [
                "Sorry, your workspace is currently suspended. To re-enable starting flows and sending messages, please contact support."
            ],
            response.json()["blockers"],
        )

        # flagged orgs should block
        self.org.unsuspend()
        self.org.flag()
        mr_mocks.flow_start_preview(query="age > 30", total=2)
        response = self.client.post(preview_url, {"query": "age > 30"}, content_type="application/json")
        self.assertEqual(
            [
                "Sorry, your workspace is currently flagged. To re-enable starting flows and sending messages, please contact support."
            ],
            response.json()["blockers"],
        )

        self.org.unflag()

        # create a pending flow start to test warning
        FlowStart.create(flow, self.admin, query="age > 30")

        mr_mocks.flow_start_preview(query='age > 30 AND status = "active" AND history != "Test Flow"', total=100)

        response = self.client.post(
            preview_url,
            {
                "query": "age > 30",
                "exclusions": {"non_active": True, "started_previously": True},
            },
            content_type="application/json",
        )

        self.assertEqual(
            [
                "A flow is already starting. To avoid confusion, make sure you are not targeting the same contacts before continuing."
            ],
            response.json()["warnings"],
        )

        ivr_flow = self.create_flow("IVR Test", flow_type=Flow.TYPE_VOICE)

        preview_url = reverse("flows.flow_preview_start", args=[ivr_flow.id])

        # shouldn't be able to since we don't have a call channel
        self.org.flow_starts.all().delete()
        mr_mocks.flow_start_preview(query='age > 30 AND status = "active" AND history != "IVR Test"', total=100)

        response = self.client.post(
            preview_url,
            {
                "query": "age > 30",
                "exclusions": {"non_active": True, "started_previously": True},
            },
            content_type="application/json",
        )

        self.assertEqual(
            response.json()["blockers"][0],
            'To start this flow you need to <a href="/channels/channel/claim/">add a voice channel</a> to your workspace which will allow you to make and receive calls.',
        )

        # if we have too many messages in our outbox we should block
        self.org.counts.create(scope=f"msgs:folder:{SystemLabel.TYPE_OUTBOX}", count=1_000_001)
        preview_url = reverse("flows.flow_preview_start", args=[flow.id])
        mr_mocks.flow_start_preview(query="age > 30", total=1000)

        response = self.client.post(
            preview_url,
            {
                "query": "age > 30",
            },
            content_type="application/json",
        )
        self.assertEqual(
            [
                "You have too many messages queued in your outbox. Please wait for these messages to send and then try again."
            ],
            response.json()["blockers"],
        )
        self.org.counts.prefix("msgs:folder:").delete()

        # check warning for lots of contacts
        preview_url = reverse("flows.flow_preview_start", args=[flow.id])

        # with patch("temba.orgs.models.Org.get_estimated_send_time") as mock_get_estimated_send_time:
        with override_settings(SEND_HOURS_WARNING=24, SEND_HOURS_BLOCK=48):

            # we send at 10 tps, so make the total take 24 hours
            expected_tps = 10
            mr_mocks.flow_start_preview(
                query='age > 30 AND status = "active" AND history != "Test Flow"', total=24 * 60 * 60 * expected_tps
            )

            # mock_get_estimated_send_time.return_value = timedelta(days=2)
            response = self.client.post(
                preview_url,
                {
                    "query": "age > 30",
                    "exclusions": {"non_active": True, "started_previously": True},
                },
                content_type="application/json",
            )

            self.assertEqual(
                response.json()["warnings"][0],
                "Your channels will likely take over a day to reach all of the selected contacts. Consider selecting fewer contacts before continuing.",
            )

            # now really long so it should block
            mr_mocks.flow_start_preview(
                query='age > 30 AND status = "active" AND history != "Test Flow"', total=3 * 24 * 60 * 60 * expected_tps
            )
            # mock_get_estimated_send_time.return_value = timedelta(days=7)
            response = self.client.post(
                preview_url,
                {
                    "query": "age > 30",
                    "exclusions": {"non_active": True, "started_previously": True},
                },
                content_type="application/json",
            )

            self.assertEqual(
                response.json()["blockers"][0],
                "Your channels cannot send fast enough to reach all of the selected contacts in a reasonable time. Select fewer contacts to continue.",
            )

        # if we release our send channel we also can't start a regular messaging flow
        self.channel.release(self.admin)
        mr_mocks.flow_start_preview(query='age > 30 AND status = "active" AND history != "Test Flow"', total=100)

        response = self.client.post(
            preview_url,
            {
                "query": "age > 30",
                "exclusions": {"non_active": True, "started_previously": True},
            },
            content_type="application/json",
        )

        self.assertEqual(
            response.json()["blockers"][0],
            'To start this flow you need to <a href="/channels/channel/claim/">add a channel</a> to your workspace which will allow you to send messages to your contacts.',
        )

        flow = self.create_flow("Background Flow", flow_type=Flow.TYPE_BACKGROUND)
        mr_mocks.flow_start_preview(query='age > 30 AND status = "active" AND history != "Background Flow"', total=100)
        preview_url = reverse("flows.flow_preview_start", args=[flow.id])

        self.login(self.editor)

        response = self.client.post(
            preview_url,
            {
                "query": "age > 30",
                "exclusions": {"non_active": True, "started_previously": True, "in_a_flow": True},
            },
            content_type="application/json",
        )
        self.assertEqual(
            {
                "query": 'age > 30 AND status = "active" AND history != "Background Flow"',
                "total": 100,
                "send_time": 0.0,
                "warnings": [],
                "blockers": [],
            },
            response.json(),
        )

    @mock_mailroom
    def test_template_warnings(self, mr_mocks):
        self.login(self.admin)
        flow = self.get_flow("whatsapp_template")

        # bring up broadcast dialog
        self.login(self.admin)

        mr_mocks.flow_start_preview(query="age > 30", total=2)
        response = self.client.post(
            reverse("flows.flow_preview_start", args=[flow.id]),
            {
                "query": "age > 30",
            },
            content_type="application/json",
        )

        # no warning, we don't have a whatsapp channel
        self.assertEqual(response.json()["warnings"], [])

        # change our channel to use a whatsapp scheme
        self.channel.schemes = [URN.WHATSAPP_SCHEME]
        self.channel.channel_type = "TWA"
        self.channel.save()

        mr_mocks.flow_start_preview(query="age > 30", total=2)
        response = self.client.post(
            reverse("flows.flow_preview_start", args=[flow.id]),
            {
                "query": "age > 30",
            },
            content_type="application/json",
        )

        # no warning, we don't have a whatsapp channel that requires a message template
        self.assertEqual(response.json()["warnings"], [])

        self.channel.channel_type = "WA"
        self.channel.save()

        # clear dependencies, this will cause our flow to look like it isn't using templates
        metadata = flow.metadata
        flow.metadata = {}
        flow.save(update_fields=["metadata"])

        mr_mocks.flow_start_preview(query="age > 30", total=2)
        response = self.client.post(
            reverse("flows.flow_preview_start", args=[flow.id]),
            {
                "query": "age > 30",
            },
            content_type="application/json",
        )

        self.assertEqual(
            response.json()["warnings"],
            [
                "This flow does not use message templates. You may still start this flow but WhatsApp contacts who have not sent an incoming message in the last 24 hours may not receive it."
            ],
        )

        # restore our dependency
        flow.metadata = metadata
        flow.save(update_fields=["metadata"])

        # template doesn't exit, will be warned
        mr_mocks.flow_start_preview(query="age > 30", total=2)
        response = self.client.post(
            reverse("flows.flow_preview_start", args=[flow.id]),
            {
                "query": "age > 30",
            },
            content_type="application/json",
        )

        self.assertEqual(
            response.json()["warnings"],
            ["The message template affirmation does not exist on your account and cannot be sent."],
        )

        # create the template, but no translations
        template = self.create_template("affirmation", [], uuid="f712e05c-bbed-40f1-b3d9-671bb9b60775")

        # will be warned again
        mr_mocks.flow_start_preview(query="age > 30", total=2)
        response = self.client.post(
            reverse("flows.flow_preview_start", args=[flow.id]),
            {
                "query": "age > 30",
            },
            content_type="application/json",
        )

        self.assertEqual(
            response.json()["warnings"], ["Your message template affirmation is not approved and cannot be sent."]
        )

        # create a translation, but not approved
        TemplateTranslation.objects.create(
            template=template,
            channel=self.channel,
            locale="eng-US",
            status=TemplateTranslation.STATUS_REJECTED,
            external_id="id1",
            external_locale="en_US",
            namespace="foo_namespace",
            components=[{"name": "body", "type": "body/text", "content": "Hello", "variables": {}, "params": []}],
            variables=[],
        )

        # will be warned again
        mr_mocks.flow_start_preview(query="age > 30", total=2)
        response = self.client.post(
            reverse("flows.flow_preview_start", args=[flow.id]),
            {
                "query": "age > 30",
            },
            content_type="application/json",
        )

        self.assertEqual(
            response.json()["warnings"], ["Your message template affirmation is not approved and cannot be sent."]
        )

        # finally, set our translation to approved
        TemplateTranslation.objects.update(status=TemplateTranslation.STATUS_APPROVED)

        # no warnings
        mr_mocks.flow_start_preview(query="age > 30", total=2)
        response = self.client.post(
            reverse("flows.flow_preview_start", args=[flow.id]),
            {
                "query": "age > 30",
            },
            content_type="application/json",
        )

        self.assertEqual(response.json()["warnings"], [])

    @mock_mailroom
    def test_start(self, mr_mocks):
        contact = self.create_contact("Bob", phone="+593979099111")
        flow = self.create_flow("Test")
        start_url = f"{reverse('flows.flow_start', args=[])}?flow={flow.id}"

        self.assertRequestDisallowed(start_url, [None, self.user, self.agent])
        self.assertUpdateFetch(start_url, [self.editor, self.admin], form_fields=["flow", "contact_search"])

        # create flow start with a query
        mr_mocks.contact_parse_query("frank", cleaned='name ~ "frank"')
        self.assertUpdateSubmit(
            start_url,
            self.admin,
            {"flow": flow.id, "contact_search": get_contact_search(query="frank")},
        )

        start = FlowStart.objects.get()
        self.assertEqual(flow, start.flow)
        self.assertEqual(FlowStart.STATUS_PENDING, start.status)
        self.assertEqual({}, start.exclusions)
        self.assertEqual('name ~ "frank"', start.query)

        self.assertEqual(1, len(mr_mocks.queued_batch_tasks))
        self.assertEqual("start_flow", mr_mocks.queued_batch_tasks[0]["type"])

        FlowStart.objects.all().delete()

        # create flow start with a bogus query
        mr_mocks.exception(mailroom.QueryValidationException("query contains an error", "syntax"))
        self.assertUpdateSubmit(
            start_url,
            self.admin,
            {"flow": flow.id, "contact_search": get_contact_search(query='name = "frank')},
            form_errors={"contact_search": "Invalid query syntax."},
            object_unchanged=flow,
        )

        # try missing contacts
        self.assertUpdateSubmit(
            start_url,
            self.admin,
            {"flow": flow.id, "contact_search": get_contact_search(contacts=[])},
            form_errors={"contact_search": "Contacts or groups are required."},
            object_unchanged=flow,
        )

        # try to create with an empty query
        self.assertUpdateSubmit(
            start_url,
            self.admin,
            {"flow": flow.id, "contact_search": get_contact_search(query="")},
            form_errors={"contact_search": "A contact query is required."},
            object_unchanged=flow,
        )

        query = f"uuid='{contact.uuid}'"
        mr_mocks.contact_parse_query(query, cleaned=query)

        # create flow start with exclude_in_other and exclude_reruns both left unchecked
        self.assertUpdateSubmit(
            start_url,
            self.admin,
            {"flow": flow.id, "contact_search": get_contact_search(query=query)},
        )

        start = FlowStart.objects.get()

        self.assertEqual(query, start.query)
        self.assertEqual(flow, start.flow)
        self.assertEqual(FlowStart.TYPE_MANUAL, start.start_type)
        self.assertEqual(FlowStart.STATUS_PENDING, start.status)
        self.assertEqual({}, start.exclusions)

        self.assertEqual(2, len(mr_mocks.queued_batch_tasks))
        self.assertEqual("start_flow", mr_mocks.queued_batch_tasks[1]["type"])

        FlowStart.objects.all().delete()

    @mock_mailroom
    def test_broadcast_background_flow(self, mr_mocks):
        flow = self.create_flow("Background", flow_type=Flow.TYPE_BACKGROUND)

        # create flow start with a query
        mr_mocks.contact_parse_query("frank", cleaned='name ~ "frank"')

        start_url = f"{reverse('flows.flow_start', args=[])}?flow={flow.id}"
        self.assertUpdateSubmit(
            start_url, self.admin, {"flow": flow.id, "contact_search": get_contact_search(query="frank")}
        )

        start = FlowStart.objects.get()
        self.assertEqual(flow, start.flow)
        self.assertEqual(FlowStart.STATUS_PENDING, start.status)
        self.assertEqual({}, start.exclusions)
        self.assertEqual('name ~ "frank"', start.query)

    def test_copy_view(self):
        flow = self.get_flow("color")

        self.login(self.admin)

        response = self.client.post(reverse("flows.flow_copy", args=[flow.id]))

        flow_copy = Flow.objects.get(org=self.org, name="Copy of %s" % flow.name)

        self.assertRedirect(response, reverse("flows.flow_editor", args=[flow_copy.uuid]))

    def test_recent_contacts(self):
        flow = self.create_flow("Test")
        contact1 = self.create_contact("Bob", phone="0979111111")
        contact2 = self.create_contact("", phone="0979222222")
        node1_exit1_uuid = "805f5073-ce96-4b6a-ab9f-e77dd412f83b"
        node2_uuid = "fcc47dc4-306b-4b2f-ad72-7e53f045c3c4"

        seg1_url = reverse("flows.flow_recent_contacts", args=[flow.uuid, node1_exit1_uuid, node2_uuid])

        # nothing set in redis just means empty list
        self.assertRequestDisallowed(seg1_url, [None, self.agent, self.admin2])
        response = self.assertReadFetch(seg1_url, [self.user, self.editor, self.admin])
        self.assertEqual([], response.json())

        def add_recent_contact(exit_uuid: str, dest_uuid: str, contact, text: str, ts: float):
            r = get_redis_connection()
            member = f"{uuid4()}|{contact.id}|{text}"  # text is prefixed with a random value to keep it unique
            r.zadd(f"recent_contacts:{exit_uuid}:{dest_uuid}", mapping={member: ts})

        add_recent_contact(node1_exit1_uuid, node2_uuid, contact1, "Hi there", 1639338554.969123)
        add_recent_contact(node1_exit1_uuid, node2_uuid, contact2, "|x|", 1639338555.234567)
        add_recent_contact(node1_exit1_uuid, node2_uuid, contact1, "Sounds good", 1639338561.345678)

        response = self.assertReadFetch(seg1_url, [self.user, self.editor, self.admin])
        self.assertEqual(
            [
                {
                    "contact": {"uuid": str(contact1.uuid), "name": "Bob"},
                    "operand": "Sounds good",
                    "time": "2021-12-12T19:49:21.345678+00:00",
                },
                {
                    "contact": {"uuid": str(contact2.uuid), "name": "0979 222 222"},
                    "operand": "|x|",
                    "time": "2021-12-12T19:49:15.234567+00:00",
                },
                {
                    "contact": {"uuid": str(contact1.uuid), "name": "Bob"},
                    "operand": "Hi there",
                    "time": "2021-12-12T19:49:14.969123+00:00",
                },
            ],
            response.json(),
        )

    def test_category_counts(self):
        flow1 = self.create_flow("Test 1")

        counts_url = reverse("flows.flow_category_counts", args=[flow1.id])

        self.assertRequestDisallowed(counts_url, [None, self.agent])

        # check with no data
        response = self.assertReadFetch(counts_url, [self.user, self.editor, self.admin])
        self.assertEqual({"counts": []}, response.json())

        # simulate some category data
        flow1.metadata["results"] = [{"key": "color", "name": "Color"}, {"key": "beer", "name": "Beer"}]
        flow1.save(update_fields=("metadata",))

        flow1.category_counts.create(
            node_uuid="9b00751c-0d46-4e5f-86b1-7ccfae76ea10",
            result_key="color",
            result_name="Color",
            category_name="Red",
            count=3,
        )
        flow1.category_counts.create(
            node_uuid="9b00751c-0d46-4e5f-86b1-7ccfae76ea10",
            result_key="color",
            result_name="Color",
            category_name="Blue",
            count=2,
        )
        flow1.category_counts.create(
            node_uuid="9b00751c-0d46-4e5f-86b1-7ccfae76ea10",
            result_key="color",
            result_name="Color",
            category_name="Other",
            count=1,
        )
        flow1.category_counts.create(
            node_uuid="300fd49b-c69d-4e8c-aba9-b6036d0b83d9",
            result_key="beer",
            result_name="Beer",
            category_name="Primus",
            count=7,
        )

        response = self.assertReadFetch(counts_url, [self.user, self.editor, self.admin])
        self.assertEqual(
            {
                "counts": [
                    {
                        "key": "color",
                        "name": "Color",
                        "categories": [
                            {"name": "Blue", "count": 2, "pct": 0.3333333333333333},
                            {"name": "Other", "count": 1, "pct": 0.16666666666666666},
                            {"name": "Red", "count": 3, "pct": 0.5},
                        ],
                        "total": 6,
                    },
                    {
                        "key": "beer",
                        "name": "Beer",
                        "categories": [
                            {"name": "Primus", "count": 7, "pct": 1.0},
                        ],
                        "total": 7,
                    },
                ]
            },
            response.json(),
        )

    def test_results(self):
        flow = self.create_flow("Test 1")

        results_url = reverse("flows.flow_results", args=[flow.id])

        self.assertRequestDisallowed(results_url, [None, self.agent])
        self.assertReadFetch(results_url, [self.user, self.editor, self.admin])

        flow.release(self.admin)

        response = self.requestView(results_url, self.admin)
        self.assertEqual(404, response.status_code)

    @patch("django.utils.timezone.now")
    def test_engagement(self, mock_now):
        # this test runs as if it's 2024-11-25 12:05:00
        mock_now.return_value = datetime(2024, 11, 25, 12, 5, 0, tzinfo=tzone.utc)

        flow1 = self.create_flow("Test 1")

        engagement_url = reverse("flows.flow_engagement", args=[flow1.id])

        # check fetching as template
        self.assertRequestDisallowed(engagement_url, [None, self.agent])
        self.assertReadFetch(engagement_url, [self.user, self.editor, self.admin])

        # check fetching as chart data (when there's no data)
        response = self.requestView(engagement_url, self.admin, HTTP_ACCEPT="application/json")
        self.assertEqual(
            {
                "timeline": {
                    "data": [],
                    "xmin": 1729900800000,  # 2024-10-26
                    "xmax": 1732492800000,  # 2024-11-25
                    "ymax": 0,
                },
                "dow": {
                    "data": [
                        {"msgs": 0, "y": 0.0},
                        {"msgs": 0, "y": 0.0},
                        {"msgs": 0, "y": 0.0},
                        {"msgs": 0, "y": 0.0},
                        {"msgs": 0, "y": 0.0},
                        {"msgs": 0, "y": 0.0},
                        {"msgs": 0, "y": 0.0},
                    ]
                },
                "hod": {"data": [[i, 0] for i in range(24)]},
                "completion": {
                    "summary": [
                        {"name": "Active", "y": 0, "drilldown": None, "color": "#2387CA"},
                        {"name": "Completed", "y": 0, "drilldown": None, "color": "#8FC93A"},
                        {
                            "name": "Interrupted, Expired and Failed",
                            "y": 0,
                            "drilldown": "incomplete",
                            "color": "#CCC",
                        },
                    ],
                    "drilldown": [
                        {
                            "name": "Interrupted, Expired and Failed",
                            "id": "incomplete",
                            "innerSize": "50%",
                            "data": [
                                {"name": "Expired", "y": 0, "color": "#CCC"},
                                {"name": "Interrupted", "y": 0, "color": "#EEE"},
                                {"name": "Failed", "y": 0, "color": "#FEE"},
                            ],
                        }
                    ],
                },
            },
            response.json(),
        )

        def engagement(flow, when, count):
            flow.counts.create(scope=f"msgsin:hour:{when.hour}", count=count)
            flow.counts.create(scope=f"msgsin:dow:{when.isoweekday()}", count=count)
            flow.counts.create(scope=f"msgsin:date:{when.date().isoformat()}", count=count)

        engagement(flow1, datetime(2024, 11, 24, 9, 0, 0, tzinfo=tzone.utc), 3)  # 2024-11-24 09:00 (Sun)
        engagement(flow1, datetime(2024, 11, 25, 12, 0, 0, tzinfo=tzone.utc), 2)  # 2024-11-25 12:00 (Mon)
        engagement(flow1, datetime(2024, 11, 26, 9, 0, 0, tzinfo=tzone.utc), 4)  # 2024-11-26 09:00 (Tue)
        engagement(flow1, datetime(2024, 11, 26, 23, 0, 0, tzinfo=tzone.utc), 1)  # 2024-11-26 23:00 (Tue)

        flow1.counts.create(scope="status:W", count=4)
        flow1.counts.create(scope="status:C", count=3)
        flow1.counts.create(scope="status:X", count=2)
        flow1.counts.create(scope="status:I", count=1)

        response = self.requestView(engagement_url, self.admin, HTTP_ACCEPT="application/json")
        self.assertEqual(
            {
                "timeline": {
                    "data": [[1732406400000, 3], [1732492800000, 2], [1732579200000, 5]],
                    "xmin": 1729900800000,  # 2024-10-26
                    "xmax": 1732492800000,
                    "ymax": 5,
                },
                "dow": {
                    "data": [
                        {"msgs": 3, "y": 30.0},
                        {"msgs": 2, "y": 20.0},
                        {"msgs": 5, "y": 50.0},
                        {"msgs": 0, "y": 0.0},
                        {"msgs": 0, "y": 0.0},
                        {"msgs": 0, "y": 0.0},
                        {"msgs": 0, "y": 0.0},
                    ]
                },
                "hod": {
                    "data": [
                        [0, 0],
                        [1, 1],  # 23:00 UTC is 01:00 in Kigali
                        [2, 0],
                        [3, 0],
                        [4, 0],
                        [5, 0],
                        [6, 0],
                        [7, 0],
                        [8, 0],
                        [9, 0],
                        [10, 0],
                        [11, 7],
                        [12, 0],
                        [13, 0],
                        [14, 2],
                        [15, 0],
                        [16, 0],
                        [17, 0],
                        [18, 0],
                        [19, 0],
                        [20, 0],
                        [21, 0],
                        [22, 0],
                        [23, 0],
                    ]
                },
                "completion": {
                    "summary": [
                        {"name": "Active", "y": 4, "drilldown": None, "color": "#2387CA"},
                        {"name": "Completed", "y": 3, "drilldown": None, "color": "#8FC93A"},
                        {
                            "name": "Interrupted, Expired and Failed",
                            "y": 3,
                            "drilldown": "incomplete",
                            "color": "#CCC",
                        },
                    ],
                    "drilldown": [
                        {
                            "name": "Interrupted, Expired and Failed",
                            "id": "incomplete",
                            "innerSize": "50%",
                            "data": [
                                {"name": "Expired", "y": 2, "color": "#CCC"},
                                {"name": "Interrupted", "y": 1, "color": "#EEE"},
                                {"name": "Failed", "y": 0, "color": "#FEE"},
                            ],
                        }
                    ],
                },
            },
            response.json(),
        )

        # simulate having some data from 6 months ago
        engagement(flow1, datetime(2024, 5, 1, 12, 0, 0, tzinfo=tzone.utc), 4)  # 2024-05-01 12:00 (Wed)

        response = self.requestView(engagement_url, self.admin, HTTP_ACCEPT="application/json")
        resp_json = response.json()
        self.assertEqual(1714521600000, resp_json["timeline"]["xmin"])  # 2024-05-01
        self.assertEqual(
            [[1714521600000, 4], [1732406400000, 3], [1732492800000, 2], [1732579200000, 5]],
            resp_json["timeline"]["data"],
        )

        # simulate having some data from 18 months ago (should trigger bucketing by week)
        engagement(flow1, datetime(2023, 5, 1, 12, 0, 0, tzinfo=tzone.utc), 3)  # 2023-05-01 12:00 (Mon)

        response = self.requestView(engagement_url, self.admin, HTTP_ACCEPT="application/json")
        resp_json = response.json()
        self.assertEqual(1682899200000, resp_json["timeline"]["xmin"])  # 2023-05-01
        self.assertEqual(
            [
                [1682899200000, 3],  # 2023-05-01 (Mon)
                [1714348800000, 4],  # 2024-04-29 (Mon)
                [1731888000000, 3],  # 2024-11-18 (Mon)
                [1732492800000, 7],  # 2024-11-25 (Mon)
            ],
            resp_json["timeline"]["data"],
        )

        # simulate having some data from 4 years ago (should trigger bucketing by month)
        engagement(flow1, datetime(2020, 11, 25, 12, 0, 0, tzinfo=tzone.utc), 6)  # 2020-11-25 12:00 (Wed)

        response = self.requestView(engagement_url, self.admin, HTTP_ACCEPT="application/json")
        resp_json = response.json()
        self.assertEqual(1606262400000, resp_json["timeline"]["xmin"])  # 2020-11-25
        self.assertEqual(
            [
                [1604188800000, 6],  # 2020-11-01
                [1682899200000, 3],  # 2023-05-01
                [1714521600000, 4],  # 2024-05-01
                [1730419200000, 10],  # 2024-11-01
            ],
            resp_json["timeline"]["data"],
        )

        # check 404 for inactive flow
        flow1.release(self.admin)

        response = self.requestView(engagement_url, self.admin)
        self.assertEqual(404, response.status_code)

    def test_activity(self):
        flow1 = self.create_flow("Test 1")
        flow2 = self.create_flow("Test 2")

        flow1.counts.create(scope="node:01c175da-d23d-40a4-a845-c4a9bb4b481a", count=4)
        flow1.counts.create(scope="node:400d6b5e-c963-42a1-a06c-50bb9b1e38b1", count=5)

        flow1.counts.create(
            scope="segment:1fff74f4-c81f-4f4c-a03d-58d113c17da1:01c175da-d23d-40a4-a845-c4a9bb4b481a", count=3
        )
        flow1.counts.create(
            scope="segment:1fff74f4-c81f-4f4c-a03d-58d113c17da1:01c175da-d23d-40a4-a845-c4a9bb4b481a", count=4
        )
        flow1.counts.create(
            scope="segment:6f607948-f3f0-4a6a-94b8-7fdd877895ca:400d6b5e-c963-42a1-a06c-50bb9b1e38b1", count=5
        )
        flow2.counts.create(
            scope="segment:a4fe3ada-b062-47e4-be58-bcbe1bca31b4:74a53ff4-fe63-4d89-875e-cae3caca177c", count=6
        )

        activity_url = reverse("flows.flow_activity", args=[flow1.uuid])

        self.assertRequestDisallowed(activity_url, [None, self.agent])

        response = self.assertReadFetch(activity_url, [self.user, self.editor, self.admin])
        self.assertEqual(
            {
                "nodes": {"01c175da-d23d-40a4-a845-c4a9bb4b481a": 4, "400d6b5e-c963-42a1-a06c-50bb9b1e38b1": 5},
                "segments": {
                    "1fff74f4-c81f-4f4c-a03d-58d113c17da1:01c175da-d23d-40a4-a845-c4a9bb4b481a": 7,
                    "6f607948-f3f0-4a6a-94b8-7fdd877895ca:400d6b5e-c963-42a1-a06c-50bb9b1e38b1": 5,
                },
            },
            response.json(),
        )

    def test_write_protection(self):
        flow = self.get_flow("favorites_v13")
        flow_json = flow.get_definition()
        flow_json_copy = flow_json.copy()

        self.assertEqual(1, flow_json["revision"])

        self.login(self.admin)

        # saving should work
        flow.save_revision(self.admin, flow_json)

        self.assertEqual(2, flow_json["revision"])

        # we can't save with older revision number
        with self.assertRaises(FlowUserConflictException):
            flow.save_revision(self.admin, flow_json_copy)

        # make flow definition invalid by creating a duplicate node UUID
        mode0_uuid = flow_json["nodes"][0]["uuid"]
        flow_json["nodes"][1]["uuid"] = mode0_uuid

        with self.assertRaises(mailroom.FlowValidationException) as cm:
            flow.save_revision(self.admin, flow_json)

        self.assertEqual(f"node UUID {mode0_uuid} isn't unique", str(cm.exception))

        # check view converts exception to error response
        response = self.client.post(
            reverse("flows.flow_revisions", args=[flow.uuid]), data=flow_json, content_type="application/json"
        )

        self.assertEqual(400, response.status_code)
        self.assertEqual(
            {
                "status": "failure",
                "description": "Your flow failed validation. Please refresh your browser.",
                "detail": f"node UUID {mode0_uuid} isn't unique",
            },
            response.json(),
        )

    def test_change_language(self):
        self.org.set_flow_languages(self.admin, ["eng", "spa", "ara"])

        flow = self.get_flow("favorites_v13")

        change_url = reverse("flows.flow_change_language", args=[flow.id])

        self.assertUpdateSubmit(
            change_url,
            self.admin,
            {"language": ""},
            form_errors={"language": "This field is required."},
            object_unchanged=flow,
        )

        self.assertUpdateSubmit(
            change_url,
            self.admin,
            {"language": "fra"},
            form_errors={"language": "Not a valid language."},
            object_unchanged=flow,
        )

        self.assertUpdateSubmit(change_url, self.admin, {"language": "spa"}, success_status=302)

        flow_def = flow.get_definition()
        self.assertIn("eng", flow_def["localization"])
        self.assertEqual("¿Cuál es tu color favorito?", flow_def["nodes"][0]["actions"][0]["text"])

    def test_export_results(self):
        export_url = reverse("flows.flow_export_results")

        flow1 = self.create_flow("Test 1")
        flow2 = self.create_flow("Test 2")
        testers = self.create_group("Testers", contacts=[])
        gender = self.create_field("gender", "Gender")

        self.assertRequestDisallowed(export_url, [None, self.agent])
        response = self.assertUpdateFetch(
            export_url + f"?ids={flow1.id},{flow2.id}",
            [self.user, self.editor, self.admin],
            form_fields=(
                "start_date",
                "end_date",
                "with_fields",
                "with_groups",
                "flows",
                "extra_urns",
                "responded_only",
            ),
        )
        self.assertNotContains(response, "already an export in progress")

        # anon orgs don't see urns option
        with self.anonymous(self.org):
            response = self.client.get(export_url)
            self.assertEqual(
                ["start_date", "end_date", "with_fields", "with_groups", "flows", "responded_only", "loc"],
                list(response.context["form"].fields.keys()),
            )

        # create a dummy export task so that we won't be able to export
        blocking_export = ResultsExport.create(
            self.org, self.admin, start_date=date.today() - timedelta(days=7), end_date=date.today()
        )

        response = self.client.get(export_url)
        self.assertContains(response, "already an export in progress")

        # check we can't submit in case a user opens the form and whilst another user is starting an export
        response = self.client.post(
            export_url, {"start_date": "2022-06-28", "end_date": "2022-09-28", "flows": [flow1.id]}
        )
        self.assertContains(response, "already an export in progress")
        self.assertEqual(1, Export.objects.count())

        # mark that one as finished so it's no longer a blocker
        blocking_export.status = Export.STATUS_COMPLETE
        blocking_export.save(update_fields=("status",))

        # try to submit with no values
        response = self.client.post(export_url, {})
        self.assertFormError(response.context["form"], "start_date", "This field is required.")
        self.assertFormError(response.context["form"], "end_date", "This field is required.")
        self.assertFormError(response.context["form"], "flows", "This field is required.")

        response = self.client.post(
            export_url,
            {
                "start_date": "2022-06-28",
                "end_date": "2022-09-28",
                "flows": [flow1.id],
                "with_groups": [testers.id],
                "with_fields": [gender.id],
            },
        )
        self.assertEqual(200, response.status_code)

        export = Export.objects.exclude(id=blocking_export.id).get()
        self.assertEqual("results", export.export_type)
        self.assertEqual(date(2022, 6, 28), export.start_date)
        self.assertEqual(date(2022, 9, 28), export.end_date)
        self.assertEqual(
            {
                "flow_ids": [flow1.id],
                "with_groups": [testers.id],
                "with_fields": [gender.id],
                "extra_urns": [],
                "responded_only": False,
            },
            export.config,
        )

    def test_export_and_download_translation(self):
        self.org.set_flow_languages(self.admin, ["spa"])

        flow = self.get_flow("favorites")
        export_url = reverse("flows.flow_export_translation", args=[flow.id])

        self.assertRequestDisallowed(export_url, [None, self.agent, self.admin2])
        self.assertUpdateFetch(export_url, [self.user, self.editor, self.admin], form_fields=["language"])

        # submit with no language
        response = self.assertUpdateSubmit(export_url, self.admin, {}, success_status=200)

        download_url = response["X-Temba-Success"]
        self.assertEqual(f"/flow/download_translation/?flow={flow.id}&language=", download_url)

        # check fetching the PO from the download link
        with patch("temba.mailroom.client.client.MailroomClient.po_export") as mock_po_export:
            mock_po_export.return_value = b'msgid "Red"\nmsgstr "Roja"\n\n'
            self.assertRequestDisallowed(download_url, [None, self.agent, self.admin2])
            response = self.assertReadFetch(download_url, [self.user, self.editor, self.admin])

            self.assertEqual(b'msgid "Red"\nmsgstr "Roja"\n\n', response.content)
            self.assertEqual('attachment; filename="favorites.po"', response["Content-Disposition"])
            self.assertEqual("text/x-gettext-translation", response["Content-Type"])

        # submit with a language
        response = self.assertUpdateSubmit(export_url, self.admin, {"language": "spa"}, success_status=200)

        download_url = response["X-Temba-Success"]
        self.assertEqual(f"/flow/download_translation/?flow={flow.id}&language=spa", download_url)

        # check fetching the PO from the download link
        with patch("temba.mailroom.client.client.MailroomClient.po_export") as mock_po_export:
            mock_po_export.return_value = b'msgid "Red"\nmsgstr "Roja"\n\n'
            response = self.requestView(download_url, self.admin)

            # filename includes language now
            self.assertEqual('attachment; filename="favorites.spa.po"', response["Content-Disposition"])

    def test_import_translation(self):
        self.org.set_flow_languages(self.admin, ["eng", "spa"])

        flow = self.get_flow("favorites_v13")
        step1_url = reverse("flows.flow_import_translation", args=[flow.id])

        # check step 1 is just a file upload
        self.assertRequestDisallowed(step1_url, [None, self.user, self.agent, self.admin2])
        self.assertUpdateFetch(step1_url, [self.editor, self.admin], form_fields=["po_file"])

        # submit with no file
        self.assertUpdateSubmit(
            step1_url, self.admin, {}, form_errors={"po_file": "This field is required."}, object_unchanged=flow
        )

        # submit with something that's empty
        response = self.requestView(step1_url, self.admin, post_data={"po_file": io.BytesIO(b"")})
        self.assertFormError(response.context["form"], "po_file", "The submitted file is empty.")

        # submit with something that's not a valid PO file
        response = self.requestView(step1_url, self.admin, post_data={"po_file": io.BytesIO(b"msgid")})
        self.assertFormError(response.context["form"], "po_file", "File doesn't appear to be a valid PO file.")

        # submit with something that's in the base language of the flow
        po_file = io.BytesIO(
            b"""
#, fuzzy
msgid ""
msgstr ""
"POT-Creation-Date: 2018-07-06 12:30+0000\\n"
"Language: en\\n"
"Language-3: eng\\n"

msgid "Blue"
msgstr "Bluuu"
        """
        )
        response = self.requestView(step1_url, self.admin, post_data={"po_file": po_file})
        self.assertFormError(
            response.context["form"],
            "po_file",
            "Contains translations in English which is the base language of this flow.",
        )

        # submit with something that's in the base language of the flow
        po_file = io.BytesIO(
            b"""
#, fuzzy
msgid ""
msgstr ""
"POT-Creation-Date: 2018-07-06 12:30+0000\\n"
"Language: fr\\n"
"Language-3: fra\\n"

msgid "Blue"
msgstr "Bleu"
        """
        )
        response = self.requestView(step1_url, self.admin, post_data={"po_file": po_file})
        self.assertFormError(
            response.context["form"],
            "po_file",
            "Contains translations in French which is not a supported translation language.",
        )

        # submit with something that doesn't have an explicit language
        po_file = io.BytesIO(
            b"""
msgid "Blue"
msgstr "Azul"
        """
        )
        response = self.requestView(step1_url, self.admin, post_data={"po_file": po_file})

        self.assertEqual(302, response.status_code)
        self.assertIn(f"/flow/import_translation/{flow.id}/?po=", response.url)

        response = self.assertUpdateFetch(response.url, [self.admin], form_fields=["language"])
        self.assertContains(response, "Unknown")

        # submit a different PO that does have language set
        po_file = io.BytesIO(
            b"""
#, fuzzy
msgid ""
msgstr ""
"POT-Creation-Date: 2018-07-06 12:30+0000\\n"
"Language: es\\n"
"MIME-Version: 1.0\\n"
"Content-Type: text/plain; charset=UTF-8\\n"
"Language-3: spa\\n"

#: Favorites/8720f157-ca1c-432f-9c0b-2014ddc77094/name:0
#: Favorites/a4d15ed4-5b24-407f-b86e-4b881f09a186/arguments:0
msgid "Blue"
msgstr "Azul"
"""
        )
        response = self.requestView(step1_url, self.admin, post_data={"po_file": po_file})

        self.assertEqual(302, response.status_code)
        self.assertIn(f"/flow/import_translation/{flow.id}/?po=", response.url)

        step2_url = response.url

        response = self.assertUpdateFetch(step2_url, [self.admin], form_fields=["language"])
        self.assertContains(response, "Spanish (spa)")
        self.assertEqual({"language": "spa"}, response.context["form"].initial)

        # confirm the import
        with patch("temba.mailroom.client.client.MailroomClient.po_import") as mock_po_import:
            mock_po_import.return_value = {"flows": [flow.get_definition()]}

            response = self.requestView(step2_url, self.admin, post_data={"language": "spa"})

        # should redirect back to editor
        self.assertEqual(302, response.status_code)
        self.assertEqual(f"/flow/editor/{flow.uuid}/", response.url)

        # should have a new revision
        self.assertEqual(2, flow.revisions.count())
