import datetime
import os
import re
import time
from datetime import timedelta
from unittest.mock import PropertyMock, patch
from uuid import uuid4

import iso8601
import pytz
from openpyxl import load_workbook

from django.conf import settings
from django.contrib.auth.models import Group
from django.test.utils import override_settings
from django.urls import reverse
from django.utils import timezone
from django.utils.encoding import force_text

from temba.archives.models import Archive
from temba.campaigns.models import Campaign, CampaignEvent, EventFire
from temba.channels.models import Channel
from temba.contacts.models import TEL_SCHEME, WHATSAPP_SCHEME, Contact, ContactField, ContactGroup
from temba.ivr.models import IVRCall
from temba.mailroom import FlowValidationException
from temba.msgs.models import INCOMING, OUTGOING, WIRED, Broadcast, Label, Msg
from temba.orgs.models import Language
from temba.templates.models import Template, TemplateTranslation
from temba.tests import (
    FlowFileTest,
    MigrationTest,
    MockResponse,
    TembaTest,
    matchers,
    skip_if_no_mailroom,
    uses_legacy_engine,
)
from temba.tests.s3 import MockS3Client
from temba.triggers.models import Trigger
from temba.utils import json
from temba.values.constants import Value

from . import legacy
from .checks import mailroom_url
from .models import (
    ActionSet,
    ExportFlowResultsTask,
    Flow,
    FlowCategoryCount,
    FlowException,
    FlowInvalidCycleException,
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
    RuleSet,
    get_flow_user,
)
from .tasks import squash_flowpathcounts, squash_flowruncounts, trim_flow_sessions, update_run_expirations_task
from .views import FlowCRUDL


class FlowTest(TembaTest):
    def setUp(self):
        super().setUp()

        self.contact = self.create_contact("Eric", "+250788382382")
        self.contact2 = self.create_contact("Nic", "+250788383383")
        self.contact3 = self.create_contact("Norbert", "+250788123456")
        self.contact4 = self.create_contact("Teeh", "+250788123457", language="por")

        self.flow = self.get_flow("color")

        self.other_group = self.create_group("Other", [])

    def export_flow_results(
        self, flow, responded_only=False, include_msgs=True, contact_fields=None, extra_urns=(), group_memberships=None
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

        response = self.client.post(reverse("flows.flow_export_results"), form)
        self.assertEqual(response.status_code, 302)

        task = ExportFlowResultsTask.objects.order_by("-id").first()
        self.assertIsNotNone(task)

        filename = "%s/test_orgs/%d/results_exports/%s.xlsx" % (settings.MEDIA_ROOT, self.org.pk, task.uuid)
        return load_workbook(filename=os.path.join(settings.MEDIA_ROOT, filename))

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

        self.create_secondary_org()
        self.assertEqual(Flow.get_unique_name(self.org2, "Sheep Poll"), "Sheep Poll")  # different org

    @patch("temba.mailroom.queue_interrupt")
    def test_archive(self, mock_queue_interrupt):
        self.flow.archive()

        mock_queue_interrupt.assert_called_once_with(self.org, flow=self.flow)

        self.flow.refresh_from_db()
        self.assertEqual(self.flow.is_archived, True)
        self.assertEqual(self.flow.is_active, True)

    @patch("temba.mailroom.queue_interrupt")
    def test_release(self, mock_queue_interrupt):
        self.flow.release()

        mock_queue_interrupt.assert_called_once_with(self.org, flow=self.flow)

        self.flow.refresh_from_db()
        self.assertEqual(self.flow.is_archived, False)
        self.assertEqual(self.flow.is_active, False)

    @patch("temba.flows.views.uuid4")
    def test_upload_media_action(self, mock_uuid):
        upload_media_action_url = reverse("flows.flow_upload_media_action", args=[self.flow.uuid])

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

        mock_uuid.side_effect = ["11111-111-11", "22222-222-22", "33333-333-33"]

        assert_media_upload(
            "%s/test_media/steve.marten.jpg" % settings.MEDIA_ROOT,
            "image/jpeg",
            "%s/attachments/%d/%d/steps/%s%s"
            % (settings.STORAGE_URL, self.flow.org.pk, self.flow.pk, "11111-111-11", ".jpg"),
        )
        assert_media_upload(
            "%s/test_media/snow.mp4" % settings.MEDIA_ROOT,
            "video/mp4",
            "%s/attachments/%d/%d/steps/%s%s"
            % (settings.STORAGE_URL, self.flow.org.pk, self.flow.pk, "22222-222-22", ".mp4"),
        )
        assert_media_upload(
            "%s/test_media/snow.m4a" % settings.MEDIA_ROOT,
            "audio/mp4",
            "%s/attachments/%d/%d/steps/%s%s"
            % (settings.STORAGE_URL, self.flow.org.pk, self.flow.pk, "33333-333-33", ".m4a"),
        )

    def test_flow_get_definition(self):
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

    def test_revision_history(self):
        # we should initially have one revision
        revision = self.flow.revisions.get()
        self.assertEqual(revision.revision, 1)
        self.assertEqual(revision.created_by, self.flow.created_by)

        flow_json = self.flow.as_json()

        # create a new update
        self.flow.update(flow_json, user=self.admin)
        revisions = self.flow.revisions.all().order_by("created_on")

        # now we should have two revisions
        self.assertEqual(2, revisions.count())
        self.assertEqual(1, revisions[0].revision)
        self.assertEqual(2, revisions[1].revision)

        self.assertEqual(revisions[0].spec_version, Flow.FINAL_LEGACY_VERSION)
        self.assertEqual(revisions[0].as_json()["version"], Flow.FINAL_LEGACY_VERSION)
        self.assertEqual(revisions[0].get_definition_json()["base_language"], "base")

        # now make one revision invalid
        revision = revisions[1]
        definition = revision.get_definition_json()
        del definition["base_language"]
        revision.definition = definition
        revision.save()

        # should be back to one valid flow
        self.login(self.admin)
        response = self.client.get(reverse("flows.flow_revisions", args=[self.flow.uuid]))
        self.assertEqual(1, len(response.json()))

        # fetch that revision
        revision_id = response.json()["results"][0]["id"]
        response = self.client.get(
            "%s%s/?version=%s"
            % (reverse("flows.flow_revisions", args=[self.flow.uuid]), revision_id, Flow.FINAL_LEGACY_VERSION)
        )

        # make sure we can read the definition
        definition = response.json()
        self.assertEqual("base", definition["base_language"])

        # make the last revision even more invalid (missing ruleset)
        revision = revisions[0]
        definition = revision.get_definition_json()
        del definition["rule_sets"]
        revision.definition = definition
        revision.save()

        # no valid revisions (but we didn't throw!)
        response = self.client.get(reverse("flows.flow_revisions", args=[self.flow.uuid]))
        self.assertEqual(0, len(response.json()["results"]))

    @skip_if_no_mailroom
    def test_goflow_revisions(self):
        self.login(self.admin)
        self.client.post(
            reverse("flows.flow_create"), data=dict(name="Go Flow", flow_type=Flow.TYPE_MESSAGE, editor_version="0")
        )
        flow = Flow.objects.get(
            org=self.org, name="Go Flow", flow_type=Flow.TYPE_MESSAGE, version_number=Flow.GOFLOW_VERSION
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

    def test_revision_history_of_inactive_flow(self):
        self.flow.release()

        self.login(self.admin)
        response = self.client.get(reverse("flows.flow_revisions", args=[self.flow.uuid]))

        self.assertEqual(response.status_code, 404)

    def test_flow_activity_of_inactive_flow(self):
        self.flow.release()

        self.login(self.admin)
        response = self.client.get(reverse("flows.flow_activity", args=[self.flow.uuid]))

        self.assertEqual(response.status_code, 404)

    def test_flow_json_of_inactive_flow(self):
        self.flow.release()

        self.login(self.admin)
        response = self.client.get(reverse("flows.flow_json", args=[self.flow.uuid]))

        self.assertEqual(response.status_code, 404)

    def test_flow_lists(self):
        self.login(self.admin)

        # add another flow
        flow2 = self.get_flow("no_ruleset_flow")

        # and archive it right off the bat
        flow2.is_archived = True
        flow2.save()

        flow3 = Flow.create(self.org, self.admin, "Flow 3", base_language="base")

        # see our trigger on the list page
        response = self.client.get(reverse("flows.flow_list"))
        self.assertContains(response, self.flow.name)
        self.assertContains(response, flow3.name)
        self.assertEqual(2, response.context["folders"][0]["count"])
        self.assertEqual(1, response.context["folders"][1]["count"])

        # archive it
        post_data = dict(action="archive", objects=self.flow.pk)
        self.client.post(reverse("flows.flow_list"), post_data)
        response = self.client.get(reverse("flows.flow_list"))
        self.assertNotContains(response, self.flow.name)
        self.assertContains(response, flow3.name)
        self.assertEqual(1, response.context["folders"][0]["count"])
        self.assertEqual(2, response.context["folders"][1]["count"])

        response = self.client.get(reverse("flows.flow_archived"), post_data)
        self.assertContains(response, self.flow.name)

        # flow2 should appear before flow since it was created later
        self.assertTrue(flow2, response.context["object_list"][0])
        self.assertTrue(self.flow, response.context["object_list"][1])

        # unarchive it
        post_data = dict(action="restore", objects=self.flow.pk)
        self.client.post(reverse("flows.flow_archived"), post_data)
        response = self.client.get(reverse("flows.flow_archived"), post_data)
        self.assertNotContains(response, self.flow.name)
        response = self.client.get(reverse("flows.flow_list"), post_data)
        self.assertContains(response, self.flow.name)
        self.assertContains(response, flow3.name)
        self.assertEqual(2, response.context["folders"][0]["count"])
        self.assertEqual(1, response.context["folders"][1]["count"])

        # voice flows should be included in the count
        Flow.objects.filter(pk=self.flow.pk).update(flow_type=Flow.TYPE_VOICE)

        response = self.client.get(reverse("flows.flow_list"))
        self.assertContains(response, self.flow.name)
        self.assertEqual(2, response.context["folders"][0]["count"])
        self.assertEqual(1, response.context["folders"][1]["count"])

        # single message flow (flom campaign) should not be included in counts and not even on this list
        Flow.objects.filter(pk=self.flow.pk).update(is_system=True)

        response = self.client.get(reverse("flows.flow_list"))

        self.assertNotContains(response, self.flow.name)
        self.assertEqual(1, response.context["folders"][0]["count"])
        self.assertEqual(1, response.context["folders"][1]["count"])

        # single message flow should not be even in the archived list
        Flow.objects.filter(pk=self.flow.pk).update(is_system=True, is_archived=True)

        response = self.client.get(reverse("flows.flow_archived"))
        self.assertNotContains(response, self.flow.name)
        self.assertEqual(1, response.context["folders"][0]["count"])
        self.assertEqual(1, response.context["folders"][1]["count"])  # only flow2

    def test_flow_select2_response(self):
        self.login(self.admin)

        self.get_flow("no_ruleset_flow")

        url = f"{reverse('flows.flow_list')}?_format=select2&search="
        response = self.client.get(url, content_type="application/json")

        self.assertEqual(response.status_code, 200)

        json_payload = response.json()

        self.assertEqual(len(json_payload["results"]), 2)
        self.assertEqual([res["text"] for res in json_payload["results"]], ["No ruleset flow", "Color Flow"])

    def test_flow_select2_response_with_exclude_flow_uuid(self):
        self.login(self.admin)
        self.get_flow("no_ruleset_flow")

        # empty exclude_flow_uuid
        url = f"{reverse('flows.flow_list')}?_format=select2&search=&exclude_flow_uuid="
        response = self.client.get(url, content_type="application/json")

        self.assertEqual(response.status_code, 200)

        json_payload = response.json()

        self.assertEqual(len(json_payload["results"]), 2)
        self.assertEqual([res["text"] for res in json_payload["results"]], ["No ruleset flow", "Color Flow"])

        # valid flow uuid
        url = f"{reverse('flows.flow_list')}?_format=select2&search=&exclude_flow_uuid={self.flow.uuid}"
        response = self.client.get(url, content_type="application/json")

        self.assertEqual(response.status_code, 200)

        json_payload = response.json()

        self.assertEqual(len(json_payload["results"]), 1)
        self.assertEqual([res["text"] for res in json_payload["results"]], ["No ruleset flow"])

    def test_flow_import_labels(self):
        self.assertFalse(Label.label_objects.all())

        label = Label.get_or_create(self.org, self.admin, "Hello")
        self.login(self.admin)
        self.import_file("migrate_to_11_11")
        flow = Flow.objects.filter(name="Add Label").first()
        label_uuid_in_def = flow.revisions.first().definition["action_sets"][1]["actions"][0]["labels"][0]["uuid"]

        self.assertNotEqual("0bfecd01-9612-48ab-8c49-72170de6ee49", label_uuid_in_def)
        self.assertEqual(label.uuid, label_uuid_in_def)

    def test_campaign_filter(self):
        self.login(self.admin)
        self.get_flow("the_clinic")

        # should have a list of four flows for our appointment schedule
        response = self.client.get(reverse("flows.flow_list"))
        self.assertContains(response, "Appointment Schedule (4)")

        campaign = Campaign.objects.filter(name="Appointment Schedule").first()
        self.assertIsNotNone(campaign)

        # check that our four flows in the campaign are there
        response = self.client.get(reverse("flows.flow_campaign", args=[campaign.id]))
        self.assertContains(response, "Confirm Appointment")
        self.assertContains(response, "Start Notifications")
        self.assertContains(response, "Stop Notifications")
        self.assertContains(response, "Appointment Followup")

    @skip_if_no_mailroom
    def test_template_warnings(self):
        self.login(self.admin)
        flow = self.get_flow("whatsapp_template")

        # bring up broadcast dialog
        self.login(self.admin)
        response = self.client.get(reverse("flows.flow_broadcast", args=[flow.id]))

        # no warning, we don't have a whatsapp channel
        self.assertNotContains(response, "affirmation")

        # change our channel to use a whatsapp scheme
        self.channel.schemes = [WHATSAPP_SCHEME]
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
            self.channel, "affirmation", "eng", "good boy", 0, TemplateTranslation.STATUS_REJECTED, "id1"
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
        changed = Flow.apply_action_archive(self.admin, Flow.objects.filter(pk=flow.pk))
        self.assertFalse(changed)

        flow.refresh_from_db()
        self.assertFalse(flow.is_archived)

        campaign.is_archived = True
        campaign.save()

        # can archive if the campaign is archived
        changed = Flow.apply_action_archive(self.admin, Flow.objects.filter(pk=flow.pk))
        self.assertTrue(changed)
        self.assertEqual(changed, [flow.pk])

        flow.refresh_from_db()
        self.assertTrue(flow.is_archived)

        campaign.is_archived = False
        campaign.save()

        flow.is_archived = False
        flow.save()

        campaign_event.is_active = False
        campaign_event.save()

        # can archive if the campaign is not archived with no active event
        changed = Flow.apply_action_archive(self.admin, Flow.objects.filter(pk=flow.pk))
        self.assertTrue(changed)
        self.assertEqual(changed, [flow.pk])

        flow.refresh_from_db()
        self.assertTrue(flow.is_archived)

    def test_flows_select2(self):
        self.login(self.admin)

        msg = Flow.create(
            self.org,
            self.admin,
            Flow.get_unique_name(self.org, "Message Flow"),
            base_language="base",
            flow_type=Flow.TYPE_MESSAGE,
        )
        survey = Flow.create(
            self.org,
            self.admin,
            Flow.get_unique_name(self.org, "Surveyor Flow"),
            base_language="base",
            flow_type=Flow.TYPE_SURVEY,
        )
        ivr = Flow.create(
            self.org,
            self.admin,
            Flow.get_unique_name(self.org, "IVR Flow"),
            base_language="base",
            flow_type=Flow.TYPE_VOICE,
        )

        # all flow types
        response = self.client.get("%s?_format=select2" % reverse("flows.flow_list"))
        self.assertContains(response, ivr.name)
        self.assertContains(response, survey.name)
        self.assertContains(response, msg.name)

        # only surveyor flows
        response = self.client.get("%s?_format=select2&flow_type=S" % reverse("flows.flow_list"))
        self.assertContains(response, survey.name)
        self.assertNotContains(response, ivr.name)
        self.assertNotContains(response, msg.name)

        # only voice flows
        response = self.client.get("%s?_format=select2&flow_type=V" % reverse("flows.flow_list"))
        self.assertContains(response, ivr.name)
        self.assertNotContains(response, survey.name)
        self.assertNotContains(response, msg.name)

        # only text flows
        response = self.client.get("%s?_format=select2&flow_type=M" % reverse("flows.flow_list"))
        self.assertContains(response, msg.name)
        self.assertNotContains(response, survey.name)
        self.assertNotContains(response, ivr.name)

        # two at a time
        response = self.client.get("%s?_format=select2&flow_type=V&flow_type=M" % reverse("flows.flow_list"))
        self.assertContains(response, ivr.name)
        self.assertContains(response, msg.name)
        self.assertNotContains(response, survey.name)

    def test_flow_editor_next(self):
        self.login(self.admin)
        response = self.client.get(reverse("flows.flow_editor_next", args=[self.flow.uuid]))
        self.assertContains(response, "id='rp-flow-editor'")

        # customer service gets a service button
        csrep = self.create_user("csrep")
        csrep.groups.add(Group.objects.get(name="Customer Support"))
        csrep.is_staff = True
        csrep.save()

        self.login(csrep)
        response = self.client.get(reverse("flows.flow_editor_next", args=[self.flow.uuid]))
        gear_links = response.context["view"].get_gear_links()
        self.assertEqual(gear_links[-1]["title"], "Previous Editor")

        # convert our flow back to an old version
        response = self.client.get(f"{reverse('flows.flow_editor', args=[self.flow.uuid])}?legacy=true")
        gear_links = response.context["view"].get_gear_links()
        self.flow.refresh_from_db()
        self.assertEqual(self.flow.version_number, Flow.FINAL_LEGACY_VERSION)

        # viewing flows that are archived can't be started
        self.login(self.admin)
        self.flow.is_archived = True
        self.flow.save()

        response = self.client.get(reverse("flows.flow_editor_next", args=[self.flow.uuid]))
        self.assertFalse(response.context["mutable"])

    def test_flow_editor(self):
        self.login(self.admin)
        response = self.client.get(reverse("flows.flow_editor", args=[self.flow.uuid]))
        self.assertTrue(response.context["mutable"])
        self.assertFalse(response.context["has_airtime_service"])
        self.assertFalse(response.context["is_starting"])

        # superusers can't edit flows
        self.login(self.superuser)
        response = self.client.get(reverse("flows.flow_editor", args=[self.flow.uuid]))
        self.assertFalse(response.context["mutable"])

        # create a customer service user
        self.csrep = self.create_user("csrep")
        self.csrep.groups.add(Group.objects.get(name="Customer Support"))
        self.csrep.is_staff = True
        self.csrep.save()

        self.org.administrators.add(self.csrep)

        self.login(self.csrep)
        response = self.client.get(reverse("flows.flow_editor", args=[self.flow.uuid]))
        gear_links = response.context["view"].get_gear_links()
        self.assertEqual(gear_links[-1]["title"], "Service")
        self.assertEqual(
            gear_links[-1]["href"],
            f"/org/service/?organization={self.flow.org_id}&redirect_url=/flow/editor/{self.flow.uuid}/",
        )
        self.assertTrue(gear_links[-2]["divider"])

        self.assertListEqual(
            [link.get("title") for link in gear_links],
            [
                "Start Flow",
                "Results",
                None,
                "Edit",
                "Copy",
                "Export",
                None,
                "Revision History",
                "Delete",
                None,
                "New Editor",
                None,
                "Service",
            ],
        )

    def test_flow_editor_for_archived_flow(self):
        self.flow.archive()

        self.login(self.admin)
        response = self.client.get(reverse("flows.flow_editor", args=[self.flow.uuid]))

        gear_links = response.context["view"].get_gear_links()

        self.assertFalse(response.context["mutable"])
        self.assertFalse(response.context["can_start"])

        # cannot 'Edit' an archived Flow
        self.assertListEqual(
            [link.get("title") for link in gear_links],
            ["Results", "Copy", "Export", None, "Revision History", "Delete", None, "New Editor"],
        )

    def test_flow_editor_for_inactive_flow(self):
        self.flow.release()

        self.login(self.admin)
        response = self.client.get(reverse("flows.flow_editor", args=[self.flow.uuid]))

        self.assertEqual(response.status_code, 404)

    @uses_legacy_engine
    def test_states(self):
        # set our flow
        color_prompt = ActionSet.objects.get(x=1, y=1)
        color_ruleset = RuleSet.objects.get(label="color")
        orange_rule = color_ruleset.get_rules()[0]
        color_reply = ActionSet.objects.get(x=2, y=2)

        # how many people in the flow?
        self.assertEqual(
            self.flow.get_run_stats(),
            {"total": 0, "active": 0, "completed": 0, "expired": 0, "interrupted": 0, "completion": 0},
        )

        # start the flow
        legacy.flow_start(self.flow, [], [self.contact, self.contact2])

        # test our stats again
        self.assertEqual(
            self.flow.get_run_stats(),
            {"total": 2, "active": 2, "completed": 0, "expired": 0, "interrupted": 0, "completion": 0},
        )

        # each contact should have received a single message
        contact1_msg = self.contact.msgs.get()
        self.assertEqual(contact1_msg.text, "What is your favorite color?")
        self.assertEqual(contact1_msg.status, WIRED)
        self.assertFalse(contact1_msg.high_priority)

        # should have a flow run for each contact
        contact1_run = FlowRun.objects.get(contact=self.contact)
        contact2_run = FlowRun.objects.get(contact=self.contact2)

        self.assertEqual(contact1_run.flow, self.flow)
        self.assertEqual(contact1_run.contact, self.contact)
        self.assertFalse(contact1_run.responded)
        self.assertFalse(contact2_run.responded)

        # check the path for contact 1
        self.assertEqual(
            contact1_run.path,
            [
                {
                    "uuid": matchers.UUID4String(),
                    "node_uuid": str(color_prompt.uuid),
                    "arrived_on": matchers.ISODate(),
                    "exit_uuid": str(color_prompt.exit_uuid),
                },
                {
                    "uuid": matchers.UUID4String(),
                    "node_uuid": str(color_ruleset.uuid),
                    "arrived_on": matchers.ISODate(),
                },
            ],
        )
        self.assertEqual(
            contact1_run.events,
            [
                {
                    "type": "msg_created",
                    "created_on": contact1_msg.created_on.isoformat(),
                    "step_uuid": contact1_run.path[0]["uuid"],
                    "msg": {
                        "uuid": str(contact1_msg.uuid),
                        "text": "What is your favorite color?",
                        "urn": "tel:+250788382382",
                        "channel": {"uuid": str(self.channel.uuid), "name": "Test Channel"},
                    },
                }
            ],
        )

        # check flow activity endpoint response
        self.login(self.admin)

        activity = self.client.get(reverse("flows.flow_activity", args=[self.flow.uuid])).json()
        self.assertEqual(2, activity["segments"][color_prompt.exit_uuid + ":" + color_ruleset.uuid])
        self.assertEqual(2, activity["nodes"][color_ruleset.uuid])
        self.assertFalse(activity["is_starting"])

        # set the flow as inactive, shouldn't react to replies
        self.flow.is_archived = True
        self.flow.save()

        # create and send a reply
        incoming = self.create_msg(direction=INCOMING, contact=self.contact, text="Orange")
        self.assertFalse(legacy.find_and_handle(incoming)[0])

        # no reply, our flow isn't active
        self.assertFalse(Msg.objects.filter(response_to=incoming))

        contact1_run.refresh_from_db()
        self.assertEqual(len(contact1_run.get_messages()), 1)
        self.assertEqual(len(contact1_run.path), 2)

        # ok, make our flow active again
        self.flow.is_archived = False
        self.flow.save()

        # simulate a response from contact #1
        incoming = self.create_msg(direction=INCOMING, contact=self.contact, text="orange")
        self.assertTrue(legacy.find_and_handle(incoming)[0])

        # our message should have gotten a reply
        reply = Msg.objects.get(response_to=incoming)
        self.assertEqual(reply.contact, self.contact)
        self.assertEqual(
            reply.text,
            "I love orange too! You said: orange which is category: "
            "Orange You are: 0788 382 382 SMS: orange Flow: color: orange",
        )
        self.assertEqual(reply.msg_type, "F")
        self.assertTrue(reply.high_priority)  # should be high priority as this is a reply

        contact1_run.refresh_from_db()
        contact1_run_msgs = contact1_run.get_messages()

        self.assertTrue(contact1_run.responded)
        self.assertEqual(len(contact1_run_msgs), 3)
        self.assertIn(incoming, contact1_run_msgs)
        self.assertIn(reply, contact1_run_msgs)

        # check our completion percentages
        self.assertEqual(
            self.flow.get_run_stats(),
            {"total": 2, "active": 1, "completed": 1, "expired": 0, "interrupted": 0, "completion": 50},
        )

        # at this point there are no more steps to take in the flow, so we shouldn't match anymore
        extra = self.create_msg(direction=INCOMING, contact=self.contact, text="Hello ther")
        self.assertFalse(legacy.find_and_handle(extra)[0])

        self.assertEqual(
            contact1_run.path,
            [
                {
                    "uuid": matchers.UUID4String(),
                    "node_uuid": str(color_prompt.uuid),
                    "arrived_on": matchers.ISODate(),
                    "exit_uuid": str(color_prompt.exit_uuid),
                },
                {
                    "uuid": matchers.UUID4String(),
                    "node_uuid": str(color_ruleset.uuid),
                    "arrived_on": matchers.ISODate(),
                    "exit_uuid": str(orange_rule.uuid),
                },
                {
                    "uuid": matchers.UUID4String(),
                    "node_uuid": str(color_reply.uuid),
                    "arrived_on": matchers.ISODate(),
                    "exit_uuid": str(color_reply.exit_uuid),
                },
            ],
        )
        self.assertEqual(
            contact1_run.events,
            [
                {
                    "type": "msg_created",
                    "created_on": contact1_msg.created_on.isoformat(),
                    "step_uuid": contact1_run.path[0]["uuid"],
                    "msg": {
                        "uuid": str(contact1_msg.uuid),
                        "text": "What is your favorite color?",
                        "urn": "tel:+250788382382",
                        "channel": {"uuid": str(self.channel.uuid), "name": "Test Channel"},
                    },
                },
                {
                    "type": "msg_received",
                    "created_on": incoming.created_on.isoformat(),
                    "step_uuid": contact1_run.path[1]["uuid"],
                    "msg": {
                        "uuid": str(incoming.uuid),
                        "text": "orange",
                        "urn": "tel:+250788382382",
                        "channel": {"uuid": str(self.channel.uuid), "name": "Test Channel"},
                    },
                },
                {
                    "type": "msg_created",
                    "created_on": reply.created_on.isoformat(),
                    "step_uuid": contact1_run.path[2]["uuid"],
                    "msg": {
                        "uuid": str(reply.uuid),
                        "text": "I love orange too! You said: orange which is category: Orange You are: 0788 382 382 SMS: orange Flow: color: orange",
                        "urn": "tel:+250788382382",
                        "channel": {"uuid": str(self.channel.uuid), "name": "Test Channel"},
                    },
                },
            ],
        )

        # we should also have a result for this RuleSet
        self.assertEqual(
            contact1_run.results,
            {
                "color": {
                    "category": "Orange",
                    "node_uuid": str(color_ruleset.uuid),
                    "name": "color",
                    "value": "orange",
                    "created_on": matchers.ISODate(),
                    "input": "orange",
                }
            },
        )

    @uses_legacy_engine
    def test_anon_export_results(self):
        self.org.is_anon = True
        self.org.save()

        (run1,) = legacy.flow_start(self.flow, [], [self.contact])

        msg = self.create_msg(direction=INCOMING, contact=self.contact, text="orange")
        legacy.find_and_handle(msg)

        run1.refresh_from_db()

        workbook = self.export_flow_results(self.flow)
        self.assertEqual(len(workbook.worksheets), 1)
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
                "color (Category) - Color Flow",
                "color (Value) - Color Flow",
                "color (Text) - Color Flow",
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
                "Orange",
                "orange",
                "orange",
            ],
            self.org.timezone,
        )

    @uses_legacy_engine
    def test_export_results_broadcast_only_flow(self):
        self.login(self.admin)

        flow = self.get_flow("two_in_row")
        contact1_run1, contact2_run1, contact3_run1 = legacy.flow_start(
            flow, [], [self.contact, self.contact2, self.contact3]
        )
        contact1_run2, contact2_run2 = legacy.flow_start(
            flow, [], [self.contact, self.contact2], restart_participants=True
        )

        for run in (contact1_run1, contact2_run1, contact3_run1, contact1_run2, contact2_run2):
            run.refresh_from_db()

        with self.assertNumQueries(41):
            workbook = self.export_flow_results(flow)

        tz = self.org.timezone

        sheet_runs, sheet_msgs = workbook.worksheets

        # check runs sheet...
        self.assertEqual(len(list(sheet_runs.rows)), 6)  # header + 5 runs
        self.assertEqual(len(list(sheet_runs.columns)), 6)

        self.assertExcelRow(sheet_runs, 0, ["Contact UUID", "URN", "Name", "Started", "Modified", "Exited"])

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
            ],
            tz,
        )

        # check messages sheet...
        self.assertEqual(len(list(sheet_msgs.rows)), 11)  # header + 10 messages
        self.assertEqual(len(list(sheet_msgs.columns)), 7)

        self.assertExcelRow(sheet_msgs, 0, ["Contact UUID", "URN", "Name", "Date", "Direction", "Message", "Channel"])

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
                "Test Channel",
            ],
            tz,
        )

        # test without msgs or unresponded
        with self.assertNumQueries(34):
            workbook = self.export_flow_results(flow, include_msgs=False, responded_only=True)

        tz = self.org.timezone
        sheet_runs = workbook.worksheets[0]

        self.assertEqual(len(list(sheet_runs.rows)), 1)  # header; no resposes to a broadcast only flow
        self.assertEqual(len(list(sheet_runs.columns)), 6)

        self.assertExcelRow(sheet_runs, 0, ["Contact UUID", "URN", "Name", "Started", "Modified", "Exited"])

    @uses_legacy_engine
    def test_export_results_with_replaced_rulesets(self):
        self.login(self.admin)
        devs = self.create_group("Devs", [self.contact])

        favorites = self.get_flow("favorites")

        contact1_run1, contact3_run1 = legacy.flow_start(favorites, [], [self.contact, self.contact3])

        # simulate two runs each for two contacts...
        contact1_in1 = self.create_msg(direction=INCOMING, contact=self.contact, text="light beige")
        legacy.find_and_handle(contact1_in1)

        contact1_in2 = self.create_msg(direction=INCOMING, contact=self.contact, text="red")
        legacy.find_and_handle(contact1_in2)

        # now remap the uuid for our color
        flow_json = favorites.as_json()
        color_ruleset = flow_json["rule_sets"][0]
        flow_json = json.loads(json.dumps(flow_json).replace(color_ruleset["uuid"], str(uuid4())))
        favorites.update(flow_json)

        contact2_run1 = legacy.flow_start(favorites, [], [self.contact2])[0]

        contact2_in1 = self.create_msg(direction=INCOMING, contact=self.contact2, text="green")
        legacy.find_and_handle(contact2_in1)

        contact1_run2, contact2_run2 = legacy.flow_start(
            favorites, [], [self.contact, self.contact2], restart_participants=True
        )

        contact1_in3 = self.create_msg(direction=INCOMING, contact=self.contact, text=" blue ")
        legacy.find_and_handle(contact1_in3)

        for run in (contact1_run1, contact2_run1, contact3_run1, contact1_run2, contact2_run2):
            run.refresh_from_db()

        workbook = self.export_flow_results(favorites, group_memberships=[devs])

        tz = self.org.timezone

        sheet_runs, sheet_msgs = workbook.worksheets

        # check runs sheet...
        self.assertEqual(len(list(sheet_runs.rows)), 6)  # header + 5 runs
        self.assertEqual(len(list(sheet_runs.columns)), 16)

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
        self.assertEqual(len(list(sheet_msgs.columns)), 7)

        self.assertExcelRow(sheet_msgs, 0, ["Contact UUID", "URN", "Name", "Date", "Direction", "Message", "Channel"])

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
                contact1_in1.created_on,
                "IN",
                "light beige",
                "Test Channel",
            ],
            tz,
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
                "Test Channel",
            ],
            tz,
        )
        self.assertExcelRow(
            sheet_msgs,
            5,
            [contact1_in2.contact.uuid, "+250788382382", "Eric", contact1_in2.created_on, "IN", "red", "Test Channel"],
            tz,
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
                "Test Channel",
            ],
            tz,
        )

    @uses_legacy_engine
    def test_export_results(self):
        # setup flow and start both contacts
        self.contact.update_urns(self.admin, ["tel:+250788382382", "twitter:erictweets"])

        devs = self.create_group("Devs", [self.contact])

        # contact name with an illegal character
        self.contact3.name = "Nor\02bert"
        self.contact3.save(update_fields=("name",), handle_update=False)

        contact1_run1, contact2_run1, contact3_run1 = legacy.flow_start(
            self.flow, [], [self.contact, self.contact2, self.contact3]
        )

        # simulate two runs each for two contacts...
        contact1_in1 = self.create_msg(direction=INCOMING, contact=self.contact, text="light beige")
        legacy.find_and_handle(contact1_in1)

        contact1_in2 = self.create_msg(direction=INCOMING, contact=self.contact, text="orange")
        legacy.find_and_handle(contact1_in2)

        contact2_in1 = self.create_msg(direction=INCOMING, contact=self.contact2, text="green")
        legacy.find_and_handle(contact2_in1)

        contact1_run2, contact2_run2 = legacy.flow_start(
            self.flow, [], [self.contact, self.contact2], restart_participants=True
        )

        contact1_in3 = self.create_msg(direction=INCOMING, contact=self.contact, text=" blue ")
        legacy.find_and_handle(contact1_in3)

        # check can't export anonymously
        exported = self.client.get(reverse("flows.flow_export_results") + "?ids=%d" % self.flow.pk)
        self.assertEqual(302, exported.status_code)

        self.login(self.admin)

        # create a dummy export task so that we won't be able to export
        blocking_export = ExportFlowResultsTask.objects.create(
            org=self.org, created_by=self.admin, modified_by=self.admin
        )
        response = self.client.post(
            reverse("flows.flow_export_results"), dict(flows=[self.flow.pk], group_memberships=[devs.pk]), follow=True
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

                with self.assertNumQueries(42):
                    workbook = self.export_flow_results(self.flow, group_memberships=[devs])

                self.assertEqual(len(captured_logger.output), 3)
                self.assertTrue("fetching runs from archives to export" in captured_logger.output[0])
                self.assertTrue("found 5 runs in database to export" in captured_logger.output[1])
                self.assertTrue("exported 5 in" in captured_logger.output[2])

        tz = self.org.timezone

        sheet_runs, sheet_msgs = workbook.worksheets

        # check runs sheet...
        self.assertEqual(len(list(sheet_runs.rows)), 6)  # header + 5 runs
        self.assertEqual(len(list(sheet_runs.columns)), 10)

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
                "color (Category) - Color Flow",
                "color (Value) - Color Flow",
                "color (Text) - Color Flow",
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
                "Blue",
                "blue",
                " blue ",
            ],
            tz,
        )

        # check messages sheet...
        self.assertEqual(len(list(sheet_msgs.rows)), 14)  # header + 13 messages
        self.assertEqual(len(list(sheet_msgs.columns)), 7)

        self.assertExcelRow(sheet_msgs, 0, ["Contact UUID", "URN", "Name", "Date", "Direction", "Message", "Channel"])

        contact1_out1 = contact1_run1.get_messages().get(text="What is your favorite color?")
        contact1_out2 = contact1_run1.get_messages().get(text="That is a funny color. Try again.")
        contact1_out3 = contact1_run1.get_messages().get(text__startswith="I love orange too")
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
                contact1_in1.created_on,
                "IN",
                "light beige",
                "Test Channel",
            ],
            tz,
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
                "That is a funny color. Try again.",
                "Test Channel",
            ],
            tz,
        )
        self.assertExcelRow(
            sheet_msgs,
            5,
            [
                contact1_in2.contact.uuid,
                "+250788382382",
                "Eric",
                contact1_in2.created_on,
                "IN",
                "orange",
                "Test Channel",
            ],
            tz,
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
                "I love orange too! You said: orange which is category: Orange You are: "
                "0788 382 382 SMS: orange Flow: color: orange",
                "Test Channel",
            ],
            tz,
        )

        # test without msgs or unresponded
        with self.assertNumQueries(41):
            workbook = self.export_flow_results(
                self.flow, include_msgs=False, responded_only=True, group_memberships=(devs,)
            )

        tz = self.org.timezone
        sheet_runs = workbook.worksheets[0]

        self.assertEqual(len(list(sheet_runs.rows)), 4)  # header + 3 runs
        self.assertEqual(len(list(sheet_runs.columns)), 10)

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
                "color (Category) - Color Flow",
                "color (Value) - Color Flow",
                "color (Text) - Color Flow",
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
                "Other",
                "green",
                "green",
            ],
            tz,
        )

        # test export with a contact field
        age = ContactField.get_or_create(self.org, self.admin, "age", "Age")
        self.contact.set_field(self.admin, "age", "36")

        with self.assertNumQueries(43):
            workbook = self.export_flow_results(
                self.flow,
                include_msgs=False,
                responded_only=True,
                contact_fields=[age],
                extra_urns=["twitter", "line"],
                group_memberships=[devs],
            )

        # try setting the field again
        self.contact.set_field(self.admin, "age", "36")

        tz = self.org.timezone
        sheet_runs, = workbook.worksheets

        # check runs sheet...
        self.assertEqual(len(list(sheet_runs.rows)), 4)  # header + 3 runs
        self.assertEqual(len(list(sheet_runs.columns)), 13)

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
                "color (Category) - Color Flow",
                "color (Value) - Color Flow",
                "color (Text) - Color Flow",
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
                "Orange",
                "orange",
                "orange",
            ],
            tz,
        )

        # test that we don't exceed the limit on rows per sheet
        with patch("temba.flows.models.ExportFlowResultsTask.MAX_EXCEL_ROWS", 4):
            workbook = self.export_flow_results(self.flow)
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
        self.flow.is_archived = True
        self.flow.save()

        workbook = self.export_flow_results(self.flow)

        tz = self.org.timezone

        sheet_runs, sheet_msgs = workbook.worksheets

        # check runs sheet...
        self.assertEqual(len(list(sheet_runs.rows)), 6)  # header + 5 runs
        self.assertEqual(len(list(sheet_runs.columns)), 9)

        # check messages sheet...
        self.assertEqual(len(list(sheet_msgs.rows)), 14)  # header + 13 messages
        self.assertEqual(len(list(sheet_msgs.columns)), 7)

    @uses_legacy_engine
    def test_export_results_remove_control_characters(self):
        contact1_run1 = legacy.flow_start(self.flow, [], [self.contact])[0]

        contact1_in1 = self.create_msg(direction=INCOMING, contact=self.contact, text="ngert\x07in.")
        legacy.find_and_handle(contact1_in1)

        contact1_run1.refresh_from_db()

        workbook = self.export_flow_results(self.flow)

        tz = self.org.timezone

        sheet_runs, sheet_msgs = workbook.worksheets

        self.assertExcelRow(
            sheet_runs,
            1,
            [
                contact1_run1.contact.uuid,
                "+250788382382",
                "Eric",
                contact1_run1.created_on,
                contact1_run1.modified_on,
                "",
                "Other",
                "ngertin.",
                "ngertin.",
            ],
            tz,
        )

    @uses_legacy_engine
    def test_run_as_archive_json(self):
        contact1_run = legacy.flow_start(self.flow, [], [self.contact])[0]
        contact1_in1 = self.create_msg(direction=INCOMING, contact=self.contact, text="green")
        legacy.find_and_handle(contact1_in1)

        # we now have 4 runs in this order of modified_on
        contact1_run.refresh_from_db()

        self.assertEqual(
            set(contact1_run.as_archive_json().keys()),
            set(
                [
                    "id",
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

        self.assertEqual(contact1_run.as_archive_json()["id"], contact1_run.id)
        self.assertEqual(contact1_run.as_archive_json()["flow"], {"uuid": str(self.flow.uuid), "name": "Color Flow"})
        self.assertEqual(contact1_run.as_archive_json()["contact"], {"uuid": str(self.contact.uuid), "name": "Eric"})
        self.assertTrue(contact1_run.as_archive_json()["responded"])

        self.assertEqual(
            contact1_run.as_archive_json()["path"],
            [
                {"node": matchers.UUID4String(), "time": matchers.ISODate()},
                {"node": matchers.UUID4String(), "time": matchers.ISODate()},
                {"node": matchers.UUID4String(), "time": matchers.ISODate()},
                {"node": matchers.UUID4String(), "time": matchers.ISODate()},
            ],
        )

        self.assertEqual(
            contact1_run.as_archive_json()["values"],
            {
                "color": {
                    "category": "Other",
                    "input": "green",
                    "name": "color",
                    "node": matchers.UUID4String(),
                    "time": matchers.ISODate(),
                    "value": "green",
                }
            },
        )

        self.assertEqual(
            contact1_run.as_archive_json()["events"],
            [
                {
                    "created_on": matchers.ISODate(),
                    "msg": {
                        "channel": {"name": "Test Channel", "uuid": matchers.UUID4String()},
                        "text": "What is your favorite color?",
                        "urn": "tel:+250788382382",
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
                        "urn": "tel:+250788382382",
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
                        "urn": "tel:+250788382382",
                        "uuid": matchers.UUID4String(),
                    },
                    "step_uuid": matchers.UUID4String(),
                    "type": "msg_created",
                },
            ],
        )

        self.assertEqual(contact1_run.as_archive_json()["created_on"], contact1_run.created_on.isoformat())
        self.assertEqual(contact1_run.as_archive_json()["modified_on"], contact1_run.modified_on.isoformat())
        self.assertIsNone(contact1_run.as_archive_json()["exit_type"])
        self.assertIsNone(contact1_run.as_archive_json()["exited_on"])
        self.assertIsNone(contact1_run.as_archive_json()["submitted_by"])

    @uses_legacy_engine
    def test_export_results_from_archives(self):
        contact1_run, contact2_run = legacy.flow_start(self.flow, [], [self.contact, self.contact2])
        contact1_in1 = self.create_msg(direction=INCOMING, contact=self.contact, text="green")
        legacy.find_and_handle(contact1_in1)
        contact2_in1 = self.create_msg(direction=INCOMING, contact=self.contact2, text="blue")
        legacy.find_and_handle(contact2_in1)

        # and a run for a different flow
        flow2 = self.get_flow("favorites")
        contact2_other_flow, = legacy.flow_start(flow2, [], [self.contact2])

        contact3_run, = legacy.flow_start(self.flow, [], [self.contact3])

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
        mock_s3.put_jsonl(
            "test-bucket",
            "archive1.jsonl.gz",
            [contact1_run.as_archive_json(), old_archive_format, contact2_other_flow.as_archive_json()],
        )

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
        mock_s3.put_jsonl("test-bucket", "archive2.jsonl.gz", [contact2_run.as_archive_json()])

        with patch("temba.archives.models.Archive.s3_client", return_value=mock_s3):
            workbook = self.export_flow_results(self.flow)

        tz = self.org.timezone
        sheet_runs, sheet_msgs = workbook.worksheets

        # check runs sheet...
        self.assertEqual(len(list(sheet_runs.rows)), 4)  # header + 3 runs

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
                "",
                "",
                "",
            ],
            tz,
        )

    @uses_legacy_engine
    def test_export_results_with_surveyor_msgs(self):
        self.flow.flow_type = Flow.TYPE_SURVEY
        self.flow.save()
        run = legacy.flow_start(self.flow, [], [self.contact])[0]

        # no urn or channel
        in1 = Msg.create_incoming(None, None, "blue", org=self.org, contact=self.contact)

        workbook = self.export_flow_results(self.flow)
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
                "Test Channel",
            ],
            tz,
        )

        # no channel or phone
        self.assertExcelRow(sheet_msgs, 2, [run.contact.uuid, "", "Eric", in1.created_on, "IN", "blue", ""], tz)

        # now try setting a submitted by on our run
        run.submitted_by = self.admin
        run.save(update_fields=("submitted_by",))

        workbook = self.export_flow_results(self.flow)
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
                "Blue",
                "blue",
                "blue",
            ],
            tz,
        )

    def test_export_results_with_no_responses(self):
        self.assertEqual(self.flow.get_run_stats()["total"], 0)

        workbook = self.export_flow_results(self.flow)

        self.assertEqual(len(workbook.worksheets), 1)

        # every sheet has only the head row
        self.assertEqual(len(list(workbook.worksheets[0].rows)), 1)
        self.assertEqual(len(list(workbook.worksheets[0].columns)), 9)

    def test_copy(self):
        # pick a really long name so we have to concatenate
        self.flow.name = "Color Flow is a long name to use for something like this"
        self.flow.expires_after_minutes = 60
        self.flow.save()

        # make sure our metadata got saved
        metadata = self.flow.metadata
        self.assertEqual("Ryan Lewis", metadata["author"])

        # now create a copy
        copy = Flow.copy(self.flow, self.admin)

        metadata = copy.metadata
        self.assertEqual("Ryan Lewis", metadata["author"])

        # expiration should be copied too
        self.assertEqual(60, copy.expires_after_minutes)

        # should have a different id
        self.assertNotEqual(self.flow.pk, copy.pk)

        # Name should start with "Copy of"
        self.assertEqual("Copy of Color Flow is a long name to use for something like thi", copy.name)

        # metadata should come out in the json
        copy_json = copy.as_json()
        self.assertEqual(
            dict(
                author="Ryan Lewis",
                name="Copy of Color Flow is a long name to use for something like thi",
                revision=1,
                expires=60,
                uuid=copy.uuid,
                saved_on=json.encode_datetime(copy.saved_on, micros=True),
            ),
            copy_json["metadata"],
        )

        # should have the same number of actionsets and rulesets
        self.assertEqual(copy.action_sets.all().count(), self.flow.action_sets.all().count())
        self.assertEqual(copy.rule_sets.all().count(), self.flow.rule_sets.all().count())

    def test_copy_group_split_no_name(self):
        flow = self.get_flow("group_split_no_name")
        flow_json = flow.as_json()

        copy = Flow.copy(flow, self.admin)

        copy_json = copy.as_json()

        self.assertEqual(len(copy_json["nodes"]), 1)
        self.assertEqual(len(copy_json["nodes"][0]["router"]["cases"]), 1)
        self.assertEqual(
            copy_json["nodes"][0]["router"]["cases"][0],
            {
                "uuid": matchers.UUID4String(),
                "type": "has_group",
                "arguments": [matchers.UUID4String()],
                "category_uuid": matchers.UUID4String(),
            },
        )

        # check that the original and the copy reference the same group
        self.assertEqual(
            flow_json["nodes"][0]["router"]["cases"][0]["arguments"],
            copy_json["nodes"][0]["router"]["cases"][0]["arguments"],
        )

    def test_parsing(self):
        # our flow should have the appropriate RuleSet and ActionSet objects
        self.assertEqual(4, ActionSet.objects.all().count())

        entry = ActionSet.objects.get(x=1, y=1)
        actions = entry.get_actions()
        self.assertEqual(len(actions), 1)
        self.assertIsInstance(actions[0], legacy.ReplyAction)
        self.assertEqual(
            actions[0].msg, dict(base="What is your favorite color?", fra="Quelle est votre couleur préférée?")
        )
        self.assertEqual(entry.uuid, self.flow.entry_uuid)

        orange = ActionSet.objects.get(x=2, y=2)
        actions = orange.get_actions()
        self.assertEqual(1, len(actions))
        self.assertEqual(
            legacy.ReplyAction(
                actions[0].uuid,
                dict(
                    base="I love orange too! You said: @step.value which is category: @flow.color.category You are: @step.contact.tel SMS: @step Flow: @flow"
                ),
            ).as_json(),
            actions[0].as_json(),
        )

        self.assertEqual(1, RuleSet.objects.all().count())
        ruleset = RuleSet.objects.get(label="color")
        self.assertEqual(entry.destination, ruleset.uuid)
        rules = ruleset.get_rules()
        self.assertEqual(4, len(rules))

        # check ordering
        self.assertEqual(rules[0].category["base"], "Orange")
        self.assertEqual(rules[1].category["base"], "Blue")
        self.assertEqual(rules[2].category["base"], "Other")

        # check routing
        self.assertEqual(legacy.ContainsTest(test=dict(base="orange")).as_json(), rules[0].test.as_json())
        self.assertEqual(legacy.ContainsTest(test=dict(base="blue")).as_json(), rules[1].test.as_json())
        self.assertEqual(legacy.TrueTest().as_json(), rules[2].test.as_json())

        # and categories
        self.assertEqual("Orange", rules[0].category["base"])
        self.assertEqual("Blue", rules[1].category["base"])

        # back out as json
        json_dict = self.flow.as_json()

        self.assertEqual(json_dict["version"], Flow.FINAL_LEGACY_VERSION)
        self.assertEqual(json_dict["flow_type"], self.flow.flow_type)
        self.assertEqual(
            json_dict["metadata"],
            {
                "name": self.flow.name,
                "author": "Ryan Lewis",
                "saved_on": json.encode_datetime(self.flow.saved_on, micros=True),
                "revision": 1,
                "expires": self.flow.expires_after_minutes,
                "uuid": self.flow.uuid,
            },
        )

        # remove one of our actions and rules
        del json_dict["action_sets"][3]
        del json_dict["rule_sets"][0]["rules"][2]

        # update
        self.flow.update(json_dict)

        self.assertEqual(3, ActionSet.objects.all().count())

        entry = ActionSet.objects.get(x=1, y=1)
        actions = entry.get_actions()
        self.assertEqual(len(actions), 1)
        self.assertIsInstance(actions[0], legacy.ReplyAction)
        self.assertEqual(
            actions[0].msg, dict(base="What is your favorite color?", fra="Quelle est votre couleur préférée?")
        )
        self.assertEqual(entry.uuid, self.flow.entry_uuid)

        orange = ActionSet.objects.get(x=2, y=2)
        actions = orange.get_actions()
        self.assertEqual(1, len(actions))
        self.assertEqual(
            legacy.ReplyAction(
                actions[0].uuid,
                dict(
                    base="I love orange too! You said: @step.value which is category: @flow.color.category You are: @step.contact.tel SMS: @step Flow: @flow"
                ),
            ).as_json(),
            actions[0].as_json(),
        )

        self.assertEqual(1, RuleSet.objects.all().count())
        ruleset = RuleSet.objects.get(label="color")
        self.assertEqual(entry.destination, ruleset.uuid)
        rules = ruleset.get_rules()
        self.assertEqual(3, len(rules))

        # check ordering
        self.assertEqual(rules[0].category["base"], "Orange")
        self.assertEqual(rules[1].category["base"], "Blue")

        # check routing
        self.assertEqual(legacy.ContainsTest(test=dict(base="orange")).as_json(), rules[0].test.as_json())
        self.assertEqual(legacy.ContainsTest(test=dict(base="blue")).as_json(), rules[1].test.as_json())

        # updating with a label name that is too long should truncate it
        json_dict["rule_sets"][0]["label"] = "W" * 75
        json_dict["rule_sets"][0]["operand"] = "W" * 135
        self.flow.update(json_dict)

        # now check they are truncated to the max lengths
        ruleset = RuleSet.objects.get()
        self.assertEqual(64, len(ruleset.label))
        self.assertEqual(128, len(ruleset.operand))

    def test_expanding(self):
        # add actions for adding to a group and messaging a contact, we'll test how these expand
        action_set = ActionSet.objects.get(x=4, y=4)

        actions = [
            legacy.AddToGroupAction(str(uuid4()), [self.other_group]).as_json(),
            legacy.SendAction(str(uuid4()), "Outgoing Message", [], [self.contact], []).as_json(),
        ]

        action_set.actions = actions
        action_set.save()

        # check expanding our groups
        json_dict = self.flow.as_json(expand_contacts=True)
        json_as_string = json.dumps(json_dict)

        # our json should contain the names of our contact and groups
        self.assertTrue(json_as_string.find("Eric") > 0)
        self.assertTrue(json_as_string.find("Other") > 0)

        # now delete our group
        self.other_group.delete()

        flow_json = self.flow.as_json(expand_contacts=True)
        add_group = flow_json["action_sets"][3]["actions"][0]
        send = flow_json["action_sets"][3]["actions"][1]

        # should still see a reference to our group even (recreated)
        self.assertEqual(1, len(add_group["groups"]))
        self.assertEqual(0, len(send["groups"]))

    def test_length(self):
        org = self.org

        js = [
            dict(category="Normal Length", uuid=uuid4(), destination=uuid4(), test=dict(type="true")),
            dict(
                category="Way too long, will get clipped at 36 characters",
                uuid=uuid4(),
                destination=uuid4(),
                test=dict(type="true"),
            ),
        ]

        rules = legacy.Rule.from_json_array(org, js)

        self.assertEqual("Normal Length", rules[0].category)
        self.assertEqual(36, len(rules[1].category))

    @uses_legacy_engine
    def test_null_categories(self):
        legacy.flow_start(self.flow, [], [self.contact])
        sms = self.create_msg(direction=INCOMING, contact=self.contact, text="blue")
        self.assertTrue(legacy.find_and_handle(sms)[0])

        FlowCategoryCount.objects.get(category_name="Blue", result_name="color", result_key="color", count=1)

        # get our run and clear the category
        run = FlowRun.objects.get(flow=self.flow, contact=self.contact)
        results = run.results
        del results["color"]["category"]
        results["color"]["created_on"] = timezone.now()
        run.save(update_fields=["results", "modified_on"])

        # should have added a negative one now
        self.assertEqual(2, FlowCategoryCount.objects.filter(category_name="Blue", result_name="color").count())
        FlowCategoryCount.objects.get(category_name="Blue", result_name="color", result_key="color", count=-1)

    def test_flow_keyword_create(self):
        self.login(self.admin)

        # try creating a flow with invalid keywords
        response = self.client.post(
            reverse("flows.flow_create"),
            {
                "name": "Flow #1",
                "keyword_triggers": "toooooooooooooolong,test",
                "flow_type": Flow.TYPE_MESSAGE,
                "expires_after_minutes": 60 * 12,
            },
        )
        self.assertEqual(response.status_code, 200)
        self.assertFormError(
            response,
            "form",
            "keyword_triggers",
            '"toooooooooooooolong" must be a single word, less than 16 characters, containing only '
            "letter and numbers",
        )

        # submit with valid keywords
        response = self.client.post(
            reverse("flows.flow_create"),
            {
                "name": "Flow #1",
                "keyword_triggers": "testing, test",
                "flow_type": Flow.TYPE_MESSAGE,
                "expires_after_minutes": 60 * 12,
            },
        )
        self.assertEqual(response.status_code, 302)

        flow = Flow.objects.get(name="Flow #1")
        self.assertEqual(flow.triggers.all().count(), 2)
        self.assertEqual(set(flow.triggers.values_list("keyword", flat=True)), {"testing", "test"})

        # try creating a survey flow with keywords (they'll be ignored)
        response = self.client.post(
            reverse("flows.flow_create"),
            {
                "name": "Survey Flow",
                "keyword_triggers": "notallowed",
                "flow_type": Flow.TYPE_SURVEY,
                "expires_after_minutes": 60 * 12,
            },
        )
        self.assertEqual(response.status_code, 302)

        # should't be allowed to have a survey flow and keywords
        flow = Flow.objects.get(name="Survey Flow")
        self.assertEqual(flow.triggers.all().count(), 0)

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
                "keyword_triggers": "it,changes,everything",
                "expires_after_minutes": 60 * 12,
                "base_language": "base",
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
            ["name", "keyword_triggers", "expires_after_minutes", "ignore_triggers", "base_language", "loc"],
        )

        # update flow triggers
        post_data = dict()
        post_data["name"] = "Flow With Keyword Triggers"
        post_data["keyword_triggers"] = "it,join"
        post_data["expires_after_minutes"] = 60 * 12
        post_data["base_language"] = "base"
        response = self.client.post(reverse("flows.flow_update", args=[flow.pk]), post_data, follow=True)

        flow_with_keywords = Flow.objects.get(name=post_data["name"])
        self.assertEqual(200, response.status_code)
        self.assertEqual(response.request["PATH_INFO"], reverse("flows.flow_list"))
        self.assertTrue(flow_with_keywords in response.context["object_list"].all())
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

    def test_copy_view(self):

        self.login(self.admin)

        # test a successful copy
        response = self.client.post(reverse("flows.flow_copy", args=[self.flow.id]))
        flow_copy = Flow.objects.get(org=self.org, name="Copy of %s" % self.flow.name)
        self.assertRedirect(response, reverse("flows.flow_editor", args=[flow_copy.uuid]))
        flow_copy.release()

        # make our first action one that can't be copied (a send with a group)
        group = ContactGroup.user_groups.filter(name="Other").first()
        actionset = self.flow.action_sets.first()
        actions = actionset.actions

        actions[0]["type"] = legacy.SendAction.TYPE
        actions[0]["groups"] = [dict(uuid=group.uuid, name=group.name)]
        actions[0]["contacts"] = []
        actionset.actions = actions
        actionset.save(update_fields=["actions"])

        # we should allow copy of flows with group sends
        response = self.client.post(reverse("flows.flow_copy", args=[self.flow.id]))
        self.assertIsNotNone(Flow.objects.filter(org=self.org, name="Copy of %s" % self.flow.name).first())

    @uses_legacy_engine
    def test_views(self):
        self.create_secondary_org()

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
        self.assertIn("flow_type", response.context["form"].fields)

        # our default brand has all choice types
        response = self.client.get(reverse("flows.flow_create"))
        choices = [(Flow.TYPE_MESSAGE, "Messaging"), (Flow.TYPE_VOICE, "Phone Call"), (Flow.TYPE_SURVEY, "Surveyor")]
        self.assertEqual(choices, response.context["form"].fields["flow_type"].choices)

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
        post_data = dict()
        post_data["name"] = "Flow With Unformated Keyword Triggers"
        post_data["keyword_triggers"] = "this is,it"
        response = self.client.post(reverse("flows.flow_create"), post_data)
        self.assertFormError(
            response,
            "form",
            "keyword_triggers",
            '"this is" must be a single word, less than 16 characters, containing only letter and numbers',
        )

        # create a new flow with one existing keyword
        post_data = dict()
        post_data["name"] = "Flow With Existing Keyword Triggers"
        post_data["keyword_triggers"] = "this,is,unique"
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
        post_data = dict()
        post_data["name"] = "Flow With Good Keyword Triggers"
        post_data["keyword_triggers"] = "this,is,it"
        post_data["flow_type"] = Flow.TYPE_MESSAGE
        post_data["expires_after_minutes"] = 30
        response = self.client.post(reverse("flows.flow_create"), post_data, follow=True)
        flow3 = Flow.objects.get(name=post_data["name"])

        self.assertEqual(200, response.status_code)
        self.assertEqual(response.request["PATH_INFO"], reverse("flows.flow_editor", args=[flow3.uuid]))
        self.assertEqual(response.context["object"].triggers.count(), 3)

        # update expiration for voice flow, and test if form has expected fields
        post_data = dict()
        response = self.client.get(reverse("flows.flow_update", args=[voice_flow.pk]), post_data, follow=True)

        field_names = [field for field in response.context_data["form"].fields]
        self.assertEqual(
            field_names, ["name", "keyword_triggers", "expires_after_minutes", "ignore_triggers", "ivr_retry", "loc"]
        )

        choices = response.context["form"].fields["expires_after_minutes"].choices
        self.assertEqual(7, len(choices))
        self.assertEqual(1, choices[0][0])
        self.assertEqual(2, choices[1][0])
        self.assertEqual(3, choices[2][0])
        self.assertEqual(4, choices[3][0])
        self.assertEqual(5, choices[4][0])
        self.assertEqual(10, choices[5][0])
        self.assertEqual(15, choices[6][0])

        # try updating with an sms type expiration to make sure it's restricted for voice flows
        post_data["expires_after_minutes"] = 60 * 12
        post_data["ivr_retry"] = 30
        post_data["name"] = "Voice Flow"
        response = self.client.post(reverse("flows.flow_update", args=[voice_flow.pk]), post_data, follow=True)

        self.assertFormError(
            response,
            "form",
            "expires_after_minutes",
            "Select a valid choice. 720 is not one of the available choices.",
        )

        voice_flow.refresh_from_db()
        self.assertEqual(5, voice_flow.expires_after_minutes)

        # now do a valid value for voice
        post_data["expires_after_minutes"] = 3
        post_data["ivr_retry"] = 30
        response = self.client.post(reverse("flows.flow_update", args=[voice_flow.pk]), post_data, follow=True)

        voice_flow.refresh_from_db()
        self.assertEqual(3, voice_flow.expires_after_minutes)

        # invalid value for ivr_retry
        post_data["expires_after_minutes"] = 3
        post_data["ivr_retry"] = 123
        response = self.client.post(reverse("flows.flow_update", args=[voice_flow.pk]), post_data, follow=True)

        self.assertFormError(
            response, "form", "ivr_retry", "Select a valid choice. 123 is not one of the available choices."
        )

        # now do a valid value for ivr_retry
        post_data["expires_after_minutes"] = 3
        post_data["ivr_retry"] = 1440
        response = self.client.post(reverse("flows.flow_update", args=[voice_flow.pk]), post_data, follow=True)

        voice_flow.refresh_from_db()
        self.assertEqual(voice_flow.metadata["ivr_retry"], 1440)

        # update flow triggers, and test if form has expected fields
        post_data = dict()
        response = self.client.post(reverse("flows.flow_update", args=[flow3.pk]), post_data, follow=True)

        field_names = [field for field in response.context_data["form"].fields]
        self.assertEqual(field_names, ["name", "keyword_triggers", "expires_after_minutes", "ignore_triggers", "loc"])

        post_data = dict()
        post_data["name"] = "Flow With Keyword Triggers"
        post_data["keyword_triggers"] = "it,changes,everything"
        post_data["expires_after_minutes"] = 60 * 12
        response = self.client.post(reverse("flows.flow_update", args=[flow3.pk]), post_data, follow=True)

        flow3 = Flow.objects.get(name=post_data["name"])
        self.assertEqual(200, response.status_code)
        self.assertEqual(response.request["PATH_INFO"], reverse("flows.flow_list"))
        self.assertTrue(flow3 in response.context["object_list"].all())
        self.assertEqual(flow3.triggers.count(), 5)
        self.assertEqual(flow3.triggers.filter(is_archived=True).count(), 2)
        self.assertEqual(flow3.triggers.filter(is_archived=False).count(), 3)
        self.assertEqual(flow3.triggers.filter(is_archived=False).exclude(groups=None).count(), 0)

        # update flow with unformatted keyword
        post_data["keyword_triggers"] = "it,changes,every thing"
        response = self.client.post(reverse("flows.flow_update", args=[flow3.pk]), post_data)
        self.assertTrue(response.context["form"].errors)

        # update flow with unformated keyword
        post_data["keyword_triggers"] = "it,changes,everything,unique"
        response = self.client.post(reverse("flows.flow_update", args=[flow3.pk]), post_data)
        self.assertTrue(response.context["form"].errors)
        response = self.client.get(reverse("flows.flow_update", args=[flow3.pk]))
        self.assertEqual(response.context["form"].fields["keyword_triggers"].initial, "it,changes,everything")
        self.assertEqual(flow3.triggers.filter(is_archived=False).count(), 3)
        self.assertEqual(flow3.triggers.filter(is_archived=False).exclude(groups=None).count(), 0)
        trigger = Trigger.objects.get(keyword="everything", flow=flow3)
        group = self.create_group("first", [self.contact])
        trigger.groups.add(group)
        self.assertEqual(flow3.triggers.filter(is_archived=False).count(), 3)
        self.assertEqual(flow3.triggers.filter(is_archived=False).exclude(groups=None).count(), 1)
        self.assertEqual(flow3.triggers.filter(is_archived=False).exclude(groups=None)[0].keyword, "everything")
        response = self.client.get(reverse("flows.flow_update", args=[flow3.pk]))
        self.assertEqual(response.context["form"].fields["keyword_triggers"].initial, "it,changes")
        self.assertNotContains(response, "contact_creation")
        self.assertEqual(flow3.triggers.filter(is_archived=False).count(), 3)
        self.assertEqual(flow3.triggers.filter(is_archived=False).exclude(groups=None).count(), 1)
        self.assertEqual(flow3.triggers.filter(is_archived=False).exclude(groups=None)[0].keyword, "everything")

        # make us a survey flow
        flow3.flow_type = Flow.TYPE_SURVEY
        flow3.save()

        # we should get the contact creation option, and test if form has expected fields
        response = self.client.get(reverse("flows.flow_update", args=[flow3.pk]))
        self.assertContains(response, "contact_creation")

        field_names = [field for field in response.context_data["form"].fields]
        self.assertEqual(field_names, ["name", "contact_creation", "expires_after_minutes", "loc"])

        # set contact creation to be per login
        del post_data["keyword_triggers"]
        post_data["contact_creation"] = Flow.CONTACT_PER_LOGIN
        response = self.client.post(reverse("flows.flow_update", args=[flow3.pk]), post_data)
        flow3.refresh_from_db()
        self.assertEqual(Flow.CONTACT_PER_LOGIN, flow3.metadata.get("contact_creation"))

        # can see results for a flow
        response = self.client.get(reverse("flows.flow_results", args=[self.flow.uuid]))
        self.assertEqual(200, response.status_code)

        # check flow listing
        response = self.client.get(reverse("flows.flow_list"))
        self.assertEqual(
            list(response.context["object_list"]), [flow3, voice_flow, flow2, flow1, self.flow]
        )  # by saved_on

        # start a contact in a flow
        legacy.flow_start(self.flow, [], [self.contact])

        # test getting the json
        response = self.client.get(reverse("flows.flow_json", args=[self.flow.uuid]))
        self.assertIn("channels", response.json())
        self.assertIn("languages", response.json())
        self.assertIn("channel_countries", response.json())
        self.assertEqual(ActionSet.objects.all().count(), 28)

        json_dict = response.json()["flow"]

        # test setting the json to a single actionset
        json_dict["action_sets"] = [
            {
                "uuid": str(uuid4()),
                "x": 1,
                "y": 1,
                "destination": None,
                "actions": [
                    {
                        "uuid": "013e6934-c439-4e14-97ec-218b5644f235",
                        "type": "reply",
                        "msg": {"base": "This flow is more like a broadcast"},
                    }
                ],
                "exit_uuid": "bd5a374d-04c4-4383-a9f8-a574fe22c780",
            }
        ]
        json_dict["rule_sets"] = []
        json_dict["entry"] = json_dict["action_sets"][0]["uuid"]

        response = self.client.post(
            reverse("flows.flow_json", args=[self.flow.uuid]), json_dict, content_type="application/json"
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(ActionSet.objects.all().count(), 25)

        # check that the flow only has a single actionset
        ActionSet.objects.get(flow=self.flow)

        # can't save with an invalid uuid
        json_dict["metadata"]["saved_on"] = json.encode_datetime(timezone.now(), micros=True)
        json_dict["action_sets"][0]["destination"] = "notthere"

        response = self.client.post(
            reverse("flows.flow_json", args=[self.flow.uuid]), json.dumps(json_dict), content_type="application/json"
        )

        self.assertEqual(response.status_code, 400)

        self.flow.refresh_from_db()
        flow_json = self.flow.as_json()
        self.assertIsNone(flow_json["action_sets"][0]["destination"])

        # flow should still be there though
        self.flow.refresh_from_db()

        # should still have the original one, nothing changed
        response = self.client.get(reverse("flows.flow_json", args=[self.flow.uuid]))
        self.assertEqual(200, response.status_code)
        json_dict = response.json()

        # can't save against the other org's flow
        response = self.client.post(
            reverse("flows.flow_json", args=[other_flow.uuid]), json.dumps(json_dict), content_type="application/json"
        )
        self.assertEqual(302, response.status_code)

        # can't save with invalid json
        with self.assertRaises(ValueError):
            response = self.client.post(
                reverse("flows.flow_json", args=[self.flow.uuid]), "badjson", content_type="application/json"
            )

        # test update view
        response = self.client.post(reverse("flows.flow_update", args=[self.flow.id]))
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
        # create the language for our org
        language = Language.create(self.org, self.flow.created_by, "English", "eng")
        self.org.primary_language = language
        self.org.save()

        response = self.client.post(
            reverse("flows.flow_create"),
            {
                "name": "Language Flow",
                "expires_after_minutes": 5,
                "base_language": language.iso_code,
                "flow_type": Flow.TYPE_MESSAGE,
            },
            follow=True,
        )

        language_flow = Flow.objects.get(name="Language Flow")

        self.assertEqual(200, response.status_code)
        self.assertEqual(response.request["PATH_INFO"], reverse("flows.flow_editor", args=[language_flow.uuid]))
        self.assertEqual(language_flow.base_language, language.iso_code)

    def test_flow_update_of_inactive_flow(self):
        flow = self.get_flow("favorites")
        # release the flow
        flow.release()

        post_data = {"name": "Flow that does not exist"}

        self.login(self.admin)
        response = self.client.post(reverse("flows.flow_update", args=[flow.pk]), post_data)

        # can't delete already released flow
        self.assertEqual(response.status_code, 404)

    def test_flow_results_of_inactive_flow(self):
        flow = self.get_flow("favorites")
        # release the flow
        flow.release()

        self.login(self.admin)
        response = self.client.get(reverse("flows.flow_results", args=[flow.uuid]))

        self.assertEqual(response.status_code, 404)

    def test_views_viewers(self):
        # create a viewer
        self.viewer = self.create_user("Viewer")
        self.org.viewers.add(self.viewer)
        self.viewer.set_org(self.org)

        self.create_secondary_org()

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
        post_data["objects"] = self.flow.pk
        post_data["label"] = flow_label.pk
        post_data["add"] = True

        response = self.client.post(flow_list_url, post_data, follow=True)
        self.assertEqual(1, response.context["object_list"].count())
        self.assertFalse(response.context["object_list"][0].labels.all())

        # can not archive
        post_data = dict()
        post_data["action"] = "archive"
        post_data["objects"] = self.flow.pk
        response = self.client.post(flow_list_url, post_data, follow=True)
        self.assertEqual(1, response.context["object_list"].count())
        self.assertEqual(response.context["object_list"][0].pk, self.flow.pk)
        self.assertFalse(response.context["object_list"][0].is_archived)

        # inactive list shouldn't have any flows
        response = self.client.get(flow_archived_url)
        self.assertEqual(0, len(response.context["object_list"]))

        response = self.client.get(reverse("flows.flow_editor", args=[self.flow.uuid]))
        self.assertEqual(200, response.status_code)
        self.assertFalse(response.context["mutable"])

        # we can fetch the json for the flow
        response = self.client.get(reverse("flows.flow_json", args=[self.flow.uuid]))
        self.assertEqual(200, response.status_code)

        # but posting to it should redirect to a get
        response = self.client.post(reverse("flows.flow_json", args=[self.flow.uuid]), post_data=response.content)
        self.assertEqual(302, response.status_code)

        self.flow.is_archived = True
        self.flow.save()

        response = self.client.get(flow_list_url)
        self.assertEqual(0, len(response.context["object_list"]))

        # can not restore
        post_data = dict()
        post_data["action"] = "archive"
        post_data["objects"] = self.flow.pk
        response = self.client.post(flow_archived_url, post_data, follow=True)
        self.assertEqual(1, response.context["object_list"].count())
        self.assertEqual(response.context["object_list"][0].pk, self.flow.pk)
        self.assertTrue(response.context["object_list"][0].is_archived)

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

    def test_flow_update_error(self):

        flow = self.get_flow("favorites")
        json_dict = flow.as_json()
        json_dict["action_sets"][0]["actions"].append(dict(type="add_label", labels=[dict(name="@badlabel")]))
        self.login(self.admin)
        response = self.client.post(
            reverse("flows.flow_json", args=[flow.uuid]), json.dumps(json_dict), content_type="application/json"
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["description"], "Your flow could not be saved. Please refresh your browser.")

    @uses_legacy_engine
    def test_flow_start_with_start_msg(self):
        msg_in = self.create_msg(direction=INCOMING, contact=self.contact, text="I am coming")
        run, = legacy.flow_start(self.flow, [], [self.contact], start_msg=msg_in)

        msg_in.refresh_from_db()
        msg_out = Msg.objects.get(direction="O")

        # both msgs should be of type FLOW
        self.assertEqual(msg_in.msg_type, "F")
        self.assertEqual(msg_out.msg_type, "F")

        run_msgs = run.get_messages().order_by("created_on")
        self.assertEqual(list(run_msgs), [msg_in, msg_out])

        self.assertEqual(len(run.path), 2)

    @uses_legacy_engine
    def test_quick_replies(self):
        flow = self.get_flow("quick_replies")
        run, = legacy.flow_start(flow, [], [self.contact4])

        # contact language is Portugese but this isn't an org language so we should use English
        msg = Msg.objects.filter(direction="O").last()
        self.assertEqual(msg.metadata, {"quick_replies": ["Yes", "No"]})

        # add Portugese as an org language and try again
        self.org.set_languages(self.admin, ["eng", "por"], "eng")
        run, = legacy.flow_start(flow, [], [self.contact4], restart_participants=True)

        msg = Msg.objects.filter(direction="O").last()
        self.assertEqual(msg.metadata, {"quick_replies": ["Sim", "No"]})

    @uses_legacy_engine
    def test_multiple(self):
        run1, = legacy.flow_start(self.flow, [], [self.contact])

        # create a second flow and start our same contact
        self.flow2 = self.flow.copy(self.flow, self.flow.created_by)
        run2, = legacy.flow_start(self.flow2, [], [self.contact])

        run1.refresh_from_db()
        run2.refresh_from_db()

        # only the second run should be active
        self.assertFalse(run1.is_active)
        self.assertEqual(len(run1.path), 2)

        self.assertTrue(run2.is_active)
        self.assertEqual(len(run2.path), 2)

        # send in a message
        incoming = self.create_msg(direction=INCOMING, contact=self.contact, text="Orange", created_on=timezone.now())
        self.assertTrue(legacy.find_and_handle(incoming)[0])

        run1.refresh_from_db()
        run2.refresh_from_db()

        # only the second flow should get it
        self.assertEqual(len(run1.path), 2)
        self.assertEqual(len(run2.path), 3)

        # start the flow again for our contact
        run3, = legacy.flow_start(self.flow, [], [self.contact], restart_participants=True)

        run1.refresh_from_db()
        run3.refresh_from_db()

        # should have two flow runs for this contact and flow
        self.assertFalse(run1.is_active)
        self.assertTrue(run3.is_active)

        self.assertEqual(len(run1.path), 2)
        self.assertEqual(len(run3.path), 2)

        # send in a message, this should be handled by our first flow, which has a more recent run active
        incoming = self.create_msg(direction=INCOMING, contact=self.contact, text="blue")
        self.assertTrue(legacy.find_and_handle(incoming)[0])

        run1.refresh_from_db()
        run3.refresh_from_db()

        self.assertEqual(len(run1.path), 2)
        self.assertEqual(len(run3.path), 3)

        # if we exclude existing and try starting again, nothing happens
        legacy.flow_start(self.flow, [], [self.contact], restart_participants=False)

        # no new runs
        self.assertEqual(self.flow.runs.count(), 2)

        # check our run results
        results = self.flow.runs.order_by("-id").first().results

        self.assertEqual(len(results), 1)
        self.assertEqual(results["color"]["name"], "color")
        self.assertEqual(results["color"]["category"], "Blue")
        self.assertEqual(results["color"]["value"], "blue")
        self.assertEqual(results["color"]["input"], incoming.text)


class FlowCRUDLTest(TembaTest):
    def test_broadcast(self):
        contact = self.create_contact("Bob", number="+593979099111")
        flow = self.get_flow("color")

        self.login(self.admin)

        response = self.client.get(reverse("flows.flow_broadcast", args=[flow.id]))

        self.assertEqual(
            ["omnibox", "restart_participants", "include_active", "loc"], list(response.context["form"].fields.keys())
        )

        # create flow start with restart_participants and include_active both enabled
        with patch("temba.mailroom.queue_flow_start") as mock_queue_flow_start:
            self.client.post(
                reverse("flows.flow_broadcast", args=[flow.id]),
                {"omnibox": "c-%s" % contact.uuid, "restart_participants": "on", "include_active": "on"},
                follow=True,
            )

            start = FlowStart.objects.get()
            self.assertEqual({contact}, set(start.contacts.all()))
            self.assertEqual(flow, start.flow)
            self.assertEqual(FlowStart.STATUS_PENDING, start.status)
            self.assertTrue(start.restart_participants)
            self.assertTrue(start.include_active)

            mock_queue_flow_start.assert_called_once_with(start)

        FlowStart.objects.all().delete()

        # create flow start with restart_participants and include_active both enabled
        with patch("temba.mailroom.queue_flow_start") as mock_queue_flow_start:
            self.client.post(
                reverse("flows.flow_broadcast", args=[flow.id]), {"omnibox": "c-%s" % contact.uuid}, follow=True
            )

            start = FlowStart.objects.get()
            self.assertEqual({contact}, set(start.contacts.all()))
            self.assertEqual(flow, start.flow)
            self.assertEqual(FlowStart.STATUS_PENDING, start.status)
            self.assertFalse(start.restart_participants)
            self.assertFalse(start.include_active)

            mock_queue_flow_start.assert_called_once_with(start)

        # trying to start again should fail because there is already a pending start for this flow
        with patch("temba.mailroom.queue_flow_start") as mock_queue_flow_start:
            response = self.client.post(
                reverse("flows.flow_broadcast", args=[flow.id]), {"omnibox": "c-%s" % contact.uuid}, follow=True
            )

            # should have an error now
            self.assertTrue(response.context["form"].errors)

            # shouldn't have a new flow start as validation failed
            self.assertFalse(FlowStart.objects.filter(flow=flow).exclude(id__lte=start.id))

            mock_queue_flow_start.assert_not_called()


class FlowRunTest(TembaTest):
    def setUp(self):
        super().setUp()

        self.flow = self.get_flow("color")
        self.contact = self.create_contact("Ben Haggerty", "+250788123123")

    def test_run_release(self):
        run = FlowRun.create(self.flow, self.contact)

        # our run go bye bye
        run.release()

        self.assertFalse(FlowRun.objects.filter(id=run.id).exists())

    def test_session_release(self):
        # create some runs that have sessions
        run1 = FlowRun.create(self.flow, self.contact, session=FlowSession.create(self.contact, None))
        run2 = FlowRun.create(self.flow, self.contact, session=FlowSession.create(self.contact, None))
        run3 = FlowRun.create(self.flow, self.contact, session=FlowSession.create(self.contact, None))

        # create an IVR run and session
        connection = IVRCall.objects.create(
            channel=self.channel,
            contact=self.contact,
            contact_urn=self.contact.urns.get(),
            direction=IVRCall.OUTGOING,
            org=self.org,
            status=IVRCall.PENDING,
        )
        session = FlowSession.create(self.contact, connection)
        run4 = FlowRun.create(self.flow, self.contact, connection=connection, session=session)

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

        trim_flow_sessions()

        run1, run2, run3, run4 = FlowRun.objects.order_by("id")

        self.assertIsNone(run1.session)
        self.assertIsNotNone(run2.session)  # ended too recently to be deleted
        self.assertIsNotNone(run3.session)  # never ended
        self.assertIsNone(run4.session)
        self.assertIsNotNone(run4.connection)  # channel session unaffected

        # only sessions for run2 and run3 are left
        self.assertEqual(FlowSession.objects.count(), 2)


class FlowLabelTest(FlowFileTest):
    def test_label_model(self):
        # test a the creation of a unique label when we have a long word(more than 32 caracters)
        response = FlowLabel.create_unique("alongwordcomposedofmorethanthirtytwoletters", self.org, parent=None)
        self.assertEqual(response.name, "alongwordcomposedofmorethanthirt")

        # try to create another label which starts with the same 32 caracteres
        # the one we already have
        label = FlowLabel.create_unique("alongwordcomposedofmorethanthirtytwocaracteres", self.org, parent=None)

        self.assertEqual(label.name, "alongwordcomposedofmorethanthi 2")
        self.assertEqual(str(label), "alongwordcomposedofmorethanthi 2")
        label = FlowLabel.create_unique("child", self.org, parent=label)
        self.assertEqual(str(label), "alongwordcomposedofmorethanthi 2 > child")

        FlowLabel.create_unique("dog", self.org)
        FlowLabel.create_unique("dog", self.org)
        dog3 = FlowLabel.create_unique("dog", self.org)
        self.assertEqual("dog 3", dog3.name)

        dog4 = FlowLabel.create_unique("dog ", self.org)
        self.assertEqual("dog 4", dog4.name)

        # view the parent label, should see the child
        self.login(self.admin)
        favorites = self.get_flow("favorites")
        label.toggle_label([favorites], True)
        response = self.client.get(reverse("flows.flow_filter", args=[label.pk]))
        self.assertTrue(response.context["object_list"])
        # our child label
        self.assertContains(response, "child")

        # and the edit gear link
        self.assertContains(response, "Edit")

        favorites.is_active = False
        favorites.save()

        response = self.client.get(reverse("flows.flow_filter", args=[label.pk]))
        self.assertFalse(response.context["object_list"])

    def test_toggle_label(self):
        label = FlowLabel.create_unique("toggle me", self.org)
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
        label_one = FlowLabel.create_unique("label1", self.org)

        delete_url = reverse("flows.flowlabel_delete", args=[label_one.pk])

        self.other_user = self.create_user("ironman")

        self.login(self.other_user)
        response = self.client.get(delete_url)
        self.assertEqual(response.status_code, 302)

        self.login(self.admin)
        response = self.client.get(delete_url)
        self.assertEqual(response.status_code, 200)

    def test_update(self):
        label_one = FlowLabel.create_unique("label1", self.org)
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


class SimulationTest(FlowFileTest):
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
                    mock_post.call_args[1]["json"]["trigger"],
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
                    },
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
                actual_payload = mock_post.call_args_list[0][1]["json"]
                actual_headers = mock_post.call_args_list[0][1]["headers"]

                self.assertEqual(actual_url, "https://mailroom.temba.io/mr/sim/start")
                self.assertEqual(actual_payload["org_id"], flow.org_id)
                self.assertEqual(actual_payload["trigger"]["environment"]["date_format"], "DD-MM-YYYY")
                self.assertEqual(len(actual_payload["assets"]["channels"]), 1)  # fake channel
                self.assertEqual(len(actual_payload["flows"]), 1)
                self.assertEqual(actual_headers["Authorization"], "Token sesame")

            # try a resume
            payload = dict(version=2, session={}, resume={}, flow={})

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
                actual_payload = mock_post.call_args_list[0][1]["json"]
                actual_headers = mock_post.call_args_list[0][1]["headers"]

                self.assertEqual(actual_url, "https://mailroom.temba.io/mr/sim/resume")
                self.assertEqual(actual_payload["org_id"], flow.org_id)
                self.assertEqual(actual_payload["resume"]["environment"]["date_format"], "DD-MM-YYYY")
                self.assertEqual(len(actual_payload["assets"]["channels"]), 1)  # fake channel
                self.assertEqual(len(actual_payload["flows"]), 1)
                self.assertEqual(actual_headers["Authorization"], "Token sesame")


class FlowsTest(FlowFileTest):
    def run_flowrun_deletion(self, delete_reason, test_cases):
        """
        Runs our favorites flow, then releases the run with the passed in delete_reason, asserting our final
        state with test_cases.
        """
        favorites = self.get_flow("favorites")
        action_set1, action_set3, action_set3 = favorites.action_sets.order_by("y")[:3]
        rule_set1, rule_set2 = favorites.rule_sets.order_by("y")[:2]

        start = FlowStart.create(favorites, self.admin, contacts=[self.contact])
        legacy.flow_start_start(start)

        Msg.create_incoming(self.channel, "tel:+12065552020", "I like red")
        Msg.create_incoming(self.channel, "tel:+12065552020", "primus")
        Msg.create_incoming(self.channel, "tel:+12065552020", "Ben")

        run = FlowRun.objects.get(flow=favorites, contact=self.contact)
        run.release(delete_reason)

        recent = FlowPathRecentRun.get_recent([action_set1.exit_uuid], rule_set1.uuid)
        self.assertEqual(len(recent), 0)

        cat_counts = {c["key"]: c for c in favorites.get_category_counts()["counts"]}
        self.assertEqual(len(cat_counts), 2)
        self.assertEqual(cat_counts["color"]["categories"][0]["count"], test_cases["red_count"])
        self.assertEqual(cat_counts["color"]["categories"][0]["count"], test_cases["primus_count"])

        self.assertEqual(FlowStartCount.get_count(start), test_cases["start_count"])
        self.assertEqual(FlowRunCount.get_totals(favorites), test_cases["run_count"])

    @uses_legacy_engine
    def test_deletion(self):
        self.run_flowrun_deletion(
            None, {"red_count": 0, "primus_count": 0, "start_count": 0, "run_count": {"C": 0, "E": 0, "I": 0, "A": 0}}
        )

    @uses_legacy_engine
    def test_user_deletion(self):
        self.run_flowrun_deletion(
            "U", {"red_count": 0, "primus_count": 0, "start_count": 0, "run_count": {"C": 0, "E": 0, "I": 0, "A": 0}}
        )

    @uses_legacy_engine
    def test_archiving(self):
        self.run_flowrun_deletion(
            "A", {"red_count": 1, "primus_count": 1, "start_count": 1, "run_count": {"C": 1, "E": 0, "I": 0, "A": 0}}
        )

    @uses_legacy_engine
    def test_simple(self):
        favorites = self.get_flow("favorites")
        action_set1, action_set3, action_set3 = favorites.action_sets.order_by("y")[:3]
        rule_set1, rule_set2 = favorites.rule_sets.order_by("y")[:2]
        red_rule = rule_set1.rules[0]

        run, = legacy.flow_start(favorites, [], [self.contact])

        msg1 = Msg.objects.get()
        self.assertEqual(msg1.direction, "O")
        self.assertEqual(msg1.text, "What is your favorite color?")
        self.assertEqual(msg1.contact, self.contact)

        self.assertEqual(run.contact, self.contact)
        self.assertIsNone(run.exit_type)
        self.assertIsNone(run.exited_on)
        self.assertFalse(run.responded)

        self.assertEqual(FlowNodeCount.get_totals(favorites), {rule_set1.uuid: 1})
        self.assertEqual(FlowPathCount.get_totals(favorites), {action_set1.exit_uuid + ":" + rule_set1.uuid: 1})
        self.assertEqual(FlowCategoryCount.objects.count(), 0)

        recent = FlowPathRecentRun.get_recent([action_set1.exit_uuid], rule_set1.uuid)
        self.assertEqual(len(recent), 1)
        self.assertEqual(recent[0]["run"], run)
        self.assertEqual(recent[0]["text"], "What is your favorite color?")

        msg2 = Msg.create_incoming(
            self.channel, "tel:+12065552020", "I like red", attachments=["image/jpeg:http://example.com/test.jpg"]
        )

        run.refresh_from_db()
        self.assertIsNone(run.exit_type)
        self.assertIsNone(run.exited_on)
        self.assertTrue(run.responded)
        self.assertEqual(
            run.results,
            {
                "color": {
                    "category": "Red",
                    "node_uuid": str(rule_set1.uuid),
                    "name": "Color",
                    "value": "red",
                    "created_on": matchers.ISODate(),
                    "input": "I like red\nhttp://example.com/test.jpg",
                }
            },
        )
        self.assertEqual(
            run.path,
            [
                {
                    "uuid": matchers.UUID4String(),
                    "node_uuid": str(action_set1.uuid),
                    "arrived_on": matchers.ISODate(),
                    "exit_uuid": str(action_set1.exit_uuid),
                },
                {
                    "uuid": matchers.UUID4String(),
                    "node_uuid": str(rule_set1.uuid),
                    "arrived_on": matchers.ISODate(),
                    "exit_uuid": str(red_rule["uuid"]),
                },
                {
                    "uuid": matchers.UUID4String(),
                    "node_uuid": str(action_set3.uuid),
                    "arrived_on": matchers.ISODate(),
                    "exit_uuid": str(action_set3.exit_uuid),
                },
                {"uuid": matchers.UUID4String(), "node_uuid": str(rule_set2.uuid), "arrived_on": matchers.ISODate()},
            ],
        )

        self.assertEqual(
            run.events,
            [
                {
                    "type": "msg_created",
                    "created_on": matchers.ISODate(),
                    "step_uuid": run.path[0]["uuid"],
                    "msg": {
                        "uuid": str(msg1.uuid),
                        "text": "What is your favorite color?",
                        "urn": "tel:+12065552020",
                        "channel": {"uuid": str(self.channel.uuid), "name": "Test Channel"},
                    },
                },
                {
                    "type": "msg_received",
                    "created_on": matchers.ISODate(),
                    "step_uuid": run.path[1]["uuid"],
                    "msg": {
                        "uuid": str(msg2.uuid),
                        "text": "I like red",
                        "attachments": ["image/jpeg:http://example.com/test.jpg"],
                        "urn": "tel:+12065552020",
                        "channel": {"uuid": str(self.channel.uuid), "name": "Test Channel"},
                    },
                },
                {
                    "type": "msg_created",
                    "created_on": matchers.ISODate(),
                    "step_uuid": run.path[2]["uuid"],
                    "msg": {
                        "uuid": matchers.UUID4String(),
                        "text": "Good choice, I like Red too! What is your favorite beer?",
                        "urn": "tel:+12065552020",
                        "channel": {"uuid": str(self.channel.uuid), "name": "Test Channel"},
                    },
                },
            ],
        )

        cat_counts = list(FlowCategoryCount.objects.order_by("id"))
        self.assertEqual(len(cat_counts), 1)
        self.assertEqual(cat_counts[0].result_name, "Color")
        self.assertEqual(cat_counts[0].category_name, "Red")
        self.assertEqual(cat_counts[0].count, 1)

        msg3 = Msg.objects.get(id__gt=msg2.id)
        self.assertEqual(msg3.direction, "O")
        self.assertEqual(msg3.text, "Good choice, I like Red too! What is your favorite beer?")

        msg4 = Msg.create_incoming(self.channel, "tel:+12065552020", "primus")

        run.refresh_from_db()
        self.assertEqual(
            run.results,
            {
                "color": {
                    "category": "Red",
                    "node_uuid": str(rule_set1.uuid),
                    "name": "Color",
                    "value": "red",
                    "created_on": matchers.ISODate(),
                    "input": "I like red\nhttp://example.com/test.jpg",
                },
                "beer": {
                    "category": "Primus",
                    "node_uuid": matchers.UUID4String(),
                    "name": "Beer",
                    "value": "primus",
                    "created_on": matchers.ISODate(),
                    "input": "primus",
                },
            },
        )

        msg5 = Msg.objects.get(id__gt=msg4.id)
        self.assertEqual(msg5.direction, "O")
        self.assertEqual(
            msg5.text, "Mmmmm... delicious Primus. If only they made red Primus! Lastly, what is your name?"
        )

        msg6 = Msg.create_incoming(self.channel, "tel:+12065552020", "Ben")

        msg7 = Msg.objects.get(id__gt=msg6.id)
        self.assertEqual(msg7.direction, "O")
        self.assertEqual(msg7.text, "Thanks Ben, we are all done!")

        run.refresh_from_db()
        self.assertEqual(run.exit_type, FlowRun.EXIT_TYPE_COMPLETED)
        self.assertIsNotNone(run.exited_on)

    @uses_legacy_engine
    def test_category_merging(self):
        favorites = self.get_flow("favorites")
        action_set1, action_set3, action_set3 = favorites.action_sets.order_by("y")[:3]
        rule_set1, rule_set2 = favorites.rule_sets.order_by("y")[:2]
        navy_rule = rule_set1.rules[3]

        run, = legacy.flow_start(favorites, [], [self.contact])
        Msg.create_incoming(self.channel, "tel:+12065552020", "navy")

        run.refresh_from_db()
        self.assertEqual(
            run.results,
            {
                "color": {
                    "category": "Blue",  # navy rule uses blue category
                    "node_uuid": str(rule_set1.uuid),
                    "name": "Color",
                    "value": "navy",
                    "created_on": matchers.ISODate(),
                    "input": "navy",
                }
            },
        )
        self.assertEqual(
            run.path[:2],
            [
                {
                    "uuid": matchers.UUID4String(),
                    "node_uuid": str(action_set1.uuid),
                    "arrived_on": matchers.ISODate(),
                    "exit_uuid": str(action_set1.exit_uuid),
                },
                {
                    "uuid": matchers.UUID4String(),
                    "node_uuid": str(rule_set1.uuid),
                    "arrived_on": matchers.ISODate(),
                    "exit_uuid": str(navy_rule["uuid"]),
                },
            ],
        )

    @uses_legacy_engine
    def test_terminal_nodes(self):
        flow = self.get_flow("terminal_nodes")
        action_set1, action_set2 = flow.action_sets.order_by("y")
        rule_set1, rule_set2 = flow.rule_sets.order_by("y")
        ben_rule = rule_set2.rules[0]

        run, = legacy.flow_start(flow, [], [self.contact])

        # answer with A to first ruleset which is Action (A) or Ruleset (R)
        Msg.create_incoming(self.channel, "tel:+12065552020", "A")
        run.refresh_from_db()

        self.assertIsNotNone(run.exited_on)
        self.assertEqual(run.exit_type, "C")
        self.assertEqual(run.path[2]["exit_uuid"], str(action_set2.exit_uuid))

        run, = legacy.flow_start(flow, [], [self.contact], restart_participants=True)

        # this time choose to end on a non-waiting ruleset
        Msg.create_incoming(self.channel, "tel:+12065552020", "R")
        run.refresh_from_db()

        self.assertIsNotNone(run.exited_on)
        self.assertEqual(run.exit_type, "C")
        self.assertEqual(run.path[2]["exit_uuid"], str(ben_rule["uuid"]))

    @uses_legacy_engine
    def test_resuming_run_with_old_uuidless_message(self):
        favorites = self.get_flow("favorites")
        run, = legacy.flow_start(favorites, [], [self.contact])

        Msg.create_incoming(self.channel, "tel:+12065552020", "I like red")

        # old messages don't have UUIDs so their events on the run also won't
        run.refresh_from_db()
        del run.events[1]["msg"]["uuid"]
        run.save(update_fields=("events",))

        Msg.create_incoming(self.channel, "tel:+12065552020", "primus")
        Msg.create_incoming(self.channel, "tel:+12065552020", "Ben")

        run.refresh_from_db()
        self.assertEqual(run.exit_type, FlowRun.EXIT_TYPE_COMPLETED)

    def test_validate_legacy_definition(self):

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

    @uses_legacy_engine
    def test_send_msg_to_urnless(self):
        flow = self.get_flow("send_msg_to_urnless")
        legacy.flow_start(flow, [], [self.contact])

        # check we create the contact
        self.assertIsNotNone(Contact.objects.get(name="Bob"))

        # even tho we can't send them the message until they get some URNs
        self.assertEqual(Msg.objects.count(), 0)

    @uses_legacy_engine
    def test_sms_forms(self):
        flow = self.get_flow("sms_form")

        def assert_response(message, response):
            self.assertEqual(response, self.send_message(flow, message, restart_participants=True))

        # invalid age
        assert_response("101 M Seattle", "Sorry, 101 doesn't look like a valid age, please try again.")

        # invalid gender
        assert_response("36 elephant Seattle", "Sorry, elephant doesn't look like a valid gender. Try again.")

        # invalid location
        assert_response("36 M Saturn", "I don't know the location Saturn. Please try again.")

        # some missing fields
        assert_response("36", "Sorry,  doesn't look like a valid gender. Try again.")
        assert_response("36 M", "I don't know the location . Please try again.")
        assert_response("36 M pequeño", "I don't know the location pequeño. Please try again.")

        # valid entry
        assert_response("36 M Seattle", "Thanks for your submission. We have that as:\n\n36 / M / Seattle")

        # valid entry with extra spaces
        assert_response("36   M  Seattle", "Thanks for your submission. We have that as:\n\n36 / M / Seattle")

        for delimiter in ["+", "."]:
            # now let's switch to pluses and make sure they do the right thing
            for ruleset in flow.rule_sets.filter(ruleset_type="form_field"):
                config = ruleset.config
                config["field_delimiter"] = delimiter
                ruleset.config = config
                ruleset.save()

            ctx = dict(delim=delimiter)

            assert_response(
                "101%(delim)sM%(delim)sSeattle" % ctx, "Sorry, 101 doesn't look like a valid age, please try again."
            )
            assert_response(
                "36%(delim)selephant%(delim)sSeattle" % ctx,
                "Sorry, elephant doesn't look like a valid gender. Try again.",
            )
            assert_response("36%(delim)sM%(delim)sSaturn" % ctx, "I don't know the location Saturn. Please try again.")
            assert_response(
                "36%(delim)sM%(delim)sSeattle" % ctx,
                "Thanks for your submission. We have that as:\n\n36 / M / Seattle",
            )
            assert_response(
                "15%(delim)sM%(delim)spequeño" % ctx, "I don't know the location pequeño. Please try again."
            )

    @skip_if_no_mailroom
    def test_create_dependencies(self):
        self.login(self.admin)

        flow = self.get_flow("favorites")
        flow_json = flow.as_json()

        # create an invalid label in our first actionset
        flow_json["action_sets"][0]["actions"].append(
            {
                "type": "add_label",
                "uuid": "aafe958f-899c-42db-8dae-e2c797767d2a",
                "labels": [{"uuid": "fake uuid", "name": "Foo zap"}],
            }
        )

        response = self.client.post(
            reverse("flows.flow_json", args=[flow.uuid]), data=flow_json, content_type="application/json"
        )

        self.assertEqual(response.status_code, 200)

        # make sure our revision doesn't have our fake uuid
        label = Label.all_objects.get(name="Foo zap")
        self.assertTrue(flow.revisions.filter(definition__contains=str(label.uuid)).last())

    @skip_if_no_mailroom
    def test_save_definitions(self):
        self.login(self.admin)

        # old flow definition
        self.client.post(
            reverse("flows.flow_create"),
            data=dict(name="Normal Flow", flow_type=Flow.TYPE_MESSAGE, editor_version="1"),
        )
        flow = Flow.objects.get(
            org=self.org, name="Normal Flow", flow_type=Flow.TYPE_MESSAGE, version_number=Flow.FINAL_LEGACY_VERSION
        )

        # old editor
        response = self.client.get(reverse("flows.flow_editor", args=[flow.uuid]))
        self.assertNotRedirect(response, reverse("flows.flow_editor_next", args=[flow.uuid]))

        # new flow definition
        self.client.post(
            reverse("flows.flow_create"), data=dict(name="Go Flow", flow_type=Flow.TYPE_MESSAGE, editor_version="0")
        )
        flow = Flow.objects.get(
            org=self.org, name="Go Flow", flow_type=Flow.TYPE_MESSAGE, version_number=Flow.GOFLOW_VERSION
        )

        # now loading the editor page should redirect
        response = self.client.get(reverse("flows.flow_editor", args=[flow.uuid]))
        self.assertRedirect(response, reverse("flows.flow_editor_next", args=[flow.uuid]))

    def test_save_revision(self):
        self.login(self.admin)
        self.client.post(
            reverse("flows.flow_create"), data=dict(name="Go Flow", flow_type=Flow.TYPE_MESSAGE, editor_version="0")
        )
        flow = Flow.objects.get(
            org=self.org, name="Go Flow", flow_type=Flow.TYPE_MESSAGE, version_number=Flow.GOFLOW_VERSION
        )

        # can't save old version over new
        definition = flow.revisions.all().order_by("-id").first().definition
        definition["spec_version"] = Flow.FINAL_LEGACY_VERSION
        with self.assertRaises(FlowVersionConflictException):
            flow.save_revision(flow.created_by, definition)

        # can't save old revision over new
        definition["spec_version"] = Flow.GOFLOW_VERSION
        definition["revision"] = 0
        with self.assertRaises(FlowUserConflictException):
            flow.save_revision(flow.created_by, definition)

    @skip_if_no_mailroom
    def test_save_contact_does_not_update_field_label(self):
        self.login(self.admin)

        rank_field = ContactField.get_or_create(
            self.org, self.admin, "rank", "Commander ranking", value_type=Value.TYPE_NUMBER
        )

        self.assertEqual(rank_field.label, "Commander ranking")

        flow = self.get_flow("favorites")
        flow_json = flow.as_json()

        # save some data to the field
        flow_json["action_sets"][0]["actions"].append(
            {
                "type": "save",
                "uuid": "aafe958f-899c-42db-8dae-e2c797767d2a",
                "label": "Rank label",
                "field": "rank",
                "value": "@flow.response_1.text",
            }
        )

        # add a new field
        flow_json["action_sets"][0]["actions"].append(
            {
                "type": "save",
                "uuid": "aafe958f-899c-42db-8dae-e2c797767d2b",
                "label": "New field label",
                "field": "new_field",
                "value": "@flow.response_1.text",
            }
        )

        response = self.client.post(
            reverse("flows.flow_json", args=[flow.uuid]), data=flow_json, content_type="application/json"
        )

        self.assertEqual(response.status_code, 200)

        rank_field.refresh_from_db()

        # the label should not be changed
        self.assertEqual(rank_field.label, "Commander ranking")

        # new field is created
        new_field = ContactField.user_fields.get(key="new_field")
        self.assertEqual(new_field.label, "New field label")

    def test_write_protection(self):
        flow = self.get_flow("favorites")
        flow_json = flow.as_json()

        self.login(self.admin)

        # saving should work
        flow.update(flow_json, self.admin)

        # but if we save from in the past after our save it should fail
        with self.assertRaises(FlowUserConflictException):
            flow.update(flow_json, self.admin)

        # check view sends converts exception to error response
        response = self.client.post(
            reverse("flows.flow_json", args=[flow.uuid]), data=flow_json, content_type="application/json"
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(
            response.json(),
            {
                "description": "Administrator is currently editing this Flow. "
                "Your changes will not be saved until you refresh your browser.",
                "status": "failure",
            },
        )

        # we should also fail if we try saving an old spec version from the editor
        flow.refresh_from_db()
        flow_json = flow.as_json()

        with patch("temba.flows.models.Flow.FINAL_LEGACY_VERSION", "1.234"):

            with self.assertRaises(FlowVersionConflictException):
                flow.update(flow_json, self.admin)

            # check view sends converts exception to error response
            response = self.client.post(
                reverse("flows.flow_json", args=[flow.uuid]), data=flow_json, content_type="application/json"
            )

            self.assertEqual(response.status_code, 400)
            self.assertEqual(
                response.json(),
                {
                    "description": "Your flow has been upgraded to the latest version. "
                    "In order to continue editing, please refresh your browser.",
                    "status": "failure",
                },
            )

        # check that flow validation failing is returned as an error message to the user
        flow_json["action_sets"][0]["uuid"] = flow_json["action_sets"][1]["uuid"]

        with self.assertRaises(FlowValidationException):
            flow.update(flow_json, self.admin)

        # check view sends converts exception to error response
        response = self.client.post(
            reverse("flows.flow_json", args=[flow.uuid]), data=flow_json, content_type="application/json"
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(
            response.json(),
            {"description": "Your flow failed validation. Please refresh your browser.", "status": "failure"},
        )

        # create an invalid loop in the flow definition
        flow_json = flow.as_json()
        flow_json["action_sets"][0]["destination"] = flow_json["action_sets"][0]["uuid"]

        with self.assertRaises(FlowInvalidCycleException):
            flow.update(flow_json, self.admin)

        # check view sends converts exception to error response
        response = self.client.post(
            reverse("flows.flow_json", args=[flow.uuid]), data=flow_json, content_type="application/json"
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(
            response.json(),
            {"description": "Your flow contains an invalid loop. Please refresh your browser.", "status": "failure"},
        )

    @uses_legacy_engine
    def test_flow_category_counts(self):
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

        favorites = self.get_flow("favorites")

        # add in some fake data
        for i in range(0, 10):
            contact = self.create_contact("Contact %d" % i, "+120655530%d" % i)
            self.send_message(favorites, "blue", contact=contact)
            self.send_message(favorites, "primus", contact=contact)
            self.send_message(favorites, "russell", contact=contact)

        for i in range(0, 5):
            contact = self.create_contact("Contact %d" % i, "+120655531%d" % i)
            self.send_message(favorites, "red", contact=contact)
            self.send_message(favorites, "primus", contact=contact)
            self.send_message(favorites, "earl", contact=contact)

        # test update flow values
        for i in range(0, 5):
            contact = self.create_contact("Contact %d" % i, "+120655532%d" % i)
            self.send_message(favorites, "orange", contact=contact)
            self.send_message(favorites, "green", contact=contact)
            self.send_message(favorites, "skol", contact=contact)
            self.send_message(favorites, "bobby", contact=contact)

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
        flow_json = favorites.as_json()
        color_ruleset = flow_json["rule_sets"][0]
        flow_json = json.loads(json.dumps(flow_json).replace(color_ruleset["uuid"], str(uuid4())))
        favorites.update(flow_json)

        # send a few more runs through our updated flow
        for i in range(0, 3):
            contact = self.create_contact("Contact %d" % i, "+120655533%d" % i)
            self.send_message(favorites, "red", contact=contact)
            self.send_message(favorites, "turbo", contact=contact)

        # should now have three more reds
        counts = favorites.get_category_counts()
        assertCount(counts, "color", "Red", 8)
        assertCount(counts, "beer", "Turbo King", 3)

        # now delete the color ruleset and repoint nodes to the beer ruleset
        color_ruleset = flow_json["rule_sets"][0]
        beer_ruleset = flow_json["rule_sets"][1]
        flow_json["rule_sets"] = flow_json["rule_sets"][1:]

        for actionset in flow_json["action_sets"]:
            if actionset["destination"] == color_ruleset["uuid"]:
                actionset["destination"] = beer_ruleset["uuid"]

        favorites.update(flow_json)

        # now the color counts have been removed, but beer is still there
        counts = favorites.get_category_counts()
        self.assertNotIn("color", counts)
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
        self.releaseRuns()

        counts = favorites.get_category_counts()
        assertCount(counts, "beer", "Turbo King", 0)

    @uses_legacy_engine
    def test_flow_results(self):
        favorites = self.get_flow("favorites")

        with patch("temba.flows.views.FlowCRUDL.RunTable.paginate_by", 1):

            pete = self.create_contact("Pete", "+12065553027")
            self.send_message(favorites, "blue", contact=pete)

            jimmy = self.create_contact("Jimmy", "+12065553026")
            self.send_message(favorites, "red", contact=jimmy)
            self.send_message(favorites, "turbo", contact=jimmy)

            self.login(self.admin)
            response = self.client.get(reverse("flows.flow_results", args=[favorites.uuid]))

            # the rulesets should be present as column headers
            self.assertContains(response, "Beer")
            self.assertContains(response, "Color")
            self.assertContains(response, "Name")

            # fetch counts endpoint, should have 2 color results (one is a test contact)
            response = self.client.get(reverse("flows.flow_category_counts", args=[favorites.uuid]))
            counts = response.json()["counts"]
            self.assertEqual("Color", counts[0]["name"])
            self.assertEqual(2, counts[0]["total"])

            # test a search on our runs
            with patch.object(Contact, "query_elasticsearch_for_ids", return_value=[pete.id]):
                response = self.client.get("%s?q=pete" % reverse("flows.flow_run_table", args=[favorites.pk]))
                self.assertEqual(len(response.context["runs"]), 1)
                self.assertContains(response, "Pete")
                self.assertNotContains(response, "Jimmy")

            # fetch our intercooler rows for the run table
            response = self.client.get(reverse("flows.flow_run_table", args=[favorites.pk]))
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
            response = self.client.get(reverse("flows.flow_activity_chart", args=[favorites.pk]))

            # we have two active runs
            self.assertContains(response, "name: 'Active', y: 2")
            self.assertContains(response, "3 Responses")

            # now send another message
            self.send_message(favorites, "primus", contact=pete)
            self.send_message(favorites, "Pete", contact=pete)

            response = self.client.get(reverse("flows.flow_run_table", args=[favorites.pk]))
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

            # now only one active, one completed, and 5 total responses
            response = self.client.get(reverse("flows.flow_activity_chart", args=[favorites.pk]))
            self.assertContains(response, "name: 'Active', y: 1")
            self.assertContains(response, "name: 'Completed', y: 1")
            self.assertContains(response, "5 Responses")

            # they all happened on the same day
            response = self.client.get(reverse("flows.flow_activity_chart", args=[favorites.pk]))
            points = response.context["histogram"]
            self.assertEqual(1, len(points))

            # put one of our counts way in the past so we get a different histogram scale
            count = FlowPathCount.objects.filter(flow=favorites).order_by("id")[1]
            count.period = count.period - timedelta(days=25)
            count.save()
            response = self.client.get(reverse("flows.flow_activity_chart", args=[favorites.pk]))
            points = response.context["histogram"]
            self.assertTrue(timedelta(days=24) < (points[1]["bucket"] - points[0]["bucket"]))

            # pick another scale
            count.period = count.period - timedelta(days=600)
            count.save()
            response = self.client.get(reverse("flows.flow_activity_chart", args=[favorites.pk]))

            # this should give us a more compressed histogram
            points = response.context["histogram"]
            self.assertTrue(timedelta(days=620) < (points[1]["bucket"] - points[0]["bucket"]))

            self.assertEqual(24, len(response.context["hod"]))
            self.assertEqual(7, len(response.context["dow"]))

        # delete a run
        with patch("temba.flows.views.FlowCRUDL.RunTable.paginate_by", 100):
            response = self.client.get(reverse("flows.flow_run_table", args=[favorites.pk]))
            self.assertEqual(len(response.context["runs"]), 2)

            self.client.post(reverse("flows.flowrun_delete", args=[response.context["runs"][0].id]))
            response = self.client.get(reverse("flows.flow_run_table", args=[favorites.pk]))
            self.assertEqual(len(response.context["runs"]), 1)

        with patch("temba.flows.views.FlowCRUDL.RunTable.paginate_by", 1):

            # create one empty run
            FlowRun.objects.create(org=favorites.org, flow=favorites, contact=pete, responded=True)

            # fetch our intercooler rows for the run table
            response = self.client.get(reverse("flows.flow_run_table", args=[favorites.pk]))
            self.assertEqual(len(response.context["runs"]), 1)
            self.assertEqual(200, response.status_code)

        with patch("temba.flows.views.FlowCRUDL.RunTable.paginate_by", 1):

            # create one empty run
            FlowRun.objects.create(org=favorites.org, flow=favorites, contact=pete, responded=False)

            # fetch our intercooler rows for the run table
            response = self.client.get("%s?responded=bla" % reverse("flows.flow_run_table", args=[favorites.pk]))
            self.assertEqual(len(response.context["runs"]), 1)
            self.assertEqual(200, response.status_code)

            response = self.client.get("%s?responded=true" % reverse("flows.flow_run_table", args=[favorites.pk]))
            self.assertEqual(len(response.context["runs"]), 1)

        # make sure we show results for flows with only expression splits
        RuleSet.objects.filter(flow=favorites).update(ruleset_type=RuleSet.TYPE_EXPRESSION)
        response = self.client.get(reverse("flows.flow_activity_chart", args=[favorites.pk]))

        self.assertEqual(24, len(response.context["hod"]))
        self.assertEqual(7, len(response.context["dow"]))

    def test_flow_activity_chart_of_inactive_flow(self):
        flow = self.get_flow("favorites")
        # release the flow
        flow.release()

        self.login(self.admin)
        response = self.client.get(reverse("flows.flow_activity_chart", args=[flow.pk]))

        self.assertEqual(response.status_code, 404)

    def test_flow_run_table_of_inactive_flow(self):
        flow = self.get_flow("favorites")
        # release the flow
        flow.release()

        self.login(self.admin)
        response = self.client.get(reverse("flows.flow_run_table", args=[flow.pk]))

        self.assertEqual(response.status_code, 404)

    def test_flow_category_counts_of_inactive_flow(self):
        flow = self.get_flow("favorites")
        # release the flow
        flow.release()

        self.login(self.admin)
        response = self.client.get(reverse("flows.flow_category_counts", args=[flow.uuid]))

        self.assertEqual(response.status_code, 404)

    @uses_legacy_engine
    def test_send_all_replies(self):
        flow = self.get_flow("send_all")

        contact = self.create_contact("Stephen", "+12078778899", twitter="stephen")
        legacy.flow_start(flow, groups=[], contacts=[contact], restart_participants=True)

        replies = Msg.objects.filter(contact=contact, direction="O")
        self.assertEqual(replies.count(), 1)
        self.assertIsNone(replies.filter(contact_urn__path="stephen").first())
        self.assertIsNotNone(replies.filter(contact_urn__path="+12078778899").first())

        Broadcast.objects.all().delete()
        Msg.objects.all().delete()

        # create twitter channel
        Channel.create(self.org, self.user, None, "TT")
        flow.org.clear_cached_schemes()

        legacy.flow_start(flow, groups=[], contacts=[contact], restart_participants=True)

        replies = Msg.objects.filter(contact=contact, direction="O")
        self.assertEqual(replies.count(), 2)
        self.assertIsNotNone(replies.filter(contact_urn__path="stephen").first())
        self.assertIsNotNone(replies.filter(contact_urn__path="+12078778899").first())

        Broadcast.objects.all().delete()
        Msg.objects.all().delete()

        flow = self.get_flow("two_to_all")
        legacy.flow_start(flow, groups=[], contacts=[contact], restart_participants=True)

        replies = Msg.objects.filter(contact=contact, direction="O")
        self.assertEqual(replies.count(), 4)
        self.assertEqual(replies.filter(contact_urn__path="stephen").count(), 2)
        self.assertEqual(replies.filter(contact_urn__path="+12078778899").count(), 2)

    @uses_legacy_engine
    def test_recent_messages(self):
        flow = self.get_flow("favorites")

        self.login(self.admin)
        recent_messages_url = reverse("flows.flow_recent_messages", args=[flow.uuid])

        color_prompt = ActionSet.objects.filter(flow=flow, y=0).first()
        color_ruleset = RuleSet.objects.filter(flow=flow, label="Color").first()
        blue_rule = color_ruleset.get_rules()[-4]
        navy_rule = color_ruleset.get_rules()[-3]
        other_rule = color_ruleset.get_rules()[-1]

        # URL params for different flow path segments
        entry_params = "?exits=%s,%s&to=%s" % (color_prompt.exit_uuid, color_prompt.uuid, color_ruleset.uuid)
        other_params = "?exits=%s&to=%s" % (other_rule.uuid, other_rule.destination)
        blue_params = "?exits=%s,%s&to=%s" % (blue_rule.uuid, navy_rule.uuid, blue_rule.destination)
        invalid_params = "?exits=%s&to=%s" % (color_ruleset.uuid, color_ruleset.uuid)

        def assert_recent(resp, msgs):
            self.assertEqual([r["text"] for r in resp.json()], msgs)

        # no params returns no results
        assert_recent(self.client.get(recent_messages_url), [])

        legacy.flow_start(flow, [], [self.contact])
        self.create_msg(direction=INCOMING, contact=self.contact, text="chartreuse").handle()

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

        self.create_msg(direction=INCOMING, contact=self.contact, text="mauve").handle()

        response = self.client.get(recent_messages_url + entry_params)
        assert_recent(response, ["What is your favorite color?"])

        response = self.client.get(recent_messages_url + other_params)
        assert_recent(response, ["mauve", "chartreuse"])

        response = self.client.get(recent_messages_url + blue_params)
        assert_recent(response, [])

        self.create_msg(direction=INCOMING, contact=self.contact, text="blue").handle()

        response = self.client.get(recent_messages_url + entry_params)
        assert_recent(response, ["What is your favorite color?"])

        response = self.client.get(recent_messages_url + other_params)
        assert_recent(response, ["mauve", "chartreuse"])

        response = self.client.get(recent_messages_url + blue_params)
        assert_recent(response, ["blue"])

    def test_completion(self):

        flow = self.get_flow("favorites")
        self.login(self.admin)

        response = self.client.get("%s?flow=%s" % (reverse("flows.flow_completion"), flow.uuid))
        response = response.json()

        def assert_in_response(response, data_key, key):
            found = False
            for item in response[data_key]:
                if key == item["name"]:
                    found = True
            self.assertTrue(found, "Key %s not found in %s" % (key, response))

        assert_in_response(response, "message_completions", "contact")
        assert_in_response(response, "message_completions", "contact.first_name")
        assert_in_response(response, "message_completions", "contact.tel")
        assert_in_response(response, "message_completions", "contact.mailto")

        assert_in_response(response, "message_completions", "parent.contact.uuid")
        assert_in_response(response, "message_completions", "child.contact.uuid")

        assert_in_response(response, "message_completions", "flow.color")
        assert_in_response(response, "message_completions", "flow.color.category")
        assert_in_response(response, "message_completions", "flow.color.text")
        assert_in_response(response, "message_completions", "flow.color.time")

        assert_in_response(response, "message_completions", "step")
        assert_in_response(response, "message_completions", "step.urn")
        assert_in_response(response, "message_completions", "step.urn.scheme")

        assert_in_response(response, "function_completions", "SUM")
        assert_in_response(response, "function_completions", "ABS")
        assert_in_response(response, "function_completions", "YEAR")

        # a Twitter channel
        Channel.create(self.org, self.user, None, "TT")

        response = self.client.get("%s?flow=%s" % (reverse("flows.flow_completion"), flow.uuid))
        response = response.json()

        assert_in_response(response, "message_completions", "contact.twitter")

    def test_squash_run_counts(self):
        flow = self.get_flow("favorites")
        flow2 = self.get_flow("pick_a_number")

        FlowRunCount.objects.create(flow=flow, count=2, exit_type=None)
        FlowRunCount.objects.create(flow=flow, count=1, exit_type=None)
        FlowRunCount.objects.create(flow=flow, count=3, exit_type="E")
        FlowRunCount.objects.create(flow=flow2, count=10, exit_type="I")
        FlowRunCount.objects.create(flow=flow2, count=-1, exit_type="I")

        squash_flowruncounts()
        self.assertEqual(FlowRunCount.objects.all().count(), 3)
        self.assertEqual(FlowRunCount.get_totals(flow2), {"A": 0, "C": 0, "E": 0, "I": 9})
        self.assertEqual(FlowRunCount.get_totals(flow), {"A": 3, "C": 0, "E": 3, "I": 0})

        max_id = FlowRunCount.objects.all().order_by("-id").first().id

        # no-op this time
        squash_flowruncounts()
        self.assertEqual(max_id, FlowRunCount.objects.all().order_by("-id").first().id)

    @uses_legacy_engine
    def test_activity(self):
        flow = self.get_flow("favorites")
        color_question = ActionSet.objects.get(y=0, flow=flow)
        other_action = ActionSet.objects.get(y=8, flow=flow)
        beer_question = ActionSet.objects.get(y=237, flow=flow)
        name_question = ActionSet.objects.get(y=535, flow=flow)
        end_prompt = ActionSet.objects.get(y=805, flow=flow)
        beer = RuleSet.objects.get(label="Beer", flow=flow)
        color = RuleSet.objects.get(label="Color", flow=flow)
        name = RuleSet.objects.get(label="Name", flow=flow)

        rules = color.get_rules()
        color_other_uuid = rules[-1].uuid
        color_blue_uuid = rules[-4].uuid

        # we don't know this shade of green, it should route us to the beginning again
        run1, = legacy.flow_start(flow, [], [self.contact])
        self.create_msg(direction=INCOMING, contact=self.contact, text="chartreuse").handle()

        (active, visited) = flow.get_activity()

        self.assertEqual(active, {color.uuid: 1})

        self.assertEqual(
            visited,
            {
                "%s:%s" % (color_question.exit_uuid, color.uuid): 1,
                "%s:%s" % (color_other_uuid, other_action.uuid): 1,
                "%s:%s" % (other_action.exit_uuid, color.uuid): 1,
            },
        )
        self.assertEqual(
            flow.get_run_stats(),
            {"total": 1, "active": 1, "completed": 0, "expired": 0, "interrupted": 0, "completion": 0},
        )

        # another unknown color, that'll route us right back again
        # the active stats will look the same, but there should be one more journey on the path
        self.create_msg(direction=INCOMING, contact=self.contact, text="mauve").handle()
        (active, visited) = flow.get_activity()

        self.assertEqual(active, {color.uuid: 1})
        self.assertEqual(
            visited,
            {
                "%s:%s" % (color_question.exit_uuid, color.uuid): 1,
                "%s:%s" % (color_other_uuid, other_action.uuid): 2,
                "%s:%s" % (other_action.exit_uuid, color.uuid): 2,
            },
        )

        # this time a color we know takes us elsewhere, activity will move
        # to another node, but still just one entry
        self.create_msg(direction=INCOMING, contact=self.contact, text="blue").handle()
        (active, visited) = flow.get_activity()

        self.assertEqual(active, {beer.uuid: 1})
        self.assertEqual(
            visited,
            {
                "%s:%s" % (color_question.exit_uuid, color.uuid): 1,
                "%s:%s" % (color_other_uuid, other_action.uuid): 2,
                "%s:%s" % (other_action.exit_uuid, color.uuid): 2,
                "%s:%s" % (color_blue_uuid, beer_question.uuid): 1,
                "%s:%s" % (beer_question.exit_uuid, beer.uuid): 1,
            },
        )

        # check recent runs
        recent = FlowPathRecentRun.get_recent([color_question.exit_uuid], color.uuid)
        self.assertEqual([r["text"] for r in recent], ["What is your favorite color?"])

        recent = FlowPathRecentRun.get_recent([color_other_uuid], other_action.uuid)
        self.assertEqual([r["text"] for r in recent], ["mauve", "chartreuse"])

        recent = FlowPathRecentRun.get_recent([other_action.exit_uuid], color.uuid)
        self.assertEqual(
            [r["text"] for r in recent], ["I don't know that color. Try again.", "I don't know that color. Try again."]
        )

        recent = FlowPathRecentRun.get_recent([color_blue_uuid], beer_question.uuid)
        self.assertEqual([r["text"] for r in recent], ["blue"])

        # check the details of the first recent run
        recent = FlowPathRecentRun.objects.order_by("id").first()
        self.assertEqual(recent.run, run1)
        self.assertEqual(str(recent.from_uuid), run1.path[0]["exit_uuid"])
        self.assertEqual(str(recent.from_step_uuid), run1.path[0]["uuid"])
        self.assertEqual(str(recent.to_uuid), run1.path[1]["node_uuid"])
        self.assertEqual(str(recent.to_step_uuid), run1.path[1]["uuid"])
        self.assertEqual(recent.visited_on, iso8601.parse_date(run1.path[1]["arrived_on"]))

        # a new participant, showing distinct active counts and incremented path
        ryan = self.create_contact("Ryan Lewis", "+12065550725")
        self.send_message(flow, "burnt sienna", contact=ryan)
        (active, visited) = flow.get_activity()

        self.assertEqual(active, {color.uuid: 1, beer.uuid: 1})
        self.assertEqual(
            visited,
            {
                "%s:%s" % (color_question.exit_uuid, color.uuid): 2,
                "%s:%s" % (color_other_uuid, other_action.uuid): 3,
                "%s:%s" % (other_action.exit_uuid, color.uuid): 3,
                "%s:%s" % (color_blue_uuid, beer_question.uuid): 1,
                "%s:%s" % (beer_question.exit_uuid, beer.uuid): 1,
            },
        )
        self.assertEqual(
            flow.get_run_stats(),
            {"total": 2, "active": 2, "completed": 0, "expired": 0, "interrupted": 0, "completion": 0},
        )

        # now let's have them land in the same place
        self.send_message(flow, "blue", contact=ryan)
        (active, visited) = flow.get_activity()
        self.assertEqual(active, {beer.uuid: 2})

        # now move our first contact forward to the end
        self.send_message(flow, "Turbo King")
        self.send_message(flow, "Ben Haggerty")
        (active, visited) = flow.get_activity()
        self.assertEqual(active, {beer.uuid: 1})

        # half of our flows are now complete
        self.assertEqual(
            flow.get_run_stats(),
            {"total": 2, "active": 1, "completed": 1, "expired": 0, "interrupted": 0, "completion": 50},
        )

        # we are going to expire, but we want runs across two different flows
        # to make sure that our optimization for expiration is working properly
        cga_flow = self.get_flow("color_gender_age")
        self.assertEqual("What is your gender?", self.send_message(cga_flow, "Red"))
        self.assertEqual(1, len(cga_flow.get_activity()[0]))

        # expire the first contact's runs
        legacy.bulk_exit(FlowRun.objects.filter(contact=self.contact), FlowRun.EXIT_TYPE_EXPIRED)

        # no active runs for our contact
        self.assertEqual(0, FlowRun.objects.filter(contact=self.contact, is_active=True).count())

        # both of our flows should have reduced active contacts
        self.assertEqual(0, len(cga_flow.get_activity()[0]))

        # now we should only have one node with active runs, but the paths stay
        # the same since those are historical
        (active, visited) = flow.get_activity()
        self.assertEqual(active, {beer.uuid: 1})
        self.assertEqual(
            visited,
            {
                "%s:%s" % (color_question.exit_uuid, color.uuid): 2,
                "%s:%s" % (color_other_uuid, other_action.uuid): 3,
                "%s:%s" % (other_action.exit_uuid, color.uuid): 3,
                "%s:%s" % (color_blue_uuid, beer_question.uuid): 2,
                "%s:%s" % (beer_question.exit_uuid, beer.uuid): 2,
                "%s:%s" % (beer.get_rules()[2].uuid, name_question.uuid): 1,
                "%s:%s" % (name_question.exit_uuid, name.uuid): 1,
                "%s:%s" % (name.get_rules()[0].uuid, end_prompt.uuid): 1,
            },
        )

        # no completed runs but one expired run
        self.assertEqual(
            flow.get_run_stats(),
            {"total": 2, "active": 1, "completed": 0, "expired": 1, "interrupted": 0, "completion": 0},
        )

        # check that we have the right number of runs
        self.assertEqual(2, FlowRun.objects.filter(flow=flow).count())

        # now let's delete our contact, we'll still have one active node, but
        # our visit path counts will go down by two since he went there twice
        self.contact.release(self.user)
        (active, visited) = flow.get_activity()
        self.assertEqual(active, {beer.uuid: 1})
        self.assertEqual(
            visited,
            {
                "%s:%s" % (color_question.exit_uuid, color.uuid): 1,
                "%s:%s" % (color_other_uuid, other_action.uuid): 1,
                "%s:%s" % (other_action.exit_uuid, color.uuid): 1,
                "%s:%s" % (color_blue_uuid, beer_question.uuid): 1,
                "%s:%s" % (beer_question.exit_uuid, beer.uuid): 1,
                "%s:%s" % (beer.get_rules()[2].uuid, name_question.uuid): 0,
                "%s:%s" % (name_question.exit_uuid, name.uuid): 0,
                "%s:%s" % (name.get_rules()[0].uuid, end_prompt.uuid): 0,
            },
        )

        # he was also accounting for our completion rate, back to nothing
        self.assertEqual(
            flow.get_run_stats(),
            {"total": 1, "active": 1, "completed": 0, "expired": 0, "interrupted": 0, "completion": 0},
        )

        # advance ryan to the end to make sure our percentage accounts for one less contact
        self.send_message(flow, "Turbo King", contact=ryan)
        self.send_message(flow, "Ryan Lewis", contact=ryan)
        (active, visited) = flow.get_activity()
        self.assertEqual(active, {})
        self.assertEqual(
            visited,
            {
                "%s:%s" % (color_question.exit_uuid, color.uuid): 1,
                "%s:%s" % (color_other_uuid, other_action.uuid): 1,
                "%s:%s" % (other_action.exit_uuid, color.uuid): 1,
                "%s:%s" % (color_blue_uuid, beer_question.uuid): 1,
                "%s:%s" % (beer_question.exit_uuid, beer.uuid): 1,
                "%s:%s" % (beer.get_rules()[2].uuid, name_question.uuid): 1,
                "%s:%s" % (name_question.exit_uuid, name.uuid): 1,
                "%s:%s" % (name.get_rules()[0].uuid, end_prompt.uuid): 1,
            },
        )
        self.assertEqual(
            flow.get_run_stats(),
            {"total": 1, "active": 0, "completed": 1, "expired": 0, "interrupted": 0, "completion": 100},
        )

        # messages to/from deleted contacts shouldn't appear in the recent runs
        recent = FlowPathRecentRun.get_recent([color_other_uuid], other_action.uuid)
        self.assertEqual([r["text"] for r in recent], ["burnt sienna"])

        # delete our last contact to make sure activity is gone without first expiring, zeros abound
        ryan.release(self.admin)
        (active, visited) = flow.get_activity()
        self.assertEqual(active, {})
        self.assertEqual(
            visited,
            {
                "%s:%s" % (color_question.exit_uuid, color.uuid): 0,
                "%s:%s" % (color_other_uuid, other_action.uuid): 0,
                "%s:%s" % (other_action.exit_uuid, color.uuid): 0,
                "%s:%s" % (color_blue_uuid, beer_question.uuid): 0,
                "%s:%s" % (beer_question.exit_uuid, beer.uuid): 0,
                "%s:%s" % (beer.get_rules()[2].uuid, name_question.uuid): 0,
                "%s:%s" % (name_question.exit_uuid, name.uuid): 0,
                "%s:%s" % (name.get_rules()[0].uuid, end_prompt.uuid): 0,
            },
        )
        self.assertEqual(
            flow.get_run_stats(),
            {"total": 0, "active": 0, "completed": 0, "expired": 0, "interrupted": 0, "completion": 0},
        )

        # runs all gone too
        self.assertEqual(0, FlowRun.objects.filter(flow=flow).count())

        # test that expirations remove activity when triggered from the cron in the same way
        tupac = self.create_contact("Tupac Shakur", "+12065550725")
        self.send_message(flow, "azul", contact=tupac)
        (active, visited) = flow.get_activity()
        self.assertEqual(active, {color.uuid: 1})
        self.assertEqual(
            visited,
            {
                "%s:%s" % (color_question.exit_uuid, color.uuid): 1,
                "%s:%s" % (color_other_uuid, other_action.uuid): 1,
                "%s:%s" % (other_action.exit_uuid, color.uuid): 1,
                "%s:%s" % (color_blue_uuid, beer_question.uuid): 0,
                "%s:%s" % (beer_question.exit_uuid, beer.uuid): 0,
                "%s:%s" % (beer.get_rules()[2].uuid, name_question.uuid): 0,
                "%s:%s" % (name_question.exit_uuid, name.uuid): 0,
                "%s:%s" % (name.get_rules()[0].uuid, end_prompt.uuid): 0,
            },
        )
        self.assertEqual(
            flow.get_run_stats(),
            {"total": 1, "active": 1, "completed": 0, "expired": 0, "interrupted": 0, "completion": 0},
        )

        # now mark run has expired and make sure it is removed from our activity
        run = tupac.runs.first()
        run.exit_type = FlowRun.EXIT_TYPE_EXPIRED
        run.exited_on = timezone.now()
        run.is_active = False
        run.save(update_fields=("exit_type", "exited_on", "is_active"))

        (active, visited) = flow.get_activity()
        self.assertEqual(active, {})
        self.assertEqual(
            flow.get_run_stats(),
            {"total": 1, "active": 0, "completed": 0, "expired": 1, "interrupted": 0, "completion": 0},
        )

        # choose a rule that is not wired up (end of flow)
        jimmy = self.create_contact("Jimmy Graham", "+12065558888")
        self.send_message(flow, "cyan", contact=jimmy, assert_reply=False)

        tyler = self.create_contact("Tyler Lockett", "+12065559999")
        self.send_message(flow, "cyan", contact=tyler, assert_reply=False)

        squash_flowpathcounts()
        (active, visited) = flow.get_activity()

        self.assertEqual(active, {})
        self.assertEqual(
            visited,
            {
                "%s:%s" % (color_question.exit_uuid, color.uuid): 3,
                "%s:%s" % (color_other_uuid, other_action.uuid): 1,
                "%s:%s" % (other_action.exit_uuid, color.uuid): 1,
                "%s:%s" % (color_blue_uuid, beer_question.uuid): 0,
                "%s:%s" % (beer_question.exit_uuid, beer.uuid): 0,
                "%s:%s" % (beer.get_rules()[2].uuid, name_question.uuid): 0,
                "%s:%s" % (name_question.exit_uuid, name.uuid): 0,
                "%s:%s" % (name.get_rules()[0].uuid, end_prompt.uuid): 0,
            },
        )

        # check that flow interruption counts properly
        rawls = self.create_contact("Thomas Rawls", "+12065557777")
        self.send_message(flow, "blue", contact=rawls)

        # but he's got other things on his mind
        random_word = self.get_flow("random_word")
        self.send_message(random_word, "blerg", contact=rawls)

        (active, visited) = flow.get_activity()

        self.assertEqual(active, {})
        self.assertEqual(
            visited,
            {
                "%s:%s" % (color_question.exit_uuid, color.uuid): 4,
                "%s:%s" % (color_other_uuid, other_action.uuid): 1,
                "%s:%s" % (other_action.exit_uuid, color.uuid): 1,
                "%s:%s" % (color_blue_uuid, beer_question.uuid): 1,
                "%s:%s" % (beer_question.exit_uuid, beer.uuid): 1,
                "%s:%s" % (beer.get_rules()[2].uuid, name_question.uuid): 0,
                "%s:%s" % (name_question.exit_uuid, name.uuid): 0,
                "%s:%s" % (name.get_rules()[0].uuid, end_prompt.uuid): 0,
            },
        )

    @uses_legacy_engine
    def test_activity_for_pruned_paths(self):
        flow = self.get_flow("color")
        color_question = ActionSet.objects.get(x=1, y=1, flow=flow)
        blue_action = ActionSet.objects.get(x=3, y=3, flow=flow)
        other_action = ActionSet.objects.get(x=4, y=4, flow=flow)
        color_ruleset = RuleSet.objects.get(label="color", flow=flow)

        rules = color_ruleset.get_rules()
        blue_rule = rules[1]
        other_rule = rules[2]

        run, = legacy.flow_start(flow, [], [self.contact])

        now = datetime.datetime(2014, 1, 2, 3, 4, 5, 6, timezone.utc)

        for m in range(50):
            # send messages as if they're 1 hour apart so we don't trigger check for duplicate replies
            with patch.object(timezone, "now", return_value=now + timedelta(hours=m)):
                self.send_message(flow, f"azure{m}")

        self.send_message(flow, f"blue")

        run.refresh_from_db()
        self.assertEqual(len(run.path), 100)  # path has been pruned to 100

        (active, visited) = flow.get_activity()

        self.assertEqual(active, {})  # run is complete
        self.assertEqual(
            visited,
            {
                "%s:%s" % (color_question.exit_uuid, color_ruleset.uuid): 1,
                "%s:%s" % (other_rule.uuid, other_action.uuid): 50,
                "%s:%s" % (other_action.exit_uuid, color_ruleset.uuid): 50,
                "%s:%s" % (blue_rule.uuid, blue_action.uuid): 1,
            },
        )

    @uses_legacy_engine
    def test_prune_recentruns(self):
        flow = self.get_flow("favorites")

        other_action = ActionSet.objects.get(y=8, flow=flow)
        color_ruleset = RuleSet.objects.get(label="Color", flow=flow)
        other_rule = color_ruleset.get_rules()[-1]

        # send 12 invalid color responses (must be from different contacts to avoid loop detection at 10 messages)
        bob = self.create_contact("Bob", number="+260964151234")
        for m in range(12):
            contact = self.contact if m % 2 == 0 else bob
            self.send_message(flow, "%d" % (m + 1), contact=contact)

        # all 12 messages are stored for the other segment
        other_recent = FlowPathRecentRun.objects.filter(from_uuid=other_rule.uuid, to_uuid=other_action.uuid)
        self.assertEqual(len(other_recent), 12)

        # and these are returned with most-recent first
        other_recent = FlowPathRecentRun.get_recent([other_rule.uuid], other_action.uuid, limit=None)
        self.assertEqual(
            [r["text"] for r in other_recent], ["12", "11", "10", "9", "8", "7", "6", "5", "4", "3", "2", "1"]
        )

        # even when limit is applied
        other_recent = FlowPathRecentRun.get_recent([other_rule.uuid], other_action.uuid, limit=5)
        self.assertEqual([r["text"] for r in other_recent], ["12", "11", "10", "9", "8"])

        squash_flowruncounts()

        # now only 5 newest are stored
        other_recent = FlowPathRecentRun.objects.filter(from_uuid=other_rule.uuid, to_uuid=other_action.uuid)
        self.assertEqual(len(other_recent), 5)

        other_recent = FlowPathRecentRun.get_recent([other_rule.uuid], other_action.uuid)
        self.assertEqual([r["text"] for r in other_recent], ["12", "11", "10", "9", "8"])

        # send another message and prune again
        self.send_message(flow, "13", contact=bob)
        squash_flowruncounts()

        other_recent = FlowPathRecentRun.get_recent([other_rule.uuid], other_action.uuid)
        self.assertEqual([r["text"] for r in other_recent], ["13", "12", "11", "10", "9"])

    def test_destination_type(self):
        flow = self.get_flow("pick_a_number")

        # our start points to a ruleset
        start = ActionSet.objects.get(flow=flow, y=0)

        # assert our destination
        self.assertEqual(Flow.NODE_TYPE_RULESET, start.destination_type)

        # and that ruleset points to an actionset
        ruleset = RuleSet.objects.get(uuid=start.destination)
        rule = ruleset.get_rules()[0]
        self.assertEqual(Flow.NODE_TYPE_ACTIONSET, rule.destination_type)

        # point our rule to a ruleset
        passive = RuleSet.objects.get(flow=flow, label="passive")
        self.update_destination(flow, rule.uuid, passive.uuid)
        ruleset = RuleSet.objects.get(uuid=start.destination)
        self.assertEqual(Flow.NODE_TYPE_RULESET, ruleset.get_rules()[0].destination_type)

    @uses_legacy_engine
    def test_orphaned_action_to_action(self):
        """
        Orphaned at an action, then routed to an action
        """

        # run a flow that ends on an action
        flow = self.get_flow("pick_a_number")
        self.assertEqual("You picked 3!", self.send_message(flow, "3"))

        pick_a_number = ActionSet.objects.get(flow=flow, y=0)
        you_picked = ActionSet.objects.get(flow=flow, y=228)

        # send a message, no flow should handle us since we are done
        incoming = self.create_msg(direction=INCOMING, contact=self.contact, text="Unhandled")
        handled = legacy.find_and_handle(incoming)[0]
        self.assertFalse(handled)

        # now wire up our finished action to the start of our flow
        flow = self.update_destination(flow, you_picked.uuid, pick_a_number.uuid)
        self.send_message(flow, "next message please", assert_reply=False, assert_handle=False)

    @uses_legacy_engine
    def test_orphaned_action_to_input_rule(self):
        """
        Orphaned at an action, then routed to a rule that evaluates on input
        """
        flow = self.get_flow("pick_a_number")

        self.assertEqual("You picked 6!", self.send_message(flow, "6"))

        you_picked = ActionSet.objects.get(flow=flow, y=228)
        number = RuleSet.objects.get(flow=flow, label="number")

        flow = self.update_destination(flow, you_picked.uuid, number.uuid)
        self.send_message(flow, "9", assert_reply=False, assert_handle=False)

    @uses_legacy_engine
    def test_orphaned_action_to_passive_rule(self):
        """
        Orphaned at an action, then routed to a rule that doesn't require input which leads
        to a rule that evaluates on input
        """
        flow = self.get_flow("pick_a_number")

        you_picked = ActionSet.objects.get(flow=flow, y=228)
        passive_ruleset = RuleSet.objects.get(flow=flow, label="passive")
        self.assertEqual("You picked 6!", self.send_message(flow, "6"))

        flow = self.update_destination(flow, you_picked.uuid, passive_ruleset.uuid)
        self.send_message(flow, "9", assert_reply=False, assert_handle=False)

    @uses_legacy_engine
    def test_deleted_ruleset(self):
        flow = self.get_flow("favorites")
        self.send_message(flow, "RED", restart_participants=True)

        # one active run
        self.assertEqual(1, FlowRun.objects.filter(contact=self.contact, is_active=True).count())

        # at this point we are waiting for the response to the second question about beer, let's delete it
        RuleSet.objects.get(flow=flow, label="Beer").delete()

        # we still have one active run, though we are somewhat in limbo
        self.assertEqual(1, FlowRun.objects.filter(contact=self.contact, is_active=True).count())

        # sending a new message in shouldn't get a reply, and our run should be terminated
        responses = self.send_message(flow, "abandoned", assert_reply=False, assert_handle=True)
        self.assertIsNone(responses)
        self.assertEqual(0, FlowRun.objects.filter(contact=self.contact, is_active=True).count())

    @uses_legacy_engine
    def test_server_runtime_cycle(self):
        flow = self.get_flow("loop_detection")
        first_actionset = ActionSet.objects.get(flow=flow, y=0)
        group_ruleset = RuleSet.objects.get(flow=flow, label="Group Split A")
        group_one_rule = group_ruleset.get_rules()[0]
        name_ruleset = RuleSet.objects.get(flow=flow, label="Name Split")
        rowan_rule = name_ruleset.get_rules()[0]

        # rule turning back on ourselves
        with self.assertRaises(FlowException):
            self.update_destination(flow, group_one_rule.uuid, group_ruleset.uuid)

        # non-blocking rule to non-blocking rule and back
        with self.assertRaises(FlowException):
            self.update_destination(flow, rowan_rule.uuid, group_ruleset.uuid)

        # our non-blocking rule to an action and back to us again
        with self.assertRaises(FlowException):
            self.update_destination(flow, group_one_rule.uuid, first_actionset.uuid)

        # add our contact to Group A
        group_a = ContactGroup.user_groups.create(
            org=self.org, name="Group A", created_by=self.admin, modified_by=self.admin
        )
        group_a.contacts.add(self.contact)

        # rule turning back on ourselves
        self.update_destination_no_check(flow, group_ruleset.uuid, group_ruleset.uuid, rule=group_one_rule.uuid)
        self.send_message(flow, "1", assert_reply=False, assert_handle=False)

        # should have an interrupted run
        self.assertEqual(
            1, FlowRun.objects.filter(contact=self.contact, exit_type=FlowRun.EXIT_TYPE_INTERRUPTED).count()
        )

        flow.release()

        # non-blocking rule to non-blocking rule and back
        flow = self.get_flow("loop_detection")

        # need to get these again as we just reimported and UUIDs have changed
        group_ruleset = RuleSet.objects.get(flow=flow, label="Group Split A")
        name_ruleset = RuleSet.objects.get(flow=flow, label="Name Split")
        rowan_rule = name_ruleset.get_rules()[0]

        # update our name to rowan so we match the name rule
        self.contact.name = "Rowan"
        self.contact.save(update_fields=("name",), handle_update=False)

        # but remove ourselves from the group so we enter the loop
        group_a.contacts.remove(self.contact)

        self.update_destination_no_check(flow, name_ruleset.uuid, group_ruleset.uuid, rule=rowan_rule.uuid)
        self.send_message(flow, "2", assert_reply=False, assert_handle=False)

        # should have an interrupted run
        self.assertEqual(
            2, FlowRun.objects.filter(contact=self.contact, exit_type=FlowRun.EXIT_TYPE_INTERRUPTED).count()
        )

    @uses_legacy_engine
    def test_decimal_substitution(self):
        flow = self.get_flow("pick_a_number")
        self.assertEqual("You picked 3!", self.send_message(flow, "3"))

    @uses_legacy_engine
    def test_rules_first(self):
        flow = self.get_flow("rules_first")
        self.assertEqual(Flow.NODE_TYPE_RULESET, flow.entry_type)
        self.assertEqual("You've got to be kitten me", self.send_message(flow, "cats"))

    @uses_legacy_engine
    def test_numeric_rule_allows_variables(self):
        flow = self.get_flow("numeric_rule_allows_variables")

        zinedine = self.create_contact("Zinedine", "+12065550100")
        zinedine.set_field(self.user, "age", "25")

        self.assertEqual("Good count", self.send_message(flow, "35", contact=zinedine))

    def test_group_dependencies(self):
        self.get_flow("dependencies")
        flow = Flow.objects.filter(name="Dependencies").first()

        group_names = ["Dog Facts", "Cat Facts", "Fish Facts", "Monkey Facts"]
        for name in group_names:
            self.assertIsNotNone(flow.group_dependencies.filter(name=name).first(), "Missing group %s" % name)

        # trim off our first action which is remove from Dog Facts
        update_json = flow.as_json()
        update_json["action_sets"][0]["actions"] = update_json["action_sets"][0]["actions"][1:]
        flow.update(update_json)

        # dog facts should be removed
        self.assertIsNone(flow.group_dependencies.filter(name="Dog Facts").first())

        # but others should still be there
        for name in group_names[1:]:
            self.assertIsNotNone(flow.group_dependencies.filter(name=name).first())

    def test_label_dependencies(self):
        self.get_flow("add_label")
        flow = Flow.objects.filter(name="Add Label").first()

        self.assertEqual(flow.label_dependencies.count(), 1)

        update_json = flow.as_json()
        # clear `add_label` actions
        update_json["action_sets"][-2]["actions"] = []
        update_json["action_sets"][-1]["actions"] = []
        flow.update(update_json)

        self.assertEqual(flow.label_dependencies.count(), 0)

    def test_channel_dependencies(self):
        self.channel.name = "1234"
        self.channel.save()

        self.get_flow("migrate_to_11_12_one_node")
        flow = Flow.objects.filter(name="channel").first()

        self.assertEqual(flow.channel_dependencies.count(), 1)

        update_json = flow.as_json()
        # clear `channel` action
        update_json["action_sets"][-1]["actions"] = []
        flow.update(update_json)

        self.assertEqual(flow.channel_dependencies.count(), 0)

    def test_flow_dependencies(self):

        self.get_flow("dependencies")
        flow = Flow.objects.filter(name="Dependencies").first()

        # we should depend on our child flow
        self.assertIsNotNone(flow.flow_dependencies.filter(name="Child Flow").first())

        # remove our start flow action
        update_json = flow.as_json()
        actionsets = update_json["action_sets"]
        actionsets[-1]["actions"] = actionsets[-1]["actions"][0:-1]
        update_json["action_sets"] = actionsets
        flow.update(update_json)

        # now we no longer depend on it
        self.assertIsNone(flow.flow_dependencies.filter(name="Child Flow").first())

    def test_update_dependencies_with_actiontype_flow(self):
        self.get_flow("dependencies")

        flow = Flow.objects.filter(name="Dependencies").first()
        dep_flow = Flow.objects.filter(name="Child Flow").first()

        update_json = flow.as_json()

        # remove existing flow dependency
        actionsets = update_json["action_sets"]
        actionsets[-1]["actions"] = actionsets[-1]["actions"][0:-1]
        update_json["action_sets"] = actionsets
        flow.update(update_json)

        self.assertEqual(flow.flow_dependencies.count(), 0)

        # add a new start another flow action
        start_new_flow_action = {
            "type": "flow",
            "uuid": "e1fa3c52-3616-499e-b1be-c759f4645247",
            "flow": {"uuid": f"{dep_flow.uuid}", "name": "Child Flow"},
        }

        actionsets[-1]["actions"].append(start_new_flow_action)
        update_json["action_sets"] = actionsets

        flow.update(update_json)

        self.assertEqual(flow.flow_dependencies.count(), 1)

    def test_group_uuid_mapping(self):
        flow = self.get_flow("group_split")

        # make sure the groups in our rules exist as expected
        ruleset = RuleSet.objects.filter(label="Member").first()
        group_count = 0
        for rule in ruleset.rules:
            if rule["test"]["type"] == "in_group":
                group = ContactGroup.user_groups.filter(uuid=rule["test"]["test"]["uuid"]).first()
                self.assertIsNotNone(group)
                group_count += 1
        self.assertEqual(2, group_count)

        self.get_flow("dependencies")
        flow = Flow.objects.filter(name="Dependencies").first()
        group_count = 0
        for actionset in flow.action_sets.all():
            for action in actionset.actions:
                if action["type"] in ("add_group", "del_group"):
                    for group in action["groups"]:
                        if isinstance(group, dict):
                            group_count += 1
                            self.assertIsNotNone(ContactGroup.user_groups.filter(uuid=group["uuid"]).first())

        # make sure we found both our group actions
        self.assertEqual(2, group_count)

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

    @uses_legacy_engine
    def test_group_split(self):
        flow = self.get_flow("group_split")

        rulesets = RuleSet.objects.filter(flow=flow)
        group_count = 0
        for ruleset in rulesets:
            for rule in ruleset.rules:
                if rule["test"]["type"] == "in_group":
                    group = ContactGroup.user_groups.filter(uuid=rule["test"]["test"]["uuid"]).first()
                    self.assertIsNotNone(group)
                    group_count += 1
        self.assertEqual(2, group_count)

        run, = legacy.flow_start(flow, [], [self.contact])

        # not in any group
        self.assertEqual(0, ContactGroup.user_groups.filter(contacts__in=[self.contact]).count())

        # add us to Group A
        self.send("add group a")

        self.assertEqual("Awaiting command.", Msg.objects.filter(direction="O").order_by("-created_on").first().text)
        groups = ContactGroup.user_groups.filter(contacts__in=[self.contact])
        self.assertEqual(1, groups.count())
        self.assertEqual("Group A", groups.first().name)

        # now split us on group membership
        self.send("split")
        self.assertEqual("You are in Group A", Msg.objects.filter(direction="O").order_by("-created_on")[1].text)

        run.refresh_from_db()
        self.assertEqual(
            run.results["member"],
            {
                "category": "Group A",
                "created_on": matchers.ISODate(),
                "input": "Ben Haggerty",
                "name": "Member",
                "node_uuid": matchers.UUID4String(),
                "value": "Group A",
            },
        )

        # now add us to group b and remove from group a
        self.send("remove group a")
        self.send("add group b")
        self.send("split")
        self.assertEqual("You are in Group B", Msg.objects.filter(direction="O").order_by("-created_on")[1].text)

        # now remove from both groups
        self.send("remove group b")
        self.send("split")
        self.assertEqual(
            "You aren't in either group.", Msg.objects.filter(direction="O").order_by("-created_on")[1].text
        )

        run.refresh_from_db()
        self.assertEqual(
            run.results["member"],
            {
                "category": "Other",
                "created_on": matchers.ISODate(),
                "input": "Ben Haggerty",
                "name": "Member",
                "node_uuid": matchers.UUID4String(),
                "value": "Ben Haggerty",
            },
        )

        # if contact has null name, value will be empty string
        nameless = self.create_contact(name=None, number="+12065553030")
        run, = legacy.flow_start(flow, [], [nameless])

        self.send("split", contact=nameless)
        self.assertEqual(
            "You aren't in either group.",
            Msg.objects.filter(direction="O", contact=nameless).order_by("created_on")[1].text,
        )

        run.refresh_from_db()
        self.assertEqual(
            run.results["member"],
            {
                "category": "Other",
                "created_on": matchers.ISODate(),
                "input": "(206) 555-3030",
                "name": "Member",
                "node_uuid": matchers.UUID4String(),
                "value": "(206) 555-3030",
            },
        )

    @uses_legacy_engine
    def test_media_first_action(self):
        flow = self.get_flow("media_first_action")

        runs = legacy.flow_start(flow, [], [self.contact])
        self.assertEqual(1, len(runs))

        msg = self.contact.msgs.get()
        self.assertEqual(msg.text, "Hey")
        self.assertEqual(
            msg.attachments,
            [f"image/jpeg:{settings.STORAGE_URL}/attachments/2/53/steps/87d34837-491c-4541-98a1-fa75b52ebccc.jpg"],
        )

    def test_group_send(self):
        # create an inactive group with the same name, to test that this doesn't blow up our import
        group = ContactGroup.get_or_create(self.org, self.admin, "Survey Audience")
        group.is_active = False
        group.save()

        # and create another as well
        ContactGroup.get_or_create(self.org, self.admin, "Survey Audience")

        # fetching a flow with a group send shouldn't throw
        self.get_flow("group_send_flow")

    @uses_legacy_engine
    def test_group_rule_first(self):
        rule_flow = self.get_flow("group_rule_first")

        # start our contact down it
        legacy.flow_start(rule_flow, [], [self.contact], restart_participants=True)

        # contact should get a message that they didn't match either group
        self.assertLastResponse("You are something else.")

        # add them to the father's group
        self.create_group("Fathers", [self.contact])

        legacy.flow_start(rule_flow, [], [self.contact], restart_participants=True)
        self.assertLastResponse("You are a father.")

    def test_flow_delete_of_inactive_flow(self):
        flow = self.get_flow("favorites")

        # release the flow
        flow.release()

        self.login(self.admin)
        response = self.client.post(reverse("flows.flow_delete", args=[flow.pk]))

        # can't delete already released flow
        self.assertEqual(response.status_code, 404)

    @uses_legacy_engine
    def test_flow_delete(self):
        flow = self.get_flow("favorites")

        # create a campaign that contains this flow
        friends = self.create_group("Friends", [])
        poll_date = ContactField.get_or_create(
            self.org, self.admin, "poll_date", "Poll Date", value_type=Value.TYPE_DATETIME
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
        self.assertEqual("Good choice, I like Red too! What is your favorite beer?", self.send_message(flow, "RED"))

        # run it again to completion
        joe = self.create_contact("Joe", "1234")
        self.send_message(flow, "green", contact=joe)
        self.send_message(flow, "primus", contact=joe)
        self.send_message(flow, "Joe", contact=joe)

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

        # our campaign event should no longer be active
        self.assertFalse(CampaignEvent.objects.filter(id=event1.id, is_active=True).exists())

        # nor should our trigger
        self.assertFalse(Trigger.objects.filter(id=trigger.id).exists())

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

    @uses_legacy_engine
    def test_start_flow_action(self):
        self.import_file("flow_starts")
        parent = Flow.objects.get(name="Parent Flow")
        child = Flow.objects.get(name="Child Flow")

        contacts = []
        for i in range(10):
            contacts.append(self.create_contact("Fred", "+25078812312%d" % i))

        # start the flow for our contacts
        start = FlowStart.objects.create(flow=parent, created_by=self.admin, modified_by=self.admin)
        for contact in contacts:
            start.contacts.add(contact)
        legacy.flow_start_start(start)

        # all our contacts should have a name of Greg now (set in the child flow)
        for contact in contacts:
            self.assertTrue(FlowRun.objects.filter(flow=parent, contact=contact))
            self.assertTrue(FlowRun.objects.filter(flow=child, contact=contact))
            self.assertEqual("Greg", Contact.objects.get(pk=contact.pk).name)

        # 10 child flow runs should be active waiting for input
        self.assertEqual(FlowRun.objects.filter(flow=child, is_active=True).count(), 10)

        # check our count
        self.assertEqual(FlowStartCount.get_count(start), 10)

        # squash them
        FlowStartCount.squash()
        self.assertEqual(FlowStartCount.get_count(start), 10)

        # recalculate and try again
        FlowStartCount.populate_for_start(start)
        self.assertEqual(FlowStartCount.get_count(start), 10)

        # send some input to complete the child flows
        for contact in contacts:
            msg = self.create_msg(contact=contact, direction="I", text="OK", channel=self.channel)
            msg.handle()

        # all of the runs should now be completed
        self.assertEqual(FlowRun.objects.filter(is_active=False, exit_type=FlowRun.EXIT_TYPE_COMPLETED).count(), 20)

    @uses_legacy_engine
    def test_cross_language_import(self):
        spanish = Language.create(self.org, self.admin, "Spanish", "spa")
        Language.create(self.org, self.admin, "English", "eng")

        # import our localized flow into an org with no languages
        self.import_file("multi_language_flow")
        flow = Flow.objects.get(name="Multi Language Flow")

        # even tho we don't have a language, our flow has enough info to function
        self.assertEqual("eng", flow.base_language)

        # now try executing this flow on our org, should use the flow base language
        self.assertEqual(
            "Hello friend! What is your favorite color?",
            self.send_message(flow, "start flow", restart_participants=True, initiate_flow=True),
        )

        replies = self.send_message(flow, "blue")
        self.assertEqual("Thank you! I like blue.", replies[0])
        self.assertEqual("This message was not translated.", replies[1])

        # now add a primary language to our org
        self.org.primary_language = spanish
        self.org.save()

        flow = Flow.objects.get(pk=flow.pk)

        # with our org in spanish, we should get the spanish version
        self.assertEqual(
            "\xa1Hola amigo! \xbfCu\xe1l es tu color favorito?",
            self.send_message(flow, "start flow", restart_participants=True, initiate_flow=True),
        )

        self.org.primary_language = None
        self.org.save()
        flow = Flow.objects.get(pk=flow.pk)

        # no longer spanish on our org
        self.assertEqual(
            "Hello friend! What is your favorite color?",
            self.send_message(flow, "start flow", restart_participants=True, initiate_flow=True),
        )

        # back to spanish
        self.org.primary_language = spanish
        self.org.save()
        flow = Flow.objects.get(pk=flow.pk)

        # but set our contact's language explicitly should keep us at english
        self.contact.language = "eng"
        self.contact.save(update_fields=("language",), handle_update=False)
        self.assertEqual(
            "Hello friend! What is your favorite color?",
            self.send_message(flow, "start flow", restart_participants=True, initiate_flow=True),
        )

    @uses_legacy_engine
    def test_different_expiration(self):
        flow = self.get_flow("favorites")
        self.send_message(flow, "RED", restart_participants=True)

        # get the latest run
        first_run = flow.runs.all()[0]
        first_expires = first_run.expires_on

        # make sure __str__ works
        str(first_run)

        time.sleep(1)

        # start it again
        self.send_message(flow, "RED", restart_participants=True)

        # previous run should no longer be active
        first_run = FlowRun.objects.get(pk=first_run.pk)
        self.assertFalse(first_run.is_active)

        # expires on shouldn't have changed on it though
        self.assertEqual(first_expires, first_run.expires_on)

        # new run should have a different expires on
        new_run = flow.runs.all().order_by("-expires_on").first()
        self.assertTrue(new_run.expires_on > first_expires)

    @uses_legacy_engine
    def test_flow_expiration_updates(self):
        flow = self.get_flow("favorites")
        self.assertEqual("Good choice, I like Red too! What is your favorite beer?", self.send_message(flow, "RED"))

        # get our current expiration
        run = flow.runs.get()
        self.assertEqual(flow.org, run.org)

        starting_expiration = run.expires_on
        starting_modified = run.modified_on

        time.sleep(1)

        # now fire another messages
        self.assertEqual(
            "Mmmmm... delicious Turbo King. If only they made red Turbo King! Lastly, what is your name?",
            self.send_message(flow, "turbo"),
        )

        # our new expiration should be later
        run.refresh_from_db()
        self.assertTrue(run.expires_on > starting_expiration)
        self.assertTrue(run.modified_on > starting_modified)

    @uses_legacy_engine
    def test_initial_expiration(self):
        flow = self.get_flow("favorites")
        legacy.flow_start(flow, groups=[], contacts=[self.contact])

        run = FlowRun.objects.get()
        self.assertTrue(run.expires_on)

    @uses_legacy_engine
    def test_flow_expiration(self):
        flow = self.get_flow("favorites")

        # run our flow like it was 10 mins ago
        with patch.object(timezone, "now") as mock_now:
            mock_now.side_effect = lambda: datetime.datetime.now(tz=timezone.utc) - timedelta(minutes=10)

            self.assertEqual(
                "Good choice, I like Red too! What is your favorite beer?", self.send_message(flow, "RED")
            )
            self.assertEqual(
                "Mmmmm... delicious Turbo King. If only they made red Turbo King! Lastly, what is your name?",
                self.send_message(flow, "turbo"),
            )
            self.assertEqual(1, flow.runs.count())

        # now let's expire them out of the flow prematurely
        flow.expires_after_minutes = 5
        flow.save()

        # this normally gets run on FlowCRUDL.Update
        update_run_expirations_task(flow.id)

        # check that our run has a new expiration
        run = flow.runs.all()[0]

        self.assertEqual(run.expires_on, iso8601.parse_date(run.path[-1]["arrived_on"]) + timedelta(minutes=5))

    def test_parsing(self):
        # test a preprocess url
        flow = self.get_flow("preprocess")
        self.assertEqual(
            "http://preprocessor.com/endpoint.php",
            flow.rule_sets.all().order_by("y")[0].config[RuleSet.CONFIG_WEBHOOK],
        )

    @uses_legacy_engine
    def test_flow_loops(self):
        self.get_flow("flow_loop")
        # this tests two flows that start each other
        flow1 = Flow.objects.get(name="First Flow")
        flow2 = Flow.objects.get(name="Second Flow")

        # start the flow, shouldn't get into a loop, but both should get started
        legacy.flow_start(flow1, [], [self.contact])

        self.assertTrue(FlowRun.objects.get(flow=flow1, contact=self.contact))
        self.assertTrue(FlowRun.objects.get(flow=flow2, contact=self.contact))

    @uses_legacy_engine
    def test_ruleset_loops(self):
        self.import_file("ruleset_loop")

        flow1 = Flow.objects.all()[1]
        flow2 = Flow.objects.all()[0]

        # start the flow, should not get into a loop
        legacy.flow_start(flow1, [], [self.contact])

        self.assertTrue(FlowRun.objects.get(flow=flow1, contact=self.contact))
        self.assertTrue(FlowRun.objects.get(flow=flow2, contact=self.contact))

    @uses_legacy_engine
    def test_parent_child(self):
        favorites = self.get_flow("favorites")

        # do a dry run once so that the groups and fields get created
        group = self.create_group("Campaign", [])
        field = ContactField.get_or_create(
            self.org, self.admin, "campaign_date", "Campaign Date", value_type=Value.TYPE_DATETIME
        )

        # tests that a contact is properly updated when a child flow is called
        child = self.get_flow("child")
        parent = self.get_flow("parent", substitutions=dict(CHILD_ID=child.id))

        # create a campaign with a single event
        campaign = Campaign.create(self.org, self.admin, "Test Campaign", group)
        CampaignEvent.create_flow_event(
            self.org, self.admin, campaign, relative_to=field, offset=10, unit="W", flow=favorites
        )

        self.assertEqual("Added to campaign.", self.send_message(parent, "start", initiate_flow=True))

        # should have one event scheduled for this contact
        self.assertTrue(EventFire.objects.filter(contact=self.contact))

    @uses_legacy_engine
    def test_priority(self):
        self.get_flow("priorities")
        joe = self.create_contact("joe", "112233")

        parent = Flow.objects.get(name="Priority Parent")
        legacy.flow_start(parent, [], [self.contact, joe])

        self.assertEqual(8, Msg.objects.filter(direction="O").count())

        # all messages so far are low prioirty as well because of no inbound
        self.assertEqual(8, Msg.objects.filter(direction="O", high_priority=False).count())

        # send a message in to become high priority
        self.send("make me high priority por favor")

        # each flow sends one message to cleanup
        self.assertEqual(11, Msg.objects.filter(direction="O").count())
        self.assertEqual(3, Msg.objects.filter(high_priority=True).count())

        # we've completed three flows, but joe is still at it
        self.assertEqual(5, FlowRun.objects.all().count())
        self.assertEqual(
            3, FlowRun.objects.filter(contact=self.contact, exit_type=FlowRun.EXIT_TYPE_COMPLETED).count()
        )
        self.assertEqual(2, FlowRun.objects.filter(contact=joe, exit_type=None).count())

    @uses_legacy_engine
    def test_priority_single_contact(self):
        # try running with a single contact, we dont create broadcasts for a single
        # contact, but the messages should still be low prioirty
        self.get_flow("priorities")
        parent = Flow.objects.get(name="Priority Parent")
        legacy.flow_start(parent, [], [self.contact], restart_participants=True)

        self.assertEqual(4, Msg.objects.count())
        self.assertEqual(0, Broadcast.objects.count())
        self.assertEqual(4, Msg.objects.filter(high_priority=False).count())

    @uses_legacy_engine
    def test_subflow(self):
        """
        Tests that a subflow can be called and the flow is handed back to the parent
        """
        self.get_flow("subflow")
        parent = Flow.objects.get(org=self.org, name="Parent Flow")
        parent_prompt = ActionSet.objects.get(flow=parent, y=0)
        kind_ruleset = RuleSet.objects.get(flow=parent, label="kind")
        subflow_ruleset = RuleSet.objects.get(flow=parent, ruleset_type="subflow")
        subflow_reply = ActionSet.objects.get(flow=parent, y=386, x=341)

        legacy.flow_start(
            parent,
            groups=[],
            contacts=[self.contact, self.create_contact("joe", "+12347778888")],
            restart_participants=True,
        )

        msg = Msg.objects.filter(contact=self.contact).first()
        self.assertEqual("This is a parent flow. What would you like to do?", msg.text)
        self.assertFalse(msg.high_priority)

        # this should launch the child flow
        self.send_message(parent, "color", assert_reply=False)

        msg = Msg.objects.filter(contact=self.contact).order_by("-created_on").first()
        self.assertEqual("What color do you like?", msg.text)
        self.assertTrue(msg.high_priority)

        # should have a run for each flow
        parent_run, child_run = FlowRun.objects.filter(contact=self.contact, is_active=True).order_by("created_on")

        # should have made it to the subflow ruleset on the parent flow
        parent_path = parent_run.path
        self.assertEqual(len(parent_path), 3)
        self.assertEqual(parent_path[0]["node_uuid"], parent_prompt.uuid)
        self.assertEqual(parent_path[0]["exit_uuid"], parent_prompt.exit_uuid)
        self.assertEqual(parent_path[1]["node_uuid"], kind_ruleset.uuid)
        self.assertEqual(parent_path[1]["exit_uuid"], kind_ruleset.get_rules()[0].uuid)
        self.assertEqual(parent_path[2]["node_uuid"], subflow_ruleset.uuid)
        self.assertNotIn("exit_uuid", parent_path[2])

        # complete the child flow
        self.send("Red")

        child_run.refresh_from_db()
        self.assertFalse(child_run.is_active)

        # now we are back to a single active flow, the parent
        parent_run.refresh_from_db()
        self.assertTrue(parent_run.is_active)

        parent_path = parent_run.path
        self.assertEqual(len(parent_path), 5)
        self.assertEqual(parent_path[2]["node_uuid"], subflow_ruleset.uuid)
        self.assertEqual(parent_path[2]["exit_uuid"], subflow_ruleset.get_rules()[0].uuid)
        self.assertEqual(parent_path[3]["node_uuid"], subflow_reply.uuid)
        self.assertEqual(parent_path[3]["exit_uuid"], subflow_reply.exit_uuid)
        self.assertEqual(parent_path[4]["node_uuid"], kind_ruleset.uuid)
        self.assertNotIn("exit_uuid", parent_path[4])

        # we should have a new outbound message from the the parent flow
        msg = Msg.objects.filter(contact=self.contact, direction="O").order_by("-created_on").first()
        self.assertEqual("Complete: You picked Red.", msg.text)

        # should only have one response msg
        self.assertEqual(
            1, Msg.objects.filter(text="Complete: You picked Red.", contact=self.contact, direction="O").count()
        )

    @uses_legacy_engine
    def test_subflow_interrupted(self):
        self.get_flow("subflow")
        parent = Flow.objects.get(org=self.org, name="Parent Flow")

        legacy.flow_start(parent, groups=[], contacts=[self.contact], restart_participants=True)
        self.send_message(parent, "color", assert_reply=False)

        # we should now have two active flows
        runs = FlowRun.objects.filter(contact=self.contact, is_active=True).order_by("-created_on")
        self.assertEqual(2, runs.count())

        # now interrupt the child flow
        run = FlowRun.objects.filter(contact=self.contact, is_active=True).order_by("-created_on").first()
        legacy.bulk_exit(FlowRun.objects.filter(id=run.id), FlowRun.EXIT_TYPE_INTERRUPTED)

        # all flows should have finished
        self.assertEqual(0, FlowRun.objects.filter(contact=self.contact, is_active=True).count())

        # and the parent should not have resumed, so our last message was from our subflow
        msg = Msg.objects.all().order_by("-created_on").first()
        self.assertEqual("What color do you like?", msg.text)

    @uses_legacy_engine
    def test_subflow_expired(self):
        self.get_flow("subflow")
        parent = Flow.objects.get(org=self.org, name="Parent Flow")

        legacy.flow_start(parent, groups=[], contacts=[self.contact], restart_participants=True)
        self.send_message(parent, "color", assert_reply=False)

        # we should now have two active flows
        runs = FlowRun.objects.filter(contact=self.contact, is_active=True).order_by("-created_on")
        self.assertEqual(2, runs.count())

        # make sure the parent run expires later than the child
        child_run = runs[0]
        parent_run = runs[1]
        self.assertTrue(parent_run.expires_on > child_run.expires_on)

        # now expire out of the child flow
        run = FlowRun.objects.filter(contact=self.contact, is_active=True).order_by("-created_on").first()
        legacy.bulk_exit(FlowRun.objects.filter(id=run.id), FlowRun.EXIT_TYPE_EXPIRED)

        # all flows should have finished
        self.assertEqual(0, FlowRun.objects.filter(contact=self.contact, is_active=True).count())

        # and should follow the expiration route
        msg = Msg.objects.all().order_by("-created_on").first()
        self.assertEqual("You expired out of the subflow", msg.text)

    @uses_legacy_engine
    def test_subflow_updates(self):

        self.get_flow("subflow")
        parent = Flow.objects.get(org=self.org, name="Parent Flow")

        legacy.flow_start(parent, groups=[], contacts=[self.contact], restart_participants=True)
        self.send_message(parent, "color", assert_reply=False)

        # we should now have two active flows
        self.assertEqual(2, FlowRun.objects.filter(contact=self.contact, is_active=True).count())

        run = FlowRun.objects.filter(flow=parent).first()
        starting_expiration = run.expires_on
        starting_modified = run.modified_on

        time.sleep(1)

        # send a message that will keep us in the child flow
        self.send("no match")

        # our new expiration should be later
        run.refresh_from_db()
        self.assertTrue(run.expires_on > starting_expiration)
        self.assertTrue(run.modified_on > starting_modified)

    @uses_legacy_engine
    def test_subflow_no_interaction(self):
        self.get_flow("subflow_no_pause")
        parent = Flow.objects.get(org=self.org, name="Flow A")
        legacy.flow_start(parent, groups=[], contacts=[self.contact], restart_participants=True)

        # check we got our three messages, the third populated by the child, but sent form the parent
        msgs = Msg.objects.order_by("created_on")
        self.assertEqual(5, msgs.count())
        self.assertEqual(msgs[0].text, "Message 1")
        self.assertEqual(msgs[1].text, "Message 2/4")
        self.assertEqual(msgs[2].text, "Message 3 (FLOW B)")
        self.assertEqual(msgs[3].text, "Message 2/4")
        self.assertEqual(msgs[4].text, "Message 5 (FLOW B)")

    @uses_legacy_engine
    def test_subflow_with_startflow(self):
        self.get_flow("subflow_with_startflow")

        parent = Flow.objects.get(name="Subflow 1")
        legacy.flow_start(parent, groups=[], contacts=[self.contact])

    @uses_legacy_engine
    def test_translations_rule_first(self):

        # import a rule first flow that already has language dicts
        # this rule first does not depend on @step.value for the first rule, so
        # it can be evaluated right away
        flow = self.get_flow("group_membership")

        # create the language for our org
        language = Language.create(self.org, flow.created_by, "English", "eng")
        self.org.primary_language = language
        self.org.save()

        # start our flow without a message (simulating it being fired by a trigger or the simulator)
        # this will evaluate requires_step() to make sure it handles localized flows
        runs = legacy.flow_start(flow, [], [self.contact])
        self.assertEqual(1, len(runs))
        self.assertEqual(self.contact.msgs.get().text, "You are not in the enrolled group.")

        enrolled_group = ContactGroup.create_static(self.org, self.user, "Enrolled")
        enrolled_group.update_contacts(self.user, [self.contact], True)

        runs_started = legacy.flow_start(flow, [], [self.contact], restart_participants=True)
        self.assertEqual(1, len(runs_started))

        msgs = list(self.contact.msgs.order_by("id"))
        self.assertEqual(len(msgs), 2)
        self.assertEqual(msgs[1].text, "You are in the enrolled group.")

    @patch("temba.flows.models.FlowRun.PATH_MAX_STEPS", 8)
    @uses_legacy_engine
    def test_run_path(self):
        flow = self.get_flow("favorites")
        colorPrompt = ActionSet.objects.get(uuid=flow.entry_uuid)
        colorRuleSet = RuleSet.objects.get(uuid=colorPrompt.destination)
        redRule = colorRuleSet.get_rules()[0]
        otherRule = colorRuleSet.get_rules()[-1]
        tryAgainPrompt = ActionSet.objects.get(uuid=otherRule.destination)
        beerPrompt = ActionSet.objects.get(uuid=redRule.destination)
        beerRuleSet = RuleSet.objects.get(uuid=beerPrompt.destination)

        # send an invalid response several times til we hit the path length limit
        for m in range(3):
            self.send_message(flow, "beige")

        run = FlowRun.objects.get()
        path = run.path

        self.assertEqual(
            [(p["node_uuid"], p.get("exit_uuid")) for p in path],
            [
                (colorPrompt.uuid, colorPrompt.exit_uuid),
                (colorRuleSet.uuid, otherRule.uuid),
                (tryAgainPrompt.uuid, tryAgainPrompt.exit_uuid),
                (colorRuleSet.uuid, otherRule.uuid),
                (tryAgainPrompt.uuid, tryAgainPrompt.exit_uuid),
                (colorRuleSet.uuid, otherRule.uuid),
                (tryAgainPrompt.uuid, tryAgainPrompt.exit_uuid),
                (colorRuleSet.uuid, None),
            ],
        )
        self.assertEqual(str(run.current_node_uuid), colorRuleSet.uuid)

        self.send_message(flow, "red")

        run.refresh_from_db()
        path = run.path

        self.assertEqual(
            [(p["node_uuid"], p.get("exit_uuid")) for p in path],
            [
                (tryAgainPrompt.uuid, tryAgainPrompt.exit_uuid),
                (colorRuleSet.uuid, otherRule.uuid),
                (tryAgainPrompt.uuid, tryAgainPrompt.exit_uuid),
                (colorRuleSet.uuid, otherRule.uuid),
                (tryAgainPrompt.uuid, tryAgainPrompt.exit_uuid),
                (colorRuleSet.uuid, redRule.uuid),
                (beerPrompt.uuid, beerPrompt.exit_uuid),
                (beerRuleSet.uuid, None),
            ],
        )
        self.assertEqual(str(run.current_node_uuid), beerRuleSet.uuid)


class DuplicateResultTest(FlowFileTest):
    @uses_legacy_engine
    def test_duplicate_value_test(self):
        flow = self.get_flow("favorites")
        self.assertEqual("I don't know that color. Try again.", self.send_message(flow, "carpet"))

        # get the run for our contact
        run = FlowRun.objects.get(contact=self.contact, flow=flow)

        # we should have one result for this run, "Other"
        results = run.results

        self.assertEqual(len(results), 1)
        self.assertEqual(results["color"]["category"], "Other")

        # retry with "red" as an aswer
        self.assertEqual("Good choice, I like Red too! What is your favorite beer?", self.send_message(flow, "red"))

        # we should now still have only one value, but the category should be Red now
        run.refresh_from_db()
        results = run.results
        self.assertEqual(len(results), 1)
        self.assertEqual(results["color"]["category"], "Red")


class ChannelSplitTest(FlowFileTest):
    def setUp(self):
        super().setUp()

        # update our channel to have a 206 address
        self.channel.address = "+12065551212"
        self.channel.save()

    @uses_legacy_engine
    def test_initial_channel_split(self):
        flow = self.get_flow("channel_split")

        # start our contact down the flow
        legacy.flow_start(flow, [], [self.contact])

        # check the message sent to them
        msgs = list(self.contact.msgs.order_by("id"))
        self.assertEqual(len(msgs), 2)
        self.assertEqual(msgs[0].text, "Your channel is +12065551212")
        self.assertEqual(msgs[1].text, "206 Channel")

    @uses_legacy_engine
    def test_no_urn_channel_split(self):
        flow = self.get_flow("channel_split")

        # ok, remove the URN on our contact
        self.contact.urns.all().update(contact=None)

        # run the flow again
        legacy.flow_start(flow, [], [self.contact])

        # shouldn't have any messages sent, as they have no URN
        self.assertFalse(self.contact.msgs.all())

        # should have completed the flow though
        run = FlowRun.objects.get(contact=self.contact)
        self.assertFalse(run.is_active)

    @uses_legacy_engine
    def test_no_urn_channel_split_first(self):
        flow = self.get_flow("channel_split_rule_first")

        # start our contact down the flow
        legacy.flow_start(flow, [], [self.contact])

        # check that the split was successful
        msg = self.contact.msgs.first()
        self.assertEqual("206 Channel", msg.text)


class GhostActionNodeTest(FlowFileTest):
    @uses_legacy_engine
    def test_ghost_action_node_test(self):
        # load our flows
        self.get_flow("parent_child_flow")
        flow = Flow.objects.get(name="Parent Flow")

        # start the flow
        legacy.flow_start(flow, [], [self.contact])

        # at this point, our contact has to active flow runs:
        # one for our parent flow at an action set (the start flow action), one in our child flow at the send message action

        # let's remove the actionset we are stuck at
        ActionSet.objects.filter(flow=flow).delete()

        # create a new message and get it handled
        msg = self.create_msg(contact=self.contact, direction="I", text="yes")
        legacy.find_and_handle(msg)

        # we should have gotten a response from our child flow
        self.assertEqual(
            "I like butter too.", Msg.objects.filter(direction=OUTGOING).order_by("-created_on").first().text
        )


class TwoInRowTest(FlowFileTest):
    @uses_legacy_engine
    def test_two_in_row(self):
        flow = self.get_flow("two_in_row")
        legacy.flow_start(flow, [], [self.contact])

        # assert contact received both messages
        msgs = self.contact.msgs.all()
        self.assertEqual(msgs.count(), 2)


class FlowSessionCRUDLTest(TembaTest):
    @uses_legacy_engine
    def test_session_json(self):
        contact = self.create_contact("Bob", number="+1234567890")
        flow = self.get_flow("color")
        legacy.flow_start(flow, [], [contact])

        # create a fake session for this run
        session = FlowSession.objects.create(
            org=self.org,
            contact=contact,
            status=FlowSession.STATUS_WAITING,
            responded=False,
            output=dict(),
            created_on=timezone.now(),
        )

        # normal users can't see session json
        url = reverse("flows.flowsession_json", args=[session.id])
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


class ExitTest(FlowFileTest):
    @uses_legacy_engine
    def test_exit_via_start(self):
        # start contact in one flow
        first_flow = self.get_flow("substitution")
        legacy.flow_start(first_flow, [], [self.contact])

        # should have one active flow run
        first_run = FlowRun.objects.get(is_active=True, flow=first_flow, contact=self.contact)

        # start in second via manual start
        second_flow = self.get_flow("favorites")
        legacy.flow_start(second_flow, [], [self.contact])

        second_run = FlowRun.objects.get(is_active=True)
        first_run.refresh_from_db()
        self.assertFalse(first_run.is_active)
        self.assertEqual(first_run.exit_type, FlowRun.EXIT_TYPE_INTERRUPTED)

        self.assertTrue(second_run.is_active)


class StackedExitsTest(FlowFileTest):
    def setUp(self):
        super().setUp()

        self.channel.delete()
        self.channel = Channel.create(
            self.org,
            self.user,
            "KE",
            "EX",
            None,
            "+250788123123",
            schemes=["tel"],
            config=dict(send_url="https://google.com"),
        )

    @uses_legacy_engine
    def test_stacked_exits(self):
        self.get_flow("stacked_exits")
        flow = Flow.objects.get(name="Stacked")

        legacy.flow_start(flow, [], [self.contact])

        msgs = Msg.objects.filter(contact=self.contact).order_by("sent_on")
        self.assertEqual(3, msgs.count())
        self.assertEqual("Start!", msgs[0].text)
        self.assertEqual("Leaf!", msgs[1].text)
        self.assertEqual("End!", msgs[2].text)

        runs = FlowRun.objects.filter(contact=self.contact, exit_type=FlowRun.EXIT_TYPE_COMPLETED).order_by(
            "exited_on"
        )
        self.assertEqual(3, runs.count())
        self.assertEqual("Stacker Leaf", runs[0].flow.name)
        self.assertEqual("Stacker", runs[1].flow.name)
        self.assertEqual("Stacked", runs[2].flow.name)

    @uses_legacy_engine
    def test_response_exits(self):
        self.get_flow("stacked_response_exits")
        flow = Flow.objects.get(name="Stacked")

        legacy.flow_start(flow, [], [self.contact])

        msgs = Msg.objects.filter(contact=self.contact).order_by("sent_on")
        self.assertEqual(2, msgs.count())
        self.assertEqual("Start!", msgs[0].text)
        self.assertEqual("Send something!", msgs[1].text)

        # nobody completed yet
        self.assertEqual(
            0, FlowRun.objects.filter(contact=self.contact, exit_type=FlowRun.EXIT_TYPE_COMPLETED).count()
        )

        # ok, send a response, should unwind all our flows
        self.create_msg(contact=self.contact, direction="I", text="something").handle()

        msgs = Msg.objects.filter(contact=self.contact, direction="O").order_by("sent_on")
        self.assertEqual(3, msgs.count())
        self.assertEqual("Start!", msgs[0].text)
        self.assertEqual("Send something!", msgs[1].text)
        self.assertEqual("End!", msgs[2].text)

        runs = FlowRun.objects.filter(contact=self.contact, exit_type=FlowRun.EXIT_TYPE_COMPLETED).order_by(
            "exited_on"
        )
        self.assertEqual(3, runs.count())
        self.assertEqual("Stacker Leaf", runs[0].flow.name)
        self.assertEqual("Stacker", runs[1].flow.name)
        self.assertEqual("Stacked", runs[2].flow.name)


class FlowChannelSelectionTest(FlowFileTest):
    def setUp(self):
        super().setUp()
        self.channel.delete()
        self.sms_channel = Channel.create(
            self.org,
            self.user,
            "RW",
            "JN",
            None,
            "+250788123123",
            schemes=["tel"],
            uuid="00000000-0000-0000-0000-000000001111",
            role=Channel.DEFAULT_ROLE,
        )

    def test_sms_channel_selection(self):
        contact_urn = self.contact.get_urn(TEL_SCHEME)
        channel = self.contact.org.get_send_channel(contact_urn=contact_urn)
        self.assertEqual(channel, self.sms_channel)


class TypeTest(TembaTest):
    @uses_legacy_engine
    def test_value_types(self):

        contact = self.create_contact("Joe", "+250788373373")
        flow = self.get_flow("type_flow")

        self.org.set_languages(self.admin, ["eng", "fra"], "eng")

        self.assertEqual(Value.TYPE_TEXT, RuleSet.objects.get(label="Text").value_type)
        self.assertEqual(Value.TYPE_DATETIME, RuleSet.objects.get(label="Date").value_type)
        self.assertEqual(Value.TYPE_NUMBER, RuleSet.objects.get(label="Number").value_type)
        self.assertEqual(Value.TYPE_STATE, RuleSet.objects.get(label="State").value_type)
        self.assertEqual(Value.TYPE_DISTRICT, RuleSet.objects.get(label="District").value_type)
        self.assertEqual(Value.TYPE_WARD, RuleSet.objects.get(label="Ward").value_type)

        incoming = self.create_msg(direction=INCOMING, contact=contact, text="types")
        legacy.flow_start(flow, groups=[], contacts=[contact], start_msg=incoming)

        self.assertTrue(legacy.find_and_handle(self.create_msg(contact=contact, direction=INCOMING, text="Some Text")))
        self.assertTrue(
            legacy.find_and_handle(self.create_msg(contact=contact, direction=INCOMING, text="not a date"))
        )

        results = FlowRun.objects.get().results

        self.assertEqual("Text", results["text"]["name"])
        self.assertEqual("Some Text", results["text"]["value"])
        self.assertEqual("Some Text", results["text"]["input"])
        self.assertEqual("All Responses", results["text"]["category"])

        self.assertEqual("Date", results["date"]["name"])
        self.assertEqual("not a date", results["date"]["value"])
        self.assertEqual("not a date", results["date"]["input"])
        self.assertEqual("Other", results["date"]["category"])

        self.assertTrue(
            legacy.find_and_handle(self.create_msg(contact=contact, direction=INCOMING, text="Born 23/06/1977"))
        )
        self.assertTrue(
            legacy.find_and_handle(self.create_msg(contact=contact, direction=INCOMING, text="The number is 10"))
        )
        self.assertTrue(
            legacy.find_and_handle(
                self.create_msg(contact=contact, direction=INCOMING, text="I'm in Eastern Province")
            )
        )
        self.assertTrue(
            legacy.find_and_handle(self.create_msg(contact=contact, direction=INCOMING, text="That's in Gatsibo"))
        )
        self.assertTrue(
            legacy.find_and_handle(self.create_msg(contact=contact, direction=INCOMING, text="ya ok that's Kageyo"))
        )

        results = FlowRun.objects.get().results

        self.assertEqual("Text", results["text"]["name"])
        self.assertEqual("Some Text", results["text"]["value"])
        self.assertEqual("Some Text", results["text"]["input"])
        self.assertEqual("All Responses", results["text"]["category"])

        self.assertEqual("Date", results["date"]["name"])
        self.assertTrue(results["date"]["value"].startswith("1977-06-23T"))
        self.assertEqual("Born 23/06/1977", results["date"]["input"])
        self.assertEqual("is a date", results["date"]["category"])

        self.assertEqual("Number", results["number"]["name"])
        self.assertEqual("10", results["number"]["value"])
        self.assertEqual("The number is 10", results["number"]["input"])
        self.assertEqual("numeric", results["number"]["category"])

        self.assertEqual("State", results["state"]["name"])
        self.assertEqual("Rwanda > Eastern Province", results["state"]["value"])
        self.assertEqual("I'm in Eastern Province", results["state"]["input"])
        self.assertEqual("state", results["state"]["category"])
        self.assertNotIn("category_localized", results["state"])

        self.assertEqual("District", results["district"]["name"])
        self.assertEqual("Rwanda > Eastern Province > Gatsibo", results["district"]["value"])
        self.assertEqual("That's in Gatsibo", results["district"]["input"])
        self.assertEqual("district", results["district"]["category"])
        self.assertEqual("le district", results["district"]["category_localized"])

        self.assertEqual("Ward", results["ward"]["name"])
        self.assertEqual("Rwanda > Eastern Province > Gatsibo > Kageyo", results["ward"]["value"])
        self.assertEqual("ya ok that's Kageyo", results["ward"]["input"])
        self.assertEqual("ward", results["ward"]["category"])


class AssetServerTest(TembaTest):
    def test_environment(self):
        self.login(self.admin)
        response = self.client.get("/flow/assets/%d/1234/environment/" % self.org.id)
        self.assertEqual(
            response.json(),
            {
                "date_format": "DD-MM-YYYY",
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
        self.org.set_languages(self.admin, ["eng", "spa"], "eng")
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


class PopulateSessionUUIDMigrationTest(MigrationTest):
    app = "flows"
    migrate_from = "0210_drop_action_log"
    migrate_to = "0211_populate_session_uuid"

    def setUpBeforeMigration(self, apps):
        # override the batch size constant
        patcher = patch("temba.flows.migrations.0211_populate_session_uuid.BATCH_SIZE", 2)
        patcher.start()
        self.addCleanup(patcher.stop)

        contact = self.create_contact("Bob", twitter="bob")

        FlowSession.objects.create(org=contact.org, contact=contact, uuid=None)
        FlowSession.objects.create(org=contact.org, contact=contact, uuid=None)
        FlowSession.objects.create(org=contact.org, contact=contact, uuid=None)

    def test_merged(self):
        self.assertEqual(FlowSession.objects.count(), 3)
        self.assertEqual(FlowSession.objects.filter(uuid=None).count(), 0)


class PopulateRunStatusMigrationTest(MigrationTest):
    app = "flows"
    migrate_from = "0213_flowrun_status"
    migrate_to = "0214_populate_run_status"

    def setUpBeforeMigration(self, apps):
        # override the batch size constant
        patcher = patch("temba.flows.migrations.0214_populate_run_status.BATCH_SIZE", 2)
        patcher.start()
        self.addCleanup(patcher.stop)

        flow1 = Flow.create_single_message(self.org, self.admin, {"eng": "Hi there"}, "eng")
        flow2 = Flow.create_single_message(self.org, self.admin, {"eng": "Goodbye"}, "eng")
        contact = self.create_contact("Bob", twitter="bob")

        completed = FlowSession.objects.create(uuid=uuid4(), org=self.org, contact=contact, status="C")
        failed = FlowSession.objects.create(uuid=uuid4(), org=self.org, contact=contact, status="F")
        waiting1 = FlowSession.objects.create(
            uuid=uuid4(), org=self.org, contact=contact, status="W", current_flow=flow1
        )
        waiting2 = FlowSession.objects.create(
            uuid=uuid4(), org=self.org, contact=contact, status="W", current_flow=flow2
        )

        def create_run(exit_type, is_active, session=None):
            FlowRun.objects.create(
                org=self.org, contact=contact, flow=flow1, exit_type=exit_type, is_active=is_active, session=session
            )

        create_run(exit_type="I", is_active=False, session=failed)
        create_run(exit_type="C", is_active=False)
        create_run(exit_type="I", is_active=False)
        create_run(exit_type="E", is_active=False)
        create_run(exit_type="Z", is_active=False)
        create_run(exit_type=None, is_active=True, session=waiting1)
        create_run(exit_type=None, is_active=True, session=waiting2)  # session is waiting but different flow
        create_run(exit_type=None, is_active=True, session=completed)  # shouldn't occur
        create_run(exit_type=None, is_active=True)
        create_run(exit_type=None, is_active=False)  # shouldn't occur

    def test_migrate(self):
        self.assertEqual(
            list(FlowRun.objects.values_list("status", flat=True).order_by("id")),
            [
                FlowRun.STATUS_FAILED,
                FlowRun.STATUS_COMPLETED,
                FlowRun.STATUS_INTERRUPTED,
                FlowRun.STATUS_EXPIRED,
                FlowRun.STATUS_COMPLETED,
                FlowRun.STATUS_WAITING,
                FlowRun.STATUS_ACTIVE,
                FlowRun.STATUS_ACTIVE,
                FlowRun.STATUS_ACTIVE,
                FlowRun.STATUS_INTERRUPTED,
            ],
        )
