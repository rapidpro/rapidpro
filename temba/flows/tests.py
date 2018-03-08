# -*- coding: utf-8 -*-
from __future__ import absolute_import, division, print_function, unicode_literals

import copy
import datetime
import json
import os
import pytz
import re
import six
import time
from uuid import uuid4
from datetime import timedelta
from decimal import Decimal

from django.utils.encoding import force_text
from mock import patch
from openpyxl import load_workbook

from django.conf import settings
from django.core.urlresolvers import reverse
from django.test.utils import override_settings
from django.utils import timezone

from temba.airtime.models import AirtimeTransfer
from temba.api.models import WebHookEvent, WebHookResult, Resthook
from temba.channels.models import Channel, ChannelEvent
from temba.contacts.models import Contact, ContactGroup, ContactField, ContactURN, URN, TEL_SCHEME
from temba.ivr.models import IVRCall
from temba.ussd.models import USSDSession
from temba.locations.models import AdminBoundary, BoundaryAlias
from temba.msgs.models import Broadcast, Label, Msg, INCOMING, PENDING, WIRED, OUTGOING, FAILED
from temba.orgs.models import Language, get_current_export_version
from temba.tests import TembaTest, MockResponse, FlowFileTest, also_in_flowserver, skip_if_no_flowserver, matchers
from temba.triggers.models import Trigger
from temba.utils.dates import datetime_to_str
from temba.utils.goflow import FlowServerException, get_client
from temba.utils.profiler import QueryTracker
from temba.values.models import Value

from .flow_migrations import (
    migrate_to_version_5, migrate_to_version_6, migrate_to_version_7, migrate_to_version_8, migrate_to_version_9,
    migrate_export_to_version_9, migrate_to_version_10_2, migrate_to_version_10_4, migrate_to_version_11_1,
    migrate_to_version_11_2, map_actions
)
from .models import (
    Flow, FlowStep, FlowRun, FlowLabel, FlowStart, FlowRevision, FlowException, ExportFlowResultsTask, ActionSet,
    RuleSet, Action, Rule, FlowRunCount, FlowPathCount, InterruptTest, get_flow_user, FlowCategoryCount,
    Test, TrueTest, FalseTest, AndTest, OrTest, PhoneTest, NumberTest, EqTest, LtTest, LteTest,
    GtTest, GteTest, BetweenTest, ContainsOnlyPhraseTest, ContainsPhraseTest, DateEqualTest, DateAfterTest,
    DateBeforeTest, DateTest, StartsWithTest, ContainsTest, ContainsAnyTest, RegexTest, NotEmptyTest, HasStateTest,
    HasDistrictTest, HasWardTest, HasEmailTest, SendAction, AddLabelAction, AddToGroupAction, ReplyAction,
    SaveToContactAction, SetLanguageAction, SetChannelAction, EmailAction, StartFlowAction, TriggerFlowAction,
    DeleteFromGroupAction, WebhookAction, ActionLog, VariableContactAction, UssdAction, FlowPathRecentRun,
    FlowUserConflictException, FlowVersionConflictException, FlowInvalidCycleException, FlowNodeCount
)

from .views import FlowCRUDL
from .tasks import update_run_expirations_task, squash_flowruncounts, squash_flowpathcounts


class FlowTest(TembaTest):

    def setUp(self):
        super(FlowTest, self).setUp()

        self.contact = self.create_contact('Eric', '+250788382382')
        self.contact2 = self.create_contact('Nic', '+250788383383')
        self.contact3 = self.create_contact('Norbert', '+250788123456')
        self.contact4 = self.create_contact('Teeh', '+250788123457', language='por')

        self.flow = self.get_flow('color')

        self.other_group = self.create_group("Other", [])

    def export_flow_results(self, flow, responded_only=False, include_msgs=True, include_runs=True, contact_fields=None, extra_urns=()):
        """
        Exports results for the given flow and returns the generated workbook
        """
        self.login(self.admin)

        form = {
            'flows': [flow.id],
            'responded_only': responded_only,
            'include_messages': include_msgs,
            'include_runs': include_runs,
            'extra_urns': extra_urns
        }
        if contact_fields:
            form['contact_fields'] = [c.id for c in contact_fields]

        response = self.client.post(reverse('flows.flow_export_results'), form)
        self.assertEqual(response.status_code, 302)

        task = ExportFlowResultsTask.objects.order_by('-id').first()
        self.assertIsNotNone(task)

        filename = "%s/test_orgs/%d/results_exports/%s.xlsx" % (settings.MEDIA_ROOT, self.org.pk, task.uuid)
        return load_workbook(filename=os.path.join(settings.MEDIA_ROOT, filename))

    def test_get_flow_user(self):
        user = get_flow_user(self.org)
        self.assertEqual(user.pk, get_flow_user(self.org).pk)

    def test_get_unique_name(self):
        flow1 = Flow.create(self.org, self.admin, Flow.get_unique_name(self.org, "Sheep Poll"), base_language='base')
        self.assertEqual(flow1.name, "Sheep Poll")

        flow2 = Flow.create(self.org, self.admin, Flow.get_unique_name(self.org, "Sheep Poll"), base_language='base')
        self.assertEqual(flow2.name, "Sheep Poll 2")

        flow3 = Flow.create(self.org, self.admin, Flow.get_unique_name(self.org, "Sheep Poll"), base_language='base')
        self.assertEqual(flow3.name, "Sheep Poll 3")

        self.create_secondary_org()
        self.assertEqual(Flow.get_unique_name(self.org2, "Sheep Poll"), "Sheep Poll")  # different org

    def test_archive_interrupt_runs(self):
        self.flow.start([], [self.contact, self.contact2])
        self.assertEqual(self.flow.runs.filter(exit_type=None).count(), 2)

        self.flow.archive()

        self.assertEqual(self.flow.runs.filter(exit_type=None).count(), 0)
        self.assertEqual(self.flow.runs.filter(exit_type=FlowRun.EXIT_TYPE_INTERRUPTED).count(), 2)

    @patch('temba.flows.views.uuid4')
    def test_upload_media_action(self, mock_uuid):
        upload_media_action_url = reverse('flows.flow_upload_media_action', args=[self.flow.pk])

        def assert_media_upload(filename, action_uuid, expected_path):
            with open(filename, 'rb') as data:
                post_data = dict(file=data, action=None, actionset='some-uuid',
                                 HTTP_X_FORWARDED_HTTPS='https')
                response = self.client.post(upload_media_action_url, post_data)

                self.assertEqual(response.status_code, 200)
                path = response.json().get('path', None)
                self.assertEqual(path, expected_path)

        self.login(self.admin)

        mock_uuid.side_effect = ['11111-111-11', '22222-222-22']

        assert_media_upload('%s/test_media/steve.marten.jpg' % settings.MEDIA_ROOT, 'action-uuid-1',
                            "attachments/%d/%d/steps/%s%s" % (self.flow.org.pk, self.flow.pk, '11111-111-11', '.jpg'))

        assert_media_upload('%s/test_media/snow.mp4' % settings.MEDIA_ROOT, 'action-uuid-2',
                            "attachments/%d/%d/steps/%s%s" % (self.flow.org.pk, self.flow.pk, '22222-222-22', '.mp4'))

    def test_revision_history(self):
        # we should initially have one revision
        revision = self.flow.revisions.get()
        self.assertEqual(revision.revision, 1)
        self.assertEqual(revision.created_by, self.flow.created_by)

        flow_json = self.flow.as_json()

        # create a new update
        self.flow.update(flow_json, user=self.admin)
        revisions = self.flow.revisions.all().order_by('created_on')

        # now we should have two revisions
        self.assertEqual(2, revisions.count())
        self.assertEqual(1, revisions[0].revision)
        self.assertEqual(2, revisions[1].revision)

        self.assertEqual(get_current_export_version(), revisions[0].spec_version)
        self.assertEqual(get_current_export_version(), revisions[0].as_json()['version'])
        self.assertEqual('base', revisions[0].get_definition_json()['base_language'])

        # now make one revision invalid
        revision = revisions[1]
        definition = revision.get_definition_json()
        del definition['base_language']
        revision.definition = definition
        revision.save()

        # should be back to one valid flow
        self.login(self.admin)
        response = self.client.get(reverse('flows.flow_revisions', args=[self.flow.pk]))
        self.assertEqual(1, len(response.json()))

        # fetch that revision
        revision_id = response.json()[0]['id']
        response = self.client.get('%s?definition=%s' % (reverse('flows.flow_revisions', args=[self.flow.pk]),
                                                         revision_id))

        # make sure we can read the definition
        definition = response.json()
        self.assertEqual('base', definition['base_language'])

        # make the last revision even more invalid (missing ruleset)
        revision = revisions[0]
        definition = revision.get_definition_json()
        del definition['rule_sets']
        revision.definition = definition
        revision.save()

        # no valid revisions (but we didn't throw!)
        response = self.client.get(reverse('flows.flow_revisions', args=[self.flow.pk]))
        self.assertEqual(0, len(response.json()))

    def test_get_localized_text(self):

        text_translations = dict(eng="Hello", spa="Hola", fra="Salut")

        # use default when flow, contact and org don't have language set
        self.assertEqual(self.flow.get_localized_text(text_translations, self.contact, "Hi"), "Hi")

        # flow language used regardless of whether it's an org language
        self.flow.base_language = 'eng'
        self.flow.save(update_fields=['base_language'])
        self.flow.org.set_languages(self.admin, ['eng'], 'eng')
        self.assertEqual(self.flow.get_localized_text(text_translations, self.contact, "Hi"), "Hello")

        # flow language now valid org language
        self.flow.org.set_languages(self.admin, ['eng', 'spa'], 'eng')
        self.assertEqual(self.flow.get_localized_text(text_translations, self.contact, "Hi"), "Hello")

        # org primary language overrides flow language
        self.flow.org.set_languages(self.admin, ['eng', 'spa'], 'spa')
        self.assertEqual(self.flow.get_localized_text(text_translations, self.contact, "Hi"), "Hola")

        # contact language doesn't override if it's not an org language
        self.contact.language = 'fra'

        self.contact.save(update_fields=('language',))
        self.assertEqual(self.flow.get_localized_text(text_translations, self.contact, "Hi"), "Hola")

        # does override if it is
        self.flow.org.set_languages(self.admin, ['eng', 'spa', 'fra'], 'fra')
        self.assertEqual(self.flow.get_localized_text(text_translations, self.contact, "Hi"), "Salut")

    def test_flow_lists(self):
        self.login(self.admin)

        # add another flow
        flow2 = self.get_flow('no_ruleset_flow')

        # and archive it right off the bat
        flow2.is_archived = True
        flow2.save()

        flow3 = Flow.create(self.org, self.admin, "Flow 3", base_language='base')

        # see our trigger on the list page
        response = self.client.get(reverse('flows.flow_list'))
        self.assertContains(response, self.flow.name)
        self.assertContains(response, flow3.name)
        self.assertEqual(2, response.context['folders'][0]['count'])
        self.assertEqual(1, response.context['folders'][1]['count'])

        # archive it
        post_data = dict(action='archive', objects=self.flow.pk)
        self.client.post(reverse('flows.flow_list'), post_data)
        response = self.client.get(reverse('flows.flow_list'))
        self.assertNotContains(response, self.flow.name)
        self.assertContains(response, flow3.name)
        self.assertEqual(1, response.context['folders'][0]['count'])
        self.assertEqual(2, response.context['folders'][1]['count'])

        response = self.client.get(reverse('flows.flow_archived'), post_data)
        self.assertContains(response, self.flow.name)

        # flow2 should appear before flow since it was created later
        self.assertTrue(flow2, response.context['object_list'][0])
        self.assertTrue(self.flow, response.context['object_list'][1])

        # unarchive it
        post_data = dict(action='restore', objects=self.flow.pk)
        self.client.post(reverse('flows.flow_archived'), post_data)
        response = self.client.get(reverse('flows.flow_archived'), post_data)
        self.assertNotContains(response, self.flow.name)
        response = self.client.get(reverse('flows.flow_list'), post_data)
        self.assertContains(response, self.flow.name)
        self.assertContains(response, flow3.name)
        self.assertEqual(2, response.context['folders'][0]['count'])
        self.assertEqual(1, response.context['folders'][1]['count'])

        # voice flows should be included in the count
        Flow.objects.filter(pk=self.flow.pk).update(flow_type=Flow.VOICE)

        response = self.client.get(reverse('flows.flow_list'))
        self.assertContains(response, self.flow.name)
        self.assertEqual(2, response.context['folders'][0]['count'])
        self.assertEqual(1, response.context['folders'][1]['count'])

        # single message flow (flom campaign) should not be included in counts and not even on this list
        Flow.objects.filter(pk=self.flow.pk).update(flow_type=Flow.MESSAGE)

        response = self.client.get(reverse('flows.flow_list'))

        self.assertNotContains(response, self.flow.name)
        self.assertEqual(1, response.context['folders'][0]['count'])
        self.assertEqual(1, response.context['folders'][1]['count'])

        # single message flow should not be even in the archived list
        Flow.objects.filter(pk=self.flow.pk).update(flow_type=Flow.MESSAGE, is_archived=True)

        response = self.client.get(reverse('flows.flow_archived'))
        self.assertNotContains(response, self.flow.name)
        self.assertEqual(1, response.context['folders'][0]['count'])
        self.assertEqual(1, response.context['folders'][1]['count'])  # only flow2

    def test_campaign_filter(self):
        self.login(self.admin)
        self.get_flow('the_clinic')

        # should have a list of four flows for our appointment schedule
        response = self.client.get(reverse('flows.flow_list'))
        self.assertContains(response, 'Appointment Schedule (4)')

        from temba.campaigns.models import Campaign
        campaign = Campaign.objects.filter(name='Appointment Schedule').first()
        self.assertIsNotNone(campaign)

        # check that our four flows in the campaign are there
        response = self.client.get(reverse('flows.flow_campaign', args=[campaign.id]))
        self.assertContains(response, 'Confirm Appointment')
        self.assertContains(response, 'Start Notifications')
        self.assertContains(response, 'Stop Notifications')
        self.assertContains(response, 'Appointment Followup')

    def test_flow_archive_with_campaign(self):
        self.login(self.admin)
        self.get_flow('the_clinic')

        from temba.campaigns.models import Campaign
        campaign = Campaign.objects.filter(name='Appointment Schedule').first()
        self.assertIsNotNone(campaign)
        flow = Flow.objects.filter(name="Confirm Appointment").first()
        self.assertIsNotNone(flow)

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

    def test_flows_select2(self):
        self.login(self.admin)

        msg = Flow.create(self.org, self.admin, Flow.get_unique_name(self.org, "Message Flow"), base_language='base', flow_type=Flow.FLOW)
        survey = Flow.create(self.org, self.admin, Flow.get_unique_name(self.org, "Surveyor Flow"), base_language='base', flow_type=Flow.SURVEY)
        ivr = Flow.create(self.org, self.admin, Flow.get_unique_name(self.org, "IVR Flow"), base_language='base', flow_type=Flow.VOICE)

        # all flow types
        response = self.client.get('%s?_format=select2' % reverse('flows.flow_list'))
        self.assertContains(response, ivr.name)
        self.assertContains(response, survey.name)
        self.assertContains(response, msg.name)

        # only surveyor flows
        response = self.client.get('%s?_format=select2&flow_type=S' % reverse('flows.flow_list'))
        self.assertContains(response, survey.name)
        self.assertNotContains(response, ivr.name)
        self.assertNotContains(response, msg.name)

        # only voice flows
        response = self.client.get('%s?_format=select2&flow_type=V' % reverse('flows.flow_list'))
        self.assertContains(response, ivr.name)
        self.assertNotContains(response, survey.name)
        self.assertNotContains(response, msg.name)

        # only text flows
        response = self.client.get('%s?_format=select2&flow_type=F' % reverse('flows.flow_list'))
        self.assertContains(response, msg.name)
        self.assertNotContains(response, survey.name)
        self.assertNotContains(response, ivr.name)

        # two at a time
        response = self.client.get('%s?_format=select2&flow_type=V&flow_type=F' % reverse('flows.flow_list'))
        self.assertContains(response, ivr.name)
        self.assertContains(response, msg.name)
        self.assertNotContains(response, survey.name)

    def test_flow_editor(self):
        self.login(self.admin)
        response = self.client.get(reverse('flows.flow_editor', args=[self.flow.uuid]))
        self.assertTrue(response.context['mutable'])
        self.assertFalse(response.context['has_airtime_service'])
        self.assertFalse(response.context['is_starting'])
        self.assertFalse(response.context['has_ussd_channel'])

        # superusers can't edit flows
        self.login(self.superuser)
        response = self.client.get(reverse('flows.flow_editor', args=[self.flow.uuid]))
        self.assertFalse(response.context['mutable'])

    def test_states(self):
        # set our flow
        color_prompt = ActionSet.objects.get(x=1, y=1)
        color_ruleset = RuleSet.objects.get(label="color")
        orange_rule = color_ruleset.get_rules()[0]
        color_reply = ActionSet.objects.get(x=2, y=2)

        # how many people in the flow?
        self.assertEqual(self.flow.get_run_stats(),
                         {'total': 0, 'active': 0, 'completed': 0, 'expired': 0, 'interrupted': 0, 'completion': 0})

        # start the flow
        self.flow.start([], [self.contact, self.contact2])

        # test our stats again
        self.assertEqual(self.flow.get_run_stats(),
                         {'total': 2, 'active': 2, 'completed': 0, 'expired': 0, 'interrupted': 0, 'completion': 0})

        # should have created a single broadcast
        broadcast = Broadcast.objects.get()
        self.assertEqual(
            broadcast.text, {'base': "What is your favorite color?", 'fra': "Quelle est votre couleur préférée?"}
        )
        self.assertEqual(set(broadcast.contacts.all()), {self.contact, self.contact2})
        self.assertEqual(broadcast.base_language, 'base')

        # each contact should have received a single message
        contact1_msg = broadcast.msgs.get(contact=self.contact)
        self.assertEqual(contact1_msg.text, "What is your favorite color?")
        self.assertEqual(contact1_msg.status, PENDING)
        self.assertFalse(contact1_msg.high_priority)

        # should have a flow run for each contact
        contact1_run = FlowRun.objects.get(contact=self.contact)
        contact2_run = FlowRun.objects.get(contact=self.contact2)

        self.assertEqual(contact1_run.flow, self.flow)
        self.assertEqual(contact1_run.contact, self.contact)
        self.assertFalse(contact1_run.responded)
        self.assertFalse(contact2_run.responded)

        # check the path for contact 1
        self.assertEqual(contact1_run.path, [
            {'node_uuid': str(color_prompt.uuid), 'arrived_on': matchers.ISODate(), 'exit_uuid': str(color_prompt.exit_uuid)},
            {'node_uuid': str(color_ruleset.uuid), 'arrived_on': matchers.ISODate()}
        ])

        # test our message context
        context = self.flow.build_expressions_context(self.contact, None)
        self.assertEqual(context['flow']['__default__'], "")
        self.assertIn('contact', context)

        # check flow activity endpoint response
        self.login(self.admin)

        test_contact = Contact.get_test_contact(self.admin)

        activity = self.client.get(reverse('flows.flow_activity', args=[self.flow.pk])).json()
        self.assertEqual(2, activity['visited'][color_prompt.exit_uuid + ":" + color_ruleset.uuid])
        self.assertEqual(2, activity['activity'][color_ruleset.uuid])
        self.assertFalse(activity['is_starting'])

        # check activity with IVR test call
        IVRCall.create_incoming(self.channel, test_contact, test_contact.get_urn(), self.admin, 'CallSid')
        activity = self.client.get(reverse('flows.flow_activity', args=[self.flow.pk])).json()
        self.assertEqual(2, activity['visited'][color_prompt.exit_uuid + ":" + color_ruleset.uuid])
        self.assertEqual(2, activity['activity'][color_ruleset.uuid])

        # set the flow as inactive, shouldn't react to replies
        self.flow.is_archived = True
        self.flow.save()

        # create and send a reply
        incoming = self.create_msg(direction=INCOMING, contact=self.contact, text="Orange")
        self.assertFalse(Flow.find_and_handle(incoming)[0])

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
        self.assertTrue(Flow.find_and_handle(incoming)[0])

        # our message should have gotten a reply
        reply = Msg.objects.get(response_to=incoming)
        self.assertEqual(reply.contact, self.contact)
        self.assertEqual(reply.text, "I love orange too! You said: orange which is category: "
                                     "Orange You are: 0788 382 382 SMS: orange Flow: color: orange")
        self.assertEqual(reply.msg_type, 'F')
        self.assertTrue(reply.high_priority)  # should be high priority as this is a reply

        contact1_run.refresh_from_db()
        contact1_run_msgs = contact1_run.get_messages()

        self.assertTrue(contact1_run.responded)
        self.assertEqual(len(contact1_run_msgs), 3)
        self.assertIn(incoming, contact1_run_msgs)
        self.assertIn(reply, contact1_run_msgs)

        # check our completion percentages
        self.assertEqual(self.flow.get_run_stats(),
                         {'total': 2, 'active': 1, 'completed': 1, 'expired': 0, 'interrupted': 0, 'completion': 50})

        # at this point there are no more steps to take in the flow, so we shouldn't match anymore
        extra = self.create_msg(direction=INCOMING, contact=self.contact, text="Hello ther")
        self.assertFalse(Flow.find_and_handle(extra)[0])

        self.assertEqual(contact1_run.path, [
            {'node_uuid': str(color_prompt.uuid), 'arrived_on': matchers.ISODate(), 'exit_uuid': str(color_prompt.exit_uuid)},
            {'node_uuid': str(color_ruleset.uuid), 'arrived_on': matchers.ISODate(), 'exit_uuid': str(orange_rule.uuid)},
            {'node_uuid': str(color_reply.uuid), 'arrived_on': matchers.ISODate()}
        ])

        # we should also have a result for this RuleSet
        self.assertEqual(contact1_run.results, {
            'color': {
                'category': 'Orange',
                'node_uuid': str(color_ruleset.uuid),
                'name': 'color',
                'value': 'orange',
                'created_on': matchers.ISODate(),
                'input': 'orange'
            }
        })

        # check what our message context looks like now
        context = self.flow.build_expressions_context(self.contact, incoming)
        self.assertTrue(context['flow'])
        self.assertEqual("color: orange", context['flow']['__default__'])
        self.assertEqual("orange", six.text_type(context['flow']['color']['__default__']))
        self.assertEqual("orange", six.text_type(context['flow']['color']['value']))
        self.assertEqual("Orange", context['flow']['color']['category'])
        self.assertEqual("orange", context['flow']['color']['text'])
        self.assertIsNotNone(context['flow']['color']['time'])

        self.assertEqual(self.channel.get_address_display(e164=True), context['channel']['tel_e164'])
        self.assertEqual(self.channel.get_address_display(), context['channel']['tel'])
        self.assertEqual(self.channel.get_name(), context['channel']['name'])
        self.assertEqual(self.channel.get_address_display(), context['channel']['__default__'])

        # change our value instead be decimal
        results = contact1_run.results
        results['color']['value'] = '10'
        contact1_run.results = results
        contact1_run.save(update_fields=('results',))

        # check our message context again
        context = self.flow.build_expressions_context(self.contact, incoming)
        self.assertEqual('10', context['flow']['color']['value'])
        self.assertEqual('Orange', context['flow']['color']['category'])

        # this is drawn from the message which didn't change
        self.assertEqual('orange', context['flow']['color']['text'])

    def test_anon_export_results(self):
        self.org.is_anon = True
        self.org.save()

        (run1,) = self.flow.start([], [self.contact])

        msg = self.create_msg(direction=INCOMING, contact=self.contact, text="orange")
        Flow.find_and_handle(msg)

        run1.refresh_from_db()

        workbook = self.export_flow_results(self.flow)
        sheet_runs, sheet_contacts, sheet_msgs = workbook.worksheets
        self.assertExcelRow(sheet_runs, 0, ["Contact UUID", "ID", "Name", "Groups", "Started", "Exited",
                                            "color (Category) - Color Flow",
                                            "color (Value) - Color Flow",
                                            "color (Text) - Color Flow"])

        self.assertExcelRow(sheet_runs, 1, [self.contact.uuid, six.text_type(self.contact.id), "Eric", "",
                                            run1.created_on, run1.exited_on,
                                            "Orange", "orange", "orange"], self.org.timezone)

        self.assertExcelRow(sheet_contacts, 0, ["Contact UUID", "ID", "Name", "Groups",
                                                "color (Category) - Color Flow",
                                                "color (Value) - Color Flow",
                                                "color (Text) - Color Flow"])

        self.assertExcelRow(sheet_contacts, 1, [self.contact.uuid, six.text_type(self.contact.id), "Eric", "",
                                                "Orange", "orange", "orange"], self.org.timezone)

        self.assertExcelRow(sheet_msgs, 0, ["Contact UUID", "ID", "Name", "Date", "Direction", "Message", "Channel"])
        self.assertExcelRow(sheet_msgs, 2, [self.contact.uuid, six.text_type(self.contact.id), "Eric",
                                            msg.created_on, "IN",
                                            "orange", "Test Channel"], self.org.timezone)

    def test_export_results_broadcast_only_flow(self):
        self.login(self.admin)

        flow = self.get_flow('two_in_row')
        contact1_run1, contact2_run1, contact3_run1 = flow.start([], [self.contact, self.contact2, self.contact3])
        contact1_run2, contact2_run2 = flow.start([], [self.contact, self.contact2], restart_participants=True)

        for run in (contact1_run1, contact2_run1, contact3_run1, contact1_run2, contact2_run2):
            run.refresh_from_db()

        with self.assertNumQueries(44):
            workbook = self.export_flow_results(flow)

        tz = self.org.timezone

        sheet_runs, sheet_contacts, sheet_msgs = workbook.worksheets

        # check runs sheet...
        self.assertEqual(len(list(sheet_runs.rows)), 6)  # header + 5 runs
        self.assertEqual(len(list(sheet_runs.columns)), 6)

        self.assertExcelRow(sheet_runs, 0, ["Contact UUID", "URN", "Name", "Groups", "Started", "Exited"])

        self.assertExcelRow(sheet_runs, 1, [contact1_run1.contact.uuid, "+250788382382", "Eric", "bootstrap 3",
                                            contact1_run1.created_on, contact1_run1.exited_on], tz)
        self.assertExcelRow(sheet_runs, 2, [contact1_run2.contact.uuid, "+250788382382", "Eric", "bootstrap 3",
                                            contact1_run2.created_on, contact1_run2.exited_on], tz)
        self.assertExcelRow(sheet_runs, 3, [contact2_run1.contact.uuid, "+250788383383", "Nic", "bootstrap 3",
                                            contact2_run1.created_on, contact2_run1.exited_on], tz)
        self.assertExcelRow(sheet_runs, 4, [contact2_run2.contact.uuid, "+250788383383", "Nic", "bootstrap 3",
                                            contact2_run2.created_on, contact2_run2.exited_on], tz)

        # check contacts sheet...
        self.assertEqual(len(list(sheet_contacts.rows)), 4)  # header + 3 contacts
        self.assertEqual(len(list(sheet_contacts.columns)), 4)

        self.assertExcelRow(sheet_contacts, 0, ["Contact UUID", "URN", "Name", "Groups"])
        self.assertExcelRow(sheet_contacts, 1, [contact1_run1.contact.uuid, "+250788382382", "Eric", "bootstrap 3"], tz)
        self.assertExcelRow(sheet_contacts, 2, [contact2_run1.contact.uuid, "+250788383383", "Nic", "bootstrap 3"], tz)
        self.assertExcelRow(sheet_contacts, 3, [contact3_run1.contact.uuid, "+250788123456", "Norbert", "bootstrap 3"], tz)

        # check messages sheet...
        self.assertEqual(len(list(sheet_msgs.rows)), 11)  # header + 10 messages
        self.assertEqual(len(list(sheet_msgs.columns)), 7)

        self.assertExcelRow(sheet_msgs, 0, ["Contact UUID", "URN", "Name", "Date", "Direction", "Message", "Channel"])

        c1_run1_msg1 = Msg.objects.get(steps__run=contact1_run1, text="This is the first message.")
        c1_run1_msg2 = Msg.objects.get(steps__run=contact1_run1, text="This is the second message.")

        c2_run1_msg1 = Msg.objects.get(steps__run=contact2_run1, text="This is the first message.")
        c2_run1_msg2 = Msg.objects.get(steps__run=contact2_run1, text="This is the second message.")

        c3_run1_msg1 = Msg.objects.get(steps__run=contact3_run1, text="This is the first message.")
        c3_run1_msg2 = Msg.objects.get(steps__run=contact3_run1, text="This is the second message.")

        c1_run2_msg1 = Msg.objects.get(steps__run=contact1_run2, text="This is the first message.")
        c1_run2_msg2 = Msg.objects.get(steps__run=contact1_run2, text="This is the second message.")

        c2_run2_msg1 = Msg.objects.get(steps__run=contact2_run2, text="This is the first message.")
        c2_run2_msg2 = Msg.objects.get(steps__run=contact2_run2, text="This is the second message.")

        self.assertExcelRow(sheet_msgs, 1, [c1_run1_msg1.contact.uuid, "+250788382382", "Eric",
                                            c1_run1_msg1.created_on, "OUT",
                                            "This is the first message.", "Test Channel"], tz)

        self.assertExcelRow(sheet_msgs, 2, [c1_run1_msg2.contact.uuid, "+250788382382", "Eric",
                                            c1_run1_msg2.created_on, "OUT",
                                            "This is the second message.", "Test Channel"], tz)

        self.assertExcelRow(sheet_msgs, 3, [c1_run2_msg1.contact.uuid, "+250788382382", "Eric",
                                            c1_run2_msg1.created_on, "OUT",
                                            "This is the first message.", "Test Channel"], tz)

        self.assertExcelRow(sheet_msgs, 4, [c1_run2_msg2.contact.uuid, "+250788382382", "Eric",
                                            c1_run2_msg2.created_on, "OUT",
                                            "This is the second message.", "Test Channel"], tz)

        self.assertExcelRow(sheet_msgs, 5, [c2_run1_msg1.contact.uuid, "+250788383383", "Nic",
                                            c2_run1_msg1.created_on, "OUT",
                                            "This is the first message.", "Test Channel"], tz)

        self.assertExcelRow(sheet_msgs, 6, [c2_run1_msg2.contact.uuid, "+250788383383", "Nic",
                                            c2_run1_msg2.created_on, "OUT",
                                            "This is the second message.", "Test Channel"], tz)

        self.assertExcelRow(sheet_msgs, 7, [c2_run2_msg1.contact.uuid, "+250788383383", "Nic",
                                            c2_run2_msg1.created_on, "OUT",
                                            "This is the first message.", "Test Channel"], tz)

        self.assertExcelRow(sheet_msgs, 8, [c2_run2_msg2.contact.uuid, "+250788383383", "Nic",
                                            c2_run2_msg2.created_on, "OUT",
                                            "This is the second message.", "Test Channel"], tz)

        self.assertExcelRow(sheet_msgs, 9, [c3_run1_msg1.contact.uuid, "+250788123456", "Norbert",
                                            c3_run1_msg1.created_on, "OUT",
                                            "This is the first message.", "Test Channel"], tz)

        self.assertExcelRow(sheet_msgs, 10, [c3_run1_msg2.contact.uuid, "+250788123456", "Norbert",
                                             c3_run1_msg2.created_on, "OUT",
                                             "This is the second message.", "Test Channel"], tz)

        # test without msgs or runs or unresponded
        with self.assertNumQueries(34):
            workbook = self.export_flow_results(flow, include_msgs=False, include_runs=False, responded_only=True)

        tz = self.org.timezone
        sheet_contacts = workbook.worksheets[0]

        self.assertEqual(len(list(sheet_contacts.rows)), 1)  # header; no resposes to a broadcast only flow
        self.assertEqual(len(list(sheet_contacts.columns)), 4)

        self.assertExcelRow(sheet_contacts, 0, ["Contact UUID", "URN", "Name", "Groups"])

    def test_export_results(self):
        # setup flow and start both contacts
        self.contact.update_urns(self.admin, ['tel:+250788382382', 'twitter:erictweets'])

        self.create_group('Devs', [self.contact])

        # contact name with an illegal character
        self.contact3.name = "Nor\02bert"
        self.contact3.save()

        contact1_run1, contact2_run1, contact3_run1 = self.flow.start([], [self.contact, self.contact2, self.contact3])

        # simulate two runs each for two contacts...
        contact1_in1 = self.create_msg(direction=INCOMING, contact=self.contact, text="light beige")
        Flow.find_and_handle(contact1_in1)

        contact1_in2 = self.create_msg(direction=INCOMING, contact=self.contact, text="orange")
        Flow.find_and_handle(contact1_in2)

        contact2_in1 = self.create_msg(direction=INCOMING, contact=self.contact2, text="green")
        Flow.find_and_handle(contact2_in1)

        contact1_run2, contact2_run2 = self.flow.start([], [self.contact, self.contact2], restart_participants=True)

        contact1_in3 = self.create_msg(direction=INCOMING, contact=self.contact, text=" blue ")
        Flow.find_and_handle(contact1_in3)

        # check can't export anonymously
        exported = self.client.get(reverse('flows.flow_export_results') + "?ids=%d" % self.flow.pk)
        self.assertEqual(302, exported.status_code)

        self.login(self.admin)

        # create a dummy export task so that we won't be able to export
        blocking_export = ExportFlowResultsTask.objects.create(org=self.org, created_by=self.admin, modified_by=self.admin)
        response = self.client.post(reverse('flows.flow_export_results'), dict(flows=[self.flow.pk]), follow=True)
        self.assertContains(response, "already an export in progress")

        # ok, mark that one as finished and try again
        blocking_export.update_status(ExportFlowResultsTask.STATUS_COMPLETE)

        for run in (contact1_run1, contact2_run1, contact3_run1, contact1_run2, contact2_run2):
            run.refresh_from_db()

        with self.assertNumQueries(43):
            workbook = self.export_flow_results(self.flow)

        tz = self.org.timezone

        sheet_runs, sheet_contacts, sheet_msgs = workbook.worksheets

        # check runs sheet...
        self.assertEqual(len(list(sheet_runs.rows)), 6)  # header + 5 runs
        self.assertEqual(len(list(sheet_runs.columns)), 9)

        self.assertExcelRow(sheet_runs, 0, ["Contact UUID", "URN", "Name", "Groups", "Started", "Exited",
                                            "color (Category) - Color Flow",
                                            "color (Value) - Color Flow",
                                            "color (Text) - Color Flow"])

        self.assertExcelRow(sheet_runs, 1, [contact1_run1.contact.uuid, "+250788382382", "Eric", "Devs",
                                            contact1_run1.created_on, contact1_run1.exited_on,
                                            "Orange", "orange", "orange"], tz)

        self.assertExcelRow(sheet_runs, 2, [contact1_run2.contact.uuid, "+250788382382", "Eric", "Devs",
                                            contact1_run2.created_on, contact1_run2.exited_on,
                                            "Blue", "blue", " blue "], tz)

        self.assertExcelRow(sheet_runs, 3, [contact2_run1.contact.uuid, "+250788383383", "Nic", "",
                                            contact2_run1.created_on, contact2_run1.exited_on,
                                            "Other", "green", "green"], tz)

        self.assertExcelRow(sheet_runs, 4, [contact2_run2.contact.uuid, "+250788383383", "Nic", "",
                                            contact2_run2.created_on, "",
                                            "", "", ""], tz)

        # check contacts sheet...
        self.assertEqual(len(list(sheet_contacts.rows)), 4)  # header + 3 contacts
        self.assertEqual(len(list(sheet_contacts.columns)), 7)

        self.assertExcelRow(sheet_contacts, 0, ["Contact UUID", "URN", "Name", "Groups",
                                                "color (Category) - Color Flow",
                                                "color (Value) - Color Flow",
                                                "color (Text) - Color Flow"])

        self.assertExcelRow(sheet_contacts, 1, [contact1_run1.contact.uuid, "+250788382382", "Eric", "Devs",
                                                "Blue", "blue", " blue "], tz)

        self.assertExcelRow(sheet_contacts, 2, [contact2_run1.contact.uuid, "+250788383383", "Nic", "",
                                                "Other", "green", "green"], tz)

        self.assertExcelRow(sheet_contacts, 3, [contact3_run1.contact.uuid, "+250788123456", "Norbert", "",
                                                "", "", ""], tz)

        # check messages sheet...
        self.assertEqual(len(list(sheet_msgs.rows)), 14)  # header + 13 messages
        self.assertEqual(len(list(sheet_msgs.columns)), 7)

        self.assertExcelRow(sheet_msgs, 0, ["Contact UUID", "URN", "Name", "Date", "Direction", "Message", "Channel"])

        contact1_out1 = Msg.objects.get(steps__run=contact1_run1, text="What is your favorite color?")
        contact1_out2 = Msg.objects.get(steps__run=contact1_run1, text="That is a funny color. Try again.")
        contact1_out3 = Msg.objects.get(steps__run=contact1_run1, text__startswith="I love orange too")

        self.assertExcelRow(sheet_msgs, 1, [contact1_out1.contact.uuid, "+250788382382", "Eric",
                                            contact1_out1.created_on, "OUT",
                                            "What is your favorite color?", "Test Channel"], tz)
        self.assertExcelRow(sheet_msgs, 2, [contact1_in1.contact.uuid, "+250788382382", "Eric", contact1_in1.created_on,
                                            "IN", "light beige", "Test Channel"], tz)
        self.assertExcelRow(sheet_msgs, 3, [contact1_out2.contact.uuid, "+250788382382", "Eric",
                                            contact1_out2.created_on, "OUT",
                                            "That is a funny color. Try again.", "Test Channel"], tz)
        self.assertExcelRow(sheet_msgs, 4, [contact1_in2.contact.uuid, "+250788382382", "Eric", contact1_in2.created_on,
                                            "IN", "orange", "Test Channel"], tz)
        self.assertExcelRow(sheet_msgs, 5, [contact1_out3.contact.uuid, "+250788382382", "Eric",
                                            contact1_out3.created_on, "OUT",
                                            "I love orange too! You said: orange which is category: Orange You are: "
                                            "0788 382 382 SMS: orange Flow: color: orange",
                                            "Test Channel"], tz)

        # test without msgs or runs or unresponded
        with self.assertNumQueries(41):
            workbook = self.export_flow_results(self.flow, include_msgs=False, include_runs=False, responded_only=True)

        tz = self.org.timezone
        sheet_contacts = workbook.worksheets[0]

        self.assertEqual(len(list(sheet_contacts.rows)), 3)  # header + 2 contacts
        self.assertEqual(len(list(sheet_contacts.columns)), 7)

        self.assertExcelRow(sheet_contacts, 0, ["Contact UUID", "URN", "Name", "Groups",
                                                "color (Category) - Color Flow",
                                                "color (Value) - Color Flow",
                                                "color (Text) - Color Flow"])

        self.assertExcelRow(sheet_contacts, 1, [contact1_run1.contact.uuid, "+250788382382", "Eric", "Devs",
                                                "Blue", "blue", " blue "], tz)

        self.assertExcelRow(sheet_contacts, 2, [contact2_run1.contact.uuid, "+250788383383", "Nic", "",
                                                "Other", "green", "green"], tz)

        # test export with a contact field
        age = ContactField.get_or_create(self.org, self.admin, 'age', "Age")
        self.contact.set_field(self.admin, 'age', 36)

        # insert a duplicate age field, this can happen due to races
        Value.objects.create(org=self.org, contact=self.contact, contact_field=age, string_value='36', decimal_value='36')

        with self.assertNumQueries(44):
            workbook = self.export_flow_results(self.flow, include_msgs=False, include_runs=True, responded_only=True,
                                                contact_fields=[age], extra_urns=['twitter', 'line'])

        # try setting the field again
        self.contact.set_field(self.admin, 'age', 36)

        # only one present now
        self.assertEqual(Value.objects.filter(contact=self.contact, contact_field=age).count(), 1)

        tz = self.org.timezone
        sheet_runs, sheet_contacts = workbook.worksheets

        self.assertEqual(len(list(sheet_contacts.rows)), 3)  # header + 2 contacts
        self.assertEqual(len(list(sheet_contacts.columns)), 10)

        self.assertExcelRow(sheet_contacts, 0, ["Contact UUID", "URN", "Twitter", "Line", "Name", "Groups", "Age",
                                                "color (Category) - Color Flow",
                                                "color (Value) - Color Flow",
                                                "color (Text) - Color Flow"])

        self.assertExcelRow(sheet_contacts, 1, [contact1_run1.contact.uuid, "+250788382382", "erictweets", "", "Eric",
                                                "Devs", "36", "Blue",
                                                "blue", " blue "], tz)

        self.assertExcelRow(sheet_contacts, 2, [contact2_run1.contact.uuid, "+250788383383", "", "", "Nic",
                                                "", "", "Other", "green", "green"], tz)

        # check runs sheet...
        self.assertEqual(len(list(sheet_runs.rows)), 4)  # header + 3 runs
        self.assertEqual(len(list(sheet_runs.columns)), 12)

        self.assertExcelRow(sheet_runs, 0, ["Contact UUID", "URN", "Twitter", "Line", "Name", "Groups", "Age",
                                            "Started", "Exited",
                                            "color (Category) - Color Flow",
                                            "color (Value) - Color Flow",
                                            "color (Text) - Color Flow"])

        self.assertExcelRow(sheet_runs, 1, [contact1_run1.contact.uuid, "+250788382382", "erictweets", "", "Eric", "Devs", "36",
                                            contact1_run1.created_on, contact1_run1.exited_on,
                                            "Orange", "orange", "orange"], tz)

        # test that we don't exceed the limit on rows per sheet
        with patch('temba.flows.models.ExportFlowResultsTask.MAX_EXCEL_ROWS', 4):
            workbook = self.export_flow_results(self.flow)
            expected_sheets = [("Runs", 4), ("Runs (2)", 3), ("Contacts", 4), ("Messages", 4),
                               ("Messages (2)", 4), ("Messages (3)", 4), ("Messages (4)", 4), ("Messages (5)", 2)]

            for s, sheet in enumerate(workbook.worksheets):
                self.assertEqual((sheet.title, len(list(sheet.rows))), expected_sheets[s])

        # test we can export archived flows
        self.flow.is_archived = True
        self.flow.save()

        workbook = self.export_flow_results(self.flow)

        tz = self.org.timezone

        sheet_runs, sheet_contacts, sheet_msgs = workbook.worksheets

        # check runs sheet...
        self.assertEqual(len(list(sheet_runs.rows)), 6)  # header + 5 runs
        self.assertEqual(len(list(sheet_runs.columns)), 9)

        # check contacts sheet...
        self.assertEqual(len(list(sheet_contacts.rows)), 4)  # header + 3 contacts
        self.assertEqual(len(list(sheet_contacts.columns)), 7)

        # check messages sheet...
        self.assertEqual(len(list(sheet_msgs.rows)), 14)  # header + 13 messages
        self.assertEqual(len(list(sheet_msgs.columns)), 7)

    def test_export_results_list_messages_once(self):
        contact1_run1 = self.flow.start([], [self.contact])[0]

        contact1_in1 = self.create_msg(direction=INCOMING, contact=self.contact, text="Red")
        Flow.find_and_handle(contact1_in1)

        contact1_run1_rs = FlowStep.objects.filter(run=contact1_run1, step_type='R')
        contact1_out1 = Msg.objects.get(steps__run=contact1_run1, text="What is your favorite color?")
        contact1_out2 = Msg.objects.get(steps__run=contact1_run1, text="That is a funny color. Try again.")

        # consider msg is also on the second step too to test it is not exported in two rows
        contact1_run1_rs.last().messages.add(contact1_in1)

        tz = self.org.timezone
        workbook = self.export_flow_results(self.flow)

        sheet_runs, sheet_contacts, sheet_msgs = workbook.worksheets

        self.assertEqual(len(list(sheet_msgs.rows)), 4)  # header + 2 msgs

        self.assertExcelRow(sheet_msgs, 0, ["Contact UUID", "URN", "Name", "Date", "Direction", "Message", "Channel"])

        self.assertExcelRow(sheet_msgs, 1, [contact1_out1.contact.uuid, "+250788382382", "Eric",
                                            contact1_out1.created_on, "OUT",
                                            "What is your favorite color?", "Test Channel"], tz)

        self.assertExcelRow(sheet_msgs, 2, [contact1_run1.contact.uuid, "+250788382382", "Eric",
                                            contact1_in1.created_on, 'IN', "Red", "Test Channel"], tz)

        self.assertExcelRow(sheet_msgs, 3, [contact1_out2.contact.uuid, "+250788382382", "Eric",
                                            contact1_out2.created_on, "OUT",
                                            "That is a funny color. Try again.", "Test Channel"], tz)

    def test_export_results_remove_control_characters(self):
        contact1_run1 = self.flow.start([], [self.contact])[0]

        contact1_in1 = self.create_msg(direction=INCOMING, contact=self.contact, text="ngert\x07in.")
        Flow.find_and_handle(contact1_in1)

        contact1_run1.refresh_from_db()

        workbook = self.export_flow_results(self.flow)

        tz = self.org.timezone

        sheet_runs, sheet_contacts, sheet_msgs = workbook.worksheets

        # check runs sheet...
        self.assertEqual(len(list(sheet_runs.rows)), 2)  # header + 1 runs
        self.assertEqual(len(list(sheet_runs.columns)), 9)

        self.assertExcelRow(sheet_runs, 0, ["Contact UUID", "URN", "Name", "Groups", "Started", "Exited",
                                            "color (Category) - Color Flow",
                                            "color (Value) - Color Flow",
                                            "color (Text) - Color Flow"])

        self.assertExcelRow(sheet_runs, 1, [contact1_run1.contact.uuid, "+250788382382", "Eric", "",
                                            contact1_run1.created_on, "",
                                            "Other", "ngertin.", "ngertin."], tz)

    def test_export_results_with_surveyor_msgs(self):
        self.flow.flow_type = Flow.SURVEY
        self.flow.save()
        run = self.flow.start([], [self.contact])[0]

        # no urn or channel
        in1 = Msg.create_incoming(None, None, "blue", org=self.org, contact=self.contact)

        workbook = self.export_flow_results(self.flow)
        tz = self.org.timezone

        sheet_runs, sheet_contacts, sheet_msgs = workbook.worksheets

        run.refresh_from_db()

        # no submitter for our run
        self.assertExcelRow(sheet_runs, 1, ["", run.contact.uuid, "+250788382382", "Eric", "",
                                            run.created_on, run.exited_on,
                                            "Blue", "blue", "blue"], tz)

        out1 = Msg.objects.get(steps__run=run, text="What is your favorite color?")

        self.assertExcelRow(sheet_msgs, 1, [run.contact.uuid, "+250788382382", "Eric", out1.created_on, "OUT",
                                            "What is your favorite color?", "Test Channel"], tz)

        # no channel or phone
        self.assertExcelRow(sheet_msgs, 2, [run.contact.uuid, "", "Eric", in1.created_on, "IN", "blue", ""], tz)

        # now try setting a submitted by on our run
        run.submitted_by = self.admin
        run.save(update_fields=('submitted_by',))

        workbook = self.export_flow_results(self.flow)
        tz = self.org.timezone

        sheet_runs, sheet_contacts, sheet_msgs = workbook.worksheets

        # now the Administrator should show up
        self.assertExcelRow(sheet_runs, 1, ["Administrator", run.contact.uuid, "+250788382382", "Eric", "",
                                            run.created_on, run.exited_on,
                                            "Blue", "blue", "blue"], tz)

    def test_export_results_with_no_responses(self):
        self.assertEqual(self.flow.get_run_stats()['total'], 0)

        workbook = self.export_flow_results(self.flow)

        self.assertEqual(len(workbook.worksheets), 2)

        # every sheet has only the head row
        self.assertEqual(len(list(workbook.worksheets[0].rows)), 1)
        self.assertEqual(len(list(workbook.worksheets[0].columns)), 9)
        self.assertEqual(len(list(workbook.worksheets[1].rows)), 1)
        self.assertEqual(len(list(workbook.worksheets[1].columns)), 7)

    def test_copy(self):
        # pick a really long name so we have to concatenate
        self.flow.name = "Color Flow is a long name to use for something like this"
        self.flow.expires_after_minutes = 60
        self.flow.save()

        # make sure our metadata got saved
        metadata = self.flow.metadata
        self.assertEqual("Ryan Lewis", metadata['author'])

        # now create a copy
        copy = Flow.copy(self.flow, self.admin)

        metadata = copy.metadata
        self.assertEqual("Ryan Lewis", metadata['author'])

        # expiration should be copied too
        self.assertEqual(60, copy.expires_after_minutes)

        # should have a different id
        self.assertNotEqual(self.flow.pk, copy.pk)

        # Name should start with "Copy of"
        self.assertEqual("Copy of Color Flow is a long name to use for something like thi", copy.name)

        # metadata should come out in the json
        copy_json = copy.as_json()
        self.assertEqual(dict(author="Ryan Lewis",
                              name='Copy of Color Flow is a long name to use for something like thi',
                              revision=1,
                              expires=60,
                              uuid=copy.uuid,
                              saved_on=datetime_to_str(copy.saved_on)),
                         copy_json['metadata'])

        # should have the same number of actionsets and rulesets
        self.assertEqual(copy.action_sets.all().count(), self.flow.action_sets.all().count())
        self.assertEqual(copy.rule_sets.all().count(), self.flow.rule_sets.all().count())

    @override_settings(SEND_WEBHOOKS=True)
    def test_optimization_reply_action(self):
        self.flow.version_number = '10.4'
        self.flow.save(update_fields=('version_number',))

        json_flow = FlowRevision.migrate_definition({
            "base_language": "base",
            "version": self.flow.version_number,
            "entry": "02a2f789-1545-466b-978a-4cebcc9ab89a",
            "rule_sets": [],
            "action_sets": [{"y": 0, "x": 100,
                             "destination": None, "uuid": "02a2f789-1545-466b-978a-4cebcc9ab89a",
                             "actions": [
                                 {"type": "api", "webhook": "http://localhost:49999/coupon",
                                  "webhook_header": [{"name": "Authorization", "value": "Token 12345"}]},
                                 {"msg": {"base": "text to get @extra.coupon"}, "type": "reply"}]}],
            "metadata": {"notes": []}}, self.flow)

        self.flow.update(json_flow)

        self.mockRequest('POST', '/coupon', '{"coupon": "NEXUS4"}')
        self.flow.start([], [self.contact])

        self.assertTrue(self.flow.get_steps())
        self.assertTrue(Msg.objects.all())
        msg = Msg.objects.all()[0]
        self.assertNotIn("@extra.coupon", msg.text)
        self.assertEqual(msg.text, "text to get NEXUS4")
        self.assertEqual(PENDING, msg.status)

        # check all our mocked requests were made
        self.assertAllRequestsMade()

    def test_parsing(self):
        # our flow should have the appropriate RuleSet and ActionSet objects
        self.assertEqual(4, ActionSet.objects.all().count())

        entry = ActionSet.objects.get(x=1, y=1)
        actions = entry.get_actions()
        self.assertEqual(len(actions), 1)
        self.assertIsInstance(actions[0], ReplyAction)
        self.assertEqual(actions[0].msg, dict(base="What is your favorite color?", fra="Quelle est votre couleur préférée?"))
        self.assertEqual(entry.uuid, self.flow.entry_uuid)

        orange = ActionSet.objects.get(x=2, y=2)
        actions = orange.get_actions()
        self.assertEqual(1, len(actions))
        self.assertEqual(ReplyAction(actions[0].uuid, dict(base='I love orange too! You said: @step.value which is category: @flow.color.category You are: @step.contact.tel SMS: @step Flow: @flow')).as_json(), actions[0].as_json())

        self.assertEqual(1, RuleSet.objects.all().count())
        ruleset = RuleSet.objects.get(label="color")
        self.assertEqual(entry.destination, ruleset.uuid)
        rules = ruleset.get_rules()
        self.assertEqual(4, len(rules))

        # check ordering
        self.assertEqual(rules[0].category['base'], "Orange")
        self.assertEqual(rules[1].category['base'], "Blue")
        self.assertEqual(rules[2].category['base'], "Other")

        # check routing
        self.assertEqual(ContainsTest(test=dict(base="orange")).as_json(), rules[0].test.as_json())
        self.assertEqual(ContainsTest(test=dict(base="blue")).as_json(), rules[1].test.as_json())
        self.assertEqual(TrueTest().as_json(), rules[2].test.as_json())

        # and categories
        self.assertEqual("Orange", rules[0].category['base'])
        self.assertEqual("Blue", rules[1].category['base'])

        # back out as json
        json_dict = self.flow.as_json()

        self.assertEqual(json_dict['version'], get_current_export_version())
        self.assertEqual(json_dict['flow_type'], self.flow.flow_type)
        self.assertEqual(json_dict['metadata'], {
            'name': self.flow.name,
            'author': "Ryan Lewis",
            'saved_on': datetime_to_str(self.flow.saved_on),
            'revision': 1,
            'expires': self.flow.expires_after_minutes,
            'uuid': self.flow.uuid
        })

        # remove one of our actions and rules
        del json_dict['action_sets'][3]
        del json_dict['rule_sets'][0]['rules'][2]

        # update
        self.flow.update(json_dict)

        self.assertEqual(3, ActionSet.objects.all().count())

        entry = ActionSet.objects.get(x=1, y=1)
        actions = entry.get_actions()
        self.assertEqual(len(actions), 1)
        self.assertIsInstance(actions[0], ReplyAction)
        self.assertEqual(actions[0].msg, dict(base="What is your favorite color?", fra="Quelle est votre couleur préférée?"))
        self.assertEqual(entry.uuid, self.flow.entry_uuid)

        orange = ActionSet.objects.get(x=2, y=2)
        actions = orange.get_actions()
        self.assertEqual(1, len(actions))
        self.assertEqual(ReplyAction(actions[0].uuid, dict(base='I love orange too! You said: @step.value which is category: @flow.color.category You are: @step.contact.tel SMS: @step Flow: @flow')).as_json(), actions[0].as_json())

        self.assertEqual(1, RuleSet.objects.all().count())
        ruleset = RuleSet.objects.get(label="color")
        self.assertEqual(entry.destination, ruleset.uuid)
        rules = ruleset.get_rules()
        self.assertEqual(3, len(rules))

        # check ordering
        self.assertEqual(rules[0].category['base'], "Orange")
        self.assertEqual(rules[1].category['base'], "Blue")

        # check routing
        self.assertEqual(ContainsTest(test=dict(base="orange")).as_json(), rules[0].test.as_json())
        self.assertEqual(ContainsTest(test=dict(base="blue")).as_json(), rules[1].test.as_json())

        # updating with a label name that is too long should truncate it
        json_dict['rule_sets'][0]['label'] = 'W' * 75
        json_dict['rule_sets'][0]['operand'] = 'W' * 135
        self.flow.update(json_dict)

        # now check they are truncated to the max lengths
        ruleset = RuleSet.objects.get()
        self.assertEqual(64, len(ruleset.label))
        self.assertEqual(128, len(ruleset.operand))

    def test_expanding(self):
        # add actions for adding to a group and messaging a contact, we'll test how these expand
        action_set = ActionSet.objects.get(x=4, y=4)

        actions = [AddToGroupAction(str(uuid4()), [self.other_group]).as_json(),
                   SendAction(str(uuid4()), "Outgoing Message", [self.other_group], [self.contact], []).as_json()]

        action_set.actions = actions
        action_set.save()

        # check expanding our groups
        json_dict = self.flow.as_json(expand_contacts=True)
        json_as_string = json.dumps(json_dict)

        # our json should contain the names of our contact and groups
        self.assertTrue(json_as_string.find('Eric') > 0)
        self.assertTrue(json_as_string.find('Other') > 0)

        # now delete our group
        self.other_group.delete()

        flow_json = self.flow.as_json(expand_contacts=True)
        add_group = flow_json['action_sets'][3]['actions'][0]
        send = flow_json['action_sets'][3]['actions'][1]

        # should still see a reference to our group even (recreated)
        self.assertEqual(1, len(add_group['groups']))
        self.assertEqual(1, len(send['groups']))

    def assertTest(self, expected_test, expected_value, test, extra=None):
        runs = FlowRun.objects.filter(contact=self.contact)
        if runs:
            run = runs[0]
        else:
            run = FlowRun.create(self.flow, self.contact)

        # clear any extra on this run
        run.fields = ""

        context = run.flow.build_expressions_context(run.contact, None)
        if extra:
            context['extra'] = extra

        result = test.evaluate(run, self.sms, context, self.sms.text)
        if expected_test:
            self.assertTrue(result[0])
        else:
            self.assertFalse(result[0])
        self.assertEqual(expected_value, result[1])

        # return our run for later inspection
        return run

    def assertDateTest(self, expected_result, expected_value, test):
        run = FlowRun.objects.filter(contact=self.contact).first()
        tz = run.flow.org.timezone
        context = run.flow.build_expressions_context(run.contact, None)

        # turn to JSON and back
        test_json = test.as_json()
        test = test.__class__.from_json(run.org, test_json)

        result, value = test.evaluate(run, self.sms, context, self.sms.text)

        if expected_result:
            self.assertTrue(result)
        else:
            self.assertFalse(result)

        if expected_result and expected_value:
            # convert our expected date time the right timezone
            expected_tz = expected_value.astimezone(tz)
            self.assertTrue(abs((expected_tz - value).total_seconds()) < 60, "%s does not match expected %s" % (value, expected_tz))

    def test_location_tests(self):
        sms = self.create_msg(contact=self.contact, text="")
        self.sms = sms

        # test State lookups
        state_test = HasStateTest()
        state_test = HasStateTest.from_json(self.org, state_test.as_json())

        sms.text = "Kigali City"
        self.assertTest(True, AdminBoundary.objects.get(name="Kigali City"), state_test)

        sms.text = "Seattle"
        self.assertTest(False, None, state_test)

        # now District lookups
        district_test = HasDistrictTest("Kigali City")
        district_test = HasDistrictTest.from_json(self.org, district_test.as_json())

        sms.text = "Nyarugenge"
        self.assertTest(True, AdminBoundary.objects.get(name="Nyarugenge"), district_test)

        sms.text = "I am from Nyarugenge"
        self.assertTest(True, AdminBoundary.objects.get(name="Nyarugenge"), district_test)

        sms.text = "Rwamagana"
        self.assertTest(False, None, district_test)

        # remove our org country, should no longer match things
        self.org.country = None
        self.org.save()

        sms.text = "Kigali City"
        self.assertTest(False, None, state_test)

        sms.text = "Nyarugenge"
        self.assertTest(False, None, district_test)

    def test_tests(self):
        sms = self.create_msg(contact=self.contact, text="GReen is my favorite!")
        self.sms = sms

        test = TrueTest()
        self.assertTest(True, sms.text, test)

        test = FalseTest()
        self.assertTest(False, None, test)

        test = ContainsTest(test=dict(base="Green"))
        self.assertTest(True, "GReen", test)

        test = ContainsTest(test=dict(base="Green green GREEN"))
        self.assertTest(True, "GReen", test)

        test = ContainsTest(test=dict(base="Green green GREEN"))
        self.assertTest(True, "GReen", test)

        sms.text = "Blue is my favorite"
        self.assertTest(False, None, test)

        sms.text = "Greenish is ok too"
        self.assertTest(False, None, test)

        # no edit distance
        sms.text = "Greenn is ok though"
        self.assertTest(False, None, test)

        sms.text = "RESIST!!"
        test = ContainsOnlyPhraseTest(test=dict(base="resist"))
        self.assertTest(True, "RESIST", test)

        sms.text = "RESIST TODAY!!"
        self.assertTest(False, None, test)

        test = ContainsOnlyPhraseTest(test=dict(base="resist now"))
        test = ContainsOnlyPhraseTest.from_json(self.org, test.as_json())
        sms.text = " resist NOW "
        self.assertTest(True, "resist NOW", test)

        sms.text = " NOW resist"
        self.assertTest(False, None, test)

        sms.text = "this isn't an email@asdf"
        test = HasEmailTest()
        test = HasEmailTest.from_json(self.org, test.as_json())
        self.assertTest(False, None, test)

        sms.text = "this is an email email@foo.bar TODAY!!"
        self.assertTest(True, "email@foo.bar", test)

        sms.text = "this is an email followed by a period email@foo.bar."
        self.assertTest(True, "email@foo.bar", test)

        sms.text = "this is an email surrounded by punctuation <email@foo.bar>,"
        self.assertTest(True, "email@foo.bar", test)

        test = ContainsOnlyPhraseTest(test=dict(base=""))
        sms.text = "  RESIST now "
        self.assertTest(False, None, test)

        sms.text = "  "
        self.assertTest(True, "", test)

        test = ContainsPhraseTest(test=dict(base="resist now"))
        test = ContainsPhraseTest.from_json(self.org, test.as_json())
        sms.text = "we must resist! NOW "
        self.assertTest(True, "resist NOW", test)

        sms.text = "we must resist but perhaps not NOW "
        self.assertTest(False, None, test)

        sms.text = "  RESIST now "
        self.assertTest(True, "RESIST now", test)

        test = ContainsPhraseTest(test=dict(base=""))
        sms.text = "  RESIST now "
        self.assertTest(True, "", test)

        test = ContainsTest(test=dict(base="Green green %%$"))
        sms.text = "GReen is my favorite!, %%$"
        self.assertTest(True, "GReen", test)

        # variable substitution
        test = ContainsTest(test=dict(base="@extra.color"))
        sms.text = "my favorite color is GREEN today"
        self.assertTest(True, "GREEN", test, extra=dict(color="green"))

        test.test = dict(base="this THAT")
        sms.text = "this is good but won't match"
        self.assertTest(False, None, test)

        test.test = dict(base="this THAT")
        sms.text = "that and this is good and will match"
        self.assertTest(True, "that this", test)

        test.test = dict(base="this THAT")
        sms.text = "this and that is good and will match"
        self.assertTest(True, "this that", test)

        test.test = dict(base="this THAT")
        sms.text = "this and that and this other thing is good and will match"
        self.assertTest(True, "this that this", test)

        sms.text = "when we win we \U0001F64C @ "

        test = ContainsTest(test=dict(base="\U0001F64C"))
        self.assertTest(True, "\U0001F64C", test)

        sms.text = "I am \U0001F44D"
        test = ContainsAnyTest(test=dict(base=u"\U0001F64C \U0001F44D"))
        self.assertTest(True, "\U0001F44D", test)

        sms.text = "text"

        test = AndTest([TrueTest(), TrueTest()])
        self.assertTest(True, "text text", test)

        test = AndTest([TrueTest(), FalseTest()])
        self.assertTest(False, None, test)

        test = OrTest([TrueTest(), FalseTest()])
        self.assertTest(True, "text", test)

        test = OrTest([FalseTest(), FalseTest()])
        self.assertTest(False, None, test)

        test = ContainsAnyTest(test=dict(base="klab Kacyiru good"))
        sms.text = "kLab is awesome"
        self.assertTest(True, "kLab", test)

        sms.text = "telecom is located at Kacyiru"
        self.assertTest(True, "Kacyiru", test)

        sms.text = "good morning"
        self.assertTest(True, "good", test)

        sms.text = "kLab is good"
        self.assertTest(True, "kLab good", test)

        sms.text = "kigali city"
        self.assertTest(False, None, test)

        # have the same behaviour when we have commas even a trailing one
        test = ContainsAnyTest(test=dict(base="klab, kacyiru, good, "))
        sms.text = "kLab is awesome"
        self.assertTest(True, "kLab", test)

        sms.text = "telecom is located at Kacyiru"
        self.assertTest(True, "Kacyiru", test)

        sms.text = "good morning"
        self.assertTest(True, "good", test)

        sms.text = "kLab is good"
        self.assertTest(True, "kLab good", test)

        sms.text = "kigali city"
        self.assertTest(False, None, test)

        sms.text = "blue white, allo$%%"
        self.assertTest(False, None, test)

        test = ContainsAnyTest(test=dict(base="%%$, &&,"))
        sms.text = "blue white, allo$%%"
        self.assertTest(False, None, test)

        sms.text = "%%$"
        self.assertTest(False, None, test)

        test = LtTest(test="5")
        self.assertTest(False, None, test)

        test = LteTest(test="0")
        sms.text = "My answer is -4"
        self.assertTest(True, Decimal("-4"), test)

        sms.text = "My answer is 4"
        test = LtTest(test="4")
        self.assertTest(False, None, test)

        test = GtTest(test="4")
        self.assertTest(False, None, test)

        test = GtTest(test="3")
        self.assertTest(True, Decimal("4"), test)

        test = GteTest(test="4")
        self.assertTest(True, Decimal("4"), test)

        test = GteTest(test="9")
        self.assertTest(False, None, test)

        test = EqTest(test="4")
        self.assertTest(True, Decimal("4"), test)

        test = EqTest(test="5")
        self.assertTest(False, None, test)

        test = BetweenTest("5", "10")
        self.assertTest(False, None, test)

        test = BetweenTest("4", "10")
        self.assertTest(True, Decimal("4"), test)

        test = BetweenTest("0", "4")
        self.assertTest(True, Decimal("4"), test)

        test = BetweenTest("0", "3")
        self.assertTest(False, None, test)

        test = BetweenTest("@extra.min", "@extra.max")
        self.assertTest(True, Decimal('4'), test, extra=dict(min=2, max=5))

        test = BetweenTest("0", "@xxx")  # invalid expression
        self.assertTest(False, None, test)

        sms.text = "My answer is or"
        self.assertTest(False, None, test)

        sms.text = "My answer is 4"
        test = BetweenTest("1", "5")
        self.assertTest(True, Decimal("4"), test)

        sms.text = "My answer is 4rwf"
        self.assertTest(True, Decimal("4"), test)

        sms.text = "My answer is a4rwf"
        self.assertTest(False, None, test)

        test = BetweenTest("10", "50")
        sms.text = "My answer is lO"
        self.assertTest(True, Decimal("10"), test)

        test = BetweenTest("1000", "5000")
        sms.text = "My answer is 4,000rwf"
        self.assertTest(True, Decimal("4000"), test)

        rule = Rule('8bfc987a-796f-4de7-bce6-a10ed06f617b', None, None, None, test)
        self.assertEqual("1000-5000", rule.get_category_name(None))

        test = StartsWithTest(test=dict(base="Green"))
        sms.text = "  green beans"
        self.assertTest(True, "green", test)

        sms.text = "greenbeans"
        self.assertTest(True, "green", test)

        sms.text = "  beans Green"
        self.assertTest(False, None, test)

        test = NumberTest()
        self.assertTest(False, None, test)

        sms.text = "I have 7"
        self.assertTest(True, Decimal("7"), test)

        sms.text = "$250"
        self.assertTest(True, Decimal("250"), test)

        sms.text = "Where is my £5,656.56?"
        self.assertTest(True, Decimal("5656.56"), test)

        sms.text = "Very hot in here, temp at 38°c"
        self.assertTest(True, Decimal("38"), test)

        sms.text = "This is aw350me"
        self.assertTest(False, None, test)

        sms.text = "random typing 12333xg333"
        self.assertTest(False, None, test)

        sms.text = ",34"
        self.assertTest(True, Decimal("34"), test)

        # phone tests
        test = PhoneTest()
        sms.text = "My phone number is 0788 383 383"
        self.assertTest(True, "+250788383383", test)

        sms.text = "+250788123123"
        self.assertTest(True, "+250788123123", test)

        sms.text = "+12067799294"
        self.assertTest(True, "+12067799294", test)

        sms.text = "My phone is 0124515"
        self.assertTest(False, None, test)

        test = ContainsTest(test=dict(base="مورنۍ"))
        sms.text = "شاملیدل مورنۍ"
        self.assertTest(True, "مورنۍ", test)

        # test = "word to start" and notice "to start" is one word in arabic ataleast according to Google translate
        test = ContainsAnyTest(test=dict(base="كلمة لبدء"))
        # set text to "give a sample word in sentence"
        sms.text = "تعطي كلمة عينة في الجملة"
        self.assertTest(True, "كلمة", test)  # we get "word"

        # we should not match "this start is not allowed" we wanted "to start"
        test = ContainsAnyTest(test=dict(base="لا يسمح هذه البداية"))
        self.assertTest(False, None, test)

        test = RegexTest(dict(base="(?P<first_name>\w+) (\w+)"))
        sms.text = "Isaac Newton"
        run = self.assertTest(True, "Isaac Newton", test)
        extra = run.fields
        self.assertEqual("Isaac Newton", extra['0'])
        self.assertEqual("Isaac", extra['1'])
        self.assertEqual("Newton", extra['2'])

        # find that arabic unicode is handled right
        sms.text = "مرحبا العالم"
        run = self.assertTest(True, "مرحبا العالم", test)
        extra = run.fields
        self.assertEqual("مرحبا العالم", extra['0'])
        self.assertEqual("مرحبا", extra['1'])
        self.assertEqual("العالم", extra['2'])

        # no matching groups, should return whole string as match
        test = RegexTest(dict(base="\w+ \w+"))
        sms.text = "Isaac Newton"
        run = self.assertTest(True, "Isaac Newton", test)
        extra = run.fields
        self.assertEqual("Isaac Newton", extra['0'])

        # no match, shouldn't return anything at all
        sms.text = "#$%^$#? !@#$"
        run = self.assertTest(False, None, test)
        extra = run.fields
        self.assertFalse(extra)

        # no case sensitivity
        test = RegexTest(dict(base="kazoo"))
        sms.text = "This is my Kazoo"
        run = self.assertTest(True, "Kazoo", test)
        extra = run.fields
        self.assertEqual("Kazoo", extra['0'])

        # change to have anchors
        test = RegexTest(dict(base="^kazoo$"))

        # no match, as at the end
        sms.text = "This is my Kazoo"
        run = self.assertTest(False, None, test)

        # this one will match
        sms.text = "Kazoo"
        run = self.assertTest(True, "Kazoo", test)
        extra = run.fields
        self.assertEqual("Kazoo", extra['0'])

        # not empty
        sms.text = ""
        self.assertTest(False, None, NotEmptyTest())
        sms.text = None
        self.assertTest(False, None, NotEmptyTest())
        sms.text = " "
        self.assertTest(False, None, NotEmptyTest())
        sms.text = "it works "
        self.assertTest(True, "it works", NotEmptyTest())

        def perform_date_tests(sms, dayfirst):
            """
            Performs a set of date tests in either day-first or month-first mode
            """
            self.org.date_format = 'D' if dayfirst else 'M'
            self.org.save(update_fields=('date_format',))

            # perform all date tests as if it were 2014-01-02 03:04:05.6 UTC - a date which when localized to DD-MM-YYYY
            # or MM-DD-YYYY is ambiguous
            with patch.object(timezone, 'now', return_value=datetime.datetime(2014, 1, 2, 3, 4, 5, 6, timezone.utc)):
                now = timezone.now()
                three_days_ago = now - timedelta(days=3)
                three_days_next = now + timedelta(days=3)
                five_days_next = now + timedelta(days=5)

                sms.text = "no date in this text"
                test = DateTest()
                self.assertDateTest(False, None, test)

                sms.text = "1980"
                self.assertDateTest(False, None, test)

                if dayfirst:
                    sms.text = "sometime on %d/%d/%d" % (now.day, now.month, now.year)
                else:
                    sms.text = "sometime on %d/%d/%d" % (now.month, now.day, now.year)

                self.assertDateTest(True, now, test)

                # date before/equal/after tests using date arithmetic

                test = DateBeforeTest('@(date.today - 1)')
                self.assertDateTest(False, None, test)

                if dayfirst:
                    sms.text = "this is for three days ago %d-%d-%d" % (three_days_ago.day, three_days_ago.month, three_days_ago.year)
                else:
                    sms.text = "this is for three days ago %d-%d-%d" % (three_days_ago.month, three_days_ago.day, three_days_ago.year)
                self.assertDateTest(True, three_days_ago, test)

                sms.text = "in the next three days %d/%d/%d" % (three_days_next.day, three_days_next.month, three_days_next.year)
                self.assertDateTest(False, None, test)

                test = DateEqualTest('@(date.today - 3)')
                self.assertDateTest(False, None, test)

                if dayfirst:
                    sms.text = "this is for three days ago %d/%d/%d" % (three_days_ago.day, three_days_ago.month, three_days_ago.year)
                else:
                    sms.text = "this is for three days ago %d/%d/%d" % (three_days_ago.month, three_days_ago.day, three_days_ago.year)
                self.assertDateTest(True, three_days_ago, test)

                test = DateAfterTest('@(date.today + 3)')
                self.assertDateTest(False, None, test)

                if dayfirst:
                    sms.text = "this is for three days ago %d/%d/%d" % (five_days_next.day, five_days_next.month, five_days_next.year)
                else:
                    sms.text = "this is for three days ago %d/%d/%d" % (five_days_next.month, five_days_next.day, five_days_next.year)
                self.assertDateTest(True, five_days_next, test)

        # check date tests in both date modes
        perform_date_tests(sms, dayfirst=True)
        perform_date_tests(sms, dayfirst=False)

    def test_length(self):
        org = self.org

        js = [dict(category="Normal Length", uuid=uuid4(), destination=uuid4(), test=dict(type='true')),
              dict(category="Way too long, will get clipped at 36 characters", uuid=uuid4(), destination=uuid4(), test=dict(type='true'))]

        rules = Rule.from_json_array(org, js)

        self.assertEqual("Normal Length", rules[0].category)
        self.assertEqual(36, len(rules[1].category))

    def test_factories(self):
        org = self.org

        js = dict(type='true')
        self.assertEqual(TrueTest, Test.from_json(org, js).__class__)
        self.assertEqual(js, TrueTest().as_json())

        js = dict(type='false')
        self.assertEqual(FalseTest, Test.from_json(org, js).__class__)
        self.assertEqual(js, FalseTest().as_json())

        js = dict(type='and', tests=[dict(type='true')])
        self.assertEqual(AndTest, Test.from_json(org, js).__class__)
        self.assertEqual(js, AndTest([TrueTest()]).as_json())

        js = dict(type='or', tests=[dict(type='true')])
        self.assertEqual(OrTest, Test.from_json(org, js).__class__)
        self.assertEqual(js, OrTest([TrueTest()]).as_json())

        js = dict(type='contains', test="green")
        self.assertEqual(ContainsTest, Test.from_json(org, js).__class__)
        self.assertEqual(js, ContainsTest("green").as_json())

        js = dict(type='lt', test="5")
        self.assertEqual(LtTest, Test.from_json(org, js).__class__)
        self.assertEqual(js, LtTest("5").as_json())

        js = dict(type='gt', test="5")
        self.assertEqual(GtTest, Test.from_json(org, js).__class__)
        self.assertEqual(js, GtTest("5").as_json())

        js = dict(type='gte', test="5")
        self.assertEqual(GteTest, Test.from_json(org, js).__class__)
        self.assertEqual(js, GteTest("5").as_json())

        js = dict(type='eq', test="5")
        self.assertEqual(EqTest, Test.from_json(org, js).__class__)
        self.assertEqual(js, EqTest("5").as_json())

        js = dict(type='between', min="5", max="10")
        self.assertEqual(BetweenTest, Test.from_json(org, js).__class__)
        self.assertEqual(js, BetweenTest("5", "10").as_json())

        self.assertEqual(ReplyAction, Action.from_json(org, dict(type='reply', msg=dict(base="hello world"))).__class__)
        self.assertEqual(SendAction, Action.from_json(org, dict(type='send', msg=dict(base="hello world"), contacts=[], groups=[], variables=[])).__class__)

    def test_decimal_values(self):
        color_ruleset = RuleSet.objects.get(label="color")

        # update our rule to include decimal parsing
        color_ruleset.rules = [
            Rule(
                "1c75fd71-027b-40e8-a819-151a0f8140e6",
                {self.flow.base_language: "< 10"},
                "7d40faea-723b-473d-8999-59fb7d3c3ca2",
                'A',
                LtTest(10)
            ).as_json(),
            Rule(
                "40cc7c36-b7c8-4f05-ae82-25275607e5aa",
                {self.flow.base_language: "> 10"},
                "c12f37e2-8e6c-4c81-ba6d-941bb3caf93f",
                'A',
                GteTest(10)
            ).as_json()
        ]

        color_ruleset.save()

        # start the flow
        self.flow.start([], [self.contact])
        sms = self.create_msg(direction=INCOMING, contact=self.contact, text="My answer is 15")
        self.assertTrue(Flow.find_and_handle(sms)[0])

        # get our run and assert our value is saved (as a string)
        run = FlowRun.objects.get(flow=self.flow, contact=self.contact)
        results = run.results
        self.assertEqual(results['color']['value'], "15")
        self.assertEqual(results['color']['node_uuid'], color_ruleset.uuid)
        self.assertEqual(results['color']['category'], "> 10")
        self.assertEqual(results['color']['name'], "color")
        self.assertIsNotNone(results['color']['created_on'])

        # and that the category counts have been updated
        self.assertIsNotNone(FlowCategoryCount.objects.filter(node_uuid=color_ruleset.uuid, category_name='> 10',
                                                              result_name='color', result_key='color', count=1).first())

    def test_location_entry_test(self):

        self.country = AdminBoundary.objects.create(osm_id='192787', name='Nigeria', level=0)
        kano = AdminBoundary.objects.create(osm_id='3710302', name='Kano', level=1, parent=self.country)
        lagos = AdminBoundary.objects.create(osm_id='3718182', name='Lagos', level=1, parent=self.country)
        ajingi = AdminBoundary.objects.create(osm_id='3710308', name='Ajingi', level=2, parent=kano)
        bichi = AdminBoundary.objects.create(osm_id='3710307', name='Bichi', level=2, parent=kano)
        apapa = AdminBoundary.objects.create(osm_id='3718187', name='Apapa', level=2, parent=lagos)
        bichiward = AdminBoundary.objects.create(osm_id='3710377', name='Bichi', level=3, parent=bichi)
        AdminBoundary.objects.create(osm_id='3710378', name='Ajingi', level=3, parent=ajingi)
        sms = self.create_msg(contact=self.contact, text="awesome text")
        self.sms = sms
        runs = FlowRun.objects.filter(contact=self.contact)
        if runs:
            run = runs[0]
        else:
            run = FlowRun.create(self.flow, self.contact)

        self.org.country = self.country
        run.flow.org = self.org
        context = run.flow.build_expressions_context(run.contact, None)

        # wrong admin level should return None if provided
        lga_tuple = HasDistrictTest('Kano').evaluate(run, sms, context, 'apapa')
        self.assertEqual(lga_tuple[1], None)

        lga_tuple = HasDistrictTest('Lagos').evaluate(run, sms, context, 'apapa')
        self.assertEqual(lga_tuple[1], apapa)

        # get lga with out higher admin level
        lga_tuple = HasDistrictTest().evaluate(run, sms, context, 'apapa')
        self.assertEqual(lga_tuple[1], apapa)

        # get ward with out higher admin levels
        ward_tuple = HasWardTest().evaluate(run, sms, context, 'bichi')
        self.assertEqual(ward_tuple[1], bichiward)

        # get with hierarchy proved
        ward_tuple = HasWardTest('Kano', 'Bichi').evaluate(run, sms, context, 'bichi')
        self.assertEqual(ward_tuple[1], bichiward)

        # wrong admin level should return None if provided
        ward_tuple = HasWardTest('Kano', 'Ajingi').evaluate(run, sms, context, 'bichi')
        js = dict(state='Kano', district='Ajingi', type='ward')
        self.assertEqual(HasWardTest('Kano', 'Ajingi').as_json(), js)
        self.assertEqual(ward_tuple[1], None)

        # get with hierarchy by aliases
        BoundaryAlias.objects.create(name='Pillars', boundary=kano, org=self.org,
                                     created_by=self.admin, modified_by=self.admin)
        ward_tuple = HasWardTest('Pillars', 'Bichi').evaluate(run, sms, context, 'bichi')
        self.assertEqual(ward_tuple[1], bichiward)

        # misconfigured flows should ignore the state and district if wards are unique by name
        ward_tuple = HasWardTest('Bichi', 'Kano').evaluate(run, sms, context, 'bichi')
        self.assertEqual(ward_tuple[1], bichiward)

        # misconfigured flows should not match if wards not unique
        AdminBoundary.objects.create(osm_id='3710379', name='Bichi', level=3, parent=apapa)
        ward_tuple = HasWardTest('Bichi', 'Kano').evaluate(run, sms, context, 'bichi')
        self.assertEqual(ward_tuple[1], None)

        self.assertEqual(HasWardTest, Test.from_json(self.org, js).__class__)

    def test_flow_keyword_create(self):
        self.login(self.admin)

        # try creating a flow with invalid keywords
        response = self.client.post(reverse('flows.flow_create'), {
            'name': "Flow #1",
            'keyword_triggers': "toooooooooooooolong,test",
            'flow_type': Flow.FLOW,
            'expires_after_minutes': 60 * 12
        })
        self.assertEqual(response.status_code, 200)
        self.assertFormError(response, 'form', 'keyword_triggers',
                             '"toooooooooooooolong" must be a single word, less than 16 characters, containing only '
                             'letter and numbers')

        # submit with valid keywords
        response = self.client.post(reverse('flows.flow_create'), {
            'name': "Flow #1",
            'keyword_triggers': "testing, test",
            'flow_type': Flow.FLOW,
            'expires_after_minutes': 60 * 12
        })
        self.assertEqual(response.status_code, 302)

        flow = Flow.objects.get(name='Flow #1')
        self.assertEqual(flow.triggers.all().count(), 2)
        self.assertEqual(set(flow.triggers.values_list('keyword', flat=True)), {'testing', 'test'})

        # try creating a survey flow with keywords (they'll be ignored)
        response = self.client.post(reverse('flows.flow_create'), {
            'name': "Survey Flow",
            'keyword_triggers': "notallowed",
            'flow_type': Flow.SURVEY,
            'expires_after_minutes': 60 * 12
        })
        self.assertEqual(response.status_code, 302)

        # should't be allowed to have a survey flow and keywords
        flow = Flow.objects.get(name='Survey Flow')
        self.assertEqual(flow.triggers.all().count(), 0)

    def test_flow_keyword_update(self):
        self.login(self.admin)
        flow = Flow.create(self.org, self.admin, "Flow")
        flow.flow_type = Flow.SURVEY
        flow.save()

        # keywords aren't an option for survey flows
        response = self.client.get(reverse('flows.flow_update', args=[flow.pk]))
        self.assertNotIn('keyword_triggers', response.context['form'].fields)
        self.assertNotIn('ignore_triggers', response.context['form'].fields)

        # send update with triggers and ignore flag anyways
        post_data = dict()
        post_data['name'] = "Flow With Keyword Triggers"
        post_data['keyword_triggers'] = "notallowed"
        post_data['ignore_keywords'] = True
        post_data['expires_after_minutes'] = 60 * 12
        response = self.client.post(reverse('flows.flow_update', args=[flow.pk]), post_data, follow=True)

        # still shouldn't have any triggers
        flow.refresh_from_db()
        self.assertFalse(flow.ignore_triggers)
        self.assertEqual(0, flow.triggers.all().count())

    def test_global_keywords_trigger_update(self):
        self.login(self.admin)
        flow = Flow.create(self.org, self.admin, "Flow")

        # update flow triggers
        response = self.client.post(reverse('flows.flow_update', args=[flow.id]), {
            'name': "Flow With Keyword Triggers",
            'keyword_triggers': "it,changes,everything",
            'expires_after_minutes': 60 * 12
        })
        self.assertEqual(response.status_code, 302)

        flow_with_keywords = Flow.objects.get(name="Flow With Keyword Triggers")
        self.assertEqual(flow_with_keywords.triggers.count(), 3)
        self.assertEqual(flow_with_keywords.triggers.filter(is_archived=False).count(), 3)
        self.assertEqual(flow_with_keywords.triggers.filter(is_archived=False).exclude(groups=None).count(), 0)

        # add triggers of other types
        Trigger.objects.create(created_by=self.admin, modified_by=self.admin, org=self.org,
                               trigger_type=Trigger.TYPE_FOLLOW, flow=flow_with_keywords, channel=self.channel)

        Trigger.objects.create(created_by=self.admin, modified_by=self.admin, org=self.org,
                               trigger_type=Trigger.TYPE_CATCH_ALL, flow=flow_with_keywords)

        Trigger.objects.create(created_by=self.admin, modified_by=self.admin, org=self.org,
                               trigger_type=Trigger.TYPE_MISSED_CALL, flow=flow_with_keywords)

        Trigger.objects.create(created_by=self.admin, modified_by=self.admin, org=self.org,
                               trigger_type=Trigger.TYPE_INBOUND_CALL, flow=flow_with_keywords)

        Trigger.objects.create(created_by=self.admin, modified_by=self.admin, org=self.org,
                               trigger_type=Trigger.TYPE_SCHEDULE, flow=flow_with_keywords)

        self.assertEqual(flow_with_keywords.triggers.filter(is_archived=False).count(), 8)

        # update flow triggers
        post_data = dict()
        post_data['name'] = "Flow With Keyword Triggers"
        post_data['keyword_triggers'] = "it,join"
        post_data['expires_after_minutes'] = 60 * 12
        response = self.client.post(reverse('flows.flow_update', args=[flow.pk]), post_data, follow=True)

        flow_with_keywords = Flow.objects.get(name=post_data['name'])
        self.assertEqual(200, response.status_code)
        self.assertEqual(response.request['PATH_INFO'], reverse('flows.flow_list'))
        self.assertTrue(flow_with_keywords in response.context['object_list'].all())
        self.assertEqual(flow_with_keywords.triggers.count(), 9)
        self.assertEqual(flow_with_keywords.triggers.filter(is_archived=True).count(), 2)
        self.assertEqual(flow_with_keywords.triggers.filter(is_archived=True,
                                                            trigger_type=Trigger.TYPE_KEYWORD).count(), 2)
        self.assertEqual(flow_with_keywords.triggers.filter(is_archived=False).count(), 7)
        self.assertEqual(flow_with_keywords.triggers.filter(is_archived=True,
                                                            trigger_type=Trigger.TYPE_KEYWORD).count(), 2)

        # only keyword triggers got archived, other are stil active
        self.assertTrue(flow_with_keywords.triggers.filter(is_archived=False, trigger_type=Trigger.TYPE_FOLLOW))
        self.assertTrue(flow_with_keywords.triggers.filter(is_archived=False, trigger_type=Trigger.TYPE_CATCH_ALL))
        self.assertTrue(flow_with_keywords.triggers.filter(is_archived=False, trigger_type=Trigger.TYPE_SCHEDULE))
        self.assertTrue(flow_with_keywords.triggers.filter(is_archived=False, trigger_type=Trigger.TYPE_MISSED_CALL))
        self.assertTrue(flow_with_keywords.triggers.filter(is_archived=False, trigger_type=Trigger.TYPE_INBOUND_CALL))

    def test_views(self):
        self.create_secondary_org()

        # create a flow for another org
        other_flow = Flow.create(self.org2, self.admin2, "Flow2", base_language='base')

        # no login, no list
        response = self.client.get(reverse('flows.flow_list'))
        self.assertRedirect(response, reverse('users.user_login'))

        user = self.admin
        user.first_name = "Test"
        user.last_name = "Contact"
        user.save()
        self.login(user)

        # list, should have only one flow (the one created in setUp)
        response = self.client.get(reverse('flows.flow_list'))
        self.assertEqual(1, len(response.context['object_list']))

        # inactive list shouldn't have any flows
        response = self.client.get(reverse('flows.flow_archived'))
        self.assertEqual(0, len(response.context['object_list']))

        # also shouldn't be able to view other flow
        response = self.client.get(reverse('flows.flow_editor', args=[other_flow.uuid]))
        self.assertEqual(302, response.status_code)

        # get our create page
        response = self.client.get(reverse('flows.flow_create'))
        self.assertTrue(response.context['has_flows'])
        self.assertIn('flow_type', response.context['form'].fields)

        # add call channel
        twilio = Channel.create(self.org, self.user, None, 'T', "Twilio", "0785553434", role="C",
                                secret="56789", gcm_id="456")

        response = self.client.get(reverse('flows.flow_create'))
        self.assertTrue(response.context['has_flows'])
        self.assertIn('flow_type', response.context['form'].fields)  # shown because of call channel

        twilio.delete()

        # create a new regular flow
        response = self.client.post(reverse('flows.flow_create'), dict(name='Flow', flow_type='F'), follow=True)
        flow1 = Flow.objects.get(org=self.org, name="Flow")
        # add a trigger on this flow
        Trigger.objects.create(org=self.org, keyword='unique', flow=flow1,
                               created_by=self.admin, modified_by=self.admin)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(flow1.flow_type, 'F')
        self.assertEqual(flow1.expires_after_minutes, 10080)

        # create a new surveyor flow
        self.client.post(reverse('flows.flow_create'), dict(name='Surveyor Flow', flow_type='S'), follow=True)
        flow2 = Flow.objects.get(org=self.org, name="Surveyor Flow")
        self.assertEqual(flow2.flow_type, 'S')
        self.assertEqual(flow2.expires_after_minutes, 10080)

        # make sure we don't get a start flow button for Android Surveys
        response = self.client.get(reverse('flows.flow_editor', args=[flow2.uuid]))
        self.assertNotContains(response, "broadcast-rulesflow btn-primary")

        # create a new voice flow
        response = self.client.post(reverse('flows.flow_create'), dict(name='Voice Flow', flow_type='V'), follow=True)
        voice_flow = Flow.objects.get(org=self.org, name="Voice Flow")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(voice_flow.flow_type, 'V')

        # default expiration for voice is shorter
        self.assertEqual(voice_flow.expires_after_minutes, 5)

        # test flows with triggers
        # create a new flow with one unformatted keyword
        post_data = dict()
        post_data['name'] = "Flow With Unformated Keyword Triggers"
        post_data['keyword_triggers'] = "this is,it"
        response = self.client.post(reverse('flows.flow_create'), post_data)
        self.assertFormError(response, 'form', 'keyword_triggers',
                             '"this is" must be a single word, less than 16 characters, containing only letter and numbers')

        # create a new flow with one existing keyword
        post_data = dict()
        post_data['name'] = "Flow With Existing Keyword Triggers"
        post_data['keyword_triggers'] = "this,is,unique"
        response = self.client.post(reverse('flows.flow_create'), post_data)
        self.assertFormError(response, 'form', 'keyword_triggers',
                             'The keyword "unique" is already used for another flow')

        # create another trigger so there are two in the way
        trigger = Trigger.objects.create(org=self.org, keyword='this', flow=flow1,
                                         created_by=self.admin, modified_by=self.admin)

        response = self.client.post(reverse('flows.flow_create'), post_data)
        self.assertFormError(response, 'form', 'keyword_triggers',
                             'The keywords "this, unique" are already used for another flow')
        trigger.delete()

        # create a new flow with keywords
        post_data = dict()
        post_data['name'] = "Flow With Good Keyword Triggers"
        post_data['keyword_triggers'] = "this,is,it"
        post_data['flow_type'] = 'F'
        post_data['expires_after_minutes'] = 30
        response = self.client.post(reverse('flows.flow_create'), post_data, follow=True)
        flow3 = Flow.objects.get(name=post_data['name'])

        self.assertEqual(200, response.status_code)
        self.assertEqual(response.request['PATH_INFO'], reverse('flows.flow_editor', args=[flow3.uuid]))
        self.assertEqual(response.context['object'].triggers.count(), 3)

        # update expiration for voice flow
        post_data = dict()
        response = self.client.get(reverse('flows.flow_update', args=[voice_flow.pk]), post_data, follow=True)

        choices = response.context['form'].fields['expires_after_minutes'].choices
        self.assertEqual(7, len(choices))
        self.assertEqual(1, choices[0][0])
        self.assertEqual(2, choices[1][0])
        self.assertEqual(3, choices[2][0])
        self.assertEqual(4, choices[3][0])
        self.assertEqual(5, choices[4][0])
        self.assertEqual(10, choices[5][0])
        self.assertEqual(15, choices[6][0])

        # try updating with an sms type expiration to make sure it's restricted for voice flows
        post_data['expires_after_minutes'] = 60 * 12
        post_data['name'] = 'Voice Flow'
        response = self.client.post(reverse('flows.flow_update', args=[voice_flow.pk]), post_data, follow=True)
        voice_flow.refresh_from_db()
        self.assertEqual(5, voice_flow.expires_after_minutes)

        # now do a valid value for voice
        post_data['expires_after_minutes'] = 3
        response = self.client.post(reverse('flows.flow_update', args=[voice_flow.pk]), post_data, follow=True)

        voice_flow.refresh_from_db()
        self.assertEqual(3, voice_flow.expires_after_minutes)

        # update flow triggers
        post_data = dict()
        post_data['name'] = "Flow With Keyword Triggers"
        post_data['keyword_triggers'] = "it,changes,everything"
        post_data['expires_after_minutes'] = 60 * 12
        response = self.client.post(reverse('flows.flow_update', args=[flow3.pk]), post_data, follow=True)
        flow3 = Flow.objects.get(name=post_data['name'])
        self.assertEqual(200, response.status_code)
        self.assertEqual(response.request['PATH_INFO'], reverse('flows.flow_list'))
        self.assertTrue(flow3 in response.context['object_list'].all())
        self.assertEqual(flow3.triggers.count(), 5)
        self.assertEqual(flow3.triggers.filter(is_archived=True).count(), 2)
        self.assertEqual(flow3.triggers.filter(is_archived=False).count(), 3)
        self.assertEqual(flow3.triggers.filter(is_archived=False).exclude(groups=None).count(), 0)

        # update flow with unformatted keyword
        post_data['keyword_triggers'] = "it,changes,every thing"
        response = self.client.post(reverse('flows.flow_update', args=[flow3.pk]), post_data)
        self.assertTrue(response.context['form'].errors)

        # update flow with unformated keyword
        post_data['keyword_triggers'] = "it,changes,everything,unique"
        response = self.client.post(reverse('flows.flow_update', args=[flow3.pk]), post_data)
        self.assertTrue(response.context['form'].errors)
        response = self.client.get(reverse('flows.flow_update', args=[flow3.pk]))
        self.assertEqual(response.context['form'].fields['keyword_triggers'].initial, "it,everything,changes")
        self.assertEqual(flow3.triggers.filter(is_archived=False).count(), 3)
        self.assertEqual(flow3.triggers.filter(is_archived=False).exclude(groups=None).count(), 0)
        trigger = Trigger.objects.get(keyword="everything", flow=flow3)
        group = self.create_group("first", [self.contact])
        trigger.groups.add(group)
        self.assertEqual(flow3.triggers.filter(is_archived=False).count(), 3)
        self.assertEqual(flow3.triggers.filter(is_archived=False).exclude(groups=None).count(), 1)
        self.assertEqual(flow3.triggers.filter(is_archived=False).exclude(groups=None)[0].keyword, "everything")
        response = self.client.get(reverse('flows.flow_update', args=[flow3.pk]))
        self.assertEqual(response.context['form'].fields['keyword_triggers'].initial, "it,changes")
        self.assertNotContains(response, "contact_creation")
        self.assertEqual(flow3.triggers.filter(is_archived=False).count(), 3)
        self.assertEqual(flow3.triggers.filter(is_archived=False).exclude(groups=None).count(), 1)
        self.assertEqual(flow3.triggers.filter(is_archived=False).exclude(groups=None)[0].keyword, "everything")

        # make us a survey flow
        flow3.flow_type = Flow.SURVEY
        flow3.save()

        # we should get the contact creation option
        response = self.client.get(reverse('flows.flow_update', args=[flow3.pk]))
        self.assertContains(response, 'contact_creation')

        # set contact creation to be per login
        del post_data['keyword_triggers']
        post_data['contact_creation'] = Flow.CONTACT_PER_LOGIN
        response = self.client.post(reverse('flows.flow_update', args=[flow3.pk]), post_data)
        flow3.refresh_from_db()
        self.assertEqual(Flow.CONTACT_PER_LOGIN, flow3.metadata.get('contact_creation'))

        # can see results for a flow
        response = self.client.get(reverse('flows.flow_results', args=[self.flow.uuid]))
        self.assertEqual(200, response.status_code)

        # check flow listing
        response = self.client.get(reverse('flows.flow_list'))
        self.assertEqual(list(response.context['object_list']), [flow3, voice_flow, flow2, flow1, self.flow])  # by saved_on

        # start a contact in a flow
        self.flow.start([], [self.contact])

        # test getting the json
        response = self.client.get(reverse('flows.flow_json', args=[self.flow.id]))
        self.assertIn('channels', response.json())
        self.assertIn('languages', response.json())
        self.assertIn('channel_countries', response.json())
        self.assertEqual(ActionSet.objects.all().count(), 28)

        json_dict = response.json()['flow']

        # test setting the json to a single actionset
        json_dict['action_sets'] = [dict(uuid=str(uuid4()), x=1, y=1, destination=None,
                                         actions=[dict(type='reply', msg=dict(base='This flow is more like a broadcast'))])]
        json_dict['rule_sets'] = []
        json_dict['entry'] = json_dict['action_sets'][0]['uuid']

        response = self.client.post(reverse('flows.flow_json', args=[self.flow.id]), json.dumps(json_dict), content_type="application/json")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(ActionSet.objects.all().count(), 25)

        # check that the flow only has a single actionset
        ActionSet.objects.get(flow=self.flow)

        # can't save with an invalid uuid
        json_dict['metadata']['saved_on'] = datetime_to_str(timezone.now())
        json_dict['action_sets'][0]['destination'] = 'notthere'

        response = self.client.post(reverse('flows.flow_json', args=[self.flow.id]), json.dumps(json_dict), content_type="application/json")
        self.assertEqual(200, response.status_code)

        self.flow.refresh_from_db()
        flow_json = self.flow.as_json()
        self.assertIsNone(flow_json['action_sets'][0]['destination'])

        # flow should still be there though
        self.flow.refresh_from_db()

        # should still have the original one, nothing changed
        response = self.client.get(reverse('flows.flow_json', args=[self.flow.id]))
        self.assertEqual(200, response.status_code)
        json_dict = response.json()

        # can't save against the other org's flow
        response = self.client.post(reverse('flows.flow_json', args=[other_flow.id]), json.dumps(json_dict), content_type="application/json")
        self.assertEqual(302, response.status_code)

        # can't save with invalid json
        with self.assertRaises(ValueError):
            response = self.client.post(reverse('flows.flow_json', args=[self.flow.id]), "badjson", content_type="application/json")

        # test simulation
        simulate_url = reverse('flows.flow_simulate', args=[self.flow.id])

        test_contact = Contact.get_test_contact(self.admin)
        group = self.create_group("players", [test_contact])
        contact_field = ContactField.get_or_create(self.org, self.admin, 'custom', 'custom')
        contact_field_value = Value.objects.create(contact=test_contact, contact_field=contact_field, org=self.org,
                                                   string_value="hey")

        response = self.client.get(simulate_url)
        self.assertEqual(response.status_code, 302)

        post_data = {'has_refresh': True, 'version': '1'}

        response = self.client.post(simulate_url, json.dumps(post_data), content_type="application/json")
        json_dict = response.json()

        self.assertFalse(group in test_contact.all_groups.all())
        self.assertFalse(test_contact.values.all())

        self.assertEqual(len(json_dict.keys()), 5)
        self.assertEqual(len(json_dict['messages']), 3)
        self.assertEqual('Test Contact has entered the &quot;Color Flow&quot; flow', json_dict['messages'][0]['text'])
        self.assertEqual("This flow is more like a broadcast", json_dict['messages'][1]['text'])
        self.assertEqual("Test Contact has exited this flow", json_dict['messages'][2]['text'])

        group = self.create_group("fans", [test_contact])
        contact_field_value = Value.objects.create(contact=test_contact, contact_field=contact_field, org=self.org,
                                                   string_value="hey")

        post_data['new_message'] = "Ok, Thanks"
        post_data['has_refresh'] = False

        response = self.client.post(simulate_url, json.dumps(post_data), content_type="application/json")
        self.assertEqual(200, response.status_code)
        json_dict = response.json()

        self.assertTrue(group in test_contact.all_groups.all())
        self.assertTrue(test_contact.values.all())
        self.assertEqual(test_contact.values.get(string_value='hey'), contact_field_value)

        self.assertEqual(len(json_dict.keys()), 5)
        self.assertIn('status', json_dict.keys())
        self.assertIn('visited', json_dict.keys())
        self.assertIn('activity', json_dict.keys())
        self.assertIn('messages', json_dict.keys())
        self.assertIn('description', json_dict.keys())
        self.assertEqual(json_dict['status'], 'success')
        self.assertEqual(json_dict['description'], 'Message sent to Flow')

        post_data['has_refresh'] = True

        response = self.client.post(simulate_url, json.dumps(post_data), content_type="application/json")
        self.assertEqual(200, response.status_code)
        json_dict = response.json()

        self.assertEqual(len(json_dict.keys()), 5)
        self.assertIn('status', json_dict.keys())
        self.assertIn('visited', json_dict.keys())
        self.assertIn('activity', json_dict.keys())
        self.assertIn('messages', json_dict.keys())
        self.assertIn('description', json_dict.keys())
        self.assertEqual(json_dict['status'], 'success')
        self.assertEqual(json_dict['description'], 'Message sent to Flow')

        # test our copy view
        response = self.client.post(reverse('flows.flow_copy', args=[self.flow.id]))
        flow_copy = Flow.objects.get(org=self.org, name="Copy of %s" % self.flow.name)
        self.assertRedirect(response, reverse('flows.flow_editor', args=[flow_copy.uuid]))

        FlowLabel.objects.create(name="one", org=self.org, parent=None)
        FlowLabel.objects.create(name="two", org=self.org2, parent=None)

        # test update view
        response = self.client.post(reverse('flows.flow_update', args=[self.flow.id]))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.context['form'].fields), 5)
        self.assertIn('name', response.context['form'].fields)
        self.assertIn('keyword_triggers', response.context['form'].fields)
        self.assertIn('ignore_triggers', response.context['form'].fields)

        # test broadcast view
        response = self.client.get(reverse('flows.flow_broadcast', args=[self.flow.id]))
        self.assertEqual(len(response.context['form'].fields), 4)
        self.assertIn('omnibox', response.context['form'].fields)
        self.assertIn('restart_participants', response.context['form'].fields)
        self.assertIn('include_active', response.context['form'].fields)

        post_data = dict()
        post_data['omnibox'] = "c-%s" % self.contact.uuid
        post_data['restart_participants'] = 'on'

        # nothing should happen, contacts are already active in the flow
        count = Broadcast.objects.all().count()
        self.client.post(reverse('flows.flow_broadcast', args=[self.flow.id]), post_data, follow=True)
        self.assertEqual(count, Broadcast.objects.all().count())

        FlowStart.objects.all().delete()

        # include people active in flows
        post_data['include_active'] = 'on'
        count = Msg.objects.all().count()
        self.client.post(reverse('flows.flow_broadcast', args=[self.flow.id]), post_data, follow=True)
        self.assertEqual(count + 1, Msg.objects.all().count())

        # we should have a flow start
        start = FlowStart.objects.get(flow=self.flow)

        # should be in a completed state
        self.assertEqual(FlowStart.STATUS_COMPLETE, start.status)
        self.assertEqual(1, start.contact_count)

        # do so again but don't restart the participants
        del post_data['restart_participants']

        self.client.post(reverse('flows.flow_broadcast', args=[self.flow.id]), post_data, follow=True)

        # should have a new flow start
        new_start = FlowStart.objects.filter(flow=self.flow).order_by('-created_on').first()
        self.assertNotEqual(start, new_start)
        self.assertEqual(FlowStart.STATUS_COMPLETE, new_start.status)
        self.assertEqual(0, new_start.contact_count)

        # mark that start as incomplete
        new_start.status = FlowStart.STATUS_STARTING
        new_start.save()

        # try to start again
        response = self.client.post(reverse('flows.flow_broadcast', args=[self.flow.id]), post_data, follow=True)

        # should have an error now
        self.assertTrue(response.context['form'].errors)

        # shouldn't have a new flow start as validation failed
        self.assertFalse(FlowStart.objects.filter(flow=self.flow).exclude(id__lte=new_start.id))

        # test ivr flow creation
        self.channel.role = 'SRCA'
        self.channel.save()

        post_data = dict(name="Message flow", expires_after_minutes=5, flow_type='F')
        response = self.client.post(reverse('flows.flow_create'), post_data, follow=True)
        msg_flow = Flow.objects.get(name=post_data['name'])

        self.assertEqual(200, response.status_code)
        self.assertEqual(response.request['PATH_INFO'], reverse('flows.flow_editor', args=[msg_flow.uuid]))
        self.assertEqual(msg_flow.flow_type, 'F')

        post_data = dict(name="Call flow", expires_after_minutes=5, flow_type='V')
        response = self.client.post(reverse('flows.flow_create'), post_data, follow=True)
        call_flow = Flow.objects.get(name=post_data['name'])

        self.assertEqual(200, response.status_code)
        self.assertEqual(response.request['PATH_INFO'], reverse('flows.flow_editor', args=[call_flow.uuid]))
        self.assertEqual(call_flow.flow_type, 'V')

        # test creating a  flow with base language
        # create the language for our org
        language = Language.create(self.org, self.flow.created_by, "English", 'eng')
        self.org.primary_language = language
        self.org.save()

        post_data = dict(name="Language Flow", expires_after_minutes=5, base_language=language.iso_code, flow_type='F')
        response = self.client.post(reverse('flows.flow_create'), post_data, follow=True)
        language_flow = Flow.objects.get(name=post_data['name'])

        self.assertEqual(200, response.status_code)
        self.assertEqual(response.request['PATH_INFO'], reverse('flows.flow_editor', args=[language_flow.uuid]))
        self.assertEqual(language_flow.base_language, language.iso_code)

    def test_views_viewers(self):
        # create a viewer
        self.viewer = self.create_user("Viewer")
        self.org.viewers.add(self.viewer)
        self.viewer.set_org(self.org)

        self.create_secondary_org()

        # create a flow for another org and a flow label
        flow2 = Flow.create(self.org2, self.admin2, "Flow2")
        flow_label = FlowLabel.objects.create(name="one", org=self.org, parent=None)

        flow_list_url = reverse('flows.flow_list')
        flow_archived_url = reverse('flows.flow_archived')
        flow_create_url = reverse('flows.flow_create')
        flowlabel_create_url = reverse('flows.flowlabel_create')

        # no login, no list
        response = self.client.get(flow_list_url)
        self.assertRedirect(response, reverse('users.user_login'))

        user = self.viewer
        user.first_name = "Test"
        user.last_name = "Contact"
        user.save()
        self.login(user)

        # list, should have only one flow (the one created in setUp)

        response = self.client.get(flow_list_url)
        self.assertEqual(1, len(response.context['object_list']))
        # no create links
        self.assertNotContains(response, flow_create_url)
        self.assertNotContains(response, flowlabel_create_url)
        # verify the action buttons we have
        self.assertNotContains(response, 'object-btn-unlabel')
        self.assertNotContains(response, 'object-btn-restore')
        self.assertNotContains(response, 'object-btn-archive')
        self.assertNotContains(response, 'object-btn-label')
        self.assertContains(response, 'object-btn-export')

        # can not label
        post_data = dict()
        post_data['action'] = 'label'
        post_data['objects'] = self.flow.pk
        post_data['label'] = flow_label.pk
        post_data['add'] = True

        response = self.client.post(flow_list_url, post_data, follow=True)
        self.assertEqual(1, response.context['object_list'].count())
        self.assertFalse(response.context['object_list'][0].labels.all())

        # can not archive
        post_data = dict()
        post_data['action'] = 'archive'
        post_data['objects'] = self.flow.pk
        response = self.client.post(flow_list_url, post_data, follow=True)
        self.assertEqual(1, response.context['object_list'].count())
        self.assertEqual(response.context['object_list'][0].pk, self.flow.pk)
        self.assertFalse(response.context['object_list'][0].is_archived)

        # inactive list shouldn't have any flows
        response = self.client.get(flow_archived_url)
        self.assertEqual(0, len(response.context['object_list']))

        response = self.client.get(reverse('flows.flow_editor', args=[self.flow.uuid]))
        self.assertEqual(200, response.status_code)
        self.assertFalse(response.context['mutable'])

        # we can fetch the json for the flow
        response = self.client.get(reverse('flows.flow_json', args=[self.flow.pk]))
        self.assertEqual(200, response.status_code)

        # but posting to it should redirect to a get
        response = self.client.post(reverse('flows.flow_json', args=[self.flow.pk]), post_data=response.content)
        self.assertEqual(302, response.status_code)

        self.flow.is_archived = True
        self.flow.save()

        response = self.client.get(flow_list_url)
        self.assertEqual(0, len(response.context['object_list']))

        # can not restore
        post_data = dict()
        post_data['action'] = 'archive'
        post_data['objects'] = self.flow.pk
        response = self.client.post(flow_archived_url, post_data, follow=True)
        self.assertEqual(1, response.context['object_list'].count())
        self.assertEqual(response.context['object_list'][0].pk, self.flow.pk)
        self.assertTrue(response.context['object_list'][0].is_archived)

        response = self.client.get(flow_archived_url)
        self.assertEqual(1, len(response.context['object_list']))

        # cannot create a flow
        response = self.client.get(flow_create_url)
        self.assertEqual(302, response.status_code)

        # cannot create a flowlabel
        response = self.client.get(flowlabel_create_url)
        self.assertEqual(302, response.status_code)

        # also shouldn't be able to view other flow
        response = self.client.get(reverse('flows.flow_editor', args=[flow2.uuid]))
        self.assertEqual(302, response.status_code)

    def test_flow_update_error(self):

        flow = self.get_flow('favorites')
        json_dict = flow.as_json()
        json_dict['action_sets'][0]['actions'].append(dict(type='add_label', labels=[dict(name='@badlabel')]))
        self.login(self.admin)
        response = self.client.post(reverse('flows.flow_json', args=[flow.pk]),
                                    json.dumps(json_dict),
                                    content_type="application/json")

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()['description'], 'Your flow could not be saved. Please refresh your browser.')

    def test_flow_start_with_start_msg(self):
        msg_in = self.create_msg(direction=INCOMING, contact=self.contact, text="I am coming")
        run, = self.flow.start([], [self.contact], start_msg=msg_in)

        msg_in.refresh_from_db()
        msg_out = Msg.objects.get(direction='O')

        # both msgs should be of type FLOW
        self.assertEqual(msg_in.msg_type, 'F')
        self.assertEqual(msg_out.msg_type, 'F')

        self.assertEqual({int(m) for m in run.message_ids}, {msg_in.id, msg_out.id})

        run_msgs = run.get_messages().order_by('created_on')
        self.assertEqual(list(run_msgs), [msg_in, msg_out])

        self.assertEqual(len(run.path), 2)

    @also_in_flowserver
    def test_quick_replies(self):
        flow = self.get_flow('quick_replies')
        run, = flow.start([], [self.contact4])

        run.refresh_from_db()
        self.assertEqual(len(run.path), 2)

        # check flow sent a message with quick replies
        msg = Msg.objects.get(direction='O')
        self.assertEqual(msg.metadata, {'quick_replies': ['Sim', 'No']})

    @also_in_flowserver
    def test_multiple(self):
        run1, = self.flow.start([], [self.contact])

        # create a second flow and start our same contact
        self.flow2 = self.flow.copy(self.flow, self.flow.created_by)
        run2, = self.flow2.start([], [self.contact])

        run1.refresh_from_db()
        run2.refresh_from_db()

        # only the second run should be active
        self.assertFalse(run1.is_active)
        self.assertEqual(len(run1.path), 2)

        self.assertTrue(run2.is_active)
        self.assertEqual(len(run2.path), 2)

        # send in a message
        incoming = self.create_msg(direction=INCOMING, contact=self.contact, text="Orange", created_on=timezone.now())
        self.assertTrue(Flow.find_and_handle(incoming)[0])

        run1.refresh_from_db()
        run2.refresh_from_db()

        # only the second flow should get it
        self.assertEqual(len(run1.path), 2)
        self.assertEqual(len(run2.path), 3)

        # start the flow again for our contact
        run3, = self.flow.start([], [self.contact], restart_participants=True)

        run1.refresh_from_db()
        run3.refresh_from_db()

        # should have two flow runs for this contact and flow
        self.assertFalse(run1.is_active)
        self.assertTrue(run3.is_active)

        self.assertEqual(len(run1.path), 2)
        self.assertEqual(len(run3.path), 2)

        # send in a message, this should be handled by our first flow, which has a more recent run active
        incoming = self.create_msg(direction=INCOMING, contact=self.contact, text="blue")
        self.assertTrue(Flow.find_and_handle(incoming)[0])

        run1.refresh_from_db()
        run3.refresh_from_db()

        self.assertEqual(len(run1.path), 2)
        self.assertEqual(len(run3.path), 3)

        # if we exclude existing and try starting again, nothing happens
        self.flow.start([], [self.contact], restart_participants=False)

        # no new runs
        self.assertEqual(self.flow.runs.count(), 2)

        # check our run results
        results = self.flow.runs.order_by('-id').first().results

        self.assertEqual(len(results), 1)
        self.assertEqual(results['color']['name'], 'color')
        self.assertEqual(results['color']['category'], 'Blue')
        self.assertEqual(results['color']['value'], 'blue')
        self.assertEqual(results['color']['input'], incoming.text)

    def test_ignore_keyword_triggers(self):
        self.flow.start([], [self.contact])

        # create a second flow
        self.flow2 = Flow.create(self.org, self.admin, "Kiva Flow")

        self.flow2 = self.flow.copy(self.flow, self.flow.created_by)

        # add a trigger on flow2
        Trigger.objects.create(org=self.org, keyword='kiva', flow=self.flow2,
                               created_by=self.admin, modified_by=self.admin)

        incoming = self.create_msg(direction=INCOMING, contact=self.contact, text="kiva")

        self.assertTrue(Trigger.find_and_handle(incoming))
        self.assertTrue(FlowRun.objects.filter(flow=self.flow2, contact=self.contact))

        self.flow.ignore_triggers = True
        self.flow.save()
        self.flow.start([], [self.contact], restart_participants=True)

        other_incoming = self.create_msg(direction=INCOMING, contact=self.contact, text="kiva")

        self.assertFalse(Trigger.find_and_handle(other_incoming))

        # complete the flow
        incoming = self.create_msg(direction=INCOMING, contact=self.contact, text="orange")
        self.assertTrue(Flow.find_and_handle(incoming)[0])

        # now we should trigger the other flow as we are at our terminal flow
        self.assertTrue(Trigger.find_and_handle(other_incoming))

    @patch('temba.flows.models.Flow.handle_ussd_ruleset_action',
           return_value=dict(handled=True, destination=None, step=None, msgs=[]))
    def test_ussd_ruleset_sends_message(self, handle_ussd_ruleset_action):
        definition = self.flow.as_json()

        # set flow to USSD and have a USSD ruleset
        definition['flow_type'] = 'U'
        definition['rule_sets'][0]['ruleset_type'] = "wait_menu"

        self.flow.update(definition)

        # start flow
        self.flow.start([], [self.contact])

        self.assertTrue(handle_ussd_ruleset_action.called)
        self.assertEqual(handle_ussd_ruleset_action.call_count, 1)

    @patch('temba.flows.models.Flow.handle_ussd_ruleset_action',
           return_value=dict(handled=True, destination=None, step=None, msgs=[]))
    def test_triggered_start_with_ussd(self, handle_ussd_ruleset_action):
        definition = self.flow.as_json()

        # set flow to USSD and have a USSD ruleset
        definition['flow_type'] = 'U'
        definition['rule_sets'][0]['ruleset_type'] = "wait_menu"

        self.flow.update(definition)

        # create a trigger
        Trigger.objects.create(org=self.org, keyword='derp', flow=self.flow,
                               created_by=self.admin, modified_by=self.admin)

        # create an incoming message
        incoming = self.create_msg(direction=INCOMING, contact=self.contact, text="derp")

        self.assertTrue(Trigger.find_and_handle(incoming))

        self.assertTrue(handle_ussd_ruleset_action.called)
        self.assertEqual(handle_ussd_ruleset_action.call_count, 1)


class ActionPackedTest(FlowFileTest):

    def setUp(self):
        super(ActionPackedTest, self).setUp()
        self.flow = self.get_flow('action_packed')

    def start_flow(self):
        self.flow.start([], [self.contact], restart_participants=True)

        # send a couple messages in the flow to trigger actions
        self.send("Trey Anastasio")
        self.send("Male")

    @also_in_flowserver
    def test_send_message(self):

        self.start_flow()

        # check our quick replies are there
        msgs = list(Msg.objects.filter(direction='O').order_by('id'))

        # first message has file attachment
        self.assertIn('4de254db-9d96-4420-8ba6-337d7f280010.png', msgs[0].attachments[0])

        # second one has quick replies
        self.assertEqual({"quick_replies": ["Male", "Female"]}, msgs[1].metadata)

        # third one has expression attachment
        self.assertEqual('image:http://textit.in/static?gender=Male', msgs[2].attachments[0])

        # to test the 'send all' option we need a telegram channel
        self.telegram = Channel.create(self.org, self.user, None, 'TG', name='Telegram')

        self.send("sendall")
        self.assertEqual(2, Msg.objects.filter(text='This is going to all urns', direction=OUTGOING).count())
        self.assertIsNotNone(Msg.objects.filter(text="Thanks Trey Anastasio, you are male.").first())

    @also_in_flowserver
    def test_add_remove_from_group(self):

        # convert the static groups created by import into dynamic ones
        groups = ContactGroup.user_groups.filter(name__in=('Males', 'Females'))
        for group in groups:
            group.query = 'gender="%s"' % group.name[0:-1]
            group.update_query(group.query)

        self.start_flow()

        # check that we are in (and only in) two groups including our dynamic one
        self.assertInUserGroups(self.contact, ('Males', 'Cat Facts'), only=True)

        # Send another contact down, but make them a female
        self.contact2 = self.create_contact('', '+250788383383')

        self.flow.start([], [self.contact2])
        self.send("Steph", self.contact2)
        self.send("Female", self.contact2)
        self.assertInUserGroups(self.contact2, ('Females', 'Cat Facts', 'Customers'), only=True)

        # can't create groups dynamically
        self.contact.refresh_from_db()
        group_name = self.contact.name
        action = self.get_action_json(self.flow, '948877da-64f5-4667-978a-3e3febcb4664')
        action['groups'][0] = '@step.contact'
        self.update_action_json(self.flow, action)
        self.start_flow()
        self.assertIsNone(ContactGroup.user_groups.filter(name=group_name).first())

        # but if that group exists already, we can add to it
        ContactGroup.get_or_create(self.org, self.admin, self.contact.name)
        self.start_flow()
        self.assertInUserGroups(self.contact, [group_name])

        # try matching with a trailing space
        group = ContactGroup.user_groups.filter(name=group_name).first()
        group.update_contacts(self.admin, [self.contact], False)
        self.contact.name += ' '
        self.contact.save()
        self.start_flow()
        self.assertInUserGroups(self.contact, [group_name])

        # try adding explicitly to a dynamic group, count should stay the same
        start_count = group.contacts.count()
        group = ContactGroup.user_groups.filter(name='Females').first()
        action['groups'][0] = {'name': group.name, 'uuid': group.uuid}
        self.update_action_json(self.flow, action)
        self.start_flow()
        self.assertEqual(start_count, group.contacts.count())

        # adding to inactive group
        customers = ContactGroup.user_groups.filter(name='Customers').first()
        customers.is_active = False
        customers.save()
        self.assertIsNone(ContactGroup.user_groups.filter(name='Customers', is_active=True).first())

        # updating with our inactive group will recreate it
        action['groups'][0] = {'name': 'Customers'}
        self.update_action_json(self.flow, action)
        self.assertIsNotNone(ContactGroup.user_groups.filter(name='Customers', is_active=True).first())

    @also_in_flowserver
    def test_labeling(self):
        self.start_flow()
        msg = Msg.objects.filter(direction=INCOMING, text='Male').order_by('-id').first()
        self.assertEqual('Friends', msg.labels.all().first().name)

    @also_in_flowserver
    def test_trigger_flow_action(self):

        self.create_contact('Oprah Winfrey', '+12065552121')

        self.start_flow()

        # we should have fired off a new flow run for a total of two
        self.assertEqual(2, Contact.objects.all().count())
        self.assertEqual(2, FlowRun.objects.all().count())

        # our newest flow run should be down the triggered flow
        triggered_run = FlowRun.objects.all().order_by('-id').first()
        self.assertEqual('Favorite Color', triggered_run.flow.name)

        msg = triggered_run.get_messages().first()
        self.assertEqual('Started by Trey Anastasio. What is your favorite color?', msg.text)

    @also_in_flowserver
    @override_settings(SEND_EMAILS=True)
    def test_email(self):
        self.start_flow()

        # trigger our email action
        self.send('email')
        self.assertOutbox(0, 'no-reply@temba.io', 'Message from Trey Anastasio',
                          'Hi there, my name is Trey Anastasio.', ['support@website.com'])

        # now try with a custom from address
        branding = copy.deepcopy(settings.BRANDING)
        branding['rapidpro.io']['flow_email'] = 'no-reply@mybrand.com'
        with override_settings(BRANDING=branding):
            self.start_flow()
            self.send('email')
            self.assertOutbox(1, 'no-reply@mybrand.com', 'Message from Trey Anastasio',
                              'Hi there, my name is Trey Anastasio.', ['support@website.com'])

        # same thing, but with a custom smtp server
        self.org.add_smtp_config('support@example.com', 'smtp.example.com', 'support@example.com', 'secret', '465', 'T', self.admin)
        self.start_flow()
        self.send('email')
        self.assertOutbox(2, 'support@example.com', 'Message from Trey Anastasio',
                          'Hi there, my name is Trey Anastasio.', ['support@website.com'])

        # test contacts should get log messages
        Contact.set_simulation(True)
        test_contact = Contact.get_test_contact(self.admin)
        self.flow.start([], [test_contact])

        self.send('Test Contact', test_contact)
        self.send('Female', test_contact)
        self.send('email', test_contact)

        log = ActionLog.objects.order_by('-id').first()
        self.assertEqual('&quot;Hi there, my name is Test Contact.&quot; would be sent to &quot;support@website.com&quot;', log.text)

        # empty recipient list should fail
        with self.assertRaises(FlowException):
            self.update_action_field(self.flow, '431b0c69-cc9f-4017-b667-0823e5017d3e', 'emails', [])

    def test_update_reserved_keys(self):
        """
        Reserved field names only applies to old engine
        """
        name_action_uuid = '0afb91da-9eb7-4e11-9cd8-ae01952c1153'
        # throw exception for other reserved words except name and first_name, and URN schemes
        for key in Contact.RESERVED_FIELD_KEYS:
            if key not in ['name', 'first_name', 'tel_e164'] + list(URN.VALID_SCHEMES):
                with self.assertRaises(ValueError):
                    action = self.get_action_json(self.flow, name_action_uuid)
                    action['label'] = key
                    action['field'] = key
                    action['value'] = ''
                    self.update_action_json(self.flow, action)

    @also_in_flowserver
    def test_update_contact(self):

        gender_action_uuid = '8492be2d-b6d1-4b1e-a15e-a7d1fa3a0671'
        name_action_uuid = '0afb91da-9eb7-4e11-9cd8-ae01952c1153'

        def update_save_fields(action, label, value):
            action['label'] = label
            action['field'] = ContactField.make_key(label)
            action['value'] = value
            return action

        # boring field updates
        self.start_flow()
        self.contact.refresh_from_db()
        self.assertEqual('Male', self.contact.get_field_display('gender'))
        self.assertEqual('Trey Anastasio', self.contact.name)

        # update action to instead clear the gender field
        self.update_action_field(self.flow, gender_action_uuid, 'value', '')
        self.start_flow()
        self.assertEqual(None, Contact.objects.get(id=self.contact.id).get_field_raw('gender'))

        # test setting just the first name
        action = update_save_fields(self.get_action_json(self.flow, name_action_uuid), 'First Name', 'Frank')
        self.update_action_json(self.flow, action)
        self.start_flow()
        self.contact.refresh_from_db()
        self.assertEqual("Frank Anastasio", self.contact.name)

        # we should strip whitespace
        self.update_action_field(self.flow, name_action_uuid, 'value', ' Jackson ')
        self.start_flow()
        self.contact.refresh_from_db()
        self.assertEqual('Jackson Anastasio', self.contact.name)

        # first name works starting with a single word
        self.contact.name = 'Percy'
        self.contact.save()
        self.update_action_field(self.flow, name_action_uuid, 'value', ' Cole')
        self.start_flow()
        self.contact.refresh_from_db()
        self.assertEqual('Cole', self.contact.name)

        # test saving something really long to a new field
        action = self.get_action_json(self.flow, gender_action_uuid)
        action = update_save_fields(action, 'Last Message',
                                    'This is a long message, longer than 160 characters, longer '
                                    'than 250 characters, all the way up to 500 some characters '
                                    'long because sometimes people save entire messages to their '
                                    'contact fields and we want to enable that for them so that '
                                    'they can do what they want with the platform.')

        self.update_action_json(self.flow, action)
        self.start_flow()
        self.assertEqual(action['value'], self.contact.get_field('last_message').string_value)

    @also_in_flowserver
    def test_add_phone_number(self):

        name_action_uuid = '0afb91da-9eb7-4e11-9cd8-ae01952c1153'

        # test saving a contact's phone number
        action = self.get_action_json(self.flow, name_action_uuid)
        action['label'] = 'Phone Number'
        action['field'] = 'tel_e164'
        action['value'] = '+12065551212'
        self.update_action_json(self.flow, action)

        # make sure they have a twitter urn first
        self.contact.urns.add(ContactURN.create(self.org, None, 'twitter:enewcomer'))
        self.assertIsNotNone(self.contact.urns.filter(path='enewcomer').first())

        # add another phone number to make sure it doesn't get removed too
        self.contact.urns.add(ContactURN.create(self.org, None, 'tel:+18005551212'))
        self.assertEqual(3, self.contact.urns.all().count())

        # create an inbound message on our original phone number
        self.create_msg(direction=INCOMING, contact=self.contact,
                        text="+12065551212", contact_urn=self.contact.urns.filter(path='+250788382382').first())

        # create another contact with that phone number, to test stealing
        robbed = self.create_contact("Robzor", "+12065551212")
        self.start_flow()

        # updating Phone Number should not create a contact field
        self.assertIsNone(ContactField.objects.filter(org=self.org, key='tel_e164').first())

        # instead it should update the tel urn for our contact
        self.contact = Contact.objects.get(id=self.contact.pk)
        self.assertEqual(4, self.contact.urns.all().count())
        self.assertIsNotNone(self.contact.urns.filter(path='+12065551212').first())

        # we should still have our twitter scheme
        self.assertIsNotNone(self.contact.urns.filter(path='enewcomer').first())

        # and our other phone number
        self.assertIsNotNone(self.contact.urns.filter(path='+18005551212').first())

        # and our original number too
        self.assertIsNotNone(self.contact.urns.filter(path='+12065552020').first())

        # robzor shouldn't have a number anymore
        self.assertFalse(robbed.urns.all())

    @also_in_flowserver
    def test_save_contact_simulator_messages(self):

        action = self.get_action_json(self.flow, '0afb91da-9eb7-4e11-9cd8-ae01952c1153')
        Contact.set_simulation(True)
        test_contact = Contact.get_test_contact(self.admin)
        test_contact_urn = test_contact.urns.all().first()

        def test_with_save(label, value):
            action['label'] = label
            action['field'] = ContactField.make_key(label)
            action['value'] = value
            self.update_action_json(self.flow, action)

            ActionLog.objects.all().delete()
            self.flow.start([], [test_contact], restart_participants=True)
            self.send('Trey Anastasio', test_contact)
            self.send('Male', test_contact)

        # valid email
        test_with_save('mailto', 'foo@bar.com')
        self.assertEqual(ActionLog.objects.all().order_by('id')[3].text,
                         'Added foo@bar.com as @contact.mailto - skipped in simulator')

        # invalid email
        test_with_save('mailto', 'foobar.com')
        self.assertEqual(ActionLog.objects.all().order_by('id')[3].text,
                         'Contact not updated, invalid connection for contact (mailto:foobar.com)')

        # URN should be unchanged on the simulator contact
        test_contact = Contact.objects.get(id=test_contact.id)
        self.assertEqual(test_contact_urn, test_contact.urns.all().first())

        # try saving some empty data into mailto
        test_with_save('mailto', '@contact.mailto')
        self.assertEqual(ActionLog.objects.all().order_by('id')[3].text,
                         'Contact not updated, missing connection for contact')

    @also_in_flowserver
    def test_set_language_action(self):

        self.org.set_languages(self.admin, ['eng', 'spa'], 'eng')
        self.start_flow()
        self.send('spanish')
        self.contact.refresh_from_db()
        self.assertEqual('spa', self.contact.language)

        # check that some messages come back in spanish
        self.send('startover')
        msg = Msg.objects.filter(direction='O').order_by('-id').first()
        self.assertEqual('Como te llamas?', msg.text)

        # setting the language to blank should clear language
        self.update_action_field(self.flow, 'bcfaa58c-e088-477a-933b-3a5bba01284d', 'lang', '')
        self.flow.start([], [self.contact], restart_participants=True)
        self.send("Trey Anastasio")
        self.send('niño')
        self.send('spanish')
        self.contact.refresh_from_db()
        self.assertIsNone(self.contact.language)


class ActionTest(TembaTest):

    def setUp(self):
        super(ActionTest, self).setUp()

        self.contact = self.create_contact('Eric', '+250788382382')
        self.contact2 = self.create_contact('Nic', '+250788383383')

        self.flow = self.get_flow('color')

        self.other_group = self.create_group("Other", [])

    def execute_action(self, action, run, msg, **kwargs):
        context = run.flow.build_expressions_context(run.contact, msg)
        return action.execute(run, context, None, msg, **kwargs)

    def test_reply_action(self):
        msg = self.create_msg(direction=INCOMING, contact=self.contact, text="Green is my favorite")
        run = FlowRun.create(self.flow, self.contact)

        with self.assertRaises(FlowException):
            ReplyAction.from_json(self.org, {'type': ReplyAction.TYPE})

        with self.assertRaises(FlowException):
            ReplyAction.from_json(self.org, {'type': ReplyAction.TYPE, ReplyAction.MESSAGE: dict()})

        with self.assertRaises(FlowException):
            ReplyAction.from_json(self.org, {'type': ReplyAction.TYPE, ReplyAction.MESSAGE: dict(base="")})

        action = ReplyAction(str(uuid4()), dict(base="We love green too!"))
        self.execute_action(action, run, msg)
        msg = Msg.objects.get(contact=self.contact, direction='O')
        self.assertEqual("We love green too!", msg.text)

        Broadcast.objects.all().delete()

        action_json = action.as_json()
        action = ReplyAction.from_json(self.org, action_json)
        self.assertEqual(dict(base="We love green too!"), action.msg)

        self.execute_action(action, run, msg)

        response = msg.responses.get()
        self.assertEqual("We love green too!", response.text)
        self.assertEqual(self.contact, response.contact)

    def test_send_all_action(self):
        contact = self.create_contact('Stephen', '+12078778899', twitter='stephen')
        msg = self.create_msg(direction=INCOMING, contact=contact, text="Green is my favorite")
        run = FlowRun.create(self.flow, self.contact)

        action = ReplyAction(str(uuid4()), dict(base="We love green too!"), None, send_all=True)
        action_replies = self.execute_action(action, run, msg)
        self.assertEqual(len(action_replies), 1)
        for action_reply in action_replies:
            self.assertIsInstance(action_reply, Msg)

        replies = Msg.objects.filter(contact=contact, direction='O')
        self.assertEqual(replies.count(), 1)
        self.assertIsNone(replies.filter(contact_urn__path='stephen').first())
        self.assertIsNotNone(replies.filter(contact_urn__path='+12078778899').first())

        Broadcast.objects.all().delete()
        Msg.objects.all().delete()

        msg = self.create_msg(direction=INCOMING, contact=contact, text="Green is my favorite")
        run = FlowRun.create(self.flow, self.contact)

        # create twitter channel
        Channel.create(self.org, self.user, None, 'TT')
        delattr(self.org, '__schemes__%s' % Channel.ROLE_SEND)

        action_json = action.as_json()
        action = ReplyAction.from_json(self.org, action_json)
        self.assertEqual(dict(base="We love green too!"), action.msg)
        self.assertTrue(action.send_all)

        action_replies = self.execute_action(action, run, msg)
        self.assertEqual(len(action_replies), 2)
        for action_reply in action_replies:
            self.assertIsInstance(action_reply, Msg)

        replies = Msg.objects.filter(contact=contact, direction='O')
        self.assertEqual(replies.count(), 2)
        self.assertIsNotNone(replies.filter(contact_urn__path='stephen').first())
        self.assertIsNotNone(replies.filter(contact_urn__path='+12078778899').first())

    def test_media_action(self):
        msg = self.create_msg(direction=INCOMING, contact=self.contact, text="Green is my favorite")
        run = FlowRun.create(self.flow, self.contact)

        action = ReplyAction(str(uuid4()), dict(base="We love green too!"), 'image/jpeg:path/to/media.jpg')
        self.execute_action(action, run, msg)
        reply_msg = Msg.objects.get(contact=self.contact, direction='O')
        self.assertEqual("We love green too!", reply_msg.text)
        self.assertEqual(reply_msg.attachments, ["image/jpeg:https://%s/%s" % (settings.AWS_BUCKET_DOMAIN, 'path/to/media.jpg')])

        Broadcast.objects.all().delete()
        Msg.objects.all().delete()
        msg = self.create_msg(direction=INCOMING, contact=self.contact, text="Green is my favorite")

        action_json = action.as_json()
        action = ReplyAction.from_json(self.org, action_json)
        self.assertEqual(dict(base="We love green too!"), action.msg)
        self.assertEqual('image/jpeg:path/to/media.jpg', action.media)

        self.execute_action(action, run, msg)

        response = msg.responses.get()
        self.assertEqual("We love green too!", response.text)
        self.assertEqual(response.attachments, ["image/jpeg:https://%s/%s" % (settings.AWS_BUCKET_DOMAIN, 'path/to/media.jpg')])
        self.assertEqual(self.contact, response.contact)

    def test_media_expression(self):
        msg = self.create_msg(direction=INCOMING, contact=self.contact, text="profile")
        run = FlowRun.create(self.flow, self.contact)

        action = ReplyAction(str(uuid4()), dict(base="Here is your profile pic."), 'image:/photos/contacts/@(contact.name).jpg')

        # export and import our json to make sure that works as well
        action_json = action.as_json()
        action = ReplyAction.from_json(self.org, action_json)

        # now execute it
        self.execute_action(action, run, msg)
        reply_msg = Msg.objects.get(contact=self.contact, direction='O')
        self.assertEqual("Here is your profile pic.", reply_msg.text)
        self.assertEqual(reply_msg.attachments, ["image:/photos/contacts/Eric.jpg"])

        response = msg.responses.get()
        self.assertEqual("Here is your profile pic.", response.text)
        self.assertEqual(self.contact, response.contact)

    def test_quick_replies_action(self):
        msg = self.create_msg(direction=INCOMING, contact=self.contact, text="Yes")
        run = FlowRun.create(self.flow, self.contact)

        payload = [dict(eng='Yes'), dict(eng='No')]

        action = ReplyAction(str(uuid4()), msg=dict(base="Are you fine?"), quick_replies=payload)
        action_json = action.as_json()
        action = ReplyAction.from_json(self.org, action_json)

        self.execute_action(action, run, msg)
        self.assertEqual(action.msg, dict(base="Are you fine?"))
        self.assertEqual(action.quick_replies, payload)

    def test_ussd_action(self):
        self.channel.delete()
        self.channel = Channel.create(self.org, self.user, 'RW', 'JNU', None, '+250788123123',
                                      role=Channel.ROLE_USSD)

        msg = self.create_msg(direction=INCOMING, contact=self.contact, text="Green is my favorite")
        run = FlowRun.create(self.flow, self.contact)

        menu_uuid = str(uuid4())

        ussd_ruleset = RuleSet.objects.create(flow=self.flow, uuid=str(uuid4()), x=0, y=0, ruleset_type=RuleSet.TYPE_WAIT_USSD_MENU)
        ussd_ruleset.rules = [Rule(str(uuid4()), dict(base="All Responses"), menu_uuid, 'R', TrueTest()).as_json()]
        ussd_ruleset.save()

        # without USSD config we only get an empty UssdAction
        action = UssdAction.from_ruleset(ussd_ruleset, run)
        execution = self.execute_action(action, run, msg)

        self.assertIsNone(action.msg)
        self.assertEqual(execution, [])

        # add menu rules
        ussd_ruleset.rules = [Rule(str(uuid4()), dict(base="All Responses"), menu_uuid, 'R', TrueTest()).as_json(),
                              Rule(str(uuid4()), dict(base="Test1"), None, 'R', EqTest(test="1"), dict(base="Test1")).as_json(),
                              Rule(str(uuid4()), dict(base="Test2"), None, 'R', EqTest(test="2"), dict(base="Test2")).as_json()]
        ussd_ruleset.save()

        # add ussd message
        config = {
            "ussd_message": {"base": "test"}
        }
        ussd_ruleset.config = config
        action = UssdAction.from_ruleset(ussd_ruleset, run)
        execution = self.execute_action(action, run, msg)

        self.assertIsNotNone(action.msg)
        self.assertEqual(action.msg, {u'base': u'test\n1: Test1\n2: Test2\n'})
        self.assertIsInstance(execution[0], Msg)
        self.assertEqual(execution[0].text, u'test\n1: Test1\n2: Test2')

        Broadcast.objects.all().delete()

    def test_multilanguage_ussd_menu_partly_translated(self):
        self.channel.delete()
        self.channel = Channel.create(self.org, self.user, 'RW', 'JNU', None, '+250788123123',
                                      role=Channel.ROLE_USSD)

        msg = self.create_msg(direction=INCOMING, contact=self.contact, text="Green is my favorite")
        run = FlowRun.create(self.flow, self.contact)

        menu_uuid = str(uuid4())

        ussd_ruleset = RuleSet.objects.create(flow=self.flow, uuid=str(uuid4()), x=0, y=0, ruleset_type=RuleSet.TYPE_WAIT_USSD_MENU)
        ussd_ruleset.rules = [Rule(str(uuid4()), dict(base="All Responses"), menu_uuid, 'R', TrueTest()).as_json()]
        ussd_ruleset.save()

        english = Language.create(self.org, self.admin, "English", 'eng')
        Language.create(self.org, self.admin, "Hungarian", 'hun')
        Language.create(self.org, self.admin, "Russian", 'rus')
        self.flow.org.primary_language = english

        # add menu rules
        ussd_ruleset.rules = [Rule(str(uuid4()), dict(base="All Responses"), menu_uuid, 'R', TrueTest()).as_json(),
                              Rule(str(uuid4()), dict(base="Test1"), None, 'R', EqTest(test="1"), dict(eng="labelENG", hun="labelHUN")).as_json(),
                              Rule(str(uuid4()), dict(base="Test2"), None, 'R', EqTest(test="2"), dict(eng="label2ENG")).as_json()]
        ussd_ruleset.save()

        # add ussd message
        config = {
            "ussd_message": {"eng": "testENG", "hun": "testHUN"}
        }

        ussd_ruleset.config = config
        action = UssdAction.from_ruleset(ussd_ruleset, run)
        execution = self.execute_action(action, run, msg)

        self.assertIsNotNone(action.msg)
        # we have three languages, although only 2 are (partly) translated
        self.assertEqual(len(action.msg.keys()), 3)
        six.assertCountEqual(self, list(action.msg.keys()), [u'rus', u'hun', u'eng'])

        # we don't have any translation for Russian, so it should be the same as eng
        self.assertEqual(action.msg['eng'], action.msg['rus'])

        # we have partly translated hungarian labels
        self.assertNotEqual(action.msg['eng'], action.msg['hun'])

        # the missing translation should be the same as the english label
        self.assertNotIn('labelENG', action.msg['hun'])
        self.assertIn('label2ENG', action.msg['hun'])

        self.assertEqual(action.msg['hun'], u'testHUN\n1: labelHUN\n2: label2ENG\n')

        # the msg sent out is in english
        self.assertIsInstance(execution[0], Msg)
        self.assertEqual(execution[0].text, u'testENG\n1: labelENG\n2: label2ENG')

        # now set contact's language to something we don't have in our org languages
        self.contact.language = 'fra'
        self.contact.save(update_fields=('language',))
        run = FlowRun.create(self.flow, self.contact)

        # resend the message to him
        execution = self.execute_action(action, run, msg)

        # he will still get the english (base language)
        self.assertIsInstance(execution[0], Msg)
        self.assertEqual(execution[0].text, u'testENG\n1: labelENG\n2: label2ENG')

        # now set contact's language to hungarian
        self.contact.language = 'hun'
        self.contact.save(update_fields=('language',))
        run = FlowRun.create(self.flow, self.contact)

        # resend the message to him
        execution = self.execute_action(action, run, msg)

        # he will get the partly translated hungarian version
        self.assertIsInstance(execution[0], Msg)
        self.assertEqual(execution[0].text, u'testHUN\n1: labelHUN\n2: label2ENG')

        Broadcast.objects.all().delete()

    def test_trigger_flow_action(self):
        flow = self.create_flow()
        run = FlowRun.create(self.flow, self.contact)

        # add a channel to make sure that country is ambiguous
        Channel.create(self.org, self.admin, 'US', 'EX', schemes=['tel'])
        delattr(self.org, '_country_code')
        self.org.country = None
        self.org.save()

        # set a contact field with another phone number
        self.contact.set_field(self.admin, "other_contact_tel", "+12065551212", "Other Contact Tel")

        action = TriggerFlowAction(str(uuid4()), flow, [], [self.contact], ["@contact.other_contact_tel"])
        self.execute_action(action, run, None)

        # should have created a new contact with the above variable
        self.assertIsNotNone(Contact.from_urn(self.org, "tel:+12065551212"))

        action_json = action.as_json()
        action = TriggerFlowAction.from_json(self.org, action_json)
        self.assertEqual(action.flow.pk, flow.pk)

        self.assertTrue(FlowRun.objects.filter(contact=self.contact, flow=flow))

        action = TriggerFlowAction(str(uuid4()), flow, [self.other_group], [], [])
        run = FlowRun.create(self.flow, self.contact)
        msgs = self.execute_action(action, run, None)

        self.assertFalse(msgs)

        self.other_group.update_contacts(self.user, [self.contact2], True)

        action = TriggerFlowAction(str(uuid4()), flow, [self.other_group], [self.contact], [])
        run = FlowRun.create(self.flow, self.contact)
        self.execute_action(action, run, None)

        self.assertTrue(FlowRun.objects.filter(contact=self.contact2, flow=flow))

        # delete the group
        self.other_group.is_active = False
        self.other_group.save()

        self.assertTrue(action.groups)
        self.assertTrue(self.other_group.pk in [g.pk for g in action.groups])
        # should create new group the next time the flow is read
        updated_action = TriggerFlowAction.from_json(self.org, action.as_json())
        self.assertTrue(updated_action.groups)
        self.assertFalse(self.other_group.pk in [g.pk for g in updated_action.groups])

    def test_send_action(self):
        # previously @step.contact was the run contact and @contact would become the recipient but that has been
        # changed so that both are the run contact
        msg_body = "Hi @contact.name (@contact.state). @step.contact (@step.contact.state) is in the flow"

        self.contact.set_field(self.user, 'state', "WA", label="State")
        self.contact2.set_field(self.user, 'state', "GA", label="State")
        run = FlowRun.create(self.flow, self.contact)

        action = SendAction(str(uuid4()), dict(base=msg_body), [], [self.contact2], [])
        self.execute_action(action, run, None)

        action_json = action.as_json()
        action = SendAction.from_json(self.org, action_json)
        self.assertEqual(action.msg['base'], msg_body)
        self.assertEqual(action.media, dict())

        broadcast = Broadcast.objects.get()
        self.assertEqual(broadcast.text, dict(base=msg_body))
        self.assertEqual(broadcast.base_language, 'base')
        self.assertEqual(broadcast.get_messages().count(), 1)
        msg = broadcast.get_messages().first()
        self.assertEqual(msg.contact, self.contact2)
        self.assertEqual(msg.text, "Hi Eric (WA). Eric (WA) is in the flow")

        # empty message should be a no-op
        action = SendAction(str(uuid4()), dict(base=""), [], [self.contact], [])
        self.execute_action(action, run, None)
        self.assertEqual(Broadcast.objects.all().count(), 1)

        # try with a test contact and a group
        test_contact = Contact.get_test_contact(self.user)
        test_contact.name = "Mr Test"
        test_contact.save()
        test_contact.set_field(self.user, 'state', "IN", label="State")

        self.other_group.update_contacts(self.user, [self.contact2], True)

        action = SendAction(str(uuid4()), dict(base=msg_body), [self.other_group], [test_contact], [])
        run = FlowRun.create(self.flow, test_contact)
        self.execute_action(action, run, None)

        # since we are test contact now, no new broadcasts
        self.assertEqual(Broadcast.objects.all().count(), 1)

        # but we should have logged instead
        logged = "Sending &#39;Hi Mr Test (IN). Mr Test (IN) is in the flow&#39; to 2 contacts"
        self.assertEqual(ActionLog.objects.all().first().text, logged)

        # delete the group
        self.other_group.is_active = False
        self.other_group.save()

        self.assertTrue(action.groups)
        self.assertTrue(self.other_group.pk in [g.pk for g in action.groups])
        # should create new group the next time the flow is read
        updated_action = SendAction.from_json(self.org, action.as_json())
        self.assertTrue(updated_action.groups)
        self.assertFalse(self.other_group.pk in [g.pk for g in updated_action.groups])

        # test send media to someone else
        run = FlowRun.create(self.flow, self.contact)
        msg_body = 'I am a media message message'

        action = SendAction(str(uuid4()), dict(base=msg_body), [], [self.contact2], [], dict(base='image/jpeg:attachments/picture.jpg'))
        self.execute_action(action, run, None)

        action_json = action.as_json()
        action = SendAction.from_json(self.org, action_json)
        self.assertEqual(action.msg['base'], msg_body)
        self.assertEqual(action.media['base'], 'image/jpeg:attachments/picture.jpg')

        self.assertEqual(Broadcast.objects.all().count(), 2)  # new broadcast with media

        broadcast = Broadcast.objects.order_by('-id').first()
        self.assertEqual(broadcast.media, dict(base='image/jpeg:attachments/picture.jpg'))
        self.assertEqual(broadcast.get_messages().count(), 1)
        msg = broadcast.get_messages().first()
        self.assertEqual(msg.contact, self.contact2)
        self.assertEqual(msg.text, msg_body)
        self.assertEqual(msg.attachments, ["image/jpeg:https://%s/%s" % (settings.AWS_BUCKET_DOMAIN, 'attachments/picture.jpg')])

        # also send if we have empty message but have an attachment
        action = SendAction(str(uuid4()), dict(base=""), [], [self.contact], [], dict(base='image/jpeg:attachments/picture.jpg'))
        self.execute_action(action, run, None)

        broadcast = Broadcast.objects.order_by('-id').first()
        self.assertEqual(broadcast.text, dict(base=""))
        self.assertEqual(broadcast.media, dict(base='image/jpeg:attachments/picture.jpg'))
        self.assertEqual(broadcast.base_language, 'base')

    def test_variable_contact_parsing(self):
        groups = dict(groups=[dict(id=-1)])
        groups = VariableContactAction.parse_groups(self.org, groups)
        self.assertTrue('Missing', groups[0].name)

    @override_settings(SEND_EMAILS=True)
    def test_email_action(self):
        msg = self.create_msg(direction=INCOMING, contact=self.contact, text="Green is my favorite")
        run = FlowRun.create(self.flow, self.contact)

        action = EmailAction(str(uuid4()), ["steve@apple.com"], "Subject", "Body")

        # check to and from JSON
        action_json = action.as_json()
        action = EmailAction.from_json(self.org, action_json)

        self.execute_action(action, run, msg)
        self.assertOutbox(0, 'no-reply@temba.io', 'Subject', 'Body', ['steve@apple.com'])

        try:
            EmailAction(str(uuid4()), [], "Subject", "Body")
            self.fail("Should have thrown due to empty recipient list")
        except FlowException:
            pass

        # check expression evaluation in action fields
        action = EmailAction(str(uuid4()), ["@contact.name", "xyz", "", '@(SUBSTITUTE(LOWER(contact), " ", "") & "@nyaruka.com")'],
                             "@contact.name added in subject",
                             "@contact.name uses phone @contact.tel")

        action_json = action.as_json()
        action = EmailAction.from_json(self.org, action_json)

        self.execute_action(action, run, msg)
        self.assertOutbox(1, 'no-reply@temba.io', 'Eric added in subject', 'Eric uses phone 0788 382 382', ['eric@nyaruka.com'])

        # check simulator reports invalid addresses
        test_contact = Contact.get_test_contact(self.user)
        test_run = FlowRun.create(self.flow, test_contact)

        self.execute_action(action, test_run, msg)

        logs = list(ActionLog.objects.order_by('pk'))
        self.assertEqual(logs[0].level, ActionLog.LEVEL_INFO)
        self.assertEqual(logs[0].text, "&quot;Test Contact uses phone (206) 555-0100&quot; would be sent to &quot;testcontact@nyaruka.com&quot;")
        self.assertEqual(logs[1].level, ActionLog.LEVEL_WARN)
        self.assertEqual(logs[1].text, 'Some email address appear to be invalid: &quot;Test Contact&quot;, &quot;xyz&quot;, &quot;&quot;')

        # check that all white space is replaced with single spaces in the subject
        test = EmailAction(str(uuid4()), ["steve@apple.com"], "Allo \n allo\tmessage", "Email notification for allo allo")
        self.execute_action(test, run, msg)

        self.assertOutbox(2, 'no-reply@temba.io', 'Allo allo message', 'Email notification for allo allo', ["steve@apple.com"])

        # now try with a custom from address
        branding = copy.deepcopy(settings.BRANDING)
        branding['rapidpro.io']['flow_email'] = 'no-reply@mybrand.com'
        with self.settings(BRANDING=branding):
            self.execute_action(action, run, msg)
            self.assertOutbox(3, 'no-reply@mybrand.com', 'Eric added in subject', 'Eric uses phone 0788 382 382', ['eric@nyaruka.com'])

        # same thing, but with a custom smtp server
        self.org.add_smtp_config('support@example.com', 'smtp.example.com', 'support@example.com', 'secret', '465', 'T', self.admin)
        action = EmailAction(str(uuid4()), ["steve@apple.com"], "Subject", "Body")
        self.execute_action(action, run, msg)
        self.assertOutbox(4, 'support@example.com', 'Subject', 'Body', ["steve@apple.com"])

    def test_save_to_contact_action(self):
        sms = self.create_msg(direction=INCOMING, contact=self.contact, text="batman")
        test = SaveToContactAction.from_json(self.org, dict(type='save', label="Superhero Name", value='@step'))
        run = FlowRun.create(self.flow, self.contact)

        field = ContactField.objects.get(org=self.org, key="superhero_name")
        self.assertEqual("Superhero Name", field.label)

        self.execute_action(test, run, sms)

        # user should now have a nickname field with a value of batman
        contact = Contact.objects.get(id=self.contact.pk)
        self.assertEqual("batman", contact.get_field_raw('superhero_name'))

        # test clearing our value
        test = SaveToContactAction.from_json(self.org, test.as_json())
        test.value = ""
        self.execute_action(test, run, sms)
        contact = Contact.objects.get(id=self.contact.pk)
        self.assertEqual(None, contact.get_field_raw('superhero_name'))

        # test setting our name
        test = SaveToContactAction.from_json(self.org, dict(type='save', label="Name", value='', field='name'))
        test.value = "Eric Newcomer"
        self.execute_action(test, run, sms)
        contact = Contact.objects.get(id=self.contact.pk)
        self.assertEqual("Eric Newcomer", contact.name)
        run.contact = contact

        # test setting just the first name
        test = SaveToContactAction.from_json(self.org, dict(type='save', label="First Name", value='', field='first_name'))
        test.value = "Jen"
        self.execute_action(test, run, sms)
        contact = Contact.objects.get(id=self.contact.pk)
        self.assertEqual("Jen Newcomer", contact.name)

        # throw exception for other reserved words except name, first_name and URN schemes
        for key in Contact.RESERVED_FIELD_KEYS:
            if key not in ['name', 'first_name', 'tel_e164'] + list(URN.VALID_SCHEMES):
                with self.assertRaises(Exception):
                    test = SaveToContactAction.from_json(self.org, dict(type='save', label=key, value='', field=key))
                    test.value = "Jen"
                    self.execute_action(test, run, sms)

        # we should strip whitespace
        run.contact = contact
        test = SaveToContactAction.from_json(self.org, dict(type='save', label="First Name", value='', field='first_name'))
        test.value = " Jackson "
        self.execute_action(test, run, sms)
        contact = Contact.objects.get(id=self.contact.pk)
        self.assertEqual("Jackson Newcomer", contact.name)

        # first name works with a single word
        run.contact = contact
        contact.name = "Percy"
        contact.save()

        test = SaveToContactAction.from_json(self.org, dict(type='save', label="First Name", value='', field='first_name'))
        test.value = " Cole"
        self.execute_action(test, run, sms)
        contact = Contact.objects.get(id=self.contact.pk)
        self.assertEqual("Cole", contact.name)

        # test saving something really long to another field
        test = SaveToContactAction.from_json(self.org, dict(type='save', label="Last Message", value='', field='last_message'))
        test.value = "This is a long message, longer than 160 characters, longer than 250 characters, all the way up "\
                     "to 500 some characters long because sometimes people save entire messages to their contact " \
                     "fields and we want to enable that for them so that they can do what they want with the platform."
        self.execute_action(test, run, sms)
        contact = Contact.objects.get(id=self.contact.pk)
        self.assertEqual(test.value, contact.get_field('last_message').string_value)

        # test saving a contact's phone number
        test = SaveToContactAction.from_json(self.org, dict(type='save', label='Phone Number', field='tel_e164', value='@step'))

        # make sure they have a twitter urn first
        contact.urns.add(ContactURN.create(self.org, None, 'twitter:enewcomer'))
        self.assertIsNotNone(contact.urns.filter(path='enewcomer').first())

        # add another phone number to make sure it doesn't get removed too
        contact.urns.add(ContactURN.create(self.org, None, 'tel:+18005551212'))
        self.assertEqual(3, contact.urns.all().count())

        # create an inbound message on our original phone number
        sms = self.create_msg(direction=INCOMING, contact=self.contact,
                              text="+12065551212", contact_urn=contact.urns.filter(path='+250788382382').first())

        # create another contact with that phone number, to test stealing
        robbed = self.create_contact("Robzor", "+12065551212")

        self.execute_action(test, run, sms)

        # updating Phone Number should not create a contact field
        self.assertIsNone(ContactField.objects.filter(org=self.org, key='tel_e164').first())

        # instead it should update the tel urn for our contact
        contact = Contact.objects.get(id=self.contact.pk)
        self.assertEqual(4, contact.urns.all().count())
        self.assertIsNotNone(contact.urns.filter(path='+12065551212').first())

        # we should still have our twitter scheme
        self.assertIsNotNone(contact.urns.filter(path='enewcomer').first())

        # and our other phone number
        self.assertIsNotNone(contact.urns.filter(path='+18005551212').first())

        # and our original number too
        self.assertIsNotNone(contact.urns.filter(path='+250788382382').first())

        # robzor shouldn't have a number anymore
        self.assertFalse(robbed.urns.all())

        # try the same with a simulator contact
        test_contact = Contact.get_test_contact(self.admin)
        test_contact_urn = test_contact.urns.all().first()
        run = FlowRun.create(self.flow, test_contact)
        self.execute_action(test, run, sms)

        ActionLog.objects.all().delete()
        action = SaveToContactAction.from_json(self.org, dict(type='save', label="mailto", value='foo@bar.com'))
        self.execute_action(action, run, None)
        self.assertEqual(ActionLog.objects.get().text, "Added foo@bar.com as @contact.mailto - skipped in simulator")

        # Invalid email
        ActionLog.objects.all().delete()
        action = SaveToContactAction.from_json(self.org, dict(type='save', label="mailto", value='foobar.com'))
        self.execute_action(action, run, None)
        self.assertEqual(ActionLog.objects.get().text, "Contact not updated, invalid connection for contact (mailto:foobar.com)")

        # URN should be unchanged on the simulator contact
        test_contact = Contact.objects.get(id=test_contact.id)
        self.assertEqual(test_contact_urn, test_contact.urns.all().first())

        self.assertFalse(ContactField.objects.filter(org=self.org, label='Ecole'))
        SaveToContactAction.from_json(self.org, dict(type='save', label="[_NEW_]Ecole", value='@step'))
        field = ContactField.objects.get(org=self.org, key="ecole")
        self.assertEqual("Ecole", field.label)

        # try saving some empty data into mailto
        ActionLog.objects.all().delete()
        action = SaveToContactAction.from_json(self.org, dict(type='save', label="mailto", value='@contact.mailto'))
        self.execute_action(action, run, None)
        self.assertEqual(ActionLog.objects.get().text, "Contact not updated, missing connection for contact")

    def test_set_language_action(self):
        action = SetLanguageAction(str(uuid4()), 'kli', 'Klingon')

        # check to and from JSON
        action_json = action.as_json()
        action = SetLanguageAction.from_json(self.org, action_json)

        self.assertEqual('kli', action.lang)
        self.assertEqual('Klingon', action.name)

        # execute our action and check we are Klingon now, eeektorp shnockahltip.
        run = FlowRun.create(self.flow, self.contact)
        self.execute_action(action, run, None)
        self.assertEqual('kli', Contact.objects.get(pk=self.contact.pk).language)

        # try setting the language to something thats not three characters
        action_json['lang'] = 'base'
        action_json['name'] = 'Default'
        action = SetLanguageAction.from_json(self.org, action_json)
        self.execute_action(action, run, None)

        # should clear the contacts language
        self.assertIsNone(Contact.objects.get(pk=self.contact.pk).language)

    def test_start_flow_action(self):
        self.flow.name = 'Parent'
        self.flow.save()

        self.flow.start([], [self.contact])

        sms = Msg.create_incoming(self.channel, "tel:+250788382382", "Blue is my favorite")

        run = FlowRun.objects.get()

        new_flow = Flow.create_single_message(self.org, self.user,
                                              {'base': "You chose @parent.color.category"}, base_language='base')
        action = StartFlowAction(str(uuid4()), new_flow)

        action_json = action.as_json()
        action = StartFlowAction.from_json(self.org, action_json)

        self.execute_action(action, run, sms, started_flows=[])

        # our contact should now be in the flow
        self.assertTrue(FlowRun.objects.filter(flow=new_flow, contact=self.contact))
        self.assertTrue(Msg.objects.filter(contact=self.contact, direction='O', text='You chose Blue'))

    def test_group_actions(self):
        msg = self.create_msg(direction=INCOMING, contact=self.contact, text="Green is my favorite")
        run = FlowRun.create(self.flow, self.contact)

        test_contact = Contact.get_test_contact(self.admin)
        test_msg = self.create_msg(direction=INCOMING, contact=self.contact, text="Blue")
        test_run = FlowRun.create(self.flow, test_contact)

        group = self.create_group("Flow Group", [])

        # check converting to and from json
        action = AddToGroupAction(str(uuid4()), [group, "@step.contact"])
        action_json = action.as_json()
        action = AddToGroupAction.from_json(self.org, action_json)

        self.execute_action(action, run, msg)

        # user should now be in the group
        self.assertEqual(set(group.contacts.all()), {self.contact})

        # we should never create a new group in the flow execution
        self.assertIsNone(ContactGroup.user_groups.filter(name=self.contact.name).first())

        # should match existing group for variables
        replace_group1 = ContactGroup.create_static(self.org, self.admin, self.contact.name)
        self.assertEqual(set(replace_group1.contacts.all()), set())

        # passing through twice doesn't change anything
        self.execute_action(action, run, msg)

        self.assertEqual(set(group.contacts.all()), {self.contact})
        self.assertEqual(self.contact.user_groups.all().count(), 2)

        # having the group name containing a space doesn't change anything
        self.contact.name += " "
        self.contact.save()
        run.contact = self.contact

        self.execute_action(action, run, msg)

        self.assertEqual(set(group.contacts.all()), {self.contact})
        self.assertEqual(set(replace_group1.contacts.all()), {self.contact})

        replace_group2 = ContactGroup.create_static(self.org, self.admin, test_contact.name)

        # with test contact, action logs are also created
        self.execute_action(action, test_run, test_msg)

        self.assertEqual(set(group.contacts.all()), {self.contact, test_contact})
        self.assertEqual(set(replace_group1.contacts.all()), {self.contact})
        self.assertEqual(set(replace_group2.contacts.all()), {test_contact})
        self.assertEqual(ActionLog.objects.filter(level='I').count(), 2)

        # now try remove action
        action = DeleteFromGroupAction(str(uuid4()), [group, "@step.contact"])
        action_json = action.as_json()
        action = DeleteFromGroupAction.from_json(self.org, action_json)

        self.execute_action(action, run, msg)

        # contact should be removed now
        self.assertEqual(set(group.contacts.all()), {test_contact})
        self.assertEqual(set(replace_group1.contacts.all()), set())

        # no change if we run again
        self.execute_action(action, run, msg)

        self.assertEqual(set(group.contacts.all()), {test_contact})
        self.assertEqual(set(replace_group1.contacts.all()), set())

        # with test contact, action logs are also created
        self.execute_action(action, test_run, test_msg)

        self.assertEqual(set(group.contacts.all()), set())
        self.assertEqual(set(replace_group2.contacts.all()), set())
        self.assertEqual(ActionLog.objects.filter(level='I').count(), 4)

        # try when group is inactive
        action = DeleteFromGroupAction(str(uuid4()), [group])
        group.is_active = False
        group.save()
        self.org.clear_cached_groups()

        self.assertIn(group, action.groups)

        # reading the action should create a new group
        updated_action = DeleteFromGroupAction.from_json(self.org, action.as_json())
        self.assertTrue(updated_action.groups)
        self.assertFalse(group.pk in [g.pk for g in updated_action.groups])

        # try adding a contact to a dynamic group
        self.create_field('isalive', "Is Alive")
        dynamic_group = self.create_group("Dynamic", query="isalive=YES")
        action = AddToGroupAction(str(uuid4()), [dynamic_group])

        self.execute_action(action, run, msg)

        # should do nothing
        self.assertEqual(dynamic_group.contacts.count(), 0)

        # tho if contact is a test contact, log as error
        self.execute_action(action, test_run, test_msg)

        self.assertEqual(dynamic_group.contacts.count(), 0)

        self.assertEqual(ActionLog.objects.filter(level='E').count(), 2)

        group1 = self.create_group("Flow Group 1", [])
        group2 = self.create_group("Flow Group 2", [])

        test = AddToGroupAction(str(uuid4()), [group1])
        action_json = test.as_json()
        test = AddToGroupAction.from_json(self.org, action_json)

        self.execute_action(test, run, test_msg)

        test = AddToGroupAction(str(uuid4()), [group2])
        action_json = test.as_json()
        test = AddToGroupAction.from_json(self.org, action_json)

        self.execute_action(test, run, test_msg)

        # user should be in both groups now
        self.assertTrue(group1.contacts.filter(id=self.contact.pk))
        self.assertEqual(1, group1.contacts.all().count())
        self.assertTrue(group2.contacts.filter(id=self.contact.pk))
        self.assertEqual(1, group2.contacts.all().count())

        test = DeleteFromGroupAction(str(uuid4()), [])
        action_json = test.as_json()
        test = DeleteFromGroupAction.from_json(self.org, action_json)

        self.execute_action(test, run, test_msg)

        # user should be gone from both groups now
        self.assertFalse(group1.contacts.filter(id=self.contact.pk))
        self.assertEqual(0, group1.contacts.all().count())
        self.assertFalse(group2.contacts.filter(id=self.contact.pk))
        self.assertEqual(0, group2.contacts.all().count())

    def test_set_channel_action(self):
        flow = self.flow
        run = FlowRun.create(flow, self.contact)

        tel1_channel = Channel.add_config_external_channel(self.org, self.admin, 'US', '+12061111111', 'KN', {})
        tel2_channel = Channel.add_config_external_channel(self.org, self.admin, 'US', '+12062222222', 'KN', {})

        fb_channel = Channel.create(self.org, self.user, None, 'FB', address="Page Id",
                                    config={'page_name': "Page Name", 'auth_token': "Page Token"})

        # create an incoming message on tel1, this should create an affinity to that channel
        Msg.create_incoming(tel1_channel, str(self.contact.urns.all().first()), "Incoming msg")
        urn = self.contact.urns.all().first()
        self.assertEqual(urn.channel, tel1_channel)

        action = SetChannelAction(str(uuid4()), tel2_channel)
        self.execute_action(action, run, None)

        # check the affinity on our urn again, should now be the second channel
        urn.refresh_from_db()
        self.assertEqual(urn.channel, tel2_channel)

        # try to set it to a channel that we don't have a URN for
        action = SetChannelAction(str(uuid4()), fb_channel)
        self.execute_action(action, run, None)

        # affinity is unchanged
        urn.refresh_from_db()
        self.assertEqual(urn.channel, tel2_channel)

        # add a FB urn for our contact
        fb_urn = ContactURN.get_or_create(self.org, self.contact, 'facebook:1001')

        # default URN should be FB now, as it has the highest priority
        contact, resolved_urn = Msg.resolve_recipient(self.org, self.admin, self.contact, None)
        self.assertEqual(resolved_urn, fb_urn)

        # but if we set our channel to tel, will override that
        run.contact.clear_urn_cache()
        action = SetChannelAction(str(uuid4()), tel1_channel)
        self.execute_action(action, run, None)

        contact.clear_urn_cache()
        contact, resolved_urn = Msg.resolve_recipient(self.org, self.admin, self.contact, None)
        self.assertEqual(resolved_urn, urn)
        self.assertEqual(resolved_urn.channel, tel1_channel)

        # test serializing
        action_json = action.as_json()
        action = SetChannelAction.from_json(self.org, action_json)
        self.assertEqual(tel1_channel, action.channel)

        # action shouldn't blow up without a channel
        action = SetChannelAction(str(uuid4()), None)
        self.execute_action(action, run, None)

        # incoming messages will still cause preference to switch
        Msg.create_incoming(tel2_channel, str(urn), "Incoming msg")
        urn.refresh_from_db()
        self.assertEqual(urn.channel, tel2_channel)

        # make sure that switch will work across schemes as well
        Msg.create_incoming(fb_channel, str(fb_urn), "Incoming FB message")
        self.contact.clear_urn_cache()
        contact, resolved_urn = Msg.resolve_recipient(self.org, self.admin, self.contact, None)
        self.assertEqual(resolved_urn, fb_urn)

    def test_add_label_action(self):
        flow = self.flow
        msg = self.create_msg(direction=INCOMING, contact=self.contact, text="Green is my favorite")
        run = FlowRun.create(flow, self.contact)

        label1 = Label.get_or_create(self.org, self.user, "green label")
        action = AddLabelAction(str(uuid4()), [label1, "@step.contact"])

        action_json = action.as_json()
        action = AddLabelAction.from_json(self.org, action_json)

        # no message yet; such Add Label action on entry Actionset. No error should be raised
        self.execute_action(action, run, None)

        self.assertFalse(label1.get_messages())
        self.assertEqual(label1.get_visible_count(), 0)

        self.execute_action(action, run, msg)

        # only label one was added to the message and no new label created
        self.assertEqual(set(label1.get_messages()), {msg})
        self.assertEqual(label1.get_visible_count(), 1)
        self.assertEqual(Label.label_objects.all().count(), 1)

        # make sure the expression variable label exists too
        label1 = Label.label_objects.get(pk=label1.pk)
        label2 = Label.label_objects.create(org=self.org, name=self.contact.name, created_by=self.admin,
                                            modified_by=self.admin)

        self.execute_action(action, run, msg)

        # and message should have been labeled with both labels
        msg = Msg.objects.get(pk=msg.pk)
        self.assertEqual(set(msg.labels.all()), {label1, label2})
        self.assertEqual(set(label1.get_messages()), {msg})
        self.assertEqual(label1.get_visible_count(), 1)
        self.assertTrue(set(label2.get_messages()), {msg})
        self.assertEqual(label2.get_visible_count(), 1)

        # passing through twice doesn't change anything
        self.execute_action(action, run, msg)

        self.assertEqual(set(Msg.objects.get(pk=msg.pk).labels.all()), {label1, label2})
        self.assertEqual(Label.label_objects.get(pk=label1.pk).get_visible_count(), 1)
        self.assertEqual(Label.label_objects.get(pk=label2.pk).get_visible_count(), 1)

    @override_settings(SEND_WEBHOOKS=True)
    def test_webhook_action_simulator(self):
        # check simulator warns of webhook URL errors
        action = WebhookAction(str(uuid4()), 'http://localhost:49999/token?xyz=@contact.xyz')
        test_contact = Contact.get_test_contact(self.user)
        test_run = FlowRun.create(self.flow, test_contact)

        self.mockRequest('POST', '/token?xyz=@contact.xyz', '{"coupon":"NEXUS4"}', content_type='application_json')
        self.execute_action(action, test_run, None)

        event = WebHookEvent.objects.order_by('-pk').first()

        logs = list(ActionLog.objects.order_by('pk'))
        self.assertEqual(logs[0].level, ActionLog.LEVEL_WARN)
        self.assertEqual(logs[0].text, "URL appears to contain errors: Undefined variable: contact.xyz")
        self.assertEqual(logs[1].level, ActionLog.LEVEL_INFO)
        self.assertEqual(logs[1].text, "Triggered <a href='/webhooks/log/%d/' target='_log'>webhook event</a> - 200" % event.pk)

        # check all our mocked requests were made
        self.assertAllRequestsMade()


class FlowRunTest(TembaTest):

    def setUp(self):
        super(FlowRunTest, self).setUp()

        self.flow = self.get_flow('color')
        self.contact = self.create_contact("Ben Haggerty", "+250788123123")

    def test_field_normalization(self):
        fields = dict(field1="value1", field2="value2")
        (normalized, count) = FlowRun.normalize_fields(fields)
        self.assertEqual(normalized, fields)

        # spaces in field keys
        fields = {'value 1': 'value1', 'value-2': 'value2'}
        (normalized, count) = FlowRun.normalize_fields(fields)
        self.assertEqual(normalized, dict(value_1='value1', value_2='value2'))

        # nulls should become empty strings
        (normalized, count) = FlowRun.normalize_fields({'value1': None})
        self.assertEqual(normalized, {'value1': ""})

        # numerics and booleans should stay as is
        (normalized, count) = FlowRun.normalize_fields({'value1': 12, 'value2': 123.45})
        self.assertEqual(normalized, {'value1': 12, 'value2': 123.45})

        (normalized, count) = FlowRun.normalize_fields({'value1': True, 'value2': False})
        self.assertEqual(normalized, {'value1': True, 'value2': False})

        # anything else blows up
        with self.assertRaises(ValueError):
            FlowRun.normalize_fields({'value1': lambda: "x"})

        # field text too long
        fields['field2'] = "*" * 650
        (normalized, count) = FlowRun.normalize_fields(fields)
        self.assertEqual(len(normalized['field2']), 640)

        # field name too long
        fields['field' + ("*" * 350)] = "short value"
        (normalized, count) = FlowRun.normalize_fields(fields)
        self.assertTrue('field' + ("_" * 250) in normalized)

        # too many fields
        for i in range(259):
            fields['field%d' % i] = 'value %d' % i
        (normalized, count) = FlowRun.normalize_fields(fields)
        self.assertEqual(count, 256)
        self.assertEqual(len(normalized), 256)

        # can manually keep more values
        (normalized, count) = FlowRun.normalize_fields(fields, 500)
        self.assertEqual(count, 262)
        self.assertEqual(len(normalized), 262)

        fields = dict(numbers=["zero", "one", "two", "three"])
        (normalized, count) = FlowRun.normalize_fields(fields)
        self.assertEqual(count, 5)
        self.assertEqual(normalized, dict(numbers={'0': "zero", '1': "one", '2': "two", '3': "three"}))

        fields = dict(united_states=dict(wa="Washington", nv="Nevada"), states=50)
        (normalized, count) = FlowRun.normalize_fields(fields)
        self.assertEqual(count, 4)
        self.assertEqual(normalized, fields)

    def test_update_fields(self):
        run = FlowRun.create(self.flow, self.contact)

        # set our fields from an empty state
        new_values = dict(Field1="value1", field_2="value2")
        run.update_fields(new_values)

        self.assertEqual(run.fields, new_values)

        run.update_fields(dict(field2="new value2", field3="value3"))
        new_values['field2'] = "new value2"
        new_values['field3'] = "value3"

        self.assertEqual(run.fields, new_values)

        run.update_fields(dict(field1=""))
        new_values['field1'] = ""

        self.assertEqual(run.fields, new_values)

        # clear our fields
        run.fields = None
        run.save(update_fields=('fields',))

        # set to a list instead
        run.update_fields(["zero", "one", "two"])
        self.assertEqual(run.fields, {"0": "zero", "1": "one", "2": "two"})

    @also_in_flowserver
    def test_is_completed(self):
        self.flow.start([], [self.contact])

        self.assertFalse(FlowRun.objects.get(contact=self.contact).is_completed())

        incoming = self.create_msg(direction=INCOMING, contact=self.contact, text="orange")
        Flow.find_and_handle(incoming)

        self.assertTrue(FlowRun.objects.get(contact=self.contact).is_completed())

    def test_is_interrupted(self):
        self.channel.delete()
        # Create a USSD channel type to test USSDSession.INTERRUPTED status
        self.channel = Channel.create(self.org, self.user, 'RW', 'JNU', None, '+250788123123',
                                      role=Channel.ROLE_USSD)

        flow = self.get_flow('ussd_example')
        flow.start([], [self.contact])

        self.assertFalse(FlowRun.objects.get(contact=self.contact).is_interrupted())

        USSDSession.handle_incoming(channel=self.channel, urn=self.contact.get_urn().path, date=timezone.now(),
                                    external_id="12341231", status=USSDSession.INTERRUPTED)

        self.assertTrue(FlowRun.objects.get(contact=self.contact).is_interrupted())


class FlowLabelTest(FlowFileTest):

    def test_label_model(self):
        # test a the creation of a unique label when we have a long word(more than 32 caracters)
        response = FlowLabel.create_unique("alongwordcomposedofmorethanthirtytwoletters",
                                           self.org,
                                           parent=None)
        self.assertEqual(response.name, "alongwordcomposedofmorethanthirt")

        # try to create another label which starts with the same 32 caracteres
        # the one we already have
        label = FlowLabel.create_unique("alongwordcomposedofmorethanthirtytwocaracteres",
                                        self.org, parent=None)

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
        favorites = self.get_flow('favorites')
        label.toggle_label([favorites], True)
        response = self.client.get(reverse('flows.flow_filter', args=[label.pk]))
        self.assertTrue(response.context['object_list'])
        # our child label
        self.assertContains(response, "child")

        # and the edit gear link
        self.assertContains(response, "Edit")

        favorites.is_active = False
        favorites.save()

        response = self.client.get(reverse('flows.flow_filter', args=[label.pk]))
        self.assertFalse(response.context['object_list'])

    def test_toggle_label(self):
        label = FlowLabel.create_unique('toggle me', self.org)
        flow = self.get_flow('favorites')

        changed = label.toggle_label([flow], True)
        self.assertEqual(1, len(changed))
        self.assertEqual(label.pk, flow.labels.all().first().pk)

        changed = label.toggle_label([flow], False)
        self.assertEqual(1, len(changed))
        self.assertIsNone(flow.labels.all().first())

    def test_create(self):
        create_url = reverse('flows.flowlabel_create')

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
        self.assertIn('form', response.context)
        self.assertTrue(response.context['form'].errors)
        self.assertEqual('Name already used', response.context['form'].errors['name'][0])

        self.assertEqual(FlowLabel.objects.all().count(), 2)
        self.assertEqual(FlowLabel.objects.filter(parent=None).count(), 1)

        post_data = dict(name="label from modal")
        response = self.client.post("%s?format=modal" % create_url, post_data, follow=True)
        self.assertEqual(FlowLabel.objects.all().count(), 3)

    def test_delete(self):
        label_one = FlowLabel.create_unique("label1", self.org)

        delete_url = reverse('flows.flowlabel_delete', args=[label_one.pk])

        self.other_user = self.create_user("ironman")

        self.login(self.other_user)
        response = self.client.get(delete_url)
        self.assertEqual(response.status_code, 302)

        self.login(self.admin)
        response = self.client.get(delete_url)
        self.assertEqual(response.status_code, 200)

    def test_update(self):
        label_one = FlowLabel.create_unique("label1", self.org)
        update_url = reverse('flows.flowlabel_update', args=[label_one.pk])

        # not logged in, no dice
        response = self.client.get(update_url)
        self.assertLoginRedirect(response)

        # login
        self.login(self.admin)
        response = self.client.get(update_url)

        # change our name
        data = response.context['form'].initial
        data['name'] = "Label One"
        data['parent'] = ''
        self.client.post(update_url, data)

        label_one.refresh_from_db()
        self.assertEqual(label_one.name, "Label One")


class WebhookTest(TembaTest):

    @override_settings(SEND_WEBHOOKS=True)
    def test_webhook_subflow_extra(self):
        # import out flow that triggers another flow
        contact1 = self.create_contact("Marshawn", "+14255551212")
        substitutions = dict(contact_id=contact1.id)
        flow = self.get_flow('triggered', substitutions)

        self.mockRequest('GET', '/where', '{ "text": "(I came from a webhook)" }')
        flow.start(groups=[], contacts=[contact1], restart_participants=True)

        # first message from our trigger flow action
        msg = Msg.objects.all().order_by('-created_on')[0]
        self.assertEqual('Honey, I triggered the flow! (I came from a webhook)', msg.text)

        # second message from our start flow action
        msg = Msg.objects.all().order_by('-created_on')[1]
        self.assertEqual('Honey, I triggered the flow! (I came from a webhook)', msg.text)

        # check all our mocked requests were made
        self.assertAllRequestsMade()

    @override_settings(SEND_WEBHOOKS=True)
    def test_webhook(self):
        flow = self.get_flow('webhook')
        contact = self.create_contact("Ben Haggerty", '+250788383383')

        self.mockRequest('GET', '/check_order.php?phone=%2B250788383383', '{ "text": "Get", "blank": "" }')

        run1, = flow.start([], [contact])
        run1.refresh_from_db()
        self.assertEqual(run1.fields, {'text': "Get", 'blank': ""})
        self.assertEqual(run1.results, {
            'order_status': {
                'category': 'Other',
                'node_uuid': matchers.UUID4String(),
                'name': 'Order Status',
                'value': 'Get ',
                'created_on': matchers.ISODate(),
                'input': ''
            },
            'response_1': {
                'category': 'Success',
                'node_uuid': matchers.UUID4String(),
                'name': 'Response 1',
                'value': '{ "text": "Get", "blank": "" }',
                'created_on': matchers.ISODate(),
                'input': ''
            }
        })

        # change our webhook to a POST
        webhook = RuleSet.objects.get(flow=flow, label="Response 1")
        config = webhook.config
        config[RuleSet.CONFIG_WEBHOOK_ACTION] = 'POST'
        webhook.config = config
        webhook.save()

        self.mockRequest('POST', '/check_order.php?phone=%2B250788383383', '{ "text": "Post", "blank": "" }')

        run2, = flow.start([], [contact], restart_participants=True)
        run2.refresh_from_db()

        self.assertEqual(run2.results, {
            'order_status': {
                'category': 'Other',
                'node_uuid': matchers.UUID4String(),
                'name': 'Order Status',
                'value': 'Post ',
                'created_on': matchers.ISODate(),
                'input': ''
            },
            'response_1': {
                'category': 'Success',
                'node_uuid': matchers.UUID4String(),
                'name': 'Response 1',
                'value': '{ "text": "Post", "blank": "" }',
                'created_on': matchers.ISODate(),
                'input': ''
            }
        })

        # check parsing of a JSON array response from a webhook
        self.mockRequest('POST', '/check_order.php?phone=%2B250788383383', '["zero", "one", "two"]')

        run3, = flow.start([], [contact], restart_participants=True)
        run3.refresh_from_db()
        self.assertEqual(run3.fields, {'0': 'zero', '1': 'one', '2': 'two'})

        # which is also how it will appear in the expressions context
        message_context = flow.build_expressions_context(contact, None)
        self.assertEqual(message_context['extra'], {'0': 'zero', '1': 'one', '2': 'two'})

        # check that we limit JSON responses to 256 values
        self.mockRequest('POST', '/check_order.php?phone=%2B250788383383', json.dumps(['x'] * 300))

        run4, = flow.start([], [contact], restart_participants=True)
        run4.refresh_from_db()
        self.assertEqual(run4.fields, {str(n): 'x' for n in range(256)})

        # check we handle a non-dict or list response
        self.mockRequest('POST', '/check_order.php?phone=%2B250788383383', "12345")

        run5, = flow.start([], [contact], restart_participants=True)
        run5.refresh_from_db()
        self.assertEqual(run5.fields, {})

        # check we handle a non-JSON response
        self.mockRequest('POST', '/check_order.php?phone=%2B250788383383', "asdfasdfasdf")

        run6, = flow.start([], [contact], restart_participants=True)
        run6.refresh_from_db()
        self.assertEqual(run6.fields, {})

        results = run6.results
        self.assertEqual(len(results), 2)
        self.assertEqual(results['response_1']['name'], 'Response 1')
        self.assertEqual(results['response_1']['value'], 'asdfasdfasdf')
        self.assertEqual(results['response_1']['category'], 'Success')

        # check a webhook that responds with a 500 error
        self.mockRequest('POST', '/check_order.php?phone=%2B250788383383', "Server Error", status=500)

        run7, = flow.start([], [contact], restart_participants=True)
        run7.refresh_from_db()

        self.assertEqual(run7.fields, {})
        self.assertEqual(run7.results, {
            'response_1': {
                'category': 'Failure',
                'node_uuid': matchers.UUID4String(),
                'name': 'Response 1',
                'value': 'Server Error',
                'created_on': matchers.ISODate(),
                'input': ''
            }
        })

        # check a webhook that responds with a 400 error
        self.mockRequest('POST', '/check_order.php?phone=%2B250788383383', '{ "text": "Valid", "error": "400", "message": "Missing field in request" }', status=400)

        run8, = flow.start([], [contact], restart_participants=True)
        run8.refresh_from_db()
        self.assertEqual(run8.fields, {'text': "Valid", 'error': "400", 'message': "Missing field in request"})

        results = run8.results
        self.assertEqual(len(results), 1)
        self.assertEqual(results['response_1']['name'], 'Response 1')
        self.assertEqual(results['response_1']['value'], '{ "text": "Valid", "error": "400", "message": "Missing field in request" }')
        self.assertEqual(results['response_1']['category'], 'Failure')

        # check all our mocked requests were made
        self.assertAllRequestsMade()

    @override_settings(SEND_WEBHOOKS=True)
    def test_resthook(self):
        self.contact = self.create_contact("Macklemore", "+12067799294")
        webhook_flow = self.get_flow('resthooks')

        # we don't have the resthook registered yet, so this won't trigger any calls
        webhook_flow.start([], [self.contact])

        # should have two messages of failures
        msgs = list(self.contact.msgs.order_by('id'))
        self.assertEqual(msgs[0].text, "That was a success.")
        self.assertEqual(msgs[1].text, "The second succeeded.")

        # but we should have created a webhook event regardless
        self.assertTrue(WebHookEvent.objects.filter(resthook__slug='new-registration'))

        # ok, let's go add a listener for that event (should have been created automatically)
        resthook = Resthook.objects.get(org=self.org, slug='new-registration')
        resthook.subscribers.create(target_url='http://localhost:49999/foo', created_by=self.admin, modified_by=self.admin)
        resthook.subscribers.create(target_url='http://localhost:49999/bar', created_by=self.admin, modified_by=self.admin)

        # clear out our messages
        Msg.objects.filter(contact=self.contact).delete()

        self.mockRequest('POST', '/foo', '{ "code": "ABABUUDDLRS" }')
        self.mockRequest('POST', '/bar', "Failure", status=400)
        self.mockRequest('POST', '/foo', "Unsubscribe", status=410)
        self.mockRequest('POST', '/bar', "Failure", status=400)

        # start over, have our first webhook fail, check that routing still works with failure
        webhook_flow.start([], [self.contact], restart_participants=True)

        msgs = list(self.contact.msgs.order_by('id'))

        # first should be a success because we had at least one success
        self.assertEqual(msgs[0].text, "That was a success.")

        # second, both failed so should be a failure
        self.assertEqual(msgs[1].text, "The second failed.")

        # we should also have unsubscribed from one of our endpoints
        self.assertTrue(resthook.subscribers.filter(is_active=False, target_url='http://localhost:49999/foo'))
        self.assertTrue(resthook.subscribers.filter(is_active=True, target_url='http://localhost:49999/bar'))

        # check all our mocked requests were made
        self.assertAllRequestsMade()


class SimulationTest(FlowFileTest):

    @override_settings(FLOW_SERVER_AUTH_TOKEN='1234', FLOW_SERVER_FORCE=True)
    def test_simulation(self):
        flow = self.get_flow('favorites')

        client = get_client()

        payload = client.request_builder(int(time.time() * 1000000)).add_contact_changed(self.contact).request

        # add a manual trigger
        payload['trigger'] = {
            'type': 'manual',
            'flow': {'uuid': str(flow.uuid), 'name': flow.name},
            'triggered_on': timezone.now().isoformat()
        }

        simulate_url = reverse('flows.flow_simulate', args=[flow.pk])
        self.login(self.admin)
        response = self.client.post(simulate_url, json.dumps(payload), content_type="application/json")

        # create a new payload based on the session we get back
        payload = client.request_builder(int(time.time() * 1000000)).add_contact_changed(self.contact).request
        payload['session'] = response.json()['session']
        self.add_message(payload, 'blue')

        response = self.client.post(simulate_url, json.dumps(payload), content_type="application/json").json()
        replies = self.get_replies(response)
        self.assertEqual(1, len(replies))
        self.assertEqual('Good choice, I like Blue too! What is your favorite beer?', replies[0])

    def test_simulation_legacy(self):
        flow = self.get_flow('pick_a_number')

        # remove our channels
        self.org.channels.all().delete()

        simulate_url = reverse('flows.flow_simulate', args=[flow.pk])
        self.admin.first_name = "Ben"
        self.admin.last_name = "Haggerty"
        self.admin.save()

        post_data = dict(has_refresh=True, version="1")
        self.login(self.admin)
        response = self.client.post(simulate_url, json.dumps(post_data), content_type="application/json")
        json_dict = response.json()

        self.assertEqual(len(json_dict.keys()), 6)
        self.assertEqual(len(json_dict['messages']), 2)
        self.assertEqual('Ben Haggerty has entered the &quot;Pick a Number&quot; flow', json_dict['messages'][0]['text'])
        self.assertEqual("Pick a number between 1-10.", json_dict['messages'][1]['text'])

        post_data['new_message'] = "3"
        post_data['has_refresh'] = False

        response = self.client.post(simulate_url, json.dumps(post_data), content_type="application/json")
        self.assertEqual(200, response.status_code)
        json_dict = response.json()

        self.assertEqual(len(json_dict['messages']), 6)
        self.assertEqual("3", json_dict['messages'][2]['text'])
        self.assertEqual("Saved &#39;3&#39; as @flow.number", json_dict['messages'][3]['text'])
        self.assertEqual("You picked 3!", json_dict['messages'][4]['text'])
        self.assertEqual('Ben Haggerty has exited this flow', json_dict['messages'][5]['text'])

    @patch('temba.ussd.models.USSDSession.handle_incoming')
    def test_ussd_simulation(self, handle_incoming):
        self.channel.delete()
        self.channel = Channel.create(self.org, self.user, 'RW', 'JNU', None, '+250788123123',
                                      role=Channel.ROLE_USSD + Channel.DEFAULT_ROLE)
        flow = self.get_flow('ussd_example')

        simulate_url = reverse('flows.flow_simulate', args=[flow.pk])

        post_data = dict(has_refresh=True, new_message="derp", version="1")

        self.login(self.admin)
        response = self.client.post(simulate_url, json.dumps(post_data), content_type="application/json")

        self.assertEqual(response.status_code, 200)

        # session should have started now
        self.assertTrue(handle_incoming.called)
        self.assertEqual(handle_incoming.call_count, 1)

        self.assertIsNone(handle_incoming.call_args[1]['status'])

        self.channel.delete()
        response = self.client.post(simulate_url, json.dumps(post_data), content_type="application/json")
        self.assertEqual(response.status_code, 200)

    @patch('temba.ussd.models.USSDSession.handle_incoming')
    def test_ussd_simulation_interrupt(self, handle_incoming):
        self.channel.delete()
        self.channel = Channel.create(self.org, self.user, 'RW', 'JNU', None, '+250788123123',
                                      role=Channel.ROLE_USSD + Channel.DEFAULT_ROLE)
        flow = self.get_flow('ussd_example')

        simulate_url = reverse('flows.flow_simulate', args=[flow.pk])

        post_data = dict(has_refresh=True, new_message='__interrupt__', version='1')

        self.login(self.admin)
        response = self.client.post(simulate_url, json.dumps(post_data), content_type="application/json")

        self.assertEqual(response.status_code, 200)

        # session should have started now
        self.assertTrue(handle_incoming.called)
        self.assertEqual(handle_incoming.call_count, 1)

        self.assertEqual(handle_incoming.call_args[1]['status'], USSDSession.INTERRUPTED)

    def test_ussd_simulation_connection_end(self):
        self.ussd_channel = Channel.create(
            self.org, self.user, 'RW', 'JNU', None, '*123#',
            schemes=['tel'], uuid='00000000-0000-0000-0000-000000002222',
            role=Channel.ROLE_USSD)

        flow = self.get_flow('ussd_session_end')

        simulate_url = reverse('flows.flow_simulate', args=[flow.pk])

        post_data = dict(has_refresh=True, new_message='4', version='1')

        self.login(self.admin)
        response = self.client.post(simulate_url, json.dumps(post_data), content_type="application/json")

        self.assertEqual(response.status_code, 200)

        connection = USSDSession.objects.get()
        self.assertEqual(connection.status, USSDSession.COMPLETED)

    def test_ussd_simulation_without_channel_doesnt_run(self):
        Channel.objects.all().delete()

        flow = self.get_flow('ussd_session_end')

        simulate_url = reverse('flows.flow_simulate', args=[flow.pk])

        post_data = dict(has_refresh=True, new_message='4', version='1')

        self.login(self.admin)
        response = self.client.post(simulate_url, json.dumps(post_data), content_type="application/json")
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()['status'], 'error')

        self.assertEqual(flow.runs.count(), 0)


class FlowsTest(FlowFileTest):

    @also_in_flowserver
    def test_simple(self):
        favorites = self.get_flow('favorites')
        action_set1 = favorites.action_sets.order_by('y').first()
        rule_set1 = favorites.rule_sets.order_by('y').first()

        run, = favorites.start([], [self.contact])

        msg1 = Msg.objects.get()
        self.assertEqual(msg1.direction, 'O')
        self.assertEqual(msg1.text, "What is your favorite color?")
        self.assertEqual(msg1.contact, self.contact)

        self.assertEqual(run.contact, self.contact)
        self.assertIsNone(run.exit_type)
        self.assertIsNone(run.exited_on)
        self.assertFalse(run.responded)

        self.assertEqual(FlowNodeCount.get_totals(favorites), {rule_set1.uuid: 1})
        self.assertEqual(FlowPathCount.get_totals(favorites), {action_set1.exit_uuid + ':' + rule_set1.uuid: 1})
        self.assertEqual(FlowCategoryCount.objects.count(), 0)

        recent = FlowPathRecentRun.get_recent([action_set1.exit_uuid], rule_set1.uuid)
        self.assertEqual(len(recent), 1)
        self.assertEqual(recent[0]['run'], run)

        # TODO currently not working in new engine
        # self.assertEqual(recent[0]['text'], "What is your favorite color?")

        msg2 = Msg.create_incoming(self.channel, 'tel:+12065552020', "I like red",
                                   attachments=['image/jpeg:http://example.com/test.jpg'])

        run.refresh_from_db()
        self.assertIsNone(run.exit_type)
        self.assertIsNone(run.exited_on)
        self.assertTrue(run.responded)
        self.assertEqual(run.results, {
            'color': {
                'category': "Red",
                'node_uuid': str(rule_set1.uuid),
                'name': "Color",
                'value': "red",
                'created_on': matchers.ISODate(),
                'input': "I like red"
            }
        })

        cat_counts = list(FlowCategoryCount.objects.order_by('id'))
        self.assertEqual(len(cat_counts), 1)
        self.assertEqual(cat_counts[0].result_name, "Color")
        self.assertEqual(cat_counts[0].category_name, "Red")
        self.assertEqual(cat_counts[0].count, 1)

        msg3 = Msg.objects.get(id__gt=msg2.id)
        self.assertEqual(msg3.direction, 'O')
        self.assertEqual(msg3.text, "Good choice, I like Red too! What is your favorite beer?")

        msg4 = Msg.create_incoming(self.channel, 'tel:+12065552020', "primus")

        run.refresh_from_db()
        self.assertEqual(run.results, {
            'color': {
                'category': "Red",
                'node_uuid': str(rule_set1.uuid),
                'name': "Color",
                'value': "red",
                'created_on': matchers.ISODate(),
                'input': "I like red"
            },
            'beer': {
                'category': "Primus",
                'node_uuid': matchers.UUID4String(),
                'name': "Beer",
                'value': "primus",
                'created_on': matchers.ISODate(),
                'input': "primus"
            }
        })

        msg5 = Msg.objects.get(id__gt=msg4.id)
        self.assertEqual(msg5.direction, 'O')
        self.assertEqual(msg5.text,
                         "Mmmmm... delicious Primus. If only they made red Primus! Lastly, what is your name?")

        msg6 = Msg.create_incoming(self.channel, 'tel:+12065552020', "Ben")

        msg7 = Msg.objects.get(id__gt=msg6.id)
        self.assertEqual(msg7.direction, 'O')
        self.assertEqual(msg7.text, "Thanks Ben, we are all done!")

        run.refresh_from_db()
        self.assertEqual(run.exit_type, FlowRun.EXIT_TYPE_COMPLETED)
        self.assertIsNotNone(run.exited_on)

    @override_settings(SEND_WEBHOOKS=True)
    def test_webhook_payload(self):
        flow = self.get_flow('webhook_payload')

        # we call as an action, and then again as a ruleset
        ctype = 'application/json'
        ruleset_post = self.mockRequest('POST', '/send_results', '{"received":"ruleset"}', content_type=ctype)
        action_post = self.mockRequest('POST', '/send_results', '{"received":"action"}', content_type=ctype)
        action_get = self.mockRequest('GET', '/send_results', '{"received":"action"}', content_type=ctype)
        ruleset_get = self.mockRequest('GET', '/send_results', '{"received":"ruleset"}', content_type=ctype)

        self.assertEqual("What is your favorite natural disaster?", self.send_message(flow, "39"))
        self.assertEqual("Hey, so should I send it as a ruleset too?", self.send_message(flow, "tornado"))
        self.assertEqual("Great work.", self.send_message(flow, "yes"))

        msg = Msg.objects.all().order_by('-id').first()
        run = FlowRun.objects.all().order_by('id').first()

        # if there is no urn, it still works, but is omitted
        empty_post = self.mockRequest('POST', '/send_results', '{"received":"ruleset"}', content_type=ctype)
        empty = self.create_contact('Empty Contact')
        flow.start([], [empty])
        self.send('39', empty)
        self.send('tornado', empty)
        self.assertNotIn('urn', empty_post.data['contact'])

        # test fallback urn
        fallback_post = self.mockRequest('POST', '/send_results', '{"received":"ruleset"}', content_type=ctype)
        empty_flow = self.get_flow('empty_payload')
        empty_flow.start([], [self.contact], restart_participants=True)
        self.assertEqual('tel:+12065552020', fallback_post.data['contact']['urn'])

        def assert_payload(payload, path_length, result_count, results):
            self.assertEqual(dict(name='Ben Haggerty', uuid=self.contact.uuid, urn='tel:+12065552020'), payload['contact'])
            self.assertEqual(dict(name='Webhook Payload Test', uuid=flow.uuid), payload['flow'])
            self.assertEqual(dict(name='Test Channel', uuid=self.channel.uuid), payload['channel'])
            self.assertEqual(path_length, len(payload['path']))
            self.assertEqual(result_count, len(payload['results']))
            self.assertEqual(dict(uuid=six.text_type(run.uuid), created_on=run.created_on.isoformat()), payload['run'])

            # make sure things don't sneak into our path format unintentionally
            # first item in the path should have node, arrived, and exit
            self.assertEqual(3, len(payload['path'][0]))

            # last item has the same, but no exit
            self.assertEqual(2, len(payload['path'][-1]))

            for key, value in six.iteritems(results):
                result = payload['results'].get(key)
                self.assertEqual(value, result.get('value'))

                # make sure nothing sneaks into our result format unintentionally
                self.assertEqual(6, len(result))

        # we arrived at our ruleset webhook first
        assert_payload(ruleset_post.data, 5, 2, dict(age="39", disaster="tornado"))
        assert_payload(action_post.data, 7, 3, dict(send_action="yes"))

        # gets shouldn't have payloads
        self.assertIsNone(action_get.data)
        self.assertIsNone(ruleset_get.data)

        # make sure triggering without a url fails properly
        WebHookEvent.trigger_flow_webhook(FlowRun.objects.all().first(), None, '', msg)
        result = WebHookResult.objects.all().order_by('-id').first()
        self.assertIn('No webhook_url specified, skipping send', result.message)

    def test_validate_flow_definition(self):

        with self.assertRaises(ValueError):
            FlowRevision.validate_flow_definition(self.get_flow_json('not_fully_localized'))

        # base_language of null, but spec version 8
        with self.assertRaises(ValueError):
            FlowRevision.validate_flow_definition(self.get_flow_json('no_base_language_v8'))

        # base_language of 'eng' but non localized actions
        with self.assertRaises(ValueError):
            FlowRevision.validate_flow_definition(self.get_flow_json('non_localized_with_language'))

        with self.assertRaises(ValueError):
            FlowRevision.validate_flow_definition(self.get_flow_json('non_localized_ruleset'))

    def test_start_flow_queueing(self):
        self.get_flow('start_flow_queued')
        self.channel.channel_type = 'TG'
        self.channel.save()

        # trigger Flow A
        self.send('flowa')

        # make sure the message sent after our start flow action is never created
        self.assertIsNone(Msg.objects.filter(text='This message should never be sent').first())

        # the message sent by the flow started by our start action should have gotten queued
        msg = Msg.objects.filter(text='This message should be queued').first()
        self.assertIsNotNone(msg.queued_on)
        self.assertEqual(WIRED, msg.status)
        msg.delete()

        # now lets do the same test but with a flow that prompts first
        self.send('flowawait')
        self.send('yes')

        msg = Msg.objects.filter(text='This message should be queued').first()
        self.assertIsNotNone(msg.queued_on)
        self.assertEqual(WIRED, msg.status)

    def test_sms_forms(self):
        flow = self.get_flow('sms_form')

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

        for delimiter in ['+', '.']:
            # now let's switch to pluses and make sure they do the right thing
            for ruleset in flow.rule_sets.filter(ruleset_type='form_field'):
                config = ruleset.config
                config['field_delimiter'] = delimiter
                ruleset.config = config
                ruleset.save()

            ctx = dict(delim=delimiter)

            assert_response("101%(delim)sM%(delim)sSeattle" % ctx, "Sorry, 101 doesn't look like a valid age, please try again.")
            assert_response("36%(delim)selephant%(delim)sSeattle" % ctx, "Sorry, elephant doesn't look like a valid gender. Try again.")
            assert_response("36%(delim)sM%(delim)sSaturn" % ctx, "I don't know the location Saturn. Please try again.")
            assert_response("36%(delim)sM%(delim)sSeattle" % ctx, "Thanks for your submission. We have that as:\n\n36 / M / Seattle")
            assert_response("15%(delim)sM%(delim)spequeño" % ctx, "I don't know the location pequeño. Please try again.")

    def test_write_protection(self):
        flow = self.get_flow('favorites')
        flow_json = flow.as_json()

        self.login(self.admin)

        # saving should work
        flow.update(flow_json, self.admin)

        # but if we save from in the past after our save it should fail
        with self.assertRaises(FlowUserConflictException):
            flow.update(flow_json, self.admin)

        # check view sends converts exception to error response
        response = self.client.post(reverse('flows.flow_json', args=[flow.id]), data=json.dumps(flow_json),
                                    content_type='application/json')

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json(), {
            'description': 'Administrator is currently editing this Flow. '
                           'Your changes will not be saved until you refresh your browser.',
            'status': 'failure'
        })

        # we should also fail if we try saving an old spec version from the editor
        flow.refresh_from_db()
        flow_json = flow.as_json()

        with patch('temba.flows.models.get_current_export_version') as mock_version:
            mock_version.return_value = '1.234'

            with self.assertRaises(FlowVersionConflictException):
                flow.update(flow_json, self.admin)

            # check view sends converts exception to error response
            response = self.client.post(reverse('flows.flow_json', args=[flow.id]), data=json.dumps(flow_json),
                                        content_type='application/json')

            self.assertEqual(response.status_code, 400)
            self.assertEqual(response.json(), {
                'description': 'Your flow has been upgraded to the latest version. '
                               'In order to continue editing, please refresh your browser.',
                'status': 'failure'
            })

        # create an invalid loop in the flow definition
        flow_json['action_sets'][0]['destination'] = flow_json['action_sets'][0]['uuid']

        with self.assertRaises(FlowInvalidCycleException):
            flow.update(flow_json, self.admin)

        # check view sends converts exception to error response
        response = self.client.post(reverse('flows.flow_json', args=[flow.id]), data=json.dumps(flow_json),
                                    content_type='application/json')

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json(), {
            'description': 'Your flow contains an invalid loop. Please refresh your browser.',
            'status': 'failure'
        })

    def test_flow_category_counts(self):

        def assertCount(counts, result_key, category_name, truth):
            found = False
            for count in counts['counts']:
                if count['key'] == result_key:
                    categories = count['categories']
                    for category in categories:
                        if category['name'] == category_name:
                            found = True
                            self.assertEqual(category['count'], truth)
            self.assertTrue(found)

        favorites = self.get_flow('favorites')

        # add in some fake data
        for i in range(0, 10):
            contact = self.create_contact('Contact %d' % i, '+120655530%d' % i)
            self.send_message(favorites, 'blue', contact=contact)
            self.send_message(favorites, 'primus', contact=contact)
            self.send_message(favorites, 'russell', contact=contact)

        for i in range(0, 5):
            contact = self.create_contact('Contact %d' % i, '+120655531%d' % i)
            self.send_message(favorites, 'red', contact=contact)
            self.send_message(favorites, 'primus', contact=contact)
            self.send_message(favorites, 'earl', contact=contact)

        # test update flow values
        for i in range(0, 5):
            contact = self.create_contact('Contact %d' % i, '+120655532%d' % i)
            self.send_message(favorites, 'orange', contact=contact)
            self.send_message(favorites, 'green', contact=contact)
            self.send_message(favorites, 'skol', contact=contact)
            self.send_message(favorites, 'bobby', contact=contact)

        counts = favorites.get_category_counts()

        assertCount(counts, 'color', 'Blue', 10)
        assertCount(counts, 'color', 'Red', 5)
        assertCount(counts, 'beer', 'Primus', 15)

        # name shouldn't be included since it's open ended
        self.assertNotIn('"name": "Name"', json.dumps(counts))

        # five oranges went back and became greens
        assertCount(counts, 'color', 'Other', 0)
        assertCount(counts, 'color', 'Green', 5)

        # now remap the uuid for our color node
        flow_json = favorites.as_json()
        color_ruleset = (flow_json['rule_sets'][0])
        flow_json = json.loads(json.dumps(flow_json).replace(color_ruleset['uuid'], str(uuid4())))
        favorites.update(flow_json)

        # send a few more runs through our updated flow
        for i in range(0, 3):
            contact = self.create_contact('Contact %d' % i, '+120655533%d' % i)
            self.send_message(favorites, 'red', contact=contact)
            self.send_message(favorites, 'turbo', contact=contact)

        # should now have three more reds
        counts = favorites.get_category_counts()
        assertCount(counts, 'color', 'Red', 8)
        assertCount(counts, 'beer', 'Turbo King', 3)

        # but if we ignore the ones from our deleted color node, should only have the three new ones
        counts = favorites.get_category_counts(deleted_nodes=False)
        assertCount(counts, 'color', 'Red', 3)

        # now erase the color key entirely
        flow_json['rule_sets'] = flow_json['rule_sets'][1:]
        favorites.update(flow_json)

        # now the color counts have been removed, but beer is still there
        counts = favorites.get_category_counts()
        self.assertNotIn('color', counts)
        assertCount(counts, 'beer', 'Turbo King', 3)

        # make sure it still works after ze squashings
        self.assertEqual(76, FlowCategoryCount.objects.all().count())
        FlowCategoryCount.squash()
        self.assertEqual(9, FlowCategoryCount.objects.all().count())
        counts = favorites.get_category_counts()
        assertCount(counts, 'beer', 'Turbo King', 3)

        # test tostring
        six.text_type(FlowCategoryCount.objects.all().first())

        # and if we delete our runs, things zero out
        FlowRun.objects.all().delete()
        counts = favorites.get_category_counts()
        assertCount(counts, 'beer', 'Turbo King', 0)

    def test_flow_results(self):
        favorites = self.get_flow('favorites')

        with patch('temba.flows.views.FlowCRUDL.RunTable.paginate_by', 1):

            pete = self.create_contact('Pete', '+12065553027')
            self.send_message(favorites, 'blue', contact=pete)

            jimmy = self.create_contact('Jimmy', '+12065553026')
            self.send_message(favorites, 'red', contact=jimmy)
            self.send_message(favorites, 'turbo', contact=jimmy)

            kobe = Contact.get_test_contact(self.admin)
            self.send_message(favorites, 'green', contact=kobe)
            self.send_message(favorites, 'skol', contact=kobe)

            self.login(self.admin)
            response = self.client.get(reverse('flows.flow_results', args=[favorites.uuid]))

            # the rulesets should be present as column headers
            self.assertContains(response, 'Beer')
            self.assertContains(response, 'Color')
            self.assertContains(response, 'Name')

            # fetch counts endpoint, should have 2 color results (one is a test contact)
            response = self.client.get(reverse('flows.flow_category_counts', args=[favorites.uuid]))
            counts = response.json()['counts']
            self.assertEqual("Color", counts[0]['name'])
            self.assertEqual(2, counts[0]['total'])

            # test a search on our runs
            response = self.client.get('%s?q=pete' % reverse('flows.flow_run_table', args=[favorites.pk]))
            self.assertEqual(len(response.context['runs']), 1)
            self.assertContains(response, 'Pete')
            self.assertNotContains(response, 'Jimmy')

            response = self.client.get('%s?q=555-3026' % reverse('flows.flow_run_table', args=[favorites.pk]))
            self.assertEqual(len(response.context['runs']), 1)
            self.assertContains(response, 'Jimmy')
            self.assertNotContains(response, 'Pete')

            # fetch our intercooler rows for the run table
            response = self.client.get(reverse('flows.flow_run_table', args=[favorites.pk]))
            self.assertEqual(len(response.context['runs']), 1)
            self.assertEqual(200, response.status_code)
            self.assertContains(response, 'Jimmy')
            self.assertContains(response, 'red')
            self.assertContains(response, 'Red')
            self.assertContains(response, 'turbo')
            self.assertContains(response, 'Turbo King')
            self.assertNotContains(response, 'skol')

            # one more row to add
            self.assertEqual(1, len(response.context['runs']))
            # self.assertNotContains(response, "ic-append-from")

            next_link = re.search('ic-append-from=\"(.*)\" ic-trigger-on', force_text(response.content)).group(1)
            response = self.client.get(next_link)
            self.assertEqual(200, response.status_code)

            FlowCRUDL.ActivityChart.HISTOGRAM_MIN = 0
            FlowCRUDL.ActivityChart.PERIOD_MIN = 0

            # and some charts
            response = self.client.get(reverse('flows.flow_activity_chart', args=[favorites.pk]))

            # we have two active runs
            self.assertContains(response, "name: 'Active', y: 2")
            self.assertContains(response, "3 Responses")

            # now send another message
            self.send_message(favorites, 'primus', contact=pete)
            self.send_message(favorites, 'Pete', contact=pete)

            # now only one active, one completed, and 5 total responses
            response = self.client.get(reverse('flows.flow_activity_chart', args=[favorites.pk]))
            self.assertContains(response, "name: 'Active', y: 1")
            self.assertContains(response, "name: 'Completed', y: 1")
            self.assertContains(response, "5 Responses")

            # they all happened on the same day
            response = self.client.get(reverse('flows.flow_activity_chart', args=[favorites.pk]))
            points = response.context['histogram']
            self.assertEqual(1, len(points))

            # put one of our counts way in the past so we get a different histogram scale
            count = FlowPathCount.objects.filter(flow=favorites).order_by('id')[1]
            count.period = count.period - timedelta(days=25)
            count.save()
            response = self.client.get(reverse('flows.flow_activity_chart', args=[favorites.pk]))
            points = response.context['histogram']
            self.assertTrue(timedelta(days=24) < (points[1]['bucket'] - points[0]['bucket']))

            # pick another scale
            count.period = count.period - timedelta(days=600)
            count.save()
            response = self.client.get(reverse('flows.flow_activity_chart', args=[favorites.pk]))

            # this should give us a more compressed histogram
            points = response.context['histogram']
            self.assertTrue(timedelta(days=620) < (points[1]['bucket'] - points[0]['bucket']))

            self.assertEqual(24, len(response.context['hod']))
            self.assertEqual(7, len(response.context['dow']))

        # delete a run
        with patch('temba.flows.views.FlowCRUDL.RunTable.paginate_by', 100):
            response = self.client.get(reverse('flows.flow_run_table', args=[favorites.pk]))
            self.assertEqual(len(response.context['runs']), 2)

            self.client.post(reverse('flows.flowrun_delete', args=[response.context['runs'][0].id]))
            response = self.client.get(reverse('flows.flow_run_table', args=[favorites.pk]))
            self.assertEqual(len(response.context['runs']), 1)

        with patch('temba.flows.views.FlowCRUDL.RunTable.paginate_by', 1):

            # create one empty run
            FlowRun.objects.create(org=favorites.org, flow=favorites, contact=pete, responded=True)

            # fetch our intercooler rows for the run table
            response = self.client.get(reverse('flows.flow_run_table', args=[favorites.pk]))
            self.assertEqual(len(response.context['runs']), 1)
            self.assertEqual(200, response.status_code)

        # make sure we show results for flows with only expression splits
        RuleSet.objects.filter(flow=favorites).update(ruleset_type=RuleSet.TYPE_EXPRESSION)
        response = self.client.get(reverse('flows.flow_activity_chart', args=[favorites.pk]))

        self.assertEqual(24, len(response.context['hod']))
        self.assertEqual(7, len(response.context['dow']))

    def test_send_all_replies(self):
        flow = self.get_flow('send_all')

        contact = self.create_contact('Stephen', '+12078778899', twitter='stephen')
        flow.start(groups=[], contacts=[contact], restart_participants=True)

        replies = Msg.objects.filter(contact=contact, direction='O')
        self.assertEqual(replies.count(), 1)
        self.assertIsNone(replies.filter(contact_urn__path='stephen').first())
        self.assertIsNotNone(replies.filter(contact_urn__path='+12078778899').first())

        Broadcast.objects.all().delete()
        Msg.objects.all().delete()

        # create twitter channel
        Channel.create(self.org, self.user, None, 'TT')
        flow.org.clear_cached_schemes()

        flow.start(groups=[], contacts=[contact], restart_participants=True)

        replies = Msg.objects.filter(contact=contact, direction='O')
        self.assertEqual(replies.count(), 2)
        self.assertIsNotNone(replies.filter(contact_urn__path='stephen').first())
        self.assertIsNotNone(replies.filter(contact_urn__path='+12078778899').first())

        Broadcast.objects.all().delete()
        Msg.objects.all().delete()

        # For offline survey runs with send to all URN
        survey_url = reverse('api.v1.steps')
        definition = flow.as_json()
        node_uuid = definition['action_sets'][0]['uuid']

        flow.update(definition)

        self.login(self.surveyor)
        data = dict(flow=flow.uuid,
                    revision=2,
                    contact=contact.uuid,
                    submitted_by=self.admin.username,
                    started='2015-08-25T11:09:29.088Z',
                    steps=[
                        dict(node=node_uuid,
                             arrived_on='2015-08-25T11:09:30.088Z',
                             actions=[
                                 dict(type="reply", msg="What is your favorite color?", send_all=True)
                             ])
                    ],
                    completed=False)

        with patch.object(timezone, 'now', return_value=datetime.datetime(2015, 9, 15, 0, 0, 0, 0, pytz.UTC)):
            self.client.post(survey_url + ".json", json.dumps(data), content_type="application/json",
                             HTTP_X_FORWARDED_HTTPS='https')

        out_msgs = Msg.objects.filter(direction='O').order_by('pk')
        self.assertEqual(len(out_msgs), 2)
        self.assertIsNotNone(out_msgs.filter(contact_urn__path='stephen').first())
        self.assertIsNotNone(out_msgs.filter(contact_urn__path='+12078778899').first())

        Broadcast.objects.all().delete()
        Msg.objects.all().delete()

        flow = self.get_flow('two_to_all')
        flow.start(groups=[], contacts=[contact], restart_participants=True)

        replies = Msg.objects.filter(contact=contact, direction='O')
        self.assertEqual(replies.count(), 4)
        self.assertEqual(replies.filter(contact_urn__path='stephen').count(), 2)
        self.assertEqual(replies.filter(contact_urn__path='+12078778899').count(), 2)

    def test_recent_messages(self):
        flow = self.get_flow('favorites')

        self.login(self.admin)
        recent_messages_url = reverse('flows.flow_recent_messages', args=[flow.pk])

        color_prompt = ActionSet.objects.filter(flow=flow, y=0).first()
        color_ruleset = RuleSet.objects.filter(flow=flow, label='Color').first()
        blue_rule = color_ruleset.get_rules()[-4]
        navy_rule = color_ruleset.get_rules()[-3]
        other_rule = color_ruleset.get_rules()[-1]

        # URL params for different flow path segments
        entry_params = "?exits=%s,%s&to=%s" % (color_prompt.exit_uuid, color_prompt.uuid, color_ruleset.uuid)
        other_params = "?exits=%s&to=%s" % (other_rule.uuid, other_rule.destination)
        blue_params = "?exits=%s,%s&to=%s" % (blue_rule.uuid, navy_rule.uuid, blue_rule.destination)
        invalid_params = "?exits=%s&to=%s" % (color_ruleset.uuid, color_ruleset.uuid)

        def assert_recent(resp, msgs):
            self.assertEqual([r['text'] for r in resp.json()], msgs)

        # no params returns no results
        assert_recent(self.client.get(recent_messages_url), [])

        flow.start([], [self.contact])
        self.create_msg(direction=INCOMING, contact=self.contact, text='chartreuse').handle()

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

        self.create_msg(direction=INCOMING, contact=self.contact, text='mauve').handle()

        response = self.client.get(recent_messages_url + entry_params)
        assert_recent(response, ["What is your favorite color?"])

        response = self.client.get(recent_messages_url + other_params)
        assert_recent(response, ["mauve", "chartreuse"])

        response = self.client.get(recent_messages_url + blue_params)
        assert_recent(response, [])

        self.create_msg(direction=INCOMING, contact=self.contact, text='blue').handle()

        response = self.client.get(recent_messages_url + entry_params)
        assert_recent(response, ["What is your favorite color?"])

        response = self.client.get(recent_messages_url + other_params)
        assert_recent(response, ["mauve", "chartreuse"])

        response = self.client.get(recent_messages_url + blue_params)
        assert_recent(response, ["blue"])

    def test_completion(self):

        flow = self.get_flow('favorites')
        self.login(self.admin)

        response = self.client.get('%s?flow=%d' % (reverse('flows.flow_completion'), flow.pk))
        response = response.json()

        def assert_in_response(response, data_key, key):
            found = False
            for item in response[data_key]:
                if key == item['name']:
                    found = True
            self.assertTrue(found, 'Key %s not found in %s' % (key, response))

        assert_in_response(response, 'message_completions', 'contact')
        assert_in_response(response, 'message_completions', 'contact.first_name')
        assert_in_response(response, 'message_completions', 'contact.tel')
        assert_in_response(response, 'message_completions', 'contact.mailto')

        assert_in_response(response, 'message_completions', 'parent.contact.uuid')
        assert_in_response(response, 'message_completions', 'child.contact.uuid')

        assert_in_response(response, 'message_completions', 'flow.color')
        assert_in_response(response, 'message_completions', 'flow.color.category')
        assert_in_response(response, 'message_completions', 'flow.color.text')
        assert_in_response(response, 'message_completions', 'flow.color.time')

        assert_in_response(response, 'function_completions', 'SUM')
        assert_in_response(response, 'function_completions', 'ABS')
        assert_in_response(response, 'function_completions', 'YEAR')

        # a Twitter channel
        Channel.create(self.org, self.user, None, 'TT')

        response = self.client.get('%s?flow=%d' % (reverse('flows.flow_completion'), flow.pk))
        response = response.json()

        assert_in_response(response, 'message_completions', 'contact.twitter')

    def test_bulk_exit(self):
        flow = self.get_flow('favorites')
        color = RuleSet.objects.get(label='Color', flow=flow)
        contacts = [self.create_contact("Run Contact %d" % i, "+25078838338%d" % i) for i in range(6)]

        # add our contacts to the flow
        for contact in contacts:
            self.send_message(flow, 'chartreuse', contact=contact)

        # should have six active flowruns
        (active, visited) = flow.get_activity()
        self.assertEqual(FlowRun.objects.filter(is_active=True).count(), 6)
        self.assertEqual(FlowRun.objects.filter(is_active=False).count(), 0)
        self.assertEqual(active[color.uuid], 6)

        self.assertEqual(FlowRunCount.get_totals(flow), {'A': 6, 'C': 0, 'E': 0, 'I': 0})

        # expire them all
        FlowRun.bulk_exit(FlowRun.objects.filter(is_active=True), FlowRun.EXIT_TYPE_EXPIRED)

        # should all be expired
        (active, visited) = flow.get_activity()
        self.assertEqual(FlowRun.objects.filter(is_active=True).count(), 0)
        self.assertEqual(FlowRun.objects.filter(is_active=False, exit_type='E').exclude(exited_on=None).count(), 6)
        self.assertEqual(len(active), 0)

        # assert our flowrun counts
        self.assertEqual(FlowRunCount.get_totals(flow), {'A': 0, 'C': 0, 'E': 6, 'I': 0})

        # start all contacts in the flow again
        for contact in contacts:
            self.send_message(flow, 'chartreuse', contact=contact, restart_participants=True)

        self.assertEqual(6, FlowRun.objects.filter(is_active=True).count())
        self.assertEqual(FlowRunCount.get_totals(flow), {'A': 6, 'C': 0, 'E': 6, 'I': 0})

        # stop them all
        FlowRun.bulk_exit(FlowRun.objects.filter(is_active=True), FlowRun.EXIT_TYPE_INTERRUPTED)

        self.assertEqual(FlowRun.objects.filter(is_active=False, exit_type='I').exclude(exited_on=None).count(), 6)
        self.assertEqual(FlowRunCount.get_totals(flow), {'A': 0, 'C': 0, 'E': 6, 'I': 6})

        # squash our counts
        squash_flowruncounts()
        self.assertEqual(FlowRunCount.get_totals(flow), {'A': 0, 'C': 0, 'E': 6, 'I': 6})

    def test_squash_run_counts(self):
        flow = self.get_flow('favorites')
        flow2 = self.get_flow('pick_a_number')

        FlowRunCount.objects.create(flow=flow, count=2, exit_type=None)
        FlowRunCount.objects.create(flow=flow, count=1, exit_type=None)
        FlowRunCount.objects.create(flow=flow, count=3, exit_type='E')
        FlowRunCount.objects.create(flow=flow2, count=10, exit_type='I')
        FlowRunCount.objects.create(flow=flow2, count=-1, exit_type='I')

        squash_flowruncounts()
        self.assertEqual(FlowRunCount.objects.all().count(), 3)
        self.assertEqual(FlowRunCount.get_totals(flow2), {'A': 0, 'C': 0, 'E': 0, 'I': 9})
        self.assertEqual(FlowRunCount.get_totals(flow), {'A': 3, 'C': 0, 'E': 3, 'I': 0})

        max_id = FlowRunCount.objects.all().order_by('-id').first().id

        # no-op this time
        squash_flowruncounts()
        self.assertEqual(max_id, FlowRunCount.objects.all().order_by('-id').first().id)

    def test_activity(self):
        flow = self.get_flow('favorites')
        color_question = ActionSet.objects.get(y=0, flow=flow)
        other_action = ActionSet.objects.get(y=8, flow=flow)
        beer_question = ActionSet.objects.get(y=237, flow=flow)
        name_question = ActionSet.objects.get(y=535, flow=flow)
        end_prompt = ActionSet.objects.get(y=805, flow=flow)
        beer = RuleSet.objects.get(label='Beer', flow=flow)
        color = RuleSet.objects.get(label='Color', flow=flow)
        name = RuleSet.objects.get(label='Name', flow=flow)

        rules = color.get_rules()
        color_other_uuid = rules[-1].uuid
        color_blue_uuid = rules[-4].uuid

        # we don't know this shade of green, it should route us to the beginning again
        run1, = flow.start([], [self.contact])
        self.create_msg(direction=INCOMING, contact=self.contact, text='chartreuse').handle()

        (active, visited) = flow.get_activity()

        self.assertEqual(active, {color.uuid: 1})

        self.assertEqual(visited, {
            '%s:%s' % (color_question.exit_uuid, color.uuid): 1,
            '%s:%s' % (color_other_uuid, other_action.uuid): 1,
            '%s:%s' % (other_action.exit_uuid, color.uuid): 1,
        })
        self.assertEqual(flow.get_run_stats(),
                         {'total': 1, 'active': 1, 'completed': 0, 'expired': 0, 'interrupted': 0, 'completion': 0})

        # another unknown color, that'll route us right back again
        # the active stats will look the same, but there should be one more journey on the path
        self.create_msg(direction=INCOMING, contact=self.contact, text='mauve').handle()
        (active, visited) = flow.get_activity()

        self.assertEqual(active, {color.uuid: 1})
        self.assertEqual(visited, {
            '%s:%s' % (color_question.exit_uuid, color.uuid): 1,
            '%s:%s' % (color_other_uuid, other_action.uuid): 2,
            '%s:%s' % (other_action.exit_uuid, color.uuid): 2,
        })

        # this time a color we know takes us elsewhere, activity will move
        # to another node, but still just one entry
        self.create_msg(direction=INCOMING, contact=self.contact, text='blue').handle()
        (active, visited) = flow.get_activity()

        self.assertEqual(active, {beer.uuid: 1})
        self.assertEqual(visited, {
            '%s:%s' % (color_question.exit_uuid, color.uuid): 1,
            '%s:%s' % (color_other_uuid, other_action.uuid): 2,
            '%s:%s' % (other_action.exit_uuid, color.uuid): 2,
            '%s:%s' % (color_blue_uuid, beer_question.uuid): 1,
            '%s:%s' % (beer_question.exit_uuid, beer.uuid): 1,
        })

        # check recent runs
        recent = FlowPathRecentRun.get_recent([color_question.exit_uuid], color.uuid)
        self.assertEqual([r['text'] for r in recent], ["What is your favorite color?"])

        recent = FlowPathRecentRun.get_recent([color_other_uuid], other_action.uuid)
        self.assertEqual([r['text'] for r in recent], ["mauve", "chartreuse"])

        recent = FlowPathRecentRun.get_recent([other_action.exit_uuid], color.uuid)
        self.assertEqual([r['text'] for r in recent], ["I don't know that color. Try again.", "I don't know that color. Try again."])

        recent = FlowPathRecentRun.get_recent([color_blue_uuid], beer_question.uuid)
        self.assertEqual([r['text'] for r in recent], ["blue"])

        # a new participant, showing distinct active counts and incremented path
        ryan = self.create_contact('Ryan Lewis', '+12065550725')
        self.send_message(flow, 'burnt sienna', contact=ryan)
        (active, visited) = flow.get_activity()

        self.assertEqual(active, {color.uuid: 1, beer.uuid: 1})
        self.assertEqual(visited, {
            '%s:%s' % (color_question.exit_uuid, color.uuid): 2,
            '%s:%s' % (color_other_uuid, other_action.uuid): 3,
            '%s:%s' % (other_action.exit_uuid, color.uuid): 3,
            '%s:%s' % (color_blue_uuid, beer_question.uuid): 1,
            '%s:%s' % (beer_question.exit_uuid, beer.uuid): 1,
        })
        self.assertEqual(flow.get_run_stats(),
                         {'total': 2, 'active': 2, 'completed': 0, 'expired': 0, 'interrupted': 0, 'completion': 0})

        # now let's have them land in the same place
        self.send_message(flow, 'blue', contact=ryan)
        (active, visited) = flow.get_activity()
        self.assertEqual(active, {beer.uuid: 2})

        # now move our first contact forward to the end
        self.send_message(flow, 'Turbo King')
        self.send_message(flow, 'Ben Haggerty')
        (active, visited) = flow.get_activity()
        self.assertEqual(active, {beer.uuid: 1})

        # half of our flows are now complete
        self.assertEqual(flow.get_run_stats(),
                         {'total': 2, 'active': 1, 'completed': 1, 'expired': 0, 'interrupted': 0, 'completion': 50})

        # we are going to expire, but we want runs across two different flows
        # to make sure that our optimization for expiration is working properly
        cga_flow = self.get_flow('color_gender_age')
        self.assertEqual("What is your gender?", self.send_message(cga_flow, "Red"))
        self.assertEqual(1, len(cga_flow.get_activity()[0]))

        # expire the first contact's runs
        FlowRun.bulk_exit(FlowRun.objects.filter(contact=self.contact), FlowRun.EXIT_TYPE_EXPIRED)

        # no active runs for our contact
        self.assertEqual(0, FlowRun.objects.filter(contact=self.contact, is_active=True).count())

        # both of our flows should have reduced active contacts
        self.assertEqual(0, len(cga_flow.get_activity()[0]))

        # now we should only have one node with active runs, but the paths stay
        # the same since those are historical
        (active, visited) = flow.get_activity()
        self.assertEqual(active, {beer.uuid: 1})
        self.assertEqual(visited, {
            '%s:%s' % (color_question.exit_uuid, color.uuid): 2,
            '%s:%s' % (color_other_uuid, other_action.uuid): 3,
            '%s:%s' % (other_action.exit_uuid, color.uuid): 3,
            '%s:%s' % (color_blue_uuid, beer_question.uuid): 2,
            '%s:%s' % (beer_question.exit_uuid, beer.uuid): 2,
            '%s:%s' % (beer.get_rules()[2].uuid, name_question.uuid): 1,
            '%s:%s' % (name_question.exit_uuid, name.uuid): 1,
            '%s:%s' % (name.get_rules()[0].uuid, end_prompt.uuid): 1,
        })

        # no completed runs but one expired run
        self.assertEqual(flow.get_run_stats(),
                         {'total': 2, 'active': 1, 'completed': 0, 'expired': 1, 'interrupted': 0, 'completion': 0})

        # check that we have the right number of runs
        self.assertEqual(2, FlowRun.objects.filter(flow=flow).count())

        # now let's delete our contact, we'll still have one active node, but
        # our visit path counts will go down by two since he went there twice
        self.contact.release(self.user)
        (active, visited) = flow.get_activity()
        self.assertEqual(active, {beer.uuid: 1})
        self.assertEqual(visited, {
            '%s:%s' % (color_question.exit_uuid, color.uuid): 1,
            '%s:%s' % (color_other_uuid, other_action.uuid): 1,
            '%s:%s' % (other_action.exit_uuid, color.uuid): 1,
            '%s:%s' % (color_blue_uuid, beer_question.uuid): 1,
            '%s:%s' % (beer_question.exit_uuid, beer.uuid): 1,
            '%s:%s' % (beer.get_rules()[2].uuid, name_question.uuid): 0,
            '%s:%s' % (name_question.exit_uuid, name.uuid): 0,
            '%s:%s' % (name.get_rules()[0].uuid, end_prompt.uuid): 0,
        })

        # he was also accounting for our completion rate, back to nothing
        self.assertEqual(flow.get_run_stats(),
                         {'total': 1, 'active': 1, 'completed': 0, 'expired': 0, 'interrupted': 0, 'completion': 0})

        # advance ryan to the end to make sure our percentage accounts for one less contact
        self.send_message(flow, 'Turbo King', contact=ryan)
        self.send_message(flow, 'Ryan Lewis', contact=ryan)
        (active, visited) = flow.get_activity()
        self.assertEqual(active, {})
        self.assertEqual(visited, {
            '%s:%s' % (color_question.exit_uuid, color.uuid): 1,
            '%s:%s' % (color_other_uuid, other_action.uuid): 1,
            '%s:%s' % (other_action.exit_uuid, color.uuid): 1,
            '%s:%s' % (color_blue_uuid, beer_question.uuid): 1,
            '%s:%s' % (beer_question.exit_uuid, beer.uuid): 1,
            '%s:%s' % (beer.get_rules()[2].uuid, name_question.uuid): 1,
            '%s:%s' % (name_question.exit_uuid, name.uuid): 1,
            '%s:%s' % (name.get_rules()[0].uuid, end_prompt.uuid): 1,
        })
        self.assertEqual(flow.get_run_stats(),
                         {'total': 1, 'active': 0, 'completed': 1, 'expired': 0, 'interrupted': 0, 'completion': 100})

        # messages to/from deleted contacts shouldn't appear in the recent runs
        recent = FlowPathRecentRun.get_recent([color_other_uuid], other_action.uuid)
        self.assertEqual([r['text'] for r in recent], ["burnt sienna"])

        # test contacts should not affect the counts
        hammer = Contact.get_test_contact(self.admin)

        # please hammer, don't hurt em
        self.send_message(flow, 'Rose', contact=hammer)
        self.send_message(flow, 'Violet', contact=hammer)
        self.send_message(flow, 'Blue', contact=hammer)
        self.send_message(flow, 'Turbo King', contact=hammer)
        self.send_message(flow, 'MC Hammer', contact=hammer)

        # our flow stats should be unchanged
        (active, visited) = flow.get_activity()
        self.assertEqual(active, {})
        self.assertEqual(visited, {
            '%s:%s' % (color_question.exit_uuid, color.uuid): 1,
            '%s:%s' % (color_other_uuid, other_action.uuid): 1,
            '%s:%s' % (other_action.exit_uuid, color.uuid): 1,
            '%s:%s' % (color_blue_uuid, beer_question.uuid): 1,
            '%s:%s' % (beer_question.exit_uuid, beer.uuid): 1,
            '%s:%s' % (beer.get_rules()[2].uuid, name_question.uuid): 1,
            '%s:%s' % (name_question.exit_uuid, name.uuid): 1,
            '%s:%s' % (name.get_rules()[0].uuid, end_prompt.uuid): 1,
        })
        self.assertEqual(flow.get_run_stats(),
                         {'total': 1, 'active': 0, 'completed': 1, 'expired': 0, 'interrupted': 0, 'completion': 100})

        # and no recent message entries for this test contact
        recent = FlowPathRecentRun.get_recent([color_other_uuid], other_action.uuid)
        self.assertEqual([r['text'] for r in recent], ["burnt sienna"])

        # try the same thing after squashing
        squash_flowpathcounts()
        visited = flow.get_activity()[1]
        self.assertEqual(visited, {
            '%s:%s' % (color_question.exit_uuid, color.uuid): 1,
            '%s:%s' % (color_other_uuid, other_action.uuid): 1,
            '%s:%s' % (other_action.exit_uuid, color.uuid): 1,
            '%s:%s' % (color_blue_uuid, beer_question.uuid): 1,
            '%s:%s' % (beer_question.exit_uuid, beer.uuid): 1,
            '%s:%s' % (beer.get_rules()[2].uuid, name_question.uuid): 1,
            '%s:%s' % (name_question.exit_uuid, name.uuid): 1,
            '%s:%s' % (name.get_rules()[0].uuid, end_prompt.uuid): 1,
        })

        # but hammer should have created some simulation activity
        (active, visited) = flow.get_activity(simulation=True)
        self.assertEqual(active, {})
        self.assertEqual(visited, {
            '%s:%s' % (color_question.exit_uuid, color.uuid): 1,
            '%s:%s' % (color_other_uuid, other_action.uuid): 2,
            '%s:%s' % (other_action.exit_uuid, color.uuid): 2,
            '%s:%s' % (color_blue_uuid, beer_question.uuid): 1,
            '%s:%s' % (beer_question.exit_uuid, beer.uuid): 1,
            '%s:%s' % (beer.get_rules()[2].uuid, name_question.uuid): 1,
            '%s:%s' % (name_question.exit_uuid, name.uuid): 1,
            '%s:%s' % (name.get_rules()[0].uuid, end_prompt.uuid): 1,
        })

        # delete our last contact to make sure activity is gone without first expiring, zeros abound
        ryan.release(self.admin)
        (active, visited) = flow.get_activity()
        self.assertEqual(active, {})
        self.assertEqual(visited, {
            '%s:%s' % (color_question.exit_uuid, color.uuid): 0,
            '%s:%s' % (color_other_uuid, other_action.uuid): 0,
            '%s:%s' % (other_action.exit_uuid, color.uuid): 0,
            '%s:%s' % (color_blue_uuid, beer_question.uuid): 0,
            '%s:%s' % (beer_question.exit_uuid, beer.uuid): 0,
            '%s:%s' % (beer.get_rules()[2].uuid, name_question.uuid): 0,
            '%s:%s' % (name_question.exit_uuid, name.uuid): 0,
            '%s:%s' % (name.get_rules()[0].uuid, end_prompt.uuid): 0,
        })
        self.assertEqual(flow.get_run_stats(),
                         {'total': 0, 'active': 0, 'completed': 0, 'expired': 0, 'interrupted': 0, 'completion': 0})

        # runs all gone too
        self.assertEqual(0, FlowRun.objects.filter(flow=flow, contact__is_test=False).count())

        # test that expirations remove activity when triggered from the cron in the same way
        tupac = self.create_contact('Tupac Shakur', '+12065550725')
        self.send_message(flow, 'azul', contact=tupac)
        (active, visited) = flow.get_activity()
        self.assertEqual(active, {color.uuid: 1})
        self.assertEqual(visited, {
            '%s:%s' % (color_question.exit_uuid, color.uuid): 1,
            '%s:%s' % (color_other_uuid, other_action.uuid): 1,
            '%s:%s' % (other_action.exit_uuid, color.uuid): 1,
            '%s:%s' % (color_blue_uuid, beer_question.uuid): 0,
            '%s:%s' % (beer_question.exit_uuid, beer.uuid): 0,
            '%s:%s' % (beer.get_rules()[2].uuid, name_question.uuid): 0,
            '%s:%s' % (name_question.exit_uuid, name.uuid): 0,
            '%s:%s' % (name.get_rules()[0].uuid, end_prompt.uuid): 0,
        })
        self.assertEqual(flow.get_run_stats(),
                         {'total': 1, 'active': 1, 'completed': 0, 'expired': 0, 'interrupted': 0, 'completion': 0})

        # set the run to be ready for expiration
        run = tupac.runs.first()
        run.expires_on = timezone.now() - timedelta(days=1)
        run.save(update_fields=('expires_on',))

        # now trigger the checking task and make sure it is removed from our activity
        from .tasks import check_flows_task
        check_flows_task()
        (active, visited) = flow.get_activity()
        self.assertEqual(active, {})
        self.assertEqual(flow.get_run_stats(),
                         {'total': 1, 'active': 0, 'completed': 0, 'expired': 1, 'interrupted': 0, 'completion': 0})

        # choose a rule that is not wired up (end of flow)
        jimmy = self.create_contact('Jimmy Graham', '+12065558888')
        self.send_message(flow, 'cyan', contact=jimmy, assert_reply=False)

        tyler = self.create_contact('Tyler Lockett', '+12065559999')
        self.send_message(flow, 'cyan', contact=tyler, assert_reply=False)

        squash_flowpathcounts()
        (active, visited) = flow.get_activity()

        self.assertEqual(active, {})
        self.assertEqual(visited, {
            '%s:%s' % (color_question.exit_uuid, color.uuid): 3,
            '%s:%s' % (color_other_uuid, other_action.uuid): 1,
            '%s:%s' % (other_action.exit_uuid, color.uuid): 1,
            '%s:%s' % (color_blue_uuid, beer_question.uuid): 0,
            '%s:%s' % (beer_question.exit_uuid, beer.uuid): 0,
            '%s:%s' % (beer.get_rules()[2].uuid, name_question.uuid): 0,
            '%s:%s' % (name_question.exit_uuid, name.uuid): 0,
            '%s:%s' % (name.get_rules()[0].uuid, end_prompt.uuid): 0,
        })

        # check that flow interruption counts properly
        rawls = self.create_contact('Thomas Rawls', '+12065557777')
        self.send_message(flow, 'blue', contact=rawls)

        # but he's got other things on his mind
        random_word = self.get_flow('random_word')
        self.send_message(random_word, 'blerg', contact=rawls)

        (active, visited) = flow.get_activity()

        self.assertEqual(active, {})
        self.assertEqual(visited, {
            '%s:%s' % (color_question.exit_uuid, color.uuid): 4,
            '%s:%s' % (color_other_uuid, other_action.uuid): 1,
            '%s:%s' % (other_action.exit_uuid, color.uuid): 1,
            '%s:%s' % (color_blue_uuid, beer_question.uuid): 1,
            '%s:%s' % (beer_question.exit_uuid, beer.uuid): 1,
            '%s:%s' % (beer.get_rules()[2].uuid, name_question.uuid): 0,
            '%s:%s' % (name_question.exit_uuid, name.uuid): 0,
            '%s:%s' % (name.get_rules()[0].uuid, end_prompt.uuid): 0,
        })

    def test_prune_recentruns(self):
        flow = self.get_flow('favorites')

        other_action = ActionSet.objects.get(y=8, flow=flow)
        color_ruleset = RuleSet.objects.get(label='Color', flow=flow)
        other_rule = color_ruleset.get_rules()[-1]

        # send 12 invalid color responses (must be from different contacts to avoid loop detection at 10 messages)
        bob = self.create_contact("Bob", number="+260964151234")
        for m in range(12):
            contact = self.contact if m % 2 == 0 else bob
            self.send_message(flow, '%d' % (m + 1), contact=contact)

        # all 12 messages are stored for the other segment
        other_recent = FlowPathRecentRun.objects.filter(from_uuid=other_rule.uuid, to_uuid=other_action.uuid)
        self.assertEqual(len(other_recent), 12)

        # and these are returned with most-recent first
        other_recent = FlowPathRecentRun.get_recent([other_rule.uuid], other_action.uuid, limit=None)
        self.assertEqual([r['text'] for r in other_recent], ["12", "11", "10", "9", "8", "7", "6", "5", "4", "3", "2", "1"])

        # even when limit is applied
        other_recent = FlowPathRecentRun.get_recent([other_rule.uuid], other_action.uuid, limit=5)
        self.assertEqual([r['text'] for r in other_recent], ["12", "11", "10", "9", "8"])

        squash_flowruncounts()

        # now only 5 newest are stored
        other_recent = FlowPathRecentRun.objects.filter(from_uuid=other_rule.uuid, to_uuid=other_action.uuid)
        self.assertEqual(len(other_recent), 5)

        other_recent = FlowPathRecentRun.get_recent([other_rule.uuid], other_action.uuid)
        self.assertEqual([r['text'] for r in other_recent], ["12", "11", "10", "9", "8"])

        # send another message and prune again
        self.send_message(flow, "13", contact=bob)
        squash_flowruncounts()

        other_recent = FlowPathRecentRun.get_recent([other_rule.uuid], other_action.uuid)
        self.assertEqual([r['text'] for r in other_recent], ["13", "12", "11", "10", "9"])

    def test_destination_type(self):
        flow = self.get_flow('pick_a_number')

        # our start points to a ruleset
        start = ActionSet.objects.get(flow=flow, y=0)

        # assert our destination
        self.assertEqual(FlowStep.TYPE_RULE_SET, start.destination_type)

        # and that ruleset points to an actionset
        ruleset = RuleSet.objects.get(uuid=start.destination)
        rule = ruleset.get_rules()[0]
        self.assertEqual(FlowStep.TYPE_ACTION_SET, rule.destination_type)

        # point our rule to a ruleset
        passive = RuleSet.objects.get(flow=flow, label='passive')
        self.update_destination(flow, rule.uuid, passive.uuid)
        ruleset = RuleSet.objects.get(uuid=start.destination)
        self.assertEqual(FlowStep.TYPE_RULE_SET, ruleset.get_rules()[0].destination_type)

    def test_orphaned_action_to_action(self):
        """
        Orphaned at an action, then routed to an action
        """

        # run a flow that ends on an action
        flow = self.get_flow('pick_a_number')
        self.assertEqual("You picked 3!", self.send_message(flow, "3"))

        pick_a_number = ActionSet.objects.get(flow=flow, y=0)
        you_picked = ActionSet.objects.get(flow=flow, y=228)

        # send a message, no flow should handle us since we are done
        incoming = self.create_msg(direction=INCOMING, contact=self.contact, text="Unhandled")
        handled = Flow.find_and_handle(incoming)[0]
        self.assertFalse(handled)

        # now wire up our finished action to the start of our flow
        flow = self.update_destination(flow, you_picked.uuid, pick_a_number.uuid)
        self.send_message(flow, "next message please", assert_reply=False, assert_handle=False)

    def test_orphaned_action_to_input_rule(self):
        """
        Orphaned at an action, then routed to a rule that evaluates on input
        """
        flow = self.get_flow('pick_a_number')

        self.assertEqual("You picked 6!", self.send_message(flow, "6"))

        you_picked = ActionSet.objects.get(flow=flow, y=228)
        number = RuleSet.objects.get(flow=flow, label='number')

        flow = self.update_destination(flow, you_picked.uuid, number.uuid)
        self.send_message(flow, "9", assert_reply=False, assert_handle=False)

    def test_orphaned_action_to_passive_rule(self):
        """
        Orphaned at an action, then routed to a rule that doesn't require input which leads
        to a rule that evaluates on input
        """
        flow = self.get_flow('pick_a_number')

        you_picked = ActionSet.objects.get(flow=flow, y=228)
        passive_ruleset = RuleSet.objects.get(flow=flow, label='passive')
        self.assertEqual("You picked 6!", self.send_message(flow, "6"))

        flow = self.update_destination(flow, you_picked.uuid, passive_ruleset.uuid)
        self.send_message(flow, "9", assert_reply=False, assert_handle=False)

    def test_deleted_ruleset(self):
        flow = self.get_flow('favorites')
        self.send_message(flow, "RED", restart_participants=True)

        # one active run
        self.assertEqual(1, FlowRun.objects.filter(contact=self.contact, is_active=True).count())

        # at this point we are waiting for the response to the second question about beer, let's delete it
        RuleSet.objects.get(flow=flow, label='Beer').delete()

        # we still have one active run, though we are somewhat in limbo
        self.assertEqual(1, FlowRun.objects.filter(contact=self.contact, is_active=True).count())

        # sending a new message in shouldn't get a reply, and our run should be terminated
        responses = self.send_message(flow, "abandoned", assert_reply=False, assert_handle=True)
        self.assertIsNone(responses)
        self.assertEqual(0, FlowRun.objects.filter(contact=self.contact, is_active=True).count())

    def test_server_runtime_cycle(self):
        flow = self.get_flow('loop_detection')
        first_actionset = ActionSet.objects.get(flow=flow, y=0)
        group_ruleset = RuleSet.objects.get(flow=flow, label='Group Split A')
        group_one_rule = group_ruleset.get_rules()[0]
        name_ruleset = RuleSet.objects.get(flow=flow, label='Name Split')
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
        group_a = ContactGroup.user_groups.create(org=self.org, name="Group A",
                                                  created_by=self.admin, modified_by=self.admin)
        group_a.contacts.add(self.contact)

        # rule turning back on ourselves
        self.update_destination_no_check(flow, group_ruleset.uuid, group_ruleset.uuid, rule=group_one_rule.uuid)
        self.send_message(flow, "1", assert_reply=False, assert_handle=False)

        # should have an interrupted run
        self.assertEqual(1, FlowRun.objects.filter(contact=self.contact, exit_type=FlowRun.EXIT_TYPE_INTERRUPTED).count())

        flow.runs.all().delete()
        flow.delete()

        # non-blocking rule to non-blocking rule and back
        flow = self.get_flow('loop_detection')

        # need to get these again as we just reimported and UUIDs have changed
        group_ruleset = RuleSet.objects.get(flow=flow, label='Group Split A')
        name_ruleset = RuleSet.objects.get(flow=flow, label='Name Split')
        rowan_rule = name_ruleset.get_rules()[0]

        # update our name to rowan so we match the name rule
        self.contact.name = "Rowan"
        self.contact.save()

        # but remove ourselves from the group so we enter the loop
        group_a.contacts.remove(self.contact)

        self.update_destination_no_check(flow, name_ruleset.uuid, group_ruleset.uuid, rule=rowan_rule.uuid)
        self.send_message(flow, "2", assert_reply=False, assert_handle=False)

        # should have an interrupted run
        self.assertEqual(1, FlowRun.objects.filter(contact=self.contact, exit_type=FlowRun.EXIT_TYPE_INTERRUPTED).count())

    def test_decimal_substitution(self):
        flow = self.get_flow('pick_a_number')
        self.assertEqual("You picked 3!", self.send_message(flow, "3"))

    def test_rules_first(self):
        flow = self.get_flow('rules_first')
        self.assertEqual(Flow.RULES_ENTRY, flow.entry_type)
        self.assertEqual("You've got to be kitten me", self.send_message(flow, "cats"))

    def test_numeric_rule_allows_variables(self):
        flow = self.get_flow('numeric_rule_allows_variables')

        zinedine = self.create_contact('Zinedine', '+12065550100')
        zinedine.set_field(self.user, 'age', 25)

        self.assertEqual('Good count', self.send_message(flow, "35", contact=zinedine))

    def test_non_blocking_rule_first(self):

        flow = self.get_flow('non_blocking_rule_first')

        eminem = self.create_contact('Eminem', '+12065550100')
        flow.start(groups=[], contacts=[eminem])
        msg = Msg.objects.filter(direction='O', contact=eminem).first()
        self.assertEqual('Hi there Eminem', msg.text)

        # put a webhook on the rule first and make sure it executes
        ruleset = RuleSet.objects.get(uuid=flow.entry_uuid)
        ruleset.webhook_url = 'http://localhost'
        ruleset.save()

        tupac = self.create_contact('Tupac', '+12065550101')
        flow.start(groups=[], contacts=[tupac])
        msg = Msg.objects.filter(direction='O', contact=tupac).first()
        self.assertEqual('Hi there Tupac', msg.text)

    def test_webhook_rule_first(self):
        flow = self.get_flow('webhook_rule_first')
        tupac = self.create_contact('Tupac', '+12065550101')
        flow.start(groups=[], contacts=[tupac])

        # a message should have been sent
        msg = Msg.objects.filter(direction='O', contact=tupac).first()
        self.assertEqual('Testing this out', msg.text)

    def test_group_dependencies(self):
        self.get_flow('dependencies')
        flow = Flow.objects.filter(name='Dependencies').first()

        group_names = ['Dog Facts', 'Cat Facts', 'Fish Facts', 'Monkey Facts']
        for name in group_names:
            self.assertIsNotNone(flow.group_dependencies.filter(name=name).first(), 'Missing group %s' % name)

        # trim off our first action which is remove from Dog Facts
        update_json = flow.as_json()
        update_json['action_sets'][0]['actions'] = update_json['action_sets'][0]['actions'][1:]
        flow.update(update_json)

        # dog facts should be removed
        self.assertIsNone(flow.group_dependencies.filter(name='Dog Facts').first())

        # but others should still be there
        for name in group_names[1:]:
            self.assertIsNotNone(flow.group_dependencies.filter(name=name).first())

    def test_flow_dependencies(self):

        self.get_flow('dependencies')
        flow = Flow.objects.filter(name='Dependencies').first()

        # we should depend on our child flow
        self.assertIsNotNone(flow.flow_dependencies.filter(name='Child Flow').first())

        # remove our start flow action
        update_json = flow.as_json()
        actionsets = update_json['action_sets']
        actionsets[-1]['actions'] = actionsets[-1]['actions'][0:-1]
        update_json['action_sets'] = actionsets
        flow.update(update_json)

        # now we no longer depend on it
        self.assertIsNone(flow.flow_dependencies.filter(name='Child Flow').first())

    def test_group_uuid_mapping(self):
        flow = self.get_flow('group_split')

        # make sure the groups in our rules exist as expected
        ruleset = RuleSet.objects.filter(label="Member").first()
        rules = ruleset.get_rules_dict()
        group_count = 0
        for rule in rules:
            if rule['test']['type'] == 'in_group':
                group = ContactGroup.user_groups.filter(uuid=rule['test']['test']['uuid']).first()
                self.assertIsNotNone(group)
                group_count += 1
        self.assertEqual(2, group_count)

        self.get_flow('dependencies')
        flow = Flow.objects.filter(name='Dependencies').first()
        group_count = 0
        for actionset in flow.action_sets.all():
            for action in actionset.actions:
                if action['type'] in ('add_group', 'del_group'):
                    for group in action['groups']:
                        if isinstance(group, dict):
                            group_count += 1
                            self.assertIsNotNone(ContactGroup.user_groups.filter(uuid=group['uuid']).first())

        # make sure we found both our group actions
        self.assertEqual(2, group_count)

    def test_group_split(self):
        flow = self.get_flow('group_split')

        rulesets = RuleSet.objects.filter(flow=flow)
        group_count = 0
        for ruleset in rulesets:
            rules = ruleset.get_rules_dict()
            for rule in rules:
                if rule['test']['type'] == 'in_group':
                    group = ContactGroup.user_groups.filter(uuid=rule['test']['test']['uuid']).first()
                    self.assertIsNotNone(group)
                    group_count += 1
        self.assertEqual(2, group_count)

        flow.start_msg_flow([self.contact.id])

        # not in any group
        self.assertEqual(0, ContactGroup.user_groups.filter(contacts__in=[self.contact]).count())

        # add us to Group A
        self.send('add group a')

        self.assertEqual('Awaiting command.', Msg.objects.filter(direction='O').order_by('-created_on').first().text)
        groups = ContactGroup.user_groups.filter(contacts__in=[self.contact])
        self.assertEqual(1, groups.count())
        self.assertEqual('Group A', groups.first().name)

        # now split us on group membership
        self.send('split')
        self.assertEqual('You are in Group A', Msg.objects.filter(direction='O').order_by('-created_on')[1].text)

        # now add us to group b and remove from group a
        self.send("remove group a")
        self.send("add group b")
        self.send('split')
        self.assertEqual('You are in Group B', Msg.objects.filter(direction='O').order_by('-created_on')[1].text)

    def test_media_first_action(self):
        flow = self.get_flow('media_first_action')

        runs = flow.start_msg_flow([self.contact.id])
        self.assertEqual(1, len(runs))

        msg = self.contact.msgs.get()
        self.assertEqual(msg.text, 'Hey')
        self.assertEqual(msg.attachments, [
            "image/jpeg:https://%s/%s" % (settings.AWS_BUCKET_DOMAIN, "attachments/2/53/steps/87d34837-491c-4541-98a1-fa75b52ebccc.jpg")
        ])

    def test_substitution(self):
        flow = self.get_flow('substitution')
        self.contact.name = "Ben Haggerty"
        self.contact.save()

        runs = flow.start_msg_flow([self.contact.id])
        self.assertEqual(1, len(runs))
        self.assertEqual(self.contact.msgs.get().text, 'Hi Ben Haggerty, what is your phone number?')

        self.assertEqual("Thanks, you typed +250788123123", self.send_message(flow, "0788123123"))
        sms = Msg.objects.get(org=flow.org, contact__urns__path="+250788123123")
        self.assertEqual("Hi from Ben Haggerty! Your phone is (206) 555-2020.", sms.text)

    def test_group_send(self):
        # create an inactive group with the same name, to test that this doesn't blow up our import
        group = ContactGroup.get_or_create(self.org, self.admin, "Survey Audience")
        group.is_active = False
        group.save()

        # and create another as well
        ContactGroup.get_or_create(self.org, self.admin, "Survey Audience")

        # this could blow up due to illegal lookup for more than one contact group
        self.get_flow('group_send_flow')

    def test_new_contact(self):
        mother_flow = self.get_flow('mama_mother_registration')
        registration_flow = self.get_flow('mama_registration', dict(NEW_MOTHER_FLOW_ID=mother_flow.pk))

        self.assertEqual("Enter the expected delivery date.", self.send_message(registration_flow, "Judy Pottier"))
        self.assertEqual("Great, thanks for registering the new mother", self.send_message(registration_flow, "31.1.2015"))

        mother = Contact.objects.get(org=self.org, name="Judy Pottier")
        self.assertTrue(mother.get_field_raw('edd').startswith('2015-01-31T'))
        self.assertEqual(mother.get_field_raw('chw_phone'), self.contact.get_urn(TEL_SCHEME).path)
        self.assertEqual(mother.get_field_raw('chw_name'), self.contact.name)

    def test_group_rule_first(self):
        rule_flow = self.get_flow('group_rule_first')

        # start our contact down it
        rule_flow.start([], [self.contact], restart_participants=True)

        # contact should get a message that they didn't match either group
        self.assertLastResponse("You are something else.")

        # add them to the father's group
        self.create_group("Fathers", [self.contact])

        rule_flow.start([], [self.contact], restart_participants=True)
        self.assertLastResponse("You are a father.")

    def test_mother_registration(self):
        mother_flow = self.get_flow('new_mother')
        registration_flow = self.get_flow('mother_registration', dict(NEW_MOTHER_FLOW_ID=mother_flow.pk))
        self.assertEqual(mother_flow.runs.count(), 0)

        self.assertEqual("What is her expected delivery date?", self.send_message(registration_flow, "Judy Pottier"))
        self.assertEqual("What is her phone number?", self.send_message(registration_flow, "31.1.2014"))
        self.assertEqual("Great, you've registered the new mother!", self.send_message(registration_flow, "0788 383 383"))

        # we start both the new mother by @flow.phone and the current contact by its uuid @contact.uuid
        self.assertEqual(mother_flow.runs.count(), 2)

        mother = Contact.from_urn(self.org, "tel:+250788383383")
        self.assertEqual("Judy Pottier", mother.name)
        self.assertTrue(mother.get_field_raw('expected_delivery_date').startswith('2014-01-31T'))
        self.assertEqual("+12065552020", mother.get_field_raw('chw'))
        self.assertTrue(mother.user_groups.filter(name="Expecting Mothers"))

        pain_flow = self.get_flow('pain_flow')
        self.assertEqual("Your CHW will be in contact soon!", self.send_message(pain_flow, "yes", contact=mother))

        chw = self.contact
        sms = Msg.objects.filter(contact=chw).order_by('-created_on')[0]
        self.assertEqual("Please follow up with Judy Pottier, she has reported she is in pain.", sms.text)

    def test_flow_delete(self):
        from temba.campaigns.models import Campaign, CampaignEvent
        flow = self.get_flow('favorites')

        # create a campaign that contains this flow
        friends = self.create_group("Friends", [])
        poll_date = ContactField.get_or_create(self.org, self.admin, 'poll_date', "Poll Date")

        campaign = Campaign.create(self.org, self.admin, Campaign.get_unique_name(self.org, "Favorite Poll"), friends)
        event1 = CampaignEvent.create_flow_event(self.org, self.admin, campaign, poll_date,
                                                 offset=0, unit='D', flow=flow, delivery_hour='13')

        # create a trigger that contains this flow
        trigger = Trigger.objects.create(org=self.org, keyword='poll', flow=flow, trigger_type=Trigger.TYPE_KEYWORD,
                                         created_by=self.admin, modified_by=self.admin)

        # run the flow
        self.assertEqual("Good choice, I like Red too! What is your favorite beer?", self.send_message(flow, "RED"))

        # run it again to completion
        joe = self.create_contact('Joe', '1234')
        self.send_message(flow, "green", contact=joe)
        self.send_message(flow, "primus", contact=joe)
        self.send_message(flow, "Joe", contact=joe)

        # try to remove the flow, not logged in, no dice
        response = self.client.post(reverse('flows.flow_delete', args=[flow.pk]))
        self.assertLoginRedirect(response)

        # login as admin
        self.login(self.admin)
        response = self.client.post(reverse('flows.flow_delete', args=[flow.pk]))
        self.assertEqual(200, response.status_code)

        # flow should no longer be active
        flow.refresh_from_db()
        self.assertFalse(flow.is_active)

        # should still have runs though
        self.assertEqual(flow.runs.count(), 2)

        # but they should all be inactive
        self.assertEqual(flow.runs.filter(is_active=True).count(), 0)

        # one is completed, the other interrupted
        self.assertEqual(flow.runs.filter(exit_type=FlowRun.EXIT_TYPE_INTERRUPTED).count(), 1)
        self.assertEqual(flow.runs.filter(exit_type=FlowRun.EXIT_TYPE_COMPLETED).count(), 1)

        # our campaign event should no longer be active
        event1.refresh_from_db()
        self.assertFalse(event1.is_active)

        # nor should our trigger
        trigger.refresh_from_db()
        self.assertFalse(trigger.is_active)

    def test_flow_delete_with_dependencies(self):
        self.login(self.admin)

        self.get_flow('dependencies')
        self.get_flow('dependencies_voice')
        parent = Flow.objects.filter(name='Dependencies').first()
        child = Flow.objects.filter(name='Child Flow').first()
        voice = Flow.objects.filter(name='Voice Dependencies').first()

        contact_fields = (
            {'key': 'contact_age', 'label': 'Contact Age'},

            # fields based on parent and child references
            {'key': 'top'},
            {'key': 'bottom'},

            # replies
            {'key': 'chw'},

            # url attachemnts
            {'key': 'attachment'},

            # dynamic groups
            {'key': 'cat_breed', 'label': 'Cat Breed'},
            {'key': 'organization'},

            # sending messages
            {'key': 'recipient'},
            {'key': 'message'},

            # sending emails
            {'key': 'email_message', 'label': 'Email Message'},
            {'key': 'subject'},

            # trigger someone else
            {'key': 'other_phone', 'label': 'Other Phone'},

            # rules and localizations
            {'key': 'rule'},
            {'key': 'french_rule', 'label': 'French Rule'},
            {'key': 'french_age', 'label': 'French Age'},
            {'key': 'french_fries', 'label': 'French Fries'},

            # updating contacts
            {'key': 'favorite_cat', 'label': 'Favorite Cat'},
            {'key': 'next_cat_fact', 'label': 'Next Cat Fact'},
            {'key': 'last_cat_fact', 'label': 'Last Cat Fact'},

            # webhook urls
            {'key': 'webhook'},

            # expression splits
            {'key': 'expression_split', 'label': 'Expression Split'},

            # voice says
            {'key': 'play_message', 'label': 'Play Message', 'flow': voice},
            {'key': 'voice_rule', 'label': 'Voice Rule', 'flow': voice},

            # voice plays (recordings)
            {'key': 'voice_recording', 'label': 'Voice Recording', 'flow': voice}
        )

        for field_spec in contact_fields:
            key = field_spec.get('key')
            label = field_spec.get('label', key.capitalize())
            flow = field_spec.get('flow', parent)

            # make sure our field exists after import
            field = ContactField.objects.filter(key=key, label=label).first()
            self.assertIsNotNone(field, "Couldn't find field %s (%s)" % (key, label))

            # and our flow is dependent on us
            self.assertIsNotNone(flow.field_dependencies.filter(key__in=[key]).first(), "Flow is missing dependency on %s (%s)" % (key, label))

        # deleting should fail since the 'Dependencies' flow depends on us
        self.client.post(reverse('flows.flow_delete', args=[child.id]))
        self.assertIsNotNone(Flow.objects.filter(id=child.id, is_active=True).first())

        # remove our child dependency
        parent = Flow.objects.filter(name='Dependencies').first()
        parent.flow_dependencies.remove(child)

        # now the child can be deleted
        self.client.post(reverse('flows.flow_delete', args=[child.id]))
        self.assertIsNotNone(Flow.objects.filter(id=child.id, is_active=False).first())

        # deleting our parent flow should work
        self.client.post(reverse('flows.flow_delete', args=[parent.id]))
        self.assertIsNotNone(Flow.objects.filter(id=parent.id, is_active=False).first())

        # our parent should no longer have any dependencies
        parent.refresh_from_db()
        self.assertEqual(0, parent.field_dependencies.all().count())
        self.assertEqual(0, parent.flow_dependencies.all().count())
        self.assertEqual(0, parent.group_dependencies.all().count())

    def test_start_flow_action(self):
        self.import_file('flow_starts')
        parent = Flow.objects.get(name='Parent Flow')
        child = Flow.objects.get(name='Child Flow')

        contacts = []
        for i in range(10):
            contacts.append(self.create_contact("Fred", '+25078812312%d' % i))

        # start the flow for our contacts
        start = FlowStart.objects.create(flow=parent, created_by=self.admin, modified_by=self.admin)
        for contact in contacts:
            start.contacts.add(contact)
        start.start()

        # all our contacts should have a name of Greg now (set in the child flow)
        for contact in contacts:
            self.assertTrue(FlowRun.objects.filter(flow=parent, contact=contact))
            self.assertTrue(FlowRun.objects.filter(flow=child, contact=contact))
            self.assertEqual("Greg", Contact.objects.get(pk=contact.pk).name)

        # 10 child flow runs should be active waiting for input
        self.assertEqual(FlowRun.objects.filter(flow=child, is_active=True).count(), 10)

        # send some input to complete the child flows
        for contact in contacts:
            msg = self.create_msg(contact=contact, direction='I', text="OK", channel=self.channel)
            msg.handle()

        # all of the runs should now be completed
        self.assertEqual(FlowRun.objects.filter(is_active=False, exit_type=FlowRun.EXIT_TYPE_COMPLETED).count(), 20)

    def test_cross_language_import(self):
        spanish = Language.create(self.org, self.admin, "Spanish", 'spa')
        Language.create(self.org, self.admin, "English", 'eng')

        # import our localized flow into an org with no languages
        self.import_file('multi_language_flow')
        flow = Flow.objects.get(name='Multi Language Flow')

        # even tho we don't have a language, our flow has enough info to function
        self.assertEqual('eng', flow.base_language)

        # now try executing this flow on our org, should use the flow base language
        self.assertEqual('Hello friend! What is your favorite color?',
                         self.send_message(flow, 'start flow', restart_participants=True, initiate_flow=True))

        replies = self.send_message(flow, 'blue')
        self.assertEqual('Thank you! I like blue.', replies[0])
        self.assertEqual('This message was not translated.', replies[1])

        # now add a primary language to our org
        self.org.primary_language = spanish
        self.org.save()

        flow = Flow.objects.get(pk=flow.pk)

        # with our org in spanish, we should get the spanish version
        self.assertEqual('\xa1Hola amigo! \xbfCu\xe1l es tu color favorito?',
                         self.send_message(flow, 'start flow', restart_participants=True, initiate_flow=True))

        self.org.primary_language = None
        self.org.save()
        flow = Flow.objects.get(pk=flow.pk)

        # no longer spanish on our org
        self.assertEqual('Hello friend! What is your favorite color?',
                         self.send_message(flow, 'start flow', restart_participants=True, initiate_flow=True))

        # back to spanish
        self.org.primary_language = spanish
        self.org.save()
        flow = Flow.objects.get(pk=flow.pk)

        # but set our contact's language explicitly should keep us at english
        self.contact.language = 'eng'
        self.contact.save()
        self.assertEqual('Hello friend! What is your favorite color?',
                         self.send_message(flow, 'start flow', restart_participants=True, initiate_flow=True))

    def test_different_expiration(self):
        flow = self.get_flow('favorites')
        self.send_message(flow, "RED", restart_participants=True)

        # get the latest run
        first_run = flow.runs.all()[0]
        first_expires = first_run.expires_on

        # make sure __str__ works
        six.text_type(first_run)

        time.sleep(1)

        # start it again
        self.send_message(flow, "RED", restart_participants=True)

        # previous run should no longer be active
        first_run = FlowRun.objects.get(pk=first_run.pk)
        self.assertFalse(first_run.is_active)

        # expires on shouldn't have changed on it though
        self.assertEqual(first_expires, first_run.expires_on)

        # new run should have a different expires on
        new_run = flow.runs.all().order_by('-expires_on').first()
        self.assertTrue(new_run.expires_on > first_expires)

    def test_flow_expiration_updates(self):
        flow = self.get_flow('favorites')
        self.assertEqual("Good choice, I like Red too! What is your favorite beer?", self.send_message(flow, "RED"))

        # get our current expiration
        run = flow.runs.get()
        self.assertEqual(flow.org, run.org)

        starting_expiration = run.expires_on
        starting_modified = run.modified_on

        time.sleep(1)

        # now fire another messages
        self.assertEqual("Mmmmm... delicious Turbo King. If only they made red Turbo King! Lastly, what is your name?",
                         self.send_message(flow, "turbo"))

        # our new expiration should be later
        run.refresh_from_db()
        self.assertTrue(run.expires_on > starting_expiration)
        self.assertTrue(run.modified_on > starting_modified)

    @also_in_flowserver
    def test_initial_expiration(self):
        flow = self.get_flow('favorites')
        flow.start(groups=[], contacts=[self.contact])

        run = FlowRun.objects.get()
        self.assertTrue(run.expires_on)

    def test_flow_expiration(self):
        flow = self.get_flow('favorites')

        # run our flow like it was 10 mins ago
        with patch.object(timezone, 'now') as mock_now:
            mock_now.side_effect = lambda: datetime.datetime.now(tz=timezone.utc) - timedelta(minutes=10)

            self.assertEqual("Good choice, I like Red too! What is your favorite beer?", self.send_message(flow, "RED"))
            self.assertEqual("Mmmmm... delicious Turbo King. If only they made red Turbo King! Lastly, what is your name?", self.send_message(flow, "turbo"))
            self.assertEqual(1, flow.runs.count())

        # now let's expire them out of the flow prematurely
        flow.expires_after_minutes = 5
        flow.save()

        # this normally gets run on FlowCRUDL.Update
        update_run_expirations_task(flow.id)

        # check that our run is expired
        run = flow.runs.all()[0]
        self.assertFalse(run.is_active)

        # we will be starting a new run now, since the other expired
        self.assertEqual("I don't know that color. Try again.",
                         self.send_message(flow, "Michael Jordan", restart_participants=True))
        self.assertEqual(2, flow.runs.count())

        previous_expiration = run.expires_on
        run.update_expiration(None)
        self.assertTrue(run.expires_on > previous_expiration)

    def test_parsing(self):
        # test a preprocess url
        flow = self.get_flow('preprocess')
        self.assertEqual('http://preprocessor.com/endpoint.php', flow.rule_sets.all().order_by('y')[0].config[RuleSet.CONFIG_WEBHOOK])

    def test_flow_loops(self):
        self.get_flow('flow_loop')
        # this tests two flows that start each other
        flow1 = Flow.objects.get(name='First Flow')
        flow2 = Flow.objects.get(name='Second Flow')

        # start the flow, shouldn't get into a loop, but both should get started
        flow1.start([], [self.contact])

        self.assertTrue(FlowRun.objects.get(flow=flow1, contact=self.contact))
        self.assertTrue(FlowRun.objects.get(flow=flow2, contact=self.contact))

    def test_ruleset_loops(self):
        self.import_file('ruleset_loop')

        flow1 = Flow.objects.all()[1]
        flow2 = Flow.objects.all()[0]

        # start the flow, should not get into a loop
        flow1.start([], [self.contact])

        self.assertTrue(FlowRun.objects.get(flow=flow1, contact=self.contact))
        self.assertTrue(FlowRun.objects.get(flow=flow2, contact=self.contact))

    def test_parent_child(self):
        from temba.campaigns.models import Campaign, CampaignEvent, EventFire

        favorites = self.get_flow('favorites')

        # do a dry run once so that the groups and fields get created
        group = self.create_group("Campaign", [])
        field = ContactField.get_or_create(self.org, self.admin, "campaign_date", "Campaign Date")

        # tests that a contact is properly updated when a child flow is called
        child = self.get_flow('child')
        parent = self.get_flow('parent', substitutions=dict(CHILD_ID=child.id))

        # create a campaign with a single event
        campaign = Campaign.create(self.org, self.admin, "Test Campaign", group)
        CampaignEvent.create_flow_event(self.org, self.admin, campaign, relative_to=field,
                                        offset=10, unit='W', flow=favorites)

        self.assertEqual("Added to campaign.", self.send_message(parent, "start", initiate_flow=True))

        # should have one event scheduled for this contact
        self.assertTrue(EventFire.objects.filter(contact=self.contact))

    def test_priority(self):
        self.get_flow('priorities')
        joe = self.create_contact("joe", "112233")

        parent = Flow.objects.get(name='Priority Parent')
        parent.start([], [self.contact, joe])

        self.assertEqual(8, Msg.objects.filter(direction='O').count())
        self.assertEqual(2, Broadcast.objects.all().count())

        # all messages so far are low prioirty as well because of no inbound
        self.assertEqual(8, Msg.objects.filter(direction='O', high_priority=False).count())

        # send a message in to become high priority
        self.send("make me high priority por favor")

        # each flow sends one message to cleanup
        self.assertEqual(11, Msg.objects.filter(direction='O').count())
        self.assertEqual(3, Msg.objects.filter(high_priority=True).count())

        # we've completed three flows, but joe is still at it
        self.assertEqual(5, FlowRun.objects.all().count())
        self.assertEqual(3, FlowRun.objects.filter(contact=self.contact, exit_type=FlowRun.EXIT_TYPE_COMPLETED).count())
        self.assertEqual(2, FlowRun.objects.filter(contact=joe, exit_type=None).count())

    def test_priority_single_contact(self):
        # try running with a single contact, we dont create broadcasts for a single
        # contact, but the messages should still be low prioirty
        self.get_flow('priorities')
        parent = Flow.objects.get(name='Priority Parent')
        parent.start([], [self.contact], restart_participants=True)

        self.assertEqual(4, Msg.objects.count())
        self.assertEqual(0, Broadcast.objects.count())
        self.assertEqual(4, Msg.objects.filter(high_priority=False).count())

    def test_priority_keyword_trigger(self):
        self.get_flow('priorities')

        # now lets kick a flow off with a message trigger
        self.send("priority")

        # now we should have two runs
        self.assertEqual(2, FlowRun.objects.count())

        # since the contact started us, all our messages should be high priority
        self.assertEqual(0, Msg.objects.filter(high_priority=False).count())
        self.assertEqual(4, Msg.objects.filter(direction='O', high_priority=True).count())

    @also_in_flowserver
    def test_subflow(self):
        """
        Tests that a subflow can be called and the flow is handed back to the parent
        """
        self.get_flow('subflow')
        parent = Flow.objects.get(org=self.org, name='Parent Flow')
        parent_prompt = ActionSet.objects.get(flow=parent, y=0)
        kind_ruleset = RuleSet.objects.get(flow=parent, label='kind')
        subflow_ruleset = RuleSet.objects.get(flow=parent, ruleset_type='subflow')
        subflow_reply = ActionSet.objects.get(flow=parent, y=386, x=341)

        parent.start(groups=[], contacts=[self.contact, self.create_contact("joe", "001122")], restart_participants=True)

        msg = Msg.objects.filter(contact=self.contact).first()
        self.assertEqual("This is a parent flow. What would you like to do?", msg.text)
        self.assertFalse(msg.high_priority)

        # this should launch the child flow
        self.send_message(parent, "color", assert_reply=False)

        msg = Msg.objects.filter(contact=self.contact).order_by('-created_on').first()
        self.assertEqual("What color do you like?", msg.text)
        self.assertTrue(msg.high_priority)

        # should have a run for each flow
        parent_run, child_run = FlowRun.objects.filter(contact=self.contact, is_active=True).order_by('created_on')

        # should have made it to the subflow ruleset on the parent flow
        parent_path = parent_run.path
        self.assertEqual(len(parent_path), 3)
        self.assertEqual(parent_path[0]['node_uuid'], parent_prompt.uuid)
        self.assertEqual(parent_path[0]['exit_uuid'], parent_prompt.exit_uuid)
        self.assertEqual(parent_path[1]['node_uuid'], kind_ruleset.uuid)
        self.assertEqual(parent_path[1]['exit_uuid'], kind_ruleset.get_rules()[0].uuid)
        self.assertEqual(parent_path[2]['node_uuid'], subflow_ruleset.uuid)
        self.assertNotIn('exit_uuid', parent_path[2])

        # complete the child flow
        self.send('Red')

        child_run.refresh_from_db()
        self.assertFalse(child_run.is_active)

        # now we are back to a single active flow, the parent
        parent_run.refresh_from_db()
        self.assertTrue(parent_run.is_active)

        parent_path = parent_run.path
        self.assertEqual(len(parent_path), 5)
        self.assertEqual(parent_path[2]['node_uuid'], subflow_ruleset.uuid)
        self.assertEqual(parent_path[2]['exit_uuid'], subflow_ruleset.get_rules()[0].uuid)
        self.assertEqual(parent_path[3]['node_uuid'], subflow_reply.uuid)
        self.assertEqual(parent_path[3]['exit_uuid'], subflow_reply.exit_uuid)
        self.assertEqual(parent_path[4]['node_uuid'], kind_ruleset.uuid)
        self.assertNotIn('exit_uuid', parent_path[4])

        # we should have a new outbound message from the the parent flow
        msg = Msg.objects.filter(contact=self.contact, direction='O').order_by('-created_on').first()
        self.assertEqual("Complete: You picked Red.", msg.text)

        # should only have one response msg
        self.assertEqual(1, Msg.objects.filter(text='Complete: You picked Red.', contact=self.contact, direction='O').count())

    def test_subflow_interrupted(self):
        self.get_flow('subflow')
        parent = Flow.objects.get(org=self.org, name='Parent Flow')

        parent.start(groups=[], contacts=[self.contact], restart_participants=True)
        self.send_message(parent, "color", assert_reply=False)

        # we should now have two active flows
        runs = FlowRun.objects.filter(contact=self.contact, is_active=True).order_by('-created_on')
        self.assertEqual(2, runs.count())

        # now interrupt the child flow
        run = FlowRun.objects.filter(contact=self.contact, is_active=True).order_by('-created_on').first()
        FlowRun.bulk_exit(FlowRun.objects.filter(id=run.id), FlowRun.EXIT_TYPE_INTERRUPTED)

        # all flows should have finished
        self.assertEqual(0, FlowRun.objects.filter(contact=self.contact, is_active=True).count())

        # and the parent should not have resumed, so our last message was from our subflow
        msg = Msg.objects.all().order_by('-created_on').first()
        self.assertEqual('What color do you like?', msg.text)

    def test_subflow_expired(self):
        self.get_flow('subflow')
        parent = Flow.objects.get(org=self.org, name='Parent Flow')

        parent.start(groups=[], contacts=[self.contact], restart_participants=True)
        self.send_message(parent, "color", assert_reply=False)

        # we should now have two active flows
        runs = FlowRun.objects.filter(contact=self.contact, is_active=True).order_by('-created_on')
        self.assertEqual(2, runs.count())

        # make sure the parent run expires later than the child
        child_run = runs[0]
        parent_run = runs[1]
        self.assertTrue(parent_run.expires_on > child_run.expires_on)

        # now expire out of the child flow
        run = FlowRun.objects.filter(contact=self.contact, is_active=True).order_by('-created_on').first()
        FlowRun.bulk_exit(FlowRun.objects.filter(id=run.id), FlowRun.EXIT_TYPE_EXPIRED)

        # all flows should have finished
        self.assertEqual(0, FlowRun.objects.filter(contact=self.contact, is_active=True).count())

        # and should follow the expiration route
        msg = Msg.objects.all().order_by('-created_on').first()
        self.assertEqual("You expired out of the subflow", msg.text)

    def test_subflow_updates(self):

        self.get_flow('subflow')
        parent = Flow.objects.get(org=self.org, name='Parent Flow')

        parent.start(groups=[], contacts=[self.contact], restart_participants=True)
        self.send_message(parent, "color", assert_reply=False)

        # we should now have two active flows
        self.assertEqual(2, FlowRun.objects.filter(contact=self.contact, is_active=True).count())

        run = FlowRun.objects.filter(flow=parent).first()
        starting_expiration = run.expires_on
        starting_modified = run.modified_on

        time.sleep(1)

        # send a message that will keep us in the child flow
        self.send('no match')

        # our new expiration should be later
        run.refresh_from_db()
        self.assertTrue(run.expires_on > starting_expiration)
        self.assertTrue(run.modified_on > starting_modified)

    def test_subflow_no_interaction(self):
        self.get_flow('subflow_no_pause')
        parent = Flow.objects.get(org=self.org, name='Flow A')
        parent.start(groups=[], contacts=[self.contact], restart_participants=True)

        # check we got our three messages, the third populated by the child, but sent form the parent
        msgs = Msg.objects.order_by('created_on')
        self.assertEqual(5, msgs.count())
        self.assertEqual(msgs[0].text, "Message 1")
        self.assertEqual(msgs[1].text, "Message 2/4")
        self.assertEqual(msgs[2].text, "Message 3 (FLOW B)")
        self.assertEqual(msgs[3].text, "Message 2/4")
        self.assertEqual(msgs[4].text, "Message 5 (FLOW B)")

    def test_subflow_resumes(self):
        self.get_flow('subflow_resumes')

        self.send("radio")

        # upon starting, we see our starting message, then our language subflow question
        msgs = Msg.objects.order_by('created_on')
        self.assertEqual(3, msgs.count())
        self.assertEqual('radio', msgs[0].text)
        self.assertEqual('Welcome message.', msgs[1].text)
        self.assertEqual('What language? English or French?', msgs[2].text)

        runs = FlowRun.objects.filter(is_active=True).order_by('created_on')
        self.assertEqual(2, runs.count())
        self.assertEqual('Radio Show Poll', runs[0].flow.name)
        self.assertEqual('Ask Language', runs[1].flow.name)

        # choose english as our language
        self.send('english')

        # we bounce back to the parent flow, and then into the gender flow
        msgs = Msg.objects.order_by('created_on')
        self.assertEqual(5, msgs.count())
        self.assertEqual('english', msgs[3].text)
        self.assertEqual('Are you Male or Female?', msgs[4].text)

        # still two runs, except a different subflow is active now
        runs = FlowRun.objects.filter(is_active=True).order_by('created_on')
        self.assertEqual(2, runs.count())
        self.assertEqual('Radio Show Poll', runs[0].flow.name)
        self.assertEqual('Ask Gender', runs[1].flow.name)

        # choose our gender
        self.send('male')

        # back in the parent flow, asking our first parent question
        msgs = Msg.objects.order_by('created_on')
        self.assertEqual(7, msgs.count())
        self.assertEqual('male', msgs[5].text)
        self.assertEqual('Have you heard of show X? Yes or No?', msgs[6].text)

        # now only one run should be active, our parent
        runs = FlowRun.objects.filter(is_active=True).order_by('created_on')
        self.assertEqual(1, runs.count())
        self.assertEqual('Radio Show Poll', runs[0].flow.name)

        # let's start over, we should pass right through language and gender
        self.send("radio")

        msgs = Msg.objects.order_by('created_on')
        self.assertEqual(10, msgs.count())
        self.assertEqual('radio', msgs[7].text)
        self.assertEqual('Welcome message.', msgs[8].text)
        self.assertEqual('Have you heard of show X? Yes or No?', msgs[9].text)

    def test_subflow_with_startflow(self):
        self.get_flow('subflow_with_startflow')

        parent = Flow.objects.get(name='Subflow 1')
        parent.start(groups=[], contacts=[self.contact])

    def test_trigger_flow_complete(self):
        contact2 = self.create_contact(name='Jason Tatum', number='+250788123123')

        self.get_flow('trigger_flow_complete', dict(contact2_uuid=contact2.uuid))

        parent = Flow.objects.get(org=self.org, name='Flow A')

        parent.start(groups=[], contacts=[self.contact], restart_participants=True)

        self.assertEqual(1, FlowRun.objects.filter(contact=self.contact).count())
        self.assertEqual(1, FlowRun.objects.filter(contact=contact2).count())

        run1 = FlowRun.objects.filter(contact=self.contact).first()
        run2 = FlowRun.objects.filter(contact=contact2).first()

        self.assertEqual(run1.exit_type, FlowRun.EXIT_TYPE_COMPLETED)
        self.assertFalse(run1.is_active)

        self.assertEqual(run2.parent.id, run1.id)

    def test_translations_rule_first(self):

        # import a rule first flow that already has language dicts
        # this rule first does not depend on @step.value for the first rule, so
        # it can be evaluated right away
        flow = self.get_flow('group_membership')

        # create the language for our org
        language = Language.create(self.org, flow.created_by, "English", 'eng')
        self.org.primary_language = language
        self.org.save()

        # start our flow without a message (simulating it being fired by a trigger or the simulator)
        # this will evaluate requires_step() to make sure it handles localized flows
        runs = flow.start_msg_flow([self.contact.id])
        self.assertEqual(1, len(runs))
        self.assertEqual(self.contact.msgs.get().text, 'You are not in the enrolled group.')

        enrolled_group = ContactGroup.create_static(self.org, self.user, "Enrolled")
        enrolled_group.update_contacts(self.user, [self.contact], True)

        runs_started = flow.start_msg_flow([self.contact.id])
        self.assertEqual(1, len(runs_started))

        msgs = list(self.contact.msgs.order_by('id'))
        self.assertEqual(len(msgs), 2)
        self.assertEqual(msgs[1].text, 'You are in the enrolled group.')

    def test_translations(self):

        favorites = self.get_flow('favorites')

        # create a new language on the org
        self.org.set_languages(self.admin, ['eng'], 'eng')

        # everything should work as normal with our flow
        self.assertEqual("What is your favorite color?", self.send_message(favorites, "favorites", initiate_flow=True))
        json_dict = favorites.as_json()
        reply = json_dict['action_sets'][0]['actions'][0]

        # we should be a normal unicode response
        self.assertTrue(isinstance(reply['msg'], dict))
        self.assertTrue(isinstance(reply['msg']['base'], six.text_type))

        # now our replies are language dicts
        json_dict = favorites.as_json()
        reply = json_dict['action_sets'][1]['actions'][0]
        self.assertEqual('Good choice, I like @flow.color.category too! What is your favorite beer?', reply['msg']['base'])

        # now interact with the flow and make sure we get an appropriate response
        FlowRun.objects.all().delete()

        self.assertEqual("What is your favorite color?", self.send_message(favorites, "favorites", initiate_flow=True))
        self.assertEqual("Good choice, I like Red too! What is your favorite beer?", self.send_message(favorites, "RED"))

        # now let's add a second language
        self.org.set_languages(self.admin, ['eng', 'tlh'], 'eng')

        # update our initial message
        initial_message = json_dict['action_sets'][0]['actions'][0]
        initial_message['msg']['tlh'] = 'Kikshtik derklop?'
        json_dict['action_sets'][0]['actions'][0] = initial_message

        # and the first response
        reply['msg']['tlh'] = 'Katishklick Shnik @flow.color.category Errrrrrrrklop'
        json_dict['action_sets'][1]['actions'][0] = reply

        # save the changes
        favorites.update(json_dict, self.admin)

        # should get org primary language (english) since our contact has no preferred language
        FlowRun.objects.all().delete()
        self.assertEqual("What is your favorite color?", self.send_message(favorites, "favorite", initiate_flow=True))
        self.assertEqual("Good choice, I like Red too! What is your favorite beer?", self.send_message(favorites, "RED"))

        # now set our contact's preferred language to klingon
        FlowRun.objects.all().delete()
        self.contact.language = 'tlh'
        self.contact.save()

        self.assertEqual("Kikshtik derklop?", self.send_message(favorites, "favorite", initiate_flow=True))
        self.assertEqual("Katishklick Shnik Red Errrrrrrrklop", self.send_message(favorites, "RED"))

        # we support localized rules and categories as well
        json_dict = favorites.as_json()
        rule = json_dict['rule_sets'][0]['rules'][0]
        self.assertTrue(isinstance(rule['test']['test'], dict))
        rule['test']['test']['tlh'] = 'klerk'
        rule['category']['tlh'] = 'Klerkistikloperopikshtop'
        json_dict['rule_sets'][0]['rules'][0] = rule
        favorites.update(json_dict, self.admin)

        FlowRun.objects.all().delete()
        self.assertEqual("Katishklick Shnik Klerkistikloperopikshtop Errrrrrrrklop", self.send_message(favorites, "klerk"))

        # test the send action as well
        json_dict = favorites.as_json()
        action = json_dict['action_sets'][1]['actions'][0]
        action['type'] = 'send'
        action['contacts'] = [dict(uuid=self.contact.uuid)]
        action['groups'] = []
        action['variables'] = []
        json_dict['action_sets'][1]['actions'][0] = action
        favorites.update(json_dict, self.admin)

        FlowRun.objects.all().delete()
        self.send_message(favorites, "klerk", assert_reply=False)
        sms = Msg.objects.filter(contact=self.contact).order_by('-pk')[0]
        self.assertEqual("Katishklick Shnik Klerkistikloperopikshtop Errrrrrrrklop", sms.text)

        # test dirty json
        json_dict = favorites.as_json()

        # boolean values in our language dict shouldn't blow up
        json_dict['action_sets'][0]['actions'][0]['msg']['updated'] = True
        json_dict['action_sets'][0]['actions'][0]['msg']['tlh'] = 'Bleck'

        # boolean values in our rule dict shouldn't blow up
        rule = json_dict['rule_sets'][0]['rules'][0]
        rule['category']['updated'] = True

        favorites.update(json_dict)

        favorites = Flow.objects.get(pk=favorites.pk)
        json_dict = favorites.as_json()
        action = self.assertEqual('Bleck', json_dict['action_sets'][0]['actions'][0]['msg']['tlh'])

        # test that simulation takes language into account
        self.login(self.admin)
        simulate_url = reverse('flows.flow_simulate', args=[favorites.pk])
        response = self.client.post(simulate_url, json.dumps(dict(has_refresh=True, version="1")), content_type="application/json").json()
        self.assertEqual('What is your favorite color?', response['messages'][1]['text'])

        # now lets toggle the UI to Klingon and try the same thing
        simulate_url = "%s?lang=tlh" % reverse('flows.flow_simulate', args=[favorites.pk])
        response = self.client.post(simulate_url, json.dumps(dict(has_refresh=True, version="1")), content_type="application/json").json()
        self.assertEqual('Bleck', response['messages'][1]['text'])

    def test_interrupted_state(self):
        self.channel.delete()
        # Create a USSD channel type to test USSDSession.INTERRUPTED status
        self.channel = Channel.create(self.org, self.user, 'RW', 'JNU', None, '+250788123123',
                                      role=Channel.ROLE_USSD)

        flow = self.get_flow('ussd_interrupt_example')

        # start the flow, check if we are interrupted yet
        flow.start([], [self.contact])
        self.assertFalse(FlowRun.objects.get(contact=self.contact).is_interrupted())

        USSDSession.handle_incoming(channel=self.channel, urn=self.contact.get_urn().path, date=timezone.now(),
                                    external_id="12341231", status=USSDSession.INTERRUPTED)

        run = FlowRun.objects.get(contact=self.contact)

        # as the example flow has an interrupt state connected to a valid destination,
        # the flow will go on and reach the destination
        self.assertFalse(run.is_interrupted())

        # the contact should have been added to the "Interrupted" group as flow step describes
        interrupted_group = ContactGroup.user_groups.get(name='Interrupted')
        self.assertTrue(interrupted_group.contacts.filter(id=run.contact.id).exists())

    def test_empty_interrupt_state(self):
        self.channel.delete()
        # Create a USSD channel type to test USSDSession.INTERRUPTED status
        self.channel = Channel.create(self.org, self.user, 'RW', 'JNU', None, '+250788123123',
                                      role=Channel.ROLE_USSD)

        flow = self.get_flow('ussd_interrupt_example')

        # disconnect action from interrupt state
        ruleset = flow.rule_sets.first()
        rules = ruleset.get_rules()
        interrupt_rule = next(rule for rule in rules if isinstance(rule.test, InterruptTest))
        interrupt_rule.destination = None
        interrupt_rule.destination_type = None
        ruleset.set_rules(rules)
        ruleset.save()

        # start the flow, check if we are interrupted yet
        flow.start([], [self.contact])

        self.assertFalse(FlowRun.objects.get(contact=self.contact).is_interrupted())

        USSDSession.handle_incoming(channel=self.channel, urn=self.contact.get_urn().path, date=timezone.now(),
                                    external_id="12341231", status=USSDSession.INTERRUPTED)

        run = FlowRun.objects.get(contact=self.contact)

        # the interrupt state is empty, it should interrupt the flow
        self.assertTrue(run.is_interrupted())

        # double check that the disconnected action wasn't run
        interrupted_group = ContactGroup.user_groups.get(name='Interrupted')
        self.assertFalse(interrupted_group.contacts.filter(id=run.contact.id).exists())

    def test_airtime_flow(self):
        flow = self.get_flow('airtime')

        contact_urn = self.contact.get_urn(TEL_SCHEME)

        airtime_event = AirtimeTransfer.objects.create(org=self.org, status=AirtimeTransfer.SUCCESS, amount=10, contact=self.contact,
                                                       recipient=contact_urn.path, created_by=self.admin, modified_by=self.admin)

        with patch('temba.flows.models.AirtimeTransfer.trigger_airtime_event') as mock_trigger_event:
            mock_trigger_event.return_value = airtime_event

            runs = flow.start_msg_flow([self.contact.id])
            self.assertEqual(1, len(runs))
            self.assertEqual(self.contact.msgs.get().text, 'Message complete')

            airtime_event.status = AirtimeTransfer.FAILED
            airtime_event.save()

            mock_trigger_event.return_value = airtime_event

            runs = flow.start_msg_flow([self.contact.id])
            self.assertEqual(1, len(runs))

            msgs = list(self.contact.msgs.order_by('id'))
            self.assertEqual(len(msgs), 2)
            self.assertEqual(msgs[1].text, 'Message failed')

    @patch('temba.airtime.models.AirtimeTransfer.post_transferto_api_response')
    def test_airtime_trigger_event(self, mock_post_transferto):
        mock_post_transferto.side_effect = [MockResponse(200, "error_code=0\r\ncurrency=USD\r\n"),
                                            MockResponse(200, "error_code=0\r\nerror_txt=\r\ncountry=United States\r\n"
                                                              "product_list=0.25,0.5,1,1.5\r\n"
                                                              "local_info_value_list=5,10,20,30\r\n"),
                                            MockResponse(200, "error_code=0\r\nerror_txt=\r\nreserved_id=234\r\n"),
                                            MockResponse(200, "error_code=0\r\nerror_txt=\r\n")]

        self.org.connect_transferto('mylogin', 'api_token', self.admin)
        self.org.refresh_transferto_account_currency()

        flow = self.get_flow('airtime')
        runs = flow.start_msg_flow([self.contact.id])
        self.assertEqual(1, len(runs))
        self.assertEqual(self.contact.msgs.get().text, 'Message complete')

        self.assertEqual(1, AirtimeTransfer.objects.all().count())
        airtime = AirtimeTransfer.objects.all().first()
        self.assertEqual(airtime.status, AirtimeTransfer.SUCCESS)
        self.assertEqual(airtime.contact, self.contact)
        self.assertEqual(airtime.message, "Airtime Transferred Successfully")
        self.assertEqual(mock_post_transferto.call_count, 4)
        mock_post_transferto.reset_mock()

        mock_post_transferto.side_effect = [MockResponse(200, "error_code=0\r\nerror_txt=\r\ncountry=Rwanda\r\n"
                                                              "product_list=0.25,0.5,1,1.5\r\n"
                                                              "local_info_value_list=5,10,20,30\r\n"),
                                            MockResponse(200, "error_code=0\r\nerror_txt=\r\nreserved_id=234\r\n"),
                                            MockResponse(200, "error_code=0\r\nerror_txt=\r\n")]

        runs = flow.start_msg_flow([self.contact.id])
        self.assertEqual(1, len(runs))
        msgs = list(self.contact.msgs.order_by('id'))
        self.assertEqual(msgs[1].text, 'Message failed')

        self.assertEqual(2, AirtimeTransfer.objects.all().count())
        airtime = AirtimeTransfer.objects.all().last()
        self.assertEqual(airtime.status, AirtimeTransfer.FAILED)
        self.assertEqual(airtime.message, "Error transferring airtime: Failed by invalid amount "
                                          "configuration or missing amount configuration for Rwanda")

        self.assertEqual(mock_post_transferto.call_count, 1)
        mock_post_transferto.reset_mock()

        mock_post_transferto.side_effect = [MockResponse(200, "error_code=0\r\nerror_txt=\r\ncountry=United States\r\n"
                                                              "product_list=0.25,0.5,1,1.5\r\n"
                                                              "local_info_value_list=5,10,20,30\r\n"),
                                            MockResponse(200, "error_code=0\r\nerror_txt=\r\nreserved_id=234\r\n"),
                                            MockResponse(200, "error_code=0\r\nerror_txt=\r\n")]

        test_contact = Contact.get_test_contact(self.admin)

        runs = flow.start_msg_flow([test_contact.id])
        self.assertEqual(1, len(runs))

        # no saved airtime event in DB
        self.assertEqual(2, AirtimeTransfer.objects.all().count())
        self.assertEqual(mock_post_transferto.call_count, 0)

        contact2 = self.create_contact(name='Bismack Biyombo', number='+250788123123', twitter='biyombo')
        self.assertEqual(contact2.get_urn().path, 'biyombo')

        runs = flow.start_msg_flow([contact2.id])
        self.assertEqual(1, len(runs))
        self.assertEqual(1, contact2.msgs.all().count())
        self.assertEqual('Message complete', contact2.msgs.all()[0].text)

        self.assertEqual(3, AirtimeTransfer.objects.all().count())
        airtime = AirtimeTransfer.objects.all().last()
        self.assertEqual(airtime.status, AirtimeTransfer.SUCCESS)
        self.assertEqual(airtime.recipient, '+250788123123')
        self.assertNotEqual(airtime.recipient, 'biyombo')
        self.assertEqual(mock_post_transferto.call_count, 3)
        mock_post_transferto.reset_mock()

        self.org.remove_transferto_account(self.admin)

        mock_post_transferto.side_effect = [MockResponse(200, "error_code=0\r\nerror_txt=\r\ncountry=United States\r\n"
                                                              "product_list=0.25,0.5,1,1.5\r\n"
                                                              "local_info_value_list=5,10,20,30\r\n"),
                                            MockResponse(200, "error_code=0\r\nerror_txt=\r\nreserved_id=234\r\n"),
                                            MockResponse(200, "error_code=0\r\nerror_txt=\r\n")]

        runs = flow.start_msg_flow([self.contact.id])
        self.assertEqual(1, len(runs))

        msgs = list(self.contact.msgs.order_by('id'))
        self.assertEqual(msgs[2].text, 'Message failed')

        self.assertEqual(4, AirtimeTransfer.objects.all().count())
        airtime = AirtimeTransfer.objects.all().last()
        self.assertEqual(airtime.status, AirtimeTransfer.FAILED)
        self.assertEqual(airtime.contact, self.contact)
        self.assertEqual(airtime.message, "Error transferring airtime: No transferTo Account connected to "
                                          "this organization")

        # we never call TransferTo API if no accoutnis connected
        self.assertEqual(mock_post_transferto.call_count, 0)
        mock_post_transferto.reset_mock()

    @patch('temba.flows.models.FlowRun.PATH_MAX_STEPS', 8)
    def test_run_path(self):
        flow = self.get_flow('favorites')
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

        self.assertEqual([(p['node_uuid'], p.get('exit_uuid')) for p in path], [
            (colorPrompt.uuid, colorPrompt.exit_uuid),
            (colorRuleSet.uuid, otherRule.uuid),
            (tryAgainPrompt.uuid, tryAgainPrompt.exit_uuid),
            (colorRuleSet.uuid, otherRule.uuid),
            (tryAgainPrompt.uuid, tryAgainPrompt.exit_uuid),
            (colorRuleSet.uuid, otherRule.uuid),
            (tryAgainPrompt.uuid, tryAgainPrompt.exit_uuid),
            (colorRuleSet.uuid, None),
        ])
        self.assertEqual(str(run.current_node_uuid), colorRuleSet.uuid)

        self.send_message(flow, "red")

        run.refresh_from_db()
        path = run.path

        self.assertEqual([(p['node_uuid'], p.get('exit_uuid')) for p in path], [
            (tryAgainPrompt.uuid, tryAgainPrompt.exit_uuid),
            (colorRuleSet.uuid, otherRule.uuid),
            (tryAgainPrompt.uuid, tryAgainPrompt.exit_uuid),
            (colorRuleSet.uuid, otherRule.uuid),
            (tryAgainPrompt.uuid, tryAgainPrompt.exit_uuid),
            (colorRuleSet.uuid, redRule.uuid),
            (beerPrompt.uuid, beerPrompt.exit_uuid),
            (beerRuleSet.uuid, None),
        ])
        self.assertEqual(str(run.current_node_uuid), beerRuleSet.uuid)


class FlowMigrationTest(FlowFileTest):

    def test_is_before_version(self):

        # works with numbers
        self.assertTrue(Flow.is_before_version(5, 6))

        self.assertTrue(Flow.is_before_version("10", "10.1"))
        self.assertFalse(Flow.is_before_version("10", "9"))

        # unknown versions return false
        self.assertFalse(Flow.is_before_version("3.1", "5"))
        self.assertFalse(Flow.is_before_version("200", "5"))
        self.assertFalse(Flow.is_before_version("3.1", "3.5"))

        self.assertFalse(Flow.is_before_version(get_current_export_version(), 10))

    def migrate_flow(self, flow, to_version=None):

        if not to_version:
            to_version = get_current_export_version()

        flow_json = flow.as_json()
        if Flow.is_before_version(flow.version_number, "6"):
            revision = flow.revisions.all().order_by('-revision').first()
            flow_json = dict(definition=flow_json, flow_type=flow.flow_type,
                             expires=flow.expires_after_minutes, id=flow.pk,
                             revision=revision.revision if revision else 1)

        flow_json = FlowRevision.migrate_definition(flow_json, flow, to_version=to_version)
        if 'definition' in flow_json:
            flow_json = flow_json['definition']

        flow.update(flow_json)
        return Flow.objects.get(pk=flow.pk)

    def test_migrate_with_flow_user(self):
        flow = Flow.create_instance(dict(name='Favorites', org=self.org,
                                         created_by=self.admin, modified_by=self.admin,
                                         saved_by=self.admin, version_number=7))

        flow_json = self.get_flow_json('favorites')
        FlowRevision.create_instance(dict(flow=flow, definition=flow_json,
                                          spec_version=7, revision=1,
                                          created_by=self.admin, modified_by=self.admin))

        old_json = flow.as_json()

        saved_on = flow.saved_on
        modified_on = flow.modified_on
        flow.ensure_current_version()
        flow.refresh_from_db()

        # system migration should not affect our saved_on even tho we are modified
        self.assertNotEqual(modified_on, flow.modified_on)
        self.assertEqual(saved_on, flow.saved_on)

        # but should still create a revision using the flow user
        self.assertEqual(1, flow.revisions.filter(created_by=get_flow_user(self.org)).count())

        # should see the system user on our revision json
        self.login(self.admin)
        response = self.client.get(reverse('flows.flow_revisions', args=[flow.id]))
        self.assertContains(response, 'System Update')
        self.assertEqual(2, len(response.json()))

        # attempt to save with old json, no bueno
        response = self.client.post(reverse('flows.flow_json', args=[flow.id]), data=json.dumps(old_json), content_type='application/json')

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json(), {
            'description': 'rapidpro_flow is currently editing this Flow. Your changes will not be saved until you refresh your browser.',
            'status': u'failure'
        })

        # now refresh and save a new version
        flow.update(flow.as_json(), user=self.admin)
        self.assertEqual(3, flow.revisions.all().count())
        self.assertEqual(1, flow.revisions.filter(created_by=get_flow_user(self.org)).count())

    def test_migrate_malformed_single_message_flow(self):

        flow = Flow.create_instance(dict(name='Single Message Flow', org=self.org,
                                         created_by=self.admin, modified_by=self.admin,
                                         saved_by=self.admin, version_number=3))

        flow_json = self.get_flow_json('malformed_single_message')['definition']

        FlowRevision.create_instance(dict(flow=flow, definition=flow_json,
                                          spec_version=3, revision=1,
                                          created_by=self.admin, modified_by=self.admin))

        flow.ensure_current_version()
        flow_json = flow.as_json()

        self.assertEqual(len(flow_json['action_sets']), 1)
        self.assertEqual(len(flow_json['rule_sets']), 0)
        self.assertEqual(flow_json['version'], get_current_export_version())
        self.assertEqual(flow_json['metadata']['revision'], 2)

    def test_migration_string_group(self):
        flow = Flow.create_instance(dict(name='String group', org=self.org,
                                         created_by=self.admin, modified_by=self.admin,
                                         saved_by=self.admin, version_number=3))

        flow_json = self.get_flow_json('string_group')['definition']

        FlowRevision.create_instance(dict(flow=flow, definition=flow_json,
                                          spec_version=3, revision=1,
                                          created_by=self.admin, modified_by=self.admin))

        flow.ensure_current_version()
        flow_json = flow.as_json()

        self.assertEqual(len(flow_json['action_sets']), 1)
        self.assertEqual("The Funky Bunch", flow_json['action_sets'][0]['actions'][0]['groups'][0]['name'])
        self.assertTrue("The Funky Bunch", flow_json['action_sets'][0]['actions'][0]['groups'][0]['uuid'])
        self.assertEqual("@contact.name", flow_json['action_sets'][0]['actions'][0]['groups'][1])

    def test_update_dependencies_on_old_version(self):
        flow_json = self.get_flow_json('call_me_maybe')['definition']
        flow = Flow.create_instance(dict(name='Call Me Maybe', org=self.org,
                                         created_by=self.admin, modified_by=self.admin,
                                         saved_by=self.admin, version_number=3))

        FlowRevision.create_instance(dict(flow=flow, definition=flow_json,
                                          spec_version=3, revision=1,
                                          created_by=self.admin, modified_by=self.admin))

        # updating our dependencies should ensure the current version
        flow.update_dependencies()

        self.assertEqual(flow.version_number, get_current_export_version())

    def test_ensure_current_version(self):
        flow_json = self.get_flow_json('call_me_maybe')['definition']
        flow = Flow.create_instance(dict(name='Call Me Maybe', org=self.org,
                                         created_by=self.admin, modified_by=self.admin,
                                         saved_by=self.admin, version_number=3))

        FlowRevision.create_instance(dict(flow=flow, definition=flow_json,
                                          spec_version=3, revision=1,
                                          created_by=self.admin, modified_by=self.admin))

        # now make sure we are on the latest version
        flow.ensure_current_version()

        # and that the format looks correct
        flow_json = flow.as_json()
        self.assertEqual(flow_json['metadata']['name'], 'Call Me Maybe')
        self.assertEqual(flow_json['metadata']['revision'], 2)
        self.assertEqual(flow_json['metadata']['expires'], 720)
        self.assertEqual(flow_json['base_language'], 'base')
        self.assertEqual(5, len(flow_json['action_sets']))
        self.assertEqual(1, len(flow_json['rule_sets']))

    def test_migrate_to_11_2(self):
        fre_definition = {
            'base_language': 'fre',
            'action_sets': [
                {
                    'uuid': '9468bbce-0df6-4d86-ae14-f26525ddda1d',
                    'destination': 'cc904a60-9de1-4f0b-9b55-a42b4ea6c434',
                    'actions': [
                        {
                            'msg': {
                                'base': 'What is your favorite color?',
                                'eng': 'What is your favorite color?',
                                'fra': 'Quelle est votre couleur préférée?'
                            },
                            'type': 'reply',
                            'uuid': '335eb13d-5167-48ba-90c6-eb116656247c'
                        }
                    ],
                    'exit_uuid': 'a9904153-c831-4b95-aa20-13f84fed0841',
                    'y': 0,
                    'x': 100
                }
            ]
        }

        base_definition = {
            'base_language': 'base',
            'action_sets': [
                {
                    'uuid': '9468bbce-0df6-4d86-ae14-f26525ddda1d',
                    'destination': 'cc904a60-9de1-4f0b-9b55-a42b4ea6c434',
                    'actions': [
                        {
                            'msg': {
                                'base': 'What is your favorite color?',
                                'eng': 'What is your favorite color?',
                                'fra': 'Quelle est votre couleur préférée?'
                            },
                            'type': 'reply',
                            'uuid': '335eb13d-5167-48ba-90c6-eb116656247c'
                        }
                    ],
                    'exit_uuid': 'a9904153-c831-4b95-aa20-13f84fed0841',
                    'y': 0,
                    'x': 100
                }
            ]
        }

        flow1 = Flow.create_instance(dict(
            name='base lang test', org=self.org, created_by=self.admin, modified_by=self.admin, saved_by=self.admin,
            version_number=1)
        )
        flow2 = Flow.create_instance(dict(
            name='Base lang test', org=self.org, created_by=self.admin, modified_by=self.admin, saved_by=self.admin,
            version_number=1)
        )
        FlowRevision.create_instance(dict(
            flow=flow1, definition=fre_definition, spec_version=1, revision=1, created_by=self.admin,
            modified_by=self.admin)
        )
        FlowRevision.create_instance(dict(
            flow=flow2, definition=fre_definition, spec_version=1, revision=1, created_by=self.admin,
            modified_by=self.admin)
        )

        new_definition = migrate_to_version_11_2(fre_definition, flow=flow1)

        fre_lang_value = new_definition['base_language']
        self.assertEqual(fre_lang_value, 'fra')

        new_definition = migrate_to_version_11_2(base_definition, flow=flow2)

        base_lang_value = new_definition['base_language']
        self.assertEqual(base_lang_value, 'base')

    def test_migrate_to_11_1(self):
        definition = {
            'base_language': 'base',
            'action_sets': [
                {
                    'uuid': '9468bbce-0df6-4d86-ae14-f26525ddda1d',
                    'destination': 'cc904a60-9de1-4f0b-9b55-a42b4ea6c434',
                    'actions': [
                        {
                            'msg': {
                                'base': 'What is your favorite color?',
                                'eng': 'What is your favorite color?',
                                'fre': 'Quelle est votre couleur préférée?'
                            },
                            'type': 'reply',
                            'uuid': '335eb13d-5167-48ba-90c6-eb116656247c'
                        }
                    ],
                    'exit_uuid': 'a9904153-c831-4b95-aa20-13f84fed0841',
                    'y': 0,
                    'x': 100
                }, {
                    'y': 1214,
                    'x': 284,
                    'destination': '498b1953-02f1-47dd-b9cb-1b51913e348f',
                    'uuid': '9769918c-8ca4-4ec5-8b5b-bf94cc6746a9',
                    'actions': [{
                        'lang': 'fre',
                        'type': 'lang',
                        'name': 'French',
                        'uuid': '56a4bca5-b9e5-4d04-883c-ca65d7c4d538'
                    }]
                }, {
                    'uuid': '9468bbce-0df6-4d86-ae14-f26525ddda1d',
                    'destination': 'cc904a60-9de1-4f0b-9b55-a42b4ea6c434',
                    'actions': [
                        {
                            'msg': {
                                'base': 'What is your favorite color?',
                                'eng': 'What is your favorite color?',
                                'fre': 'Quelle est votre couleur préférée?',
                                'newl': 'Bogus translation'
                            },
                            'type': 'reply',
                            'uuid': '335eb13d-5167-48ba-90c6-eb116656247c'
                        }
                    ],
                    'exit_uuid': 'a9904153-c831-4b95-aa20-13f84fed0841',
                    'y': 0,
                    'x': 100
                }
            ]
        }

        flow = Flow.create_instance(dict(
            name='String group', org=self.org, created_by=self.admin, modified_by=self.admin, saved_by=self.admin,
            version_number=1)
        )

        FlowRevision.create_instance(dict(
            flow=flow, definition=definition, spec_version=1, revision=1, created_by=self.admin,
            modified_by=self.admin)
        )

        new_definition = migrate_to_version_11_1(definition, flow=flow)

        lang_path = new_definition['action_sets'][0]['actions'][0]['msg']

        self.assertIn('fra', lang_path)
        self.assertEqual(len(lang_path), 3)

        lang_key_value = new_definition['action_sets'][1]['actions'][0]['lang']

        self.assertEqual(lang_key_value, 'fra')

        should_not_be_migrated_path = new_definition['action_sets'][2]['actions'][0]['msg']
        self.assertIn('fre', should_not_be_migrated_path)

        # we cannot migrate flows to version 11 without flow object (languages depend on flow.org)
        self.assertRaises(ValueError, migrate_to_version_11_1, definition)

    def test_migrate_to_11_0(self):
        self.create_field('nickname', "Nickname", Value.TYPE_TEXT)
        self.create_field('district', "District", Value.TYPE_DISTRICT)
        self.create_field('joined_on', "Joined On", Value.TYPE_DATETIME)

        flow = self.get_flow("type_flow")
        flow_json = flow.as_json()

        # gather up replies to check expressions were migrated
        replies = []
        for action_set in flow_json['action_sets']:
            for action in action_set['actions']:
                if action['type'] == 'reply':
                    for text in sorted(action['msg'].values()):
                        replies.append(text)

        self.assertEqual(replies, [
            "Hey @contact.nickname, you joined on @(format_date(contact.joined_on)) in @(format_location(contact.district)).",
            "It's @(format_date(date)). The time is @(format_date(date.now)) on @date.today.",
            "Send text",
            "You said @flow.text at @(format_date(flow.text.time)). Send date",
            "You said @(format_date(flow.date)) which was in category @flow.date.category Send number",
            "You said @flow.number. Send state",
            "You said @(format_location(flow.state)) which was in category @flow.state.category. Send district",
            "You said @(format_location(flow.district)). Send ward",
            "Tu as dit @(format_location(flow.ward))",  # flow var followed by end of input
            "You said @(format_location(flow.ward))."   # flow var followed by period then end of input
        ])

    def test_migrate_to_10_4(self):
        definition = {
            'action_sets': [
                {
                    "y": 0, "x": 100,
                    "destination": "0ecf7914-05e0-4b71-8816-495d2c0921b5",
                    "uuid": "a6676605-332a-4309-a8b8-79b33e73adcd",
                    "actions": [
                        {
                            "type": "reply",
                            "msg": {"base": "What is your favorite color?"}
                        }
                    ]
                },
            ]
        }

        definition = migrate_to_version_10_4(definition)

        # make sure all of our action sets have an exit uuid and all of our actions have uuids set
        for actionset in definition['action_sets']:
            self.assertIsNotNone(actionset['exit_uuid'])
            for action in actionset['actions']:
                self.assertIsNotNone(action['uuid'])

    def test_migrate_to_10_3(self):
        favorites = self.get_flow('favorites')

        # make sure all of our action sets have an exit uuid
        for actionset in favorites.action_sets.all():
            self.assertIsNotNone(actionset.exit_uuid)

    def test_migrate_to_10_2(self):
        flow_json = self.get_flow_json('single_message_bad_localization')
        flow_json = migrate_to_version_10_2(flow_json)
        self.assertEqual('Campaign Message 12', flow_json['action_sets'][0]['actions'][0]['msg']['eng'])

    def test_migrate_to_10_1(self):
        favorites = self.get_flow('favorites')

        # make sure all of our actions have uuids set
        for actionset in favorites.action_sets.all():
            for action in actionset.get_actions():
                self.assertIsNotNone(action.uuid)

        # since actions can generate their own uuids, lets make sure fetching from the databse yields the same uuids
        exported = favorites.as_json()
        flow = Flow.objects.filter(name='Favorites').first()
        self.assertEqual(exported, flow.as_json())
        self.assertEqual(flow.version_number, get_current_export_version())

    @override_settings(SEND_WEBHOOKS=True)
    def test_migrate_to_10(self):
        # this is really just testing our rewriting of webhook rulesets
        webhook_flow = self.get_flow('dual_webhook')
        self.assertNotEqual(webhook_flow.modified_on, webhook_flow.saved_on)

        # get our definition out
        flow_def = webhook_flow.as_json()

        # make sure our rulesets no longer have 'webhook' or 'webhook_action'
        for ruleset in flow_def['rule_sets']:
            self.assertNotIn('webhook', ruleset)
            self.assertNotIn('webhook_action', ruleset)

        self.mockRequest('POST', '/code', '{"code": "ABABUUDDLRS"}', content_type='application/json')

        run, = webhook_flow.start([], [self.contact])

        # assert the code we received was right
        msg = Msg.objects.filter(direction='O', contact=self.contact).order_by('id').last()
        self.assertEqual(msg.text, "Great, your code is ABABUUDDLRS. Enter your name")

        self.mockRequest('GET', '/success', "Success")

        self.send_message(webhook_flow, "Ryan Lewis", assert_reply=False)

        # startover have our first webhook fail, check that routing still works with failure
        flow_def['rule_sets'][0]['config']['webhook'] = 'http://localhost:49999/error'
        webhook_flow.update(flow_def)

        self.mockRequest('POST', '/error', 'BOOM', status=400)

        webhook_flow.start([], [self.contact], restart_participants=True)

        # assert the code we received was right
        msg = Msg.objects.filter(direction='O', contact=self.contact).order_by('id').last()
        self.assertEqual("Great, your code is @extra.code. Enter your name", msg.text)

        # check all our mocked requests were made
        self.assertAllRequestsMade()

    def test_migrate_to_9(self):

        # our group and flow to move to uuids
        group = self.create_group("Phans", [])
        previous_flow = self.create_flow()
        start_flow = self.create_flow()
        label = Label.get_or_create(self.org, self.admin, 'My label')

        substitutions = dict(group_id=group.pk,
                             contact_id=self.contact.pk,
                             start_flow_id=start_flow.pk,
                             previous_flow_id=previous_flow.pk,
                             label_id=label.pk)

        exported_json = json.loads(self.get_import_json('migrate_to_9', substitutions))
        exported_json = migrate_export_to_version_9(exported_json, self.org, True)

        # our campaign events shouldn't have ids
        campaign = exported_json['campaigns'][0]
        event = campaign['events'][0]

        # campaigns should have uuids
        self.assertIn('uuid', campaign)
        self.assertNotIn('id', campaign)

        # our event flow should be a uuid
        self.assertIn('flow', event)
        self.assertIn('uuid', event['flow'])
        self.assertNotIn('id', event['flow'])

        # our relative field should not have an id
        self.assertNotIn('id', event['relative_to'])

        # evaluate that the flow json is migrated properly
        flow_json = exported_json['flows'][0]

        # check that contacts migrated properly
        send_action = flow_json['action_sets'][0]['actions'][1]
        self.assertEqual(1, len(send_action['contacts']))
        self.assertEqual(1, len(send_action['groups']))

        for contact in send_action['contacts']:
            self.assertIn('uuid', contact)
            self.assertNotIn('id', contact)

        for group in send_action['groups']:
            self.assertIn('uuid', group)
            self.assertNotIn('id', contact)

        label_action = flow_json['action_sets'][0]['actions'][2]
        for label in label_action.get('labels'):
            self.assertNotIn('id', label)
            self.assertIn('uuid', label)

        action_set = flow_json['action_sets'][1]
        actions = action_set['actions']

        for action in actions[0:2]:
            self.assertIn(action['type'], ('del_group', 'add_group'))
            self.assertIn('uuid', action['groups'][0])
            self.assertNotIn('id', action['groups'][0])

        for action in actions[2:4]:
            self.assertIn(action['type'], ('trigger-flow', 'flow'))
            self.assertIn('flow', action)
            self.assertIn('uuid', action['flow'])
            self.assertIn('name', action['flow'])
            self.assertNotIn('id', action)
            self.assertNotIn('name', action)

        # we also switch flow ids to uuids in the metadata
        self.assertIn('uuid', flow_json['metadata'])
        self.assertNotIn('id', flow_json['metadata'])

        # import the same thing again, should have the same uuids
        new_exported_json = json.loads(self.get_import_json('migrate_to_9', substitutions))
        new_exported_json = migrate_export_to_version_9(new_exported_json, self.org, True)
        self.assertEqual(flow_json['metadata']['uuid'], new_exported_json['flows'][0]['metadata']['uuid'])

        # but when done as a different site, it should be unique
        new_exported_json = json.loads(self.get_import_json('migrate_to_9', substitutions))
        new_exported_json = migrate_export_to_version_9(new_exported_json, self.org, False)
        self.assertNotEqual(flow_json['metadata']['uuid'], new_exported_json['flows'][0]['metadata']['uuid'])

        # check we can update a flow with the migrated definition
        flow = Flow.objects.create(name='test flow', created_by=self.admin, modified_by=self.admin, org=self.org, saved_by=self.admin)
        flow.update(FlowRevision.migrate_definition(exported_json['flows'][0], flow))

        # can also just import a single flow
        exported_json = json.loads(self.get_import_json('migrate_to_9', substitutions))
        flow_json = migrate_to_version_9(exported_json['flows'][0], flow)
        self.assertIn('uuid', flow_json['metadata'])
        self.assertNotIn('id', flow_json['metadata'])

        # try it with missing metadata
        flow_json = json.loads(self.get_import_json('migrate_to_9', substitutions))['flows'][0]
        del flow_json['metadata']
        flow_json = migrate_to_version_9(flow_json, flow)
        self.assertEqual(1, flow_json['metadata']['revision'])
        self.assertEqual('test flow', flow_json['metadata']['name'])
        self.assertEqual(720, flow_json['metadata']['expires'])
        self.assertIn('uuid', flow_json['metadata'])
        self.assertIn('saved_on', flow_json['metadata'])

        # check that our replacements work
        self.assertEqual('@(CONCAT(parent.divided, parent.sky))', flow_json['action_sets'][0]['actions'][3]['value'])
        self.assertEqual('@parent.contact.name', flow_json['action_sets'][0]['actions'][4]['value'])

    def test_migrate_to_8(self):
        # file uses old style expressions
        flow_json = self.get_flow_json('old_expressions')

        # migrate to the version right before us first
        flow_json = migrate_to_version_7(flow_json)
        flow_json = migrate_to_version_8(flow_json)

        self.assertEqual(flow_json['action_sets'][0]['actions'][0]['msg']['eng'], "Hi @(UPPER(contact.name)). Today is @(date.now)")
        self.assertEqual(flow_json['action_sets'][1]['actions'][0]['groups'][0], "@flow.response_1.category")
        self.assertEqual(flow_json['action_sets'][1]['actions'][1]['msg']['eng'], "Was @(PROPER(LOWER(contact.name))).")
        self.assertEqual(flow_json['action_sets'][1]['actions'][1]['variables'][0]['id'], "@flow.response_1.category")
        self.assertEqual(flow_json['rule_sets'][0]['webhook'], "http://example.com/query.php?contact=@(UPPER(contact.name))")
        self.assertEqual(flow_json['rule_sets'][0]['operand'], "@(step.value)")
        self.assertEqual(flow_json['rule_sets'][1]['operand'], "@(step.value + 3)")

    def test_migrate_to_7(self):
        flow_json = self.get_flow_json('call_me_maybe')

        # migrate to the version right before us first
        flow_json = migrate_to_version_5(flow_json)
        flow_json = migrate_to_version_6(flow_json)

        self.assertIsNotNone(flow_json.get('definition'))
        self.assertEqual('Call me maybe', flow_json.get('name'))
        self.assertEqual(100, flow_json.get('id'))
        self.assertEqual('V', flow_json.get('flow_type'))

        flow_json = migrate_to_version_7(flow_json)
        self.assertIsNone(flow_json.get('definition', None))
        self.assertIsNotNone(flow_json.get('metadata', None))

        metadata = flow_json.get('metadata')
        self.assertEqual('Call me maybe', metadata['name'])
        self.assertEqual(100, metadata['id'])
        self.assertEqual('V', flow_json.get('flow_type'))

    def test_migrate_to_6(self):

        # file format is old non-localized format
        voice_json = self.get_flow_json('call_me_maybe')
        definition = voice_json.get('definition')

        # no language set
        self.assertIsNone(definition.get('base_language', None))
        self.assertEqual('Yes', definition['rule_sets'][0]['rules'][0]['category'])
        self.assertEqual('Press one, two, or three. Thanks.', definition['action_sets'][0]['actions'][0]['msg'])

        # add a recording to make sure that gets migrated properly too
        definition['action_sets'][0]['actions'][0]['recording'] = '/recording.mp3'

        voice_json = migrate_to_version_5(voice_json)
        voice_json = migrate_to_version_6(voice_json)
        definition = voice_json.get('definition')

        # now we should have a language
        self.assertEqual('base', definition.get('base_language', None))
        self.assertEqual('Yes', definition['rule_sets'][0]['rules'][0]['category']['base'])
        self.assertEqual('Press one, two, or three. Thanks.', definition['action_sets'][0]['actions'][0]['msg']['base'])
        self.assertEqual('/recording.mp3', definition['action_sets'][0]['actions'][0]['recording']['base'])

        # now try one that doesn't have a recording set
        voice_json = self.get_flow_json('call_me_maybe')
        definition = voice_json.get('definition')
        del definition['action_sets'][0]['actions'][0]['recording']
        voice_json = migrate_to_version_5(voice_json)
        voice_json = migrate_to_version_6(voice_json)
        definition = voice_json.get('definition')
        self.assertNotIn('recording', definition['action_sets'][0]['actions'][0])

    def test_migrate_to_5_language(self):

        flow_json = self.get_flow_json('multi_language_flow')
        ruleset = flow_json['definition']['rule_sets'][0]
        ruleset['operand'] = '@step.value|lower_case'

        # now migrate us forward
        flow_json = migrate_to_version_5(flow_json)

        wait_ruleset = None
        rules = None
        for ruleset in flow_json.get('definition').get('rule_sets'):
            if ruleset['ruleset_type'] == 'wait_message':
                rules = ruleset['rules']
                wait_ruleset = ruleset
                break

        self.assertIsNotNone(wait_ruleset)
        self.assertIsNotNone(rules)

        self.assertEqual(1, len(rules))
        self.assertEqual('All Responses', rules[0]['category']['eng'])
        self.assertEqual('Otro', rules[0]['category']['spa'])

    @override_settings(SEND_WEBHOOKS=True)
    def test_migrate_to_5(self):
        flow = self.get_flow('favorites_v4')

        # first node should be a wait node
        ruleset = RuleSet.objects.filter(label='Color Response').first()
        self.assertEqual('wait_message', ruleset.ruleset_type)
        self.assertEqual('@step.value', ruleset.operand)

        # we should now be pointing to a newly created webhook rule
        webhook = RuleSet.objects.get(flow=flow, uuid=ruleset.get_rules()[0].destination)
        self.assertEqual('webhook', webhook.ruleset_type)
        self.assertEqual('http://localhost:49999/status', webhook.config[RuleSet.CONFIG_WEBHOOK])
        self.assertEqual('POST', webhook.config[RuleSet.CONFIG_WEBHOOK_ACTION])
        self.assertEqual('@step.value', webhook.operand)
        self.assertEqual('Color Webhook', webhook.label)

        # which should in turn point to a new expression split on @extra.value
        expression = RuleSet.objects.get(flow=flow, uuid=webhook.get_rules()[0].destination)
        self.assertEqual('expression', expression.ruleset_type)
        self.assertEqual('@extra.value', expression.operand)

        # takes us to the next question
        beer_question = ActionSet.objects.get(flow=flow, uuid=expression.get_rules()[0].destination)

        # which should pause for the response
        wait_beer = RuleSet.objects.get(flow=flow, uuid=beer_question.destination)
        self.assertEqual('wait_message', wait_beer.ruleset_type)
        self.assertEqual('@step.value', wait_beer.operand)
        self.assertEqual(1, len(wait_beer.get_rules()))
        self.assertEqual('All Responses', wait_beer.get_rules()[0].category[flow.base_language])

        # and then split on the expression for various beer choices
        beer_expression = RuleSet.objects.get(flow=flow, uuid=wait_beer.get_rules()[0].destination)
        self.assertEqual('expression', beer_expression.ruleset_type)
        self.assertEqual('@(LOWER(step.value))', beer_expression.operand)
        self.assertEqual(5, len(beer_expression.get_rules()))

        # set our expression to operate on the last inbound message
        expression.operand = '@step.value'
        expression.save()

        # now try executing our migrated flow
        first_response = ActionSet.objects.get(flow=flow, x=131)
        actions = first_response.actions
        actions[0]['msg'][flow.base_language] = 'I like @flow.color.category too! What is your favorite beer? @flow.color_webhook'
        first_response.actions = actions
        first_response.save()

        self.mockRequest('POST', '/status', '{ "status": "valid" }')

        reply = self.send_message(flow, 'red')
        self.assertEqual('I like Red too! What is your favorite beer? { "status": "valid" }', reply)

        reply = self.send_message(flow, 'Turbo King')
        self.assertEqual('Mmmmm... delicious Turbo King. If only they made red Turbo King! Lastly, what is your name?', reply)

        # check all our mocked requests were made
        self.assertAllRequestsMade()

    def test_migrate_revisions(self):
        flow = self.get_flow('favorites_v4')
        rev = flow.revisions.all().first()
        json_flow = rev.get_definition_json()

        # remove our flow version from the flow
        del json_flow[Flow.VERSION]
        rev.definition = json_flow
        rev.spec_version = '10'
        rev.save()

        new_rev = flow.update(rev.get_definition_json())
        self.assertEqual(new_rev.spec_version, get_current_export_version())

        flow.refresh_from_db()
        self.assertEqual(flow.revisions.all().count(), 2)
        self.assertEqual(flow.version_number, get_current_export_version())

    def test_migrate_sample_flows(self):
        self.org.create_sample_flows('https://app.rapidpro.io')
        self.assertEqual(3, self.org.flows.filter(name__icontains='Sample Flow').count())

        # make sure it is localized
        poll = self.org.flows.filter(name='Sample Flow - Simple Poll').first()
        self.assertIn('base', poll.action_sets.all().order_by('y').first().get_actions()[0].msg)
        self.assertEqual('base', poll.base_language)

        # check replacement
        order_checker = self.org.flows.filter(name='Sample Flow - Order Status Checker').first()
        ruleset = order_checker.rule_sets.filter(y=298).first()
        self.assertEqual('https://app.rapidpro.io/demo/status/', ruleset.config[RuleSet.CONFIG_WEBHOOK])

        # our test user doesn't use an email address, check for Administrator for the email
        actionset = order_checker.action_sets.filter(y=991).first()
        self.assertEqual('Administrator', actionset.get_actions()[1].emails[0])

    def test_migrate_bad_group_names(self):
        # This test makes sure that bad contact groups (< 25, etc) are migrated forward properly.
        # However, since it was a missed migration, now we need to apply it for any current version
        # at the time of this fix
        for v in ('4', '5', '6', '7', '8', '9', '10'):
            error = 'Failure migrating group names "%s" forward from v%s'
            flow = self.get_flow('favorites_bad_group_name_v%s' % v)
            self.assertIsNotNone(flow, "Failure importing favorites from v%s" % v)
            self.assertTrue(ContactGroup.user_groups.filter(name='Contacts < 25').exists(), error % ("< 25", v))
            self.assertTrue(ContactGroup.user_groups.filter(name='Contacts > 100').exists(), error % ("> 100", v))

            ContactGroup.user_groups.all().delete()
            self.assertEqual(get_current_export_version(), flow.version_number)
            flow.delete()

    def test_migrate_malformed_groups(self):
        flow = self.get_flow('malformed_groups')
        self.assertIsNotNone(flow)
        self.assertTrue(ContactGroup.user_groups.filter(name='Contacts < 25').exists())
        self.assertTrue(ContactGroup.user_groups.filter(name='Unknown').exists())


class DuplicateResultTest(FlowFileTest):

    def test_duplicate_value_test(self):
        flow = self.get_flow('favorites')
        self.assertEqual("I don't know that color. Try again.", self.send_message(flow, "carpet"))

        # get the run for our contact
        run = FlowRun.objects.get(contact=self.contact, flow=flow)

        # we should have one result for this run, "Other"
        results = run.results

        self.assertEqual(len(results), 1)
        self.assertEqual(results['color']['category'], "Other")

        # retry with "red" as an aswer
        self.assertEqual("Good choice, I like Red too! What is your favorite beer?", self.send_message(flow, "red"))

        # we should now still have only one value, but the category should be Red now
        run.refresh_from_db()
        results = run.results
        self.assertEqual(len(results), 1)
        self.assertEqual(results['color']['category'], "Red")


class ChannelSplitTest(FlowFileTest):

    def setUp(self):
        super(ChannelSplitTest, self).setUp()

        # update our channel to have a 206 address
        self.channel.address = '+12065551212'
        self.channel.save()

    def test_initial_channel_split(self):
        flow = self.get_flow('channel_split')

        # start our contact down the flow
        flow.start([], [self.contact])

        # check the message sent to them
        msgs = list(self.contact.msgs.order_by('id'))
        self.assertEqual(len(msgs), 2)
        self.assertEqual(msgs[0].text, "Your channel is +12065551212")
        self.assertEqual(msgs[1].text, "206 Channel")

    def test_no_urn_channel_split(self):
        flow = self.get_flow('channel_split')

        # ok, remove the URN on our contact
        self.contact.urns.all().update(contact=None)

        # run the flow again
        flow.start([], [self.contact])

        # shouldn't have any messages sent, as they have no URN
        self.assertFalse(self.contact.msgs.all())

        # should have completed the flow though
        run = FlowRun.objects.get(contact=self.contact)
        self.assertFalse(run.is_active)

    def test_no_urn_channel_split_first(self):
        flow = self.get_flow('channel_split_rule_first')

        # start our contact down the flow
        flow.start([], [self.contact])

        # check that the split was successful
        msg = self.contact.msgs.first()
        self.assertEqual("206 Channel", msg.text)


class WebhookLoopTest(FlowFileTest):

    @override_settings(SEND_WEBHOOKS=True)
    def test_webhook_loop(self):
        flow = self.get_flow('webhook_loop')

        self.mockRequest('GET', '/msg', '{ "text": "first message" }')
        self.assertEqual("first message", self.send_message(flow, "first", initiate_flow=True))

        self.mockRequest('GET', '/msg', '{ "text": "second message" }')
        self.assertEqual("second message", self.send_message(flow, "second"))

        # check all our mocked requests were made
        self.assertAllRequestsMade()


class MissedCallChannelTest(FlowFileTest):

    def test_missed_call_channel(self):
        flow = self.get_flow('call_channel_split')

        # trigger a missed call on our channel
        call = ChannelEvent.create(self.channel, 'tel:+250788111222', ChannelEvent.TYPE_CALL_IN_MISSED,
                                   timezone.now(), {})

        # we aren't in the group, so no run should be started
        run = FlowRun.objects.filter(flow=flow).first()
        self.assertIsNone(run)

        # but if we add our contact to the group..
        group = ContactGroup.user_groups.filter(name='Trigger Group').first()
        group.update_contacts(self.admin, [self.create_contact(number='+250788111222')], True)

        # now create another missed call which should fire our trigger
        call = ChannelEvent.create(self.channel, 'tel:+250788111222', ChannelEvent.TYPE_CALL_IN_MISSED,
                                   timezone.now(), {})

        # should have triggered our flow
        FlowRun.objects.get(flow=flow)

        # should have sent a message to the user
        msg = Msg.objects.get(contact=call.contact, channel=self.channel)
        self.assertEqual(msg.text, "Matched +250785551212")

        # try the same thing with a contact trigger (same as missed calls via twilio)
        Trigger.catch_triggers(msg.contact, Trigger.TYPE_MISSED_CALL, msg.channel)

        self.assertEqual(2, Msg.objects.filter(contact=call.contact, channel=self.channel).count())
        last = Msg.objects.filter(contact=call.contact, channel=self.channel).order_by('-pk').first()
        self.assertEqual(last.text, "Matched +250785551212")


class GhostActionNodeTest(FlowFileTest):

    def test_ghost_action_node_test(self):
        # load our flows
        self.get_flow('parent_child_flow')
        flow = Flow.objects.get(name="Parent Flow")

        # start the flow
        flow.start([], [self.contact])

        # at this point, our contact has to active flow runs:
        # one for our parent flow at an action set (the start flow action), one in our child flow at the send message action

        # let's remove the actionset we are stuck at
        ActionSet.objects.filter(flow=flow).delete()

        # create a new message and get it handled
        msg = self.create_msg(contact=self.contact, direction='I', text="yes")
        Flow.find_and_handle(msg)

        # we should have gotten a response from our child flow
        self.assertEqual("I like butter too.",
                         Msg.objects.filter(direction=OUTGOING).order_by('-created_on').first().text)


class TriggerStartTest(FlowFileTest):

    def test_trigger_start(self):
        """
        Test case for a flow starting with a split on a contact field, sending an action, THEN waiting for a message.
        Having this flow start from a trigger should NOT advance the contact past the first wait.
        """
        flow = self.get_flow('trigger_start')

        # create our message that will start our flow
        incoming = self.create_msg(direction=INCOMING, contact=self.contact, text="trigger")

        self.assertTrue(Trigger.find_and_handle(incoming))

        # flow should have started
        self.assertTrue(FlowRun.objects.filter(flow=flow, contact=self.contact))

        # but we shouldn't have our name be trigger
        contact = Contact.objects.get(pk=self.contact.pk)
        self.assertNotEqual(contact.name, "trigger")

        self.assertLastResponse("Thanks for participating, what is your name?")

        # if we send another message, that should set our name
        incoming = self.create_msg(direction=INCOMING, contact=self.contact, text="Rudolph")
        self.assertTrue(Flow.find_and_handle(incoming)[0])
        contact = Contact.objects.get(pk=self.contact.pk)
        self.assertEqual(contact.name, "Rudolph")

        self.assertLastResponse("Great to meet you Rudolph")

    def test_trigger_capture(self):
        """
        Test case for a flow starting with a wait. Having this flow start with a trigger should advance the flow
        past that wait and process the rest of the flow (until the next wait)
        """
        flow = self.get_flow('trigger_capture')

        # create our incoming message that will start our flow
        incoming = self.create_msg(direction=INCOMING, contact=self.contact, text="trigger2 Rudolph")

        self.assertTrue(Trigger.find_and_handle(incoming))

        # flow should have started
        self.assertTrue(FlowRun.objects.filter(flow=flow, contact=self.contact))

        # and our name should be set to Nic
        contact = Contact.objects.get(pk=self.contact.pk)
        self.assertEqual(contact.name, "Rudolph")

        self.assertLastResponse("Hi Rudolph, how old are you?")


@patch('temba.flows.models.START_FLOW_BATCH_SIZE', 10)
class FlowBatchTest(FlowFileTest):

    def test_flow_batch_start(self):
        """
        Tests starting a flow for a group of contacts
        """
        flow = self.get_flow('two_in_row')

        # create 10 contacts
        contacts = []
        for i in range(11):
            contacts.append(self.create_contact("Contact %d" % i, "2507883833%02d" % i))

        # stop our last contact
        stopped = contacts[10]
        stopped.stop(self.admin)

        # start our flow, this will take two batches
        with QueryTracker(assert_query_count=308, stack_count=10, skip_unique_queries=True):
            flow.start([], contacts)

        # ensure 11 flow runs were created
        self.assertEqual(11, FlowRun.objects.all().count())

        # ensure 20 outgoing messages were created (2 for each successful run)
        self.assertEqual(20, Msg.objects.all().exclude(contact=stopped).count())

        # but only one broadcast
        self.assertEqual(1, Broadcast.objects.all().count())
        broadcast = Broadcast.objects.get()

        # ensure that our flowsteps all have the broadcast set on them
        for step in FlowStep.objects.filter(step_type=FlowStep.TYPE_ACTION_SET).exclude(run__contact=stopped):
            self.assertEqual(broadcast, step.broadcasts.all().get())

        # make sure that adding a msg more than once doesn't blow up
        step.run.add_messages(list(step.messages.all()), step=step)
        self.assertEqual(step.messages.all().count(), 2)
        self.assertEqual(step.broadcasts.all().count(), 1)

        # our stopped contact should have only received one msg before blowing up
        self.assertEqual(1, Msg.objects.filter(contact=stopped, status=FAILED).count())
        self.assertEqual(1, FlowRun.objects.filter(contact=stopped, exit_type=FlowRun.EXIT_TYPE_INTERRUPTED).count())


class TwoInRowTest(FlowFileTest):

    def test_two_in_row(self):
        flow = self.get_flow('two_in_row')
        flow.start([], [self.contact])

        # assert contact received both messages
        msgs = self.contact.msgs.all()
        self.assertEqual(msgs.count(), 2)


class SendActionTest(FlowFileTest):

    def test_send(self):
        contact1 = self.create_contact("Mark", "+14255551212")
        contact2 = self.create_contact("Gregg", "+12065551212")

        substitutions = dict(contact1_id=contact1.id, contact2_id=contact2.id)
        exported_json = json.loads(self.get_import_json('bad_send_action', substitutions))

        # create a flow object, we just need this to test our flow revision
        flow = Flow.objects.create(org=self.org, name="Import Flow", created_by=self.admin, modified_by=self.admin,
                                   saved_by=self.admin)
        revision = FlowRevision.objects.create(flow=flow, definition=exported_json, spec_version='8',
                                               revision=1, created_by=self.admin, modified_by=self.admin)
        flow.version_number = '8'
        flow.save()

        migrated = revision.get_definition_json()

        # assert our contacts have valid uuids now
        self.assertEqual(migrated['action_sets'][0]['actions'][0]['contacts'][0]['uuid'], contact1.uuid)
        self.assertEqual(migrated['action_sets'][0]['actions'][0]['contacts'][1]['uuid'], contact2.uuid)


class ExitTest(FlowFileTest):

    def test_exit_via_start(self):
        # start contact in one flow
        first_flow = self.get_flow('substitution')
        first_flow.start([], [self.contact])

        # should have one active flow run
        first_run = FlowRun.objects.get(is_active=True, flow=first_flow, contact=self.contact)

        # start in second via manual start
        second_flow = self.get_flow('favorites')
        second_flow.start([], [self.contact])

        second_run = FlowRun.objects.get(is_active=True)
        first_run.refresh_from_db()
        self.assertFalse(first_run.is_active)
        self.assertEqual(first_run.exit_type, FlowRun.EXIT_TYPE_INTERRUPTED)

        self.assertTrue(second_run.is_active)

    def test_exit_via_trigger(self):
        # start contact in one flow
        first_flow = self.get_flow('substitution')
        first_flow.start([], [self.contact])

        # should have one active flow run
        first_run = FlowRun.objects.get(is_active=True, flow=first_flow, contact=self.contact)

        # start in second via a keyword trigger
        second_flow = self.get_flow('favorites')

        Trigger.objects.create(org=self.org, keyword='favorites', flow=second_flow,
                               trigger_type=Trigger.TYPE_KEYWORD,
                               created_by=self.admin, modified_by=self.admin)

        # start it via the keyword
        msg = self.create_msg(contact=self.contact, direction=INCOMING, text="favorites")
        msg.handle()

        second_run = FlowRun.objects.get(is_active=True)
        first_run.refresh_from_db()
        self.assertFalse(first_run.is_active)
        self.assertEqual(first_run.exit_type, FlowRun.EXIT_TYPE_INTERRUPTED)

        self.assertTrue(second_run.is_active)

    def test_exit_via_campaign(self):
        from temba.campaigns.models import Campaign, CampaignEvent, EventFire

        # start contact in one flow
        first_flow = self.get_flow('substitution')
        first_flow.start([], [self.contact])

        # should have one active flow run
        first_run = FlowRun.objects.get(is_active=True, flow=first_flow, contact=self.contact)

        # start in second via a campaign event
        second_flow = self.get_flow('favorites')
        self.farmers = self.create_group("Farmers", [self.contact])

        campaign = Campaign.create(self.org, self.admin, Campaign.get_unique_name(self.org, "Reminders"), self.farmers)
        planting_date = ContactField.get_or_create(self.org, self.admin, 'planting_date', "Planting Date")
        event = CampaignEvent.create_flow_event(self.org, self.admin, campaign, planting_date,
                                                offset=1, unit='W', flow=second_flow, delivery_hour='13')

        self.contact.set_field(self.user, 'planting_date', "05-10-2020 12:30:10")

        # update our campaign events
        EventFire.update_campaign_events(campaign)
        event = EventFire.objects.get()

        # fire it, this will start our second flow
        event.fire()

        second_run = FlowRun.objects.get(is_active=True)
        first_run.refresh_from_db()
        self.assertFalse(first_run.is_active)
        self.assertEqual(first_run.exit_type, FlowRun.EXIT_TYPE_INTERRUPTED)

        self.assertTrue(second_run.is_active)


class OrderingTest(FlowFileTest):

    def setUp(self):
        super(OrderingTest, self).setUp()

        self.contact2 = self.create_contact('Ryan Lewis', '+12065552121')

        self.channel.delete()
        self.channel = Channel.create(self.org, self.user, 'KE', 'EX', None, '+250788123123', schemes=['tel'],
                                      config=dict(send_url='https://google.com'))

    def tearDown(self):
        super(OrderingTest, self).tearDown()

    def test_two_in_row(self):
        flow = self.get_flow('ordering')
        from temba.channels.tasks import send_msg_task

        # start our flow with a contact
        with patch('temba.channels.tasks.send_msg_task', wraps=send_msg_task) as mock_send_msg:
            flow.start([], [self.contact])

            # check the ordering of when the msgs were sent
            msgs = Msg.objects.filter(status=WIRED).order_by('sent_on')

            # the four messages should have been sent in order
            self.assertEqual(msgs[0].text, "Msg1")
            self.assertEqual(msgs[1].text, "Msg2")
            self.assertTrue(msgs[1].sent_on - msgs[0].sent_on > timedelta(seconds=.750))
            self.assertEqual(msgs[2].text, "Msg3")
            self.assertTrue(msgs[2].sent_on - msgs[1].sent_on > timedelta(seconds=.750))
            self.assertEqual(msgs[3].text, "Msg4")
            self.assertTrue(msgs[3].sent_on - msgs[2].sent_on > timedelta(seconds=.750))

            # send_msg_task should have only been called once
            self.assertEqual(mock_send_msg.call_count, 1)

        # reply, should get another 4 messages
        with patch('temba.channels.tasks.send_msg_task', wraps=send_msg_task) as mock_send_msg:
            msg = self.create_msg(contact=self.contact, direction=INCOMING, text="onwards!")
            Flow.find_and_handle(msg)

            msgs = Msg.objects.filter(direction=OUTGOING, status=WIRED).order_by('sent_on')[4:]
            self.assertEqual(msgs[0].text, "Ack1")
            self.assertEqual(msgs[1].text, "Ack2")
            self.assertTrue(msgs[1].sent_on - msgs[0].sent_on > timedelta(seconds=.750))
            self.assertEqual(msgs[2].text, "Ack3")
            self.assertTrue(msgs[2].sent_on - msgs[1].sent_on > timedelta(seconds=.750))
            self.assertEqual(msgs[3].text, "Ack4")
            self.assertTrue(msgs[3].sent_on - msgs[2].sent_on > timedelta(seconds=.750))

            # again, only one send_msg
            self.assertEqual(mock_send_msg.call_count, 1)

        Msg.objects.all().delete()

        # try with multiple contacts
        with patch('temba.channels.tasks.send_msg_task', wraps=send_msg_task) as mock_send_msg:
            flow.start([], [self.contact, self.contact2], restart_participants=True)

            # we should have two batches of messages, for for each contact
            msgs = Msg.objects.filter(status=WIRED).order_by('sent_on')

            self.assertEqual(msgs[0].contact, self.contact)
            self.assertEqual(msgs[0].text, "Msg1")
            self.assertEqual(msgs[1].text, "Msg2")
            self.assertTrue(msgs[1].sent_on - msgs[0].sent_on > timedelta(seconds=.750))
            self.assertEqual(msgs[2].text, "Msg3")
            self.assertTrue(msgs[2].sent_on - msgs[1].sent_on > timedelta(seconds=.750))
            self.assertEqual(msgs[3].text, "Msg4")
            self.assertTrue(msgs[3].sent_on - msgs[2].sent_on > timedelta(seconds=.750))

            self.assertEqual(msgs[4].contact, self.contact2)
            self.assertEqual(msgs[4].text, "Msg1")
            self.assertTrue(msgs[4].sent_on - msgs[3].sent_on < timedelta(seconds=.500))
            self.assertEqual(msgs[5].text, "Msg2")
            self.assertTrue(msgs[5].sent_on - msgs[4].sent_on > timedelta(seconds=.750))
            self.assertEqual(msgs[6].text, "Msg3")
            self.assertTrue(msgs[6].sent_on - msgs[5].sent_on > timedelta(seconds=.750))
            self.assertEqual(msgs[7].text, "Msg4")
            self.assertTrue(msgs[7].sent_on - msgs[6].sent_on > timedelta(seconds=.750))

            # two batches of messages, one batch for each contact
            self.assertEqual(mock_send_msg.call_count, 2)


class TimeoutTest(FlowFileTest):

    def _update_timeout(self, run, timeout_on):
        run.timeout_on = timeout_on
        run.save(update_fields=('timeout_on',))

        if run.session and run.session.output:
            output = json.loads(run.session.output)
            output['wait']['timeout_on'] = datetime_to_str(timeout_on)
            run.session.output = json.dumps(output)
            run.session.save(update_fields=('output',))

    def test_disappearing_timeout(self):
        from temba.flows.tasks import check_flow_timeouts_task
        flow = self.get_flow('timeout')

        # start the flow
        flow.start([], [self.contact])

        # check our timeout is set
        run = FlowRun.objects.get()
        self.assertTrue(run.is_active)

        start_step = run.steps.order_by('-id').first()

        # mark our last message as sent
        last_msg = run.get_last_msg(OUTGOING)
        last_msg.sent_on = timezone.now() - timedelta(minutes=5)
        last_msg.save()

        time.sleep(1)

        # ok, change our timeout to the past
        timeout = timezone.now()
        self._update_timeout(run, timeout)

        # remove our timeout rule
        flow_json = flow.as_json()
        del flow_json['rule_sets'][0]['rules'][-1]
        flow.update(flow_json)

        # process our timeouts
        check_flow_timeouts_task()

        # our timeout_on should have been cleared and we should be at the same node
        run.refresh_from_db()
        self.assertIsNone(run.timeout_on)
        current_step = run.steps.order_by('-id').first()
        self.assertEqual(current_step.step_uuid, start_step.step_uuid)

        # check that we can't be double queued by manually moving our timeout back
        with patch('temba.utils.queues.push_task') as mock_push:
            FlowRun.objects.all().update(timeout_on=timeout)
            check_flow_timeouts_task()

            self.assertEqual(0, mock_push.call_count)

    def test_timeout_race(self):
        # start one flow
        flow1 = self.get_flow('timeout')
        flow1.start([], [self.contact])
        run1 = FlowRun.objects.get(flow=flow1, contact=self.contact)

        # start another flow
        flow2 = self.get_flow('multi_timeout')
        flow2.start([], [self.contact])

        # remove our timeout rule on our second flow
        flow_json = flow2.as_json()
        del flow_json['rule_sets'][0]['rules'][-1]
        flow2.update(flow_json)

        # mark our last message as sent
        last_msg = run1.get_last_msg(OUTGOING)
        last_msg.sent_on = timezone.now() - timedelta(minutes=5)
        last_msg.save()

        time.sleep(.5)

        # ok, change our timeout to the past
        timeout = timezone.now()
        self._update_timeout(run1, timeout)

        # process our timeout
        run1.resume_after_timeout(timeout)

        # should have cleared the timeout, run2 is the active one now
        run1.refresh_from_db()
        self.assertIsNone(run1.timeout_on)

    def test_timeout_loop(self):
        from temba.flows.tasks import check_flow_timeouts_task
        from temba.msgs.tasks import process_run_timeout
        flow = self.get_flow('timeout_loop')

        # start the flow
        run, = flow.start([], [self.contact])

        # mark our last message as sent
        run.refresh_from_db()
        last_msg = run.get_last_msg(OUTGOING)
        last_msg.sent_on = timezone.now() - timedelta(minutes=2)
        last_msg.save()

        timeout = timezone.now()
        expiration = run.expires_on

        self._update_timeout(run, timezone.now())

        check_flow_timeouts_task()

        run.refresh_from_db()

        # should have a new outgoing message
        last_msg = run.get_last_msg(OUTGOING)
        self.assertTrue(last_msg.text.find("No seriously, what's your name?") >= 0)

        # fire the task manually, shouldn't change anything (this tests double firing)
        process_run_timeout(run.id, timeout)

        # expiration should still be the same
        run.refresh_from_db()
        self.assertEqual(run.expires_on, expiration)

        new_last_msg = run.get_last_msg(OUTGOING)
        self.assertEqual(new_last_msg, last_msg)

        # ok, now respond
        msg = self.create_msg(contact=self.contact, direction='I', text="Wilson")
        Flow.find_and_handle(msg)

        # should have completed our flow
        run.refresh_from_db()
        self.assertFalse(run.is_active)

        last_msg = run.get_last_msg(OUTGOING)
        self.assertEqual(last_msg.text, "Cool, got it..")

    def test_multi_timeout(self):
        from temba.flows.tasks import check_flow_timeouts_task
        flow = self.get_flow('multi_timeout')

        # start the flow
        flow.start([], [self.contact])

        # create a new message and get it handled
        msg = self.create_msg(contact=self.contact, direction='I', text="Wilson")
        Flow.find_and_handle(msg)

        run = FlowRun.objects.get()

        time.sleep(1)
        self._update_timeout(run, timezone.now())

        check_flow_timeouts_task()

        # nothing should have changed as we haven't yet sent our msg
        self.assertTrue(run.is_active)
        time.sleep(1)

        # ok, mark our message as sent, but only two minutes ago
        last_msg = run.get_last_msg(OUTGOING)
        last_msg.sent_on = timezone.now() - timedelta(minutes=2)
        last_msg.save()
        FlowRun.objects.all().update(timeout_on=timezone.now())
        check_flow_timeouts_task()

        # still nothing should have changed, not enough time has passed, but our timeout should be in the future now
        run.refresh_from_db()
        self.assertTrue(run.is_active)
        self.assertTrue(run.timeout_on > timezone.now() + timedelta(minutes=2))

        # ok, finally mark our message sent a while ago
        last_msg.sent_on = timezone.now() - timedelta(minutes=10)
        last_msg.save()

        time.sleep(1)

        self._update_timeout(run, timezone.now())

        check_flow_timeouts_task()
        run.refresh_from_db()

        # run should be complete now
        self.assertFalse(run.is_active)
        self.assertEqual(run.exit_type, FlowRun.EXIT_TYPE_COMPLETED)

        # and we should have sent our message
        self.assertEqual("Thanks, Wilson",
                         Msg.objects.filter(direction=OUTGOING).order_by('-created_on').first().text)

    def test_timeout(self):
        from temba.flows.tasks import check_flow_timeouts_task
        flow = self.get_flow('timeout')

        # start the flow
        flow.start([], [self.contact])

        # create a new message and get it handled
        msg = self.create_msg(contact=self.contact, direction='I', text="Wilson")
        Flow.find_and_handle(msg)

        # we should have sent a response
        self.assertEqual("Great. Good to meet you Wilson",
                         Msg.objects.filter(direction=OUTGOING).order_by('-created_on').first().text)

        # assert we have exited our flow
        run = FlowRun.objects.get()
        self.assertFalse(run.is_active)
        self.assertEqual(run.exit_type, FlowRun.EXIT_TYPE_COMPLETED)

        # ok, now let's try with a timeout
        FlowRun.objects.all().delete()
        Msg.objects.all().delete()

        # start the flow
        flow.start([], [self.contact])

        # check our timeout is set
        run = FlowRun.objects.get()
        self.assertTrue(run.is_active)
        self.assertTrue(timezone.now() - timedelta(minutes=1) < run.timeout_on > timezone.now() + timedelta(minutes=4))

        # mark our last message as sent
        last_msg = run.get_last_msg(OUTGOING)
        last_msg.sent_on = timezone.now() - timedelta(minutes=5)
        last_msg.save()

        time.sleep(.5)

        # run our timeout check task
        check_flow_timeouts_task()

        # nothing occured as we haven't timed out yet
        run.refresh_from_db()
        self.assertTrue(run.is_active)
        self.assertTrue(timezone.now() - timedelta(minutes=1) < run.timeout_on > timezone.now() + timedelta(minutes=4))

        time.sleep(1)

        # ok, change our timeout to the past
        self._update_timeout(run, timezone.now())

        # check our timeouts again
        check_flow_timeouts_task()
        run.refresh_from_db()

        # run should be complete now
        self.assertFalse(run.is_active)
        self.assertEqual(run.exit_type, FlowRun.EXIT_TYPE_COMPLETED)

        # and we should have sent our message
        self.assertEqual("Don't worry about it , we'll catch up next week.",
                         Msg.objects.filter(direction=OUTGOING).order_by('-created_on').first().text)

    def test_timeout_no_credits(self):
        from temba.flows.tasks import check_flow_timeouts_task
        flow = self.get_flow('timeout')

        # start the flow
        flow.start([], [self.contact])

        # check our timeout is set
        run = FlowRun.objects.get()
        self.assertTrue(run.is_active)
        self.assertTrue(timezone.now() - timedelta(minutes=1) < run.timeout_on > timezone.now() + timedelta(minutes=4))

        # timeout in the past
        FlowRun.objects.all().update(timeout_on=timezone.now())

        # mark our last message as not having a credit
        last_msg = run.get_last_msg(OUTGOING)
        last_msg.topup_id = None
        last_msg.save()

        time.sleep(1)

        # run our timeout check task
        check_flow_timeouts_task()

        # our timeout should be cleared
        run.refresh_from_db()
        self.assertTrue(run.is_active)
        self.assertIsNone(run.timeout_on)


class MigrationUtilsTest(TembaTest):

    def test_map_actions(self):
        # minimalist flow def with just actions and entry
        flow_def = dict(entry='1234', action_sets=[dict(uuid='1234', x=100, y=0, actions=[dict(type='reply', msg=None)])], rule_sets=[dict(y=10, x=100, uuid='5678')])
        removed = map_actions(flow_def, lambda x: None)

        # no more action sets and entry is remapped
        self.assertFalse(removed['action_sets'])
        self.assertEqual('5678', removed['entry'])

        # add two action sets, we should remap entry to be the first
        flow_def['action_sets'] = [dict(uuid='1234', y=0, x=100, actions=[dict(type='reply', msg=None)]), dict(uuid='2345', y=5, x=100, actions=[dict(type='reply', msg="foo")])]
        removed = map_actions(flow_def, lambda x: None if x['msg'] is None else x)

        self.assertEqual(len(removed['action_sets']), 1)
        self.assertEqual(removed['action_sets'][0]['uuid'], '2345')
        self.assertEqual(removed['entry'], '2345')

        # remove a single action
        flow_def['action_sets'] = [dict(uuid='1234', y=10, x=100, actions=[dict(type='reply', msg=None), dict(type='reply', msg="foo")])]
        removed = map_actions(flow_def, lambda x: None if x['msg'] is None else x)

        self.assertEqual(len(removed['action_sets']), 1)
        self.assertEqual(len(removed['action_sets'][0]['actions']), 1)
        self.assertEqual(removed['entry'], '2345')

        # no entry
        flow_def = dict(entry='1234', action_sets=[dict(uuid='1234', y=0, x=100, actions=[dict(type='reply', msg=None)])], rule_sets=[])
        removed = map_actions(flow_def, lambda x: None if x['msg'] is None else x)

        self.assertEqual(len(removed['action_sets']), 0)
        self.assertEqual(removed['entry'], None)

        # check entry horizontal winner
        flow_def = dict(entry='1234', action_sets=[dict(uuid='1234', x=100, y=0, actions=[dict(type='reply', msg=None)])], rule_sets=[dict(y=10, x=100, uuid='5678'), dict(y=10, x=50, uuid='9012')])
        removed = map_actions(flow_def, lambda x: None if x['msg'] is None else x)
        self.assertEqual(removed['entry'], '9012')

        # same horizontal check with action sets
        flow_def = dict(entry='1234', action_sets=[
            dict(uuid='1234', x=100, y=0, actions=[dict(type='reply', msg=None)]),
            dict(uuid='9012', x=50, y=50, actions=[dict(type='reply', msg="foo")]),
            dict(uuid='3456', x=0, y=50, actions=[dict(type='reply', msg="foo")])
        ], rule_sets=[
            dict(y=100, x=100, uuid='5678')
        ])

        removed = map_actions(flow_def, lambda x: None if x['msg'] is None else x)
        self.assertEqual(removed['entry'], '3456')


class TriggerFlowTest(FlowFileTest):

    def test_trigger_then_loop(self):
        # start our parent flow
        flow = self.get_flow('parent_child_loop')
        flow.start([], [self.contact])

        # trigger our second flow to start
        msg = self.create_msg(contact=self.contact, direction='I', text="add 12067797878")
        Flow.find_and_handle(msg)

        child_run = FlowRun.objects.get(contact__urns__path="+12067797878")
        msg = self.create_msg(contact=child_run.contact, direction='I', text="Christine")
        Flow.find_and_handle(msg)
        child_run.refresh_from_db()
        self.assertEqual('C', child_run.exit_type)

        # main contact should still be in the flow
        run = FlowRun.objects.get(flow=flow, contact=self.contact)
        self.assertTrue(run.is_active)
        self.assertIsNone(run.exit_type)

        # and can do it again
        msg = self.create_msg(contact=self.contact, direction='I', text="add 12067798080")
        Flow.find_and_handle(msg)

        FlowRun.objects.get(contact__urns__path="+12067798080")
        run.refresh_from_db()
        self.assertTrue(run.is_active)


class StackedExitsTest(FlowFileTest):

    def setUp(self):
        super(StackedExitsTest, self).setUp()

        self.channel.delete()
        self.channel = Channel.create(self.org, self.user, 'KE', 'EX', None, '+250788123123', schemes=['tel'],
                                      config=dict(send_url='https://google.com'))

    def test_stacked_exits(self):
        self.get_flow('stacked_exits')
        flow = Flow.objects.get(name="Stacked")

        flow.start([], [self.contact])

        msgs = Msg.objects.filter(contact=self.contact).order_by('sent_on')
        self.assertEqual(3, msgs.count())
        self.assertEqual("Start!", msgs[0].text)
        self.assertEqual("Leaf!", msgs[1].text)
        self.assertEqual("End!", msgs[2].text)

        runs = FlowRun.objects.filter(contact=self.contact, exit_type=FlowRun.EXIT_TYPE_COMPLETED).order_by('exited_on')
        self.assertEqual(3, runs.count())
        self.assertEqual("Stacker Leaf", runs[0].flow.name)
        self.assertEqual("Stacker", runs[1].flow.name)
        self.assertEqual("Stacked", runs[2].flow.name)

    def test_stacked_webhook_exits(self):
        self.get_flow('stacked_webhook_exits')
        flow = Flow.objects.get(name="Stacked")

        flow.start([], [self.contact])

        msgs = Msg.objects.filter(contact=self.contact).order_by('sent_on')
        self.assertEqual(4, msgs.count())
        self.assertEqual("Start!", msgs[0].text)
        self.assertEqual("Leaf!", msgs[1].text)
        self.assertEqual("Middle!", msgs[2].text)
        self.assertEqual("End!", msgs[3].text)

        runs = FlowRun.objects.filter(contact=self.contact, exit_type=FlowRun.EXIT_TYPE_COMPLETED).order_by('exited_on')
        self.assertEqual(3, runs.count())
        self.assertEqual("Stacker Leaf", runs[0].flow.name)
        self.assertEqual("Stacker", runs[1].flow.name)
        self.assertEqual("Stacked", runs[2].flow.name)

    def test_response_exits(self):
        self.get_flow('stacked_response_exits')
        flow = Flow.objects.get(name="Stacked")

        flow.start([], [self.contact])

        msgs = Msg.objects.filter(contact=self.contact).order_by('sent_on')
        self.assertEqual(2, msgs.count())
        self.assertEqual("Start!", msgs[0].text)
        self.assertEqual("Send something!", msgs[1].text)

        # nobody completed yet
        self.assertEqual(0, FlowRun.objects.filter(contact=self.contact, exit_type=FlowRun.EXIT_TYPE_COMPLETED).count())

        # ok, send a response, should unwind all our flows
        msg = self.create_msg(contact=self.contact, direction='I', text="something")
        Msg.process_message(msg)

        msgs = Msg.objects.filter(contact=self.contact, direction='O').order_by('sent_on')
        self.assertEqual(3, msgs.count())
        self.assertEqual("Start!", msgs[0].text)
        self.assertEqual("Send something!", msgs[1].text)
        self.assertEqual("End!", msgs[2].text)

        runs = FlowRun.objects.filter(contact=self.contact, exit_type=FlowRun.EXIT_TYPE_COMPLETED).order_by('exited_on')
        self.assertEqual(3, runs.count())
        self.assertEqual("Stacker Leaf", runs[0].flow.name)
        self.assertEqual("Stacker", runs[1].flow.name)
        self.assertEqual("Stacked", runs[2].flow.name)


class ParentChildOrderingTest(FlowFileTest):

    def setUp(self):
        super(ParentChildOrderingTest, self).setUp()
        self.channel.delete()
        self.channel = Channel.create(self.org, self.user, 'KE', 'EX', None, '+250788123123', schemes=['tel'],
                                      config=dict(send_url='https://google.com'))

    def test_parent_child_ordering(self):
        from temba.channels.tasks import send_msg_task
        self.get_flow('parent_child_ordering')
        flow = Flow.objects.get(name="Parent Flow")

        with patch('temba.channels.tasks.send_msg_task', wraps=send_msg_task) as mock_send_msg:
            flow.start([], [self.contact])

            # get the msgs for our contact
            msgs = Msg.objects.filter(contact=self.contact).order_by('sent_on')
            self.assertEqual(msgs[0].text, "Parent 1")
            self.assertEqual(msgs[1].text, "Child Msg")

            self.assertEqual(mock_send_msg.call_count, 1)


class AndroidChildStatus(FlowFileTest):
    def setUp(self):
        super(AndroidChildStatus, self).setUp()
        self.channel.delete()
        self.channel = Channel.create(self.org, self.user, 'RW', 'A', None, '+250788123123', schemes=['tel'])

    def test_split_first(self):
        self.get_flow('split_first_child_msg')

        incoming = self.create_msg(direction=INCOMING, contact=self.contact, text="split")
        self.assertTrue(Trigger.find_and_handle(incoming))

        # get the msgs for our contact
        msgs = Msg.objects.filter(contact=self.contact, status=PENDING, direction=OUTGOING).order_by('created_on')
        self.assertEqual(msgs[0].text, "Child Msg 1")

        # respond
        msg = self.create_msg(contact=self.contact, direction='I', text="Response")
        Flow.find_and_handle(msg)

        msgs = Msg.objects.filter(contact=self.contact, status=PENDING, direction=OUTGOING).order_by('created_on')
        self.assertEqual(msgs[0].text, "Child Msg 1")
        self.assertEqual(msgs[1].text, "Child Msg 2")


class QueryTest(FlowFileTest):

    @override_settings(SEND_WEBHOOKS=True)
    def test_num_queries(self):

        self.get_flow('query_test')
        flow = Flow.objects.filter(name="Query Test").first()

        # mock our webhook call which will get triggered in the flow
        self.mockRequest('GET', '/ip_test', '{"ip":"192.168.1.1"}', content_type='application/json')
        with QueryTracker(assert_query_count=147, stack_count=10, skip_unique_queries=True):
            flow.start([], [self.contact])


class FlowChannelSelectionTest(FlowFileTest):

    def setUp(self):
        super(FlowChannelSelectionTest, self).setUp()
        self.channel.delete()
        self.sms_channel = Channel.create(
            self.org, self.user, 'RW', 'JN', None, '+250788123123',
            schemes=['tel'], uuid='00000000-0000-0000-0000-000000001111',
            role=Channel.DEFAULT_ROLE)
        self.ussd_channel = Channel.create(
            self.org, self.user, 'RW', 'JNU', None, '*123#',
            schemes=['tel'], uuid='00000000-0000-0000-0000-000000002222',
            role=Channel.ROLE_USSD)

    def test_sms_channel_selection(self):
        contact_urn = self.contact.get_urn(TEL_SCHEME)
        channel = self.contact.org.get_send_channel(contact_urn=contact_urn)
        self.assertEqual(channel, self.sms_channel)

    def test_ussd_channel_selection(self):
        contact_urn = self.contact.get_urn(TEL_SCHEME)
        channel = self.contact.org.get_ussd_channel(contact_urn=contact_urn)
        self.assertEqual(channel, self.ussd_channel)


class FlowTriggerTest(TembaTest):

    def test_group_trigger(self):
        flow = self.get_flow('favorites')

        contact = self.create_contact("Joe", "+250788373373")
        group = self.create_group("Contact Group", [contact])

        # create a trigger, first just for the contact
        contact_trigger = Trigger.objects.create(org=self.org, flow=flow, trigger_type=Trigger.TYPE_SCHEDULE,
                                                 created_by=self.admin, modified_by=self.admin)
        contact_trigger.contacts.add(contact)

        # fire it manually
        contact_trigger.fire()

        # contact should be added to flow
        self.assertEqual(1, FlowRun.objects.filter(flow=flow, contact=contact).count())

        # but no flow starts were created
        self.assertEqual(0, FlowStart.objects.all().count())

        # now create a trigger for the group
        group_trigger = Trigger.objects.create(org=self.org, flow=flow, trigger_type=Trigger.TYPE_SCHEDULE,
                                               created_by=self.admin, modified_by=self.admin)
        group_trigger.groups.add(group)

        group_trigger.fire()

        # contact should be added to flow again
        self.assertEqual(2, FlowRun.objects.filter(flow=flow, contact=contact).count())

        # and we should have a flow start
        start = FlowStart.objects.get()
        self.assertEqual(0, start.contacts.all().count())
        self.assertEqual(1, start.groups.filter(id=group.id).count())

        # clear our the group on our group trigger
        group_trigger.groups.clear()

        # refire
        group_trigger.fire()

        # nothing should have changed
        self.assertEqual(2, FlowRun.objects.filter(flow=flow, contact=contact).count())
        self.assertEqual(1, FlowStart.objects.all().count())


class TypeTest(TembaTest):

    def test_value_types(self):

        contact = self.create_contact("Joe", "+250788373373")
        self.get_flow('type_flow')

        self.assertEqual(Value.TYPE_TEXT, RuleSet.objects.get(label="Text").value_type)
        self.assertEqual(Value.TYPE_DATETIME, RuleSet.objects.get(label="Date").value_type)
        self.assertEqual(Value.TYPE_DECIMAL, RuleSet.objects.get(label="Number").value_type)
        self.assertEqual(Value.TYPE_STATE, RuleSet.objects.get(label="State").value_type)
        self.assertEqual(Value.TYPE_DISTRICT, RuleSet.objects.get(label="District").value_type)
        self.assertEqual(Value.TYPE_WARD, RuleSet.objects.get(label="Ward").value_type)

        incoming = self.create_msg(direction=INCOMING, contact=contact, text="types")
        self.assertTrue(Trigger.find_and_handle(incoming))

        self.assertTrue(Flow.find_and_handle(self.create_msg(contact=contact, direction=INCOMING, text="Some Text")))
        self.assertTrue(Flow.find_and_handle(self.create_msg(contact=contact, direction=INCOMING, text="not a date")))

        results = FlowRun.objects.get().results

        self.assertEqual('Text', results['text']['name'])
        self.assertEqual('Some Text', results['text']['value'])
        self.assertEqual('Some Text', results['text']['input'])
        self.assertEqual('All Responses', results['text']['category'])

        self.assertEqual('Date', results['date']['name'])
        self.assertEqual("not a date", results['date']['value'])
        self.assertEqual('not a date', results['date']['input'])
        self.assertEqual('Other', results['date']['category'])

        self.assertTrue(Flow.find_and_handle(self.create_msg(contact=contact, direction=INCOMING, text="Born 23/06/1977")))
        self.assertTrue(Flow.find_and_handle(self.create_msg(contact=contact, direction=INCOMING, text="The number is 10")))
        self.assertTrue(Flow.find_and_handle(self.create_msg(contact=contact, direction=INCOMING, text="I'm in Eastern Province")))
        self.assertTrue(Flow.find_and_handle(self.create_msg(contact=contact, direction=INCOMING, text="That's in Gatsibo")))
        self.assertTrue(Flow.find_and_handle(self.create_msg(contact=contact, direction=INCOMING, text="ya ok that's Kageyo")))

        results = FlowRun.objects.get().results

        self.assertEqual('Text', results['text']['name'])
        self.assertEqual('Some Text', results['text']['value'])
        self.assertEqual('Some Text', results['text']['input'])
        self.assertEqual('All Responses', results['text']['category'])

        self.assertEqual('Date', results['date']['name'])
        self.assertTrue(results['date']['value'].startswith("1977-06-23T"))
        self.assertEqual('Born 23/06/1977', results['date']['input'])
        self.assertEqual('is a date', results['date']['category'])

        self.assertEqual('Number', results['number']['name'])
        self.assertEqual('10', results['number']['value'])
        self.assertEqual('The number is 10', results['number']['input'])
        self.assertEqual('numeric', results['number']['category'])

        self.assertEqual('State', results['state']['name'])
        self.assertEqual('Rwanda > Eastern Province', results['state']['value'])
        self.assertEqual('I\'m in Eastern Province', results['state']['input'])
        self.assertEqual('state', results['state']['category'])
        self.assertNotIn('category_localized', results['state'])

        self.assertEqual('District', results['district']['name'])
        self.assertEqual('Rwanda > Eastern Province > Gatsibo', results['district']['value'])
        self.assertEqual('That\'s in Gatsibo', results['district']['input'])
        self.assertEqual('district', results['district']['category'])
        self.assertEqual('le district', results['district']['category_localized'])

        self.assertEqual('Ward', results['ward']['name'])
        self.assertEqual('Rwanda > Eastern Province > Gatsibo > Kageyo', results['ward']['value'])
        self.assertEqual('ya ok that\'s Kageyo', results['ward']['input'])
        self.assertEqual('ward', results['ward']['category'])


class FlowServerTest(TembaTest):
    def setUp(self):
        super(FlowServerTest, self).setUp()

        self.contact = self.create_contact("Joe", "+250788373373")

    @skip_if_no_flowserver
    @override_settings(FLOW_SERVER_AUTH_TOKEN='1234', FLOW_SERVER_FORCE=True)
    def test_session_bulk_start(self):
        flow = self.get_flow('favorites')

        # regular start
        run1, = flow.start([], [self.contact])

        self.assertTrue(run1.session.output)
        self.assertEqual(run1.session.status, 'W')
        self.assertEqual(run1.flow, flow)
        self.assertEqual(run1.contact, self.contact)

        # regular re-start
        run2, = flow.start([], [self.contact], restart_participants=True)

        self.assertTrue(run2.session.output)
        self.assertNotEqual(run1, run2)

        # with flow start object
        start = FlowStart.create(flow, self.admin, [], [self.contact], restart_participants=True)
        run3, = start.start()

        self.assertTrue(run3.session.output)
        self.assertNotEqual(run1, run3)

        start.refresh_from_db()

        self.assertEqual(start.status, 'C')
        self.assertEqual(set(start.runs.all()), {run3})

        # with some extra
        run4, = flow.start([], [self.contact], restart_participants=True, extra={'foo': "bar"})

        self.assertTrue(run4.session.output['trigger']['params'], {'foo': "bar"})

        # with an initial message
        msg = self.create_msg(direction='I', text="Hello", contact=self.contact)
        run5, = flow.start([], [self.contact], restart_participants=True, start_msg=msg)
        run5_output = run5.session.output['runs'][0]

        self.assertTrue(run5_output['path'][0]['events'][0]['type'], "msg_received")
        self.assertTrue(run5_output['path'][0]['events'][0]['msg']['text'], "Hello")

        # when flowserver returns an error
        with patch('temba.utils.goflow.FlowServerClient.start') as mock_start:
            mock_start.side_effect = FlowServerException("nope")

            self.assertEqual(flow.start([], [self.contact], restart_participants=True), [])

    @skip_if_no_flowserver
    @override_settings(FLOW_SERVER_AUTH_TOKEN='1234', FLOW_SERVER_FORCE=True)
    def test_session_resume(self):
        flow = self.get_flow('favorites')

        run1, = flow.start([], [self.contact])

        # resume with an incoming message
        msg1 = self.create_msg(direction='I', text="Blue", contact=self.contact)
        run1.session.resume(msg_in=msg1)

        run1.refresh_from_db()
        self.assertIn('color', run1.results)

        # when flowserver returns an error
        with patch('temba.utils.goflow.FlowServerClient.resume') as mock_resume:
            mock_resume.side_effect = FlowServerException("nope")

            msg2 = self.create_msg(direction='I', text="Primus", contact=self.contact)
            run1.session.resume(msg_in=msg2)

        run1.refresh_from_db()
        self.assertEqual(run1.session.status, 'F')
        self.assertEqual(run1.exit_type, 'C')
        self.assertIsNotNone(run1.exited_on)


class AssetServerTest(TembaTest):
    @override_settings(FLOW_SERVER_AUTH_TOKEN="112233445566")
    def test_authentication(self):
        flows_url = reverse('flows.flow_assets', args=[self.org.id, "1234", "flow"])

        # can't access without auth token
        response = self.client.get(flows_url)
        self.assertEqual(response.status_code, 302)

        # can't access with incorrect auth token
        response = self.client.get(flows_url, HTTP_AUTHORIZATION='Token 77888')
        self.assertEqual(response.status_code, 302)

        # can access with correct auth token
        response = self.client.get(flows_url, HTTP_AUTHORIZATION='Token 112233445566')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), [])

        # can access as regular user too
        self.login(self.admin)

        response = self.client.get(flows_url)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), [])

    @skip_if_no_flowserver
    def test_flows(self):
        flow1 = self.get_flow('color')
        flow2 = self.get_flow('favorites')
        self.login(self.admin)

        # get all flows
        response = self.client.get('/flow/assets/%d/1234/flow/' % self.org.id)
        resp_json = response.json()
        self.assertEqual(len(resp_json), 2)
        self.assertEqual(resp_json[0]['uuid'], str(flow1.uuid))
        self.assertEqual(resp_json[1]['uuid'], str(flow2.uuid))

        # get a specific flow
        response = self.client.get('/flow/assets/%d/1234/flow/%s/' % (self.org.id, str(flow2.uuid)))
        resp_json = response.json()
        self.assertEqual(resp_json['uuid'], str(flow2.uuid))

    def test_location_hierarchy(self):
        self.login(self.admin)

        response = self.client.get('/flow/assets/%d/1234/location_hierarchy/' % self.org.id)
        resp_json = response.json()
        self.assertEqual(resp_json, {
            'name': 'Rwanda',
            'children': [
                {
                    'name': 'Kigali City',
                    'children': [{'name': 'Nyarugenge'}],
                },
                {
                    'name': 'Eastern Province',
                    'children': [
                        {
                            'name': 'Gatsibo',
                            'children': [{'name': 'Kageyo'}]
                        },
                        {
                            'name': 'Kay\xf4nza',
                            'children': [{'name': 'Kabare'}]
                        },
                        {
                            'name': 'Rwamagana',
                            'children': [{'name': 'Bukure'}]
                        }
                    ],
                }
            ],
        })
