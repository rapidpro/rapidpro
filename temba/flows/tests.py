# -*- coding: utf-8 -*-
from __future__ import absolute_import, print_function, unicode_literals

import datetime
import json
import os
import pytz
import re
import six
import time

from datetime import timedelta
from decimal import Decimal
from django.conf import settings
from django.core import mail
from django.core.urlresolvers import reverse
from django.db.models import Prefetch
from django.test.utils import override_settings
from django.utils import timezone
from mock import patch
from openpyxl import load_workbook
from temba.airtime.models import AirtimeTransfer
from temba.api.models import WebHookEvent, Resthook
from temba.channels.models import Channel, ChannelEvent
from temba.contacts.models import Contact, ContactGroup, ContactField, ContactURN, URN, TEL_SCHEME
from temba.ivr.models import IVRCall
from temba.ussd.models import USSDSession
from temba.locations.models import AdminBoundary, BoundaryAlias
from temba.msgs.models import Broadcast, Label, Msg, INCOMING, PENDING, FLOW, WIRED, OUTGOING, FAILED
from temba.orgs.models import Language, CURRENT_EXPORT_VERSION
from temba.tests import TembaTest, MockResponse, FlowFileTest
from temba.triggers.models import Trigger
from temba.utils import datetime_to_str, str_to_datetime
from temba.values.models import Value
from uuid import uuid4
from .flow_migrations import migrate_to_version_5, migrate_to_version_6, migrate_to_version_7
from .flow_migrations import migrate_to_version_8, migrate_to_version_9, migrate_export_to_version_9
from .models import Flow, FlowStep, FlowRun, FlowLabel, FlowStart, FlowRevision, FlowException, ExportFlowResultsTask
from .models import ActionSet, RuleSet, Action, Rule, FlowRunCount, FlowPathCount, InterruptTest, get_flow_user
from .models import FlowPathRecentMessage, Test, TrueTest, FalseTest, AndTest, OrTest, PhoneTest, NumberTest
from .models import EqTest, LtTest, LteTest, GtTest, GteTest, BetweenTest, ContainsOnlyPhraseTest, ContainsPhraseTest
from .models import DateEqualTest, DateAfterTest, DateBeforeTest, HasDateTest
from .models import StartsWithTest, ContainsTest, ContainsAnyTest, RegexTest, NotEmptyTest
from .models import HasStateTest, HasDistrictTest, HasWardTest, HasEmailTest
from .models import SendAction, AddLabelAction, AddToGroupAction, ReplyAction, SaveToContactAction, SetLanguageAction, SetChannelAction
from .models import EmailAction, StartFlowAction, TriggerFlowAction, DeleteFromGroupAction, WebhookAction, ActionLog
from .models import VariableContactAction, UssdAction
from .views import FlowCRUDL
from .flow_migrations import map_actions
from .tasks import update_run_expirations_task, prune_recentmessages, squash_flowruncounts, squash_flowpathcounts


class FlowTest(TembaTest):

    def setUp(self):
        super(FlowTest, self).setUp()

        self.contact = self.create_contact('Eric', '+250788382382')
        self.contact2 = self.create_contact('Nic', '+250788383383')
        self.contact3 = self.create_contact('Norbert', '+250788123456')

        self.flow = self.create_flow(name="Color Flow", base_language='base', definition=self.COLOR_FLOW_DEFINITION)

        self.other_group = self.create_group("Other", [])

    def export_flow_results(self, flow, responded_only=False, include_msgs=True, include_runs=True, contact_fields=None):
        """
        Exports results for the given flow and returns the generated workbook
        """
        self.login(self.admin)

        form = {
            'flows': [flow.id],
            'responded_only': responded_only,
            'include_messages': include_msgs,
            'include_runs': include_runs
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

    def test_flow_get_results_queries(self):
        contact3 = self.create_contact('George', '+250788382234')
        self.flow.start([], [self.contact, self.contact2, contact3])

        with self.assertNumQueries(13):
            runs = FlowRun.objects.filter(flow=self.flow)
            for run_elt in runs:
                self.flow.get_results(contact=run_elt.contact, run=run_elt)

        # still perform ruleset lookup 7 queries because flow and flow__org select_related
        with self.assertNumQueries(7):
            steps_prefetch = Prefetch('steps', queryset=FlowStep.objects.order_by('arrived_on'))

            rulesets_prefetch = Prefetch('flow__rule_sets',
                                         queryset=RuleSet.objects.exclude(label=None).order_by('pk'),
                                         to_attr='ruleset_prefetch')

            # use prefetch rather than select_related for foreign keys flow/contact to avoid joins
            runs = FlowRun.objects.filter(flow=self.flow).prefetch_related('flow', rulesets_prefetch, steps_prefetch,
                                                                           'steps__messages', 'contact')
            for run_elt in runs:
                self.flow.get_results(contact=run_elt.contact, run=run_elt)

        flow2 = self.get_flow('no_ruleset_flow')
        flow2.start([], [self.contact, self.contact2, contact3])

        with self.assertNumQueries(13):
            runs = FlowRun.objects.filter(flow=flow2)
            for run_elt in runs:
                flow2.get_results(contact=run_elt.contact, run=run_elt)

        # no ruleset do not look up rulesets at all; 6 queries because no org query from flow__org select related too
        with self.assertNumQueries(6):
            steps_prefetch = Prefetch('steps', queryset=FlowStep.objects.order_by('arrived_on'))

            rulesets_prefetch = Prefetch('flow__rule_sets',
                                         queryset=RuleSet.objects.exclude(label=None).order_by('pk'),
                                         to_attr='ruleset_prefetch')

            # use prefetch rather than select_related for foreign keys flow/contact to avoid joins
            runs = FlowRun.objects.filter(flow=flow2).prefetch_related('flow', rulesets_prefetch, steps_prefetch,
                                                                       'steps__messages', 'contact')
            for run_elt in runs:
                flow2.get_results(contact=run_elt.contact, run=run_elt)

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
                self.assertEquals(path, expected_path)

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
        self.assertEquals(2, revisions.count())
        self.assertEquals(1, revisions[0].revision)
        self.assertEquals(2, revisions[1].revision)

        self.assertEquals(CURRENT_EXPORT_VERSION, revisions[0].spec_version)
        self.assertEquals(CURRENT_EXPORT_VERSION, revisions[0].as_json()['version'])
        self.assertEquals('base', revisions[0].get_definition_json()['base_language'])

        # now make one revision invalid
        revision = revisions[1]
        definition = revision.get_definition_json()
        del definition['base_language']
        revision.definition = json.dumps(definition)
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
        revision.definition = json.dumps(definition)
        revision.save()

        # no valid revisions (but we didn't throw!)
        response = self.client.get(reverse('flows.flow_revisions', args=[self.flow.pk]))
        self.assertEquals(0, len(response.json()))

    def test_get_localized_text(self):

        text_translations = dict(eng="Hello", esp="Hola", fre="Salut")

        # use default when flow, contact and org don't have language set
        self.assertEqual(self.flow.get_localized_text(text_translations, self.contact, "Hi"), "Hi")

        # flow language used regardless of whether it's an org language
        self.flow.base_language = 'eng'
        self.flow.save(update_fields=('base_language',))
        self.assertEqual(self.flow.get_localized_text(text_translations, self.contact, "Hi"), "Hello")

        Language.create(self.org, self.admin, "English", 'eng')
        esp = Language.create(self.org, self.admin, "Spanish", 'esp')

        # flow language now valid org language
        self.assertEqual(self.flow.get_localized_text(text_translations, self.contact, "Hi"), "Hello")

        # org primary language overrides flow language
        self.flow.org.primary_language = esp
        self.flow.org.save(update_fields=('primary_language',))
        self.assertEqual(self.flow.get_localized_text(text_translations, self.contact, "Hi"), "Hola")

        # contact language doesn't override if it's not an org language
        self.contact.language = 'fre'
        self.contact.save(update_fields=('language',))
        self.assertEqual(self.flow.get_localized_text(text_translations, self.contact, "Hi"), "Hola")

        # does override if it is
        Language.create(self.org, self.admin, "French", 'fre')
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
        self.assertEquals(2, response.context['folders'][0]['count'])
        self.assertEquals(1, response.context['folders'][1]['count'])

        # archive it
        post_data = dict(action='archive', objects=self.flow.pk)
        self.client.post(reverse('flows.flow_list'), post_data)
        response = self.client.get(reverse('flows.flow_list'))
        self.assertNotContains(response, self.flow.name)
        self.assertContains(response, flow3.name)
        self.assertEquals(1, response.context['folders'][0]['count'])
        self.assertEquals(2, response.context['folders'][1]['count'])

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
        self.assertEquals(2, response.context['folders'][0]['count'])
        self.assertEquals(1, response.context['folders'][1]['count'])

        # voice flows should be included in the count
        Flow.objects.filter(pk=self.flow.pk).update(flow_type=Flow.VOICE)

        response = self.client.get(reverse('flows.flow_list'))
        self.assertContains(response, self.flow.name)
        self.assertEquals(2, response.context['folders'][0]['count'])
        self.assertEquals(1, response.context['folders'][1]['count'])

        # single message flow (flom campaign) should not be included in counts and not even on this list
        Flow.objects.filter(pk=self.flow.pk).update(flow_type=Flow.MESSAGE)

        response = self.client.get(reverse('flows.flow_list'))

        self.assertNotContains(response, self.flow.name)
        self.assertEquals(1, response.context['folders'][0]['count'])
        self.assertEquals(1, response.context['folders'][1]['count'])

        # single message flow should not be even in the archived list
        Flow.objects.filter(pk=self.flow.pk).update(flow_type=Flow.MESSAGE, is_archived=True)

        response = self.client.get(reverse('flows.flow_archived'))
        self.assertNotContains(response, self.flow.name)
        self.assertEquals(1, response.context['folders'][0]['count'])
        self.assertEquals(1, response.context['folders'][1]['count'])  # only flow2

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

    def test_flow_read(self):
        self.login(self.admin)
        response = self.client.get(reverse('flows.flow_read', args=[self.flow.uuid]))
        self.assertTrue('initial' in response.context)

    def test_flow_editor(self):
        self.login(self.admin)
        response = self.client.get(reverse('flows.flow_editor', args=[self.flow.uuid]))
        self.assertTrue('mutable' in response.context)
        self.assertTrue('has_airtime_service' in response.context)

        self.login(self.superuser)
        response = self.client.get(reverse('flows.flow_editor', args=[self.flow.uuid]))
        self.assertTrue('mutable' in response.context)
        self.assertTrue('has_airtime_service' in response.context)

    def test_states(self):
        # set our flow
        entry = ActionSet.objects.filter(uuid=self.flow.entry_uuid)[0]

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
        self.assertEqual(broadcast.text, {'base': "What is your favorite color?", 'fre': "Quelle est votre couleur préférée?"})
        self.assertEqual(set(broadcast.contacts.all()), {self.contact, self.contact2})
        self.assertEqual(broadcast.base_language, 'base')

        # each contact should have received a single message
        contact1_msg = broadcast.msgs.get(contact=self.contact)
        self.assertEqual(contact1_msg.text, "What is your favorite color?")
        self.assertEqual(contact1_msg.status, PENDING)
        self.assertEqual(contact1_msg.priority, Msg.PRIORITY_NORMAL)

        # should have a flow run for each contact
        contact1_run = FlowRun.objects.get(contact=self.contact)
        contact2_run = FlowRun.objects.get(contact=self.contact2)

        self.assertEqual(contact1_run.flow, self.flow)
        self.assertEqual(contact1_run.contact, self.contact)
        self.assertFalse(contact1_run.responded)
        self.assertFalse(contact2_run.responded)

        # should have two steps, one for the outgoing message, another for the rule set we are now waiting on
        contact1_steps = list(FlowStep.objects.filter(run__contact=self.contact).order_by('pk'))
        contact2_steps = list(FlowStep.objects.filter(run__contact=self.contact2).order_by('pk'))

        self.assertEqual(len(contact1_steps), 2)
        self.assertEqual(len(contact2_steps), 2)

        # check our steps for contact #1
        self.assertEqual(six.text_type(contact1_steps[0]), "Eric - A:d51ec25f-04e6-4349-a448-e7c4d93d4597")
        self.assertEqual(contact1_steps[0].step_uuid, entry.uuid)
        self.assertEqual(contact1_steps[0].step_type, FlowStep.TYPE_ACTION_SET)
        self.assertEqual(contact1_steps[0].contact, self.contact)
        self.assertTrue(contact1_steps[0].arrived_on)
        self.assertTrue(contact1_steps[0].left_on)
        self.assertEqual(set(contact1_steps[0].broadcasts.all()), {broadcast})
        self.assertEqual(set(contact1_steps[0].messages.all()), {contact1_msg})
        self.assertEqual(contact1_steps[0].next_uuid, entry.destination)

        self.assertEqual(six.text_type(contact1_steps[1]), "Eric - R:bd531ace-911e-4722-8e53-6730d6122fe1")
        self.assertEqual(contact1_steps[1].step_uuid, entry.destination)
        self.assertEqual(contact1_steps[1].step_type, FlowStep.TYPE_RULE_SET)
        self.assertEqual(contact1_steps[1].contact, self.contact)
        self.assertTrue(contact1_steps[1].arrived_on)
        self.assertEqual(contact1_steps[1].left_on, None)
        self.assertEqual(set(contact1_steps[1].messages.all()), set())
        self.assertEqual(contact1_steps[1].next_uuid, None)

        # test our message context
        context = self.flow.build_expressions_context(self.contact, None)
        self.assertEquals(dict(__default__=''), context['flow'])

        # check flow activity endpoint response
        self.login(self.admin)

        test_contact = Contact.get_test_contact(self.admin)
        test_message = self.create_msg(contact=test_contact, text='Hi')

        activity = json.loads(self.client.get(reverse('flows.flow_activity', args=[self.flow.pk])).content)
        self.assertEquals(2, activity['visited']["d51ec25f-04e6-4349-a448-e7c4d93d4597:bd531ace-911e-4722-8e53-6730d6122fe1"])
        self.assertEquals(2, activity['activity']["bd531ace-911e-4722-8e53-6730d6122fe1"])
        self.assertEquals(activity['messages'], [])

        # check activity with IVR test call
        IVRCall.create_incoming(self.channel, test_contact, test_contact.get_urn(), self.admin, 'CallSid')
        activity = json.loads(self.client.get(reverse('flows.flow_activity', args=[self.flow.pk])).content)
        self.assertEquals(2, activity['visited']["d51ec25f-04e6-4349-a448-e7c4d93d4597:bd531ace-911e-4722-8e53-6730d6122fe1"])
        self.assertEquals(2, activity['activity']["bd531ace-911e-4722-8e53-6730d6122fe1"])
        self.assertTrue(activity['messages'], [test_message.as_json()])

        # if we try to get contacts at this step for our compose we should have two contacts
        self.login(self.admin)
        response = self.client.get(reverse('contacts.contact_omnibox') + "?s=%s" % contact1_steps[1].step_uuid)
        contact_json = response.json()
        self.assertEquals(2, len(contact_json['results']))
        self.client.logout()

        # set the flow as inactive, shouldn't react to replies
        self.flow.is_archived = True
        self.flow.save()

        # create and send a reply
        incoming = self.create_msg(direction=INCOMING, contact=self.contact, text="Orange")
        self.assertFalse(Flow.find_and_handle(incoming)[0])

        # no reply, our flow isn't active
        self.assertFalse(Msg.objects.filter(response_to=incoming))
        step = FlowStep.objects.get(pk=contact1_steps[1].pk)
        self.assertFalse(step.left_on)
        self.assertFalse(step.messages.all())

        # ok, make our flow active again
        self.flow.is_archived = False
        self.flow.save()

        # simulate a response from contact #1
        incoming = self.create_msg(direction=INCOMING, contact=self.contact, text="orange")
        self.assertTrue(Flow.find_and_handle(incoming)[0])

        contact1_run.refresh_from_db()
        self.assertTrue(contact1_run.responded)

        # our message should have gotten a reply
        reply = Msg.objects.get(response_to=incoming)
        self.assertEquals(self.contact, reply.contact)
        self.assertEquals("I love orange too! You said: orange which is category: Orange You are: 0788 382 382 SMS: orange Flow: color: orange", reply.text)

        # should be high priority
        self.assertEqual(reply.priority, Msg.PRIORITY_HIGH)

        # our previous state should be executed
        step = FlowStep.objects.get(run__contact=self.contact, pk=step.id)
        self.assertTrue(step.left_on)
        self.assertEquals(step.messages.all()[0].msg_type, 'F')

        # it should contain what rule matched and what came next
        self.assertEquals("1c75fd71-027b-40e8-a819-151a0f8140e6", step.rule_uuid)
        self.assertEquals("Orange", step.rule_category)
        self.assertEquals("orange", step.rule_value)
        self.assertFalse(step.rule_decimal_value)
        self.assertEquals("7d40faea-723b-473d-8999-59fb7d3c3ca2", step.next_uuid)
        self.assertTrue(incoming in step.messages.all())

        # we should also have a Value for this RuleSet
        value = Value.objects.get(run=step.run, ruleset__label="color")
        self.assertEquals("1c75fd71-027b-40e8-a819-151a0f8140e6", value.rule_uuid)
        self.assertEquals("Orange", value.category)
        self.assertEquals("orange", value.string_value)
        self.assertEquals(None, value.decimal_value)
        self.assertEquals(None, value.datetime_value)

        # check what our message context looks like now
        context = self.flow.build_expressions_context(self.contact, incoming)
        self.assertTrue(context['flow'])
        self.assertEqual("color: orange", context['flow']['__default__'])
        self.assertEqual("orange", six.text_type(context['flow']['color']['__default__']))
        self.assertEqual("orange", six.text_type(context['flow']['color']['value']))
        self.assertEqual("Orange", context['flow']['color']['category'])
        self.assertEqual("orange", context['flow']['color']['text'])

        # value time should be in org format and timezone
        val_time = datetime_to_str(step.left_on, '%d-%m-%Y %H:%M', tz=self.org.timezone)
        self.assertEqual(val_time, context['flow']['color']['time'])

        self.assertEquals(self.channel.get_address_display(e164=True), context['channel']['tel_e164'])
        self.assertEquals(self.channel.get_address_display(), context['channel']['tel'])
        self.assertEquals(self.channel.get_name(), context['channel']['name'])
        self.assertEquals(self.channel.get_address_display(), context['channel']['__default__'])

        # change our step instead be decimal
        step.rule_value = '10'
        step.rule_decimal_value = Decimal('10')
        step.save()

        # check our message context again
        context = self.flow.build_expressions_context(self.contact, incoming)
        self.assertEquals('10', context['flow']['color']['value'])
        self.assertEquals('Orange', context['flow']['color']['category'])

        # this is drawn from the message which didn't change
        self.assertEquals('orange', context['flow']['color']['text'])

        # revert above change
        step.rule_value = 'orange'
        step.rule_decimal_value = None
        step.save()

        # finally we should have our final step which was our outgoing reply
        step = FlowStep.objects.filter(run__contact=self.contact).order_by('pk')[2]

        self.assertEquals(FlowStep.TYPE_ACTION_SET, step.step_type)
        self.assertEquals(self.contact, step.run.contact)
        self.assertEquals(self.contact, step.contact)
        self.assertEquals(self.flow, step.run.flow)
        self.assertTrue(step.arrived_on)

        # we have left the flow
        self.assertTrue(step.left_on)
        self.assertTrue(step.run.is_completed)
        self.assertFalse(step.next_uuid)

        # check our completion percentages
        self.assertEqual(self.flow.get_run_stats(),
                         {'total': 2, 'active': 1, 'completed': 1, 'expired': 0, 'interrupted': 0, 'completion': 50})

        # at this point there are no more steps to take in the flow, so we shouldn't match anymore
        extra = self.create_msg(direction=INCOMING, contact=self.contact, text="Hello ther")
        self.assertFalse(Flow.find_and_handle(extra)[0])

        # try getting our results
        results = self.flow.get_results()

        # should have two results
        self.assertEquals(2, len(results))

        # check the value
        found = False
        for result in results:
            if result['contact'] == self.contact:
                found = True
                self.assertEquals(1, len(result['values']))

        self.assertTrue(found)

        color = result['values'][0]
        self.assertEquals('color', color['label'])
        self.assertEquals('Orange', color['category']['base'])
        self.assertEquals('orange', color['value'])
        self.assertEquals("bd531ace-911e-4722-8e53-6730d6122fe1", color['node'])
        self.assertEquals(incoming.text, color['text'])

    def test_anon_export_results(self):
        self.org.is_anon = True
        self.org.save()

        (run1,) = self.flow.start([], [self.contact])

        msg = self.create_msg(direction=INCOMING, contact=self.contact, text="orange")
        Flow.find_and_handle(msg)

        workbook = self.export_flow_results(self.flow)
        sheet_runs, sheet_contacts, sheet_msgs = workbook.worksheets
        self.assertExcelRow(sheet_runs, 0, ["Contact UUID", "ID", "Name", "Groups", "First Seen", "Last Seen",
                                            "color (Category) - Color Flow",
                                            "color (Value) - Color Flow",
                                            "color (Text) - Color Flow"])

        steps = FlowStep.objects.filter(run=run1, step_type='R')
        c1_run1_first = steps.order_by('pk').first().arrived_on
        c1_run1_last = steps.order_by('-pk').first().arrived_on

        self.assertExcelRow(sheet_runs, 1, [self.contact.uuid, six.text_type(self.contact.id), "Eric", "", c1_run1_first,
                                            c1_run1_last, "Orange", "orange", "orange"], self.org.timezone)

        self.assertExcelRow(sheet_contacts, 0, ["Contact UUID", "ID", "Name", "Groups", "First Seen", "Last Seen",
                                                "color (Category) - Color Flow",
                                                "color (Value) - Color Flow",
                                                "color (Text) - Color Flow"])

        self.assertExcelRow(sheet_contacts, 1, [self.contact.uuid, six.text_type(self.contact.id), "Eric", "",
                                                c1_run1_first, c1_run1_last, "Orange", "orange", "orange"], self.org.timezone)

        self.assertExcelRow(sheet_msgs, 0, ["Contact UUID", "ID", "Name", "Date", "Direction", "Message", "Channel"])
        self.assertExcelRow(sheet_msgs, 2, [self.contact.uuid, six.text_type(self.contact.id), "Eric",
                                            msg.created_on, "IN",
                                            "orange", "Test Channel"], self.org.timezone)

    def test_export_results_broadcast_only_flow(self):
        self.login(self.admin)

        flow = self.get_flow('two_in_row')
        contact1_run1, contact2_run1, contact3_run1 = flow.start([], [self.contact, self.contact2, self.contact3])
        contact1_run2, contact2_run2 = flow.start([], [self.contact, self.contact2], restart_participants=True)

        with self.assertNumQueries(50):
            workbook = self.export_flow_results(flow)

        tz = self.org.timezone

        sheet_runs, sheet_contacts, sheet_msgs = workbook.worksheets

        # check runs sheet...
        self.assertEqual(len(list(sheet_runs.rows)), 6)  # header + 5 runs
        self.assertEqual(len(list(sheet_runs.columns)), 6)

        self.assertExcelRow(sheet_runs, 0, ["Contact UUID", "URN", "Name", "Groups", "First Seen", "Last Seen"])

        contact1_run1_rs = FlowStep.objects.filter(run=contact1_run1)
        c1_run1_first = contact1_run1_rs.order_by('pk').first().arrived_on
        c1_run1_last = contact1_run1_rs.order_by('-pk').first().arrived_on

        contact1_run2_rs = FlowStep.objects.filter(run=contact1_run2)
        c1_run2_first = contact1_run2_rs.order_by('pk').first().arrived_on
        c1_run2_last = contact1_run2_rs.order_by('-pk').first().arrived_on

        self.assertExcelRow(sheet_runs, 1, [contact1_run1.contact.uuid, "+250788382382", "Eric", "bootstrap 3", c1_run1_first,
                                            c1_run1_last], tz)

        self.assertExcelRow(sheet_runs, 2, [contact1_run2.contact.uuid, "+250788382382", "Eric", "bootstrap 3", c1_run2_first,
                                            c1_run2_last], tz)

        contact2_run1_steps = FlowStep.objects.filter(run=contact2_run1)
        c2_run1_first = contact2_run1_steps.order_by('pk').first().arrived_on
        c2_run1_last = contact2_run1_steps.order_by('-pk').first().arrived_on

        contact2_run2_steps = FlowStep.objects.filter(run=contact2_run2)
        c2_run2_first = contact2_run2_steps.order_by('pk').first().arrived_on
        c2_run2_last = contact2_run2_steps.order_by('-pk').first().arrived_on

        contact3_run1_steps = FlowStep.objects.filter(run=contact3_run1)
        c3_run1_first = contact3_run1_steps.order_by('pk').first().arrived_on
        c3_run1_last = contact3_run1_steps.order_by('-pk').first().arrived_on

        self.assertExcelRow(sheet_runs, 3, [contact2_run1.contact.uuid, "+250788383383", "Nic", "bootstrap 3", c2_run1_first,
                                            c2_run1_last], tz)

        self.assertExcelRow(sheet_runs, 4, [contact2_run2.contact.uuid, "+250788383383", "Nic", "bootstrap 3", c2_run2_first,
                                            c2_run2_last], tz)

        # check contacts sheet...
        self.assertEqual(len(list(sheet_contacts.rows)), 4)  # header + 3 contacts
        self.assertEqual(len(list(sheet_contacts.columns)), 6)

        self.assertExcelRow(sheet_contacts, 0, ["Contact UUID", "URN", "Name", "Groups", "First Seen", "Last Seen"])

        self.assertExcelRow(sheet_contacts, 1, [contact1_run1.contact.uuid, "+250788382382", "Eric", "bootstrap 3",
                                                c1_run1_first, c1_run2_last], tz)

        self.assertExcelRow(sheet_contacts, 2, [contact2_run1.contact.uuid, "+250788383383", "Nic", "bootstrap 3",
                                                c2_run1_first, c2_run2_last], tz)

        self.assertExcelRow(sheet_contacts, 3, [contact3_run1.contact.uuid, "+250788123456", "Norbert", "bootstrap 3",
                                                c3_run1_first, c3_run1_last], tz)

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
        with self.assertNumQueries(39):
            workbook = self.export_flow_results(flow, include_msgs=False, include_runs=False, responded_only=True)

        tz = self.org.timezone
        sheet_contacts = workbook.worksheets[0]

        self.assertEqual(len(list(sheet_contacts.rows)), 1)  # header; no resposes to a broadcast only flow
        self.assertEqual(len(list(sheet_contacts.columns)), 6)

        self.assertExcelRow(sheet_contacts, 0, ["Contact UUID", "URN", "Name", "Groups", "First Seen", "Last Seen"])

    def test_export_results(self):
        # setup flow and start both contacts

        self.create_group('Devs', [self.contact])

        # contact name with an illegal character
        self.contact3.name = "Nor\02bert"
        self.contact3.save()

        contact1_run1, contact2_run1, contact3_run1 = self.flow.start([], [self.contact, self.contact2, self.contact3])

        time.sleep(1)

        # simulate two runs each for two contacts...
        contact1_in1 = self.create_msg(direction=INCOMING, contact=self.contact, text="light beige")
        Flow.find_and_handle(contact1_in1)

        contact1_in2 = self.create_msg(direction=INCOMING, contact=self.contact, text="orange")
        Flow.find_and_handle(contact1_in2)

        contact2_in1 = self.create_msg(direction=INCOMING, contact=self.contact2, text="green")
        Flow.find_and_handle(contact2_in1)

        time.sleep(1)

        contact1_run2, contact2_run2 = self.flow.start([], [self.contact, self.contact2], restart_participants=True)

        time.sleep(1)

        contact1_in3 = self.create_msg(direction=INCOMING, contact=self.contact, text=" blue ")
        Flow.find_and_handle(contact1_in3)

        # check can't export anonymously
        exported = self.client.get(reverse('flows.flow_export_results') + "?ids=%d" % self.flow.pk)
        self.assertEquals(302, exported.status_code)

        self.login(self.admin)

        # create a dummy export task so that we won't be able to export
        blocking_export = ExportFlowResultsTask.objects.create(org=self.org, created_by=self.admin, modified_by=self.admin)
        response = self.client.post(reverse('flows.flow_export_results'), dict(flows=[self.flow.pk]), follow=True)
        self.assertContains(response, "already an export in progress")

        # ok, mark that one as finished and try again
        blocking_export.update_status(ExportFlowResultsTask.STATUS_COMPLETE)

        with self.assertNumQueries(51):
            workbook = self.export_flow_results(self.flow)

        tz = self.org.timezone

        sheet_runs, sheet_contacts, sheet_msgs = workbook.worksheets

        # check runs sheet...
        self.assertEqual(len(list(sheet_runs.rows)), 6)  # header + 5 runs
        self.assertEqual(len(list(sheet_runs.columns)), 9)

        self.assertExcelRow(sheet_runs, 0, ["Contact UUID", "URN", "Name", "Groups", "First Seen", "Last Seen",
                                            "color (Category) - Color Flow",
                                            "color (Value) - Color Flow",
                                            "color (Text) - Color Flow"])

        contact1_run1_rs = FlowStep.objects.filter(run=contact1_run1, step_type='R')
        c1_run1_first = contact1_run1_rs.order_by('pk').first().arrived_on
        c1_run1_last = contact1_run1_rs.order_by('-pk').first().arrived_on

        contact1_run2_rs = FlowStep.objects.filter(run=contact1_run2, step_type='R')
        c1_run2_first = contact1_run2_rs.order_by('pk').first().arrived_on
        c1_run2_last = contact1_run2_rs.order_by('-pk').first().arrived_on

        self.assertExcelRow(sheet_runs, 1, [contact1_run1.contact.uuid, "+250788382382", "Eric", "Devs", c1_run1_first,
                                            c1_run1_last, "Orange", "orange", "orange"], tz)

        self.assertExcelRow(sheet_runs, 2, [contact1_run2.contact.uuid, "+250788382382", "Eric", "Devs", c1_run2_first,
                                            c1_run2_last, "Blue", "blue", " blue "], tz)

        contact2_run1_rs = FlowStep.objects.filter(run=contact2_run1, step_type='R')
        c2_run1_first = contact2_run1_rs.order_by('pk').first().arrived_on
        c2_run1_last = contact2_run1_rs.order_by('-pk').first().arrived_on

        contact2_run2_rs = FlowStep.objects.filter(run=contact2_run2, step_type='R')
        c2_run2_first = contact2_run2_rs.order_by('pk').first().arrived_on
        c2_run2_last = contact2_run2_rs.order_by('-pk').first().arrived_on

        contact3_run1_rs = FlowStep.objects.filter(run=contact3_run1, step_type='R')
        c3_run1_first = contact3_run1_rs.order_by('pk').first().arrived_on
        c3_run1_last = contact3_run1_rs.order_by('-pk').first().arrived_on

        self.assertExcelRow(sheet_runs, 3, [contact2_run1.contact.uuid, "+250788383383", "Nic", "", c2_run1_first,
                                            c2_run1_last, "Other", "green", "green"], tz)

        self.assertExcelRow(sheet_runs, 4, [contact2_run2.contact.uuid, "+250788383383", "Nic", "", c2_run2_first,
                                            c2_run2_last, "", "", ""], tz)

        # check contacts sheet...
        self.assertEqual(len(list(sheet_contacts.rows)), 4)  # header + 3 contacts
        self.assertEqual(len(list(sheet_contacts.columns)), 9)

        self.assertExcelRow(sheet_contacts, 0, ["Contact UUID", "URN", "Name", "Groups", "First Seen", "Last Seen",
                                                "color (Category) - Color Flow",
                                                "color (Value) - Color Flow",
                                                "color (Text) - Color Flow"])

        self.assertExcelRow(sheet_contacts, 1, [contact1_run1.contact.uuid, "+250788382382", "Eric", "Devs",
                                                c1_run1_first, c1_run2_last, "Blue", "blue", " blue "], tz)

        self.assertExcelRow(sheet_contacts, 2, [contact2_run1.contact.uuid, "+250788383383", "Nic", "",
                                                c2_run1_first, c2_run2_last, "Other", "green", "green"], tz)

        self.assertExcelRow(sheet_contacts, 3, [contact3_run1.contact.uuid, "+250788123456", "Norbert", "",
                                                c3_run1_first, c3_run1_last, "", "", ""], tz)

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
                                            "0788 382 382 SMS: orange Flow: color: light beige\ncolor: orange",
                                            "Test Channel"], tz)

        # test without msgs or runs or unresponded
        with self.assertNumQueries(49):
            workbook = self.export_flow_results(self.flow, include_msgs=False, include_runs=False, responded_only=True)

        tz = self.org.timezone
        sheet_contacts = workbook.worksheets[0]

        self.assertEqual(len(list(sheet_contacts.rows)), 3)  # header + 2 contacts
        self.assertEqual(len(list(sheet_contacts.columns)), 9)

        self.assertExcelRow(sheet_contacts, 0, ["Contact UUID", "URN", "Name", "Groups", "First Seen", "Last Seen",
                                                "color (Category) - Color Flow",
                                                "color (Value) - Color Flow",
                                                "color (Text) - Color Flow"])

        self.assertExcelRow(sheet_contacts, 1, [contact1_run1.contact.uuid, "+250788382382", "Eric", "Devs",
                                                c1_run1_first, c1_run2_last, "Blue", "blue", " blue "], tz)

        self.assertExcelRow(sheet_contacts, 2, [contact2_run1.contact.uuid, "+250788383383", "Nic", "", c2_run1_first,
                                                c2_run1_last, "Other", "green", "green"], tz)

        # test export with a contact field
        age = ContactField.get_or_create(self.org, self.admin, 'age', "Age")
        self.contact.set_field(self.admin, 'age', 36)

        # insert a duplicate age field, this can happen due to races
        Value.objects.create(org=self.org, contact=self.contact, contact_field=age, string_value='36', decimal_value='36')

        with self.assertNumQueries(54):
            workbook = self.export_flow_results(self.flow, include_msgs=False, include_runs=True, responded_only=True,
                                                contact_fields=[age])

        # try setting the field again
        self.contact.set_field(self.admin, 'age', 36)

        # only one present now
        self.assertEqual(Value.objects.filter(contact=self.contact, contact_field=age).count(), 1)

        tz = self.org.timezone
        sheet_runs, sheet_contacts = workbook.worksheets

        self.assertEqual(len(list(sheet_contacts.rows)), 3)  # header + 2 contacts
        self.assertEqual(len(list(sheet_contacts.columns)), 10)

        self.assertExcelRow(sheet_contacts, 0, ["Contact UUID", "URN", "Name", "Groups", "Age",
                                                "First Seen", "Last Seen",
                                                "color (Category) - Color Flow",
                                                "color (Value) - Color Flow",
                                                "color (Text) - Color Flow"])

        self.assertExcelRow(sheet_contacts, 1, [contact1_run1.contact.uuid, "+250788382382", "Eric", "Devs", "36",
                                                c1_run1_first, c1_run2_last, "Blue", "blue", " blue "], tz)

        self.assertExcelRow(sheet_contacts, 2, [contact2_run1.contact.uuid, "+250788383383", "Nic", "", "",
                                                c2_run1_first, c2_run1_last, "Other", "green", "green"], tz)

        # check runs sheet...
        self.assertEqual(len(list(sheet_runs.rows)), 4)  # header + 3 runs
        self.assertEqual(len(list(sheet_runs.columns)), 10)

        self.assertExcelRow(sheet_runs, 0, ["Contact UUID", "URN", "Name", "Groups", "Age",
                                            "First Seen", "Last Seen",
                                            "color (Category) - Color Flow",
                                            "color (Value) - Color Flow",
                                            "color (Text) - Color Flow"])

        self.assertExcelRow(sheet_runs, 1, [contact1_run1.contact.uuid, "+250788382382", "Eric", "Devs", "36",
                                            c1_run1_first, c1_run1_last, "Orange", "orange", "orange"], tz)

    def test_export_results_list_messages_once(self):
        contact1_run1 = self.flow.start([], [self.contact])[0]

        time.sleep(1)

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

        time.sleep(1)

        contact1_in1 = self.create_msg(direction=INCOMING, contact=self.contact, text="ngert\x07in.")
        Flow.find_and_handle(contact1_in1)

        contact1_run1_rs = FlowStep.objects.filter(run=contact1_run1, step_type='R')
        c1_run1_first = contact1_run1_rs.order_by('pk').first().arrived_on
        c1_run1_last = contact1_run1_rs.order_by('-pk').first().arrived_on

        workbook = self.export_flow_results(self.flow)

        tz = self.org.timezone

        sheet_runs, sheet_contacts, sheet_msgs = workbook.worksheets

        # check runs sheet...
        self.assertEqual(len(list(sheet_runs.rows)), 2)  # header + 1 runs
        self.assertEqual(len(list(sheet_runs.columns)), 9)

        self.assertExcelRow(sheet_runs, 0, ["Contact UUID", "URN", "Name", "Groups", "First Seen", "Last Seen",
                                            "color (Category) - Color Flow",
                                            "color (Value) - Color Flow",
                                            "color (Text) - Color Flow"])

        self.assertExcelRow(sheet_runs, 1, [contact1_run1.contact.uuid, "+250788382382", "Eric", "", c1_run1_first,
                                            c1_run1_last, "Other", "ngertin.", "ngertin."], tz)

    def test_export_results_with_surveyor_msgs(self):
        self.flow.flow_type = Flow.SURVEY
        self.flow.save()
        run = self.flow.start([], [self.contact])[0]

        # run.submitted_by = self.admin
        run.save()

        # no urn or channel
        in1 = Msg.create_incoming(None, None, "blue", org=self.org, contact=self.contact)

        workbook = self.export_flow_results(self.flow)
        tz = self.org.timezone

        sheet_runs, sheet_contacts, sheet_msgs = workbook.worksheets

        run1_rs = FlowStep.objects.filter(run=run, step_type='R')
        run1_first = run1_rs.order_by('pk').first().arrived_on
        run1_last = run1_rs.order_by('-pk').first().arrived_on

        # no submitter for our run
        self.assertExcelRow(sheet_runs, 1, ["", run.contact.uuid, "+250788382382", "Eric", "", run1_first, run1_last,
                                            "Blue", "blue", "blue"], tz)

        out1 = Msg.objects.get(steps__run=run, text="What is your favorite color?")

        self.assertExcelRow(sheet_msgs, 1, [run.contact.uuid, "+250788382382", "Eric", out1.created_on, "OUT",
                                            "What is your favorite color?", "Test Channel"], tz)

        # no channel or phone
        self.assertExcelRow(sheet_msgs, 2, [run.contact.uuid, "", "Eric", in1.created_on, "IN", "blue", ""], tz)

        # now try setting a submitted by on our run
        run.submitted_by = self.admin
        run.save()

        workbook = self.export_flow_results(self.flow)
        tz = self.org.timezone

        sheet_runs, sheet_contacts, sheet_msgs = workbook.worksheets

        # now the Administrator should show up
        self.assertExcelRow(sheet_runs, 1, ["Administrator", run.contact.uuid, "+250788382382", "Eric", "", run1_first, run1_last,
                                            "Blue", "blue", "blue"], tz)

    def test_export_results_with_no_responses(self):
        self.assertEqual(self.flow.get_run_stats()['total'], 0)

        workbook = self.export_flow_results(self.flow)

        self.assertEqual(len(workbook.worksheets), 2)

        # every sheet has only the head row
        for entries in workbook.worksheets:
            self.assertEqual(len(list(entries.rows)), 1)
            self.assertEqual(len(list(entries.columns)), 9)

    def test_copy(self):
        # pick a really long name so we have to concatenate
        self.flow.name = "Color Flow is a long name to use for something like this"
        self.flow.expires_after_minutes = 60
        self.flow.save()

        # make sure our metadata got saved
        metadata = json.loads(self.flow.metadata)
        self.assertEquals("Ryan Lewis", metadata['author'])

        # now create a copy
        copy = Flow.copy(self.flow, self.admin)

        metadata = json.loads(copy.metadata)
        self.assertEquals("Ryan Lewis", metadata['author'])

        # expiration should be copied too
        self.assertEquals(60, copy.expires_after_minutes)

        # should have a different id
        self.assertNotEqual(self.flow.pk, copy.pk)

        # Name should start with "Copy of"
        self.assertEquals("Copy of Color Flow is a long name to use for something like thi", copy.name)

        # metadata should come out in the json
        copy_json = copy.as_json()
        self.assertEquals(dict(author="Ryan Lewis",
                               name='Copy of Color Flow is a long name to use for something like thi',
                               revision=1,
                               expires=60,
                               uuid=copy.uuid,
                               saved_on=datetime_to_str(copy.saved_on)),
                          copy_json['metadata'])

        # should have the same number of actionsets and rulesets
        self.assertEquals(copy.action_sets.all().count(), self.flow.action_sets.all().count())
        self.assertEquals(copy.rule_sets.all().count(), self.flow.rule_sets.all().count())

    @override_settings(SEND_WEBHOOKS=True)
    def test_optimization_reply_action(self):

        self.flow.update({"base_language": "base",
                          "entry": "02a2f789-1545-466b-978a-4cebcc9ab89a",
                          "rule_sets": [],
                          "action_sets": [{"y": 0, "x": 100,
                                           "destination": None, "uuid": "02a2f789-1545-466b-978a-4cebcc9ab89a",
                                           "actions": [{"type": "api", "webhook": "https://rapidpro.io/demo/coupon/",
                                                        "webhook_header": [{
                                                            "name": "Authorization", "value": "Token 12345"
                                                        }]},
                                                       {"msg": {"base": "text to get @extra.coupon"}, "type": "reply"}]}],
                          "metadata": {"notes": []}})

        with patch('requests.post') as mock:
            mock.return_value = MockResponse(200, '{ "coupon": "NEXUS4" }')

            self.flow.start([], [self.contact])

            self.assertTrue(self.flow.get_steps())
            self.assertTrue(Msg.objects.all())
            msg = Msg.objects.all()[0]
            self.assertFalse("@extra.coupon" in msg.text)
            self.assertEquals(msg.text, "text to get NEXUS4")
            self.assertEquals(PENDING, msg.status)

    def test_parsing(self):
        # our flow should have the appropriate RuleSet and ActionSet objects
        self.assertEquals(4, ActionSet.objects.all().count())

        entry = ActionSet.objects.get(uuid="d51ec25f-04e6-4349-a448-e7c4d93d4597")
        actions = entry.get_actions()
        self.assertEqual(len(actions), 1)
        self.assertIsInstance(actions[0], ReplyAction)
        self.assertEqual(actions[0].msg, dict(base="What is your favorite color?", fre="Quelle est votre couleur préférée?"))
        self.assertEqual(entry.uuid, self.flow.entry_uuid)

        orange = ActionSet.objects.get(uuid="7d40faea-723b-473d-8999-59fb7d3c3ca2")
        actions = orange.get_actions()
        self.assertEquals(1, len(actions))
        self.assertEquals(ReplyAction(dict(base='I love orange too! You said: @step.value which is category: @flow.color.category You are: @step.contact.tel SMS: @step Flow: @flow')).as_json(), actions[0].as_json())

        self.assertEquals(1, RuleSet.objects.all().count())
        ruleset = RuleSet.objects.get(uuid="bd531ace-911e-4722-8e53-6730d6122fe1")
        self.assertEquals(entry.destination, ruleset.uuid)
        rules = ruleset.get_rules()
        self.assertEquals(4, len(rules))

        # check ordering
        self.assertEquals("7d40faea-723b-473d-8999-59fb7d3c3ca2", rules[0].destination)
        self.assertEquals("1c75fd71-027b-40e8-a819-151a0f8140e6", rules[0].uuid)
        self.assertEquals("c12f37e2-8e6c-4c81-ba6d-941bb3caf93f", rules[1].destination)
        self.assertEquals("40cc7c36-b7c8-4f05-ae82-25275607e5aa", rules[1].uuid)
        self.assertEquals("1cb6d8da-3749-45b2-9382-3f756e3ca71f", rules[2].destination)
        self.assertEquals("93998b50-6580-4574-8f60-74654df7d243", rules[2].uuid)

        # check routing
        self.assertEquals(ContainsTest(test=dict(base="orange")).as_json(), rules[0].test.as_json())
        self.assertEquals(ContainsTest(test=dict(base="blue")).as_json(), rules[1].test.as_json())
        self.assertEquals(TrueTest().as_json(), rules[2].test.as_json())

        # and categories
        self.assertEquals("Orange", rules[0].category['base'])
        self.assertEquals("Blue", rules[1].category['base'])

        # back out as json
        json_dict = self.flow.as_json()

        self.assertEqual(json_dict['version'], CURRENT_EXPORT_VERSION)
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

        self.assertEquals(3, ActionSet.objects.all().count())

        entry = ActionSet.objects.get(uuid="d51ec25f-04e6-4349-a448-e7c4d93d4597")
        actions = entry.get_actions()
        self.assertEqual(len(actions), 1)
        self.assertIsInstance(actions[0], ReplyAction)
        self.assertEqual(actions[0].msg, dict(base="What is your favorite color?", fre="Quelle est votre couleur préférée?"))
        self.assertEqual(entry.uuid, self.flow.entry_uuid)

        orange = ActionSet.objects.get(uuid="7d40faea-723b-473d-8999-59fb7d3c3ca2")
        actions = orange.get_actions()
        self.assertEquals(1, len(actions))
        self.assertEquals(ReplyAction(dict(base='I love orange too! You said: @step.value which is category: @flow.color.category You are: @step.contact.tel SMS: @step Flow: @flow')).as_json(), actions[0].as_json())

        self.assertEquals(1, RuleSet.objects.all().count())
        ruleset = RuleSet.objects.get(uuid="bd531ace-911e-4722-8e53-6730d6122fe1")
        self.assertEquals(entry.destination, ruleset.uuid)
        rules = ruleset.get_rules()
        self.assertEquals(3, len(rules))

        # check ordering
        self.assertEquals("7d40faea-723b-473d-8999-59fb7d3c3ca2", rules[0].destination)
        self.assertEquals("c12f37e2-8e6c-4c81-ba6d-941bb3caf93f", rules[1].destination)

        # check routing
        self.assertEquals(ContainsTest(test=dict(base="orange")).as_json(), rules[0].test.as_json())
        self.assertEquals(ContainsTest(test=dict(base="blue")).as_json(), rules[1].test.as_json())

        # updating with a label name that is too long should truncate it
        json_dict['rule_sets'][0]['label'] = 'W' * 75
        json_dict['rule_sets'][0]['operand'] = 'W' * 135
        self.flow.update(json_dict)

        # now check they are truncated to the max lengths
        ruleset = RuleSet.objects.get(uuid="bd531ace-911e-4722-8e53-6730d6122fe1")
        self.assertEquals(64, len(ruleset.label))
        self.assertEquals(128, len(ruleset.operand))

    def test_expanding(self):
        # add actions for adding to a group and messaging a contact, we'll test how these expand
        action_set = ActionSet.objects.get(uuid="1cb6d8da-3749-45b2-9382-3f756e3ca71f")

        actions = [AddToGroupAction([self.other_group]).as_json(),
                   SendAction("Outgoing Message", [self.other_group], [self.contact], []).as_json()]

        action_set.set_actions_dict(actions)
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
        self.assertEquals(1, len(add_group['groups']))
        self.assertEquals(1, len(send['groups']))

    def assertTest(self, expected_test, expected_value, test, extra=None):
        runs = FlowRun.objects.filter(contact=self.contact)
        if runs:
            run = runs[0]
        else:
            run = FlowRun.create(self.flow, self.contact.pk)

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
        self.assertEquals(expected_value, result[1])

        # return our run for later inspection
        return run

    def assertDateTest(self, expected_test, expected_value, test):
        run = FlowRun.objects.filter(contact=self.contact).first()
        tz = run.flow.org.timezone
        context = run.flow.build_expressions_context(run.contact, None)

        tuple = test.evaluate(run, self.sms, context, self.sms.text)
        if expected_test:
            self.assertTrue(tuple[0])
        else:
            self.assertFalse(tuple[0])
        if expected_test and expected_value:
            # convert our expected date time the right timezone
            expected_tz = expected_value.astimezone(tz)
            expected_value = expected_value.replace(hour=expected_tz.hour).replace(day=expected_tz.day).replace(month=expected_tz.month)
            self.assertTrue(abs((expected_value - str_to_datetime(tuple[1], tz=timezone.utc)).total_seconds()) < 60)

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

        # edit distance
        sms.text = "Greenn is ok though"
        self.assertTest(True, "Greenn", test)

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

        test = ContainsPhraseTest(test=dict(base="resist now"))
        test = ContainsPhraseTest.from_json(self.org, test.as_json())
        sms.text = "we must resist! NOW "
        self.assertTest(True, "resist NOW", test)

        sms.text = "we must resist but perhaps not NOW "
        self.assertTest(False, None, test)

        sms.text = "  RESIST now "
        self.assertTest(True, "RESIST now", test)

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
        self.assertEquals("1000-5000", rule.get_category_name(None))

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
        extra = run.field_dict()
        self.assertEquals("Isaac Newton", extra['0'])
        self.assertEquals("Isaac", extra['1'])
        self.assertEquals("Newton", extra['2'])
        self.assertEquals("Isaac", extra['first_name'])

        # find that arabic unicode is handled right
        sms.text = "مرحبا العالم"
        run = self.assertTest(True, "مرحبا العالم", test)
        extra = run.field_dict()
        self.assertEquals("مرحبا العالم", extra['0'])
        self.assertEquals("مرحبا", extra['1'])
        self.assertEquals("العالم", extra['2'])
        self.assertEquals("مرحبا", extra['first_name'])

        # no matching groups, should return whole string as match
        test = RegexTest(dict(base="\w+ \w+"))
        sms.text = "Isaac Newton"
        run = self.assertTest(True, "Isaac Newton", test)
        extra = run.field_dict()
        self.assertEquals("Isaac Newton", extra['0'])

        # no match, shouldn't return anything at all
        sms.text = "#$%^$#? !@#$"
        run = self.assertTest(False, None, test)
        extra = run.field_dict()
        self.assertFalse(extra)

        # no case sensitivity
        test = RegexTest(dict(base="kazoo"))
        sms.text = "This is my Kazoo"
        run = self.assertTest(True, "Kazoo", test)
        extra = run.field_dict()
        self.assertEquals("Kazoo", extra['0'])

        # change to have anchors
        test = RegexTest(dict(base="^kazoo$"))

        # no match, as at the end
        sms.text = "This is my Kazoo"
        run = self.assertTest(False, None, test)

        # this one will match
        sms.text = "Kazoo"
        run = self.assertTest(True, "Kazoo", test)
        extra = run.field_dict()
        self.assertEquals("Kazoo", extra['0'])

        # not empty
        sms.text = ""
        self.assertTest(False, None, NotEmptyTest())
        sms.text = None
        self.assertTest(False, None, NotEmptyTest())
        sms.text = " "
        self.assertTest(False, None, NotEmptyTest())
        sms.text = "it works"
        self.assertTest(True, "it works", NotEmptyTest())

        def perform_date_tests(sms, dayfirst):
            """
            Performs a set of date tests in either day-first or month-first mode
            """
            self.org.date_format = 'D' if dayfirst else 'M'
            self.org.save()

            # perform all date tests as if it were 2014-01-02 03:04:05.6 UTC - a date which when localized to DD-MM-YYYY
            # or MM-DD-YYYY is ambiguous
            with patch.object(timezone, 'now', return_value=datetime.datetime(2014, 1, 2, 3, 4, 5, 6, timezone.utc)):
                now = timezone.now()
                three_days_ago = now - timedelta(days=3)
                three_days_next = now + timedelta(days=3)
                five_days_next = now + timedelta(days=5)

                sms.text = "no date in this text"
                test = HasDateTest()
                self.assertDateTest(False, None, test)

                sms.text = "123"
                self.assertDateTest(True, now.replace(year=123), test)

                sms.text = "December 14, 1892"
                self.assertDateTest(True, now.replace(year=1892, month=12, day=14), test)

                sms.text = "sometime on %d/%d/%d" % (now.day, now.month, now.year)
                self.assertDateTest(True, now, test)

                # date before/equal/after tests using date arithmetic

                test = DateBeforeTest('@(date.today - 1)')
                self.assertDateTest(False, None, test)

                sms.text = "this is for three days ago %d/%d/%d" % (three_days_ago.day, three_days_ago.month, three_days_ago.year)
                self.assertDateTest(True, three_days_ago, test)

                sms.text = "in the next three days %d/%d/%d" % (three_days_next.day, three_days_next.month, three_days_next.year)
                self.assertDateTest(False, None, test)

                test = DateEqualTest('@(date.today - 3)')
                self.assertDateTest(False, None, test)

                sms.text = "this is for three days ago %d/%d/%d" % (three_days_ago.day, three_days_ago.month, three_days_ago.year)
                self.assertDateTest(True, three_days_ago, test)

                test = DateAfterTest('@(date.today + 3)')
                self.assertDateTest(False, None, test)

                sms.text = "this is for three days ago %d/%d/%d" % (five_days_next.day, five_days_next.month, five_days_next.year)
                self.assertDateTest(True, five_days_next, test)

        # check date tests in both date modes
        perform_date_tests(sms, True)
        perform_date_tests(sms, False)

    def test_length(self):
        org = self.org

        js = [dict(category="Normal Length", uuid=uuid4(), destination=uuid4(), test=dict(type='true')),
              dict(category="Way too long, will get clipped at 36 characters", uuid=uuid4(), destination=uuid4(), test=dict(type='true'))]

        rules = Rule.from_json_array(org, js)

        self.assertEquals("Normal Length", rules[0].category)
        self.assertEquals(36, len(rules[1].category))

    def test_factories(self):
        org = self.org

        js = dict(type='true')
        self.assertEquals(TrueTest, Test.from_json(org, js).__class__)
        self.assertEquals(js, TrueTest().as_json())

        js = dict(type='false')
        self.assertEquals(FalseTest, Test.from_json(org, js).__class__)
        self.assertEquals(js, FalseTest().as_json())

        js = dict(type='and', tests=[dict(type='true')])
        self.assertEquals(AndTest, Test.from_json(org, js).__class__)
        self.assertEquals(js, AndTest([TrueTest()]).as_json())

        js = dict(type='or', tests=[dict(type='true')])
        self.assertEquals(OrTest, Test.from_json(org, js).__class__)
        self.assertEquals(js, OrTest([TrueTest()]).as_json())

        js = dict(type='contains', test="green")
        self.assertEquals(ContainsTest, Test.from_json(org, js).__class__)
        self.assertEquals(js, ContainsTest("green").as_json())

        js = dict(type='lt', test="5")
        self.assertEquals(LtTest, Test.from_json(org, js).__class__)
        self.assertEquals(js, LtTest("5").as_json())

        js = dict(type='gt', test="5")
        self.assertEquals(GtTest, Test.from_json(org, js).__class__)
        self.assertEquals(js, GtTest("5").as_json())

        js = dict(type='gte', test="5")
        self.assertEquals(GteTest, Test.from_json(org, js).__class__)
        self.assertEquals(js, GteTest("5").as_json())

        js = dict(type='eq', test="5")
        self.assertEquals(EqTest, Test.from_json(org, js).__class__)
        self.assertEquals(js, EqTest("5").as_json())

        js = dict(type='between', min="5", max="10")
        self.assertEquals(BetweenTest, Test.from_json(org, js).__class__)
        self.assertEquals(js, BetweenTest("5", "10").as_json())

        self.assertEquals(ReplyAction, Action.from_json(org, dict(type='reply', msg=dict(base="hello world"))).__class__)
        self.assertEquals(SendAction, Action.from_json(org, dict(type='send', msg=dict(base="hello world"), contacts=[], groups=[], variables=[])).__class__)

    def test_decimal_values(self):
        rules = RuleSet.objects.get(uuid="bd531ace-911e-4722-8e53-6730d6122fe1")

        # update our rule to include decimal parsing
        rules.set_rules_dict([
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
        ])

        rules.save()

        # start the flow
        self.flow.start([], [self.contact])
        sms = self.create_msg(direction=INCOMING, contact=self.contact, text="My answer is 15")
        self.assertTrue(Flow.find_and_handle(sms)[0])

        step = FlowStep.objects.get(step_uuid="bd531ace-911e-4722-8e53-6730d6122fe1")
        self.assertEquals("> 10", step.rule_category)
        self.assertEquals("40cc7c36-b7c8-4f05-ae82-25275607e5aa", step.rule_uuid)
        self.assertEquals("15", step.rule_value)
        self.assertEquals(Decimal("15"), step.rule_decimal_value)

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
            run = FlowRun.create(self.flow, self.contact.id)

        self.org.country = self.country
        run.flow.org = self.org
        context = run.flow.build_expressions_context(run.contact, None)

        # wrong admin level should return None if provided
        lga_tuple = HasDistrictTest('Kano').evaluate(run, sms, context, 'apapa')
        self.assertEquals(lga_tuple[1], None)

        lga_tuple = HasDistrictTest('Lagos').evaluate(run, sms, context, 'apapa')
        self.assertEquals(lga_tuple[1], apapa)

        # get lga with out higher admin level
        lga_tuple = HasDistrictTest().evaluate(run, sms, context, 'apapa')
        self.assertEquals(lga_tuple[1], apapa)

        # get ward with out higher admin levels
        ward_tuple = HasWardTest().evaluate(run, sms, context, 'bichi')
        self.assertEquals(ward_tuple[1], bichiward)

        # get with hierarchy proved
        ward_tuple = HasWardTest('Kano', 'Bichi').evaluate(run, sms, context, 'bichi')
        self.assertEquals(ward_tuple[1], bichiward)

        # wrong admin level should return None if provided
        ward_tuple = HasWardTest('Kano', 'Ajingi').evaluate(run, sms, context, 'bichi')
        js = dict(state='Kano', district='Ajingi', type='ward')
        self.assertEquals(HasWardTest('Kano', 'Ajingi').as_json(), js)
        self.assertEquals(ward_tuple[1], None)

        # get with hierarchy by aliases
        BoundaryAlias.objects.create(name='Pillars', boundary=kano, org=self.org,
                                     created_by=self.admin, modified_by=self.admin)
        ward_tuple = HasWardTest('Pillars', 'Bichi').evaluate(run, sms, context, 'bichi')
        self.assertEquals(ward_tuple[1], bichiward)

        # misconfigured flows should ignore the state and district if wards are unique by name
        ward_tuple = HasWardTest('Bichi', 'Kano').evaluate(run, sms, context, 'bichi')
        self.assertEquals(ward_tuple[1], bichiward)

        # misconfigured flows should not match if wards not unique
        AdminBoundary.objects.create(osm_id='3710379', name='Bichi', level=3, parent=apapa)
        ward_tuple = HasWardTest('Bichi', 'Kano').evaluate(run, sms, context, 'bichi')
        self.assertEquals(ward_tuple[1], None)

        self.assertEquals(HasWardTest, Test.from_json(self.org, js).__class__)

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

    def test_flow_create_with_ussd_trigger(self):
        self.login(self.admin)

        post_data = dict()
        post_data['name'] = "USSD Flow 1"
        post_data['ussd_trigger'] = "notallowed"
        post_data['flow_type'] = Flow.USSD
        response = self.client.post(reverse('flows.flow_create'), post_data, follow=True)

        self.assertEqual(response.context_data['form'].errors,
                         {u'ussd_trigger': [u'USSD code must contain only *,# and numbers']})

        post_data['ussd_trigger'] = "*111#"
        self.client.post(reverse('flows.flow_create'), post_data, follow=True)

        self.assertEqual(Trigger.objects.count(), 1)

        # try to add duplicate
        response = self.client.post(reverse('flows.flow_create'), post_data, follow=True)
        self.assertEqual(response.context_data['form'].errors,
                         {u'ussd_trigger': [u"An active trigger already uses this keyword on this channel."]})

    def test_flow_update_with_ussd_trigger(self):
        self.login(self.admin)

        post_data = dict()
        post_data['name'] = "USSD Flow 2"
        post_data['ussd_trigger'] = "*112#"
        post_data['flow_type'] = Flow.USSD
        post_data['ussd_push_enabled'] = False
        self.client.post(reverse('flows.flow_create'), post_data, follow=True)

        flow = Flow.objects.get(name="USSD Flow 2")
        trigger = Trigger.objects.get(keyword="*112#")

        # update ussd code
        post_data['ussd_trigger'] = "*113#"
        post_data['ussd_push_enabled'] = True
        post_data['expires_after_minutes'] = 60 * 12
        self.client.post(reverse('flows.flow_update', args=[flow.pk]), post_data, follow=True)

        flow.refresh_from_db()
        trigger.refresh_from_db()

        self.assertTrue(flow.ussd_push_enabled)
        self.assertEqual(trigger.keyword, "*113#")
        self.assertFalse(Trigger.objects.filter(keyword="*112#").exists())
        self.assertTrue(Trigger.objects.filter(keyword="*113#").exists())

        # delete trigger
        post_data['ussd_trigger'] = ""
        post_data['ussd_push_enabled'] = True
        post_data['expires_after_minutes'] = 60 * 12
        self.client.post(reverse('flows.flow_update', args=[flow.pk]), post_data, follow=True)

        flow.refresh_from_db()

        self.assertTrue(flow.ussd_push_enabled)
        self.assertFalse(Trigger.objects.filter(keyword="*112#").exists())
        self.assertFalse(Trigger.objects.filter(keyword="*113#").exists())

    def test_flow_create_on_update_ussd_trigger(self):
        self.login(self.admin)

        # create flow without trigger
        post_data = dict()
        post_data['name'] = "USSD Flow 2"
        post_data['flow_type'] = Flow.USSD
        post_data['ussd_push_enabled'] = False
        self.client.post(reverse('flows.flow_create'), post_data, follow=True)

        flow = Flow.objects.get(name="USSD Flow 2")

        post_data['ussd_trigger'] = "*112#"
        post_data['ussd_push_enabled'] = True
        post_data['expires_after_minutes'] = 60 * 12
        self.client.post(reverse('flows.flow_update', args=[flow.pk]), post_data, follow=True)

        flow.refresh_from_db()
        trigger = Trigger.objects.get(keyword="*112#")

        self.assertTrue(flow.ussd_push_enabled)
        self.assertEqual(trigger.keyword, "*112#")
        self.assertEqual(trigger.flow, flow)

    def test_flow_keyword_update(self):
        self.login(self.admin)
        flow = Flow.create(self.org, self.admin, "Flow")
        flow.flow_type = Flow.SURVEY
        flow.save()

        # keywords aren't an option for survey flows
        response = self.client.get(reverse('flows.flow_update', args=[flow.pk]))
        self.assertTrue('keyword_triggers' not in response.context['form'].fields)
        self.assertTrue('ignore_triggers' not in response.context['form'].fields)

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
        self.assertEquals(flow_with_keywords.triggers.count(), 3)
        self.assertEquals(flow_with_keywords.triggers.filter(is_archived=False).count(), 3)
        self.assertEquals(flow_with_keywords.triggers.filter(is_archived=False).exclude(groups=None).count(), 0)

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

        self.assertEquals(flow_with_keywords.triggers.filter(is_archived=False).count(), 8)

        # update flow triggers
        post_data = dict()
        post_data['name'] = "Flow With Keyword Triggers"
        post_data['keyword_triggers'] = "it,join"
        post_data['expires_after_minutes'] = 60 * 12
        response = self.client.post(reverse('flows.flow_update', args=[flow.pk]), post_data, follow=True)

        flow_with_keywords = Flow.objects.get(name=post_data['name'])
        self.assertEquals(200, response.status_code)
        self.assertEquals(response.request['PATH_INFO'], reverse('flows.flow_list'))
        self.assertTrue(flow_with_keywords in response.context['object_list'].all())
        self.assertEquals(flow_with_keywords.triggers.count(), 9)
        self.assertEquals(flow_with_keywords.triggers.filter(is_archived=True).count(), 2)
        self.assertEquals(flow_with_keywords.triggers.filter(is_archived=True,
                                                             trigger_type=Trigger.TYPE_KEYWORD).count(), 2)
        self.assertEquals(flow_with_keywords.triggers.filter(is_archived=False).count(), 7)
        self.assertEquals(flow_with_keywords.triggers.filter(is_archived=True,
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
        self.assertEquals(1, len(response.context['object_list']))

        # inactive list shouldn't have any flows
        response = self.client.get(reverse('flows.flow_archived'))
        self.assertEquals(0, len(response.context['object_list']))

        # also shouldn't be able to view other flow
        response = self.client.get(reverse('flows.flow_editor', args=[other_flow.uuid]))
        self.assertEquals(302, response.status_code)

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
        self.assertEquals(200, response.status_code)
        self.assertEquals(response.request['PATH_INFO'], reverse('flows.flow_list'))
        self.assertTrue(flow3 in response.context['object_list'].all())
        self.assertEquals(flow3.triggers.count(), 5)
        self.assertEquals(flow3.triggers.filter(is_archived=True).count(), 2)
        self.assertEquals(flow3.triggers.filter(is_archived=False).count(), 3)
        self.assertEquals(flow3.triggers.filter(is_archived=False).exclude(groups=None).count(), 0)

        # update flow with unformatted keyword
        post_data['keyword_triggers'] = "it,changes,every thing"
        response = self.client.post(reverse('flows.flow_update', args=[flow3.pk]), post_data)
        self.assertTrue(response.context['form'].errors)

        # update flow with unformated keyword
        post_data['keyword_triggers'] = "it,changes,everything,unique"
        response = self.client.post(reverse('flows.flow_update', args=[flow3.pk]), post_data)
        self.assertTrue(response.context['form'].errors)
        response = self.client.get(reverse('flows.flow_update', args=[flow3.pk]))
        self.assertEquals(response.context['form'].fields['keyword_triggers'].initial, "it,everything,changes")
        self.assertEquals(flow3.triggers.filter(is_archived=False).count(), 3)
        self.assertEquals(flow3.triggers.filter(is_archived=False).exclude(groups=None).count(), 0)
        trigger = Trigger.objects.get(keyword="everything", flow=flow3)
        group = self.create_group("first", [self.contact])
        trigger.groups.add(group)
        self.assertEquals(flow3.triggers.filter(is_archived=False).count(), 3)
        self.assertEquals(flow3.triggers.filter(is_archived=False).exclude(groups=None).count(), 1)
        self.assertEquals(flow3.triggers.filter(is_archived=False).exclude(groups=None)[0].keyword, "everything")
        response = self.client.get(reverse('flows.flow_update', args=[flow3.pk]))
        self.assertEquals(response.context['form'].fields['keyword_triggers'].initial, "it,changes")
        self.assertNotContains(response, "contact_creation")
        self.assertEquals(flow3.triggers.filter(is_archived=False).count(), 3)
        self.assertEquals(flow3.triggers.filter(is_archived=False).exclude(groups=None).count(), 1)
        self.assertEquals(flow3.triggers.filter(is_archived=False).exclude(groups=None)[0].keyword, "everything")

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
        self.assertEqual(Flow.CONTACT_PER_LOGIN, flow3.get_metadata_json().get('contact_creation'))

        # can see results for a flow
        response = self.client.get(reverse('flows.flow_results', args=[self.flow.id]))
        self.assertEquals(200, response.status_code)

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
        self.assertEqual(ActionSet.objects.all().count(), 32)

        json_dict = response.json()['flow']

        # test setting the json to a single actionset
        json_dict['action_sets'] = [dict(uuid=str(uuid4()), x=1, y=1, destination=None,
                                         actions=[dict(type='reply', msg=dict(base='This flow is more like a broadcast'))])]
        json_dict['rule_sets'] = []
        json_dict['entry'] = json_dict['action_sets'][0]['uuid']

        response = self.client.post(reverse('flows.flow_json', args=[self.flow.id]), json.dumps(json_dict), content_type="application/json")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(ActionSet.objects.all().count(), 29)

        # check that the flow only has a single actionset
        ActionSet.objects.get(flow=self.flow)

        # can't save with an invalid uuid
        json_dict['metadata']['saved_on'] = datetime_to_str(timezone.now())
        json_dict['action_sets'][0]['destination'] = 'notthere'

        response = self.client.post(reverse('flows.flow_json', args=[self.flow.id]), json.dumps(json_dict), content_type="application/json")
        self.assertEquals(200, response.status_code)

        self.flow.refresh_from_db()
        flow_json = self.flow.as_json()
        self.assertIsNone(flow_json['action_sets'][0]['destination'])

        # flow should still be there though
        self.flow.refresh_from_db()

        # should still have the original one, nothing changed
        response = self.client.get(reverse('flows.flow_json', args=[self.flow.id]))
        self.assertEquals(200, response.status_code)
        json_dict = response.json()

        # can't save against the other org's flow
        response = self.client.post(reverse('flows.flow_json', args=[other_flow.id]), json.dumps(json_dict), content_type="application/json")
        self.assertEquals(302, response.status_code)

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
        self.assertEquals(response.status_code, 302)

        post_data = {'has_refresh': True}

        response = self.client.post(simulate_url, json.dumps(post_data), content_type="application/json")
        json_dict = response.json()

        self.assertFalse(group in test_contact.all_groups.all())
        self.assertFalse(test_contact.values.all())

        self.assertEquals(len(json_dict.keys()), 5)
        self.assertEquals(len(json_dict['messages']), 3)
        self.assertEquals('Test Contact has entered the &quot;Color Flow&quot; flow', json_dict['messages'][0]['text'])
        self.assertEquals("This flow is more like a broadcast", json_dict['messages'][1]['text'])
        self.assertEquals("Test Contact has exited this flow", json_dict['messages'][2]['text'])

        group = self.create_group("fans", [test_contact])
        contact_field_value = Value.objects.create(contact=test_contact, contact_field=contact_field, org=self.org,
                                                   string_value="hey")

        post_data['new_message'] = "Ok, Thanks"
        post_data['has_refresh'] = False

        response = self.client.post(simulate_url, json.dumps(post_data), content_type="application/json")
        self.assertEquals(200, response.status_code)
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
        self.assertEquals(200, response.status_code)
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
        self.assertEquals(count, Broadcast.objects.all().count())

        FlowStart.objects.all().delete()

        # include people active in flows
        post_data['include_active'] = 'on'
        count = Broadcast.objects.all().count()
        self.client.post(reverse('flows.flow_broadcast', args=[self.flow.id]), post_data, follow=True)
        self.assertEquals(count + 1, Broadcast.objects.all().count())

        # we should have a flow start
        start = FlowStart.objects.get(flow=self.flow)

        # should be in a completed state
        self.assertEquals(FlowStart.STATUS_COMPLETE, start.status)
        self.assertEquals(1, start.contact_count)

        # do so again but don't restart the participants
        del post_data['restart_participants']

        self.client.post(reverse('flows.flow_broadcast', args=[self.flow.id]), post_data, follow=True)

        # should have a new flow start
        new_start = FlowStart.objects.filter(flow=self.flow).order_by('-created_on').first()
        self.assertNotEquals(start, new_start)
        self.assertEquals(FlowStart.STATUS_COMPLETE, new_start.status)
        self.assertEquals(0, new_start.contact_count)

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

        self.assertEquals(200, response.status_code)
        self.assertEquals(response.request['PATH_INFO'], reverse('flows.flow_editor', args=[msg_flow.uuid]))
        self.assertEquals(msg_flow.flow_type, 'F')

        post_data = dict(name="Call flow", expires_after_minutes=5, flow_type='V')
        response = self.client.post(reverse('flows.flow_create'), post_data, follow=True)
        call_flow = Flow.objects.get(name=post_data['name'])

        self.assertEquals(200, response.status_code)
        self.assertEquals(response.request['PATH_INFO'], reverse('flows.flow_editor', args=[call_flow.uuid]))
        self.assertEquals(call_flow.flow_type, 'V')

        # test creating a  flow with base language
        # create the language for our org
        language = Language.create(self.org, self.flow.created_by, "English", 'eng')
        self.org.primary_language = language
        self.org.save()

        post_data = dict(name="Language Flow", expires_after_minutes=5, base_language=language.iso_code, flow_type='F')
        response = self.client.post(reverse('flows.flow_create'), post_data, follow=True)
        language_flow = Flow.objects.get(name=post_data['name'])

        self.assertEquals(200, response.status_code)
        self.assertEquals(response.request['PATH_INFO'], reverse('flows.flow_editor', args=[language_flow.uuid]))
        self.assertEquals(language_flow.base_language, language.iso_code)

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
        self.assertEquals(1, len(response.context['object_list']))
        # no create links
        self.assertFalse(flow_create_url in response.content)
        self.assertFalse(flowlabel_create_url in response.content)
        # verify the action buttons we have
        self.assertFalse('object-btn-unlabel' in response.content)
        self.assertFalse('object-btn-restore' in response.content)
        self.assertFalse('object-btn-archive' in response.content)
        self.assertFalse('object-btn-label' in response.content)
        self.assertTrue('object-btn-export' in response.content)

        # can not label
        post_data = dict()
        post_data['action'] = 'label'
        post_data['objects'] = self.flow.pk
        post_data['label'] = flow_label.pk
        post_data['add'] = True

        response = self.client.post(flow_list_url, post_data, follow=True)
        self.assertEquals(1, response.context['object_list'].count())
        self.assertFalse(response.context['object_list'][0].labels.all())

        # can not archive
        post_data = dict()
        post_data['action'] = 'archive'
        post_data['objects'] = self.flow.pk
        response = self.client.post(flow_list_url, post_data, follow=True)
        self.assertEquals(1, response.context['object_list'].count())
        self.assertEquals(response.context['object_list'][0].pk, self.flow.pk)
        self.assertFalse(response.context['object_list'][0].is_archived)

        # inactive list shouldn't have any flows
        response = self.client.get(flow_archived_url)
        self.assertEquals(0, len(response.context['object_list']))

        response = self.client.get(reverse('flows.flow_editor', args=[self.flow.uuid]))
        self.assertEquals(200, response.status_code)
        self.assertFalse(response.context['mutable'])

        # we can fetch the json for the flow
        response = self.client.get(reverse('flows.flow_json', args=[self.flow.pk]))
        self.assertEquals(200, response.status_code)

        # but posting to it should redirect to a get
        response = self.client.post(reverse('flows.flow_json', args=[self.flow.pk]), post_data=response.content)
        self.assertEquals(302, response.status_code)

        self.flow.is_archived = True
        self.flow.save()

        response = self.client.get(flow_list_url)
        self.assertEquals(0, len(response.context['object_list']))

        # can not restore
        post_data = dict()
        post_data['action'] = 'archive'
        post_data['objects'] = self.flow.pk
        response = self.client.post(flow_archived_url, post_data, follow=True)
        self.assertEquals(1, response.context['object_list'].count())
        self.assertEquals(response.context['object_list'][0].pk, self.flow.pk)
        self.assertTrue(response.context['object_list'][0].is_archived)

        response = self.client.get(flow_archived_url)
        self.assertEquals(1, len(response.context['object_list']))

        # cannot create a flow
        response = self.client.get(flow_create_url)
        self.assertEquals(302, response.status_code)

        # cannot create a flowlabel
        response = self.client.get(flowlabel_create_url)
        self.assertEquals(302, response.status_code)

        # also shouldn't be able to view other flow
        response = self.client.get(reverse('flows.flow_editor', args=[flow2.uuid]))
        self.assertEquals(302, response.status_code)

    def test_flow_update_error(self):

        flow = self.get_flow('favorites')
        json_dict = flow.as_json()
        json_dict['action_sets'][0]['actions'].append(dict(type='add_label', labels=[dict(name='@badlabel')]))
        self.login(self.admin)
        response = self.client.post(reverse('flows.flow_json', args=[flow.pk]),
                                    json.dumps(json_dict),
                                    content_type="application/json")

        self.assertEquals(400, response.status_code)
        self.assertEquals('Invalid label name: @badlabel', response.json()['description'])

    def test_flow_start_with_start_msg(self):
        sms = self.create_msg(direction=INCOMING, contact=self.contact, text="I am coming")
        self.flow.start([], [self.contact], start_msg=sms)

        self.assertTrue(FlowRun.objects.filter(contact=self.contact))
        run = FlowRun.objects.filter(contact=self.contact).first()

        self.assertEquals(run.steps.all().count(), 2)
        actionset_step = run.steps.filter(step_type=FlowStep.TYPE_ACTION_SET).first()
        ruleset_step = run.steps.filter(step_type=FlowStep.TYPE_RULE_SET).first()

        # no messages on the ruleset step
        self.assertFalse(ruleset_step.messages.all())

        # should have 2 messages on the actionset step
        self.assertEquals(actionset_step.messages.all().count(), 2)

        # one is the start msg
        self.assertTrue(actionset_step.messages.filter(pk=sms.pk))

        # sms msg_type should be FLOW
        self.assertEquals(Msg.objects.get(pk=sms.pk).msg_type, FLOW)

    def test_multiple(self):
        self.flow.start([], [self.contact])

        # create a second flow
        self.flow2 = Flow.create(self.org, self.admin, "Color Flow 2")

        # broadcast to one user
        self.flow2 = self.flow.copy(self.flow, self.flow.created_by)
        self.flow2.start([], [self.contact])

        # each flow should have two events
        self.assertEquals(2, FlowStep.objects.filter(run__flow=self.flow).count())
        self.assertEquals(2, FlowStep.objects.filter(run__flow=self.flow2).count())

        # send in a message
        incoming = self.create_msg(direction=INCOMING, contact=self.contact, text="Orange", created_on=timezone.now())
        self.assertTrue(Flow.find_and_handle(incoming)[0])

        # only the second flow should get it
        self.assertEquals(2, FlowStep.objects.filter(run__flow=self.flow).count())
        self.assertEquals(3, FlowStep.objects.filter(run__flow=self.flow2).count())

        # start the flow again for our contact
        self.flow.start([], [self.contact], restart_participants=True)

        # should have two flow runs for this contact and flow
        runs = FlowRun.objects.filter(flow=self.flow, contact=self.contact).order_by('-created_on')
        self.assertTrue(runs[0].is_active)
        self.assertFalse(runs[1].is_active)

        self.assertEquals(2, runs[0].steps.all().count())
        self.assertEquals(2, runs[1].steps.all().count())

        # send in a message, this should be handled by our first flow, which has a more recent run active
        incoming = self.create_msg(direction=INCOMING, contact=self.contact, text="blue")
        self.assertTrue(Flow.find_and_handle(incoming)[0])

        self.assertEquals(3, runs[0].steps.all().count())

        # if we exclude existing and try starting again, nothing happens
        self.flow.start([], [self.contact], restart_participants=False)

        # no new runs
        self.assertEquals(2, self.flow.runs.all().count())

        # get the results for the flow
        results = self.flow.get_results()

        # should only have one result
        self.assertEquals(1, len(results))

        # and only one value
        self.assertEquals(1, len(results[0]['values']))

        color = results[0]['values'][0]
        self.assertEquals('color', color['label'])
        self.assertEquals('Blue', color['category']['base'])
        self.assertEquals('blue', color['value'])
        self.assertEquals(incoming.text, color['text'])

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


class ActionTest(TembaTest):

    def setUp(self):
        super(ActionTest, self).setUp()

        self.contact = self.create_contact('Eric', '+250788382382')
        self.contact2 = self.create_contact('Nic', '+250788383383')

        self.flow = self.create_flow(name="Empty Flow", base_language='base', definition=self.COLOR_FLOW_DEFINITION)

        self.other_group = self.create_group("Other", [])

    def execute_action(self, action, run, msg, **kwargs):
        context = run.flow.build_expressions_context(run.contact, msg)
        return action.execute(run, context, None, msg, **kwargs)

    def test_reply_action(self):
        msg = self.create_msg(direction=INCOMING, contact=self.contact, text="Green is my favorite")
        run = FlowRun.create(self.flow, self.contact.pk)

        with self.assertRaises(FlowException):
            ReplyAction.from_json(self.org, {'type': ReplyAction.TYPE})

        with self.assertRaises(FlowException):
            ReplyAction.from_json(self.org, {'type': ReplyAction.TYPE, ReplyAction.MESSAGE: dict()})

        with self.assertRaises(FlowException):
            ReplyAction.from_json(self.org, {'type': ReplyAction.TYPE, ReplyAction.MESSAGE: dict(base="")})

        action = ReplyAction(dict(base="We love green too!"))
        self.execute_action(action, run, msg)
        msg = Msg.objects.get(contact=self.contact, direction='O')
        self.assertEquals("We love green too!", msg.text)

        Broadcast.objects.all().delete()

        action_json = action.as_json()
        action = ReplyAction.from_json(self.org, action_json)
        self.assertEquals(dict(base="We love green too!"), action.msg)

        self.execute_action(action, run, msg)

        response = msg.responses.get()
        self.assertEquals("We love green too!", response.text)
        self.assertEquals(self.contact, response.contact)

    def test_send_all_action(self):
        contact = self.create_contact('Stephen', '+12078778899', twitter='stephen')
        msg = self.create_msg(direction=INCOMING, contact=contact, text="Green is my favorite")
        run = FlowRun.create(self.flow, self.contact.pk)

        action = ReplyAction(dict(base="We love green too!"), None, send_all=True)
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
        run = FlowRun.create(self.flow, self.contact.pk)

        # create twitter channel
        Channel.create(self.org, self.user, None, 'TT')
        delattr(self.org, '__schemes__%s' % Channel.ROLE_SEND)

        action_json = action.as_json()
        action = ReplyAction.from_json(self.org, action_json)
        self.assertEquals(dict(base="We love green too!"), action.msg)
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
        run = FlowRun.create(self.flow, self.contact.pk)

        action = ReplyAction(dict(base="We love green too!"), 'image/jpeg:path/to/media.jpg')
        self.execute_action(action, run, msg)
        reply_msg = Msg.objects.get(contact=self.contact, direction='O')
        self.assertEquals("We love green too!", reply_msg.text)
        self.assertEquals(reply_msg.attachments, ["image/jpeg:https://%s/%s" % (settings.AWS_BUCKET_DOMAIN, 'path/to/media.jpg')])

        Broadcast.objects.all().delete()
        Msg.objects.all().delete()
        msg = self.create_msg(direction=INCOMING, contact=self.contact, text="Green is my favorite")

        action_json = action.as_json()
        action = ReplyAction.from_json(self.org, action_json)
        self.assertEquals(dict(base="We love green too!"), action.msg)
        self.assertEquals('image/jpeg:path/to/media.jpg', action.media)

        self.execute_action(action, run, msg)

        response = msg.responses.get()
        self.assertEquals("We love green too!", response.text)
        self.assertEquals(response.attachments, ["image/jpeg:https://%s/%s" % (settings.AWS_BUCKET_DOMAIN, 'path/to/media.jpg')])
        self.assertEquals(self.contact, response.contact)

    def test_ussd_action(self):
        self.channel.delete()
        self.channel = Channel.create(self.org, self.user, 'RW', Channel.TYPE_JUNEBUG_USSD, None, '+250788123123',
                                      role=Channel.ROLE_USSD)

        msg = self.create_msg(direction=INCOMING, contact=self.contact, text="Green is my favorite")
        run = FlowRun.create(self.flow, self.contact.pk)

        menu_uuid = str(uuid4())

        ussd_ruleset = RuleSet.objects.create(flow=self.flow, uuid=str(uuid4()), x=0, y=0, ruleset_type=RuleSet.TYPE_WAIT_USSD_MENU)
        ussd_ruleset.set_rules_dict([Rule(str(uuid4()), dict(base="All Responses"), menu_uuid, 'R', TrueTest()).as_json()])
        ussd_ruleset.save()

        # without USSD config we only get an empty UssdAction
        action = UssdAction.from_ruleset(ussd_ruleset, run)
        execution = self.execute_action(action, run, msg)

        self.assertIsNone(action.msg)
        self.assertEquals(execution, [])

        # add menu rules
        ussd_ruleset.set_rules_dict([Rule(str(uuid4()), dict(base="All Responses"), menu_uuid, 'R', TrueTest()).as_json(),
                                    Rule(str(uuid4()), dict(base="Test1"), None, 'R', EqTest(test="1"), dict(base="Test1")).as_json(),
                                    Rule(str(uuid4()), dict(base="Test2"), None, 'R', EqTest(test="2"), dict(base="Test2")).as_json()])
        ussd_ruleset.save()

        # add ussd message
        config = {
            "ussd_message": {"base": "test"}
        }
        ussd_ruleset.config = json.dumps(config)
        action = UssdAction.from_ruleset(ussd_ruleset, run)
        execution = self.execute_action(action, run, msg)

        self.assertIsNotNone(action.msg)
        self.assertEquals(action.msg, {u'base': u'test\n1: Test1\n2: Test2\n'})
        self.assertIsInstance(execution[0], Msg)
        self.assertEquals(execution[0].text, u'test\n1: Test1\n2: Test2')

        Broadcast.objects.all().delete()

    def test_multilanguage_ussd_menu_partly_translated(self):
        self.channel.delete()
        self.channel = Channel.create(self.org, self.user, 'RW', Channel.TYPE_JUNEBUG_USSD, None, '+250788123123',
                                      role=Channel.ROLE_USSD)

        msg = self.create_msg(direction=INCOMING, contact=self.contact, text="Green is my favorite")
        run = FlowRun.create(self.flow, self.contact.pk)

        menu_uuid = str(uuid4())

        ussd_ruleset = RuleSet.objects.create(flow=self.flow, uuid=str(uuid4()), x=0, y=0, ruleset_type=RuleSet.TYPE_WAIT_USSD_MENU)
        ussd_ruleset.set_rules_dict([Rule(str(uuid4()), dict(base="All Responses"), menu_uuid, 'R', TrueTest()).as_json()])
        ussd_ruleset.save()

        english = Language.create(self.org, self.admin, "English", 'eng')
        Language.create(self.org, self.admin, "Hungarian", 'hun')
        Language.create(self.org, self.admin, "Russian", 'rus')
        self.flow.org.primary_language = english

        # add menu rules
        ussd_ruleset.set_rules_dict([Rule(str(uuid4()), dict(base="All Responses"), menu_uuid, 'R', TrueTest()).as_json(),
                                    Rule(str(uuid4()), dict(base="Test1"), None, 'R', EqTest(test="1"), dict(eng="labelENG", hun="labelHUN")).as_json(),
                                    Rule(str(uuid4()), dict(base="Test2"), None, 'R', EqTest(test="2"), dict(eng="label2ENG")).as_json()])
        ussd_ruleset.save()

        # add ussd message
        config = {
            "ussd_message": {"eng": "testENG", "hun": "testHUN"}
        }

        ussd_ruleset.config = json.dumps(config)
        action = UssdAction.from_ruleset(ussd_ruleset, run)
        execution = self.execute_action(action, run, msg)

        self.assertIsNotNone(action.msg)
        # we have three languages, although only 2 are (partly) translated
        self.assertEqual(len(action.msg.keys()), 3)
        self.assertEqual(action.msg.keys(), [u'rus', u'hun', u'eng'])

        # we don't have any translation for Russian, so it should be the same as eng
        self.assertEqual(action.msg['eng'], action.msg['rus'])

        # we have partly translated hungarian labels
        self.assertNotEqual(action.msg['eng'], action.msg['hun'])

        # the missing translation should be the same as the english label
        self.assertNotIn('labelENG', action.msg['hun'])
        self.assertIn('label2ENG', action.msg['hun'])

        self.assertEquals(action.msg['hun'], u'testHUN\n1: labelHUN\n2: label2ENG\n')

        # the msg sent out is in english
        self.assertIsInstance(execution[0], Msg)
        self.assertEquals(execution[0].text, u'testENG\n1: labelENG\n2: label2ENG')

        # now set contact's language to something we don't have in our org languages
        self.contact.language = 'fre'
        self.contact.save(update_fields=('language',))
        run = FlowRun.create(self.flow, self.contact.pk)

        # resend the message to him
        execution = self.execute_action(action, run, msg)

        # he will still get the english (base language)
        self.assertIsInstance(execution[0], Msg)
        self.assertEquals(execution[0].text, u'testENG\n1: labelENG\n2: label2ENG')

        # now set contact's language to hungarian
        self.contact.language = 'hun'
        self.contact.save(update_fields=('language',))
        run = FlowRun.create(self.flow, self.contact.pk)

        # resend the message to him
        execution = self.execute_action(action, run, msg)

        # he will get the partly translated hungarian version
        self.assertIsInstance(execution[0], Msg)
        self.assertEquals(execution[0].text, u'testHUN\n1: labelHUN\n2: label2ENG')

        Broadcast.objects.all().delete()

    def test_trigger_flow_action(self):
        flow = self.create_flow()
        run = FlowRun.create(self.flow, self.contact.pk)

        action = TriggerFlowAction(flow, [], [self.contact], [])
        self.execute_action(action, run, None)

        action_json = action.as_json()
        action = TriggerFlowAction.from_json(self.org, action_json)
        self.assertEqual(action.flow.pk, flow.pk)

        self.assertTrue(FlowRun.objects.filter(contact=self.contact, flow=flow))

        action = TriggerFlowAction(flow, [self.other_group], [], [])
        run = FlowRun.create(self.flow, self.contact.pk)
        msgs = self.execute_action(action, run, None)

        self.assertFalse(msgs)

        self.other_group.update_contacts(self.user, [self.contact2], True)

        action = TriggerFlowAction(flow, [self.other_group], [self.contact], [])
        run = FlowRun.create(self.flow, self.contact.pk)
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
        msg_body = "Hi @contact.name (@contact.state). @step.contact (@step.contact.state) is in the flow"

        self.contact.set_field(self.user, 'state', "WA", label="State")
        self.contact2.set_field(self.user, 'state', "GA", label="State")
        run = FlowRun.create(self.flow, self.contact.pk)

        action = SendAction(dict(base=msg_body),
                            [], [self.contact2], [])
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
        self.assertEqual(msg.text, "Hi Nic (GA). Eric (WA) is in the flow")

        # empty message should be a no-op
        action = SendAction(dict(base=""), [], [self.contact], [])
        self.execute_action(action, run, None)
        self.assertEqual(Broadcast.objects.all().count(), 1)

        # try with a test contact and a group
        test_contact = Contact.get_test_contact(self.user)
        test_contact.name = "Mr Test"
        test_contact.save()
        test_contact.set_field(self.user, 'state', "IN", label="State")

        self.other_group.update_contacts(self.user, [self.contact2], True)

        action = SendAction(dict(base=msg_body), [self.other_group], [test_contact], [])
        run = FlowRun.create(self.flow, test_contact.pk)
        self.execute_action(action, run, None)

        # since we are test contact now, no new broadcasts
        self.assertEqual(Broadcast.objects.all().count(), 1)

        # but we should have logged instead
        logged = "Sending &#39;Hi @contact.name (@contact.state). Mr Test (IN) is in the flow&#39; to 2 contacts"
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
        run = FlowRun.create(self.flow, self.contact.pk)
        msg_body = 'I am a media message message'

        action = SendAction(dict(base=msg_body), [], [self.contact2], [], dict(base='image/jpeg:attachments/picture.jpg'))
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
        action = SendAction(dict(base=""), [], [self.contact], [], dict(base='image/jpeg:attachments/picture.jpg'))
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
        run = FlowRun.create(self.flow, self.contact.pk)

        action = EmailAction(["steve@apple.com"], "Subject", "Body")

        # check to and from JSON
        action_json = action.as_json()
        action = EmailAction.from_json(self.org, action_json)

        self.execute_action(action, run, msg)

        self.assertEquals(len(mail.outbox), 1)
        self.assertEquals(mail.outbox[0].subject, "Subject")
        self.assertEquals(mail.outbox[0].body, "Body")
        self.assertEquals(mail.outbox[0].recipients(), ["steve@apple.com"])

        try:
            EmailAction([], "Subject", "Body")
            self.fail("Should have thrown due to empty recipient list")
        except FlowException:
            pass

        # check expression evaluation in action fields
        action = EmailAction(["@contact.name", "xyz", "", '@(SUBSTITUTE(LOWER(contact), " ", "") & "@nyaruka.com")'],
                             "@contact.name added in subject",
                             "@contact.name uses phone @contact.tel")

        action_json = action.as_json()
        action = EmailAction.from_json(self.org, action_json)

        self.execute_action(action, run, msg)

        self.assertEquals(len(mail.outbox), 2)
        self.assertEquals(mail.outbox[1].subject, "Eric added in subject")
        self.assertEquals(mail.outbox[1].body, "Eric uses phone 0788 382 382")
        self.assertEquals(mail.outbox[1].recipients(), ["eric@nyaruka.com"])  # invalid emails are ignored

        # check simulator reports invalid addresses
        test_contact = Contact.get_test_contact(self.user)
        test_run = FlowRun.create(self.flow, test_contact.pk)

        self.execute_action(action, test_run, msg)

        logs = list(ActionLog.objects.order_by('pk'))
        self.assertEqual(logs[0].level, ActionLog.LEVEL_INFO)
        self.assertEqual(logs[0].text, "&quot;Test Contact uses phone (206) 555-0100&quot; would be sent to &quot;testcontact@nyaruka.com&quot;")
        self.assertEqual(logs[1].level, ActionLog.LEVEL_WARN)
        self.assertEqual(logs[1].text, 'Some email address appear to be invalid: &quot;Test Contact&quot;, &quot;xyz&quot;, &quot;&quot;')

        # check that all white space is replaced with single spaces in the subject
        test = EmailAction(["steve@apple.com"], "Allo \n allo\tmessage", "Email notification for allo allo")
        self.execute_action(test, run, msg)

        self.assertEquals(len(mail.outbox), 3)
        self.assertEquals(mail.outbox[2].subject, 'Allo allo message')
        self.assertEquals(mail.outbox[2].body, 'Email notification for allo allo')
        self.assertEquals(mail.outbox[2].recipients(), ["steve@apple.com"])

        self.org.add_smtp_config('support@example.com', 'smtp.example.com', 'support@example.com', 'secret', '465', 'T', self.admin)

        action = EmailAction(["steve@apple.com"], "Subject", "Body")
        self.execute_action(action, run, msg)

        self.assertEquals(len(mail.outbox), 4)
        self.assertEquals(mail.outbox[3].from_email, 'support@example.com')
        self.assertEquals(mail.outbox[3].subject, 'Subject')
        self.assertEquals(mail.outbox[3].body, 'Body')
        self.assertEquals(mail.outbox[3].recipients(), ["steve@apple.com"])

    def test_save_to_contact_action(self):
        sms = self.create_msg(direction=INCOMING, contact=self.contact, text="batman")
        test = SaveToContactAction.from_json(self.org, dict(type='save', label="Superhero Name", value='@step'))
        run = FlowRun.create(self.flow, self.contact.pk)

        field = ContactField.objects.get(org=self.org, key="superhero_name")
        self.assertEquals("Superhero Name", field.label)

        self.execute_action(test, run, sms)

        # user should now have a nickname field with a value of batman
        contact = Contact.objects.get(id=self.contact.pk)
        self.assertEquals("batman", contact.get_field_raw('superhero_name'))

        # test clearing our value
        test = SaveToContactAction.from_json(self.org, test.as_json())
        test.value = ""
        self.execute_action(test, run, sms)
        contact = Contact.objects.get(id=self.contact.pk)
        self.assertEquals(None, contact.get_field_raw('superhero_name'))

        # test setting our name
        test = SaveToContactAction.from_json(self.org, dict(type='save', label="Name", value='', field='name'))
        test.value = "Eric Newcomer"
        self.execute_action(test, run, sms)
        contact = Contact.objects.get(id=self.contact.pk)
        self.assertEquals("Eric Newcomer", contact.name)
        run.contact = contact

        # test setting just the first name
        test = SaveToContactAction.from_json(self.org, dict(type='save', label="First Name", value='', field='first_name'))
        test.value = "Jen"
        self.execute_action(test, run, sms)
        contact = Contact.objects.get(id=self.contact.pk)
        self.assertEquals("Jen Newcomer", contact.name)

        # throw exception for other reserved words except name and first_name
        for word in Contact.RESERVED_FIELDS:
            if word not in ['name', 'first_name'] + list(URN.VALID_SCHEMES):
                with self.assertRaises(Exception):
                    test = SaveToContactAction.from_json(self.org, dict(type='save', label=word, value='', field=word))
                    test.value = "Jen"
                    self.execute_action(test, run, sms)

        # we should strip whitespace
        run.contact = contact
        test = SaveToContactAction.from_json(self.org, dict(type='save', label="First Name", value='', field='first_name'))
        test.value = " Jackson "
        self.execute_action(test, run, sms)
        contact = Contact.objects.get(id=self.contact.pk)
        self.assertEquals("Jackson Newcomer", contact.name)

        # first name works with a single word
        run.contact = contact
        contact.name = "Percy"
        contact.save()

        test = SaveToContactAction.from_json(self.org, dict(type='save', label="First Name", value='', field='first_name'))
        test.value = " Cole"
        self.execute_action(test, run, sms)
        contact = Contact.objects.get(id=self.contact.pk)
        self.assertEquals("Cole", contact.name)

        # test saving something really long to another field
        test = SaveToContactAction.from_json(self.org, dict(type='save', label="Last Message", value='', field='last_message'))
        test.value = "This is a long message, longer than 160 characters, longer than 250 characters, all the way up "\
                     "to 500 some characters long because sometimes people save entire messages to their contact " \
                     "fields and we want to enable that for them so that they can do what they want with the platform."
        self.execute_action(test, run, sms)
        contact = Contact.objects.get(id=self.contact.pk)
        self.assertEquals(test.value, contact.get_field('last_message').string_value)

        # test saving a contact's phone number
        test = SaveToContactAction.from_json(self.org, dict(type='save', label='Phone Number', field='tel_e164', value='@step'))

        # make sure they have a twitter urn first
        contact.urns.add(ContactURN.create(self.org, None, 'twitter:enewcomer'))
        self.assertIsNotNone(contact.urns.filter(path='enewcomer').first())

        # add another phone number to make sure it doesn't get removed too
        contact.urns.add(ContactURN.create(self.org, None, 'tel:+18005551212'))
        self.assertEquals(3, contact.urns.all().count())

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
        self.assertEquals(4, contact.urns.all().count())
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
        run = FlowRun.create(self.flow, test_contact.pk)
        self.execute_action(test, run, sms)

        ActionLog.objects.all().delete()
        action = SaveToContactAction.from_json(self.org, dict(type='save', label="mailto", value='foo@bar.com'))
        self.execute_action(action, run, None)
        self.assertEquals(ActionLog.objects.get().text, "Added foo@bar.com as @contact.mailto - skipped in simulator")

        # Invalid email
        ActionLog.objects.all().delete()
        action = SaveToContactAction.from_json(self.org, dict(type='save', label="mailto", value='foobar.com'))
        self.execute_action(action, run, None)
        self.assertEquals(ActionLog.objects.get().text, "Contact not updated, invalid connection for contact (mailto:foobar.com)")

        # URN should be unchanged on the simulator contact
        test_contact = Contact.objects.get(id=test_contact.id)
        self.assertEquals(test_contact_urn, test_contact.urns.all().first())

        self.assertFalse(ContactField.objects.filter(org=self.org, label='Ecole'))
        SaveToContactAction.from_json(self.org, dict(type='save', label="[_NEW_]Ecole", value='@step'))
        field = ContactField.objects.get(org=self.org, key="ecole")
        self.assertEquals("Ecole", field.label)

        # try saving some empty data into mailto
        ActionLog.objects.all().delete()
        action = SaveToContactAction.from_json(self.org, dict(type='save', label="mailto", value='@contact.mailto'))
        self.execute_action(action, run, None)
        self.assertEquals(ActionLog.objects.get().text, "Contact not updated, missing connection for contact")

    def test_set_language_action(self):
        action = SetLanguageAction('kli', 'Klingon')

        # check to and from JSON
        action_json = action.as_json()
        action = SetLanguageAction.from_json(self.org, action_json)

        self.assertEqual('kli', action.lang)
        self.assertEqual('Klingon', action.name)

        # execute our action and check we are Klingon now, eeektorp shnockahltip.
        run = FlowRun.create(self.flow, self.contact.pk)
        self.execute_action(action, run, None)
        self.assertEquals('kli', Contact.objects.get(pk=self.contact.pk).language)

        # try setting the language to something thats not three characters
        action_json['lang'] = 'base'
        action_json['name'] = 'Default'
        action = SetLanguageAction.from_json(self.org, action_json)
        self.execute_action(action, run, None)

        # should clear the contacts language
        self.assertIsNone(Contact.objects.get(pk=self.contact.pk).language)

    def test_start_flow_action(self):
        self.flow.update(self.COLOR_FLOW_DEFINITION)
        self.flow.name = 'Parent'
        self.flow.save()

        self.flow.start([], [self.contact])

        sms = Msg.create_incoming(self.channel, "tel:+250788382382", "Blue is my favorite")

        run = FlowRun.objects.get()

        new_flow = Flow.create_single_message(self.org, self.user,
                                              {'base': "You chose @parent.color.category"}, base_language='base')
        action = StartFlowAction(new_flow)

        action_json = action.as_json()
        action = StartFlowAction.from_json(self.org, action_json)

        self.execute_action(action, run, sms, started_flows=[])

        # our contact should now be in the flow
        self.assertTrue(FlowStep.objects.filter(run__flow=new_flow, run__contact=self.contact))
        self.assertTrue(Msg.objects.filter(contact=self.contact, direction='O', text='You chose Blue'))

    def test_group_actions(self):
        msg = self.create_msg(direction=INCOMING, contact=self.contact, text="Green is my favorite")
        run = FlowRun.create(self.flow, self.contact.pk)

        test_contact = Contact.get_test_contact(self.admin)
        test_msg = self.create_msg(direction=INCOMING, contact=self.contact, text="Blue")
        test_run = FlowRun.create(self.flow, test_contact.pk)

        group = self.create_group("Flow Group", [])

        # check converting to and from json
        action = AddToGroupAction([group, "@step.contact"])
        action_json = action.as_json()
        action = AddToGroupAction.from_json(self.org, action_json)

        self.execute_action(action, run, msg)

        # user should now be in the group
        self.assertEqual(set(group.contacts.all()), {self.contact})

        # we should have created a group with the name of the contact
        replace_group1 = ContactGroup.user_groups.get(name=self.contact.name)
        self.assertEqual(set(replace_group1.contacts.all()), {self.contact})

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

        # with test contact, action logs are also created
        self.execute_action(action, test_run, test_msg)

        replace_group2 = ContactGroup.user_groups.get(name=test_contact.name)

        self.assertEqual(set(group.contacts.all()), {self.contact, test_contact})
        self.assertEqual(set(replace_group1.contacts.all()), {self.contact})
        self.assertEqual(set(replace_group2.contacts.all()), {test_contact})
        self.assertEqual(ActionLog.objects.filter(level='I').count(), 3)

        # now try remove action
        action = DeleteFromGroupAction([group, "@step.contact"])
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
        self.assertEqual(ActionLog.objects.filter(level='I').count(), 5)

        # try when group is inactive
        action = DeleteFromGroupAction([group])
        group.is_active = False
        group.save()

        self.assertIn(group, action.groups)

        # reading the action should create a new group
        updated_action = DeleteFromGroupAction.from_json(self.org, action.as_json())
        self.assertTrue(updated_action.groups)
        self.assertFalse(group.pk in [g.pk for g in updated_action.groups])

        # try adding a contact to a dynamic group
        self.create_field('isalive', "Is Alive")
        dynamic_group = self.create_group("Dynamic", query="isalive=YES")
        action = AddToGroupAction([dynamic_group])

        self.execute_action(action, run, msg)

        # should do nothing
        self.assertEqual(dynamic_group.contacts.count(), 0)

        # tho if contact is a test contact, log as error
        self.execute_action(action, test_run, test_msg)

        self.assertEqual(dynamic_group.contacts.count(), 0)

        self.assertEqual(ActionLog.objects.filter(level='E').count(), 1)

        group1 = self.create_group("Flow Group 1", [])
        group2 = self.create_group("Flow Group 2", [])

        test = AddToGroupAction([group1])
        action_json = test.as_json()
        test = AddToGroupAction.from_json(self.org, action_json)

        self.execute_action(test, run, test_msg)

        test = AddToGroupAction([group2])
        action_json = test.as_json()
        test = AddToGroupAction.from_json(self.org, action_json)

        self.execute_action(test, run, test_msg)

        # user should be in both groups now
        self.assertTrue(group1.contacts.filter(id=self.contact.pk))
        self.assertEquals(1, group1.contacts.all().count())
        self.assertTrue(group2.contacts.filter(id=self.contact.pk))
        self.assertEquals(1, group2.contacts.all().count())

        test = DeleteFromGroupAction([])
        action_json = test.as_json()
        test = DeleteFromGroupAction.from_json(self.org, action_json)

        self.execute_action(test, run, test_msg)

        # user should be gone from both groups now
        self.assertFalse(group1.contacts.filter(id=self.contact.pk))
        self.assertEquals(0, group1.contacts.all().count())
        self.assertFalse(group2.contacts.filter(id=self.contact.pk))
        self.assertEquals(0, group2.contacts.all().count())

    def test_set_channel_action(self):
        flow = self.flow
        run = FlowRun.create(flow, self.contact.pk)

        tel1_channel = Channel.add_config_external_channel(self.org, self.admin, 'US', '+12061111111', 'KN', {})
        tel2_channel = Channel.add_config_external_channel(self.org, self.admin, 'US', '+12062222222', 'KN', {})

        fb_channel = Channel.create(self.org, self.user, None, 'FB', address="Page Id",
                                    config={'page_name': "Page Name", 'auth_token': "Page Token"})

        # create an incoming message on tel1, this should create an affinity to that channel
        Msg.create_incoming(tel1_channel, str(self.contact.urns.all().first()), "Incoming msg")
        urn = self.contact.urns.all().first()
        self.assertEqual(urn.channel, tel1_channel)

        action = SetChannelAction(tel2_channel)
        self.execute_action(action, run, None)

        # check the affinity on our urn again, should now be the second channel
        urn.refresh_from_db()
        self.assertEqual(urn.channel, tel2_channel)

        # try to set it to a channel that we don't have a URN for
        action = SetChannelAction(fb_channel)
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
        action = SetChannelAction(tel1_channel)
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
        action = SetChannelAction(None)
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
        run = FlowRun.create(flow, self.contact.pk)

        label = Label.get_or_create(self.org, self.user, "green label")

        action = AddLabelAction([label, "@step.contact"])

        action_json = action.as_json()
        action = AddLabelAction.from_json(self.org, action_json)

        # no message yet; such Add Label action on entry Actionset. No error should be raised
        self.execute_action(action, run, None)

        self.assertFalse(label.get_messages())
        self.assertEqual(label.get_visible_count(), 0)

        self.execute_action(action, run, msg)

        # new label should have been created with the name of the contact
        new_label = Label.label_objects.get(name=self.contact.name)
        label = Label.label_objects.get(pk=label.pk)

        # and message should have been labeled with both labels
        msg = Msg.objects.get(pk=msg.pk)
        self.assertEqual(set(msg.labels.all()), {label, new_label})
        self.assertEqual(set(label.get_messages()), {msg})
        self.assertEqual(label.get_visible_count(), 1)
        self.assertTrue(set(new_label.get_messages()), {msg})
        self.assertEqual(new_label.get_visible_count(), 1)

        # passing through twice doesn't change anything
        self.execute_action(action, run, msg)

        self.assertEqual(set(Msg.objects.get(pk=msg.pk).labels.all()), {label, new_label})
        self.assertEquals(Label.label_objects.get(pk=label.pk).get_visible_count(), 1)
        self.assertEquals(Label.label_objects.get(pk=new_label.pk).get_visible_count(), 1)

    @override_settings(SEND_WEBHOOKS=True)
    @patch('django.utils.timezone.now')
    @patch('requests.post')
    def test_webhook_action(self, mock_requests_post, mock_timezone_now):
        tz = pytz.timezone("Africa/Kigali")
        mock_requests_post.return_value = MockResponse(200, '{ "coupon": "NEXUS4" }')
        mock_timezone_now.return_value = tz.localize(datetime.datetime(2015, 10, 27, 16, 7, 30, 6))

        action = WebhookAction('http://example.com/callback.php',
                               webhook_headers=[{'name': 'Authorization', 'value': 'Token 12345'}])

        # check to and from JSON
        action_json = action.as_json()
        action = WebhookAction.from_json(self.org, action_json)

        self.assertEqual(action.webhook, 'http://example.com/callback.php')

        run = FlowRun.create(self.flow, self.contact.pk)

        # test with no incoming message
        self.execute_action(action, run, None)

        # check webhook was called with correct payload
        mock_requests_post.assert_called_once_with(
            'http://example.com/callback.php',
            headers={'Authorization': 'Token 12345', 'User-agent': 'RapidPro'},
            data={
                'run': run.pk,
                'phone': u'+250788382382',
                'contact': self.contact.uuid,
                'contact_name': self.contact.name,
                'urn': u'tel:+250788382382',
                'text': None,
                'attachments': [],
                'flow': self.flow.pk,
                'flow_uuid': self.flow.uuid,
                'flow_name': self.flow.name,
                'flow_base_language': self.flow.base_language,
                'relayer': -1,
                'step': 'None',
                'values': '[]',
                'time': '2015-10-27T14:07:30.000006Z',
                'steps': '[]',
                'channel': -1,
                'channel_uuid': None,
                'header': {'Authorization': 'Token 12345'}
            },
            timeout=10
        )
        mock_requests_post.reset_mock()

        # check that run @extra was updated
        self.assertEqual(json.loads(run.fields), {'coupon': "NEXUS4"})

        # test with an incoming message
        msg = self.create_msg(direction=INCOMING, contact=self.contact, text="Green is my favorite",
                              attachments=['image/jpeg:http://example.com/test.jpg'])
        self.execute_action(action, run, msg)

        # check webhook was called with correct payload
        mock_requests_post.assert_called_once_with(
            'http://example.com/callback.php',
            headers={'User-agent': 'RapidPro', 'Authorization': 'Token 12345'},
            data={
                'run': run.pk,
                'phone': u'+250788382382',
                'contact': self.contact.uuid,
                'contact_name': self.contact.name,
                'urn': u'tel:+250788382382',
                'text': "Green is my favorite",
                'attachments': ["http://example.com/test.jpg"],
                'flow': self.flow.pk,
                'flow_uuid': self.flow.uuid,
                'flow_name': self.flow.name,
                'flow_base_language': self.flow.base_language,
                'relayer': msg.channel.pk,
                'step': 'None',
                'values': '[]',
                'time': '2015-10-27T14:07:30.000006Z',
                'steps': '[]',
                'channel': msg.channel.pk,
                'channel_uuid': msg.channel.uuid,
                'header': {'Authorization': 'Token 12345'}
            },
            timeout=10
        )

        # check simulator warns of webhook URL errors
        action = WebhookAction('http://example.com/callback.php?@contact.xyz')
        test_contact = Contact.get_test_contact(self.user)
        test_run = FlowRun.create(self.flow, test_contact.pk)

        self.execute_action(action, test_run, None)

        event = WebHookEvent.objects.order_by('-pk').first()

        logs = list(ActionLog.objects.order_by('pk'))
        self.assertEqual(logs[0].level, ActionLog.LEVEL_WARN)
        self.assertEqual(logs[0].text, "URL appears to contain errors: Undefined variable: contact.xyz")
        self.assertEqual(logs[1].level, ActionLog.LEVEL_INFO)
        self.assertEqual(logs[1].text, "Triggered <a href='/webhooks/log/%d/' target='_log'>webhook event</a> - 200" % event.pk)


class FlowRunTest(TembaTest):

    def setUp(self):
        super(FlowRunTest, self).setUp()

        self.flow = self.create_flow()
        self.contact = self.create_contact("Ben Haggerty", "+250788123123")

    def test_field_normalization(self):
        fields = dict(field1="value1", field2="value2")
        (normalized, count) = FlowRun.normalize_fields(fields)
        self.assertEqual(normalized, fields)

        # spaces in field keys
        fields = {'value 1': 'value1', 'value-2': 'value2'}
        (normalized, count) = FlowRun.normalize_fields(fields)
        self.assertEqual(normalized, dict(value_1='value1', value_2='value2'))

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
        run = FlowRun.create(self.flow, self.contact.pk)

        # set our fields from an empty state
        new_values = dict(Field1="value1", field_2="value2")
        run.update_fields(new_values)

        self.assertEquals(run.field_dict(), new_values)

        run.update_fields(dict(field2="new value2", field3="value3"))
        new_values['field2'] = "new value2"
        new_values['field3'] = "value3"

        self.assertEquals(run.field_dict(), new_values)

        run.update_fields(dict(field1=""))
        new_values['field1'] = ""

        self.assertEquals(run.field_dict(), new_values)

        # clear our fields
        run.fields = None
        run.save()

        # set to a list instead
        run.update_fields(["zero", "one", "two"])
        self.assertEqual(run.field_dict(), {"0": "zero", "1": "one", "2": "two"})

    def test_is_completed(self):
        self.flow.update(self.COLOR_FLOW_DEFINITION)
        self.flow.start([], [self.contact])

        self.assertFalse(FlowRun.objects.get(contact=self.contact).is_completed())

        incoming = self.create_msg(direction=INCOMING, contact=self.contact, text="orange")
        Flow.find_and_handle(incoming)

        self.assertTrue(FlowRun.objects.get(contact=self.contact).is_completed())

    def test_is_interrupted(self):
        self.channel.delete()
        # Create a USSD channel type to test USSDSession.INTERRUPTED status
        self.channel = Channel.create(self.org, self.user, 'RW', Channel.TYPE_JUNEBUG_USSD, None, '+250788123123',
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
        self.assertEquals(response.name, "alongwordcomposedofmorethanthirt")

        # try to create another label which starts with the same 32 caracteres
        # the one we already have
        label = FlowLabel.create_unique("alongwordcomposedofmorethanthirtytwocaracteres",
                                        self.org, parent=None)

        self.assertEquals(label.name, "alongwordcomposedofmorethanthi 2")
        self.assertEquals(str(label), "alongwordcomposedofmorethanthi 2")
        label = FlowLabel.create_unique("child", self.org, parent=label)
        self.assertEquals(str(label), "alongwordcomposedofmorethanthi 2 > child")

        FlowLabel.create_unique("dog", self.org)
        FlowLabel.create_unique("dog", self.org)
        dog3 = FlowLabel.create_unique("dog", self.org)
        self.assertEquals("dog 3", dog3.name)

        dog4 = FlowLabel.create_unique("dog ", self.org)
        self.assertEquals("dog 4", dog4.name)

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
        self.assertEquals(FlowLabel.objects.all().count(), 1)
        self.assertEquals(FlowLabel.objects.all()[0].parent, None)

        label_one = FlowLabel.objects.all()[0]
        post_data = dict(name="sub_label", parent=label_one.pk)
        response = self.client.post(create_url, post_data, follow=True)

        self.assertEquals(FlowLabel.objects.all().count(), 2)
        self.assertEquals(FlowLabel.objects.filter(parent=None).count(), 1)

        post_data = dict(name="sub_label ", parent=label_one.pk)
        response = self.client.post(create_url, post_data, follow=True)
        self.assertTrue('form' in response.context)
        self.assertTrue(response.context['form'].errors)
        self.assertEquals('Name already used', response.context['form'].errors['name'][0])

        self.assertEquals(FlowLabel.objects.all().count(), 2)
        self.assertEquals(FlowLabel.objects.filter(parent=None).count(), 1)

        post_data = dict(name="label from modal")
        response = self.client.post("%s?format=modal" % create_url, post_data, follow=True)
        self.assertEquals(FlowLabel.objects.all().count(), 3)

    def test_delete(self):
        label_one = FlowLabel.create_unique("label1", self.org)

        delete_url = reverse('flows.flowlabel_delete', args=[label_one.pk])

        self.other_user = self.create_user("ironman")

        self.login(self.other_user)
        response = self.client.get(delete_url)
        self.assertEquals(response.status_code, 302)

        self.login(self.admin)
        response = self.client.get(delete_url)
        self.assertEquals(response.status_code, 200)

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

    def setUp(self):
        super(WebhookTest, self).setUp()
        settings.SEND_WEBHOOKS = True

    def tearDown(self):
        super(WebhookTest, self).tearDown()
        settings.SEND_WEBHOOKS = False

    def test_webhook_subflow_extra(self):
        # import out flow that triggers another flow
        contact1 = self.create_contact("Marshawn", "+14255551212")
        substitutions = dict(contact_id=contact1.id)
        flow = self.get_flow('triggered', substitutions)

        with patch('requests.get') as get:
            get.return_value = MockResponse(200, '{ "text": "(I came from a webhook)" }')
            flow.start(groups=[], contacts=[contact1], restart_participants=True)

            # first message from our trigger flow action
            msg = Msg.objects.all().order_by('-created_on')[0]
            self.assertEqual('Honey, I triggered the flow! (I came from a webhook)', msg.text)

            # second message from our start flow action
            msg = Msg.objects.all().order_by('-created_on')[1]
            self.assertEqual('Honey, I triggered the flow! (I came from a webhook)', msg.text)

    def test_webhook(self):
        self.flow = self.create_flow(definition=self.COLOR_FLOW_DEFINITION)
        self.contact = self.create_contact("Ben Haggerty", '+250788383383')

        run = FlowRun.create(self.flow, self.contact.pk)

        split_uuid = str(uuid4())
        valid_uuid = str(uuid4())

        # webhook ruleset comes first
        webhook = RuleSet.objects.create(flow=self.flow, uuid=str(uuid4()), x=0, y=0, ruleset_type=RuleSet.TYPE_WEBHOOK)
        config = {RuleSet.CONFIG_WEBHOOK: "http://ordercheck.com/check_order.php?phone=@step.contact.tel_e164",
                  RuleSet.CONFIG_WEBHOOK_ACTION: "GET",
                  RuleSet.CONFIG_WEBHOOK_HEADERS: [{"name": "Authorization", "value": "Token 12345"}]}
        webhook.config = json.dumps(config)
        webhook.set_rules_dict([Rule(str(uuid4()), dict(base="All Responses"), split_uuid, 'R', TrueTest()).as_json()])
        webhook.save()

        # and a ruleset to split off the results
        rules = RuleSet.objects.create(flow=self.flow, uuid=split_uuid, x=0, y=200, ruleset_type=RuleSet.TYPE_EXPRESSION)
        rules.set_rules_dict([Rule(valid_uuid, dict(base="Valid"), "7d40faea-723b-473d-8999-59fb7d3c3ca2", 'A', ContainsTest(dict(base="valid"))).as_json(),
                              Rule(str(uuid4()), dict(base="Invalid"), "c12f37e2-8e6c-4c81-ba6d-941bb3caf93f", 'A', ContainsTest(dict(base="invalid"))).as_json()])
        rules.save()

        webhook_step = FlowStep.objects.create(run=run, contact=run.contact, step_type=FlowStep.TYPE_RULE_SET,
                                               step_uuid=webhook.uuid, arrived_on=timezone.now())
        incoming = self.create_msg(direction=INCOMING, contact=self.contact, text="1001")

        (match, value) = rules.find_matching_rule(webhook_step, run, incoming)
        self.assertIsNone(match)
        self.assertIsNone(value)

        rules.operand = "@extra.text @extra.blank"
        rules.save()

        with patch('requests.get') as get:
            with patch('requests.post') as post:
                get.return_value = MockResponse(200, '{ "text": "Get", "blank": "" }')
                post.return_value = MockResponse(200, '{ "text": "Post", "blank": "" }')

                # first do a GET
                webhook.find_matching_rule(webhook_step, run, incoming)
                self.assertEquals(dict(text="Get", blank=""), run.field_dict())

                # assert our phone number got encoded
                self.assertEquals("http://ordercheck.com/check_order.php?phone=%2B250788383383", get.call_args[0][0])

                # now do a POST
                config = webhook.config_json()
                config[RuleSet.CONFIG_WEBHOOK_ACTION] = 'POST'
                webhook.config = json.dumps(config)
                webhook.save()
                webhook.find_matching_rule(webhook_step, run, incoming)
                self.assertEquals(dict(text="Post", blank=""), run.field_dict())

                self.assertEquals("http://ordercheck.com/check_order.php?phone=%2B250788383383", post.call_args[0][0])

        # remove @extra.blank from our text
        rules.operand = "@extra.text"
        rules.save()

        # clear our run's field dict
        run.fields = json.dumps(dict())
        run.save()

        rule_step = FlowStep.objects.create(run=run, contact=run.contact, step_type=FlowStep.TYPE_RULE_SET,
                                            step_uuid=rules.uuid, arrived_on=timezone.now())

        with patch('requests.post') as mock:
            mock.return_value = MockResponse(200, '{ "text": "Valid" }')

            (match, value) = webhook.find_matching_rule(webhook_step, run, incoming)
            (match, value) = rules.find_matching_rule(rule_step, run, incoming)

            self.assertEquals(valid_uuid, match.uuid)
            self.assertEquals("Valid", value)
            self.assertEquals(dict(text="Valid"), run.field_dict())

        with patch('requests.post') as mock:
            mock.return_value = MockResponse(200, '{ "text": "Valid", "order_number": "PX1001" }')

            (match, value) = webhook.find_matching_rule(webhook_step, run, incoming)
            (match, value) = rules.find_matching_rule(rule_step, run, incoming)
            self.assertEquals(valid_uuid, match.uuid)
            self.assertEquals("Valid", value)
            self.assertEquals(dict(text="Valid", order_number="PX1001"), run.field_dict())

            message_context = self.flow.build_expressions_context(self.contact, incoming)
            self.assertEquals(dict(text="Valid", order_number="PX1001"), message_context['extra'])

        with patch('requests.post') as mock:
            mock.return_value = MockResponse(200, '{ "text": "Valid", "order_number": "PX1002" }')

            webhook.find_matching_rule(webhook_step, run, incoming)
            (match, value) = rules.find_matching_rule(rule_step, run, incoming)
            self.assertEquals(valid_uuid, match.uuid)
            self.assertEquals("Valid", value)
            self.assertEquals(dict(text="Valid", order_number="PX1002"), run.field_dict())

            message_context = self.flow.build_expressions_context(self.contact, incoming)
            self.assertEquals(dict(text="Valid", order_number="PX1002"), message_context['extra'])

        with patch('requests.post') as mock:
            mock.return_value = MockResponse(200, '["zero", "one", "two"]')
            rule_step.run.fields = None
            rule_step.run.save()

            webhook.find_matching_rule(webhook_step, run, incoming)
            (match, value) = rules.find_matching_rule(rule_step, run, incoming)
            self.assertIsNone(match)
            self.assertIsNone(value)
            self.assertEquals("1001", incoming.text)

            message_context = self.flow.build_expressions_context(self.contact, incoming)
            self.assertEqual(message_context['extra'], {'0': 'zero', '1': 'one', '2': 'two'})

        with patch('requests.post') as mock:
            mock.return_value = MockResponse(200, json.dumps(range(300)))
            rule_step.run.fields = None
            rule_step.run.save()

            webhook.find_matching_rule(webhook_step, run, incoming)
            (match, value) = rules.find_matching_rule(rule_step, run, incoming)
            self.assertIsNone(match)
            self.assertIsNone(value)
            self.assertEquals("1001", incoming.text)

            message_context = self.flow.build_expressions_context(self.contact, incoming)
            extra = message_context['extra']

            # should only keep first 256 values
            self.assertEqual(256, len(extra))
            self.assertFalse('256' in extra)

        with patch('requests.post') as mock:
            mock.return_value = MockResponse(200, "asdfasdfasdf")
            rule_step.run.fields = None
            rule_step.run.save()

            webhook.find_matching_rule(webhook_step, run, incoming)
            (match, value) = rules.find_matching_rule(rule_step, run, incoming)
            self.assertIsNone(match)
            self.assertIsNone(value)
            self.assertEquals("1001", incoming.text)

            message_context = self.flow.build_expressions_context(self.contact, incoming)
            self.assertEquals({}, message_context['extra'])

        with patch('requests.post') as mock:
            mock.return_value = MockResponse(200, "12345")
            rule_step.run.fields = None
            rule_step.run.save()

            webhook.find_matching_rule(webhook_step, run, incoming)
            (match, value) = rules.find_matching_rule(rule_step, run, incoming)
            self.assertIsNone(match)
            self.assertIsNone(value)
            self.assertEquals("1001", incoming.text)

            message_context = self.flow.build_expressions_context(self.contact, incoming)
            self.assertEquals({}, message_context['extra'])

        with patch('requests.post') as mock:
            mock.return_value = MockResponse(500, "Server Error")
            rule_step.run.fields = None
            rule_step.run.save()

            webhook.find_matching_rule(webhook_step, run, incoming)
            (match, value) = rules.find_matching_rule(rule_step, run, incoming)
            self.assertIsNone(match)
            self.assertIsNone(value)
            self.assertEquals("1001", incoming.text)

    def test_resthook(self):
        self.contact = self.create_contact("Macklemore", "+12067799294")
        webhook_flow = self.get_flow('resthooks')

        # we don't have the resthook registered yet, so this won't trigger any calls
        with patch('requests.post') as mock_post:
            webhook_flow.start([], [self.contact])
            self.assertEqual(mock_post.call_count, 0)

            # should have two messages of failures
            self.assertEqual("That was a success.", Msg.objects.filter(contact=self.contact).last().text)
            self.assertEqual("The second succeeded.", Msg.objects.filter(contact=self.contact).first().text)

            # but we should have created a webhook event regardless
            self.assertTrue(WebHookEvent.objects.filter(resthook__slug='new-registration'))

        # ok, let's go add a listener for that event (should have been created automatically)
        resthook = Resthook.objects.get(org=self.org, slug='new-registration')
        resthook.subscribers.create(target_url='https://foo.bar/', created_by=self.admin, modified_by=self.admin)
        resthook.subscribers.create(target_url='https://bar.foo/', created_by=self.admin, modified_by=self.admin)

        # clear out our messages
        Msg.objects.filter(contact=self.contact).delete()

        # start over, have our first webhook fail, check that routing still works with failure
        with patch('requests.post') as mock_post:
            mock_post.side_effect = [MockResponse(200, '{ "code": "ABABUUDDLRS" }'), MockResponse(400, "Failure"),
                                     MockResponse(410, 'Unsubscribe'), MockResponse(400, "Failure")]

            webhook_flow.start([], [self.contact], restart_participants=True)

            # should have called all our subscribers
            self.assertEqual(mock_post.call_args_list[0][0][0], 'https://foo.bar/')
            self.assertEqual(mock_post.call_args_list[1][0][0], 'https://bar.foo/')
            self.assertEqual(mock_post.call_args_list[2][0][0], 'https://foo.bar/')
            self.assertEqual(mock_post.call_args_list[3][0][0], 'https://bar.foo/')

            # first should be a success because we had at least one success
            self.assertEqual("That was a success.", Msg.objects.filter(contact=self.contact).last().text)

            # second, both failed so should be a failure
            self.assertEqual("The second failed.", Msg.objects.filter(contact=self.contact).first().text)

            # we should also have unsubscribed from one of our endpoints
            self.assertTrue(resthook.subscribers.filter(is_active=False, target_url='https://foo.bar/'))
            self.assertTrue(resthook.subscribers.filter(is_active=True, target_url='https://bar.foo/'))


class SimulationTest(FlowFileTest):

    def test_simulation(self):
        flow = self.get_flow('pick_a_number')

        # remove our channels
        self.org.channels.all().delete()

        simulate_url = reverse('flows.flow_simulate', args=[flow.pk])
        self.admin.first_name = "Ben"
        self.admin.last_name = "Haggerty"
        self.admin.save()

        post_data = dict()
        post_data['has_refresh'] = True

        self.login(self.admin)
        response = self.client.post(simulate_url, json.dumps(post_data), content_type="application/json")
        json_dict = response.json()

        self.assertEquals(len(json_dict.keys()), 6)
        self.assertEquals(len(json_dict['messages']), 2)
        self.assertEquals('Ben Haggerty has entered the &quot;Pick a Number&quot; flow', json_dict['messages'][0]['text'])
        self.assertEquals("Pick a number between 1-10.", json_dict['messages'][1]['text'])

        post_data['new_message'] = "3"
        post_data['has_refresh'] = False

        response = self.client.post(simulate_url, json.dumps(post_data), content_type="application/json")
        self.assertEquals(200, response.status_code)
        json_dict = response.json()

        self.assertEquals(len(json_dict['messages']), 6)
        self.assertEquals("3", json_dict['messages'][2]['text'])
        self.assertEquals("Saved &#39;3&#39; as @flow.number", json_dict['messages'][3]['text'])
        self.assertEquals("You picked 3!", json_dict['messages'][4]['text'])
        self.assertEquals('Ben Haggerty has exited this flow', json_dict['messages'][5]['text'])

    @patch('temba.ussd.models.USSDSession.handle_incoming')
    def test_ussd_simulation(self, handle_incoming):
        self.channel.delete()
        self.channel = Channel.create(self.org, self.user, 'RW', Channel.TYPE_JUNEBUG_USSD, None, '+250788123123',
                                      role=Channel.ROLE_USSD + Channel.DEFAULT_ROLE)
        flow = self.get_flow('ussd_example')

        simulate_url = reverse('flows.flow_simulate', args=[flow.pk])

        post_data = dict(has_refresh=True, new_message="derp")

        self.login(self.admin)
        response = self.client.post(simulate_url, json.dumps(post_data), content_type="application/json")

        self.assertEquals(response.status_code, 200)

        # session should have started now
        self.assertTrue(handle_incoming.called)
        self.assertEqual(handle_incoming.call_count, 1)

        self.assertIsNone(handle_incoming.call_args[1]['status'])

        self.channel.delete()
        response = self.client.post(simulate_url, json.dumps(post_data), content_type="application/json")
        self.assertEquals(response.status_code, 200)

    @patch('temba.ussd.models.USSDSession.handle_incoming')
    def test_ussd_simulation_interrupt(self, handle_incoming):
        self.channel.delete()
        self.channel = Channel.create(self.org, self.user, 'RW', Channel.TYPE_JUNEBUG_USSD, None, '+250788123123',
                                      role=Channel.ROLE_USSD + Channel.DEFAULT_ROLE)
        flow = self.get_flow('ussd_example')

        simulate_url = reverse('flows.flow_simulate', args=[flow.pk])

        post_data = dict(has_refresh=True, new_message="__interrupt__")

        self.login(self.admin)
        response = self.client.post(simulate_url, json.dumps(post_data), content_type="application/json")

        self.assertEquals(response.status_code, 200)

        # session should have started now
        self.assertTrue(handle_incoming.called)
        self.assertEqual(handle_incoming.call_count, 1)

        self.assertEqual(handle_incoming.call_args[1]['status'], USSDSession.INTERRUPTED)

    def test_ussd_simulation_session_end(self):
        self.ussd_channel = Channel.create(
            self.org, self.user, 'RW', Channel.TYPE_JUNEBUG_USSD, None, '*123#',
            scheme='tel', uuid='00000000-0000-0000-0000-000000002222',
            role=Channel.ROLE_USSD)

        flow = self.get_flow('ussd_session_end')

        simulate_url = reverse('flows.flow_simulate', args=[flow.pk])

        post_data = dict(has_refresh=True, new_message="4")

        self.login(self.admin)
        response = self.client.post(simulate_url, json.dumps(post_data), content_type="application/json")

        self.assertEquals(response.status_code, 200)

        session = USSDSession.objects.get()
        self.assertEquals(session.status, USSDSession.COMPLETED)

    def test_ussd_simulation_without_channel_doesnt_run(self):
        Channel.objects.all().delete()

        flow = self.get_flow('ussd_session_end')

        simulate_url = reverse('flows.flow_simulate', args=[flow.pk])

        post_data = dict(has_refresh=True, new_message="4")

        self.login(self.admin)
        response = self.client.post(simulate_url, json.dumps(post_data), content_type="application/json")
        self.assertEquals(response.status_code, 400)
        self.assertEqual(response.json()['status'], 'error')

        self.assertEqual(flow.runs.count(), 0)


class FlowsTest(FlowFileTest):

    def test_validate_flow_definition(self):

        with self.assertRaises(ValueError):
            self.get_flow('not_fully_localized')

        # base_language of null, but spec version 8
        with self.assertRaises(ValueError):
            self.get_flow('no_base_language_v8')

        # base_language of 'eng' but non localized actions
        with self.assertRaises(ValueError):
            self.get_flow('non_localized_with_language')

        with self.assertRaises(ValueError):
            self.get_flow('non_localized_ruleset')

    def test_sms_forms(self):
        flow = self.get_flow('sms_form')

        def assert_response(message, response):
            self.assertEquals(response, self.send_message(flow, message, restart_participants=True))

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
                config = ruleset.config_json()
                config['field_delimiter'] = delimiter
                ruleset.set_config(config)
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

        # saving should work
        response = flow.update(flow_json, self.admin)
        self.assertEquals(response.get('status'), 'success')

        # but if we save from in the past after our save it should fail
        response = flow.update(flow_json, self.admin)
        self.assertEquals(response.get('status'), 'unsaved')

    def test_flow_results(self):
        favorites = self.get_flow('favorites')
        FlowCRUDL.RunTable.paginate_by = 1

        pete = self.create_contact('Pete', '+12065553027')
        self.send_message(favorites, 'blue', contact=pete)

        jimmy = self.create_contact('Jimmy', '+12065553026')
        self.send_message(favorites, 'red', contact=jimmy)
        self.send_message(favorites, 'turbo', contact=jimmy)

        kobe = Contact.get_test_contact(self.admin)
        self.send_message(favorites, 'green', contact=kobe)
        self.send_message(favorites, 'skol', contact=kobe)

        self.login(self.admin)
        response = self.client.get(reverse('flows.flow_results', args=[favorites.pk]))

        # the rulesets should be present as column headers
        self.assertContains(response, 'Beer')
        self.assertContains(response, 'Color')
        self.assertContains(response, 'Name')

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

        next_link = re.search('ic-append-from=\"(.*)\" ic-trigger-on', response.content).group(1)
        response = self.client.get(next_link)
        self.assertEqual(200, response.status_code)

        # one more row to add
        self.assertEqual(1, len(response.context['runs']))
        self.assertNotContains(response, "ic-append-from")

        FlowCRUDL.ActivityChart.HISTOGRAM_MIN = 0
        FlowCRUDL.ActivityChart.PERIOD_MIN = 0

        # and some charts
        response = self.client.get(reverse('flows.flow_activity_chart', args=[favorites.pk]))

        # we have two active runs
        self.assertContains(response, "{ name: 'Active', y: 2 }")
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
        FlowCRUDL.RunTable.paginate_by = 100
        response = self.client.get(reverse('flows.flow_run_table', args=[favorites.pk]))
        self.assertEqual(len(response.context['runs']), 2)

        rulesets = favorites.rule_sets.all().order_by('-y')
        results0 = Value.get_value_summary(ruleset=rulesets[0])[0]
        results1 = Value.get_value_summary(ruleset=rulesets[1])[0]
        results2 = Value.get_value_summary(ruleset=rulesets[2])[0]

        self.assertEqual(results0['set'], 1)
        self.assertEqual(results0['unset'], 1)
        self.assertEqual(len(results0['categories']), 1)
        self.assertEqual(results0['categories'], [{'count': 1, 'label': u'pete'}])

        self.assertEqual(results1['set'], 2)
        self.assertEqual(results1['unset'], 0)
        self.assertEqual(len(results1['categories']), 4)
        self.assertEqual(results1['categories'], [{'count': 0, 'label': u'Mutzig'}, {'count': 1, 'label': u'Primus'},
                                                  {'count': 1, 'label': u'Turbo King'}, {'count': 0, 'label': u'Skol'}])

        self.assertEqual(results2['set'], 2)
        self.assertEqual(results2['unset'], 0)
        self.assertEqual(len(results2['categories']), 4)
        self.assertEqual(results2['categories'], [{'count': 1, 'label': u'Red'}, {'count': 0, 'label': u'Green'},
                                                  {'count': 1, 'label': u'Blue'}, {'count': 0, 'label': u'Cyan'}])

        self.client.post(reverse('flows.flowrun_delete', args=[response.context['runs'][0].id]))
        response = self.client.get(reverse('flows.flow_run_table', args=[favorites.pk]))
        self.assertEqual(len(response.context['runs']), 1)

        results0 = Value.get_value_summary(ruleset=rulesets[0])[0]
        results1 = Value.get_value_summary(ruleset=rulesets[1])[0]
        results2 = Value.get_value_summary(ruleset=rulesets[2])[0]

        self.assertEqual(results0['set'], 0)
        self.assertEqual(results0['unset'], 1)
        self.assertEqual(len(results0['categories']), 0)
        self.assertEqual(results0['categories'], [])

        self.assertEqual(results1['set'], 1)
        self.assertEqual(results1['unset'], 0)
        self.assertEqual(len(results1['categories']), 4)
        self.assertEqual(results1['categories'], [{'count': 0, 'label': u'Mutzig'}, {'count': 0, 'label': u'Primus'},
                                                  {'count': 1, 'label': u'Turbo King'}, {'count': 0, 'label': u'Skol'}])

        self.assertEqual(results2['set'], 1)
        self.assertEqual(results2['unset'], 0)
        self.assertEqual(len(results2['categories']), 4)
        self.assertEqual(results2['categories'], [{'count': 1, 'label': u'Red'}, {'count': 0, 'label': u'Green'},
                                                  {'count': 0, 'label': u'Blue'}, {'count': 0, 'label': u'Cyan'}])

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

    def test_get_columns_order(self):
        flow = self.get_flow('columns_order')

        export_columns = flow.get_columns()
        self.assertEquals(export_columns[0], RuleSet.objects.filter(flow=flow, label='Beer').first())
        self.assertEquals(export_columns[1], RuleSet.objects.filter(flow=flow, label='Name').first())
        self.assertEquals(export_columns[2], RuleSet.objects.filter(flow=flow, label='Color').first())

    def test_recent_messages(self):
        flow = self.get_flow('favorites')

        self.login(self.admin)
        recent_messages_url = reverse('flows.flow_recent_messages', args=[flow.pk])

        actionset = ActionSet.objects.filter(flow=flow, y=0).first()
        first_action_set_uuid = actionset.uuid
        first_action_set_destination = actionset.destination

        ruleset = RuleSet.objects.filter(flow=flow, label='Color').first()
        first_ruleset_uuid = ruleset.uuid

        other_rule = ruleset.get_rules()[-1]
        other_rule_destination = other_rule.destination
        other_rule_uuid = other_rule.uuid

        blue_rule = ruleset.get_rules()[-4]
        blue_rule_uuid = blue_rule.uuid
        blue_rule_destination = blue_rule.destination

        navy_rule = ruleset.get_rules()[-3]
        navy_rule_uuid = navy_rule.uuid

        # URL params for different flow path segments
        entry_params = "?step=%s&destination=%s&rule=" % (first_action_set_uuid, first_action_set_destination)
        other_params = "?step=%s&destination=%s&rule=%s" % (first_ruleset_uuid, other_rule_destination, other_rule_uuid)
        blue_params = "?step=%s&destination=%s&rule=%s,%s" % (first_ruleset_uuid, blue_rule_destination, blue_rule_uuid, navy_rule_uuid)
        invalid_params = "?step=%s&destination=%s&rule=" % (first_ruleset_uuid, first_action_set_destination)

        def assert_recent(resp, msgs):
            self.assertEqual([r['text'] for r in resp.json()], msgs)

        # no params returns no results
        assert_recent(self.client.get(recent_messages_url), [])

        self.send_message(flow, 'chartreuse')

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

        self.send_message(flow, 'mauve')
        msg1 = Msg.objects.filter(text='chartreuse').first()
        msg2 = Msg.objects.filter(text='mauve').first()

        response = self.client.get(recent_messages_url + entry_params)
        assert_recent(response, ["What is your favorite color?"])

        response = self.client.get(recent_messages_url + other_params)
        self.assertEqual(response.json(), [
            {'text': "mauve", 'sent': datetime_to_str(msg2.created_on, tz=self.org.timezone)},
            {'text': "chartreuse", 'sent': datetime_to_str(msg1.created_on, tz=self.org.timezone)}
        ])

        response = self.client.get(recent_messages_url + blue_params)
        assert_recent(response, [])

        self.send_message(flow, 'blue')

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
        self.assertEquals(FlowRun.objects.filter(is_active=True).count(), 0)
        self.assertEquals(FlowRun.objects.filter(is_active=False, exit_type='E').exclude(exited_on=None).count(), 6)
        self.assertEquals(len(active), 0)

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
        beer = RuleSet.objects.get(label='Beer', flow=flow)
        color = RuleSet.objects.get(label='Color', flow=flow)

        rules = color.get_rules()
        color_other_uuid = rules[-1].uuid
        color_cyan_uuid = rules[-2].uuid
        color_blue_uuid = rules[-4].uuid

        other_rule_to_msg = '%s:%s' % (color_other_uuid, other_action.uuid)
        msg_to_color_step = '%s:%s' % (other_action.uuid, color.uuid)
        cyan_to_nothing = '%s:None' % (color_cyan_uuid)
        blue_to_beer = '%s:%s' % (color_blue_uuid, beer_question.uuid)

        # we don't know this shade of green, it should route us to the beginning again
        self.send_message(flow, 'chartreuse')
        (active, visited) = flow.get_activity()

        self.assertEquals(1, len(active))
        self.assertEquals(1, active[color.uuid])
        self.assertEquals(1, visited[other_rule_to_msg])
        self.assertEquals(1, visited[msg_to_color_step])
        self.assertEqual(flow.get_run_stats(),
                         {'total': 1, 'active': 1, 'completed': 0, 'expired': 0, 'interrupted': 0, 'completion': 0})

        # another unknown color, that'll route us right back again
        # the active stats will look the same, but there should be one more journey on the path
        self.send_message(flow, 'mauve')
        (active, visited) = flow.get_activity()
        self.assertEquals(1, len(active))
        self.assertEquals(1, active[color.uuid])
        self.assertEquals(2, visited[other_rule_to_msg])
        self.assertEquals(2, visited[msg_to_color_step])

        # this time a color we know takes us elsewhere, activity will move
        # to another node, but still just one entry
        self.send_message(flow, 'blue')
        (active, visited) = flow.get_activity()
        self.assertEquals(1, len(active))
        self.assertEquals(1, active[beer.uuid])

        # check recent messages
        recent = FlowPathRecentMessage.get_recent([color_question.uuid], [color.uuid])
        self.assertEqual([m.text for m in recent], ["What is your favorite color?"])

        recent = FlowPathRecentMessage.get_recent([color_other_uuid], [other_action.uuid])
        self.assertEqual([m.text for m in recent], ["mauve", "chartreuse"])

        recent = FlowPathRecentMessage.get_recent([other_action.uuid], [color.uuid])
        self.assertEqual([m.text for m in recent], ["I don't know that color. Try again.", "I don't know that color. Try again."])

        recent = FlowPathRecentMessage.get_recent([color_blue_uuid], [beer_question.uuid])
        self.assertEqual([m.text for m in recent], ["blue"])

        # a new participant, showing distinct active counts and incremented path
        ryan = self.create_contact('Ryan Lewis', '+12065550725')
        self.send_message(flow, 'burnt sienna', contact=ryan)
        (active, visited) = flow.get_activity()
        self.assertEquals(2, len(active))
        self.assertEquals(1, active[color.uuid])
        self.assertEquals(1, active[beer.uuid])
        self.assertEquals(3, visited[other_rule_to_msg])
        self.assertEquals(3, visited[msg_to_color_step])
        self.assertEqual(flow.get_run_stats(),
                         {'total': 2, 'active': 2, 'completed': 0, 'expired': 0, 'interrupted': 0, 'completion': 0})

        # now let's have them land in the same place
        self.send_message(flow, 'blue', contact=ryan)
        (active, visited) = flow.get_activity()
        self.assertEquals(1, len(active))
        self.assertEquals(2, active[beer.uuid])

        # now move our first contact forward to the end, both out of the flow now
        self.send_message(flow, 'Turbo King')
        self.send_message(flow, 'Ben Haggerty')
        (active, visited) = flow.get_activity()
        self.assertEquals(1, len(active))

        # half of our flows are now complete
        self.assertEqual(flow.get_run_stats(),
                         {'total': 2, 'active': 1, 'completed': 1, 'expired': 0, 'interrupted': 0, 'completion': 50})

        # we are going to expire, but we want runs across two different flows
        # to make sure that our optimization for expiration is working properly
        cga_flow = self.get_flow('color_gender_age')
        self.assertEquals("What is your gender?", self.send_message(cga_flow, "Red"))
        self.assertEquals(1, len(cga_flow.get_activity()[0]))

        # expire the first contact's runs
        FlowRun.bulk_exit(FlowRun.objects.filter(contact=self.contact), FlowRun.EXIT_TYPE_EXPIRED)

        # no active runs for our contact
        self.assertEquals(0, FlowRun.objects.filter(contact=self.contact, is_active=True).count())

        # both of our flows should have reduced active contacts
        self.assertEquals(0, len(cga_flow.get_activity()[0]))

        # now we should only have one node with active runs, but the paths stay
        # the same since those are historical
        (active, visited) = flow.get_activity()
        self.assertEquals(1, len(active))
        self.assertEquals(3, visited[other_rule_to_msg])

        # no completed runs but one expired run
        self.assertEqual(flow.get_run_stats(),
                         {'total': 2, 'active': 1, 'completed': 0, 'expired': 1, 'interrupted': 0, 'completion': 0})

        # check that we have the right number of steps and runs
        self.assertEquals(17, FlowStep.objects.filter(run__flow=flow).count())
        self.assertEquals(2, FlowRun.objects.filter(flow=flow).count())

        # now let's delete our contact, we'll still have one active node, but
        # our visit path counts will go down by two since he went there twice
        self.contact.release(self.user)
        (active, visited) = flow.get_activity()
        self.assertEquals(1, len(active))
        self.assertEquals(1, visited[msg_to_color_step])
        self.assertEquals(1, visited[other_rule_to_msg])

        # he was also accounting for our completion rate, back to nothing
        self.assertEqual(flow.get_run_stats(),
                         {'total': 1, 'active': 1, 'completed': 0, 'expired': 0, 'interrupted': 0, 'completion': 0})

        # advance ryan to the end to make sure our percentage accounts for one less contact
        self.send_message(flow, 'Turbo King', contact=ryan)
        self.send_message(flow, 'Ryan Lewis', contact=ryan)
        (active, visited) = flow.get_activity()
        self.assertEquals(0, len(active))
        self.assertEqual(flow.get_run_stats(),
                         {'total': 1, 'active': 0, 'completed': 1, 'expired': 0, 'interrupted': 0, 'completion': 100})

        # messages to/from deleted contacts shouldn't appear in the recent messages
        recent = FlowPathRecentMessage.get_recent([color_other_uuid], [other_action.uuid])
        self.assertEqual([m.text for m in recent], ["burnt sienna"])

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
        self.assertEquals(0, len(active))
        self.assertEquals(1, visited[msg_to_color_step])
        self.assertEquals(1, visited[other_rule_to_msg])
        self.assertEqual(flow.get_run_stats(),
                         {'total': 1, 'active': 0, 'completed': 1, 'expired': 0, 'interrupted': 0, 'completion': 100})

        # and no recent message entries for this test contact
        recent = FlowPathRecentMessage.get_recent([color_other_uuid], [other_action.uuid])
        self.assertEqual([m.text for m in recent], ["burnt sienna"])

        # try the same thing after squashing
        squash_flowpathcounts()
        visited = flow.get_activity()[1]
        self.assertEquals(1, visited[msg_to_color_step])
        self.assertEquals(1, visited[other_rule_to_msg])

        # but hammer should have created some simulation activity
        (active, visited) = flow.get_activity(simulation=True)
        self.assertEquals(0, len(active))
        self.assertEquals(2, visited[msg_to_color_step])
        self.assertEquals(2, visited[other_rule_to_msg])

        # delete our last contact to make sure activity is gone without first expiring, zeros abound
        ryan.release(self.admin)
        (active, visited) = flow.get_activity()
        self.assertEquals(0, len(active))
        self.assertEquals(0, visited[msg_to_color_step])
        self.assertEquals(0, visited[other_rule_to_msg])

        self.assertEqual(flow.get_run_stats(),
                         {'total': 0, 'active': 0, 'completed': 0, 'expired': 0, 'interrupted': 0, 'completion': 0})

        # runs and steps all gone too
        self.assertEquals(0, FlowStep.objects.filter(run__flow=flow, contact__is_test=False).count())
        self.assertEquals(0, FlowRun.objects.filter(flow=flow, contact__is_test=False).count())

        # test that expirations remove activity when triggered from the cron in the same way
        tupac = self.create_contact('Tupac Shakur', '+12065550725')
        self.send_message(flow, 'azul', contact=tupac)
        (active, visited) = flow.get_activity()
        self.assertEquals(1, len(active))
        self.assertEquals(1, active[color.uuid])
        self.assertEquals(1, visited[other_rule_to_msg])
        self.assertEquals(1, visited[msg_to_color_step])
        self.assertEqual(flow.get_run_stats(),
                         {'total': 1, 'active': 1, 'completed': 0, 'expired': 0, 'interrupted': 0, 'completion': 0})

        # set the run to be ready for expiration
        run = tupac.runs.first()
        run.expires_on = timezone.now() - timedelta(days=1)
        run.save()

        # now trigger the checking task and make sure it is removed from our activity
        from .tasks import check_flows_task
        check_flows_task()
        (active, visited) = flow.get_activity()
        self.assertEquals(0, len(active))
        self.assertEqual(flow.get_run_stats(),
                         {'total': 1, 'active': 0, 'completed': 0, 'expired': 1, 'interrupted': 0, 'completion': 0})

        # choose a rule that is not wired up (end of flow)
        jimmy = self.create_contact('Jimmy Graham', '+12065558888')
        self.send_message(flow, 'cyan', contact=jimmy, assert_reply=False)

        tyler = self.create_contact('Tyler Lockett', '+12065559999')
        self.send_message(flow, 'cyan', contact=tyler, assert_reply=False)

        # we should have 2 counts of the cyan rule to nothing
        self.assertEqual(2, flow.get_segment_counts(simulation=False, include_incomplete=True)[cyan_to_nothing])
        self.assertEqual(2, FlowPathCount.objects.filter(from_uuid=color_cyan_uuid).count())

        # squash our counts and make sure they are still the same
        squash_flowpathcounts()
        self.assertEqual(2, flow.get_segment_counts(simulation=False, include_incomplete=True)[cyan_to_nothing])

        # but now we have a single count
        self.assertEqual(1, FlowPathCount.objects.filter(from_uuid=color_cyan_uuid).count())

        counts = len(flow.get_segment_counts(False))

        # check that flow interruption counts properly
        rawls = self.create_contact('Thomas Rawls', '+12065557777')
        self.send_message(flow, 'blue', contact=rawls)

        # but he's got other things on his mind
        random_word = self.get_flow('random_word')
        self.send_message(random_word, 'blerg', contact=rawls)

        # here's our count for our response path
        self.assertEqual(1, flow.get_segment_counts(False)[blue_to_beer])

        # let's also create a flow run that gets expired
        pete = self.create_contact('Pete', '+12065554444')
        self.send_message(flow, 'blue', contact=pete)
        run = FlowRun.objects.filter(contact=pete).first()
        run.expire()

        # but there should be no additional records due to the interruption or expiration
        # ie, there are no counts added with respect to the next question
        self.assertEqual(counts, len(flow.get_segment_counts(False)))

        # ensure no negative counts
        for k, v in flow.get_segment_counts(False).items():
            self.assertTrue(v >= 0)

    def test_prune_recentmessages(self):
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
        other_recent = FlowPathRecentMessage.objects.filter(from_uuid=other_rule.uuid, to_uuid=other_action.uuid)
        self.assertEqual(len(other_recent), 12)

        # and these are returned with most-recent first
        other_recent = FlowPathRecentMessage.get_recent([other_rule.uuid], [other_action.uuid], limit=None)
        self.assertEqual([m.text for m in other_recent], ["12", "11", "10", "9", "8", "7", "6", "5", "4", "3", "2", "1"])

        # even when limit is applied
        other_recent = FlowPathRecentMessage.get_recent([other_rule.uuid], [other_action.uuid], limit=5)
        self.assertEqual([m.text for m in other_recent], ["12", "11", "10", "9", "8"])

        prune_recentmessages()

        # now only 5 newest are stored
        other_recent = FlowPathRecentMessage.objects.filter(from_uuid=other_rule.uuid, to_uuid=other_action.uuid)
        self.assertEqual(len(other_recent), 5)

        other_recent = FlowPathRecentMessage.get_recent([other_rule.uuid], [other_action.uuid])
        self.assertEqual([m.text for m in other_recent], ["12", "11", "10", "9", "8"])

        # send another message and prune again
        self.send_message(flow, "13", contact=bob)
        prune_recentmessages()

        other_recent = FlowPathRecentMessage.get_recent([other_rule.uuid], [other_action.uuid])
        self.assertEqual([m.text for m in other_recent], ["13", "12", "11", "10", "9"])

    def test_destination_type(self):
        flow = self.get_flow('pick_a_number')

        # our start points to a ruleset
        start = ActionSet.objects.get(flow=flow, y=0)

        # assert our destination
        self.assertEquals(FlowStep.TYPE_RULE_SET, start.destination_type)

        # and that ruleset points to an actionset
        ruleset = RuleSet.objects.get(uuid=start.destination)
        rule = ruleset.get_rules()[0]
        self.assertEquals(FlowStep.TYPE_ACTION_SET, rule.destination_type)

        # point our rule to a ruleset
        passive = RuleSet.objects.get(flow=flow, label='passive')
        self.update_destination(flow, rule.uuid, passive.uuid)
        ruleset = RuleSet.objects.get(uuid=start.destination)
        self.assertEquals(FlowStep.TYPE_RULE_SET, ruleset.get_rules()[0].destination_type)

    def test_orphaned_action_to_action(self):
        """
        Orphaned at an action, then routed to an action
        """

        # run a flow that ends on an action
        flow = self.get_flow('pick_a_number')
        self.assertEquals("You picked 3!", self.send_message(flow, "3"))

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

        self.assertEquals("You picked 6!", self.send_message(flow, "6"))

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
        self.assertEquals("You picked 6!", self.send_message(flow, "6"))

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
        self.assertEquals("You picked 3!", self.send_message(flow, "3"))

    def test_rules_first(self):
        flow = self.get_flow('rules_first')
        self.assertEquals(Flow.RULES_ENTRY, flow.entry_type)
        self.assertEquals("You've got to be kitten me", self.send_message(flow, "cats"))

    def test_numeric_rule_allows_variables(self):
        flow = self.get_flow('numeric_rule_allows_variables')

        zinedine = self.create_contact('Zinedine', '+123456')
        zinedine.set_field(self.user, 'age', 25)

        self.assertEquals('Good count', self.send_message(flow, "35", contact=zinedine))

    def test_non_blocking_rule_first(self):

        flow = self.get_flow('non_blocking_rule_first')

        eminem = self.create_contact('Eminem', '+12345')
        flow.start(groups=[], contacts=[eminem])
        msg = Msg.objects.filter(direction='O', contact=eminem).first()
        self.assertEquals('Hi there Eminem', msg.text)

        # put a webhook on the rule first and make sure it executes
        ruleset = RuleSet.objects.get(uuid=flow.entry_uuid)
        ruleset.webhook_url = 'http://localhost'
        ruleset.save()

        tupac = self.create_contact('Tupac', '+15432')
        flow.start(groups=[], contacts=[tupac])
        msg = Msg.objects.filter(direction='O', contact=tupac).first()
        self.assertEquals('Hi there Tupac', msg.text)

    def test_webhook_rule_first(self):

        flow = self.get_flow('webhook_rule_first')
        tupac = self.create_contact('Tupac', '+15432')
        flow.start(groups=[], contacts=[tupac])

        # a message should have been sent
        msg = Msg.objects.filter(direction='O', contact=tupac).first()
        self.assertEquals('Testing this out', msg.text)

    def test_group_split(self):
        flow = self.get_flow('group_split')
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
        self.assertEquals(1, len(runs))
        self.assertEquals(1, self.contact.msgs.all().count())
        self.assertEquals('Hey', self.contact.msgs.all()[0].text)
        self.assertEquals(["image/jpeg:https://%s/%s" % (settings.AWS_BUCKET_DOMAIN, "attachments/2/53/steps/87d34837-491c-4541-98a1-fa75b52ebccc.jpg")],
                          self.contact.msgs.all()[0].attachments)

    def test_substitution(self):
        flow = self.get_flow('substitution')

        self.contact.name = "Ben Haggerty"
        self.contact.save()

        runs = flow.start_msg_flow([self.contact.id])
        self.assertEquals(1, len(runs))
        self.assertEquals(1, self.contact.msgs.all().count())
        self.assertEquals('Hi Ben Haggerty, what is your phone number?', self.contact.msgs.all()[0].text)

        self.assertEquals("Thanks, you typed +250788123123", self.send_message(flow, "0788123123"))
        sms = Msg.objects.get(org=flow.org, contact__urns__path="+250788123123")
        self.assertEquals("Hi from Ben Haggerty! Your phone is 0788 123 123.", sms.text)

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

        self.assertEquals("Enter the expected delivery date.", self.send_message(registration_flow, "Judy Pottier"))
        self.assertEquals("Great, thanks for registering the new mother", self.send_message(registration_flow, "31.1.2015"))

        mother = Contact.objects.get(org=self.org, name="Judy Pottier")
        self.assertTrue(mother.get_field_raw('edd').startswith('31-01-2015'))
        self.assertEquals(mother.get_field_raw('chw_phone'), self.contact.get_urn(TEL_SCHEME).path)
        self.assertEquals(mother.get_field_raw('chw_name'), self.contact.name)

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

        self.assertEquals("What is her expected delivery date?", self.send_message(registration_flow, "Judy Pottier"))
        self.assertEquals("What is her phone number?", self.send_message(registration_flow, "31.1.2014"))
        self.assertEquals("Great, you've registered the new mother!", self.send_message(registration_flow, "0788 383 383"))

        mother = Contact.from_urn(self.org, "tel:+250788383383")
        self.assertEquals("Judy Pottier", mother.name)
        self.assertTrue(mother.get_field_raw('expected_delivery_date').startswith('31-01-2014'))
        self.assertEquals("+12065552020", mother.get_field_raw('chw'))
        self.assertTrue(mother.user_groups.filter(name="Expecting Mothers"))

        pain_flow = self.get_flow('pain_flow')
        self.assertEquals("Your CHW will be in contact soon!", self.send_message(pain_flow, "yes", contact=mother))

        chw = self.contact
        sms = Msg.objects.filter(contact=chw).order_by('-created_on')[0]
        self.assertEquals("Please follow up with Judy Pottier, she has reported she is in pain.", sms.text)

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
        self.assertEquals("Good choice, I like Red too! What is your favorite beer?", self.send_message(flow, "RED"))

        # try to remove the flow, not logged in, no dice
        response = self.client.post(reverse('flows.flow_delete', args=[flow.pk]))
        self.assertLoginRedirect(response)

        # login as admin
        self.login(self.admin)

        # try again
        response = self.client.post(reverse('flows.flow_delete', args=[flow.pk]))
        self.assertRedirect(response, reverse('flows.flow_list'))

        # flow should no longer be active
        flow.refresh_from_db()
        self.assertFalse(flow.is_active)

        # should still have a run though
        self.assertEqual(flow.runs.count(), 1)

        # but they should all be inactive
        self.assertEqual(flow.runs.filter(is_active=True).count(), 0)

        # just no steps or values
        self.assertEqual(Value.objects.all().count(), 0)
        self.assertEqual(FlowStep.objects.all().count(), 0)

        # our campaign event should no longer be active
        event1.refresh_from_db()
        self.assertFalse(event1.is_active)

        # nor should our trigger
        trigger.refresh_from_db()
        self.assertFalse(trigger.is_active)

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
            self.assertEquals("Greg", Contact.objects.get(pk=contact.pk).name)

        # 10 of the runs should be completed (parent runs)
        self.assertEqual(FlowRun.objects.filter(flow=parent, is_active=False, exit_type=FlowRun.EXIT_TYPE_COMPLETED).count(), 10)

        # 10 should be active waiting for input
        self.assertEqual(FlowRun.objects.filter(flow=child, is_active=True).count(), 10)

    def test_cross_language_import(self):
        spanish = Language.create(self.org, self.admin, "Spanish", 'spa')
        Language.create(self.org, self.admin, "English", 'eng')

        # import our localized flow into an org with no languages
        self.import_file('multi_language_flow')
        flow = Flow.objects.get(name='Multi Language Flow')

        # even tho we don't have a language, our flow has enough info to function
        self.assertEquals('eng', flow.base_language)

        # now try executing this flow on our org, should use the flow base language
        self.assertEquals('Hello friend! What is your favorite color?',
                          self.send_message(flow, 'start flow', restart_participants=True, initiate_flow=True))

        replies = self.send_message(flow, 'blue')
        self.assertEquals('Thank you! I like blue.', replies[0])
        self.assertEquals('This message was not translated.', replies[1])

        # now add a primary language to our org
        self.org.primary_language = spanish
        self.org.save()

        flow = Flow.objects.get(pk=flow.pk)

        # with our org in spanish, we should get the spanish version
        self.assertEquals('\xa1Hola amigo! \xbfCu\xe1l es tu color favorito?',
                          self.send_message(flow, 'start flow', restart_participants=True, initiate_flow=True))

        self.org.primary_language = None
        self.org.save()
        flow = Flow.objects.get(pk=flow.pk)

        # no longer spanish on our org
        self.assertEquals('Hello friend! What is your favorite color?',
                          self.send_message(flow, 'start flow', restart_participants=True, initiate_flow=True))

        # back to spanish
        self.org.primary_language = spanish
        self.org.save()
        flow = Flow.objects.get(pk=flow.pk)

        # but set our contact's language explicitly should keep us at english
        self.contact.language = 'eng'
        self.contact.save()
        self.assertEquals('Hello friend! What is your favorite color?',
                          self.send_message(flow, 'start flow', restart_participants=True, initiate_flow=True))

    def test_different_expiration(self):
        flow = self.get_flow('favorites')
        self.send_message(flow, "RED", restart_participants=True)

        # get the latest run
        first_run = flow.runs.all()[0]
        first_expires = first_run.expires_on

        time.sleep(1)

        # start it again
        self.send_message(flow, "RED", restart_participants=True)

        # previous run should no longer be active
        first_run = FlowRun.objects.get(pk=first_run.pk)
        self.assertFalse(first_run.is_active)

        # expires on shouldn't have changed on it though
        self.assertEquals(first_expires, first_run.expires_on)

        # new run should have a different expires on
        new_run = flow.runs.all().order_by('-expires_on').first()
        self.assertTrue(new_run.expires_on > first_expires)

    def test_flow_expiration_updates(self):
        flow = self.get_flow('favorites')
        self.assertEquals("Good choice, I like Red too! What is your favorite beer?", self.send_message(flow, "RED"))

        # get our current expiration
        run = flow.runs.get()
        self.assertEquals(flow.org, run.org)

        starting_expiration = run.expires_on
        starting_modified = run.modified_on

        time.sleep(1)

        # now fire another messages
        self.assertEquals("Mmmmm... delicious Turbo King. If only they made red Turbo King! Lastly, what is your name?",
                          self.send_message(flow, "turbo"))

        # our new expiration should be later
        run.refresh_from_db()
        self.assertTrue(run.expires_on > starting_expiration)
        self.assertTrue(run.modified_on > starting_modified)

    def test_initial_expiration(self):
        flow = self.get_flow('favorites')
        flow.start(groups=[], contacts=[self.contact])

        run = FlowRun.objects.get()
        self.assertTrue(run.expires_on)

    def test_flow_expiration(self):
        flow = self.get_flow('favorites')
        self.assertEquals("Good choice, I like Red too! What is your favorite beer?", self.send_message(flow, "RED"))
        self.assertEquals("Mmmmm... delicious Turbo King. If only they made red Turbo King! Lastly, what is your name?", self.send_message(flow, "turbo"))
        self.assertEquals(1, flow.runs.count())

        # pretend our step happened 10 minutes ago
        step = FlowStep.objects.filter(run=flow.runs.all()[0], left_on=None)[0]
        step.arrived_on = timezone.now() - timedelta(minutes=10)
        step.save()

        # now let's expire them out of the flow prematurely
        flow.expires_after_minutes = 5
        flow.save()

        # this normally gets run on FlowCRUDL.Update
        update_run_expirations_task(flow.id)

        # check that our run is expired
        run = flow.runs.all()[0]
        self.assertFalse(run.is_active)

        # we will be starting a new run now, since the other expired
        self.assertEquals("I don't know that color. Try again.",
                          self.send_message(flow, "Michael Jordan", restart_participants=True))
        self.assertEquals(2, flow.runs.count())

        previous_expiration = run.expires_on
        run.update_expiration(None)
        self.assertTrue(run.expires_on > previous_expiration)

    def test_parsing(self):
        # test a preprocess url
        flow = self.get_flow('preprocess')
        self.assertEquals('http://preprocessor.com/endpoint.php', flow.rule_sets.all().order_by('y')[0].config_json()[RuleSet.CONFIG_WEBHOOK])

    def test_flow_loops(self):
        # this tests two flows that start each other
        flow1 = self.create_flow()
        flow2 = self.create_flow()

        # create an action on flow1 to start flow2
        flow1.update(dict(action_sets=[dict(uuid=str(uuid4()), x=1, y=1,
                                            actions=[dict(type='flow', flow=dict(uuid=flow2.uuid))])]))
        flow2.update(dict(action_sets=[dict(uuid=str(uuid4()), x=1, y=1,
                                            actions=[dict(type='flow', flow=dict(uuid=flow1.uuid))])]))

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

        self.assertEquals("Added to campaign.", self.send_message(parent, "start", initiate_flow=True))

        # should have one event scheduled for this contact
        self.assertTrue(EventFire.objects.filter(contact=self.contact))

    def test_subflow(self):
        """
        Tests that a subflow can be called and the flow is handed back to the parent
        """
        self.get_flow('subflow')
        parent = Flow.objects.get(org=self.org, name='Parent Flow')
        parent.start(groups=[], contacts=[self.contact], restart_participants=True)

        msg = Msg.objects.filter(contact=self.contact).first()
        self.assertEqual("This is a parent flow. What would you like to do?", msg.text)

        # this should launch the child flow
        self.send_message(parent, "color", assert_reply=False)
        msg = Msg.objects.filter(contact=self.contact).order_by('-created_on').first()

        subflow_ruleset = RuleSet.objects.filter(flow=parent, ruleset_type='subflow').first()

        # should have one step on the subflow ruleset
        self.assertEqual(1, FlowStep.objects.filter(step_uuid=subflow_ruleset.uuid).count())
        self.assertEqual("What color do you like?", msg.text)

        # we should now have two active flows
        self.assertEqual(2, FlowRun.objects.filter(contact=self.contact, is_active=True).count())

        # complete the child flow
        self.send('Red')

        # should still only have one step on our subflow ruleset
        self.assertEqual(1, FlowStep.objects.filter(step_uuid=subflow_ruleset.uuid).count())

        # now we are back to a single active flow, the parent
        self.assertEqual(1, FlowRun.objects.filter(contact=self.contact, is_active=True).count())
        active_run = FlowRun.objects.filter(contact=self.contact, is_active=True).first()
        self.assertEqual(parent.name, active_run.flow.name)

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
        self.assertEquals(1, len(runs))
        self.assertEquals(1, self.contact.msgs.all().count())
        self.assertEquals('You are not in the enrolled group.', self.contact.msgs.all()[0].text)

        enrolled_group = ContactGroup.create_static(self.org, self.user, "Enrolled")
        enrolled_group.update_contacts(self.user, [self.contact], True)

        runs_started = flow.start_msg_flow([self.contact.id])
        self.assertEquals(1, len(runs_started))
        self.assertEquals(2, self.contact.msgs.all().count())
        self.assertEquals('You are in the enrolled group.', self.contact.msgs.all().order_by('-pk')[0].text)

    def test_translations(self):

        favorites = self.get_flow('favorites')

        # create a new language on the org
        language = Language.create(self.org, favorites.created_by, "English", 'eng')

        # set it as our primary language
        self.org.primary_language = language
        self.org.save()

        # everything should work as normal with our flow
        self.assertEquals("What is your favorite color?", self.send_message(favorites, "favorites", initiate_flow=True))
        json_dict = favorites.as_json()
        reply = json_dict['action_sets'][0]['actions'][0]

        # we should be a normal unicode response
        self.assertTrue(isinstance(reply['msg'], dict))
        self.assertTrue(isinstance(reply['msg']['base'], six.text_type))

        # now our replies are language dicts
        json_dict = favorites.as_json()
        reply = json_dict['action_sets'][1]['actions'][0]
        self.assertEquals('Good choice, I like @flow.color.category too! What is your favorite beer?', reply['msg']['base'])

        # now interact with the flow and make sure we get an appropriate response
        FlowRun.objects.all().delete()

        self.assertEquals("What is your favorite color?", self.send_message(favorites, "favorites", initiate_flow=True))
        self.assertEquals("Good choice, I like Red too! What is your favorite beer?", self.send_message(favorites, "RED"))

        # now let's add a second language
        Language.create(self.org, favorites.created_by, "Klingon", 'kli')

        # update our initial message
        initial_message = json_dict['action_sets'][0]['actions'][0]
        initial_message['msg']['kli'] = 'Kikshtik derklop?'
        json_dict['action_sets'][0]['actions'][0] = initial_message

        # and the first response
        reply['msg']['kli'] = 'Katishklick Shnik @flow.color.category Errrrrrrrklop'
        json_dict['action_sets'][1]['actions'][0] = reply

        # save the changes
        self.assertEquals('success', favorites.update(json_dict, self.admin)['status'])

        # should get org primary language (english) since our contact has no preferred language
        FlowRun.objects.all().delete()
        self.assertEquals("What is your favorite color?", self.send_message(favorites, "favorite", initiate_flow=True))
        self.assertEquals("Good choice, I like Red too! What is your favorite beer?", self.send_message(favorites, "RED"))

        # now set our contact's preferred language to klingon
        FlowRun.objects.all().delete()
        self.contact.language = 'kli'
        self.contact.save()

        self.assertEquals("Kikshtik derklop?", self.send_message(favorites, "favorite", initiate_flow=True))
        self.assertEquals("Katishklick Shnik Red Errrrrrrrklop", self.send_message(favorites, "RED"))

        # we support localized rules and categories as well
        json_dict = favorites.as_json()
        rule = json_dict['rule_sets'][0]['rules'][0]
        self.assertTrue(isinstance(rule['test']['test'], dict))
        rule['test']['test']['kli'] = 'klerk'
        rule['category']['kli'] = 'Klerkistikloperopikshtop'
        json_dict['rule_sets'][0]['rules'][0] = rule
        self.assertEquals('success', favorites.update(json_dict, self.admin)['status'])

        FlowRun.objects.all().delete()
        self.assertEquals("Katishklick Shnik Klerkistikloperopikshtop Errrrrrrrklop", self.send_message(favorites, "klerk"))

        # test the send action as well
        json_dict = favorites.as_json()
        action = json_dict['action_sets'][1]['actions'][0]
        action['type'] = 'send'
        action['contacts'] = [dict(uuid=self.contact.uuid)]
        action['groups'] = []
        action['variables'] = []
        json_dict['action_sets'][1]['actions'][0] = action
        self.assertEquals('success', favorites.update(json_dict, self.admin)['status'])

        FlowRun.objects.all().delete()
        self.send_message(favorites, "klerk", assert_reply=False)
        sms = Msg.objects.filter(contact=self.contact).order_by('-pk')[0]
        self.assertEquals("Katishklick Shnik Klerkistikloperopikshtop Errrrrrrrklop", sms.text)

        # test dirty json
        json_dict = favorites.as_json()

        # boolean values in our language dict shouldn't blow up
        json_dict['action_sets'][0]['actions'][0]['msg']['updated'] = True
        json_dict['action_sets'][0]['actions'][0]['msg']['kli'] = 'Bleck'

        # boolean values in our rule dict shouldn't blow up
        rule = json_dict['rule_sets'][0]['rules'][0]
        rule['category']['updated'] = True

        response = favorites.update(json_dict)
        self.assertEquals('success', response['status'])

        favorites = Flow.objects.get(pk=favorites.pk)
        json_dict = favorites.as_json()
        action = self.assertEquals('Bleck', json_dict['action_sets'][0]['actions'][0]['msg']['kli'])

        # test that simulation takes language into account
        self.login(self.admin)
        simulate_url = reverse('flows.flow_simulate', args=[favorites.pk])
        response = json.loads(self.client.post(simulate_url, json.dumps(dict(has_refresh=True)), content_type="application/json").content)
        self.assertEquals('What is your favorite color?', response['messages'][1]['text'])

        # now lets toggle the UI to Klingon and try the same thing
        simulate_url = "%s?lang=kli" % reverse('flows.flow_simulate', args=[favorites.pk])
        response = json.loads(self.client.post(simulate_url, json.dumps(dict(has_refresh=True)), content_type="application/json").content)
        self.assertEquals('Bleck', response['messages'][1]['text'])

    def test_interrupted_state(self):
        self.channel.delete()
        # Create a USSD channel type to test USSDSession.INTERRUPTED status
        self.channel = Channel.create(self.org, self.user, 'RW', Channel.TYPE_JUNEBUG_USSD, None, '+250788123123',
                                      role=Channel.ROLE_USSD)

        flow = self.get_flow('ussd_interrupt_example')

        # start the flow, check if we are interrupted yet
        flow.start([], [self.contact])
        self.assertFalse(FlowRun.objects.get(contact=self.contact).is_interrupted())

        USSDSession.handle_incoming(channel=self.channel, urn=self.contact.get_urn().path, date=timezone.now(),
                                    external_id="12341231", status=USSDSession.INTERRUPTED)

        self.assertTrue(FlowRun.objects.get(contact=self.contact).is_interrupted())

        # the contact should have been added to the "Interrupted" group as flow step describes
        contact = flow.get_results()[0]['contact']
        interrupted_group = ContactGroup.user_groups.get(name='Interrupted')
        self.assertTrue(interrupted_group.contacts.filter(id=contact.id).exists())

    def test_empty_interrupt_state(self):
        self.channel.delete()
        # Create a USSD channel type to test USSDSession.INTERRUPTED status
        self.channel = Channel.create(self.org, self.user, 'RW', Channel.TYPE_JUNEBUG_USSD, None, '+250788123123',
                                      role=Channel.ROLE_USSD)

        flow = self.get_flow('ussd_interrupt_example')

        # disconnect action from interrupt state
        ruleset = flow.rule_sets.first()
        rules = ruleset.get_rules()
        interrupt_rule = filter(lambda rule: isinstance(rule.test, InterruptTest), rules)[0]
        interrupt_rule.destination = None
        interrupt_rule.destination_type = None
        ruleset.set_rules(rules)
        ruleset.save()

        # start the flow, check if we are interrupted yet
        flow.start([], [self.contact])

        self.assertFalse(FlowRun.objects.get(contact=self.contact).is_interrupted())

        USSDSession.handle_incoming(channel=self.channel, urn=self.contact.get_urn().path, date=timezone.now(),
                                    external_id="12341231", status=USSDSession.INTERRUPTED)

        # the interrupt state is empty, it should interrupt the flow
        self.assertTrue(FlowRun.objects.get(contact=self.contact).is_interrupted())

        # double check that the disconnected action wasn't run
        contact = flow.get_results()[0]['contact']
        interrupted_group = ContactGroup.user_groups.get(name='Interrupted')
        self.assertFalse(interrupted_group.contacts.filter(id=contact.id).exists())

    def test_airtime_flow(self):
        flow = self.get_flow('airtime')

        contact_urn = self.contact.get_urn(TEL_SCHEME)

        airtime_event = AirtimeTransfer.objects.create(org=self.org, status=AirtimeTransfer.SUCCESS, amount=10, contact=self.contact,
                                                       recipient=contact_urn.path, created_by=self.admin, modified_by=self.admin)

        with patch('temba.flows.models.AirtimeTransfer.trigger_airtime_event') as mock_trigger_event:
            mock_trigger_event.return_value = airtime_event

            runs = flow.start_msg_flow([self.contact.id])
            self.assertEquals(1, len(runs))
            self.assertEquals(1, self.contact.msgs.all().count())
            self.assertEquals('Message complete', self.contact.msgs.all()[0].text)

            airtime_event.status = AirtimeTransfer.FAILED
            airtime_event.save()

            mock_trigger_event.return_value = airtime_event

            runs = flow.start_msg_flow([self.contact.id])
            self.assertEquals(1, len(runs))
            self.assertEquals(2, self.contact.msgs.all().count())
            self.assertEquals('Message failed', self.contact.msgs.all()[0].text)

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
        self.assertEquals(1, len(runs))
        self.assertEquals(1, self.contact.msgs.all().count())
        self.assertEquals('Message complete', self.contact.msgs.all()[0].text)

        self.assertEquals(1, AirtimeTransfer.objects.all().count())
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
        self.assertEquals(1, len(runs))
        self.assertEquals(2, self.contact.msgs.all().count())
        self.assertEquals('Message failed', self.contact.msgs.all()[0].text)

        self.assertEquals(2, AirtimeTransfer.objects.all().count())
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
        self.assertEquals(1, len(runs))

        # no saved airtime event in DB
        self.assertEquals(2, AirtimeTransfer.objects.all().count())
        self.assertEqual(mock_post_transferto.call_count, 0)

        contact2 = self.create_contact(name='Bismack Biyombo', number='+250788123123', twitter='biyombo')
        self.assertEqual(contact2.get_urn().path, 'biyombo')

        runs = flow.start_msg_flow([contact2.id])
        self.assertEquals(1, len(runs))
        self.assertEquals(1, contact2.msgs.all().count())
        self.assertEquals('Message complete', contact2.msgs.all()[0].text)

        self.assertEquals(3, AirtimeTransfer.objects.all().count())
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
        self.assertEquals(1, len(runs))
        self.assertEquals(3, self.contact.msgs.all().count())
        self.assertEquals('Message failed', self.contact.msgs.all()[0].text)

        self.assertEquals(4, AirtimeTransfer.objects.all().count())
        airtime = AirtimeTransfer.objects.all().last()
        self.assertEqual(airtime.status, AirtimeTransfer.FAILED)
        self.assertEqual(airtime.contact, self.contact)
        self.assertEqual(airtime.message, "Error transferring airtime: No transferTo Account connected to "
                                          "this organization")

        # we never call TransferTo API if no accoutnis connected
        self.assertEqual(mock_post_transferto.call_count, 0)
        mock_post_transferto.reset_mock()


class FlowMigrationTest(FlowFileTest):

    def migrate_flow(self, flow, to_version=None):

        if not to_version:
            to_version = CURRENT_EXPORT_VERSION

        flow_json = flow.as_json()
        if flow.version_number <= 6:
            revision = flow.revisions.all().order_by('-revision').first()
            flow_json = dict(definition=flow_json, flow_type=flow.flow_type,
                             expires=flow.expires_after_minutes, id=flow.pk,
                             revision=revision.revision if revision else 1)

        flow_json = FlowRevision.migrate_definition(flow_json, flow, flow.version_number, to_version=to_version)
        if 'definition' in flow_json:
            flow_json = flow_json['definition']

        flow.update(flow_json)
        return Flow.objects.get(pk=flow.pk)

    def test_migrate_with_flow_user(self):
        flow = Flow.create_instance(dict(name='Favorites', org=self.org,
                                         created_by=self.admin, modified_by=self.admin,
                                         saved_by=self.admin, version_number=7))

        flow_json = self.get_flow_json('favorites')
        FlowRevision.create_instance(dict(flow=flow, definition=json.dumps(flow_json),
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
        response = self.client.get(reverse('flows.flow_revisions', args=[flow.pk]))
        self.assertContains(response, 'System Update')
        self.assertEqual(2, len(response.json()))

        # attempt to save with old json, no bueno
        failed = flow.update(old_json, user=self.admin)
        self.assertEqual('unsaved', failed.get('status'))
        self.assertEqual('System Update', failed.get('saved_by'))

        # now refresh and save a new version
        flow.update(flow.as_json(), user=self.admin)
        self.assertEqual(3, flow.revisions.all().count())
        self.assertEqual(1, flow.revisions.filter(created_by=get_flow_user(self.org)).count())

    def test_migrate_malformed_single_message_flow(self):

        flow = Flow.create_instance(dict(name='Single Message Flow', org=self.org,
                                         created_by=self.admin, modified_by=self.admin,
                                         saved_by=self.admin, version_number=3))

        flow_json = self.get_flow_json('malformed_single_message')['definition']

        FlowRevision.create_instance(dict(flow=flow, definition=json.dumps(flow_json),
                                          spec_version=3, revision=1,
                                          created_by=self.admin, modified_by=self.admin))

        flow.ensure_current_version()
        flow_json = flow.as_json()

        self.assertEqual(len(flow_json['action_sets']), 1)
        self.assertEqual(len(flow_json['rule_sets']), 0)
        self.assertEqual(flow_json['version'], CURRENT_EXPORT_VERSION)
        self.assertEqual(flow_json['metadata']['revision'], 2)

    def test_migration_string_group(self):
        flow = Flow.create_instance(dict(name='String group', org=self.org,
                                         created_by=self.admin, modified_by=self.admin,
                                         saved_by=self.admin, version_number=3))

        flow_json = self.get_flow_json('string_group')['definition']

        FlowRevision.create_instance(dict(flow=flow, definition=json.dumps(flow_json),
                                          spec_version=3, revision=1,
                                          created_by=self.admin, modified_by=self.admin))

        flow.ensure_current_version()
        flow_json = flow.as_json()

        self.assertEqual(len(flow_json['action_sets']), 1)
        self.assertEqual("The Funky Bunch", flow_json['action_sets'][0]['actions'][0]['groups'][0]['name'])
        self.assertTrue("The Funky Bunch", flow_json['action_sets'][0]['actions'][0]['groups'][0]['uuid'])
        self.assertEqual("@contact.name", flow_json['action_sets'][0]['actions'][0]['groups'][1])

    def test_ensure_current_version(self):
        flow_json = self.get_flow_json('call_me_maybe')['definition']
        flow = Flow.create_instance(dict(name='Call Me Maybe', org=self.org,
                                         created_by=self.admin, modified_by=self.admin,
                                         saved_by=self.admin, version_number=3))

        FlowRevision.create_instance(dict(flow=flow, definition=json.dumps(flow_json),
                                          spec_version=3, revision=1,
                                          created_by=self.admin, modified_by=self.admin))

        # now make sure we are on the latest version
        flow.ensure_current_version()

        # and that the format looks correct
        flow_json = flow.as_json()
        self.assertEquals(flow_json['metadata']['name'], 'Call Me Maybe')
        self.assertEquals(flow_json['metadata']['revision'], 2)
        self.assertEquals(flow_json['metadata']['expires'], 720)
        self.assertEquals(flow_json['base_language'], 'base')
        self.assertEquals(5, len(flow_json['action_sets']))
        self.assertEquals(1, len(flow_json['rule_sets']))

    @override_settings(SEND_WEBHOOKS=True)
    def test_migrate_to_10(self):
        # this is really just testing our rewriting of webhook rulesets
        webhook_flow = self.get_flow('dual_webhook')

        self.assertNotEqual(webhook_flow.modified_on, webhook_flow.saved_on)

        # get our definition out
        flow_def = webhook_flow.as_json()

        # make sure our rulesets no longer have 'webhook' or 'webhook_action'
        for ruleset in flow_def['rule_sets']:
            self.assertFalse('webhook' in ruleset)
            self.assertFalse('webhook_action' in ruleset)

        with patch('requests.post') as mock_post:
            mock_post.return_value = MockResponse(200, '{ "code": "ABABUUDDLRS" }')

            webhook_flow.start([], [self.contact])
            self.assertEqual(mock_post.call_args[0][0], 'http://foo.bar/')

            # assert the code we received was right
            msg = Msg.objects.filter(direction='O', contact=self.contact).first()
            self.assertEqual("Great, your code is ABABUUDDLRS. Enter your name", msg.text)

            with patch('requests.get') as mock_get:
                mock_get.return_value = MockResponse(400, "Error")
                self.send_message(webhook_flow, "Ryan Lewis", assert_reply=False)
                self.assertEqual(mock_get.call_args[0][0], 'http://bar.foo/')

        # startover have our first webhook fail, check that routing still works with failure
        with patch('requests.post') as mock_post:
            mock_post.return_value = MockResponse(400, 'Error')

            webhook_flow.start([], [self.contact], restart_participants=True)
            self.assertEqual(mock_post.call_args[0][0], 'http://foo.bar/')

            # assert the code we received was right
            msg = Msg.objects.filter(direction='O', contact=self.contact).first()
            self.assertEqual("Great, your code is @extra.code. Enter your name", msg.text)

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
        self.assertEquals(1, len(send_action['contacts']))
        self.assertEquals(1, len(send_action['groups']))

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

        flow = Flow.objects.create(name='test flow', created_by=self.admin, modified_by=self.admin, org=self.org, saved_by=self.admin)
        flow.update(exported_json)

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
        self.assertTrue('uuid' in flow_json['metadata'])
        self.assertTrue('saved_on' in flow_json['metadata'])

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
        self.assertEquals('Call me maybe', flow_json.get('name'))
        self.assertEquals(100, flow_json.get('id'))
        self.assertEquals('V', flow_json.get('flow_type'))

        flow_json = migrate_to_version_7(flow_json)
        self.assertIsNone(flow_json.get('definition', None))
        self.assertIsNotNone(flow_json.get('metadata', None))

        metadata = flow_json.get('metadata')
        self.assertEquals('Call me maybe', metadata['name'])
        self.assertEquals(100, metadata['id'])
        self.assertEquals('V', flow_json.get('flow_type'))

    def test_migrate_to_6(self):

        # file format is old non-localized format
        voice_json = self.get_flow_json('call_me_maybe')
        definition = voice_json.get('definition')

        # no language set
        self.assertIsNone(definition.get('base_language', None))
        self.assertEquals('Yes', definition['rule_sets'][0]['rules'][0]['category'])
        self.assertEquals('Press one, two, or three. Thanks.', definition['action_sets'][0]['actions'][0]['msg'])

        # add a recording to make sure that gets migrated properly too
        definition['action_sets'][0]['actions'][0]['recording'] = '/recording.mp3'

        voice_json = migrate_to_version_5(voice_json)
        voice_json = migrate_to_version_6(voice_json)
        definition = voice_json.get('definition')

        # now we should have a language
        self.assertEquals('base', definition.get('base_language', None))
        self.assertEquals('Yes', definition['rule_sets'][0]['rules'][0]['category']['base'])
        self.assertEquals('Press one, two, or three. Thanks.', definition['action_sets'][0]['actions'][0]['msg']['base'])
        self.assertEquals('/recording.mp3', definition['action_sets'][0]['actions'][0]['recording']['base'])

        # now try one that doesn't have a recording set
        voice_json = self.get_flow_json('call_me_maybe')
        definition = voice_json.get('definition')
        del definition['action_sets'][0]['actions'][0]['recording']
        voice_json = migrate_to_version_5(voice_json)
        voice_json = migrate_to_version_6(voice_json)
        definition = voice_json.get('definition')
        self.assertTrue('recording' not in definition['action_sets'][0]['actions'][0])

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

        self.assertEquals(1, len(rules))
        self.assertEquals('All Responses', rules[0]['category']['eng'])
        self.assertEquals('Otro', rules[0]['category']['spa'])

    @override_settings(SEND_WEBHOOKS=True)
    def test_migrate_to_5(self):
        flow = self.get_flow('favorites')

        # start the flow for our contact
        flow.start(groups=[], contacts=[self.contact])

        # we should be sitting at the ruleset waiting for a message
        step = FlowStep.objects.get(run__flow=flow, step_type='R')
        ruleset = RuleSet.objects.get(uuid=step.step_uuid)
        self.assertEquals('wait_message', ruleset.ruleset_type)

        # fake a version 4 flow
        RuleSet.objects.filter(flow=flow).update(response_type='C', ruleset_type=None)
        flow.version_number = 4
        flow.save()

        # pretend our current ruleset was stopped at a webhook with a passive rule
        ruleset = RuleSet.objects.get(flow=flow, uuid=step.step_uuid)
        ruleset.webhook_url = 'http://www.mywebhook.com/lookup'
        ruleset.webhook_action = 'POST'
        ruleset.operand = '@extra.value'
        ruleset.save()

        # make beer use @step.value with a filter to test node creation
        beer_ruleset = RuleSet.objects.get(flow=flow, label='Beer')
        beer_ruleset.operand = '@step.value|lower_case'
        beer_ruleset.save()

        # now migrate our flow
        flow = self.migrate_flow(flow)

        # we should be sitting at a wait node
        ruleset = RuleSet.objects.get(uuid=step.step_uuid)
        self.assertEquals('wait_message', ruleset.ruleset_type)
        self.assertEquals('@step.value', ruleset.operand)

        # we should be pointing to a newly created webhook rule
        webhook = RuleSet.objects.get(flow=flow, uuid=ruleset.get_rules()[0].destination)
        self.assertEquals('webhook', webhook.ruleset_type)
        self.assertEquals('http://www.mywebhook.com/lookup', webhook.config_json()[RuleSet.CONFIG_WEBHOOK])
        self.assertEquals('POST', webhook.config_json()[RuleSet.CONFIG_WEBHOOK_ACTION])
        self.assertEquals('@step.value', webhook.operand)
        self.assertEquals('Color Webhook', webhook.label)

        # which should in turn point to a new expression split on @extra.value
        expression = RuleSet.objects.get(flow=flow, uuid=webhook.get_rules()[0].destination)
        self.assertEquals('expression', expression.ruleset_type)
        self.assertEquals('@extra.value', expression.operand)

        # takes us to the next question
        beer_question = ActionSet.objects.get(flow=flow, uuid=expression.get_rules()[0].destination)

        # which should pause for the response
        wait_beer = RuleSet.objects.get(flow=flow, uuid=beer_question.destination)
        self.assertEquals('wait_message', wait_beer.ruleset_type)
        self.assertEquals('@step.value', wait_beer.operand)
        self.assertEquals(1, len(wait_beer.get_rules()))
        self.assertEquals('All Responses', wait_beer.get_rules()[0].category[flow.base_language])

        # and then split on the expression for various beer choices
        beer_expression = RuleSet.objects.get(flow=flow, uuid=wait_beer.get_rules()[0].destination)
        self.assertEquals('expression', beer_expression.ruleset_type)
        self.assertEquals('@(LOWER(step.value))', beer_expression.operand)
        self.assertEquals(5, len(beer_expression.get_rules()))

        # set our expression to operate on the last inbound message
        expression.operand = '@step.value'
        expression.save()

        with patch('requests.post') as mock:
            mock.return_value = MockResponse(200, '{ "status": "valid" }')

            # now move our straggler forward with a message, should get a reply

            first_response = ActionSet.objects.get(flow=flow, x=131)
            actions = first_response.get_actions_dict()
            actions[0]['msg'][flow.base_language] = 'I like @flow.color.category too! What is your favorite beer? @flow.color_webhook'
            first_response.set_actions_dict(actions)
            first_response.save()

            reply = self.send_message(flow, 'red')
            self.assertEquals('I like Red too! What is your favorite beer? { "status": "valid" }', reply)

            reply = self.send_message(flow, 'Turbo King')
            self.assertEquals('Mmmmm... delicious Turbo King. If only they made red Turbo King! Lastly, what is your name?', reply)

    def test_migrate_sample_flows(self):
        self.org.create_sample_flows('https://app.rapidpro.io')
        self.assertEquals(4, self.org.flows.filter(name__icontains='Sample Flow').count())

        # make sure it is localized
        poll = self.org.flows.filter(name='Sample Flow - Simple Poll').first()
        self.assertTrue('base' in poll.action_sets.all().order_by('y').first().get_actions()[0].msg)
        self.assertEqual('base', poll.base_language)

        # check replacement
        order_checker = self.org.flows.filter(name='Sample Flow - Order Status Checker').first()
        ruleset = order_checker.rule_sets.filter(y=298).first()
        self.assertEqual('https://app.rapidpro.io/demo/status/', ruleset.config_json()[RuleSet.CONFIG_WEBHOOK])

        # our test user doesn't use an email address, check for Administrator for the email
        actionset = order_checker.action_sets.filter(y=991).first()
        self.assertEqual('Administrator', actionset.get_actions()[1].emails[0])


class DuplicateValueTest(FlowFileTest):

    def test_duplicate_value_test(self):
        flow = self.get_flow('favorites')
        self.assertEquals("I don't know that color. Try again.", self.send_message(flow, "carpet"))

        # get the run for our contact
        run = FlowRun.objects.get(contact=self.contact, flow=flow)

        # we should have one value for this run, "Other"
        value = Value.objects.get(run=run)
        self.assertEquals("Other", value.category)

        # retry with "red" as an aswer
        self.assertEquals("Good choice, I like Red too! What is your favorite beer?", self.send_message(flow, "red"))

        # we should now still have only one value, but the category should be Red now
        value = Value.objects.get(run=run)
        self.assertEquals("Red", value.category)


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
        msg = self.contact.msgs.last()
        self.assertEqual("Your channel is +12065551212", msg.text)

        # check the split
        msg = self.contact.msgs.first()
        self.assertEqual("206 Channel", msg.text)

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

    def setUp(self):
        super(WebhookLoopTest, self).setUp()
        settings.SEND_WEBHOOKS = True

    def tearDown(self):
        super(WebhookLoopTest, self).tearDown()
        settings.SEND_WEBHOOKS = False

    def test_webhook_loop(self):
        flow = self.get_flow('webhook_loop')

        with patch('requests.get') as mock:
            mock.return_value = MockResponse(200, '{ "text": "first message" }')
            self.assertEquals("first message", self.send_message(flow, "first", initiate_flow=True))

        with patch('requests.get') as mock:
            mock.return_value = MockResponse(200, '{ "text": "second message" }')
            self.assertEquals("second message", self.send_message(flow, "second"))


class MissedCallChannelTest(FlowFileTest):

    def test_missed_call_channel(self):
        flow = self.get_flow('call_channel_split')

        # trigger a missed call on our channel
        call = ChannelEvent.create(self.channel, 'tel:+250788111222', ChannelEvent.TYPE_CALL_IN_MISSED,
                                   timezone.now(), 0)

        # we aren't in the group, so no run should be started
        run = FlowRun.objects.filter(flow=flow).first()
        self.assertIsNone(run)

        # but if we add our contact to the group..
        group = ContactGroup.user_groups.filter(name='Trigger Group').first()
        group.update_contacts(self.admin, [self.create_contact(number='+250788111222')], True)

        # now create another missed call which should fire our trigger
        call = ChannelEvent.create(self.channel, 'tel:+250788111222', ChannelEvent.TYPE_CALL_IN_MISSED,
                                   timezone.now(), 0)

        # should have triggered our flow
        FlowRun.objects.get(flow=flow)

        # should have sent a message to the user
        msg = Msg.objects.get(contact=call.contact, channel=self.channel)
        self.assertEquals(msg.text, "Matched +250785551212")

        # try the same thing with a contact trigger (same as missed calls via twilio)
        Trigger.catch_triggers(msg.contact, Trigger.TYPE_MISSED_CALL, msg.channel)

        self.assertEquals(2, Msg.objects.filter(contact=call.contact, channel=self.channel).count())
        last = Msg.objects.filter(contact=call.contact, channel=self.channel).order_by('-pk').first()
        self.assertEquals(last.text, "Matched +250785551212")


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
        self.assertEquals("I like butter too.",
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


class FlowBatchTest(FlowFileTest):

    def setUp(self):
        super(FlowBatchTest, self).setUp()
        from temba.flows import models as flow_models
        self.orig_batch_size = flow_models.START_FLOW_BATCH_SIZE
        flow_models.START_FLOW_BATCH_SIZE = 10

    def tearDown(self):
        super(FlowBatchTest, self).tearDown()
        from temba.flows import models as flow_models
        flow_models.START_FLOW_BATCH_SIZE = self.orig_batch_size

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
        flow.start([], contacts)

        # ensure 11 flow runs were created
        self.assertEquals(11, FlowRun.objects.all().count())

        # ensure 20 outgoing messages were created (2 for each successful run)
        self.assertEquals(20, Msg.objects.all().exclude(contact=stopped).count())

        # but only one broadcast
        self.assertEquals(1, Broadcast.objects.all().count())
        broadcast = Broadcast.objects.get()

        # ensure that our flowsteps all have the broadcast set on them
        for step in FlowStep.objects.filter(step_type=FlowStep.TYPE_ACTION_SET).exclude(run__contact=stopped):
            self.assertEqual(broadcast, step.broadcasts.all().get())

        # make sure that adding a msg more than once doesn't blow up
        step.add_message(step.messages.all()[0])
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
        flow = Flow.objects.create(org=self.org, name="Import Flow", created_by=self.admin, modified_by=self.admin, saved_by=self.admin)
        revision = FlowRevision.objects.create(flow=flow, definition=json.dumps(exported_json), spec_version=8, revision=1,
                                               created_by=self.admin, modified_by=self.admin)

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
        self.channel = Channel.create(self.org, self.user, 'KE', 'EX', None, '+250788123123', scheme='tel',
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
        FlowRun.objects.all().update(timeout_on=timeout)

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

        # reactivate our first run (not usually possible to have both active)
        run1.is_active = True
        run1.save()

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
        run1.timeout_on = timezone.now()
        run1.save(update_fields=['timeout_on'])

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
        flow.start([], [self.contact])

        # mark our last message as sent
        run = FlowRun.objects.all().first()
        last_msg = run.get_last_msg(OUTGOING)
        last_msg.sent_on = timezone.now() - timedelta(minutes=2)
        last_msg.save()

        timeout = timezone.now()
        expiration = run.expires_on

        FlowRun.objects.all().update(timeout_on=timeout)
        check_flow_timeouts_task()

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

        time.sleep(1)
        FlowRun.objects.all().update(timeout_on=timezone.now())
        check_flow_timeouts_task()

        run = FlowRun.objects.get()

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
        FlowRun.objects.all().update(timeout_on=timezone.now())
        check_flow_timeouts_task()
        run.refresh_from_db()

        # run should be complete now
        self.assertFalse(run.is_active)
        self.assertEqual(run.exit_type, FlowRun.EXIT_TYPE_COMPLETED)

        # and we should have sent our message
        self.assertEquals("Thanks, Wilson",
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
        self.assertEquals("Great. Good to meet you Wilson",
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
        FlowRun.objects.all().update(timeout_on=timezone.now())

        # check our timeouts again
        check_flow_timeouts_task()
        run.refresh_from_db()

        # run should be complete now
        self.assertFalse(run.is_active)
        self.assertEqual(run.exit_type, FlowRun.EXIT_TYPE_COMPLETED)

        # and we should have sent our message
        self.assertEquals("Don't worry about it , we'll catch up next week.",
                          Msg.objects.filter(direction=OUTGOING).order_by('-created_on').first().text)


class MigrationUtilsTest(TembaTest):

    def test_map_actions(self):
        # minimalist flow def with just actions and entry
        flow_def = dict(entry='1234', action_sets=[dict(uuid='1234', y=0, actions=[dict(type='reply', msg=None)])], rule_sets=[dict(y=10, uuid='5678')])
        removed = map_actions(flow_def, lambda x: None)

        # no more action sets and entry is remapped
        self.assertFalse(removed['action_sets'])
        self.assertEqual('5678', removed['entry'])

        # add two action sets, we should remap entry to be the first
        flow_def['action_sets'] = [dict(uuid='1234', y=0, actions=[dict(type='reply', msg=None)]), dict(uuid='2345', y=5, actions=[dict(type='reply', msg="foo")])]
        removed = map_actions(flow_def, lambda x: None if x['msg'] is None else x)

        self.assertEqual(len(removed['action_sets']), 1)
        self.assertEqual(removed['action_sets'][0]['uuid'], '2345')
        self.assertEqual(removed['entry'], '2345')

        # remove a single action
        flow_def['action_sets'] = [dict(uuid='1234', y=0, actions=[dict(type='reply', msg=None), dict(type='reply', msg="foo")])]
        removed = map_actions(flow_def, lambda x: None if x['msg'] is None else x)

        self.assertEqual(len(removed['action_sets']), 1)
        self.assertEqual(len(removed['action_sets'][0]['actions']), 1)
        self.assertEqual(removed['entry'], '1234')

        # no entry
        flow_def = dict(entry='1234', action_sets=[dict(uuid='1234', y=0, actions=[dict(type='reply', msg=None)])], rule_sets=[])
        removed = map_actions(flow_def, lambda x: None if x['msg'] is None else x)

        self.assertEqual(len(removed['action_sets']), 0)
        self.assertEqual(removed['entry'], None)


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
        self.channel = Channel.create(self.org, self.user, 'KE', 'EX', None, '+250788123123', scheme='tel',
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
        self.channel = Channel.create(self.org, self.user, 'KE', 'EX', None, '+250788123123', scheme='tel',
                                      config=dict(send_url='https://google.com'))

    def test_parent_child_ordering(self):
        from temba.channels.tasks import send_msg_task
        self.get_flow('parent_child_ordering')
        flow = Flow.objects.get(name="Parent Flow")

        with patch('temba.channels.tasks.send_msg_task', wraps=send_msg_task) as mock_send_msg:
            flow.start([], [self.contact])

            # get the msgs for our contact
            msgs = Msg.objects.filter(contact=self.contact).order_by('sent_on')
            self.assertEquals(msgs[0].text, "Parent 1")
            self.assertEquals(msgs[1].text, "Child Msg")

            self.assertEqual(mock_send_msg.call_count, 1)


class AndroidChildStatus(FlowFileTest):
    def setUp(self):
        super(AndroidChildStatus, self).setUp()
        self.channel.delete()
        self.channel = Channel.create(self.org, self.user, 'RW', 'A', None, '+250788123123', scheme='tel')

    def test_split_first(self):
        self.get_flow('split_first_child_msg')

        incoming = self.create_msg(direction=INCOMING, contact=self.contact, text="split")
        self.assertTrue(Trigger.find_and_handle(incoming))

        # get the msgs for our contact
        msgs = Msg.objects.filter(contact=self.contact, status=PENDING, direction=OUTGOING).order_by('created_on')
        self.assertEquals(msgs[0].text, "Child Msg 1")

        # respond
        msg = self.create_msg(contact=self.contact, direction='I', text="Response")
        Flow.find_and_handle(msg)

        msgs = Msg.objects.filter(contact=self.contact, status=PENDING, direction=OUTGOING).order_by('created_on')
        self.assertEquals(msgs[0].text, "Child Msg 1")
        self.assertEquals(msgs[1].text, "Child Msg 2")


class FlowChannelSelectionTest(FlowFileTest):

    def setUp(self):
        super(FlowChannelSelectionTest, self).setUp()
        self.channel.delete()
        self.sms_channel = Channel.create(
            self.org, self.user, 'RW', Channel.TYPE_JUNEBUG, None, '+250788123123',
            scheme='tel', uuid='00000000-0000-0000-0000-000000001111',
            role=Channel.DEFAULT_ROLE)
        self.ussd_channel = Channel.create(
            self.org, self.user, 'RW', Channel.TYPE_JUNEBUG_USSD, None, '*123#',
            scheme='tel', uuid='00000000-0000-0000-0000-000000002222',
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
        self.assertEquals(2, FlowRun.objects.filter(flow=flow, contact=contact).count())
        self.assertEquals(1, FlowStart.objects.all().count())
