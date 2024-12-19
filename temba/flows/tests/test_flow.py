from datetime import datetime, timezone as tzone
from unittest.mock import patch

from django.urls import reverse
from django.utils import timezone

from temba.api.models import Resthook
from temba.campaigns.models import Campaign, CampaignEvent
from temba.classifiers.models import Classifier
from temba.contacts.models import URN, ContactField, ContactGroup
from temba.flows.models import (
    Flow,
    FlowCategoryCount,
    FlowRun,
    FlowSession,
    FlowStart,
    FlowStartCount,
    FlowUserConflictException,
    FlowVersionConflictException,
)
from temba.flows.tasks import squash_flow_counts, update_session_wait_expires
from temba.globals.models import Global
from temba.orgs.integrations.dtone import DTOneType
from temba.tests import CRUDLTestMixin, TembaTest, matchers
from temba.tests.engine import MockSessionWriter
from temba.triggers.models import Trigger
from temba.utils import json
from temba.utils.uuid import uuid4


class FlowTest(TembaTest, CRUDLTestMixin):
    def setUp(self):
        super().setUp()

        self.contact = self.create_contact("Eric", phone="+250788382382")
        self.contact2 = self.create_contact("Nic", phone="+250788383383")
        self.contact3 = self.create_contact("Norbert", phone="+250788123456")
        self.contact4 = self.create_contact("Teeh", phone="+250788123457", language="por")

        self.other_group = self.create_group("Other", [])

    def test_get_unique_name(self):
        self.assertEqual("Testing", Flow.get_unique_name(self.org, "Testing"))

        # ensure checking against existing flows is case-insensitive
        testing = self.create_flow("TESTING")

        self.assertEqual("Testing 2", Flow.get_unique_name(self.org, "Testing"))
        self.assertEqual("Testing", Flow.get_unique_name(self.org, "Testing", ignore=testing))
        self.assertEqual("Testing", Flow.get_unique_name(self.org2, "Testing"))  # different org

        self.create_flow("Testing 2")

        self.assertEqual("Testing 3", Flow.get_unique_name(self.org, "Testing"))

        # ensure we don't exceed the name length limit
        self.create_flow("X" * 64)

        self.assertEqual(f"{'X' * 62} 2", Flow.get_unique_name(self.org, "X" * 64))

    def test_clean_name(self):
        self.assertEqual("Hello", Flow.clean_name("Hello\0"))
        self.assertEqual("Hello/n", Flow.clean_name("Hello\\n"))
        self.assertEqual("Say 'Hi'", Flow.clean_name('Say "Hi"'))
        self.assertEqual("x" * 64, Flow.clean_name("x" * 100))
        self.assertEqual("a                                b", Flow.clean_name(f"a{' ' * 32}b{' ' * 32}c"))

    @patch("temba.mailroom.queue_interrupt")
    def test_archive(self, mock_queue_interrupt):
        flow = self.create_flow("Test")
        flow.archive(self.admin)

        mock_queue_interrupt.assert_called_once_with(self.org, flow=flow)

        flow.refresh_from_db()
        self.assertEqual(flow.is_archived, True)
        self.assertEqual(flow.is_active, True)

    @patch("temba.mailroom.queue_interrupt")
    def test_release(self, mock_queue_interrupt):
        global1 = Global.get_or_create(self.org, self.admin, "api_key", "API Key", "234325")
        flow = self.create_flow("Test")
        flow.global_dependencies.add(global1)

        flow.release(self.admin)

        mock_queue_interrupt.assert_called_once_with(self.org, flow=flow)

        flow.refresh_from_db()
        self.assertTrue(flow.name.startswith("deleted-"))
        self.assertFalse(flow.is_archived)
        self.assertFalse(flow.is_active)
        self.assertEqual(0, flow.global_dependencies.count())

    def test_get_definition(self):
        favorites = self.get_flow("favorites_v13")

        # fill the definition with junk metadata
        rev = favorites.get_current_revision()
        rev.definition["uuid"] = "Nope"
        rev.definition["name"] = "Not the name"
        rev.definition["revision"] = 1234567
        rev.definition["expire_after_minutes"] = 7654
        rev.save(update_fields=("definition",))

        # definition should use values from flow db object
        definition = favorites.get_definition()
        self.assertEqual(definition["uuid"], str(favorites.uuid))
        self.assertEqual(definition["name"], "Favorites")
        self.assertEqual(definition["revision"], 1)
        self.assertEqual(definition["expire_after_minutes"], 720)

        # when saving a new revision we overwrite metadata
        favorites.save_revision(self.admin, rev.definition)
        rev = favorites.get_current_revision()
        self.assertEqual(rev.definition["uuid"], str(favorites.uuid))
        self.assertEqual(rev.definition["name"], "Favorites")
        self.assertEqual(rev.definition["revision"], 2)
        self.assertEqual(rev.definition["expire_after_minutes"], 720)

        # can't get definition of a flow with no revisions
        favorites.revisions.all().delete()
        self.assertRaises(AssertionError, favorites.get_definition)

    def test_ensure_current_version(self):
        # importing migrates to latest spec version
        flow = self.get_flow("favorites_v13")
        self.assertEqual("13.6.1", flow.version_number)
        self.assertEqual(1, flow.revisions.count())

        # rewind one spec version..
        flow.version_number = "13.0.0"
        flow.save(update_fields=("version_number",))
        rev = flow.revisions.get()
        rev.definition["spec_version"] = "13.0.0"
        rev.spec_version = "13.0.0"
        rev.save()

        old_modified_on = flow.modified_on
        old_saved_on = flow.saved_on

        flow.ensure_current_version()

        # check we migrate to current spec version
        self.assertEqual("13.6.1", flow.version_number)
        self.assertEqual(2, flow.revisions.count())
        self.assertEqual("system", flow.revisions.order_by("id").last().created_by.username)

        # saved on won't have been updated but modified on will
        self.assertEqual(old_saved_on, flow.saved_on)
        self.assertGreater(flow.modified_on, old_modified_on)

    def test_flow_archive_with_campaign(self):
        self.login(self.admin)
        self.get_flow("the_clinic")

        campaign = Campaign.objects.get(name="Appointment Schedule")
        flow = Flow.objects.get(name="Confirm Appointment")

        campaign_event = CampaignEvent.objects.filter(flow=flow, campaign=campaign).first()
        self.assertIsNotNone(campaign_event)

        # do not archive if the campaign is active
        Flow.apply_action_archive(self.admin, Flow.objects.filter(pk=flow.pk))

        flow.refresh_from_db()
        self.assertFalse(flow.is_archived)

        campaign.is_archived = True
        campaign.save()

        # can archive if the campaign is archived
        Flow.apply_action_archive(self.admin, Flow.objects.filter(pk=flow.pk))

        flow.refresh_from_db()
        self.assertTrue(flow.is_archived)

        campaign.is_archived = False
        campaign.save()

        flow.is_archived = False
        flow.save()

        campaign_event.is_active = False
        campaign_event.save()

        # can archive if the campaign is not archived with no active event
        Flow.apply_action_archive(self.admin, Flow.objects.filter(pk=flow.pk))

        flow.refresh_from_db()
        self.assertTrue(flow.is_archived)

    def test_editor(self):
        flow = self.create_flow("Test")

        self.login(self.admin)

        flow_editor_url = reverse("flows.flow_editor", args=[flow.uuid])

        response = self.client.get(flow_editor_url)

        self.assertTrue(response.context["mutable"])
        self.assertTrue(response.context["can_start"])
        self.assertTrue(response.context["can_simulate"])
        self.assertContains(response, reverse("flows.flow_simulate", args=[flow.id]))
        self.assertContains(response, 'id="rp-flow-editor"')

        # flows that are archived can't be edited, started or simulated
        self.login(self.admin)

        flow.is_archived = True
        flow.save(update_fields=("is_archived",))

        response = self.client.get(flow_editor_url)

        self.assertFalse(response.context["mutable"])
        self.assertFalse(response.context["can_start"])
        self.assertFalse(response.context["can_simulate"])

    def test_editor_feature_filters(self):
        flow = self.create_flow("Test")

        self.login(self.admin)

        def assert_features(features: set):
            response = self.client.get(reverse("flows.flow_editor", args=[flow.uuid]))
            self.assertEqual(features, set(json.loads(response.context["feature_filters"])))

        # add a resthook
        Resthook.objects.create(org=flow.org, created_by=self.admin, modified_by=self.admin)
        assert_features({"resthook"})

        # add an NLP classifier
        Classifier.objects.create(org=flow.org, config="", created_by=self.admin, modified_by=self.admin)
        assert_features({"classifier", "resthook"})

        # add a DT One integration
        DTOneType().connect(flow.org, self.admin, "login", "token")
        assert_features({"airtime", "classifier", "resthook"})

        # change our channel to use a whatsapp scheme
        self.channel.schemes = [URN.WHATSAPP_SCHEME]
        self.channel.save()
        assert_features({"whatsapp", "airtime", "classifier", "resthook"})

        # change our channel to use a facebook scheme
        self.channel.schemes = [URN.FACEBOOK_SCHEME]
        self.channel.save()
        assert_features({"facebook", "optins", "airtime", "classifier", "resthook"})

        self.setUpLocations()

        assert_features({"facebook", "optins", "airtime", "classifier", "resthook", "locations"})

    def test_save_revision(self):
        self.login(self.admin)
        self.client.post(
            reverse("flows.flow_create"), {"name": "Go Flow", "flow_type": Flow.TYPE_MESSAGE, "base_language": "eng"}
        )
        flow = Flow.objects.get(
            org=self.org, name="Go Flow", flow_type=Flow.TYPE_MESSAGE, version_number=Flow.CURRENT_SPEC_VERSION
        )

        # can't save older spec version over newer
        definition = flow.revisions.order_by("id").last().definition
        definition["spec_version"] = Flow.FINAL_LEGACY_VERSION

        with self.assertRaises(FlowVersionConflictException):
            flow.save_revision(self.admin, definition)

        # can't save older revision over newer
        definition["spec_version"] = Flow.CURRENT_SPEC_VERSION
        definition["revision"] = 0

        with self.assertRaises(FlowUserConflictException):
            flow.save_revision(self.admin, definition)

    def test_clone(self):
        flow = self.create_flow("123456789012345678901234567890123456789012345678901234567890")  # 60 chars
        flow.expires_after_minutes = 60
        flow.save(update_fields=("expires_after_minutes",))

        copy1 = flow.clone(self.admin)

        self.assertNotEqual(flow.id, copy1.id)
        self.assertEqual(60, copy1.expires_after_minutes)

        # name should start with "Copy of" and be truncated to 64 chars
        self.assertEqual("Copy of 12345678901234567890123456789012345678901234567890123456", copy1.name)

        # cloning again should generate a unique name
        copy2 = flow.clone(self.admin)
        self.assertEqual("Copy of 123456789012345678901234567890123456789012345678901234 2", copy2.name)
        copy3 = flow.clone(self.admin)
        self.assertEqual("Copy of 123456789012345678901234567890123456789012345678901234 3", copy3.name)

        # ensure that truncating doesn't leave trailing spaces
        flow2 = self.create_flow("abcdefghijklmnopqrstuvwxyzabcdefghijklmnopqrstuvwxyzabc efghijkl")
        copy2 = flow2.clone(self.admin)
        self.assertEqual("Copy of abcdefghijklmnopqrstuvwxyzabcdefghijklmnopqrstuvwxyzabc", copy2.name)

    def test_copy_group_split_no_name(self):
        flow = self.get_flow("group_split_no_name")
        flow_def = flow.get_definition()

        copy = flow.clone(self.admin)
        copy_def = copy.get_definition()

        self.assertEqual(len(copy_def["nodes"]), 1)
        self.assertEqual(len(copy_def["nodes"][0]["router"]["cases"]), 1)
        self.assertEqual(
            copy_def["nodes"][0]["router"]["cases"][0],
            {
                "uuid": matchers.UUID4String(),
                "type": "has_group",
                "arguments": [matchers.UUID4String()],
                "category_uuid": matchers.UUID4String(),
            },
        )

        # check that the original and the copy reference the same group
        self.assertEqual(
            flow_def["nodes"][0]["router"]["cases"][0]["arguments"],
            copy_def["nodes"][0]["router"]["cases"][0]["arguments"],
        )

    def test_get_activity(self):
        flow1 = self.create_flow("Test 1")
        flow2 = self.create_flow("Test 2")

        flow1.counts.create(scope="node:01c175da-d23d-40a4-a845-c4a9bb4b481a", count=3)
        flow1.counts.create(scope="node:01c175da-d23d-40a4-a845-c4a9bb4b481a", count=1)
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

        self.assertEqual(
            (
                {"01c175da-d23d-40a4-a845-c4a9bb4b481a": 4, "400d6b5e-c963-42a1-a06c-50bb9b1e38b1": 5},
                {
                    "1fff74f4-c81f-4f4c-a03d-58d113c17da1:01c175da-d23d-40a4-a845-c4a9bb4b481a": 7,
                    "6f607948-f3f0-4a6a-94b8-7fdd877895ca:400d6b5e-c963-42a1-a06c-50bb9b1e38b1": 5,
                },
            ),
            flow1.get_activity(),
        )
        self.assertEqual(
            ({}, {"a4fe3ada-b062-47e4-be58-bcbe1bca31b4:74a53ff4-fe63-4d89-875e-cae3caca177c": 6}), flow2.get_activity()
        )

    def test_get_category_counts(self):
        def assertCount(counts, result_key, category_name, truth):
            found = False
            for count in counts:
                if count["key"] == result_key:
                    categories = count["categories"]
                    for category in categories:
                        if category["name"] == category_name:
                            found = True
                            self.assertEqual(category["count"], truth)
            self.assertTrue(found)

        favorites = self.get_flow("favorites_v13")
        flow_nodes = favorites.get_definition()["nodes"]
        color_prompt = flow_nodes[0]
        color_other = flow_nodes[1]
        color_split = flow_nodes[2]
        beer_prompt = flow_nodes[3]
        beer_split = flow_nodes[5]
        name_prompt = flow_nodes[6]
        name_split = flow_nodes[7]

        # add in some fake data
        for i in range(0, 10):
            contact = self.create_contact("Contact %d" % i, phone="+120655530%d" % i)
            (
                MockSessionWriter(contact, favorites)
                .visit(color_prompt)
                .send_msg("What is your favorite color?", self.channel)
                .visit(color_split)
                .wait()
                .resume(msg=self.create_incoming_msg(contact, "blue"))
                .set_result("Color", "blue", "Blue", "blue")
                .visit(beer_prompt)
                .send_msg("Good choice, I like Blue too! What is your favorite beer?", self.channel)
                .visit(beer_split)
                .wait()
                .resume(msg=self.create_incoming_msg(contact, "primus"))
                .set_result("Beer", "primus", "Primus", "primus")
                .visit(name_prompt)
                .send_msg("Lastly, what is your name?", self.channel)
                .visit(name_split)
                .wait()
                .resume(msg=self.create_incoming_msg(contact, "russell"))
                .set_result("Name", "russell", "All Responses", "russell")
                .complete()
                .save()
            )

        for i in range(0, 5):
            contact = self.create_contact("Contact %d" % i, phone="+120655531%d" % i)
            (
                MockSessionWriter(contact, favorites)
                .visit(color_prompt)
                .send_msg("What is your favorite color?", self.channel)
                .visit(color_split)
                .wait()
                .resume(msg=self.create_incoming_msg(contact, "red"))
                .set_result("Color", "red", "Red", "red")
                .visit(beer_prompt)
                .send_msg("Good choice, I like Red too! What is your favorite beer?", self.channel)
                .visit(beer_split)
                .wait()
                .resume(msg=self.create_incoming_msg(contact, "primus"))
                .set_result("Beer", "primus", "Primus", "primus")
                .visit(name_prompt)
                .send_msg("Lastly, what is your name?", self.channel)
                .visit(name_split)
                .wait()
                .resume(msg=self.create_incoming_msg(contact, "earl"))
                .set_result("Name", "earl", "All Responses", "earl")
                .complete()
                .save()
            )

        # test update flow values
        for i in range(0, 5):
            contact = self.create_contact("Contact %d" % i, phone="+120655532%d" % i)
            (
                MockSessionWriter(contact, favorites)
                .visit(color_prompt)
                .send_msg("What is your favorite color?", self.channel)
                .visit(color_split)
                .wait()
                .resume(msg=self.create_incoming_msg(contact, "orange"))
                .set_result("Color", "orange", "Other", "orange")
                .visit(color_other)
                .send_msg("I don't know that one, try again please.", self.channel)
                .visit(color_split)
                .wait()
                .save()
                .resume(msg=self.create_incoming_msg(contact, "green"))
                .set_result("Color", "green", "Green", "green")
                .visit(beer_prompt)
                .send_msg("Good choice, I like Green too! What is your favorite beer?", self.channel)
                .visit(beer_split)
                .wait()
                .resume(msg=self.create_incoming_msg(contact, "skol"))
                .set_result("Beer", "skol", "Skol", "skol")
                .visit(name_prompt)
                .send_msg("Lastly, what is your name?", self.channel)
                .visit(name_split)
                .wait()
                .resume(msg=self.create_incoming_msg(contact, "bobby"))
                .set_result("Name", "bobby", "All Responses", "bobby")
                .complete()
                .save()
            )

        counts = favorites.get_category_counts()

        assertCount(counts, "color", "Blue", 10)
        assertCount(counts, "color", "Red", 5)
        assertCount(counts, "beer", "Primus", 15)

        # name shouldn't be included since it's open ended
        self.assertNotIn('"name": "Name"', json.dumps(counts))

        # five oranges went back and became greens
        assertCount(counts, "color", "Other", 0)
        assertCount(counts, "color", "Green", 5)

        # now remap the uuid for our color node
        flow_json = favorites.get_definition()
        flow_json = json.loads(json.dumps(flow_json).replace(color_split["uuid"], str(uuid4())))
        flow_nodes = flow_json["nodes"]
        color_prompt = flow_nodes[0]
        color_other = flow_nodes[1]
        color_split = flow_nodes[2]

        favorites.save_revision(self.admin, flow_json)

        # send a few more runs through our updated flow
        for i in range(0, 3):
            contact = self.create_contact("Contact %d" % i, phone="+120655533%d" % i)
            (
                MockSessionWriter(contact, favorites)
                .visit(color_prompt)
                .send_msg("What is your favorite color?", self.channel)
                .visit(color_split)
                .wait()
                .resume(msg=self.create_incoming_msg(contact, "red"))
                .set_result("Color", "red", "Red", "red")
                .visit(beer_prompt)
                .send_msg("Good choice, I like Red too! What is your favorite beer?", self.channel)
                .visit(beer_split)
                .wait()
                .resume(msg=self.create_incoming_msg(contact, "turbo"))
                .set_result("Beer", "turbo", "Turbo King", "turbo")
                .visit(name_prompt)
                .wait()
                .save()
            )

        # should now have three more reds
        counts = favorites.get_category_counts()
        assertCount(counts, "color", "Red", 8)
        assertCount(counts, "beer", "Turbo King", 3)

        # now delete the color split and repoint nodes to the beer split
        flow_json["nodes"].pop(2)
        for node in flow_json["nodes"]:
            for exit in node["exits"]:
                if exit.get("destination_uuid") == color_split["uuid"]:
                    exit["destination_uuid"] = beer_split["uuid"]

        favorites.save_revision(self.admin, flow_json)

        # now the color counts have been removed, but beer is still there
        counts = favorites.get_category_counts()
        self.assertEqual(["beer"], [c["key"] for c in counts])
        assertCount(counts, "beer", "Turbo King", 3)

        # make sure it still works after ze squashings
        self.assertEqual(76, FlowCategoryCount.objects.all().count())
        squash_flow_counts()
        self.assertEqual(9, FlowCategoryCount.objects.all().count())
        counts = favorites.get_category_counts()
        assertCount(counts, "beer", "Turbo King", 3)

        # test tostring
        str(FlowCategoryCount.objects.all().first())

        # and if we delete our runs, things zero out
        for run in FlowRun.objects.all():
            run.delete()

        counts = favorites.get_category_counts()
        assertCount(counts, "beer", "Turbo King", 0)

    def test_category_counts_with_null_categories(self):
        flow = self.get_flow("color_v13")
        flow_nodes = flow.get_definition()["nodes"]
        color_prompt = flow_nodes[0]
        color_split = flow_nodes[4]

        msg = self.create_incoming_msg(self.contact, "blue")
        run = (
            MockSessionWriter(self.contact, flow)
            .visit(color_prompt)
            .send_msg("What is your favorite color?", self.channel)
            .visit(color_split)
            .wait()
            .resume(msg=msg)
            .set_result("Color", "blue", "Blue", "blue")
            .complete()
            .save()
        ).session.runs.get()

        FlowCategoryCount.objects.get(category_name="Blue", result_name="Color", result_key="color", count=1)

        # get our run and clear the category
        run = FlowRun.objects.get(flow=flow, contact=self.contact)
        results = run.results
        del results["color"]["category"]
        results["color"]["created_on"] = timezone.now()
        run.save(update_fields=["results", "modified_on"])

        # should have added a negative one now
        self.assertEqual(2, FlowCategoryCount.objects.filter(category_name="Blue", result_name="Color").count())
        FlowCategoryCount.objects.get(category_name="Blue", result_name="Color", result_key="color", count=-1)

    def test_start_counts(self):
        # create start for 10 contacts
        flow = self.create_flow("Test")
        start = FlowStart.objects.create(org=self.org, flow=flow, created_by=self.admin)
        for i in range(10):
            start.contacts.add(self.create_contact("Bob", urns=[f"twitter:bobby{i}"]))

        # create runs for first 5
        for c in start.contacts.order_by("id")[:5]:
            MockSessionWriter(contact=c, flow=flow, start=start).wait().save()

        # check our count
        self.assertEqual(FlowStartCount.get_count(start), 5)

        # create runs for last 5
        for c in start.contacts.order_by("id")[5:]:
            MockSessionWriter(contact=c, flow=flow, start=start).wait().save()

        # check our count
        self.assertEqual(FlowStartCount.get_count(start), 10)

        # squash them
        FlowStartCount.squash()
        self.assertEqual(FlowStartCount.get_count(start), 10)

    def test_flow_keyword_update(self):
        self.login(self.admin)
        flow = Flow.create(self.org, self.admin, "Flow")
        flow.flow_type = Flow.TYPE_SURVEY
        flow.save()

        # keywords aren't an option for survey flows
        response = self.client.get(reverse("flows.flow_update", args=[flow.pk]))
        self.assertNotIn("keyword_triggers", response.context["form"].fields)
        self.assertNotIn("ignore_triggers", response.context["form"].fields)

        # send update with triggers and ignore flag anyways
        post_data = dict()
        post_data["name"] = "Flow With Keyword Triggers"
        post_data["keyword_triggers"] = "notallowed"
        post_data["ignore_keywords"] = True
        post_data["expires_after_minutes"] = 60 * 12
        response = self.client.post(reverse("flows.flow_update", args=[flow.pk]), post_data, follow=True)

        # still shouldn't have any triggers
        flow.refresh_from_db()
        self.assertFalse(flow.ignore_triggers)
        self.assertEqual(0, flow.triggers.all().count())

    def test_flow_update_of_inactive_flow(self):
        flow = self.get_flow("favorites")
        flow.release(self.admin)

        post_data = {"name": "Flow that does not exist"}

        self.login(self.admin)
        response = self.client.post(reverse("flows.flow_update", args=[flow.pk]), post_data)

        # can't delete already released flow
        self.assertEqual(response.status_code, 404)

    def test_importing_dependencies(self):
        # create channel to be matched by name
        channel = self.create_channel("TG", "RapidPro Test", "12345324635")

        flow = self.get_flow("dependencies_v13")
        flow_def = flow.get_definition()

        # global should have been created with blank value
        self.assertTrue(self.org.globals.filter(name="Org Name", key="org_name", value="").exists())

        # topic should have been created too
        self.assertTrue(self.org.topics.filter(name="Support").exists())

        # fields created with type if exists in export
        self.assertTrue(self.org.fields.filter(key="cat_breed", name="Cat Breed", value_type="T").exists())
        self.assertTrue(self.org.fields.filter(key="french_age", value_type="N").exists())

        # reference to channel changed to match existing channel by name
        self.assertEqual(
            {"uuid": str(channel.uuid), "name": "RapidPro Test"}, flow_def["nodes"][0]["actions"][4]["channel"]
        )

        # reference to classifier unchanged since it doesn't exist
        self.assertEqual(
            {"uuid": "891a1c5d-1140-4fd0-bd0d-a919ea25abb6", "name": "Feelings"},
            flow_def["nodes"][7]["actions"][0]["classifier"],
        )

    def test_flow_metadata(self):
        # test importing both old and new flow formats
        for flow_file in ("favorites", "favorites_v13"):
            flow = self.get_flow(flow_file)

            self.assertEqual(
                flow.metadata["results"],
                [
                    {
                        "key": "color",
                        "name": "Color",
                        "categories": ["Red", "Green", "Blue", "Cyan", "Other"],
                        "node_uuids": [matchers.UUID4String()],
                    },
                    {
                        "key": "beer",
                        "name": "Beer",
                        "categories": ["Mutzig", "Primus", "Turbo King", "Skol", "Other"],
                        "node_uuids": [matchers.UUID4String()],
                    },
                    {
                        "key": "name",
                        "name": "Name",
                        "categories": ["All Responses"],
                        "node_uuids": [matchers.UUID4String()],
                    },
                ],
            )
            self.assertEqual(len(flow.metadata["parent_refs"]), 0)

    def test_group_send(self):
        # create an inactive group with the same name, to test that this doesn't blow up our import
        group = ContactGroup.get_or_create(self.org, self.admin, "Survey Audience")
        group.release(self.admin)

        # and create another as well
        ContactGroup.get_or_create(self.org, self.admin, "Survey Audience")

        # fetching a flow with a group send shouldn't throw
        self.get_flow("group_send_flow")

    def test_flow_delete_of_inactive_flow(self):
        flow = self.create_flow("Test")
        flow.release(self.admin)

        self.login(self.admin)
        response = self.client.post(reverse("flows.flow_delete", args=[flow.pk]))

        # can't delete already released flow
        self.assertEqual(response.status_code, 404)

    def test_delete(self):
        flow = self.get_flow("favorites_v13")
        flow_nodes = flow.get_definition()["nodes"]
        color_prompt = flow_nodes[0]
        color_split = flow_nodes[2]
        beer_prompt = flow_nodes[3]
        beer_split = flow_nodes[5]

        # create a campaign that contains this flow
        friends = self.create_group("Friends", [])
        poll_date = self.create_field("poll_date", "Poll Date", value_type=ContactField.TYPE_DATETIME)

        campaign = Campaign.create(self.org, self.admin, Campaign.get_unique_name(self.org, "Favorite Poll"), friends)
        event1 = CampaignEvent.create_flow_event(
            self.org, self.admin, campaign, poll_date, offset=0, unit="D", flow=flow, delivery_hour="13"
        )

        # create a trigger that contains this flow
        trigger = Trigger.create(
            self.org, self.admin, Trigger.TYPE_KEYWORD, flow, keywords=["poll"], match_type=Trigger.MATCH_FIRST_WORD
        )

        # run the flow
        (
            MockSessionWriter(self.contact, flow)
            .visit(color_prompt)
            .visit(color_split)
            .wait()
            .resume(msg=self.create_incoming_msg(self.contact, "RED"))
            .visit(beer_prompt)
            .visit(beer_split)
            .wait()
            .save()
        )

        # run it again to completion
        joe = self.create_contact("Joe", phone="1234")
        (
            MockSessionWriter(joe, flow)
            .visit(color_prompt)
            .visit(color_split)
            .wait()
            .resume(msg=self.create_incoming_msg(joe, "green"))
            .visit(beer_prompt)
            .visit(beer_split)
            .wait()
            .resume(msg=self.create_incoming_msg(joe, "primus"))
            .complete()
            .save()
        )

        # try to remove the flow, not logged in, no dice
        response = self.client.post(reverse("flows.flow_delete", args=[flow.uuid]))
        self.assertLoginRedirect(response)

        # login as admin
        self.login(self.admin)
        response = self.client.post(reverse("flows.flow_delete", args=[flow.uuid]))
        self.assertEqual(200, response.status_code)

        # flow should no longer be active
        flow.refresh_from_db()
        self.assertFalse(flow.is_active)

        # runs should not be deleted
        self.assertEqual(flow.runs.count(), 2)

        # our campaign event and trigger should no longer be active
        event1.refresh_from_db()
        self.assertFalse(event1.is_active)

        trigger.refresh_from_db()
        self.assertFalse(trigger.is_active)

    def test_delete_with_dependencies(self):
        self.login(self.admin)

        self.get_flow("dependencies")
        self.get_flow("dependencies_voice")
        parent = Flow.objects.filter(name="Dependencies").first()
        child = Flow.objects.filter(name="Child Flow").first()
        voice = Flow.objects.filter(name="Voice Dependencies").first()

        contact_fields = (
            {"key": "contact_age", "name": "Contact Age"},
            # fields based on parent and child references
            {"key": "top"},
            {"key": "bottom"},
            # replies
            {"key": "chw"},
            # url attachemnts
            {"key": "attachment"},
            # dynamic groups
            {"key": "cat_breed", "name": "Cat Breed"},
            {"key": "organization"},
            # sending messages
            {"key": "recipient"},
            {"key": "message"},
            # sending emails
            {"key": "email_message", "name": "Email Message"},
            {"key": "subject"},
            # trigger someone else
            {"key": "other_phone", "name": "Other Phone"},
            # rules and localizations
            {"key": "rule"},
            {"key": "french_rule", "name": "French Rule"},
            {"key": "french_age", "name": "French Age"},
            {"key": "french_fries", "name": "French Fries"},
            # updating contacts
            {"key": "favorite_cat", "name": "Favorite Cat"},
            {"key": "next_cat_fact", "name": "Next Cat Fact"},
            {"key": "last_cat_fact", "name": "Last Cat Fact"},
            # webhook urls
            {"key": "webhook"},
            # expression splits
            {"key": "expression_split", "name": "Expression Split"},
            # voice says
            {"key": "play_message", "name": "Play Message", "flow": voice},
            {"key": "voice_rule", "name": "Voice Rule", "flow": voice},
            # voice plays (recordings)
            {"key": "voice_recording", "name": "Voice Recording", "flow": voice},
        )

        for field_spec in contact_fields:
            key = field_spec.get("key")
            name = field_spec.get("name", key.capitalize())
            flow = field_spec.get("flow", parent)

            # make sure our field exists after import
            field = self.org.fields.filter(key=key, name=name, is_system=False, is_proxy=False).first()
            self.assertIsNotNone(field, "Couldn't find field %s (%s)" % (key, name))

            # and our flow is dependent on us
            self.assertIsNotNone(
                flow.field_dependencies.filter(key__in=[key]).first(),
                "Flow is missing dependency on %s (%s)" % (key, name),
            )

        # we can delete our child flow and the parent ('Dependencies') will be marked as having issues
        self.client.post(reverse("flows.flow_delete", args=[child.uuid]))

        parent = Flow.objects.filter(name="Dependencies").get()
        child.refresh_from_db()

        self.assertFalse(child.is_active)
        self.assertTrue(parent.has_issues)
        self.assertNotIn(child, parent.flow_dependencies.all())

        # deleting our parent flow should also work
        self.client.post(reverse("flows.flow_delete", args=[parent.uuid]))

        parent.refresh_from_db()
        self.assertFalse(parent.is_active)
        self.assertEqual(0, parent.field_dependencies.all().count())
        self.assertEqual(0, parent.flow_dependencies.all().count())
        self.assertEqual(0, parent.group_dependencies.all().count())

    def test_update_expiration_task(self):
        flow1 = self.create_flow("Test 1")
        flow2 = self.create_flow("Test 2")

        # create waiting session and run for flow 1
        session1 = FlowSession.objects.create(
            uuid=uuid4(),
            org=self.org,
            contact=self.contact,
            current_flow=flow1,
            status=FlowSession.STATUS_WAITING,
            output_url="http://sessions.com/123.json",
            wait_started_on=datetime(2022, 1, 1, 0, 0, 0, 0, tzone.utc),
            wait_expires_on=datetime(2022, 1, 2, 0, 0, 0, 0, tzone.utc),
            wait_resume_on_expire=False,
        )

        # create non-waiting session for flow 1
        session2 = FlowSession.objects.create(
            uuid=uuid4(),
            org=self.org,
            contact=self.contact,
            current_flow=flow1,
            status=FlowSession.STATUS_COMPLETED,
            output_url="http://sessions.com/234.json",
            wait_started_on=datetime(2022, 1, 1, 0, 0, 0, 0, tzone.utc),
            wait_expires_on=None,
            wait_resume_on_expire=False,
            ended_on=timezone.now(),
        )

        # create waiting session for flow 2
        session3 = FlowSession.objects.create(
            uuid=uuid4(),
            org=self.org,
            contact=self.contact,
            current_flow=flow2,
            status=FlowSession.STATUS_WAITING,
            output_url="http://sessions.com/345.json",
            wait_started_on=datetime(2022, 1, 1, 0, 0, 0, 0, tzone.utc),
            wait_expires_on=datetime(2022, 1, 2, 0, 0, 0, 0, tzone.utc),
            wait_resume_on_expire=False,
        )

        # update flow 1 expires to 2 hours
        flow1.expires_after_minutes = 120
        flow1.save(update_fields=("expires_after_minutes",))

        update_session_wait_expires(flow1.id)

        # new session expiration should be wait_started_on + 1 hour
        session1.refresh_from_db()
        self.assertEqual(datetime(2022, 1, 1, 2, 0, 0, 0, tzone.utc), session1.wait_expires_on)

        # other sessions should be unchanged
        session2.refresh_from_db()
        session3.refresh_from_db()
        self.assertIsNone(session2.wait_expires_on)
        self.assertEqual(datetime(2022, 1, 2, 0, 0, 0, 0, tzone.utc), session3.wait_expires_on)
