import datetime
import decimal
import io
import os
import re
from datetime import timedelta
from unittest.mock import PropertyMock, patch

import iso8601
import pytz
from openpyxl import load_workbook

from django.conf import settings
from django.contrib.auth.models import Group
from django.db.models.functions import TruncDate
from django.test.utils import override_settings
from django.urls import reverse
from django.utils import timezone
from django.utils.encoding import force_text

from temba.api.models import Resthook
from temba.archives.models import Archive
from temba.campaigns.models import Campaign, CampaignEvent
from temba.classifiers.models import Classifier
from temba.contacts.models import URN, Contact, ContactField, ContactGroup
from temba.globals.models import Global
from temba.mailroom import FlowValidationException
from temba.orgs.integrations.dtone import DTOneType
from temba.templates.models import Template, TemplateTranslation
from temba.tests import AnonymousOrg, CRUDLTestMixin, MockResponse, TembaTest, matchers, mock_mailroom
from temba.tests.engine import MockSessionWriter
from temba.tests.s3 import MockS3Client, jsonlgz_encode
from temba.tickets.models import Ticketer
from temba.triggers.models import Trigger
from temba.utils import json
from temba.utils.uuid import uuid4

from .checks import mailroom_url
from .models import (
    ExportFlowResultsTask,
    Flow,
    FlowCategoryCount,
    FlowLabel,
    FlowNodeCount,
    FlowPathCount,
    FlowPathRecentRun,
    FlowRevision,
    FlowRun,
    FlowRunCount,
    FlowSession,
    FlowStart,
    FlowStartCount,
    FlowUserConflictException,
    FlowVersionConflictException,
    get_flow_user,
)
from .tasks import squash_flowcounts, trim_flow_revisions, trim_flow_sessions_and_starts, update_run_expirations_task
from .views import FlowCRUDL


class FlowTest(TembaTest):
    def setUp(self):
        super().setUp()

        self.contact = self.create_contact("Eric", phone="+250788382382")
        self.contact2 = self.create_contact("Nic", phone="+250788383383")
        self.contact3 = self.create_contact("Norbert", phone="+250788123456")
        self.contact4 = self.create_contact("Teeh", phone="+250788123457", language="por")

        self.other_group = self.create_group("Other", [])

    def test_get_flow_user(self):
        user = get_flow_user(self.org)
        self.assertEqual(user.pk, get_flow_user(self.org).pk)

    def test_get_unique_name(self):
        flow1 = Flow.create(self.org, self.admin, Flow.get_unique_name(self.org, "Sheep Poll"), base_language="base")
        self.assertEqual(flow1.name, "Sheep Poll")

        flow2 = Flow.create(self.org, self.admin, Flow.get_unique_name(self.org, "Sheep Poll"), base_language="base")
        self.assertEqual(flow2.name, "Sheep Poll 2")

        flow3 = Flow.create(self.org, self.admin, Flow.get_unique_name(self.org, "Sheep Poll"), base_language="base")
        self.assertEqual(flow3.name, "Sheep Poll 3")

        self.assertEqual(Flow.get_unique_name(self.org2, "Sheep Poll"), "Sheep Poll")  # different org

    @patch("temba.mailroom.queue_interrupt")
    def test_archive(self, mock_queue_interrupt):
        flow = self.get_flow("color")
        flow.archive(self.admin)

        mock_queue_interrupt.assert_called_once_with(self.org, flow=flow)

        flow.refresh_from_db()
        self.assertEqual(flow.is_archived, True)
        self.assertEqual(flow.is_active, True)

    @patch("temba.mailroom.queue_interrupt")
    def test_release(self, mock_queue_interrupt):
        global1 = Global.get_or_create(self.org, self.admin, "api_key", "API Key", "234325")
        flow = self.get_flow("color")
        flow.global_dependencies.add(global1)

        flow.release(self.admin)

        mock_queue_interrupt.assert_called_once_with(self.org, flow=flow)

        flow.refresh_from_db()
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
        self.assertEqual("13.1.0", flow.version_number)
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
        self.assertEqual("13.1.0", flow.version_number)
        self.assertEqual(2, flow.revisions.count())
        self.assertEqual(get_flow_user(self.org), flow.revisions.order_by("id").last().created_by)

        # saved on won't have been updated but modified on will
        self.assertEqual(old_saved_on, flow.saved_on)
        self.assertGreater(flow.modified_on, old_modified_on)

    def test_campaign_filter(self):
        self.login(self.admin)
        self.get_flow("the_clinic")

        # should have a list of four flows for our appointment schedule
        response = self.client.get(reverse("flows.flow_list"))
        self.assertContains(response, "Appointment Schedule")
        self.assertEqual(4, response.context["campaigns"][0]["count"])

        campaign = Campaign.objects.filter(name="Appointment Schedule").first()
        self.assertIsNotNone(campaign)

        # check that our four flows in the campaign are there
        response = self.client.get(reverse("flows.flow_campaign", args=[campaign.id]))
        self.assertContains(response, "Confirm Appointment")
        self.assertContains(response, "Start Notifications")
        self.assertContains(response, "Stop Notifications")
        self.assertContains(response, "Appointment Followup")

        # check we can't see farmers
        farmers = ContactGroup.create_static(self.org2, self.admin, "Farmers")
        campaign2 = Campaign.create(self.org2, self.admin, Campaign.get_unique_name(self.org, "Reminders"), farmers)

        response = self.client.get(reverse("flows.flow_campaign", args=[campaign2.id]))
        self.assertLoginRedirect(response)

    def test_facebook_warnings(self):
        no_topic = self.get_flow("pick_a_number")
        with_topic = self.get_flow("with_message_topic")

        # bring up broadcast dialog
        self.login(self.admin)
        response = self.client.get(reverse("flows.flow_broadcast", args=[no_topic.id]))

        # no warning, we don't have a facebook channel
        self.assertNotContains(response, "does not specify a Facebook topic")

        # change our channel to use a facebook scheme
        self.channel.schemes = [URN.FACEBOOK_SCHEME]
        self.channel.save()

        # should see a warning for no topic now
        response = self.client.get(reverse("flows.flow_broadcast", args=[no_topic.id]))
        self.assertContains(response, "does not specify a Facebook topic")

        # warning shouldn't be present for flow with a topic
        response = self.client.get(reverse("flows.flow_broadcast", args=[with_topic.id]))
        self.assertNotContains(response, "does not specify a Facebook topic")

    def test_template_warnings(self):
        self.login(self.admin)
        flow = self.get_flow("whatsapp_template")

        # bring up broadcast dialog
        self.login(self.admin)
        response = self.client.get(reverse("flows.flow_broadcast", args=[flow.id]))

        # no warning, we don't have a whatsapp channel
        self.assertNotContains(response, "affirmation")

        # change our channel to use a whatsapp scheme
        self.channel.schemes = [URN.WHATSAPP_SCHEME]
        self.channel.save()

        # clear dependencies, this will cause our flow to look like it isn't using templates
        metadata = flow.metadata
        flow.metadata = {}
        flow.save(update_fields=["metadata"])

        response = self.client.get(reverse("flows.flow_broadcast", args=[flow.id]))
        self.assertContains(response, "does not use message")

        # restore our dependency
        flow.metadata = metadata
        flow.save(update_fields=["metadata"])

        # template doesn't exit, will be warned
        response = self.client.get(reverse("flows.flow_broadcast", args=[flow.id]))
        self.assertContains(response, "affirmation")

        # create the template, but no translations
        Template.objects.create(org=self.org, name="affirmation", uuid="f712e05c-bbed-40f1-b3d9-671bb9b60775")

        # will be warned again
        response = self.client.get(reverse("flows.flow_broadcast", args=[flow.id]))
        self.assertContains(response, "affirmation")

        # create a translation, but not approved
        TemplateTranslation.get_or_create(
            self.channel,
            "affirmation",
            "eng",
            "US",
            "good boy",
            0,
            TemplateTranslation.STATUS_REJECTED,
            "id1",
            "foo_namespace",
        )

        response = self.client.get(reverse("flows.flow_broadcast", args=[flow.id]))
        self.assertContains(response, "affirmation")

        # finally, set our translation to approved
        TemplateTranslation.objects.update(status=TemplateTranslation.STATUS_APPROVED)

        # no warnings again
        response = self.client.get(reverse("flows.flow_broadcast", args=[flow.id]))
        self.assertNotContains(response, "affirmation")

    def test_flow_archive_with_campaign(self):
        self.login(self.admin)
        self.get_flow("the_clinic")

        campaign = Campaign.objects.filter(name="Appointment Schedule").first()
        self.assertIsNotNone(campaign)
        flow = Flow.objects.filter(name="Confirm Appointment").first()
        self.assertIsNotNone(flow)
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
        flow = self.get_flow("color")

        self.login(self.admin)

        response = self.client.get(reverse("flows.flow_editor", args=[flow.uuid]))

        self.assertTrue(response.context["mutable"])
        self.assertTrue(response.context["can_start"])
        self.assertTrue(response.context["can_simulate"])
        self.assertContains(response, reverse("flows.flow_simulate", args=[flow.id]))
        self.assertContains(response, "id='rp-flow-editor'")

        # customer service gets a service button
        csrep = self.create_user("csrep")
        csrep.groups.add(Group.objects.get(name="Customer Support"))
        csrep.is_staff = True
        csrep.save()

        self.login(csrep)

        response = self.client.get(reverse("flows.flow_editor", args=[flow.uuid]))
        self.assertContains(response, "Service")

        # flows that are archived can't be edited, started or simulated
        self.login(self.admin)

        flow.is_archived = True
        flow.save(update_fields=("is_archived",))

        response = self.client.get(reverse("flows.flow_editor", args=[flow.uuid]))

        self.assertFalse(response.context["mutable"])
        self.assertFalse(response.context["can_start"])
        self.assertFalse(response.context["can_simulate"])
        self.assertNotContains(response, reverse("flows.flow_simulate", args=[flow.id]))

    def test_editor_feature_filters(self):
        flow = self.create_flow()

        self.login(self.admin)

        def assert_features(features: set):
            response = self.client.get(reverse("flows.flow_editor", args=[flow.uuid]))
            self.assertEqual(features, set(json.loads(response.context["feature_filters"])))

        # every org has a ticketer now...
        assert_features({"ticketer"})

        # add a resthook
        Resthook.objects.create(org=flow.org, created_by=self.admin, modified_by=self.admin)
        assert_features({"ticketer", "resthook"})

        # add an NLP classifier
        Classifier.objects.create(org=flow.org, config="", created_by=self.admin, modified_by=self.admin)
        assert_features({"classifier", "ticketer", "resthook"})

        # add a DT One integration
        DTOneType().connect(flow.org, self.admin, "login", "token")
        assert_features({"airtime", "classifier", "ticketer", "resthook"})

        # change our channel to use a whatsapp scheme
        self.channel.schemes = [URN.WHATSAPP_SCHEME]
        self.channel.save()
        assert_features({"whatsapp", "airtime", "classifier", "ticketer", "resthook"})

        # change our channel to use a facebook scheme
        self.channel.schemes = [URN.FACEBOOK_SCHEME]
        self.channel.save()
        assert_features({"facebook", "airtime", "classifier", "ticketer", "resthook"})

        self.setUpLocations()

        assert_features({"facebook", "airtime", "classifier", "ticketer", "resthook", "locations"})

    def test_save_revision(self):
        self.login(self.admin)
        self.client.post(reverse("flows.flow_create"), {"name": "Go Flow", "flow_type": Flow.TYPE_MESSAGE})
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

    def test_copy(self):
        flow = self.get_flow("color")

        # pick a really long name so we have to concatenate
        flow.name = "Color Flow is a long name to use for something like this"
        flow.expires_after_minutes = 60
        flow.save()

        # now create a copy
        copy = Flow.copy(flow, self.admin)

        # expiration should be copied too
        self.assertEqual(60, copy.expires_after_minutes)

        # should have a different id
        self.assertNotEqual(flow.id, copy.id)

        # Name should start with "Copy of"
        self.assertEqual("Copy of Color Flow is a long name to use for something like thi", copy.name)

    def test_copy_group_split_no_name(self):
        flow = self.get_flow("group_split_no_name")
        flow_def = flow.get_definition()

        copy = Flow.copy(flow, self.admin)

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

    def test_activity(self):
        flow = self.get_flow("favorites_v13")
        flow_nodes = flow.get_definition()["nodes"]
        color_prompt = flow_nodes[0]
        color_other = flow_nodes[1]
        color_split = flow_nodes[2]
        beer_prompt = flow_nodes[3]
        beer_split = flow_nodes[5]
        name_prompt = flow_nodes[6]
        name_split = flow_nodes[7]
        end_prompt = flow_nodes[8]

        # we don't know this shade of green, it should route us to the beginning again
        session1 = (
            MockSessionWriter(self.contact, flow)
            .visit(color_prompt)
            .send_msg("What is your favorite color?", self.channel)
            .visit(color_split)
            .wait()
            .resume(msg=self.create_incoming_msg(self.contact, "chartreuse"))
            .visit(color_other)
            .send_msg("I don't know that color. Try again.")
            .visit(color_split)
            .wait()
            .save()
        )

        self.assertEqual({color_split["uuid"]: 1}, FlowNodeCount.get_totals(flow))

        (active, visited) = flow.get_activity()

        self.assertEqual({color_split["uuid"]: 1}, active)
        self.assertEqual(
            {
                f'{color_prompt["exits"][0]["uuid"]}:{color_split["uuid"]}': 1,
                f'{color_split["exits"][-1]["uuid"]}:{color_other["uuid"]}': 1,
                f'{color_other["exits"][0]["uuid"]}:{color_split["uuid"]}': 1,
            },
            visited,
        )
        self.assertEqual(
            {"total": 1, "active": 1, "completed": 0, "expired": 0, "interrupted": 0, "failed": 0, "completion": 0},
            flow.get_run_stats(),
        )

        # another unknown color, that'll route us right back again
        # the active stats will look the same, but there should be one more journey on the path
        (
            session1.resume(msg=self.create_incoming_msg(self.contact, "mauve"))
            .visit(color_other)
            .send_msg("I don't know that color. Try again.")
            .visit(color_split)
            .wait()
            .save()
        )

        (active, visited) = flow.get_activity()

        self.assertEqual({color_split["uuid"]: 1}, active)
        self.assertEqual(
            {
                f'{color_prompt["exits"][0]["uuid"]}:{color_split["uuid"]}': 1,
                f'{color_split["exits"][-1]["uuid"]}:{color_other["uuid"]}': 2,
                f'{color_other["exits"][0]["uuid"]}:{color_split["uuid"]}': 2,
            },
            visited,
        )

        # this time a color we know takes us elsewhere, activity will move
        # to another node, but still just one entry
        (
            session1.resume(msg=self.create_incoming_msg(self.contact, "blue"))
            .visit(beer_prompt, exit_index=2)
            .send_msg("Good choice, I like Blue too! What is your favorite beer?")
            .visit(beer_split)
            .wait()
            .save()
        )

        (active, visited) = flow.get_activity()

        self.assertEqual({beer_split["uuid"]: 1}, active)
        self.assertEqual(
            {
                f'{color_prompt["exits"][0]["uuid"]}:{color_split["uuid"]}': 1,
                f'{color_split["exits"][-1]["uuid"]}:{color_other["uuid"]}': 2,
                f'{color_other["exits"][0]["uuid"]}:{color_split["uuid"]}': 2,
                f'{color_split["exits"][2]["uuid"]}:{beer_prompt["uuid"]}': 1,
                f'{beer_prompt["exits"][0]["uuid"]}:{beer_split["uuid"]}': 1,
            },
            visited,
        )

        # check recent runs
        recent = FlowPathRecentRun.get_recent([color_prompt["exits"][0]["uuid"]], color_split["uuid"])
        self.assertEqual(["What is your favorite color?"], [r["text"] for r in recent])

        recent = FlowPathRecentRun.get_recent([color_split["exits"][-1]["uuid"]], color_other["uuid"])
        self.assertEqual(["mauve", "chartreuse"], [r["text"] for r in recent])

        recent = FlowPathRecentRun.get_recent([color_other["exits"][0]["uuid"]], color_split["uuid"])
        self.assertEqual(
            ["I don't know that color. Try again.", "I don't know that color. Try again."], [r["text"] for r in recent]
        )

        recent = FlowPathRecentRun.get_recent([color_split["exits"][2]["uuid"]], beer_prompt["uuid"])
        self.assertEqual(["blue"], [r["text"] for r in recent])

        # check the details of the first recent run
        recent = FlowPathRecentRun.objects.order_by("id").first()
        run1 = session1.session.runs.get()

        self.assertEqual(recent.run, run1)
        self.assertEqual(str(recent.from_uuid), run1.path[0]["exit_uuid"])
        self.assertEqual(str(recent.from_step_uuid), run1.path[0]["uuid"])
        self.assertEqual(str(recent.to_uuid), run1.path[1]["node_uuid"])
        self.assertEqual(str(recent.to_step_uuid), run1.path[1]["uuid"])
        self.assertEqual(recent.visited_on, iso8601.parse_date(run1.path[1]["arrived_on"]))

        # a new participant, showing distinct active counts and incremented path
        ryan = self.create_contact("Ryan Lewis", phone="+12065550725")
        session2 = (
            MockSessionWriter(ryan, flow)
            .visit(color_prompt)
            .send_msg("What is your favorite color?", self.channel)
            .visit(color_split)
            .wait()
            .resume(msg=self.create_incoming_msg(ryan, "burnt sienna"))
            .visit(color_other)
            .send_msg("I don't know that color. Try again.")
            .visit(color_split)
            .wait()
            .save()
        )

        (active, visited) = flow.get_activity()

        self.assertEqual({color_split["uuid"]: 1, beer_split["uuid"]: 1}, active)
        self.assertEqual(
            {
                f'{color_prompt["exits"][0]["uuid"]}:{color_split["uuid"]}': 2,
                f'{color_split["exits"][-1]["uuid"]}:{color_other["uuid"]}': 3,
                f'{color_other["exits"][0]["uuid"]}:{color_split["uuid"]}': 3,
                f'{color_split["exits"][2]["uuid"]}:{beer_prompt["uuid"]}': 1,
                f'{beer_prompt["exits"][0]["uuid"]}:{beer_split["uuid"]}': 1,
            },
            visited,
        )
        self.assertEqual(
            {"total": 2, "active": 2, "completed": 0, "expired": 0, "interrupted": 0, "failed": 0, "completion": 0},
            flow.get_run_stats(),
        )

        # now let's have them land in the same place
        (
            session2.resume(msg=self.create_incoming_msg(ryan, "blue"))
            .visit(beer_prompt, exit_index=2)
            .send_msg("Good choice, I like Blue too! What is your favorite beer?")
            .visit(beer_split)
            .wait()
            .save()
        )

        (active, visited) = flow.get_activity()

        self.assertEqual({beer_split["uuid"]: 2}, active)

        # now move our first contact forward to the end
        (
            session1.resume(msg=self.create_incoming_msg(self.contact, "Turbo King"))
            .visit(name_prompt, exit_index=2)
            .send_msg("Mmmmm... delicious Turbo King. Lastly, what is your name?")
            .visit(name_split)
            .wait()
            .resume(msg=self.create_incoming_msg(self.contact, "Ben Haggerty"))
            .visit(end_prompt)
            .complete()
            .save()
        )

        (active, visited) = flow.get_activity()

        self.assertEqual({beer_split["uuid"]: 1}, active)
        self.assertEqual(
            {
                f'{color_prompt["exits"][0]["uuid"]}:{color_split["uuid"]}': 2,
                f'{color_split["exits"][-1]["uuid"]}:{color_other["uuid"]}': 3,
                f'{color_other["exits"][0]["uuid"]}:{color_split["uuid"]}': 3,
                f'{color_split["exits"][2]["uuid"]}:{beer_prompt["uuid"]}': 2,
                f'{beer_prompt["exits"][0]["uuid"]}:{beer_split["uuid"]}': 2,
                f'{beer_split["exits"][2]["uuid"]}:{name_prompt["uuid"]}': 1,
                f'{name_prompt["exits"][0]["uuid"]}:{name_split["uuid"]}': 1,
                f'{name_split["exits"][0]["uuid"]}:{end_prompt["uuid"]}': 1,
            },
            visited,
        )

        # half of our flows are now complete
        self.assertEqual(
            {"total": 2, "active": 1, "completed": 1, "expired": 0, "interrupted": 0, "failed": 0, "completion": 50},
            flow.get_run_stats(),
        )

        # check squashing doesn't change anything
        squash_flowcounts()

        (active, visited) = flow.get_activity()

        self.assertEqual({beer_split["uuid"]: 1}, active)
        self.assertEqual(
            {
                f'{color_prompt["exits"][0]["uuid"]}:{color_split["uuid"]}': 2,
                f'{color_split["exits"][-1]["uuid"]}:{color_other["uuid"]}': 3,
                f'{color_other["exits"][0]["uuid"]}:{color_split["uuid"]}': 3,
                f'{color_split["exits"][2]["uuid"]}:{beer_prompt["uuid"]}': 2,
                f'{beer_prompt["exits"][0]["uuid"]}:{beer_split["uuid"]}': 2,
                f'{beer_split["exits"][2]["uuid"]}:{name_prompt["uuid"]}': 1,
                f'{name_prompt["exits"][0]["uuid"]}:{name_split["uuid"]}': 1,
                f'{name_split["exits"][0]["uuid"]}:{end_prompt["uuid"]}': 1,
            },
            visited,
        )
        self.assertEqual(
            {"total": 2, "active": 1, "completed": 1, "expired": 0, "interrupted": 0, "failed": 0, "completion": 50},
            flow.get_run_stats(),
        )

        # now let's delete our contact, we'll still have one active node, but
        # our visit path counts will go down by two since he went there twice
        self.contact.release(self.user)

        (active, visited) = flow.get_activity()

        self.assertEqual({beer_split["uuid"]: 1}, active)
        self.assertEqual(
            {
                f'{color_prompt["exits"][0]["uuid"]}:{color_split["uuid"]}': 1,
                f'{color_split["exits"][-1]["uuid"]}:{color_other["uuid"]}': 1,
                f'{color_other["exits"][0]["uuid"]}:{color_split["uuid"]}': 1,
                f'{color_split["exits"][2]["uuid"]}:{beer_prompt["uuid"]}': 1,
                f'{beer_prompt["exits"][0]["uuid"]}:{beer_split["uuid"]}': 1,
                f'{beer_split["exits"][2]["uuid"]}:{name_prompt["uuid"]}': 0,
                f'{name_prompt["exits"][0]["uuid"]}:{name_split["uuid"]}': 0,
                f'{name_split["exits"][0]["uuid"]}:{end_prompt["uuid"]}': 0,
            },
            visited,
        )

        # he was also accounting for our completion rate, back to nothing
        self.assertEqual(
            {"total": 1, "active": 1, "completed": 0, "expired": 0, "interrupted": 0, "failed": 0, "completion": 0},
            flow.get_run_stats(),
        )

        # advance ryan to the end to make sure our percentage accounts for one less contact
        (
            session2.resume(msg=self.create_incoming_msg(ryan, "Turbo King"))
            .visit(name_prompt, exit_index=2)
            .send_msg("Mmmmm... delicious Turbo King. Lastly, what is your name?")
            .visit(name_split)
            .wait()
            .resume(msg=self.create_incoming_msg(ryan, "Ryan Lewis"))
            .visit(end_prompt)
            .complete()
            .save()
        )

        (active, visited) = flow.get_activity()

        self.assertEqual({}, active)
        self.assertEqual(
            {
                f'{color_prompt["exits"][0]["uuid"]}:{color_split["uuid"]}': 1,
                f'{color_split["exits"][-1]["uuid"]}:{color_other["uuid"]}': 1,
                f'{color_other["exits"][0]["uuid"]}:{color_split["uuid"]}': 1,
                f'{color_split["exits"][2]["uuid"]}:{beer_prompt["uuid"]}': 1,
                f'{beer_prompt["exits"][0]["uuid"]}:{beer_split["uuid"]}': 1,
                f'{beer_split["exits"][2]["uuid"]}:{name_prompt["uuid"]}': 1,
                f'{name_prompt["exits"][0]["uuid"]}:{name_split["uuid"]}': 1,
                f'{name_split["exits"][0]["uuid"]}:{end_prompt["uuid"]}': 1,
            },
            visited,
        )
        self.assertEqual(
            {"total": 1, "active": 0, "completed": 1, "expired": 0, "interrupted": 0, "failed": 0, "completion": 100},
            flow.get_run_stats(),
        )

        # messages to/from deleted contacts shouldn't appear in the recent runs
        recent = FlowPathRecentRun.get_recent([color_split["exits"][-1]["uuid"]], color_other["uuid"])
        self.assertEqual(["burnt sienna"], [r["text"] for r in recent])

        # delete our last contact to make sure activity is gone without first expiring, zeros abound
        ryan.release(self.admin)

        (active, visited) = flow.get_activity()

        self.assertEqual({}, active)
        self.assertEqual(
            {
                f'{color_prompt["exits"][0]["uuid"]}:{color_split["uuid"]}': 0,
                f'{color_split["exits"][-1]["uuid"]}:{color_other["uuid"]}': 0,
                f'{color_other["exits"][0]["uuid"]}:{color_split["uuid"]}': 0,
                f'{color_split["exits"][2]["uuid"]}:{beer_prompt["uuid"]}': 0,
                f'{beer_prompt["exits"][0]["uuid"]}:{beer_split["uuid"]}': 0,
                f'{beer_split["exits"][2]["uuid"]}:{name_prompt["uuid"]}': 0,
                f'{name_prompt["exits"][0]["uuid"]}:{name_split["uuid"]}': 0,
                f'{name_split["exits"][0]["uuid"]}:{end_prompt["uuid"]}': 0,
            },
            visited,
        )
        self.assertEqual(
            {"total": 0, "active": 0, "completed": 0, "expired": 0, "interrupted": 0, "failed": 0, "completion": 0},
            flow.get_run_stats(),
        )

        # runs all gone too
        self.assertEqual(0, FlowRun.objects.filter(flow=flow).count())

        # test that expirations remove activity when triggered from the cron in the same way
        tupac = self.create_contact("Tupac Shakur", phone="+12065550725")
        (
            MockSessionWriter(tupac, flow)
            .visit(color_prompt)
            .send_msg("What is your favorite color?", self.channel)
            .visit(color_split)
            .wait()
            .resume(msg=self.create_incoming_msg(tupac, "azul"))
            .visit(color_other)
            .send_msg("I don't know that color. Try again.")
            .visit(color_split)
            .wait()
            .save()
        )

        (active, visited) = flow.get_activity()

        self.assertEqual({color_split["uuid"]: 1}, active)
        self.assertEqual(
            {
                f'{color_prompt["exits"][0]["uuid"]}:{color_split["uuid"]}': 1,
                f'{color_split["exits"][-1]["uuid"]}:{color_other["uuid"]}': 1,
                f'{color_other["exits"][0]["uuid"]}:{color_split["uuid"]}': 1,
                f'{color_split["exits"][2]["uuid"]}:{beer_prompt["uuid"]}': 0,
                f'{beer_prompt["exits"][0]["uuid"]}:{beer_split["uuid"]}': 0,
                f'{beer_split["exits"][2]["uuid"]}:{name_prompt["uuid"]}': 0,
                f'{name_prompt["exits"][0]["uuid"]}:{name_split["uuid"]}': 0,
                f'{name_split["exits"][0]["uuid"]}:{end_prompt["uuid"]}': 0,
            },
            visited,
        )
        self.assertEqual(
            {"total": 1, "active": 1, "completed": 0, "expired": 0, "interrupted": 0, "failed": 0, "completion": 0},
            flow.get_run_stats(),
        )

        # now mark run has expired and make sure it is removed from our activity
        run = tupac.runs.get()
        run.exit_type = FlowRun.EXIT_TYPE_EXPIRED
        run.exited_on = timezone.now()
        run.is_active = False
        run.save(update_fields=("exit_type", "exited_on", "is_active"))

        (active, visited) = flow.get_activity()

        self.assertEqual({}, active)
        self.assertEqual(
            {
                f'{color_prompt["exits"][0]["uuid"]}:{color_split["uuid"]}': 1,
                f'{color_split["exits"][-1]["uuid"]}:{color_other["uuid"]}': 1,
                f'{color_other["exits"][0]["uuid"]}:{color_split["uuid"]}': 1,
                f'{color_split["exits"][2]["uuid"]}:{beer_prompt["uuid"]}': 0,
                f'{beer_prompt["exits"][0]["uuid"]}:{beer_split["uuid"]}': 0,
                f'{beer_split["exits"][2]["uuid"]}:{name_prompt["uuid"]}': 0,
                f'{name_prompt["exits"][0]["uuid"]}:{name_split["uuid"]}': 0,
                f'{name_split["exits"][0]["uuid"]}:{end_prompt["uuid"]}': 0,
            },
            visited,
        )
        self.assertEqual(
            {"total": 1, "active": 0, "completed": 0, "expired": 1, "interrupted": 0, "failed": 0, "completion": 0},
            flow.get_run_stats(),
        )

        # check that flow interruption counts properly
        jimmy = self.create_contact("Jimmy Graham", phone="+12065558888")
        (
            MockSessionWriter(jimmy, flow)
            .visit(color_prompt)
            .send_msg("What is your favorite color?", self.channel)
            .visit(color_split)
            .wait()
            .resume(msg=self.create_incoming_msg(jimmy, "cyan"))
            .visit(color_other)
            .send_msg("I don't know that color. Try again.")
            .visit(color_split)
            .wait()
            .save()
        )

        (active, visited) = flow.get_activity()

        self.assertEqual({color_split["uuid"]: 1}, active)
        self.assertEqual(
            {"total": 2, "active": 1, "completed": 0, "expired": 1, "interrupted": 0, "failed": 0, "completion": 0},
            flow.get_run_stats(),
        )

        run = jimmy.runs.get()
        run.exit_type = FlowRun.EXIT_TYPE_INTERRUPTED
        run.exited_on = timezone.now()
        run.is_active = False
        run.save(update_fields=("exit_type", "exited_on", "is_active"))

        (active, visited) = flow.get_activity()

        self.assertEqual({}, active)
        self.assertEqual(
            {"total": 2, "active": 0, "completed": 0, "expired": 1, "interrupted": 1, "failed": 0, "completion": 0},
            flow.get_run_stats(),
        )

    def test_squash_counts(self):
        flow = self.get_flow("favorites")
        flow2 = self.get_flow("pick_a_number")

        FlowRunCount.objects.create(flow=flow, count=2, exit_type=None)
        FlowRunCount.objects.create(flow=flow, count=1, exit_type=None)
        FlowRunCount.objects.create(flow=flow, count=3, exit_type="E")
        FlowRunCount.objects.create(flow=flow2, count=10, exit_type="I")
        FlowRunCount.objects.create(flow=flow2, count=-1, exit_type="I")

        squash_flowcounts()
        self.assertEqual(FlowRunCount.objects.all().count(), 3)
        self.assertEqual(FlowRunCount.get_totals(flow2), {"I": 9})
        self.assertEqual(FlowRunCount.get_totals(flow), {None: 3, "E": 3})

        max_id = FlowRunCount.objects.all().order_by("-id").first().id

        # no-op this time
        squash_flowcounts()
        self.assertEqual(max_id, FlowRunCount.objects.all().order_by("-id").first().id)

    def test_category_counts(self):
        def assertCount(counts, result_key, category_name, truth):
            found = False
            for count in counts["counts"]:
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
        self.assertEqual(["beer"], [c["key"] for c in counts["counts"]])
        assertCount(counts, "beer", "Turbo King", 3)

        # make sure it still works after ze squashings
        self.assertEqual(76, FlowCategoryCount.objects.all().count())
        FlowCategoryCount.squash()
        self.assertEqual(9, FlowCategoryCount.objects.all().count())
        counts = favorites.get_category_counts()
        assertCount(counts, "beer", "Turbo King", 3)

        # test tostring
        str(FlowCategoryCount.objects.all().first())

        # and if we delete our runs, things zero out
        for run in FlowRun.objects.all():
            run.release()

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

    def test_flow_start_counts(self):
        flow = self.get_flow("color")

        # create start for 10 contacts
        start = FlowStart.objects.create(org=self.org, flow=flow, created_by=self.admin)
        for i in range(10):
            contact = self.create_contact("Bob", urns=[f"twitter:bobby{i}"])
            start.contacts.add(contact)

        # create runs for first 5
        for contact in start.contacts.order_by("id")[:5]:
            FlowRun.objects.create(org=self.org, flow=flow, contact=contact, start=start)

        # check our count
        self.assertEqual(FlowStartCount.get_count(start), 5)

        # create runs for last 5
        for contact in start.contacts.order_by("id")[5:]:
            FlowRun.objects.create(org=self.org, flow=flow, contact=contact, start=start)

        # check our count
        self.assertEqual(FlowStartCount.get_count(start), 10)

        # squash them
        FlowStartCount.squash()
        self.assertEqual(FlowStartCount.get_count(start), 10)

    def test_prune_recentruns(self):
        flow = self.get_flow("color_v13")
        flow_nodes = flow.get_definition()["nodes"]
        color_prompt = flow_nodes[0]
        color_other = flow_nodes[3]
        color_split = flow_nodes[4]
        other_exit = color_split["exits"][2]

        # send 12 invalid color responses from two contacts
        session = None
        bob = self.create_contact("Bob", phone="+260964151234")
        for m in range(12):
            contact = self.contact if m % 2 == 0 else bob
            session = (
                MockSessionWriter(contact, flow)
                .visit(color_prompt)
                .send_msg("What is your favorite color?", self.channel)
                .visit(color_split)
                .wait()
                .resume(msg=self.create_incoming_msg(contact, text=str(m + 1)))
                .visit(color_other)
                .visit(color_split)
                .wait()
                .save()
            )

        # all 12 messages are stored for the other segment
        other_recent = FlowPathRecentRun.objects.filter(from_uuid=other_exit["uuid"], to_uuid=color_other["uuid"])
        self.assertEqual(12, len(other_recent))

        # and these are returned with most-recent first
        other_recent = FlowPathRecentRun.get_recent([other_exit["uuid"]], color_other["uuid"], limit=None)
        self.assertEqual(
            ["12", "11", "10", "9", "8", "7", "6", "5", "4", "3", "2", "1"], [r["text"] for r in other_recent]
        )

        # even when limit is applied
        other_recent = FlowPathRecentRun.get_recent([other_exit["uuid"]], color_other["uuid"], limit=5)
        self.assertEqual(["12", "11", "10", "9", "8"], [r["text"] for r in other_recent])

        squash_flowcounts()

        # now only 5 newest are stored
        other_recent = FlowPathRecentRun.objects.filter(from_uuid=other_exit["uuid"], to_uuid=color_other["uuid"])
        self.assertEqual(5, len(other_recent))

        other_recent = FlowPathRecentRun.get_recent([other_exit["uuid"]], color_other["uuid"])
        self.assertEqual(["12", "11", "10", "9", "8"], [r["text"] for r in other_recent])

        # send another message and prune again
        (session.resume(msg=self.create_incoming_msg(bob, "13")).visit(color_other).visit(color_split).wait().save())
        squash_flowcounts()

        other_recent = FlowPathRecentRun.get_recent([other_exit["uuid"]], color_other["uuid"])
        self.assertEqual(["13", "12", "11", "10", "9"], [r["text"] for r in other_recent])

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

    def test_global_keywords_trigger_update(self):
        self.login(self.admin)
        flow = Flow.create(self.org, self.admin, "Flow")

        # update flow triggers
        response = self.client.post(
            reverse("flows.flow_update", args=[flow.id]),
            {
                "name": "Flow With Keyword Triggers",
                "keyword_triggers": ["it", "changes", "everything"],
                "expires_after_minutes": 60 * 12,
            },
        )
        self.assertEqual(response.status_code, 302)

        flow_with_keywords = Flow.objects.get(name="Flow With Keyword Triggers")
        self.assertEqual(flow_with_keywords.triggers.count(), 3)
        self.assertEqual(flow_with_keywords.triggers.filter(is_archived=False).count(), 3)
        self.assertEqual(flow_with_keywords.triggers.filter(is_archived=False).exclude(groups=None).count(), 0)

        Trigger.objects.create(
            created_by=self.admin,
            modified_by=self.admin,
            org=self.org,
            trigger_type=Trigger.TYPE_CATCH_ALL,
            flow=flow_with_keywords,
        )

        Trigger.objects.create(
            created_by=self.admin,
            modified_by=self.admin,
            org=self.org,
            trigger_type=Trigger.TYPE_MISSED_CALL,
            flow=flow_with_keywords,
        )

        Trigger.objects.create(
            created_by=self.admin,
            modified_by=self.admin,
            org=self.org,
            trigger_type=Trigger.TYPE_INBOUND_CALL,
            flow=flow_with_keywords,
        )

        Trigger.objects.create(
            created_by=self.admin,
            modified_by=self.admin,
            org=self.org,
            trigger_type=Trigger.TYPE_SCHEDULE,
            flow=flow_with_keywords,
        )

        self.assertEqual(flow_with_keywords.triggers.filter(is_archived=False).count(), 7)

        # test if form has expected fields
        post_data = dict()
        response = self.client.post(reverse("flows.flow_update", args=[flow.pk]), post_data, follow=True)

        field_names = [field for field in response.context_data["form"].fields]
        self.assertEqual(
            field_names,
            ["name", "keyword_triggers", "expires_after_minutes", "ignore_triggers", "loc"],
        )

        # update flow triggers
        post_data = dict()
        post_data["name"] = "Flow With Keyword Triggers"
        post_data["keyword_triggers"] = ["it", "join"]
        post_data["expires_after_minutes"] = 60 * 12
        response = self.client.post(reverse("flows.flow_update", args=[flow.pk]), post_data, follow=True)

        flow_with_keywords = Flow.objects.get(name=post_data["name"])
        self.assertEqual(200, response.status_code)
        self.assertEqual(response.request["PATH_INFO"], reverse("flows.flow_editor", args=[flow.uuid]))
        self.assertEqual(flow_with_keywords.triggers.count(), 8)
        self.assertEqual(flow_with_keywords.triggers.filter(is_archived=True).count(), 2)
        self.assertEqual(
            flow_with_keywords.triggers.filter(is_archived=True, trigger_type=Trigger.TYPE_KEYWORD).count(), 2
        )
        self.assertEqual(flow_with_keywords.triggers.filter(is_archived=False).count(), 6)
        self.assertEqual(
            flow_with_keywords.triggers.filter(is_archived=True, trigger_type=Trigger.TYPE_KEYWORD).count(), 2
        )

        # only keyword triggers got archived, other are stil active
        self.assertTrue(flow_with_keywords.triggers.filter(is_archived=False, trigger_type=Trigger.TYPE_CATCH_ALL))
        self.assertTrue(flow_with_keywords.triggers.filter(is_archived=False, trigger_type=Trigger.TYPE_SCHEDULE))
        self.assertTrue(flow_with_keywords.triggers.filter(is_archived=False, trigger_type=Trigger.TYPE_MISSED_CALL))
        self.assertTrue(flow_with_keywords.triggers.filter(is_archived=False, trigger_type=Trigger.TYPE_INBOUND_CALL))

    def test_flow_update_of_inactive_flow(self):
        flow = self.get_flow("favorites")
        flow.release(self.admin)

        post_data = {"name": "Flow that does not exist"}

        self.login(self.admin)
        response = self.client.post(reverse("flows.flow_update", args=[flow.pk]), post_data)

        # can't delete already released flow
        self.assertEqual(response.status_code, 404)

    def test_flow_results_of_inactive_flow(self):
        flow = self.get_flow("favorites")
        flow.release(self.admin)

        self.login(self.admin)
        response = self.client.get(reverse("flows.flow_results", args=[flow.uuid]))

        self.assertEqual(response.status_code, 404)

    def test_flow_results_with_hidden_results(self):
        flow = self.get_flow("color_v13")
        flow_nodes = flow.get_definition()["nodes"]
        color_split = flow_nodes[4]

        # add a spec for a hidden result to this flow.. which should not be included below
        flow.metadata[Flow.METADATA_RESULTS].append(
            {
                "key": "_color_classification",
                "name": "_Color Classification",
                "categories": ["Success", "Skipped", "Failure"],
                "node_uuids": [color_split["uuid"]],
            }
        )

        self.login(self.admin)
        response = self.client.get(reverse("flows.flow_results", args=[flow.uuid]))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.context["result_fields"],
            [
                {
                    "key": "color",
                    "name": "Color",
                    "categories": ["Orange", "Blue", "Other", "Nothing"],
                    "node_uuids": [color_split["uuid"]],
                    "has_categories": "true",
                }
            ],
        )

    def test_views_viewers(self):
        flow = self.get_flow("color")

        # create a viewer
        self.viewer = self.create_user("Viewer")
        self.org.viewers.add(self.viewer)
        self.viewer.set_org(self.org)

        # create a flow for another org and a flow label
        flow2 = Flow.create(self.org2, self.admin2, "Flow2")
        flow_label = FlowLabel.objects.create(name="one", org=self.org, parent=None)

        flow_list_url = reverse("flows.flow_list")
        flow_archived_url = reverse("flows.flow_archived")
        flow_create_url = reverse("flows.flow_create")
        flowlabel_create_url = reverse("flows.flowlabel_create")

        # no login, no list
        response = self.client.get(flow_list_url)
        self.assertRedirect(response, reverse("users.user_login"))

        user = self.viewer
        user.first_name = "Test"
        user.last_name = "Contact"
        user.save()
        self.login(user)

        # list, should have only one flow (the one created in setUp)

        response = self.client.get(flow_list_url)
        self.assertEqual(1, len(response.context["object_list"]))
        # no create links
        self.assertNotContains(response, flow_create_url)
        self.assertNotContains(response, flowlabel_create_url)
        # verify the action buttons we have
        self.assertNotContains(response, "object-btn-unlabel")
        self.assertNotContains(response, "object-btn-restore")
        self.assertNotContains(response, "object-btn-archive")
        self.assertNotContains(response, "object-btn-label")
        self.assertContains(response, "object-btn-export")

        # can not label
        post_data = dict()
        post_data["action"] = "label"
        post_data["objects"] = flow.id
        post_data["label"] = flow_label.id
        post_data["add"] = True

        response = self.client.post(flow_list_url, post_data, follow=True)
        self.assertEqual(403, response.status_code)

        flow.refresh_from_db()
        self.assertEqual(0, flow.labels.count())

        # can not archive
        post_data = dict()
        post_data["action"] = "archive"
        post_data["objects"] = flow.pk
        response = self.client.post(flow_list_url, post_data, follow=True)
        self.assertEqual(403, response.status_code)

        flow.refresh_from_db()
        self.assertFalse(flow.is_archived)

        # inactive list shouldn't have any flows
        response = self.client.get(flow_archived_url)
        self.assertEqual(0, len(response.context["object_list"]))

        response = self.client.get(reverse("flows.flow_editor", args=[flow.uuid]))
        self.assertEqual(200, response.status_code)
        self.assertFalse(response.context["mutable"])

        flow.is_archived = True
        flow.save()

        response = self.client.get(flow_list_url)
        self.assertEqual(0, len(response.context["object_list"]))

        # cannot restore
        post_data = dict()
        post_data["action"] = "restore"
        post_data["objects"] = flow.id
        response = self.client.post(flow_archived_url, post_data, follow=True)
        self.assertEqual(403, response.status_code)

        flow.refresh_from_db()
        self.assertTrue(flow.is_archived)

        response = self.client.get(flow_archived_url)
        self.assertEqual(1, len(response.context["object_list"]))

        # cannot create a flow
        response = self.client.get(flow_create_url)
        self.assertEqual(302, response.status_code)

        # cannot create a flowlabel
        response = self.client.get(flowlabel_create_url)
        self.assertEqual(302, response.status_code)

        # also shouldn't be able to view other flow
        response = self.client.get(reverse("flows.flow_editor", args=[flow2.uuid]))
        self.assertEqual(302, response.status_code)

    def test_legacy_validate_definition(self):
        with self.assertRaises(ValueError):
            FlowRevision.validate_legacy_definition({"flow_type": "U", "nodes": []})

        with self.assertRaises(ValueError):
            FlowRevision.validate_legacy_definition(self.get_flow_json("not_fully_localized"))

        # base_language of null, but spec version 8
        with self.assertRaises(ValueError):
            FlowRevision.validate_legacy_definition(self.get_flow_json("no_base_language_v8"))

        # base_language of 'eng' but non localized actions
        with self.assertRaises(ValueError):
            FlowRevision.validate_legacy_definition(self.get_flow_json("non_localized_with_language"))

        with self.assertRaises(ValueError):
            FlowRevision.validate_legacy_definition(self.get_flow_json("non_localized_ruleset"))

    def test_importing_dependencies(self):
        # create channel to be matched by name
        channel = self.create_channel("TG", "RapidPro Test", "12345324635")

        # create ticketer to be matched by UUID
        ticketer = Ticketer.create(self.org, self.admin, "zendesk", "Zendesk Tickets", {})
        ticketer.uuid = "6ceb51cd-1d19-4f28-a9c3-2e244a9e2959"
        ticketer.save(update_fields=("uuid",))

        flow = self.get_flow("dependencies_v13")
        flow_def = flow.get_definition()

        # global should have been created with blank value
        self.assertTrue(self.org.globals.filter(name="Org Name", key="org_name", value="").exists())

        # topic should have been created too
        self.assertTrue(self.org.topics.filter(name="Support").exists())

        # fields created with type if exists in export
        self.assertTrue(self.org.contactfields.filter(key="cat_breed", label="Cat Breed", value_type="T").exists())
        self.assertTrue(self.org.contactfields.filter(key="french_age", value_type="N").exists())

        # reference to channel changed to match existing channel by name
        self.assertEqual(
            {"uuid": str(channel.uuid), "name": "RapidPro Test"}, flow_def["nodes"][0]["actions"][4]["channel"]
        )

        # reference to ticketer unchanged because it matched existing ticketer by UUID
        self.assertEqual(
            {"uuid": "6ceb51cd-1d19-4f28-a9c3-2e244a9e2959", "name": "Zendesk"},
            flow_def["nodes"][8]["actions"][0]["ticketer"],
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
            self.assertEqual(len(flow.metadata["waiting_exit_uuids"]), 11)
            self.assertEqual(len(flow.metadata["parent_refs"]), 0)

    def test_group_send(self):
        # create an inactive group with the same name, to test that this doesn't blow up our import
        group = ContactGroup.get_or_create(self.org, self.admin, "Survey Audience")
        group.is_active = False
        group.save()

        # and create another as well
        ContactGroup.get_or_create(self.org, self.admin, "Survey Audience")

        # fetching a flow with a group send shouldn't throw
        self.get_flow("group_send_flow")

    def test_flow_delete_of_inactive_flow(self):
        flow = self.get_flow("favorites")
        flow.release(self.admin)

        self.login(self.admin)
        response = self.client.post(reverse("flows.flow_delete", args=[flow.pk]))

        # can't delete already released flow
        self.assertEqual(response.status_code, 404)

    def test_flow_delete(self):
        flow = self.get_flow("favorites_v13")
        flow_nodes = flow.get_definition()["nodes"]
        color_prompt = flow_nodes[0]
        color_split = flow_nodes[2]
        beer_prompt = flow_nodes[3]
        beer_split = flow_nodes[5]

        # create a campaign that contains this flow
        friends = self.create_group("Friends", [])
        poll_date = ContactField.get_or_create(
            self.org, self.admin, "poll_date", "Poll Date", value_type=ContactField.TYPE_DATETIME
        )

        campaign = Campaign.create(self.org, self.admin, Campaign.get_unique_name(self.org, "Favorite Poll"), friends)
        event1 = CampaignEvent.create_flow_event(
            self.org, self.admin, campaign, poll_date, offset=0, unit="D", flow=flow, delivery_hour="13"
        )

        # create a trigger that contains this flow
        trigger = Trigger.objects.create(
            org=self.org,
            keyword="poll",
            flow=flow,
            trigger_type=Trigger.TYPE_KEYWORD,
            created_by=self.admin,
            modified_by=self.admin,
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
        response = self.client.post(reverse("flows.flow_delete", args=[flow.pk]))
        self.assertLoginRedirect(response)

        # login as admin
        self.login(self.admin)
        response = self.client.post(reverse("flows.flow_delete", args=[flow.pk]))
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

    def test_flow_delete_with_dependencies(self):
        self.login(self.admin)

        self.get_flow("dependencies")
        self.get_flow("dependencies_voice")
        parent = Flow.objects.filter(name="Dependencies").first()
        child = Flow.objects.filter(name="Child Flow").first()
        voice = Flow.objects.filter(name="Voice Dependencies").first()

        contact_fields = (
            {"key": "contact_age", "label": "Contact Age"},
            # fields based on parent and child references
            {"key": "top"},
            {"key": "bottom"},
            # replies
            {"key": "chw"},
            # url attachemnts
            {"key": "attachment"},
            # dynamic groups
            {"key": "cat_breed", "label": "Cat Breed"},
            {"key": "organization"},
            # sending messages
            {"key": "recipient"},
            {"key": "message"},
            # sending emails
            {"key": "email_message", "label": "Email Message"},
            {"key": "subject"},
            # trigger someone else
            {"key": "other_phone", "label": "Other Phone"},
            # rules and localizations
            {"key": "rule"},
            {"key": "french_rule", "label": "French Rule"},
            {"key": "french_age", "label": "French Age"},
            {"key": "french_fries", "label": "French Fries"},
            # updating contacts
            {"key": "favorite_cat", "label": "Favorite Cat"},
            {"key": "next_cat_fact", "label": "Next Cat Fact"},
            {"key": "last_cat_fact", "label": "Last Cat Fact"},
            # webhook urls
            {"key": "webhook"},
            # expression splits
            {"key": "expression_split", "label": "Expression Split"},
            # voice says
            {"key": "play_message", "label": "Play Message", "flow": voice},
            {"key": "voice_rule", "label": "Voice Rule", "flow": voice},
            # voice plays (recordings)
            {"key": "voice_recording", "label": "Voice Recording", "flow": voice},
        )

        for field_spec in contact_fields:
            key = field_spec.get("key")
            label = field_spec.get("label", key.capitalize())
            flow = field_spec.get("flow", parent)

            # make sure our field exists after import
            field = ContactField.user_fields.filter(key=key, label=label).first()
            self.assertIsNotNone(field, "Couldn't find field %s (%s)" % (key, label))

            # and our flow is dependent on us
            self.assertIsNotNone(
                flow.field_dependencies.filter(key__in=[key]).first(),
                "Flow is missing dependency on %s (%s)" % (key, label),
            )

        # deleting should fail since the 'Dependencies' flow depends on us
        self.client.post(reverse("flows.flow_delete", args=[child.id]))
        self.assertIsNotNone(Flow.objects.filter(id=child.id, is_active=True).first())

        # remove our child dependency
        parent = Flow.objects.filter(name="Dependencies").first()
        parent.flow_dependencies.remove(child)

        # now the child can be deleted
        self.client.post(reverse("flows.flow_delete", args=[child.id]))
        self.assertIsNotNone(Flow.objects.filter(id=child.id, is_active=False).first())

        # deleting our parent flow should work
        self.client.post(reverse("flows.flow_delete", args=[parent.id]))
        self.assertIsNotNone(Flow.objects.filter(id=parent.id, is_active=False).first())

        # our parent should no longer have any dependencies
        parent.refresh_from_db()
        self.assertEqual(0, parent.field_dependencies.all().count())
        self.assertEqual(0, parent.flow_dependencies.all().count())
        self.assertEqual(0, parent.group_dependencies.all().count())

    def test_update_expiration(self):
        flow1 = self.get_flow("favorites")
        flow2 = Flow.copy(flow1, self.admin)

        parent = FlowRun.objects.create(
            org=self.org,
            flow=flow1,
            contact=self.contact,
            path=[
                {
                    FlowRun.PATH_STEP_UUID: "1b9c7862-55fb-4ad8-9c81-203a12a63a63",
                    FlowRun.PATH_NODE_UUID: "93a9f3b9-3471-4849-b6af-daec7c431e2a",
                    FlowRun.PATH_ARRIVED_ON: datetime.datetime(2019, 1, 1, 0, 0, 0, 0, pytz.UTC),
                }
            ],
        )
        child = FlowRun.objects.create(
            org=self.org,
            flow=flow2,
            contact=self.contact,
            path=[
                {
                    FlowRun.PATH_STEP_UUID: "263a6e6c-c1d9-4af3-b8cf-b52a3085a625",
                    FlowRun.PATH_NODE_UUID: "474fd1be-eaec-4ae7-96cd-c771410fac18",
                    FlowRun.PATH_ARRIVED_ON: datetime.datetime(2019, 1, 1, 0, 0, 0, 0, pytz.UTC),
                }
            ],
            parent=parent,
        )

        update_run_expirations_task(flow2.id)

        parent.refresh_from_db()
        child.refresh_from_db()

        # child expiration should be last arrived_on + 12 hours
        self.assertEqual(datetime.datetime(2019, 1, 1, 12, 0, 0, 0, pytz.UTC), child.expires_on)

        # parent expiration should be that + 12 hours
        self.assertEqual(datetime.datetime(2019, 1, 2, 0, 0, 0, 0, pytz.UTC), parent.expires_on)


class FlowCRUDLTest(TembaTest, CRUDLTestMixin):
    def test_create(self):
        create_url = reverse("flows.flow_create")

        # don't show language if workspace doesn't have languages configured
        self.assertCreateFetch(
            create_url, allow_viewers=False, allow_editors=True, form_fields=["name", "keyword_triggers", "flow_type"]
        )

        self.org.set_flow_languages(self.admin, ["eng", "spa"])
        self.org2.set_flow_languages(self.admin, ["eng"])

        response = self.assertCreateFetch(
            create_url,
            allow_viewers=False,
            allow_editors=True,
            form_fields=["name", "keyword_triggers", "flow_type", "base_language"],
        )

        # check flow type options
        self.assertEqual(
            [
                (Flow.TYPE_MESSAGE, "Messaging"),
                (Flow.TYPE_VOICE, "Phone Call"),
                (Flow.TYPE_BACKGROUND, "Background"),
                (Flow.TYPE_SURVEY, "Surveyor"),
            ],
            response.context["form"].fields["flow_type"].choices,
        )

        # try to submit without name or language
        self.assertCreateSubmit(
            create_url,
            {"flow_type": "M"},
            form_errors={"name": "This field is required.", "base_language": "This field is required."},
        )

        response = self.assertCreateSubmit(
            create_url,
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
            {
                "name": "Flow #1",
                "keyword_triggers": ["toooooooooooooolong", "test"],
                "flow_type": Flow.TYPE_MESSAGE,
            },
            form_errors={
                "keyword_triggers": '"toooooooooooooolong" must be a single word, less than 16 characters, containing only letter and numbers'
            },
        )

        # submit with valid keywords
        self.assertCreateSubmit(
            create_url,
            {
                "name": "Flow 1",
                "keyword_triggers": ["testing", "test"],
                "flow_type": Flow.TYPE_MESSAGE,
            },
            new_obj_query=Flow.objects.filter(org=self.org, name="Flow 1", flow_type="M"),
        )

        # check the created keyword triggers
        flow1 = Flow.objects.get(name="Flow 1")
        self.assertEqual({"testing", "test"}, set(flow1.triggers.values_list("keyword", flat=True)))

        # try to create another flow with one of the same keywords
        self.assertCreateSubmit(
            create_url,
            {
                "name": "Flow 2",
                "keyword_triggers": ["test"],
                "flow_type": Flow.TYPE_MESSAGE,
            },
            form_errors={"keyword_triggers": 'The keyword "test" is already used for another flow'},
        )

        # add a group to the existing trigger with that keyword
        group = self.create_group("Testers", contacts=[])
        flow1.triggers.get(keyword="test").groups.add(group)

        # and now it's no longer a conflict
        self.assertCreateSubmit(
            create_url,
            {
                "name": "Flow 2",
                "keyword_triggers": ["test"],
                "flow_type": Flow.TYPE_MESSAGE,
            },
            new_obj_query=Flow.objects.filter(org=self.org, name="Flow 2", flow_type="M"),
        )

        # check the created keyword triggers
        flow2 = Flow.objects.get(name="Flow 2")
        self.assertEqual({"test"}, set(flow2.triggers.values_list("keyword", flat=True)))

    def test_views(self):
        contact = self.create_contact("Eric", phone="+250788382382")
        flow = self.get_flow("color")

        # create a flow for another org
        other_flow = Flow.create(self.org2, self.admin2, "Flow2", base_language="base")

        # no login, no list
        response = self.client.get(reverse("flows.flow_list"))
        self.assertRedirect(response, reverse("users.user_login"))

        user = self.admin
        user.first_name = "Test"
        user.last_name = "Contact"
        user.save()
        self.login(user)

        # list, should have only one flow (the one created in setUp)
        response = self.client.get(reverse("flows.flow_list"))
        self.assertEqual(1, len(response.context["object_list"]))

        # inactive list shouldn't have any flows
        response = self.client.get(reverse("flows.flow_archived"))
        self.assertEqual(0, len(response.context["object_list"]))

        # also shouldn't be able to view other flow
        response = self.client.get(reverse("flows.flow_editor", args=[other_flow.uuid]))
        self.assertEqual(302, response.status_code)

        # get our create page
        response = self.client.get(reverse("flows.flow_create"))
        self.assertTrue(response.context["has_flows"])

        # create a new regular flow
        response = self.client.post(
            reverse("flows.flow_create"), dict(name="Flow", flow_type=Flow.TYPE_MESSAGE), follow=True
        )
        flow1 = Flow.objects.get(org=self.org, name="Flow")
        self.assertEqual(1, flow1.revisions.all().count())
        # add a trigger on this flow
        Trigger.objects.create(
            org=self.org, keyword="unique", flow=flow1, created_by=self.admin, modified_by=self.admin
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(flow1.flow_type, Flow.TYPE_MESSAGE)
        self.assertEqual(flow1.expires_after_minutes, 10080)

        # create a new surveyor flow
        self.client.post(
            reverse("flows.flow_create"), dict(name="Surveyor Flow", flow_type=Flow.TYPE_SURVEY), follow=True
        )
        flow2 = Flow.objects.get(org=self.org, name="Surveyor Flow")
        self.assertEqual(flow2.flow_type, "S")
        self.assertEqual(flow2.expires_after_minutes, 10080)

        # make sure we don't get a start flow button for Android Surveys
        response = self.client.get(reverse("flows.flow_editor", args=[flow2.uuid]))
        self.assertNotContains(response, "broadcast-rulesflow btn-primary")

        # create a new voice flow
        response = self.client.post(
            reverse("flows.flow_create"), dict(name="Voice Flow", flow_type=Flow.TYPE_VOICE), follow=True
        )
        voice_flow = Flow.objects.get(org=self.org, name="Voice Flow")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(voice_flow.flow_type, "V")

        # default expiration for voice is shorter
        self.assertEqual(voice_flow.expires_after_minutes, 5)

        # test flows with triggers
        # create a new flow with one unformatted keyword
        post_data = {"name": "Flow With Unformated Keyword Triggers", "keyword_triggers": ["this is", "it"]}
        response = self.client.post(reverse("flows.flow_create"), post_data)
        self.assertFormError(
            response,
            "form",
            "keyword_triggers",
            '"this is" must be a single word, less than 16 characters, containing only letter and numbers',
        )

        # create a new flow with one existing keyword
        post_data = {"name": "Flow With Existing Keyword Triggers", "keyword_triggers": ["this", "is", "unique"]}
        response = self.client.post(reverse("flows.flow_create"), post_data)
        self.assertFormError(
            response, "form", "keyword_triggers", 'The keyword "unique" is already used for another flow'
        )

        # create another trigger so there are two in the way
        trigger = Trigger.objects.create(
            org=self.org, keyword="this", flow=flow1, created_by=self.admin, modified_by=self.admin
        )

        response = self.client.post(reverse("flows.flow_create"), post_data)
        self.assertFormError(
            response, "form", "keyword_triggers", 'The keywords "this, unique" are already used for another flow'
        )
        trigger.delete()

        # create a new flow with keywords
        post_data = {
            "name": "Flow With Good Keyword Triggers",
            "keyword_triggers": ["this", "is", "it"],
            "flow_type": Flow.TYPE_MESSAGE,
            "expires_after_minutes": 30,
        }
        response = self.client.post(reverse("flows.flow_create"), post_data, follow=True)
        flow3 = Flow.objects.get(name=post_data["name"])

        self.assertEqual(200, response.status_code)
        self.assertEqual(response.request["PATH_INFO"], reverse("flows.flow_editor", args=[flow3.uuid]))
        self.assertEqual(response.context["object"].triggers.count(), 3)

        # update flow triggers, and test if form has expected fields
        post_data = dict()
        response = self.client.post(reverse("flows.flow_update", args=[flow3.pk]), post_data, follow=True)

        field_names = [field for field in response.context_data["form"].fields]
        self.assertEqual(field_names, ["name", "keyword_triggers", "expires_after_minutes", "ignore_triggers", "loc"])

        post_data = dict()
        post_data["name"] = "Flow With Keyword Triggers"
        post_data["keyword_triggers"] = ["it", "changes", "everything"]
        post_data["expires_after_minutes"] = 60 * 12
        response = self.client.post(reverse("flows.flow_update", args=[flow3.pk]), post_data, follow=True)

        flow3 = Flow.objects.get(name=post_data["name"])
        self.assertEqual(200, response.status_code)
        self.assertEqual(response.request["PATH_INFO"], reverse("flows.flow_editor", args=[flow3.uuid]))
        self.assertEqual(flow3.triggers.count(), 5)
        self.assertEqual(flow3.triggers.filter(is_archived=True).count(), 2)
        self.assertEqual(flow3.triggers.filter(is_archived=False).count(), 3)
        self.assertEqual(flow3.triggers.filter(is_archived=False).exclude(groups=None).count(), 0)

        # update flow with unformatted keyword
        post_data["keyword_triggers"] = "it,changes,every thing"
        response = self.client.post(reverse("flows.flow_update", args=[flow3.pk]), post_data)
        self.assertTrue(response.context["form"].errors)

        # update flow with unformatted keyword
        post_data["keyword_triggers"] = ["it", "changes", "everything", "unique"]
        response = self.client.post(reverse("flows.flow_update", args=[flow3.pk]), post_data)
        self.assertTrue(response.context["form"].errors)
        response = self.client.get(reverse("flows.flow_update", args=[flow3.pk]))
        self.assertEqual(response.context["form"].fields["keyword_triggers"].initial, ["it", "changes", "everything"])
        self.assertEqual(flow3.triggers.filter(is_archived=False).count(), 3)
        self.assertEqual(flow3.triggers.filter(is_archived=False).exclude(groups=None).count(), 0)
        trigger = Trigger.objects.get(keyword="everything", flow=flow3)
        group = self.create_group("first", [contact])
        trigger.groups.add(group)
        self.assertEqual(flow3.triggers.filter(is_archived=False).count(), 3)
        self.assertEqual(flow3.triggers.filter(is_archived=False).exclude(groups=None).count(), 1)
        self.assertEqual(flow3.triggers.filter(is_archived=False).exclude(groups=None)[0].keyword, "everything")
        response = self.client.get(reverse("flows.flow_update", args=[flow3.pk]))
        self.assertEqual(response.context["form"].fields["keyword_triggers"].initial, ["it", "changes"])
        self.assertNotContains(response, "contact_creation")
        self.assertEqual(flow3.triggers.filter(is_archived=False).count(), 3)
        self.assertEqual(flow3.triggers.filter(is_archived=False).exclude(groups=None).count(), 1)
        self.assertEqual(flow3.triggers.filter(is_archived=False).exclude(groups=None)[0].keyword, "everything")

        # can see results for a flow
        response = self.client.get(reverse("flows.flow_results", args=[flow.uuid]))
        self.assertEqual(200, response.status_code)

        # check flow listing
        response = self.client.get(reverse("flows.flow_list"))
        self.assertEqual(list(response.context["object_list"]), [flow3, voice_flow, flow2, flow1, flow])  # by saved_on

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

        post_data = dict(name="Message flow", expires_after_minutes=5, flow_type=Flow.TYPE_MESSAGE)
        response = self.client.post(reverse("flows.flow_create"), post_data, follow=True)
        msg_flow = Flow.objects.get(name=post_data["name"])

        self.assertEqual(200, response.status_code)
        self.assertEqual(response.request["PATH_INFO"], reverse("flows.flow_editor", args=[msg_flow.uuid]))
        self.assertEqual(msg_flow.flow_type, Flow.TYPE_MESSAGE)

        post_data = dict(name="Call flow", expires_after_minutes=5, flow_type=Flow.TYPE_VOICE)
        response = self.client.post(reverse("flows.flow_create"), post_data, follow=True)
        call_flow = Flow.objects.get(name=post_data["name"])

        self.assertEqual(200, response.status_code)
        self.assertEqual(response.request["PATH_INFO"], reverse("flows.flow_editor", args=[call_flow.uuid]))
        self.assertEqual(call_flow.flow_type, Flow.TYPE_VOICE)

        # test creating a flow with base language
        self.org.set_flow_languages(self.admin, ["eng"])

        response = self.client.post(
            reverse("flows.flow_create"),
            {
                "name": "Language Flow",
                "expires_after_minutes": 5,
                "base_language": "eng",
                "flow_type": Flow.TYPE_MESSAGE,
            },
            follow=True,
        )

        language_flow = Flow.objects.get(name="Language Flow")

        self.assertEqual(200, response.status_code)
        self.assertEqual(response.request["PATH_INFO"], reverse("flows.flow_editor", args=[language_flow.uuid]))
        self.assertEqual(language_flow.base_language, "eng")

    def test_update_messaging_flow(self):
        flow = self.get_flow("color_v13")
        update_url = reverse("flows.flow_update", args=[flow.id])

        # we should only see name and contact creation option on form
        self.assertUpdateFetch(
            update_url,
            allow_viewers=False,
            allow_editors=True,
            form_fields=["name", "keyword_triggers", "expires_after_minutes", "ignore_triggers"],
        )

        # try to update with empty name
        self.assertUpdateSubmit(
            update_url,
            {"name": "", "expires_after_minutes": 10, "ignore_triggers": True},
            form_errors={"name": "This field is required."},
            object_unchanged=flow,
        )

        # update name and contact creation option to be per login
        self.assertUpdateSubmit(
            update_url,
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
        self.assertEqual({"test", "help"}, {t.keyword for t in flow.triggers.filter(is_active=True)})
        self.assertTrue(flow.ignore_triggers)

    def test_update_voice_flow(self):
        flow = self.get_flow("ivr")
        update_url = reverse("flows.flow_update", args=[flow.id])

        # check fields
        self.assertUpdateFetch(
            update_url,
            allow_viewers=False,
            allow_editors=True,
            form_fields=["name", "keyword_triggers", "expires_after_minutes", "ignore_triggers", "ivr_retry"],
        )

        # try to update with an expires value which is only for messaging flows and an invalid retry value
        self.assertUpdateSubmit(
            update_url,
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
        self.assertEqual({"test", "help"}, {t.keyword for t in flow.triggers.filter(is_active=True)})
        self.assertTrue(flow.ignore_triggers)
        self.assertEqual(30, flow.metadata.get("ivr_retry"))

        # check we still have that value after saving a new revision
        flow.save_revision(self.admin, flow.get_definition())
        self.assertEqual(30, flow.metadata["ivr_retry"])

    def test_update_surveyor_flow(self):
        flow = self.get_flow("media_survey")
        update_url = reverse("flows.flow_update", args=[flow.id])

        # we should only see name and contact creation option on form
        self.assertUpdateFetch(
            update_url, allow_viewers=False, allow_editors=True, form_fields=["name", "contact_creation"]
        )

        # update name and contact creation option to be per login
        self.assertUpdateSubmit(update_url, {"name": "New Name", "contact_creation": "login"})

        flow.refresh_from_db()
        self.assertEqual("New Name", flow.name)
        self.assertEqual("login", flow.metadata.get("contact_creation"))

    def test_update_background_flow(self):
        flow = self.get_flow("background")
        update_url = reverse("flows.flow_update", args=[flow.id])

        # we should only see name on form
        self.assertUpdateFetch(update_url, allow_viewers=False, allow_editors=True, form_fields=["name"])

        # update name and contact creation option to be per login
        self.assertUpdateSubmit(update_url, {"name": "New Name"})

        flow.refresh_from_db()
        self.assertEqual("New Name", flow.name)

    def test_list_views(self):
        flow1 = self.get_flow("color_v13")
        flow2 = self.get_flow("no_ruleset_flow")

        # archive second flow
        flow2.is_archived = True
        flow2.save(update_fields=("is_archived",))

        flow3 = Flow.create(self.org, self.admin, "Flow 3", base_language="base")

        self.login(self.admin)

        # see our trigger on the list page
        response = self.client.get(reverse("flows.flow_list"))
        self.assertContains(response, flow1.name)
        self.assertContains(response, flow3.name)
        self.assertEqual(2, response.context["folders"][0]["count"])
        self.assertEqual(1, response.context["folders"][1]["count"])

        # archive it
        response = self.client.post(reverse("flows.flow_list"), {"action": "archive", "objects": flow1.id})
        self.assertEqual(200, response.status_code)

        # flow should no longer appear in list
        response = self.client.get(reverse("flows.flow_list"))
        self.assertNotContains(response, flow1.name)
        self.assertContains(response, flow3.name)
        self.assertEqual(1, response.context["folders"][0]["count"])
        self.assertEqual(2, response.context["folders"][1]["count"])

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

        # but does appear in normal list
        response = self.client.get(reverse("flows.flow_list"))
        self.assertContains(response, flow1.name)
        self.assertContains(response, flow3.name)
        self.assertEqual(2, response.context["folders"][0]["count"])
        self.assertEqual(1, response.context["folders"][1]["count"])

        # can label flows
        label1 = FlowLabel.create(self.org, "Important")
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
        self.assertEqual(2, response.context["folders"][0]["count"])
        self.assertEqual(1, response.context["folders"][1]["count"])

        # single message flow (flom campaign) should not be included in counts and not even on this list
        Flow.objects.filter(id=flow1.id).update(is_system=True)

        response = self.client.get(reverse("flows.flow_list"))

        self.assertNotContains(response, flow1.name)
        self.assertEqual(1, response.context["folders"][0]["count"])
        self.assertEqual(1, response.context["folders"][1]["count"])

        # single message flow should not be even in the archived list
        Flow.objects.filter(id=flow1.id).update(is_system=True, is_archived=True)

        response = self.client.get(reverse("flows.flow_archived"))
        self.assertNotContains(response, flow1.name)
        self.assertEqual(1, response.context["folders"][0]["count"])
        self.assertEqual(1, response.context["folders"][1]["count"])  # only flow2

    def test_get_definition(self):
        flow = self.get_flow("color_v13")

        # if definition is outdated, metadata values are updated from db object
        flow.name = "Amazing Flow"
        flow.save(update_fields=("name",))

        self.assertEqual("Amazing Flow", flow.get_definition()["name"])

        # make a flow that looks like a legacy flow
        flow = self.get_flow("color_v11")
        original_def = self.get_flow_json("color_v11")

        flow.version_number = "11.12"
        flow.save(update_fields=("version_number",))

        revision = flow.revisions.get()
        revision.definition = original_def
        revision.spec_version = "11.12"
        revision.save(update_fields=("definition", "spec_version"))

        self.assertIn("metadata", flow.get_definition())

        # if definition is outdated, metadata values are updated from db object
        flow.name = "Amazing Flow"
        flow.save(update_fields=("name",))

        self.assertEqual("Amazing Flow", flow.get_definition()["metadata"]["name"])

        # metadata section can be missing too
        del original_def["metadata"]
        revision.definition = original_def
        revision.save(update_fields=("definition",))

        self.assertEqual("Amazing Flow", flow.get_definition()["metadata"]["name"])

    def test_fetch_revisions(self):
        self.login(self.admin)

        # we should have one revision for an imported flow
        flow = self.get_flow("color_v11")
        original_def = self.get_flow_json("color_v11")

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

        response = self.client.get(reverse("flows.flow_revisions", args=[flow.uuid]))
        self.assertEqual(
            [
                {
                    "user": {"email": "Administrator@nyaruka.com", "name": ""},
                    "created_on": matchers.ISODate(),
                    "id": revisions[0].id,
                    "version": "13.1.0",
                    "revision": 2,
                },
                {
                    "user": {"email": "Administrator@nyaruka.com", "name": ""},
                    "created_on": matchers.ISODate(),
                    "id": revisions[1].id,
                    "version": "11.12",
                    "revision": 1,
                },
            ],
            response.json()["results"],
        )

        # now make our legacy revision invalid
        definition = original_def.copy()
        del definition["base_language"]
        revisions[1].definition = definition
        revisions[1].save(update_fields=("definition",))

        # should be back to one valid revision (the non-legacy one)
        response = self.client.get(reverse("flows.flow_revisions", args=[flow.uuid]))
        self.assertEqual(1, len(response.json()["results"]))

        # fetch that revision
        revision_id = response.json()["results"][0]["id"]
        response = self.client.get(f"{reverse('flows.flow_revisions', args=[flow.uuid])}{revision_id}/")

        # make sure we can read the definition
        definition = response.json()["definition"]
        self.assertEqual("base", definition["language"])

        # really break the legacy revision
        revisions[1].definition = {"foo": "bar"}
        revisions[1].save(update_fields=("definition",))

        # should still have only one valid revision
        response = self.client.get(reverse("flows.flow_revisions", args=[flow.uuid]))
        self.assertEqual(1, len(response.json()["results"]))

        # fix the legacy revision
        revisions[1].definition = original_def.copy()
        revisions[1].save(update_fields=("definition",))

        # fetch that revision
        response = self.client.get(f"{reverse('flows.flow_revisions', args=[flow.uuid])}{revisions[1].id}/")

        # should automatically migrate to latest spec
        self.assertEqual(Flow.CURRENT_SPEC_VERSION, response.json()["definition"]["spec_version"])

        # but we can also limit how far it is migrated
        response = self.client.get(
            f"{reverse('flows.flow_revisions', args=[flow.uuid])}{revisions[1].id}/?version=13.0.0"
        )

        # should only have been migrated to that version
        self.assertEqual("13.0.0", response.json()["definition"]["spec_version"])

    def test_save_revisions(self):
        self.login(self.admin)
        self.client.post(reverse("flows.flow_create"), data=dict(name="Go Flow", flow_type=Flow.TYPE_MESSAGE))
        flow = Flow.objects.get(
            org=self.org, name="Go Flow", flow_type=Flow.TYPE_MESSAGE, version_number=Flow.CURRENT_SPEC_VERSION
        )
        response = self.client.get(reverse("flows.flow_revisions", args=[flow.uuid]))
        self.assertEqual(1, len(response.json()))

        definition = flow.revisions.all().first().definition

        # viewers can't save flows
        self.login(self.user)
        response = self.client.post(
            reverse("flows.flow_revisions", args=[flow.uuid]), definition, content_type="application/json"
        )
        self.assertEqual(403, response.status_code)

        # check that we can create a new revision
        self.login(self.admin)
        response = self.client.post(
            reverse("flows.flow_revisions", args=[flow.uuid]), definition, content_type="application/json"
        )
        new_revision = response.json()
        self.assertEqual(2, new_revision["revision"][Flow.DEFINITION_REVISION])

        # but we can't save our old revision
        response = self.client.post(
            reverse("flows.flow_revisions", args=[flow.uuid]), definition, content_type="application/json"
        )
        self.assertResponseError(
            response, "description", "Your changes will not be saved until you refresh your browser"
        )

        # but we can't save our old revision
        response = self.client.post(
            reverse("flows.flow_revisions", args=[flow.uuid]), definition, content_type="application/json"
        )
        self.assertResponseError(
            response, "description", "Your changes will not be saved until you refresh your browser"
        )

        # or save an old version
        definition = flow.revisions.all().first().definition
        definition[Flow.DEFINITION_SPEC_VERSION] = "11.12"
        response = self.client.post(
            reverse("flows.flow_revisions", args=[flow.uuid]), definition, content_type="application/json"
        )
        self.assertResponseError(response, "description", "Your flow has been upgraded to the latest version")

    def test_inactive_flow(self):
        flow = self.get_flow("color_v13")
        flow.release(self.admin)

        self.login(self.admin)

        response = self.client.get(reverse("flows.flow_revisions", args=[flow.uuid]))

        self.assertEqual(404, response.status_code)

        response = self.client.get(reverse("flows.flow_activity", args=[flow.uuid]))

        self.assertEqual(404, response.status_code)

    @mock_mailroom
    def test_broadcast(self, mr_mocks):
        contact = self.create_contact("Bob", phone="+593979099111")
        flow = self.create_flow()
        ivr_flow = self.create_flow(flow_type=Flow.TYPE_VOICE)

        broadcast_url = reverse("flows.flow_broadcast", args=[flow.id])

        self.assertUpdateFetch(
            broadcast_url,
            allow_viewers=False,
            allow_editors=True,
            form_fields=["mode", "omnibox", "query", "exclude_in_other", "exclude_reruns"],
        )

        # create flow start with a query
        mr_mocks.parse_query("frank", cleaned='name ~ "frank"', fields=[])

        self.assertUpdateSubmit(
            broadcast_url,
            {"mode": "query", "query": "frank", "exclude_in_other": False, "exclude_reruns": False},
        )

        start = FlowStart.objects.get()
        self.assertEqual(flow, start.flow)
        self.assertEqual(FlowStart.STATUS_PENDING, start.status)
        self.assertTrue(start.restart_participants)
        self.assertTrue(start.include_active)
        self.assertEqual('name ~ "frank"', start.query)

        self.assertEqual(1, len(mr_mocks.queued_batch_tasks))
        self.assertEqual("start_flow", mr_mocks.queued_batch_tasks[0]["type"])

        FlowStart.objects.all().delete()

        # create flow start with a bogus query
        mr_mocks.error("query contains an error")

        self.assertUpdateSubmit(
            broadcast_url,
            {"mode": "query", "query": 'name = "frank', "exclude_in_other": False, "exclude_reruns": False},
            form_errors={"query": "query contains an error"},
            object_unchanged=flow,
        )

        # try to create a query based flow start with an empty query
        self.assertUpdateSubmit(
            broadcast_url,
            {"mode": "query", "query": "", "exclude_in_other": False, "exclude_reruns": False},
            form_errors={"query": "This field is required."},
            object_unchanged=flow,
        )

        # try to create selection based flow start with an empty selection
        self.assertUpdateSubmit(
            broadcast_url,
            {"mode": "select", "omnibox": [], "exclude_in_other": False, "exclude_reruns": False},
            form_errors={"omnibox": "This field is required."},
            object_unchanged=flow,
        )

        # create selection based flow start with exclude_in_other and exclude_reruns both left unchecked
        selection = json.dumps({"id": contact.uuid, "name": contact.name, "type": "contact"})

        self.assertUpdateSubmit(
            broadcast_url,
            {"mode": "select", "omnibox": selection, "exclude_in_other": False, "exclude_reruns": False},
        )

        start = FlowStart.objects.get()
        self.assertEqual({contact}, set(start.contacts.all()))
        self.assertEqual(flow, start.flow)
        self.assertEqual(FlowStart.TYPE_MANUAL, start.start_type)
        self.assertEqual(FlowStart.STATUS_PENDING, start.status)
        self.assertTrue(start.restart_participants)
        self.assertTrue(start.include_active)

        self.assertEqual(2, len(mr_mocks.queued_batch_tasks))
        self.assertEqual("start_flow", mr_mocks.queued_batch_tasks[1]["type"])

        FlowStart.objects.all().delete()

        # create selection based flow start with exclude_in_other and exclude_reruns both checked
        self.assertUpdateSubmit(
            broadcast_url, {"mode": "select", "omnibox": selection, "exclude_in_other": True, "exclude_reruns": True}
        )

        start = FlowStart.objects.get()
        self.assertEqual({contact}, set(start.contacts.all()))
        self.assertEqual(flow, start.flow)
        self.assertEqual(FlowStart.STATUS_PENDING, start.status)
        self.assertFalse(start.restart_participants)
        self.assertFalse(start.include_active)

        self.assertEqual(3, len(mr_mocks.queued_batch_tasks))

        # trying to start again should fail because there is already a pending start for this flow
        response = self.requestView(broadcast_url, self.admin)
        self.assertContains(response, "This flow is already being started - please wait")
        self.assertNotContains(response, "Start Flow")

        # clear that start and try to start the IVR flow
        FlowStart.objects.all().delete()
        ivr_bcast_url = reverse("flows.flow_broadcast", args=[ivr_flow.id])

        # shouldn't be able to since we don't have a call channel
        response = self.requestView(ivr_bcast_url, self.admin)
        self.assertContains(
            response, 'To get started you need to <a href="/channels/channel/claim/">add a voice channel</a>'
        )
        self.assertNotContains(response, "Start Flow")

        # if we release our send channel we also can't start a regular messaging flow
        self.channel.release(self.admin)

        response = self.requestView(broadcast_url, self.admin)
        self.assertContains(
            response, 'To get started you need to <a href="/channels/channel/claim/">add a channel</a>'
        )
        self.assertNotContains(response, "Start Flow")

    @mock_mailroom
    def test_broadcast_background_flow(self, mr_mocks):
        flow = self.create_flow(flow_type=Flow.TYPE_BACKGROUND)

        broadcast_url = reverse("flows.flow_broadcast", args=[flow.id])

        response = self.assertUpdateFetch(
            broadcast_url,
            allow_viewers=False,
            allow_editors=True,
            form_fields=["mode", "omnibox", "query", "exclude_in_other", "exclude_reruns"],
        )

        # option to exclude contact in other flows is hidden
        self.assertNotContains(response, "Exclude contacts currently in a flow")

        # create flow start with a query
        mr_mocks.parse_query("frank", cleaned='name ~ "frank"', fields=[])

        self.assertUpdateSubmit(broadcast_url, {"mode": "query", "query": "frank", "exclude_reruns": False})

        start = FlowStart.objects.get()
        self.assertEqual(flow, start.flow)
        self.assertEqual(FlowStart.STATUS_PENDING, start.status)
        self.assertTrue(start.restart_participants)  # should default to true
        self.assertTrue(start.include_active)
        self.assertEqual('name ~ "frank"', start.query)

    @patch("temba.flows.views.uuid4")
    def test_upload_media_action(self, mock_uuid):
        flow = self.get_flow("color_v13")
        other_org_flow = self.create_flow(org=self.org2)

        upload_media_action_url = reverse("flows.flow_upload_media_action", args=[flow.uuid])

        def assert_media_upload(filename, expected_type, expected_path):
            with open(filename, "rb") as data:
                post_data = dict(file=data, action="", HTTP_X_FORWARDED_HTTPS="https")
                response = self.client.post(upload_media_action_url, post_data)

                self.assertEqual(response.status_code, 200)
                actual_type = response.json()["type"]
                actual_url = response.json()["url"]
                self.assertEqual(actual_type, expected_type)
                self.assertEqual(actual_url, expected_path)

        self.login(self.admin)

        mock_uuid.side_effect = ["11111-111-11", "22222-222-22", "33333-333-33", "44444-444-44"]

        assert_media_upload(
            f"{settings.MEDIA_ROOT}/test_media/steve.marten.jpg",
            "image/jpeg",
            "%s/attachments/%d/%d/steps/%s%s" % (settings.STORAGE_URL, self.org.id, flow.id, "11111-111-11", ".jpg"),
        )
        assert_media_upload(
            f"{settings.MEDIA_ROOT}/test_media/snow.mp4",
            "video/mp4",
            "%s/attachments/%d/%d/steps/%s%s" % (settings.STORAGE_URL, self.org.id, flow.id, "22222-222-22", ".mp4"),
        )
        assert_media_upload(
            f"{settings.MEDIA_ROOT}/test_media/snow.m4a",
            "audio/mp4",
            "%s/attachments/%d/%d/steps/%s%s" % (settings.STORAGE_URL, self.org.id, flow.id, "33333-333-33", ".m4a"),
        )

        # can't upload for flow in other org
        with open(f"{settings.MEDIA_ROOT}/test_media/steve.marten.jpg", "rb") as data:
            upload_url = reverse("flows.flow_upload_media_action", args=[other_org_flow.uuid])
            response = self.client.post(upload_url, {"file": data, "action": "", "HTTP_X_FORWARDED_HTTPS": "https"})
            self.assertLoginRedirect(response)

    def test_copy_view(self):
        flow = self.get_flow("color")

        self.login(self.admin)

        response = self.client.post(reverse("flows.flow_copy", args=[flow.id]))

        flow_copy = Flow.objects.get(org=self.org, name="Copy of %s" % flow.name)

        self.assertRedirect(response, reverse("flows.flow_editor", args=[flow_copy.uuid]))

    def test_recent_messages(self):
        contact = self.create_contact("Bob", phone="+593979099111")
        flow = self.get_flow("favorites_v13")
        flow_nodes = flow.get_definition()["nodes"]
        color_prompt = flow_nodes[0]
        color_other = flow_nodes[1]
        color_split = flow_nodes[2]
        beer_prompt = flow_nodes[3]
        beer_split = flow_nodes[5]

        self.login(self.admin)
        recent_messages_url = reverse("flows.flow_recent_messages", args=[flow.uuid])

        # URL params for different flow path segments
        entry_params = f'?exits={color_prompt["exits"][0]["uuid"]}&to={color_split["uuid"]}'
        other_params = f'?exits={color_split["exits"][-1]["uuid"]}&to={color_other["uuid"]}'
        blue_params = f'?exits={color_split["exits"][2]["uuid"]}&to={beer_prompt["uuid"]}'
        invalid_params = f'?exits={color_split["exits"][0]["uuid"]}&to={color_split["uuid"]}'

        def assert_recent(resp, msgs):
            self.assertEqual(msgs, [r["text"] for r in resp.json()])

        # no params returns no results
        assert_recent(self.client.get(recent_messages_url), [])

        session = (
            MockSessionWriter(contact, flow)
            .visit(color_prompt)
            .send_msg("What is your favorite color?", self.channel)
            .visit(color_split)
            .wait()
            .resume(msg=self.create_incoming_msg(contact, "chartreuse"))
            .visit(color_other)
            .send_msg("I don't know that color. Try again.")
            .visit(color_split)
            .wait()
            .save()
        )

        response = self.client.get(recent_messages_url + entry_params)
        assert_recent(response, ["What is your favorite color?"])

        # one incoming message on the other segment
        response = self.client.get(recent_messages_url + other_params)
        assert_recent(response, ["chartreuse"])

        # nothing yet on the blue segment
        response = self.client.get(recent_messages_url + blue_params)
        assert_recent(response, [])

        # invalid segment
        response = self.client.get(recent_messages_url + invalid_params)
        assert_recent(response, [])

        (
            session.resume(msg=self.create_incoming_msg(contact, "mauve"))
            .visit(color_other)
            .send_msg("I don't know that color. Try again.")
            .visit(color_split)
            .wait()
            .save()
        )

        response = self.client.get(recent_messages_url + entry_params)
        assert_recent(response, ["What is your favorite color?"])

        response = self.client.get(recent_messages_url + other_params)
        assert_recent(response, ["mauve", "chartreuse"])

        response = self.client.get(recent_messages_url + blue_params)
        assert_recent(response, [])

        (
            session.resume(msg=self.create_incoming_msg(contact, "blue"))
            .visit(beer_prompt, exit_index=2)
            .send_msg("I like Blue. What beer do you like?")
            .visit(beer_split)
            .wait()
            .save()
        )

        response = self.client.get(recent_messages_url + entry_params)
        assert_recent(response, ["What is your favorite color?"])

        response = self.client.get(recent_messages_url + other_params)
        assert_recent(response, ["mauve", "chartreuse"])

        response = self.client.get(recent_messages_url + blue_params)
        assert_recent(response, ["blue"])

    def test_results(self):
        flow = self.get_flow("favorites_v13")
        flow_nodes = flow.get_definition()["nodes"]
        color_prompt = flow_nodes[0]
        color_split = flow_nodes[2]
        beer_prompt = flow_nodes[3]
        beer_split = flow_nodes[5]
        name_prompt = flow_nodes[6]
        name_split = flow_nodes[7]
        end_prompt = flow_nodes[8]

        pete = self.create_contact("Pete", phone="+12065553027")
        pete_session = (
            MockSessionWriter(pete, flow)
            .visit(color_prompt)
            .send_msg("What is your favorite color?", self.channel)
            .visit(color_split)
            .wait()
            .resume(msg=self.create_incoming_msg(pete, "blue"))
            .set_result("Color", "blue", "Blue", "blue")
            .visit(beer_prompt, exit_index=2)
            .send_msg("Good choice, I like Blue too! What is your favorite beer?")
            .visit(beer_split)
            .wait()
            .save()
        )

        jimmy = self.create_contact("Jimmy", phone="+12065553026")
        (
            MockSessionWriter(jimmy, flow)
            .visit(color_prompt)
            .send_msg("What is your favorite color?", self.channel)
            .visit(color_split)
            .wait()
            .resume(msg=self.create_incoming_msg(jimmy, "red"))
            .set_result("Color", "red", "Red", "red")
            .visit(beer_prompt, exit_index=2)
            .send_msg("Good choice, I like Red too! What is your favorite beer?")
            .visit(beer_split)
            .wait()
            .resume(msg=self.create_incoming_msg(jimmy, "turbo"))
            .set_result("Beer", "turbo", "Turbo King", "turbo")
            .visit(name_prompt, exit_index=2)
            .send_msg("Mmmmm... delicious Turbo King. Lastly, what is your name?")
            .visit(name_split)
            .wait()
            .save()
        )

        john = self.create_contact("John", phone="+12065553028")
        (
            MockSessionWriter(john, flow)
            .visit(color_prompt)
            .send_msg("What is your favorite color?", self.channel)
            .visit(color_split)
            .wait()
            .fail("some error")
            .save()
        )

        self.login(self.admin)

        with patch("temba.flows.views.FlowCRUDL.RunTable.paginate_by", 1):
            response = self.client.get(reverse("flows.flow_results", args=[flow.uuid]))

            # the rulesets should be present as column headers
            self.assertContains(response, "Beer")
            self.assertContains(response, "Color")
            self.assertContains(response, "Name")

            # fetch counts endpoint, should have 2 color results (one is a test contact)
            response = self.client.get(reverse("flows.flow_category_counts", args=[flow.uuid]))
            counts = response.json()["counts"]
            self.assertEqual("Color", counts[0]["name"])
            self.assertEqual(2, counts[0]["total"])

            # fetch our intercooler rows for the run table
            response = self.client.get(reverse("flows.flow_run_table", args=[flow.id]))
            self.assertEqual(len(response.context["runs"]), 1)
            self.assertEqual(200, response.status_code)
            self.assertContains(response, "Jimmy")
            self.assertContains(response, "red")
            self.assertContains(response, "Red")
            self.assertContains(response, "turbo")
            self.assertContains(response, "Turbo King")
            self.assertNotContains(response, "skol")

            # one more row to add
            self.assertEqual(1, len(response.context["runs"]))
            # self.assertNotContains(response, "ic-append-from")

            next_link = re.search('ic-append-from="(.*)" ic-trigger-on', force_text(response.content)).group(1)
            response = self.client.get(next_link)
            self.assertEqual(200, response.status_code)

            FlowCRUDL.ActivityChart.HISTOGRAM_MIN = 0
            FlowCRUDL.ActivityChart.PERIOD_MIN = 0

            # and some charts
            response = self.client.get(reverse("flows.flow_activity_chart", args=[flow.id]))

            # we have two active runs, one failed run
            self.assertEqual(response.context["failed"], 1)
            self.assertEqual(response.context["active"], 2)
            self.assertEqual(response.context["completed"], 0)
            self.assertEqual(response.context["expired"], 0)
            self.assertEqual(response.context["interrupted"], 0)
            self.assertContains(response, "3 Responses")

            # now complete the flow for Pete
            (
                pete_session.resume(msg=self.create_incoming_msg(pete, "primus"))
                .set_result("Beer", "primus", "Primus", "primus")
                .visit(name_prompt)
                .send_msg("Mmmmm... delicious Primus. Lastly, what is your name?")
                .visit(name_split)
                .wait()
                .resume(msg=self.create_incoming_msg(pete, "Pete"))
                .visit(end_prompt)
                .complete()
                .save()
            )

            response = self.client.get(reverse("flows.flow_run_table", args=[flow.id]))
            self.assertEqual(len(response.context["runs"]), 1)
            self.assertEqual(200, response.status_code)
            self.assertContains(response, "Pete")
            self.assertNotContains(response, "Jimmy")

            # one more row to add
            self.assertEqual(1, len(response.context["runs"]))

            next_link = re.search('ic-append-from="(.*)" ic-trigger-on', force_text(response.content)).group(1)
            response = self.client.get(next_link)
            self.assertEqual(200, response.status_code)
            self.assertEqual(1, len(response.context["runs"]))
            self.assertContains(response, "Jimmy")

            # now only one active, one completed, one failed and 5 total responses
            response = self.client.get(reverse("flows.flow_activity_chart", args=[flow.id]))

            self.assertEqual(response.context["failed"], 1)
            self.assertEqual(response.context["active"], 1)
            self.assertEqual(response.context["completed"], 1)
            self.assertEqual(response.context["expired"], 0)
            self.assertEqual(response.context["interrupted"], 0)
            self.assertContains(response, "5 Responses")

            # they all happened on the same day
            response = self.client.get(reverse("flows.flow_activity_chart", args=[flow.id]))
            points = response.context["histogram"]
            self.assertEqual(1, len(points))

            # put one of our counts way in the past so we get a different histogram scale
            count = FlowPathCount.objects.filter(flow=flow).order_by("id")[1]
            count.period = count.period - timedelta(days=25)
            count.save()
            response = self.client.get(reverse("flows.flow_activity_chart", args=[flow.id]))
            points = response.context["histogram"]
            self.assertTrue(timedelta(days=24) < (points[1]["bucket"] - points[0]["bucket"]))

            # pick another scale
            count.period = count.period - timedelta(days=600)
            count.save()
            response = self.client.get(reverse("flows.flow_activity_chart", args=[flow.id]))

            # this should give us a more compressed histogram
            points = response.context["histogram"]
            self.assertTrue(timedelta(days=620) < (points[1]["bucket"] - points[0]["bucket"]))

            self.assertEqual(24, len(response.context["hod"]))
            self.assertEqual(7, len(response.context["dow"]))

        # delete a run
        with patch("temba.flows.views.FlowCRUDL.RunTable.paginate_by", 100):
            response = self.client.get(reverse("flows.flow_run_table", args=[flow.id]))
            self.assertEqual(len(response.context["runs"]), 2)

            self.client.post(reverse("flows.flowrun_delete", args=[response.context["runs"][0].id]))
            response = self.client.get(reverse("flows.flow_run_table", args=[flow.id]))
            self.assertEqual(len(response.context["runs"]), 1)

        with patch("temba.flows.views.FlowCRUDL.RunTable.paginate_by", 1):
            # create one empty run
            FlowRun.objects.create(org=self.org, flow=flow, contact=pete, responded=True)

            # fetch our intercooler rows for the run table
            response = self.client.get(reverse("flows.flow_run_table", args=[flow.id]))
            self.assertEqual(len(response.context["runs"]), 1)
            self.assertEqual(200, response.status_code)

        with patch("temba.flows.views.FlowCRUDL.RunTable.paginate_by", 1):
            # create one empty run
            FlowRun.objects.create(org=self.org, flow=flow, contact=pete, responded=False)

            # fetch our intercooler rows for the run table
            response = self.client.get("%s?responded=bla" % reverse("flows.flow_run_table", args=[flow.id]))
            self.assertEqual(len(response.context["runs"]), 1)
            self.assertEqual(200, response.status_code)

            response = self.client.get("%s?responded=true" % reverse("flows.flow_run_table", args=[flow.id]))
            self.assertEqual(len(response.context["runs"]), 1)

    def test_activity(self):
        flow = self.get_flow("favorites_v13")
        flow_nodes = flow.get_definition()["nodes"]
        color_prompt = flow_nodes[0]
        color_split = flow_nodes[2]
        beer_prompt = flow_nodes[3]
        beer_split = flow_nodes[5]

        pete = self.create_contact("Pete", phone="+12065553027")
        (
            MockSessionWriter(pete, flow)
            .visit(color_prompt)
            .send_msg("What is your favorite color?", self.channel)
            .visit(color_split)
            .wait()
            .resume(msg=self.create_incoming_msg(pete, "blue"))
            .set_result("Color", "blue", "Blue", "blue")
            .visit(beer_prompt, exit_index=2)
            .send_msg("Good choice, I like Blue too! What is your favorite beer?")
            .visit(beer_split)
            .wait()
            .save()
        )

        self.login(self.admin)
        response = self.client.get(reverse("flows.flow_activity", args=[flow.uuid]))

        self.assertEqual(200, response.status_code)
        self.assertEqual(
            {
                "is_starting": False,
                "nodes": {beer_split["uuid"]: 1},
                "segments": {
                    f'{color_prompt["exits"][0]["uuid"]}:{color_split["uuid"]}': 1,
                    f'{color_split["exits"][2]["uuid"]}:{beer_prompt["uuid"]}': 1,
                    f'{beer_prompt["exits"][0]["uuid"]}:{beer_split["uuid"]}': 1,
                },
            },
            response.json(),
        )

    def test_activity_chart_of_inactive_flow(self):
        flow = self.get_flow("favorites")
        flow.release(self.admin)

        self.login(self.admin)
        response = self.client.get(reverse("flows.flow_activity_chart", args=[flow.id]))

        self.assertEqual(404, response.status_code)

    def test_run_table_of_inactive_flow(self):
        flow = self.get_flow("favorites")
        flow.release(self.admin)

        self.login(self.admin)
        response = self.client.get(reverse("flows.flow_run_table", args=[flow.id]))

        self.assertEqual(404, response.status_code)

    def test_category_counts_of_inactive_flow(self):
        flow = self.get_flow("favorites")
        flow.release(self.admin)

        self.login(self.admin)
        response = self.client.get(reverse("flows.flow_category_counts", args=[flow.uuid]))

        self.assertEqual(404, response.status_code)

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

        with self.assertRaises(FlowValidationException) as cm:
            flow.save_revision(self.admin, flow_json)

        self.assertEqual(f"unable to read flow: node UUID {mode0_uuid} isn't unique", str(cm.exception))

        # check view converts exception to error response
        response = self.client.post(
            reverse("flows.flow_revisions", args=[flow.uuid]), data=flow_json, content_type="application/json"
        )

        self.assertEqual(400, response.status_code)
        self.assertEqual(
            {
                "status": "failure",
                "description": "Your flow failed validation. Please refresh your browser.",
                "detail": f"unable to read flow: node UUID {mode0_uuid} isn't unique",
            },
            response.json(),
        )

    def test_change_language(self):
        self.org.set_flow_languages(self.admin, ["eng", "spa", "ara"])

        flow = self.get_flow("favorites_v13")

        change_url = reverse("flows.flow_change_language", args=[flow.id])

        self.assertUpdateSubmit(
            change_url, {"language": ""}, form_errors={"language": "This field is required."}, object_unchanged=flow
        )

        self.assertUpdateSubmit(
            change_url, {"language": "fra"}, form_errors={"language": "Not a valid language."}, object_unchanged=flow
        )

        self.assertUpdateSubmit(change_url, {"language": "spa"}, success_status=302)

        flow_def = flow.get_definition()
        self.assertIn("eng", flow_def["localization"])
        self.assertEqual("¿Cuál es tu color favorito?", flow_def["nodes"][0]["actions"][0]["text"])

    def test_export_and_download_translation(self):
        self.org.set_flow_languages(self.admin, ["spa"])

        flow = self.get_flow("favorites")
        export_url = reverse("flows.flow_export_translation", args=[flow.id])

        self.assertUpdateFetch(
            export_url, allow_viewers=False, allow_editors=True, form_fields=["language", "include_args"]
        )

        # submit with no language
        response = self.assertUpdateSubmit(export_url, {})

        self.assertEqual(f"/flow/download_translation/?flow={flow.id}&language=&exclude_args=1", response.url)

        # check fetching the PO from the download link
        with patch("temba.mailroom.client.MailroomClient.po_export") as mock_po_export:
            mock_po_export.return_value = b'msgid "Red"\nmsgstr "Roja"\n\n'
            response = self.assertReadFetch(response.url, allow_viewers=False, allow_editors=True)

            self.assertEqual(b'msgid "Red"\nmsgstr "Roja"\n\n', response.content)
            self.assertEqual('attachment; filename="favorites.po"', response["Content-Disposition"])
            self.assertEqual("text/x-gettext-translation", response["Content-Type"])

        # submit with a language
        response = self.requestView(export_url, self.admin, post_data={"language": "spa"})

        self.assertEqual(f"/flow/download_translation/?flow={flow.id}&language=spa&exclude_args=1", response.url)

        # check fetching the PO from the download link
        with patch("temba.mailroom.client.MailroomClient.po_export") as mock_po_export:
            mock_po_export.return_value = b'msgid "Red"\nmsgstr "Roja"\n\n'
            response = self.requestView(response.url, self.admin)

            # filename includes language now
            self.assertEqual('attachment; filename="favorites.spa.po"', response["Content-Disposition"])

        # check submitting the form from a modal
        response = self.client.post(export_url, data={}, HTTP_X_PJAX=True)
        self.assertEqual(
            f"/flow/download_translation/?flow={flow.id}&language=&exclude_args=1", response["Temba-Success"]
        )

    def test_import_translation(self):
        self.org.set_flow_languages(self.admin, ["eng", "spa"])

        flow = self.get_flow("favorites_v13")
        step1_url = reverse("flows.flow_import_translation", args=[flow.id])

        # check step 1 is just a file upload
        self.assertUpdateFetch(step1_url, allow_viewers=False, allow_editors=True, form_fields=["po_file"])

        # submit with no file
        self.assertUpdateSubmit(
            step1_url, {}, form_errors={"po_file": "This field is required."}, object_unchanged=flow
        )

        # submit with something that's empty
        response = self.requestView(step1_url, self.admin, post_data={"po_file": io.BytesIO(b"")})
        self.assertFormError(response, "form", "po_file", "The submitted file is empty.")

        # submit with something that's not a valid PO file
        response = self.requestView(step1_url, self.admin, post_data={"po_file": io.BytesIO(b"msgid")})
        self.assertFormError(response, "form", "po_file", "File doesn't appear to be a valid PO file.")

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
            response, "form", "po_file", "Contains translations in English which is the base language of this flow."
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
            response,
            "form",
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

        response = self.assertUpdateFetch(
            response.url, allow_viewers=False, allow_editors=True, form_fields=["language"]
        )
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

        response = self.assertUpdateFetch(step2_url, allow_viewers=False, allow_editors=True, form_fields=["language"])
        self.assertContains(response, "Spanish (spa)")
        self.assertEqual({"language": "spa"}, response.context["form"].initial)

        # confirm the import
        with patch("temba.mailroom.client.MailroomClient.po_import") as mock_po_import:
            mock_po_import.return_value = {"flows": [flow.get_definition()]}

            response = self.requestView(step2_url, self.admin, post_data={"language": "spa"})

        # should redirect back to editor
        self.assertEqual(302, response.status_code)
        self.assertEqual(f"/flow/editor/{flow.uuid}/", response.url)

        # should have a new revision
        self.assertEqual(2, flow.revisions.count())


class FlowRunTest(TembaTest):
    def setUp(self):
        super().setUp()

        self.contact = self.create_contact("Ben Haggerty", phone="+250788123123")

    def test_as_archive_json(self):
        flow = self.get_flow("color_v13")
        flow_nodes = flow.get_definition()["nodes"]
        color_prompt = flow_nodes[0]
        color_split = flow_nodes[4]
        color_other = flow_nodes[3]

        msg_in = self.create_incoming_msg(self.contact, "green")

        run = (
            MockSessionWriter(self.contact, flow)
            .visit(color_prompt)
            .send_msg("What is your favorite color?", self.channel)
            .visit(color_split)
            .wait()
            .resume(msg=msg_in)
            .set_result("Color", "green", "Other", "green")
            .visit(color_other)
            .send_msg("That is a funny color. Try again.", self.channel)
            .visit(color_split)
            .wait()
            .save()
        ).session.runs.get()

        run_json = run.as_archive_json()

        self.assertEqual(
            set(run_json.keys()),
            set(
                [
                    "id",
                    "uuid",
                    "flow",
                    "contact",
                    "responded",
                    "path",
                    "values",
                    "events",
                    "created_on",
                    "modified_on",
                    "exited_on",
                    "exit_type",
                    "submitted_by",
                ]
            ),
        )

        self.assertEqual(run.id, run_json["id"])
        self.assertEqual({"uuid": str(flow.uuid), "name": "Colors"}, run_json["flow"])
        self.assertEqual({"uuid": str(self.contact.uuid), "name": "Ben Haggerty"}, run_json["contact"])
        self.assertTrue(run_json["responded"])

        self.assertEqual(
            [
                {"node": matchers.UUID4String(), "time": matchers.ISODate()},
                {"node": matchers.UUID4String(), "time": matchers.ISODate()},
                {"node": matchers.UUID4String(), "time": matchers.ISODate()},
                {"node": matchers.UUID4String(), "time": matchers.ISODate()},
            ],
            run_json["path"],
        )

        self.assertEqual(
            {
                "color": {
                    "category": "Other",
                    "input": "green",
                    "name": "Color",
                    "node": matchers.UUID4String(),
                    "time": matchers.ISODate(),
                    "value": "green",
                }
            },
            run_json["values"],
        )

        self.assertEqual(
            [
                {
                    "created_on": matchers.ISODate(),
                    "msg": {
                        "channel": {"name": "Test Channel", "uuid": matchers.UUID4String()},
                        "text": "What is your favorite color?",
                        "urn": "tel:+250788123123",
                        "uuid": matchers.UUID4String(),
                    },
                    "step_uuid": matchers.UUID4String(),
                    "type": "msg_created",
                },
                {
                    "created_on": matchers.ISODate(),
                    "msg": {
                        "channel": {"name": "Test Channel", "uuid": matchers.UUID4String()},
                        "text": "green",
                        "urn": "tel:+250788123123",
                        "uuid": matchers.UUID4String(),
                    },
                    "step_uuid": matchers.UUID4String(),
                    "type": "msg_received",
                },
                {
                    "created_on": matchers.ISODate(),
                    "msg": {
                        "channel": {"name": "Test Channel", "uuid": matchers.UUID4String()},
                        "text": "That is a funny color. Try again.",
                        "urn": "tel:+250788123123",
                        "uuid": matchers.UUID4String(),
                    },
                    "step_uuid": matchers.UUID4String(),
                    "type": "msg_created",
                },
            ],
            run_json["events"],
        )

        self.assertEqual(run.created_on.isoformat(), run_json["created_on"])
        self.assertEqual(run.modified_on.isoformat(), run_json["modified_on"])
        self.assertIsNone(run_json["exit_type"])
        self.assertIsNone(run_json["exited_on"])
        self.assertIsNone(run_json["submitted_by"])

    def _check_deletion(self, delete_reason, expected, session_completed=True):
        """
        Runs our favorites flow, then releases the run with the passed in delete_reason, asserting our final state
        """

        flow = self.get_flow("favorites_v13")
        flow_nodes = flow.get_definition()["nodes"]
        color_prompt = flow_nodes[0]
        color_split = flow_nodes[2]
        beer_prompt = flow_nodes[3]
        beer_split = flow_nodes[5]
        name_prompt = flow_nodes[6]
        name_split = flow_nodes[7]
        end_prompt = flow_nodes[8]

        start = FlowStart.create(flow, self.admin, contacts=[self.contact])
        if session_completed:
            (
                MockSessionWriter(self.contact, flow, start)
                .visit(color_prompt)
                .send_msg("What is your favorite color?", self.channel)
                .visit(color_split)
                .wait()
                .resume(msg=self.create_incoming_msg(self.contact, "blue"))
                .set_result("Color", "blue", "Blue", "blue")
                .visit(beer_prompt, exit_index=2)
                .send_msg("Good choice, I like Blue too! What is your favorite beer?")
                .visit(beer_split)
                .wait()
                .resume(msg=self.create_incoming_msg(self.contact, "primus"))
                .set_result("Beer", "primus", "Primus", "primus")
                .visit(name_prompt, exit_index=2)
                .send_msg("Mmmmm... delicious Turbo King. Lastly, what is your name?")
                .visit(name_split)
                .wait()
                .resume(msg=self.create_incoming_msg(self.contact, "Ryan Lewis"))
                .visit(end_prompt)
                .complete()
                .save()
            )
        else:
            (
                MockSessionWriter(self.contact, flow, start)
                .visit(color_prompt)
                .send_msg("What is your favorite color?", self.channel)
                .visit(color_split)
                .wait()
                .resume(msg=self.create_incoming_msg(self.contact, "blue"))
                .set_result("Color", "blue", "Blue", "blue")
                .visit(beer_prompt, exit_index=2)
                .send_msg("Good choice, I like Blue too! What is your favorite beer?")
                .visit(beer_split)
                .wait()
                .resume(msg=self.create_incoming_msg(self.contact, "primus"))
                .set_result("Beer", "primus", "Primus", "primus")
                .visit(name_prompt, exit_index=2)
                .send_msg("Mmmmm... delicious Turbo King. Lastly, what is your name?")
                .visit(name_split)
                .wait()
                .save()
            )

        run = FlowRun.objects.get(contact=self.contact)
        run.release(delete_reason)

        recent = FlowPathRecentRun.get_recent([color_prompt["exits"][0]["uuid"]], color_split["uuid"])

        self.assertEqual(0, len(recent))

        cat_counts = {c["key"]: c for c in flow.get_category_counts()["counts"]}

        self.assertEqual(2, len(cat_counts))
        self.assertEqual(expected["red_count"], cat_counts["color"]["categories"][0]["count"])
        self.assertEqual(expected["primus_count"], cat_counts["color"]["categories"][0]["count"])

        self.assertEqual(expected["start_count"], FlowStartCount.get_count(start))
        self.assertEqual(expected["run_count"], flow.get_run_stats())

        self.assertFalse(FlowRun.objects.filter(id=run.id).exists())

    @patch("temba.mailroom.queue_interrupt")
    def test_deletion(self, mock_queue_interrupt):
        self._check_deletion(
            None,
            {
                "red_count": 0,
                "primus_count": 0,
                "start_count": 0,
                "run_count": {
                    "total": 0,
                    "active": 0,
                    "completed": 0,
                    "expired": 0,
                    "interrupted": 0,
                    "failed": 0,
                    "completion": 0,
                },
            },
        )
        self.assertFalse(mock_queue_interrupt.called)

    @patch("temba.mailroom.queue_interrupt")
    def test_user_deletion_with_complete_session(self, mock_queue_interrupt):
        self._check_deletion(
            "U",
            {
                "red_count": 0,
                "primus_count": 0,
                "start_count": 0,
                "run_count": {
                    "total": 0,
                    "active": 0,
                    "completed": 0,
                    "expired": 0,
                    "interrupted": 0,
                    "failed": 0,
                    "completion": 0,
                },
            },
        )
        self.assertFalse(mock_queue_interrupt.called)

    @patch("temba.mailroom.queue_interrupt")
    def test_user_deletion_without_complete_session(self, mock_queue_interrupt):
        self._check_deletion(
            "U",
            {
                "red_count": 0,
                "primus_count": 0,
                "start_count": 0,
                "run_count": {
                    "total": 0,
                    "active": 0,
                    "completed": 0,
                    "expired": 0,
                    "interrupted": 0,
                    "failed": 0,
                    "completion": 0,
                },
            },
            False,
        )
        mock_queue_interrupt.assert_called_once()

    @patch("temba.mailroom.queue_interrupt")
    def test_archiving(self, mock_queue_interrupt):
        self._check_deletion(
            "A",
            {
                "red_count": 1,
                "primus_count": 1,
                "start_count": 1,
                "run_count": {
                    "total": 1,
                    "active": 0,
                    "completed": 1,
                    "expired": 0,
                    "interrupted": 0,
                    "failed": 0,
                    "completion": 100,
                },
            },
        )
        self.assertFalse(mock_queue_interrupt.called)


class FlowSessionTest(TembaTest):
    def test_trim(self):
        contact = self.create_contact("Ben Haggerty", phone="+250788123123")
        flow = self.get_flow("color")

        # create some runs that have sessions
        session1 = FlowSession.objects.create(uuid=uuid4(), org=self.org, contact=contact)
        session2 = FlowSession.objects.create(uuid=uuid4(), org=self.org, contact=contact)
        session3 = FlowSession.objects.create(uuid=uuid4(), org=self.org, contact=contact)
        run1 = FlowRun.objects.create(org=self.org, flow=flow, contact=contact, session=session1)
        run2 = FlowRun.objects.create(org=self.org, flow=flow, contact=contact, session=session2)
        run3 = FlowRun.objects.create(org=self.org, flow=flow, contact=contact, session=session3)

        # create an IVR call with session
        call = self.create_incoming_call(flow, contact)
        run4 = call.runs.get()

        self.assertIsNotNone(run1.session)
        self.assertIsNotNone(run2.session)
        self.assertIsNotNone(run3.session)
        self.assertIsNotNone(run4.session)

        # end run1 and run4's sessions in the past
        run1.session.ended_on = datetime.datetime(2015, 9, 15, 0, 0, 0, 0, pytz.UTC)
        run1.session.save(update_fields=("ended_on",))
        run4.session.ended_on = datetime.datetime(2015, 9, 15, 0, 0, 0, 0, pytz.UTC)
        run4.session.save(update_fields=("ended_on",))

        # end run2's session now
        run2.session.ended_on = timezone.now()
        run2.session.save(update_fields=("ended_on",))

        trim_flow_sessions_and_starts()

        run1, run2, run3, run4 = FlowRun.objects.order_by("id")

        self.assertIsNone(run1.session)
        self.assertIsNotNone(run2.session)  # ended too recently to be deleted
        self.assertIsNotNone(run3.session)  # never ended
        self.assertIsNone(run4.session)
        self.assertIsNotNone(run4.connection)  # channel session unaffected

        # only sessions for run2 and run3 are left
        self.assertEqual(FlowSession.objects.count(), 2)


class FlowStartTest(TembaTest):
    def test_trim(self):
        contact = self.create_contact("Ben Haggerty", phone="+250788123123")
        group = self.create_group("Testers", contacts=[contact])
        flow = self.get_flow("color")

        def create_start(user, start_type, status, modified_on, **kwargs):
            start = FlowStart.create(flow, user, start_type, **kwargs)
            start.status = status
            start.modified_on = modified_on
            start.save(update_fields=("status", "modified_on"))

            session = FlowSession.objects.create(uuid=uuid4(), org=self.org, contact=contact)
            FlowRun.objects.create(org=self.org, contact=contact, flow=flow, session=session, start=start)

            FlowStartCount.objects.create(start=start, count=1, is_squashed=False)

        date1 = timezone.now() - timedelta(days=8)
        date2 = timezone.now()

        # some starts that won't be deleted because they are user created
        create_start(self.admin, FlowStart.TYPE_API, FlowStart.STATUS_COMPLETE, date1, contacts=[contact])
        create_start(self.admin, FlowStart.TYPE_MANUAL, FlowStart.STATUS_COMPLETE, date1, groups=[group])
        create_start(self.admin, FlowStart.TYPE_MANUAL, FlowStart.STATUS_FAILED, date1, query="name ~ Ben")

        # some starts that are mailroom created and will be deleted
        create_start(None, FlowStart.TYPE_FLOW_ACTION, FlowStart.STATUS_COMPLETE, date1, contacts=[contact])
        create_start(None, FlowStart.TYPE_TRIGGER, FlowStart.STATUS_FAILED, date1, groups=[group])

        # some starts that are mailroom created but not completed so won't be deleted
        create_start(None, FlowStart.TYPE_FLOW_ACTION, FlowStart.STATUS_STARTING, date1, contacts=[contact])
        create_start(None, FlowStart.TYPE_TRIGGER, FlowStart.STATUS_PENDING, date1, groups=[group])
        create_start(None, FlowStart.TYPE_TRIGGER, FlowStart.STATUS_PENDING, date1, groups=[group])

        # some starts that are mailroom created but too new so won't be deleted
        create_start(None, FlowStart.TYPE_FLOW_ACTION, FlowStart.STATUS_COMPLETE, date2, contacts=[contact])
        create_start(None, FlowStart.TYPE_TRIGGER, FlowStart.STATUS_FAILED, date2, groups=[group])

        trim_flow_sessions_and_starts()

        # check that related objects still exist!
        contact.refresh_from_db()
        group.refresh_from_db()
        flow.refresh_from_db()

        # check user created starts still exist
        self.assertEqual(3, FlowStart.objects.filter(created_by=self.admin).count())

        # 5 mailroom created starts remain
        self.assertEqual(5, FlowStart.objects.filter(created_by=None).count())

        # only runs from our remaining starts still have start ids
        self.assertEqual(8, FlowRun.objects.exclude(start=None).count())

        # the 3 that aren't complete...
        self.assertEqual(3, FlowStart.objects.filter(created_by=None).exclude(status="C").exclude(status="F").count())

        # and the 2 that are too new
        self.assertEqual(2, FlowStart.objects.filter(created_by=None, modified_on=date2).count())


class ExportFlowResultsTest(TembaTest):
    def setUp(self):
        super().setUp()

        self.contact = self.create_contact("Eric", phone="+250788382382")
        self.contact2 = self.create_contact("Nic", phone="+250788383383")
        self.contact3 = self.create_contact("Norbert", phone="+250788123456")

    def _export(
        self,
        flow,
        responded_only=False,
        include_msgs=True,
        contact_fields=None,
        extra_urns=(),
        group_memberships=None,
        has_results=True,
    ):
        """
        Exports results for the given flow and returns the generated workbook
        """
        self.login(self.admin)

        form = {
            "flows": [flow.id],
            "responded_only": responded_only,
            "include_msgs": include_msgs,
            "extra_urns": extra_urns,
        }
        if contact_fields:
            form["contact_fields"] = [c.id for c in contact_fields]

        if group_memberships:
            form["group_memberships"] = [g.id for g in group_memberships]

        readonly_models = {FlowRun, ContactGroup, ContactField}
        if has_results:
            readonly_models.add(Contact)

        with self.mockReadOnly(assert_models=readonly_models):
            response = self.client.post(reverse("flows.flow_export_results"), form)

        self.assertEqual(response.status_code, 302)

        task = ExportFlowResultsTask.objects.order_by("-id").first()
        self.assertIsNotNone(task)

        filename = "%s/test_orgs/%d/results_exports/%s.xlsx" % (settings.MEDIA_ROOT, self.org.pk, task.uuid)
        return load_workbook(filename=os.path.join(settings.MEDIA_ROOT, filename))

    @mock_mailroom
    def test_export_results(self, mr_mocks):
        flow = self.get_flow("color_v13")
        flow_nodes = flow.get_definition()["nodes"]
        color_prompt = flow_nodes[0]
        color_split = flow_nodes[4]
        color_other = flow_nodes[3]
        orange_reply = flow_nodes[1]

        # add a spec for a hidden result to this flow
        flow.metadata[Flow.METADATA_RESULTS].append(
            {
                "key": "_color_classification",
                "name": "_Color Classification",
                "categories": ["Success", "Skipped", "Failure"],
                "node_uuids": [color_split["uuid"]],
            }
        )

        age = self.create_field("age", "Age")
        devs = self.create_group("Devs", [self.contact])

        mods = self.contact.update_fields({age: "36"})
        mods += self.contact.update_urns(["tel:+250788382382", "twitter:erictweets"])
        self.contact.modify(self.admin, mods)

        # contact name with an illegal character
        self.contact3.name = "Nor\02bert"
        self.contact3.save(update_fields=("name",))

        contact3_run1 = (
            MockSessionWriter(self.contact3, flow)
            .visit(color_prompt)
            .send_msg("What is your favorite color?", self.channel)
            .visit(color_split)
            .wait()
            .save()
        ).session.runs.get()

        contact1_in1 = self.create_incoming_msg(self.contact, "light beige")
        contact1_in2 = self.create_incoming_msg(self.contact, "orange")
        contact1_run1 = (
            MockSessionWriter(self.contact, flow)
            .visit(color_prompt)
            .send_msg("What is your favorite color?", self.channel)
            .visit(color_split)
            .wait()
            .resume(msg=contact1_in1)
            .set_result("Color", "light beige", "Other", "light beige")
            .visit(color_other)
            .send_msg("That is a funny color. Try again.", self.channel)
            .visit(color_split)
            .wait()
            .resume(msg=contact1_in2)
            .set_result("Color", "orange", "Orange", "orange")
            .set_result("_Color Classification", "orange", "Success", "color_selection")  # hidden result
            .visit(orange_reply)
            .send_msg(
                "I love orange too! You said: orange which is category: Orange You are: 0788 382 382 SMS: orange Flow: color: orange",
                self.channel,
            )
            .complete()
            .save()
        ).session.runs.get()

        contact2_in1 = self.create_incoming_msg(self.contact2, "green")
        contact2_run1 = (
            MockSessionWriter(self.contact2, flow)
            .visit(color_prompt)
            .send_msg("What is your favorite color?", self.channel)
            .visit(color_split)
            .wait()
            .resume(msg=contact2_in1)
            .set_result("Color", "green", "Other", "green")
            .visit(color_other)
            .send_msg("That is a funny color. Try again.", self.channel)
            .visit(color_split)
            .wait()
            .save()
        ).session.runs.get()

        contact2_run2 = (
            MockSessionWriter(self.contact2, flow)
            .visit(color_prompt)
            .send_msg("What is your favorite color?", self.channel)
            .visit(color_split)
            .wait()
            .save()
        ).session.runs.get()

        contact1_in3 = self.create_incoming_msg(self.contact, " blue ")
        contact1_run2 = (
            MockSessionWriter(self.contact, flow)
            .visit(color_prompt)
            .send_msg("What is your favorite color?", self.channel)
            .visit(color_split)
            .wait()
            .resume(msg=contact1_in3)
            .set_result("Color", "blue", "Blue", " blue ")
            .visit(orange_reply)
            .send_msg("Blue is sad. :(", self.channel)
            .complete()
            .save()
        ).session.runs.get()

        # check can't export anonymously
        exported = self.client.get(reverse("flows.flow_export_results") + "?ids=%d" % flow.id)
        self.assertEqual(302, exported.status_code)

        self.login(self.admin)

        # create a dummy export task so that we won't be able to export
        blocking_export = ExportFlowResultsTask.objects.create(
            org=self.org, created_by=self.admin, modified_by=self.admin
        )
        response = self.client.post(
            reverse("flows.flow_export_results"), {"flows": [flow.id], "group_memberships": [devs.id]}, follow=True
        )
        self.assertContains(response, "already an export in progress")

        # ok, mark that one as finished and try again
        blocking_export.update_status(ExportFlowResultsTask.STATUS_COMPLETE)

        for run in (contact1_run1, contact2_run1, contact3_run1, contact1_run2, contact2_run2):
            run.refresh_from_db()

        with self.assertLogs("temba.flows.models", level="INFO") as captured_logger:
            with patch(
                "temba.flows.models.ExportFlowResultsTask.LOG_PROGRESS_PER_ROWS", new_callable=PropertyMock
            ) as log_info_threshold:
                # make sure that we trigger logger
                log_info_threshold.return_value = 1

                with self.assertNumQueries(45):
                    workbook = self._export(flow, group_memberships=[devs])

                self.assertEqual(len(captured_logger.output), 3)
                self.assertTrue("fetching runs from archives to export" in captured_logger.output[0])
                self.assertTrue("found 5 runs in database to export" in captured_logger.output[1])
                self.assertTrue("exported 5 in" in captured_logger.output[2])

        # check that notifications were created
        export = ExportFlowResultsTask.objects.order_by("id").last()
        self.assertEqual(
            1, self.admin.notifications.filter(notification_type="export:finished", results_export=export).count()
        )

        tz = self.org.timezone

        sheet_runs, sheet_msgs = workbook.worksheets

        # check runs sheet...
        self.assertEqual(6, len(list(sheet_runs.rows)))  # header + 5 runs
        self.assertEqual(11, len(list(sheet_runs.columns)))

        self.assertExcelRow(
            sheet_runs,
            0,
            [
                "Contact UUID",
                "URN",
                "Name",
                "Group:Devs",
                "Started",
                "Modified",
                "Exited",
                "Run UUID",
                "Color (Category) - Colors",
                "Color (Value) - Colors",
                "Color (Text) - Colors",
            ],
        )

        self.assertExcelRow(
            sheet_runs,
            1,
            [
                contact3_run1.contact.uuid,
                "+250788123456",
                "Norbert",
                False,
                contact3_run1.created_on,
                contact3_run1.modified_on,
                "",
                contact3_run1.uuid,
                "",
                "",
                "",
            ],
            tz,
        )

        self.assertExcelRow(
            sheet_runs,
            2,
            [
                contact1_run1.contact.uuid,
                "+250788382382",
                "Eric",
                True,
                contact1_run1.created_on,
                contact1_run1.modified_on,
                contact1_run1.exited_on,
                contact1_run1.uuid,
                "Orange",
                "orange",
                "orange",
            ],
            tz,
        )

        self.assertExcelRow(
            sheet_runs,
            3,
            [
                contact2_run1.contact.uuid,
                "+250788383383",
                "Nic",
                False,
                contact2_run1.created_on,
                contact2_run1.modified_on,
                contact2_run1.exited_on,
                contact2_run1.uuid,
                "Other",
                "green",
                "green",
            ],
            tz,
        )

        self.assertExcelRow(
            sheet_runs,
            4,
            [
                contact2_run2.contact.uuid,
                "+250788383383",
                "Nic",
                False,
                contact2_run2.created_on,
                contact2_run2.modified_on,
                "",
                contact2_run2.uuid,
                "",
                "",
                "",
            ],
            tz,
        )

        self.assertExcelRow(
            sheet_runs,
            5,
            [
                contact1_run2.contact.uuid,
                "+250788382382",
                "Eric",
                True,
                contact1_run2.created_on,
                contact1_run2.modified_on,
                contact1_run2.exited_on,
                contact1_run2.uuid,
                "Blue",
                "blue",
                " blue ",
            ],
            tz,
        )

        # check messages sheet...
        self.assertEqual(14, len(list(sheet_msgs.rows)))  # header + 13 messages
        self.assertEqual(8, len(list(sheet_msgs.columns)))

        self.assertExcelRow(
            sheet_msgs, 0, ["Contact UUID", "URN", "Name", "Date", "Direction", "Message", "Attachments", "Channel"]
        )

        contact1_out1 = contact1_run1.get_messages().get(text="What is your favorite color?")
        contact1_out2 = contact1_run1.get_messages().get(text="That is a funny color. Try again.")
        contact1_out3 = contact1_run1.get_messages().get(text__startswith="I love orange too")
        contact3_out1 = contact3_run1.get_messages().get(text="What is your favorite color?")

        def msg_event_time(run, text):
            for evt in run.get_msg_events():
                if evt["msg"]["text"] == text:
                    return iso8601.parse_date(evt["created_on"])
            raise self.fail(f"no such message on run with text '{text}'")

        self.assertExcelRow(
            sheet_msgs,
            1,
            [
                self.contact3.uuid,
                "+250788123456",
                "Norbert",
                contact3_out1.created_on,
                "OUT",
                "What is your favorite color?",
                "",
                "Test Channel",
            ],
            tz,
        )
        self.assertExcelRow(
            sheet_msgs,
            2,
            [
                self.contact.uuid,
                "+250788382382",
                "Eric",
                contact1_out1.created_on,
                "OUT",
                "What is your favorite color?",
                "",
                "Test Channel",
            ],
            tz,
        )
        self.assertExcelRow(
            sheet_msgs,
            3,
            [
                self.contact.uuid,
                "+250788382382",
                "Eric",
                msg_event_time(contact1_run1, "light beige"),
                "IN",
                "light beige",
                "",
                "Test Channel",
            ],
            tz,
        )
        self.assertExcelRow(
            sheet_msgs,
            4,
            [
                self.contact.uuid,
                "+250788382382",
                "Eric",
                contact1_out2.created_on,
                "OUT",
                "That is a funny color. Try again.",
                "",
                "Test Channel",
            ],
            tz,
        )
        self.assertExcelRow(
            sheet_msgs,
            5,
            [
                self.contact.uuid,
                "+250788382382",
                "Eric",
                msg_event_time(contact1_run1, "orange"),
                "IN",
                "orange",
                "",
                "Test Channel",
            ],
            tz,
        )
        self.assertExcelRow(
            sheet_msgs,
            6,
            [
                self.contact.uuid,
                "+250788382382",
                "Eric",
                contact1_out3.created_on,
                "OUT",
                "I love orange too! You said: orange which is category: Orange You are: 0788 382 382 SMS: orange Flow: color: orange",
                "",
                "Test Channel",
            ],
            tz,
        )

        # test without msgs or unresponded
        with self.assertNumQueries(43):
            workbook = self._export(flow, include_msgs=False, responded_only=True, group_memberships=(devs,))

        tz = self.org.timezone
        sheet_runs = workbook.worksheets[0]

        self.assertEqual(4, len(list(sheet_runs.rows)))  # header + 3 runs
        self.assertEqual(11, len(list(sheet_runs.columns)))

        self.assertExcelRow(
            sheet_runs,
            0,
            [
                "Contact UUID",
                "URN",
                "Name",
                "Group:Devs",
                "Started",
                "Modified",
                "Exited",
                "Run UUID",
                "Color (Category) - Colors",
                "Color (Value) - Colors",
                "Color (Text) - Colors",
            ],
        )

        self.assertExcelRow(
            sheet_runs,
            1,
            [
                contact1_run1.contact.uuid,
                "+250788382382",
                "Eric",
                True,
                contact1_run1.created_on,
                contact1_run1.modified_on,
                contact1_run1.exited_on,
                contact1_run1.uuid,
                "Orange",
                "orange",
                "orange",
            ],
            tz,
        )

        self.assertExcelRow(
            sheet_runs,
            2,
            [
                contact2_run1.contact.uuid,
                "+250788383383",
                "Nic",
                False,
                contact2_run1.created_on,
                contact2_run1.modified_on,
                contact2_run1.exited_on,
                contact2_run1.uuid,
                "Other",
                "green",
                "green",
            ],
            tz,
        )

        # test export with a contact field
        with self.assertNumQueries(45):
            workbook = self._export(
                flow,
                include_msgs=False,
                responded_only=True,
                contact_fields=[age],
                extra_urns=["twitter", "line"],
                group_memberships=[devs],
            )

        tz = self.org.timezone
        (sheet_runs,) = workbook.worksheets

        # check runs sheet...
        self.assertEqual(4, len(list(sheet_runs.rows)))  # header + 3 runs
        self.assertEqual(14, len(list(sheet_runs.columns)))

        self.assertExcelRow(
            sheet_runs,
            0,
            [
                "Contact UUID",
                "URN",
                "URN:Twitter",
                "URN:Line",
                "Name",
                "Group:Devs",
                "Field:Age",
                "Started",
                "Modified",
                "Exited",
                "Run UUID",
                "Color (Category) - Colors",
                "Color (Value) - Colors",
                "Color (Text) - Colors",
            ],
        )

        self.assertExcelRow(
            sheet_runs,
            1,
            [
                contact1_run1.contact.uuid,
                "+250788382382",
                "erictweets",
                "",
                "Eric",
                True,
                "36",
                contact1_run1.created_on,
                contact1_run1.modified_on,
                contact1_run1.exited_on,
                contact1_run1.uuid,
                "Orange",
                "orange",
                "orange",
            ],
            tz,
        )

        # test that we don't exceed the limit on rows per sheet
        with patch("temba.flows.models.ExportFlowResultsTask.MAX_EXCEL_ROWS", 4):
            workbook = self._export(flow)
            expected_sheets = [
                ("Runs", 4),
                ("Runs (2)", 3),
                ("Messages", 4),
                ("Messages (2)", 4),
                ("Messages (3)", 4),
                ("Messages (4)", 4),
                ("Messages (5)", 2),
            ]

            for s, sheet in enumerate(workbook.worksheets):
                self.assertEqual((sheet.title, len(list(sheet.rows))), expected_sheets[s])

        # test we can export archived flows
        flow.is_archived = True
        flow.save()

        workbook = self._export(flow)

        tz = self.org.timezone

        sheet_runs, sheet_msgs = workbook.worksheets

        # check runs sheet...
        self.assertEqual(6, len(list(sheet_runs.rows)))  # header + 5 runs
        self.assertEqual(10, len(list(sheet_runs.columns)))

        # check messages sheet...
        self.assertEqual(14, len(list(sheet_msgs.rows)))  # header + 13 messages
        self.assertEqual(8, len(list(sheet_msgs.columns)))

    def test_anon_org(self):
        with AnonymousOrg(self.org):
            flow = self.get_flow("color_v13")
            flow_nodes = flow.get_definition()["nodes"]
            color_prompt = flow_nodes[0]
            color_split = flow_nodes[4]

            msg_in = self.create_incoming_msg(self.contact, "orange")

            run1 = (
                MockSessionWriter(self.contact, flow)
                .visit(color_prompt)
                .send_msg("What is your favorite color?", self.channel)
                .visit(color_split)
                .wait()
                .resume(msg=msg_in)
                .set_result("Color", "orange", "Orange", "orange")
                .send_msg("I love orange too!", self.channel)
                .complete()
                .save()
            ).session.runs.get()

            workbook = self._export(flow)
            self.assertEqual(1, len(workbook.worksheets))
            sheet_runs = workbook.worksheets[0]
            self.assertExcelRow(
                sheet_runs,
                0,
                [
                    "Contact UUID",
                    "ID",
                    "Name",
                    "Started",
                    "Modified",
                    "Exited",
                    "Run UUID",
                    "Color (Category) - Colors",
                    "Color (Value) - Colors",
                    "Color (Text) - Colors",
                ],
            )

            self.assertExcelRow(
                sheet_runs,
                1,
                [
                    self.contact.uuid,
                    f"{self.contact.id:010d}",
                    "Eric",
                    run1.created_on,
                    run1.modified_on,
                    run1.exited_on,
                    run1.uuid,
                    "Orange",
                    "orange",
                    "orange",
                ],
                self.org.timezone,
            )

    def test_msg_with_attachments(self):
        flow = self.get_flow("color_v13")
        flow_nodes = flow.get_definition()["nodes"]
        color_prompt = flow_nodes[0]
        color_split = flow_nodes[4]

        contact1_run1 = (
            MockSessionWriter(self.contact, flow)
            .visit(color_prompt)
            .send_msg(
                "What is your favorite color?", self.channel, attachments=["audio:http://rapidpro.io/audio/sound.mp3"]
            )
            .visit(color_split)
            .wait()
            .save()
        ).session.runs.get()

        contact1_out1 = contact1_run1.get_messages().get(text="What is your favorite color?")

        workbook = self._export(flow)
        self.assertEqual(2, len(workbook.worksheets))

        sheet_runs, sheet_msgs = workbook.worksheets

        tz = self.org.timezone

        # check runs sheet...
        self.assertEqual(2, len(list(sheet_runs.rows)))
        self.assertEqual(10, len(list(sheet_runs.columns)))

        # check messages sheet...
        self.assertEqual(2, len(list(sheet_msgs.rows)))
        self.assertEqual(8, len(list(sheet_msgs.columns)))

        self.assertExcelRow(
            sheet_msgs,
            1,
            [
                contact1_out1.contact.uuid,
                "+250788382382",
                "Eric",
                contact1_out1.created_on,
                "OUT",
                "What is your favorite color?",
                "http://rapidpro.io/audio/sound.mp3",
                "Test Channel",
            ],
            tz,
        )

    def test_broadcast_only_flow(self):
        flow = self.get_flow("send_only_v13")
        send_node = flow.get_definition()["nodes"][0]

        for contact in [self.contact, self.contact2, self.contact3]:
            (
                MockSessionWriter(contact, flow)
                .visit(send_node)
                .send_msg("This is the first message.", self.channel)
                .send_msg("This is the second message.", self.channel)
                .complete()
                .save()
            ).session.runs.get()

        for contact in [self.contact, self.contact2]:
            (
                MockSessionWriter(contact, flow)
                .visit(send_node)
                .send_msg("This is the first message.", self.channel)
                .send_msg("This is the second message.", self.channel)
                .complete()
                .save()
            ).session.runs.get()

        contact1_run1, contact2_run1, contact3_run1, contact1_run2, contact2_run2 = FlowRun.objects.order_by("id")

        with self.assertNumQueries(53):
            workbook = self._export(flow)

        tz = self.org.timezone

        sheet_runs, sheet_msgs = workbook.worksheets

        # check runs sheet...
        self.assertEqual(len(list(sheet_runs.rows)), 6)  # header + 5 runs
        self.assertEqual(len(list(sheet_runs.columns)), 7)

        self.assertExcelRow(
            sheet_runs, 0, ["Contact UUID", "URN", "Name", "Started", "Modified", "Exited", "Run UUID"]
        )

        self.assertExcelRow(
            sheet_runs,
            1,
            [
                contact1_run1.contact.uuid,
                "+250788382382",
                "Eric",
                contact1_run1.created_on,
                contact1_run1.modified_on,
                contact1_run1.exited_on,
                contact1_run1.uuid,
            ],
            tz,
        )
        self.assertExcelRow(
            sheet_runs,
            2,
            [
                contact2_run1.contact.uuid,
                "+250788383383",
                "Nic",
                contact2_run1.created_on,
                contact2_run1.modified_on,
                contact2_run1.exited_on,
                contact2_run1.uuid,
            ],
            tz,
        )
        self.assertExcelRow(
            sheet_runs,
            3,
            [
                contact3_run1.contact.uuid,
                "+250788123456",
                "Norbert",
                contact3_run1.created_on,
                contact3_run1.modified_on,
                contact3_run1.exited_on,
                contact3_run1.uuid,
            ],
            tz,
        )
        self.assertExcelRow(
            sheet_runs,
            4,
            [
                contact1_run2.contact.uuid,
                "+250788382382",
                "Eric",
                contact1_run2.created_on,
                contact1_run2.modified_on,
                contact1_run2.exited_on,
                contact1_run2.uuid,
            ],
            tz,
        )
        self.assertExcelRow(
            sheet_runs,
            5,
            [
                contact2_run2.contact.uuid,
                "+250788383383",
                "Nic",
                contact2_run2.created_on,
                contact2_run2.modified_on,
                contact2_run2.exited_on,
                contact2_run2.uuid,
            ],
            tz,
        )

        # check messages sheet...
        self.assertEqual(len(list(sheet_msgs.rows)), 11)  # header + 10 messages
        self.assertEqual(len(list(sheet_msgs.columns)), 8)

        self.assertExcelRow(
            sheet_msgs, 0, ["Contact UUID", "URN", "Name", "Date", "Direction", "Message", "Attachments", "Channel"]
        )

        c1_run1_msg1 = contact1_run1.get_messages().get(text="This is the first message.")
        c1_run1_msg2 = contact1_run1.get_messages().get(text="This is the second message.")

        c2_run1_msg1 = contact2_run1.get_messages().get(text="This is the first message.")
        c2_run1_msg2 = contact2_run1.get_messages().get(text="This is the second message.")

        c3_run1_msg1 = contact3_run1.get_messages().get(text="This is the first message.")
        c3_run1_msg2 = contact3_run1.get_messages().get(text="This is the second message.")

        c1_run2_msg1 = contact1_run2.get_messages().get(text="This is the first message.")
        c1_run2_msg2 = contact1_run2.get_messages().get(text="This is the second message.")

        c2_run2_msg1 = contact2_run2.get_messages().get(text="This is the first message.")
        c2_run2_msg2 = contact2_run2.get_messages().get(text="This is the second message.")

        self.assertExcelRow(
            sheet_msgs,
            1,
            [
                c1_run1_msg1.contact.uuid,
                "+250788382382",
                "Eric",
                c1_run1_msg1.created_on,
                "OUT",
                "This is the first message.",
                "",
                "Test Channel",
            ],
            tz,
        )

        self.assertExcelRow(
            sheet_msgs,
            2,
            [
                c1_run1_msg2.contact.uuid,
                "+250788382382",
                "Eric",
                c1_run1_msg2.created_on,
                "OUT",
                "This is the second message.",
                "",
                "Test Channel",
            ],
            tz,
        )

        self.assertExcelRow(
            sheet_msgs,
            3,
            [
                c2_run1_msg1.contact.uuid,
                "+250788383383",
                "Nic",
                c2_run1_msg1.created_on,
                "OUT",
                "This is the first message.",
                "",
                "Test Channel",
            ],
            tz,
        )

        self.assertExcelRow(
            sheet_msgs,
            4,
            [
                c2_run1_msg2.contact.uuid,
                "+250788383383",
                "Nic",
                c2_run1_msg2.created_on,
                "OUT",
                "This is the second message.",
                "",
                "Test Channel",
            ],
            tz,
        )

        self.assertExcelRow(
            sheet_msgs,
            5,
            [
                c3_run1_msg1.contact.uuid,
                "+250788123456",
                "Norbert",
                c3_run1_msg1.created_on,
                "OUT",
                "This is the first message.",
                "",
                "Test Channel",
            ],
            tz,
        )

        self.assertExcelRow(
            sheet_msgs,
            6,
            [
                c3_run1_msg2.contact.uuid,
                "+250788123456",
                "Norbert",
                c3_run1_msg2.created_on,
                "OUT",
                "This is the second message.",
                "",
                "Test Channel",
            ],
            tz,
        )

        self.assertExcelRow(
            sheet_msgs,
            7,
            [
                c1_run2_msg1.contact.uuid,
                "+250788382382",
                "Eric",
                c1_run2_msg1.created_on,
                "OUT",
                "This is the first message.",
                "",
                "Test Channel",
            ],
            tz,
        )

        self.assertExcelRow(
            sheet_msgs,
            8,
            [
                c1_run2_msg2.contact.uuid,
                "+250788382382",
                "Eric",
                c1_run2_msg2.created_on,
                "OUT",
                "This is the second message.",
                "",
                "Test Channel",
            ],
            tz,
        )

        self.assertExcelRow(
            sheet_msgs,
            9,
            [
                c2_run2_msg1.contact.uuid,
                "+250788383383",
                "Nic",
                c2_run2_msg1.created_on,
                "OUT",
                "This is the first message.",
                "",
                "Test Channel",
            ],
            tz,
        )

        self.assertExcelRow(
            sheet_msgs,
            10,
            [
                c2_run2_msg2.contact.uuid,
                "+250788383383",
                "Nic",
                c2_run2_msg2.created_on,
                "OUT",
                "This is the second message.",
                "",
                "Test Channel",
            ],
            tz,
        )

        # test without msgs or unresponded
        with self.assertNumQueries(36):
            workbook = self._export(flow, include_msgs=False, responded_only=True, has_results=False)

        tz = self.org.timezone
        sheet_runs = workbook.worksheets[0]

        self.assertEqual(len(list(sheet_runs.rows)), 1)  # header; no resposes to a broadcast only flow
        self.assertEqual(len(list(sheet_runs.columns)), 7)

        self.assertExcelRow(
            sheet_runs, 0, ["Contact UUID", "URN", "Name", "Started", "Modified", "Exited", "Run UUID"]
        )

    def test_replaced_rulesets(self):
        favorites = self.get_flow("favorites_v13")
        flow_json = favorites.get_definition()
        flow_nodes = flow_json["nodes"]
        color_prompt = flow_nodes[0]
        color_other = flow_nodes[1]
        color_split = flow_nodes[2]
        beer_prompt = flow_nodes[3]
        beer_split = flow_nodes[5]

        contact3_run1 = (
            MockSessionWriter(self.contact3, favorites)
            .visit(color_prompt)
            .send_msg("What is your favorite color?", self.channel)
            .visit(color_split)
            .wait()
            .save()
        ).session.runs.get()

        contact1_in1 = self.create_incoming_msg(self.contact, "light beige")
        contact1_in2 = self.create_incoming_msg(self.contact, "red")
        contact1_run1 = (
            MockSessionWriter(self.contact, favorites)
            .visit(color_prompt)
            .send_msg("What is your favorite color?", self.channel)
            .visit(color_split)
            .wait()
            .resume(msg=contact1_in1)
            .set_result("Color", "light beige", "Other", "light beige")
            .visit(color_other)
            .send_msg("I don't know that color. Try again.", self.channel)
            .visit(color_split)
            .wait()
            .save()
            .resume(msg=contact1_in2)
            .set_result("Color", "red", "Red", "red")
            .visit(beer_prompt)
            .send_msg("Good choice, I like Red too! What is your favorite beer?", self.channel)
            .visit(beer_split)
            .complete()
            .save()
        ).session.runs.get()

        devs = self.create_group("Devs", [self.contact])

        # now remap the uuid for our color
        flow_json = json.loads(json.dumps(flow_json).replace(color_split["uuid"], str(uuid4())))
        favorites.save_revision(self.admin, flow_json)
        flow_nodes = flow_json["nodes"]
        color_prompt = flow_nodes[0]
        color_other = flow_nodes[1]
        color_split = flow_nodes[2]

        contact2_in1 = self.create_incoming_msg(self.contact2, "green")
        contact2_run1 = (
            MockSessionWriter(self.contact2, favorites)
            .visit(color_prompt)
            .send_msg("What is your favorite color?", self.channel)
            .visit(color_split)
            .wait()
            .resume(msg=contact2_in1)
            .set_result("Color", "green", "Green", "green")
            .visit(beer_prompt)
            .send_msg("Good choice, I like Green too! What is your favorite beer?", self.channel)
            .visit(beer_split)
            .wait()
            .save()
        ).session.runs.get()

        contact2_run2 = (
            MockSessionWriter(self.contact2, favorites)
            .visit(color_prompt)
            .send_msg("What is your favorite color?", self.channel)
            .visit(color_split)
            .wait()
            .save()
        ).session.runs.get()

        contact1_in3 = self.create_incoming_msg(self.contact, " blue ")
        contact1_run2 = (
            MockSessionWriter(self.contact, favorites)
            .visit(color_prompt)
            .send_msg("What is your favorite color?", self.channel)
            .visit(color_split)
            .wait()
            .resume(msg=contact1_in3)
            .set_result("Color", "blue", "Blue", " blue ")
            .visit(beer_prompt)
            .send_msg("Good choice, I like Blue too! What is your favorite beer?", self.channel)
            .visit(beer_split)
            .wait()
            .save()
        ).session.runs.get()

        for run in (contact1_run1, contact2_run1, contact3_run1, contact1_run2, contact2_run2):
            run.refresh_from_db()

        workbook = self._export(favorites, group_memberships=[devs])

        tz = self.org.timezone

        sheet_runs, sheet_msgs = workbook.worksheets

        # check runs sheet...
        self.assertEqual(6, len(list(sheet_runs.rows)))  # header + 5 runs
        self.assertEqual(17, len(list(sheet_runs.columns)))

        self.assertExcelRow(
            sheet_runs,
            0,
            [
                "Contact UUID",
                "URN",
                "Name",
                "Group:Devs",
                "Started",
                "Modified",
                "Exited",
                "Run UUID",
                "Color (Category) - Favorites",
                "Color (Value) - Favorites",
                "Color (Text) - Favorites",
                "Beer (Category) - Favorites",
                "Beer (Value) - Favorites",
                "Beer (Text) - Favorites",
                "Name (Category) - Favorites",
                "Name (Value) - Favorites",
                "Name (Text) - Favorites",
            ],
        )

        self.assertExcelRow(
            sheet_runs,
            1,
            [
                contact3_run1.contact.uuid,
                "+250788123456",
                "Norbert",
                False,
                contact3_run1.created_on,
                contact3_run1.modified_on,
                "",
                contact3_run1.uuid,
                "",
                "",
                "",
                "",
                "",
                "",
                "",
                "",
                "",
            ],
            tz,
        )

        self.assertExcelRow(
            sheet_runs,
            2,
            [
                contact1_run1.contact.uuid,
                "+250788382382",
                "Eric",
                True,
                contact1_run1.created_on,
                contact1_run1.modified_on,
                contact1_run1.exited_on,
                contact1_run1.uuid,
                "Red",
                "red",
                "red",
                "",
                "",
                "",
                "",
                "",
                "",
            ],
            tz,
        )

        self.assertExcelRow(
            sheet_runs,
            3,
            [
                contact2_run1.contact.uuid,
                "+250788383383",
                "Nic",
                False,
                contact2_run1.created_on,
                contact2_run1.modified_on,
                contact2_run1.exited_on,
                contact2_run1.uuid,
                "Green",
                "green",
                "green",
                "",
                "",
                "",
                "",
                "",
                "",
            ],
            tz,
        )

        self.assertExcelRow(
            sheet_runs,
            4,
            [
                contact2_run2.contact.uuid,
                "+250788383383",
                "Nic",
                False,
                contact2_run2.created_on,
                contact2_run2.modified_on,
                "",
                contact2_run2.uuid,
                "",
                "",
                "",
                "",
                "",
                "",
                "",
                "",
                "",
            ],
            tz,
        )

        self.assertExcelRow(
            sheet_runs,
            5,
            [
                contact1_run2.contact.uuid,
                "+250788382382",
                "Eric",
                True,
                contact1_run2.created_on,
                contact1_run2.modified_on,
                "",
                contact1_run2.uuid,
                "Blue",
                "blue",
                " blue ",
                "",
                "",
                "",
                "",
                "",
                "",
            ],
            tz,
        )

        # check messages sheet...
        self.assertEqual(len(list(sheet_msgs.rows)), 14)  # header + 13 messages
        self.assertEqual(len(list(sheet_msgs.columns)), 8)

        self.assertExcelRow(
            sheet_msgs, 0, ["Contact UUID", "URN", "Name", "Date", "Direction", "Message", "Attachments", "Channel"]
        )

        contact1_out1 = contact1_run1.get_messages().get(text="What is your favorite color?")
        contact1_out2 = contact1_run1.get_messages().get(text="I don't know that color. Try again.")
        contact1_out3 = contact1_run1.get_messages().get(
            text__startswith="Good choice, I like Red too! What is your favorite beer?"
        )
        contact3_out1 = contact3_run1.get_messages().get(text="What is your favorite color?")

        self.assertExcelRow(
            sheet_msgs,
            1,
            [
                self.contact3.uuid,
                "+250788123456",
                "Norbert",
                contact3_out1.created_on,
                "OUT",
                "What is your favorite color?",
                "",
                "Test Channel",
            ],
            tz,
        )
        self.assertExcelRow(
            sheet_msgs,
            2,
            [
                contact1_out1.contact.uuid,
                "+250788382382",
                "Eric",
                contact1_out1.created_on,
                "OUT",
                "What is your favorite color?",
                "",
                "Test Channel",
            ],
            tz,
        )
        self.assertExcelRow(
            sheet_msgs,
            3,
            [
                contact1_in1.contact.uuid,
                "+250788382382",
                "Eric",
                matchers.Datetime(),
                "IN",
                "light beige",
                "",
                "Test Channel",
            ],
        )
        self.assertExcelRow(
            sheet_msgs,
            4,
            [
                contact1_out2.contact.uuid,
                "+250788382382",
                "Eric",
                contact1_out2.created_on,
                "OUT",
                "I don't know that color. Try again.",
                "",
                "Test Channel",
            ],
            tz,
        )
        self.assertExcelRow(
            sheet_msgs,
            5,
            [contact1_in2.contact.uuid, "+250788382382", "Eric", matchers.Datetime(), "IN", "red", "", "Test Channel"],
        )
        self.assertExcelRow(
            sheet_msgs,
            6,
            [
                contact1_out3.contact.uuid,
                "+250788382382",
                "Eric",
                contact1_out3.created_on,
                "OUT",
                "Good choice, I like Red too! What is your favorite beer?",
                "",
                "Test Channel",
            ],
            tz,
        )

    def test_remove_control_characters(self):
        flow = self.get_flow("color_v13")
        flow_nodes = flow.get_definition()["nodes"]
        color_prompt = flow_nodes[0]
        color_split = flow_nodes[4]
        color_other = flow_nodes[3]

        msg_in = self.create_incoming_msg(self.contact, "ngert\x07in.")

        run1 = (
            MockSessionWriter(self.contact, flow)
            .visit(color_prompt)
            .send_msg("What is your favorite color?", self.channel)
            .visit(color_split)
            .wait()
            .resume(msg=msg_in)
            .set_result("Color", "ngert\x07in.", "Other", "ngert\x07in.")
            .visit(color_other)
            .send_msg("That is a funny color. Try again.", self.channel)
            .visit(color_split)
            .wait()
            .save()
        ).session.runs.get()

        workbook = self._export(flow)
        tz = self.org.timezone
        sheet_runs, sheet_msgs = workbook.worksheets

        self.assertExcelRow(
            sheet_runs,
            1,
            [
                run1.contact.uuid,
                "+250788382382",
                "Eric",
                run1.created_on,
                run1.modified_on,
                "",
                run1.uuid,
                "Other",
                "ngertin.",
                "ngertin.",
            ],
            tz,
        )

    def test_from_archives(self):
        flow = self.get_flow("color_v13")
        flow_nodes = flow.get_definition()["nodes"]
        color_prompt = flow_nodes[0]
        color_split = flow_nodes[4]
        color_other = flow_nodes[3]
        blue_reply = flow_nodes[2]

        contact1_in1 = self.create_incoming_msg(self.contact, "green")
        contact1_run = (
            MockSessionWriter(self.contact, flow)
            .visit(color_prompt)
            .send_msg("What is your favorite color?", self.channel)
            .visit(color_split)
            .wait()
            .resume(msg=contact1_in1)
            .set_result("Color", "green", "Other", "green")
            .visit(color_other)
            .send_msg("That is a funny color. Try again.", self.channel)
            .visit(color_split)
            .wait()
            .save()
        ).session.runs.get()

        contact2_in1 = self.create_incoming_msg(self.contact2, "blue")
        contact2_run = (
            MockSessionWriter(self.contact2, flow)
            .visit(color_prompt)
            .send_msg("What is your favorite color?", self.channel)
            .visit(color_split)
            .wait()
            .resume(msg=contact2_in1)
            .set_result("Color", "blue", "Blue", "blue")
            .visit(blue_reply)
            .send_msg("Blue is sad :(.", self.channel)
            .complete()
            .save()
        ).session.runs.get()

        # and a run for a different flow
        flow2 = self.get_flow("favorites_v13")
        flow2_nodes = flow2.get_definition()["nodes"]

        contact2_other_flow = (
            MockSessionWriter(self.contact2, flow2)
            .visit(flow2_nodes[0])
            .send_msg("Color???", self.channel)
            .visit(flow2_nodes[2])
            .wait()
            .save()
        ).session.runs.get()

        contact3_run = (
            MockSessionWriter(self.contact3, flow)
            .visit(color_prompt)
            .send_msg("What is your favorite color?", self.channel)
            .visit(color_split)
            .wait()
            .save()
        ).session.runs.get()

        # we now have 4 runs in this order of modified_on
        contact1_run.refresh_from_db()
        contact2_run.refresh_from_db()
        contact2_other_flow.refresh_from_db()
        contact3_run.refresh_from_db()

        # archive the first 3 runs
        Archive.objects.create(
            org=self.org,
            archive_type=Archive.TYPE_FLOWRUN,
            size=10,
            hash=uuid4().hex,
            url="http://test-bucket.aws.com/archive1.jsonl.gz",
            record_count=3,
            start_date=timezone.now().date(),
            period="D",
            build_time=23425,
        )

        # prepare 'old' archive format that used a list of values
        old_archive_format = contact2_run.as_archive_json()
        old_archive_format["values"] = [old_archive_format["values"]]

        mock_s3 = MockS3Client()
        body, md5, size = jsonlgz_encode(
            [contact1_run.as_archive_json(), old_archive_format, contact2_other_flow.as_archive_json()]
        )
        mock_s3.put_object("test-bucket", "archive1.jsonl.gz", body)

        contact1_run.release()
        contact2_run.release()

        # create an archive earlier than our flow created date so we check that it isn't included
        Archive.objects.create(
            org=self.org,
            archive_type=Archive.TYPE_FLOWRUN,
            size=10,
            hash=uuid4().hex,
            url="http://test-bucket.aws.com/archive2.jsonl.gz",
            record_count=1,
            start_date=timezone.now().date() - timedelta(days=2),
            period="D",
            build_time=5678,
        )
        body, md5, size = jsonlgz_encode([contact2_run.as_archive_json()])
        mock_s3.put_object("test-bucket", "archive2.jsonl.gz", body)

        with patch("temba.utils.s3.client", return_value=mock_s3):
            workbook = self._export(flow)

        tz = self.org.timezone
        sheet_runs, sheet_msgs = workbook.worksheets

        # check runs sheet...
        self.assertEqual(4, len(list(sheet_runs.rows)))  # header + 3 runs

        self.assertExcelRow(
            sheet_runs,
            1,
            [
                contact1_run.contact.uuid,
                "+250788382382",
                "Eric",
                contact1_run.created_on,
                contact1_run.modified_on,
                "",
                contact1_run.uuid,
                "Other",
                "green",
                "green",
            ],
            tz,
        )
        self.assertExcelRow(
            sheet_runs,
            2,
            [
                contact2_run.contact.uuid,
                "+250788383383",
                "Nic",
                contact2_run.created_on,
                contact2_run.modified_on,
                contact2_run.exited_on,
                contact2_run.uuid,
                "Blue",
                "blue",
                "blue",
            ],
            tz,
        )
        self.assertExcelRow(
            sheet_runs,
            3,
            [
                contact3_run.contact.uuid,
                "+250788123456",
                "Norbert",
                contact3_run.created_on,
                contact3_run.modified_on,
                "",
                contact3_run.uuid,
                "",
                "",
                "",
            ],
            tz,
        )

    def test_surveyor_msgs(self):
        flow = self.get_flow("color_v13")
        flow.flow_type = Flow.TYPE_SURVEY
        flow.save()

        flow_nodes = flow.get_definition()["nodes"]
        color_prompt = flow_nodes[0]
        color_split = flow_nodes[4]

        # no urn or channel
        in1 = self.create_incoming_msg(self.contact, "blue", surveyor=True)

        run = (
            MockSessionWriter(self.contact, flow)
            .visit(color_prompt)
            .send_msg("What is your favorite color?", self.channel)
            .visit(color_split)
            .wait()
            .resume(msg=in1)
            .set_result("Color", "blue", "Blue", "blue")
            .send_msg("That is a funny color. Try again.", self.channel)
            .complete()
            .save()
        ).session.runs.get()

        workbook = self._export(flow)
        tz = self.org.timezone

        sheet_runs, sheet_msgs = workbook.worksheets

        run.refresh_from_db()

        # no submitter for our run
        self.assertExcelRow(
            sheet_runs,
            1,
            [
                "",
                run.contact.uuid,
                "+250788382382",
                "Eric",
                run.created_on,
                run.modified_on,
                run.exited_on,
                run.uuid,
                "Blue",
                "blue",
                "blue",
            ],
            tz,
        )

        out1 = run.get_messages().get(text="What is your favorite color?")

        self.assertExcelRow(
            sheet_msgs,
            1,
            [
                run.contact.uuid,
                "+250788382382",
                "Eric",
                out1.created_on,
                "OUT",
                "What is your favorite color?",
                "",
                "Test Channel",
            ],
            tz,
        )

        # no channel or phone
        self.assertExcelRow(sheet_msgs, 2, [run.contact.uuid, "", "Eric", matchers.Datetime(), "IN", "blue", ""])

        # now try setting a submitted by on our run
        run.submitted_by = self.admin
        run.save(update_fields=("submitted_by",))

        workbook = self._export(flow)
        tz = self.org.timezone

        sheet_runs, sheet_msgs = workbook.worksheets

        # now the Administrator should show up
        self.assertExcelRow(
            sheet_runs,
            1,
            [
                "Administrator",
                run.contact.uuid,
                "+250788382382",
                "Eric",
                run.created_on,
                run.modified_on,
                run.exited_on,
                run.uuid,
                "Blue",
                "blue",
                "blue",
            ],
            tz,
        )

    def test_no_responses(self):
        flow = self.get_flow("color_v13")

        self.assertEqual(flow.get_run_stats()["total"], 0)

        workbook = self._export(flow, has_results=False)

        self.assertEqual(len(workbook.worksheets), 1)

        # every sheet has only the head row
        self.assertEqual(len(list(workbook.worksheets[0].rows)), 1)
        self.assertEqual(len(list(workbook.worksheets[0].columns)), 10)


class FlowLabelTest(TembaTest):
    def test_label_model(self):
        # test a the creation of a unique label when we have a long word(more than 32 caracters)
        response = FlowLabel.create(self.org, "alongwordcomposedofmorethanthirtytwoletters", parent=None)
        self.assertEqual(response.name, "alongwordcomposedofmorethanthirt")

        # try to create another label which starts with the same 32 caracteres
        # the one we already have
        label = FlowLabel.create(self.org, "alongwordcomposedofmorethanthirtytwocaracteres", parent=None)

        self.assertEqual(label.name, "alongwordcomposedofmorethanthi 2")
        self.assertEqual(str(label), "alongwordcomposedofmorethanthi 2")
        label = FlowLabel.create(self.org, "child", parent=label)
        self.assertEqual(str(label), "alongwordcomposedofmorethanthi 2 > child")

        FlowLabel.create(self.org, "dog")
        FlowLabel.create(self.org, "dog")
        dog3 = FlowLabel.create(self.org, "dog")
        self.assertEqual("dog 3", dog3.name)

        dog4 = FlowLabel.create(self.org, "dog ")
        self.assertEqual("dog 4", dog4.name)

        # view the parent label, should see the child
        self.login(self.admin)
        favorites = self.get_flow("favorites")
        label.toggle_label([favorites], True)
        response = self.client.get(reverse("flows.flow_filter", args=[label.pk]))
        self.assertEqual([favorites], list(response.context["object_list"]))
        # our child label
        self.assertContains(response, "child")

        # and the edit gear link
        self.assertContains(response, "Edit")

        favorites.is_active = False
        favorites.save()

        response = self.client.get(reverse("flows.flow_filter", args=[label.pk]))
        self.assertFalse(response.context["object_list"])

        # try to view our cat label in our other org
        cat = FlowLabel.create(self.org2, "cat")
        response = self.client.get(reverse("flows.flow_filter", args=[cat.pk]))
        self.assertLoginRedirect(response)

    def test_toggle_label(self):
        label = FlowLabel.create(self.org, "toggle me")
        flow = self.get_flow("favorites")

        changed = label.toggle_label([flow], True)
        self.assertEqual(1, len(changed))
        self.assertEqual(label.pk, flow.labels.all().first().pk)

        changed = label.toggle_label([flow], False)
        self.assertEqual(1, len(changed))
        self.assertIsNone(flow.labels.all().first())

    def test_create(self):
        create_url = reverse("flows.flowlabel_create")

        post_data = dict(name="label_one")

        self.login(self.admin)
        response = self.client.post(create_url, post_data, follow=True)
        self.assertEqual(FlowLabel.objects.all().count(), 1)
        self.assertEqual(FlowLabel.objects.all()[0].parent, None)

        label_one = FlowLabel.objects.all()[0]
        post_data = dict(name="sub_label", parent=label_one.pk)
        response = self.client.post(create_url, post_data, follow=True)

        self.assertEqual(FlowLabel.objects.all().count(), 2)
        self.assertEqual(FlowLabel.objects.filter(parent=None).count(), 1)

        post_data = dict(name="sub_label ", parent=label_one.pk)
        response = self.client.post(create_url, post_data, follow=True)
        self.assertIn("form", response.context)
        self.assertTrue(response.context["form"].errors)
        self.assertEqual("Name already used", response.context["form"].errors["name"][0])

        self.assertEqual(FlowLabel.objects.all().count(), 2)
        self.assertEqual(FlowLabel.objects.filter(parent=None).count(), 1)

        post_data = dict(name="label from modal")
        response = self.client.post("%s?format=modal" % create_url, post_data, follow=True)
        self.assertEqual(FlowLabel.objects.all().count(), 3)

    def test_delete(self):
        label_one = FlowLabel.create(self.org, "label1")

        delete_url = reverse("flows.flowlabel_delete", args=[label_one.pk])

        self.other_user = self.create_user("ironman")

        self.login(self.other_user)
        response = self.client.get(delete_url)
        self.assertEqual(response.status_code, 302)

        self.login(self.admin)
        response = self.client.post(delete_url)
        self.assertEqual(response.status_code, 200)
        self.assertIsNone(FlowLabel.objects.filter(uuid=label_one.uuid).first())

    def test_update(self):
        label_one = FlowLabel.create(self.org, "label1")
        update_url = reverse("flows.flowlabel_update", args=[label_one.pk])

        # not logged in, no dice
        response = self.client.get(update_url)
        self.assertLoginRedirect(response)

        # login
        self.login(self.admin)
        response = self.client.get(update_url)

        # change our name
        data = response.context["form"].initial
        data["name"] = "Label One"
        data["parent"] = ""
        self.client.post(update_url, data)

        label_one.refresh_from_db()
        self.assertEqual(label_one.name, "Label One")


class SimulationTest(TembaTest):
    def add_message(self, payload, text):
        """
        Add a message to the payload for the flow server using the default contact
        """
        payload["resume"] = {
            "type": "msg",
            "resumed_on": timezone.now().isoformat(),
            "msg": {"text": text, "uuid": str(uuid4()), "urn": "tel:+12065551212"},
        }

    def get_replies(self, response):
        """
        Gets any replies in a response from the flow server as a list of strings
        """
        replies = []
        for event in response.get("events", []):
            if event["type"] == "broadcast_created":
                replies.append(event["text"])
            elif event["type"] == "msg_created":
                replies.append(event["msg"]["text"])
        return replies

    def test_simulation_ivr(self):
        self.login(self.admin)
        flow = self.get_flow("ivr")

        # create our payload
        payload = {"version": 2, "trigger": {}, "flow": {}}
        url = reverse("flows.flow_simulate", args=[flow.id])

        with override_settings(MAILROOM_AUTH_TOKEN="sesame", MAILROOM_URL="https://mailroom.temba.io"):
            with patch("requests.post") as mock_post:
                mock_post.return_value = MockResponse(200, '{"session": {}}')
                response = self.client.post(url, payload, content_type="application/json")

                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.json(), {"session": {}})

                # since this is an IVR flow, the session trigger will have a connection
                self.assertEqual(
                    {
                        "connection": {
                            "channel": {"uuid": "440099cf-200c-4d45-a8e7-4a564f4a0e8b", "name": "Test Channel"},
                            "urn": "tel:+12065551212",
                        },
                        "environment": {
                            "date_format": "DD-MM-YYYY",
                            "time_format": "tt:mm",
                            "timezone": "Africa/Kigali",
                            "default_language": None,
                            "allowed_languages": [],
                            "default_country": "RW",
                            "redaction_policy": "none",
                        },
                        "user": {"email": "Administrator@nyaruka.com", "name": ""},
                    },
                    json.loads(mock_post.call_args[1]["data"])["trigger"],
                )

    def test_simulation(self):
        self.login(self.admin)
        flow = self.get_flow("favorites")

        # create our payload
        payload = dict(version=2, trigger={}, flow={})

        url = reverse("flows.flow_simulate", args=[flow.pk])

        with override_settings(MAILROOM_AUTH_TOKEN="sesame", MAILROOM_URL="https://mailroom.temba.io"):
            with patch("requests.post") as mock_post:
                mock_post.return_value = MockResponse(400, '{"session": {}}')
                response = self.client.post(url, json.dumps(payload), content_type="application/json")
                self.assertEqual(500, response.status_code)

            # start a flow
            with patch("requests.post") as mock_post:
                mock_post.return_value = MockResponse(200, '{"session": {}}')
                response = self.client.post(url, json.dumps(payload), content_type="application/json")
                self.assertEqual(200, response.status_code)
                self.assertEqual({}, response.json()["session"])

                actual_url = mock_post.call_args_list[0][0][0]
                actual_payload = json.loads(mock_post.call_args_list[0][1]["data"])
                actual_headers = mock_post.call_args_list[0][1]["headers"]

                self.assertEqual(actual_url, "https://mailroom.temba.io/mr/sim/start")
                self.assertEqual(actual_payload["org_id"], flow.org_id)
                self.assertEqual(actual_payload["trigger"]["environment"]["date_format"], "DD-MM-YYYY")
                self.assertEqual(len(actual_payload["assets"]["channels"]), 1)  # fake channel
                self.assertEqual(len(actual_payload["flows"]), 1)
                self.assertEqual(actual_headers["Authorization"], "Token sesame")
                self.assertEqual(actual_headers["Content-Type"], "application/json")

            # try a resume
            payload = {
                "version": 2,
                "session": {"contact": {"fields": {"age": decimal.Decimal("39")}}},
                "resume": {},
                "flow": {},
            }

            with patch("requests.post") as mock_post:
                mock_post.return_value = MockResponse(400, '{"session": {}}')
                response = self.client.post(url, json.dumps(payload), content_type="application/json")
                self.assertEqual(500, response.status_code)

            with patch("requests.post") as mock_post:
                mock_post.return_value = MockResponse(200, '{"session": {}}')
                response = self.client.post(url, json.dumps(payload), content_type="application/json")
                self.assertEqual(200, response.status_code)
                self.assertEqual({}, response.json()["session"])

                actual_url = mock_post.call_args_list[0][0][0]
                actual_payload = json.loads(mock_post.call_args_list[0][1]["data"])
                actual_headers = mock_post.call_args_list[0][1]["headers"]

                self.assertEqual(actual_url, "https://mailroom.temba.io/mr/sim/resume")
                self.assertEqual(actual_payload["org_id"], flow.org_id)
                self.assertEqual(actual_payload["resume"]["environment"]["date_format"], "DD-MM-YYYY")
                self.assertEqual(len(actual_payload["assets"]["channels"]), 1)  # fake channel
                self.assertEqual(len(actual_payload["flows"]), 1)
                self.assertEqual(actual_headers["Authorization"], "Token sesame")
                self.assertEqual(actual_headers["Content-Type"], "application/json")


class FlowSessionCRUDLTest(TembaTest):
    def test_session_json(self):
        contact = self.create_contact("Bob", phone="+1234567890")
        flow = self.get_flow("color_v13")

        session = MockSessionWriter(contact, flow).wait().save().session

        # normal users can't see session json
        url = reverse("flows.flowsession_json", args=[session.uuid])
        response = self.client.get(url)
        self.assertLoginRedirect(response)

        self.login(self.admin)
        response = self.client.get(url)
        self.assertLoginRedirect(response)

        # but logged in as a CS rep we can
        self.login(self.customer_support)

        response = self.client.get(url)
        self.assertEqual(200, response.status_code)

        response_json = json.loads(response.content)
        self.assertEqual("Temba", response_json["_metadata"]["org"])
        self.assertEqual(session.uuid, response_json["uuid"])

        # now try with an s3 session
        mock_s3 = MockS3Client()
        mock_s3.objects[("temba-sessions", "c/session.json")] = io.StringIO(json.dumps(session.output))

        FlowSession.objects.filter(id=session.id).update(
            output_url="https://temba-sessions.s3.aws.amazon.com/c/session.json",
            output=None,
        )

        # fetch our contact history
        with patch("temba.utils.s3.s3.client", return_value=mock_s3):
            response = self.client.get(url)
            self.assertEqual(200, response.status_code)
            self.assertEqual("Temba", response_json["_metadata"]["org"])
            self.assertEqual(session.uuid, response_json["uuid"])


class FlowStartCRUDLTest(TembaTest, CRUDLTestMixin):
    def test_list(self):
        list_url = reverse("flows.flowstart_list")

        flow = self.get_flow("color_v13")
        contact = self.create_contact("Bob", phone="+1234567890")
        group = self.create_group("Testers", contacts=[contact])
        start1 = FlowStart.create(flow, self.admin, contacts=[contact])
        start2 = FlowStart.create(flow, self.admin, query="name ~ Bob", restart_participants=False, start_type="A")
        start3 = FlowStart.create(flow, self.admin, groups=[group], include_active=False, start_type="Z")

        FlowStartCount.objects.create(start=start3, count=1000)
        FlowStartCount.objects.create(start=start3, count=234)

        other_org_flow = self.create_flow(org=self.org2)
        FlowStart.create(other_org_flow, self.admin2)

        response = self.assertListFetch(
            list_url, allow_viewers=True, allow_editors=True, context_objects=[start3, start2, start1]
        )

        self.assertContains(response, "was started by Administrator for")
        self.assertContains(response, "was started by an API call for")
        self.assertContains(response, "was started by Zapier for")
        self.assertContains(response, "all contacts")
        self.assertContains(response, "contacts who haven't already been through this flow")
        self.assertContains(response, "<b>1,234</b> runs")

        response = self.assertListFetch(
            list_url + "?type=manual", allow_viewers=True, allow_editors=True, context_objects=[start1]
        )
        self.assertTrue(response.context["filtered"])
        self.assertEqual(response.context["url_params"], "?type=manual&")


class AssetServerTest(TembaTest):
    def test_environment(self):
        self.login(self.admin)

        date_formats = {"D": "DD-MM-YYYY", "M": "MM-DD-YYYY", "Y": "YYYY-MM-DD"}

        for org_date_format, date_format in date_formats.items():
            self.org.date_format = org_date_format
            self.org.save()

            response = self.client.get("/flow/assets/%d/1234/environment/" % self.org.id)
            self.assertEqual(
                response.json(),
                {
                    "date_format": date_format,
                    "time_format": "tt:mm",
                    "timezone": "Africa/Kigali",
                    "default_language": None,
                    "allowed_languages": [],
                    "default_country": "RW",
                    "redaction_policy": "none",
                },
            )

    def test_languages(self):
        self.login(self.admin)
        self.org.set_flow_languages(self.admin, ["eng", "spa"])
        response = self.client.get("/flow/assets/%d/1234/language/" % self.org.id)
        self.assertEqual(
            response.json(), {"results": [{"iso": "eng", "name": "English"}, {"iso": "spa", "name": "Spanish"}]}
        )


class SystemChecksTest(TembaTest):
    def test_mailroom_url(self):
        with override_settings(MAILROOM_URL="http://mailroom.io"):
            self.assertEqual(len(mailroom_url(None)), 0)

        with override_settings(MAILROOM_URL=None):
            self.assertEqual(mailroom_url(None)[0].msg, "No mailroom URL set, simulation will not be available")


class FlowRevisionTest(TembaTest):
    def test_trim_revisions(self):
        start = timezone.now()

        color = self.get_flow("color")
        clinic = self.get_flow("the_clinic")

        revision = 100
        FlowRevision.objects.all().update(revision=revision)

        # create a single old clinic revision
        FlowRevision.objects.create(
            flow=clinic,
            definition=dict(),
            revision=99,
            created_on=timezone.now() - timedelta(days=7),
            modified_on=timezone.now(),
            created_by=self.admin,
            modified_by=self.admin,
        )

        # make a bunch of revisions for color on the same day
        created = timezone.now().replace(hour=6) - timedelta(days=1)
        for i in range(25):
            revision -= 1
            created = created - timedelta(minutes=1)
            FlowRevision.objects.create(
                flow=color,
                definition=dict(),
                revision=revision,
                created_by=self.admin,
                modified_by=self.admin,
                created_on=created,
                modified_on=created,
            )

        # then for 5 days prior, make a few more
        for i in range(5):
            created = created - timedelta(days=1)
            for i in range(10):
                revision -= 1
                created = created - timedelta(minutes=1)
                FlowRevision.objects.create(
                    flow=color,
                    definition=dict(),
                    revision=revision,
                    created_by=self.admin,
                    modified_by=self.admin,
                    created_on=created,
                    modified_on=created,
                )

        # trim our flow revisions, should be left with original (today), 25 from yesterday, 1 per day for 5 days = 31
        self.assertEqual(76, FlowRevision.objects.filter(flow=color).count())
        self.assertEqual(45, FlowRevision.trim(start))
        self.assertEqual(31, FlowRevision.objects.filter(flow=color).count())
        self.assertEqual(
            7,
            FlowRevision.objects.filter(flow=color)
            .annotate(created_date=TruncDate("created_on"))
            .distinct("created_date")
            .count(),
        )

        # trim our clinic flow manually, should remain unchanged
        self.assertEqual(2, FlowRevision.objects.filter(flow=clinic).count())
        self.assertEqual(0, FlowRevision.trim_for_flow(clinic.id))
        self.assertEqual(2, FlowRevision.objects.filter(flow=clinic).count())

        # call our task
        trim_flow_revisions()
        self.assertEqual(2, FlowRevision.objects.filter(flow=clinic).count())
        self.assertEqual(31, FlowRevision.objects.filter(flow=color).count())

        # call again (testing reading redis key)
        trim_flow_revisions()
        self.assertEqual(2, FlowRevision.objects.filter(flow=clinic).count())
        self.assertEqual(31, FlowRevision.objects.filter(flow=color).count())
