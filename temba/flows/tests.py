# -*- coding: utf-8 -*-
from __future__ import absolute_import, unicode_literals

import datetime
import json
import os
import pytz
import time

from datetime import timedelta
from decimal import Decimal
from django.conf import settings
from django.core import mail
from django.core.urlresolvers import reverse
from django.test.utils import override_settings
from django.utils import timezone
from mock import patch
from redis_cache import get_redis_connection
from smartmin.tests import SmartminTest
from temba.api.models import WebHookEvent
from temba.channels.models import Channel
from temba.contacts.models import Contact, ContactGroup, ContactField, ContactURN, TEL_SCHEME, TWITTER_SCHEME
from temba.msgs.models import Broadcast, Label, Msg, INCOMING, SMS_NORMAL_PRIORITY, SMS_HIGH_PRIORITY, PENDING, FLOW
from temba.msgs.models import OUTGOING, Call
from temba.orgs.models import Org, Language, CURRENT_EXPORT_VERSION
from temba.tests import TembaTest, MockResponse, FlowFileTest, uuid
from temba.triggers.models import Trigger
from temba.utils import datetime_to_str, str_to_datetime
from temba.values.models import Value
from uuid import uuid4
from xlrd import xldate_as_tuple
from .models import Flow, FlowStep, FlowRun, FlowLabel, FlowStart, FlowRevision, FlowException, ExportFlowResultsTask, COMPLETE
from .models import ActionSet, RuleSet, Action, Rule, ACTION_SET, RULE_SET
from .models import Test, TrueTest, FalseTest, AndTest, OrTest, PhoneTest, NumberTest
from .models import EqTest, LtTest, LteTest, GtTest, GteTest, BetweenTest
from .models import DateEqualTest, DateAfterTest, DateBeforeTest, HasDateTest
from .models import StartsWithTest, ContainsTest, ContainsAnyTest, RegexTest, NotEmptyTest
from .models import SendAction, AddLabelAction, AddToGroupAction, ReplyAction, SaveToContactAction, SetLanguageAction
from .models import EmailAction, StartFlowAction, DeleteFromGroupAction, APIAction, ActionLog
from .flow_migrations import migrate_to_version_5, migrate_to_version_6, migrate_to_version_7, migrate_to_version_8


class FlowTest(TembaTest):

    def setUp(self):
        super(FlowTest, self).setUp()

        self.contact = self.create_contact('Eric', '+250788382382')
        self.contact2 = self.create_contact('Nic', '+250788383383')

        self.flow = Flow.create(self.org, self.admin, "Color Flow", base_language='base')

        self.other_group = self.create_group("Other", [])

        self.definition = self.create_flow_definition(0)

    def export_flow_results(self, flow):
        """
        Exports results for the given flow and returns the generated workbook
        """
        self.login(self.admin)
        response = self.client.get(reverse('flows.flow_export_results') + "?ids=%d" % flow.pk)
        self.assertEqual(response.status_code, 302)

        task = ExportFlowResultsTask.objects.order_by('-pk').first()
        self.assertIsNotNone(task)

        from xlrd import open_workbook
        filename = "%s/test_orgs/%d/results_exports/%s.xls" % (settings.MEDIA_ROOT, self.org.pk, task.uuid)
        return open_workbook(os.path.join(settings.MEDIA_ROOT, filename), 'rb')

    def test_revision_history(self):

        # every save should result in a new flow version
        response = self.flow.update(self.definition)

        self.assertEquals(self.flow.created_by, self.flow.revisions.all()[0].created_by)

        # one version
        revisions = self.flow.revisions.all()
        self.assertEquals(1, revisions.count())
        self.assertEquals(1, revisions[0].revision)

        # create a new update
        self.definition['metadata']['saved_on'] = response['saved_on']
        self.flow.update(self.definition, user=self.admin)
        revisions = self.flow.revisions.all().order_by('created_on')

        # now we should have two revisions
        self.assertEquals(2, revisions.count())
        self.assertEquals(1, revisions[0].revision)
        self.assertEquals(2, revisions[1].revision)

        self.assertEquals(CURRENT_EXPORT_VERSION, revisions[0].spec_version)
        self.assertEquals(CURRENT_EXPORT_VERSION, revisions[0].as_json()['version'])
        self.assertEquals('base', revisions[0].get_definition_json()['base_language'])

    def test_get_localized_text(self):

        text_translations = dict(eng="Hello", esp="Hola", fre="Salut")

        # use default when flow, contact and org don't have language set
        self.assertEqual(self.flow.get_localized_text(text_translations, self.contact, "Hi"), "Hi")

        # flow language used regardless of whether it's an org language
        self.flow.base_language = 'eng'
        self.flow.save(update_fields=('base_language',))
        self.assertEqual(self.flow.get_localized_text(text_translations, self.contact, "Hi"), "Hello")

        eng = Language.create(self.org, self.admin, "English", 'eng')
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

        # see our trigger on the list page
        response = self.client.get(reverse('flows.flow_list'))
        self.assertContains(response, self.flow.name)

        # archive it
        post_data = dict(action='archive', objects=self.flow.pk)
        self.client.post(reverse('flows.flow_list'), post_data)
        response = self.client.get(reverse('flows.flow_list'))
        self.assertNotContains(response, self.flow.name)

        # unarchive it
        response = self.client.get(reverse('flows.flow_archived'), post_data)
        self.assertContains(response, self.flow.name)
        post_data = dict(action='restore', objects=self.flow.pk)
        self.client.post(reverse('flows.flow_archived'), post_data)
        response = self.client.get(reverse('flows.flow_archived'), post_data)
        self.assertNotContains(response, self.flow.name)
        response = self.client.get(reverse('flows.flow_list'), post_data)
        self.assertContains(response, self.flow.name)

    def test_flow_read(self):
        self.login(self.admin)
        response = self.client.get(reverse('flows.flow_read', args=[self.flow.pk]))
        self.assertTrue('initial' in response.context)

    def test_flow_editor(self):
        self.login(self.admin)
        response = self.client.get(reverse('flows.flow_editor', args=[self.flow.pk]))
        self.assertTrue('mutable' in response.context)

    def test_states(self):
        # make sure to delete any old exports
        from shutil import rmtree
        export_dir = "%s/test_orgs/%d/results_exports/" % (settings.MEDIA_ROOT, self.org.pk)
        rmtree(export_dir, ignore_errors=True)

        # set our flow
        self.flow.update(self.definition)

        # how many people in the flow?
        self.assertEquals(0, self.flow.get_total_contacts())
        self.assertEquals(0, self.flow.get_completed_percentage())

        # start the flow
        self.flow.start([], [self.contact, self.contact2])

        # test our stats again
        self.assertEquals(2, self.flow.get_total_contacts())
        self.assertEquals(0, self.flow.get_completed_percentage())

        # should have created a single broadcast
        broadcast = Broadcast.objects.get()
        self.assertEquals("What is your favorite color?", broadcast.text)
        self.assertTrue(broadcast.contacts.filter(pk=self.contact.pk))
        self.assertTrue(broadcast.contacts.filter(pk=self.contact2.pk))

        # should have received a single message
        msg = Msg.objects.get(contact=self.contact)
        self.assertEquals("What is your favorite color?", msg.text)
        self.assertEquals(PENDING, msg.status)
        self.assertEquals(SMS_NORMAL_PRIORITY, msg.priority)

        # should have two steps, one for the outgoing message, another for the rule set we are now waiting on
        entry = ActionSet.objects.filter(uuid=self.flow.entry_uuid)[0]
        step = FlowStep.objects.filter(run__contact=self.contact).order_by('pk')[0]
        contact2_step = FlowStep.objects.filter(run__contact=self.contact2).order_by('pk')[1]
        self.assertEquals("Eric - A:00000000-00000000-00000000-00000001", str(step))

        # test our message context
        context = self.flow.build_message_context(self.contact, None)
        self.assertEquals(dict(__default__=''), context['flow'])

        self.login(self.admin)
        activity = json.loads(self.client.get(reverse('flows.flow_activity', args=[self.flow.pk])).content)
        self.assertEquals(2, activity['visited']["%s:%s" % (uuid(1), uuid(5))])
        self.assertEquals(2, activity['activity'][uuid(5)])

        self.assertEquals(entry.uuid, step.step_uuid)
        self.assertEquals(ACTION_SET, step.step_type)
        self.assertEquals(self.contact, step.run.contact)
        self.assertEquals(self.contact, step.contact)
        self.assertEquals(self.flow, step.run.flow)
        self.assertTrue(step.arrived_on)
        self.assertTrue(step.left_on)
        self.assertEquals(entry.destination, step.next_uuid)

        step = FlowStep.objects.filter(run__contact=self.contact).order_by('pk')[1]

        self.assertEquals(entry.destination, step.step_uuid)
        self.assertEquals(RULE_SET, step.step_type)
        self.assertEquals(self.contact, step.run.contact)
        self.assertEquals(self.contact, step.contact)
        self.assertEquals(self.flow, step.run.flow)
        self.assertTrue(step.arrived_on)
        self.assertFalse(step.left_on)
        self.assertFalse(step.messages.all())

        # if we try to get contacts at this step for our compose we should have two contacts
        self.login(self.admin)
        response = self.client.get(reverse('contacts.contact_omnibox') + "?s=%s" % step.step_uuid)
        contact_json = json.loads(response.content)
        self.assertEquals(2, len(contact_json['results']))
        self.client.logout()

        # set the flow as inactive, shouldn't react to replies
        self.flow.is_archived = True
        self.flow.save()

        # create and send a reply
        incoming = self.create_msg(direction=INCOMING, contact=self.contact, text="Orange")
        self.assertFalse(Flow.find_and_handle(incoming))

        # no reply, our flow isn't active
        self.assertFalse(Msg.objects.filter(response_to=incoming))
        step = FlowStep.objects.get(pk=step.pk)
        self.assertFalse(step.left_on)
        self.assertFalse(step.messages.all())

        # ok, make our flow active again
        self.flow.is_archived = False
        self.flow.save()

        incoming = self.create_msg(direction=INCOMING, contact=self.contact, text="orange")
        self.assertTrue(Flow.find_and_handle(incoming))

        # our message should have gotten a reply
        reply = Msg.objects.get(response_to=incoming)
        self.assertEquals(self.contact, reply.contact)
        self.assertEquals("I love orange too! You said: orange which is category: orange You are: 0788 382 382 SMS: orange Flow: color: orange", reply.text)

        # should be high priority
        self.assertEquals(SMS_HIGH_PRIORITY, reply.priority)

        # message context again
        context = self.flow.build_message_context(self.contact, incoming)
        self.assertTrue(context['flow'])
        self.assertEquals("orange", str(context['flow']['color']['__default__']))
        self.assertEquals("color: orange", context['flow']['__default__'])
        self.assertEquals("Orange", context['flow']['color']['category'])
        self.assertEquals("orange", context['flow']['color']['text'])

        # should have the time this value was collected
        self.assertTrue(context['flow']['color']['time'])

        self.assertEquals(self.channel.get_address_display(e164=True), context['channel']['tel_e164'])
        self.assertEquals(self.channel.get_address_display(), context['channel']['tel'])
        self.assertEquals(self.channel.get_name(), context['channel']['name'])
        self.assertEquals(self.channel.get_address_display(), context['channel']['__default__'])

        # our previous state should be executed
        step = FlowStep.objects.get(run__contact=self.contact, pk=step.id)
        self.assertTrue(step.left_on)
        self.assertEquals(step.messages.all()[0].msg_type, 'F')

        # it should contain what rule matched and what came next
        self.assertEquals(uuid(12), step.rule_uuid)
        self.assertEquals("Orange", step.rule_category)
        self.assertEquals("orange", step.rule_value)
        self.assertFalse(step.rule_decimal_value)
        self.assertEquals(uuid(2), step.next_uuid)
        self.assertTrue(incoming in step.messages.all())

        # we should also have a Value for this RuleSet
        value = Value.objects.get(run=step.run, ruleset__label="color")
        self.assertEquals(uuid(12), value.rule_uuid)
        self.assertEquals("Orange", value.category)
        self.assertEquals("orange", value.string_value)
        self.assertEquals(None, value.decimal_value)
        self.assertEquals(None, value.datetime_value)

        # check what our message context looks like now
        context = self.flow.build_message_context(self.contact, incoming)
        self.assertEquals('orange', context['flow']['color']['value'])
        self.assertEquals('Orange', context['flow']['color']['category'])
        self.assertEquals('orange', context['flow']['color']['text'])

        # change our step instead be decimal
        step.rule_value = '10'
        step.rule_decimal_value = Decimal('10')
        step.save()

        # check our message context again
        context = self.flow.build_message_context(self.contact, incoming)
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

        # we should have a new step
        orange_response = ActionSet.objects.get(uuid=uuid(2))

        self.assertEquals(ACTION_SET, step.step_type)
        self.assertEquals(self.contact, step.run.contact)
        self.assertEquals(self.contact, step.contact)
        self.assertEquals(self.flow, step.run.flow)
        self.assertTrue(step.arrived_on)

        # we have left the flow
        self.assertTrue(step.left_on)
        self.assertTrue(step.run.is_completed)
        self.assertFalse(step.next_uuid)

        # check our completion percentages
        self.assertEquals(2, self.flow.get_total_contacts())
        self.assertEquals(50, self.flow.get_completed_percentage())

        # at this point there are no more steps to take in the flow, so we shouldn't match anymore
        extra = self.create_msg(direction=INCOMING, contact=self.contact, text="Hello ther")
        self.assertFalse(Flow.find_and_handle(extra))

        # try exporting this flow
        exported = self.client.get(reverse('flows.flow_export_results') + "?ids=%d" % self.flow.pk)
        self.assertEquals(302, exported.status_code)

        self.login(self.admin)

        # create a dummy export task so that we won't be able to export
        blocking_export = ExportFlowResultsTask.objects.create(org=self.org, host='test',
                                                               created_by=self.admin, modified_by=self.admin)
        response = self.client.get(reverse('flows.flow_export_results') + "?ids=%d" % self.flow.pk, follow=True)

        self.assertContains(response, "already an export in progress")

        # ok, mark that one as finished and try again
        blocking_export.is_finished = True
        blocking_export.save()

        workbook = self.export_flow_results(self.flow)

        self.assertEquals(3, len(workbook.sheets()))
        entries = workbook.sheets()[0]
        self.assertEquals(3, entries.nrows)
        self.assertEquals(8, entries.ncols)

        self.assertEqual(entries.cell(0, 0).value, "Phone")
        self.assertEqual(entries.cell(0, 1).value, "Name")
        self.assertEqual(entries.cell(0, 2).value, "Groups")
        self.assertEqual(entries.cell(0, 3).value, "First Seen")
        self.assertEqual(entries.cell(0, 4).value, "Last Seen")
        self.assertEqual(entries.cell(0, 5).value, "color (Category) - Color Flow")
        self.assertEqual(entries.cell(0, 6).value, "color (Value) - Color Flow")
        self.assertEqual(entries.cell(0, 7).value, "color (Text) - Color Flow")

        self.assertEqual(entries.cell(1, 0).value, "+250788382382")
        self.assertEqual(entries.cell(1, 1).value, "Eric")
        self.assertEqual(entries.cell(1, 5).value, "Orange")
        self.assertEqual(entries.cell(1, 6).value, "orange")
        self.assertEqual(entries.cell(1, 7).value, "orange")

        self.assertEqual(entries.cell(2, 0).value, "+250788383383")
        self.assertEqual(entries.cell(2, 1).value, "Nic")
        self.assertEqual(entries.cell(1, 5).value, "Orange")

        messages = workbook.sheets()[2]
        self.assertEquals(5, messages.nrows)
        self.assertEquals(6, messages.ncols)

        self.assertEqual(messages.cell(0, 0).value, "Phone")
        self.assertEqual(messages.cell(0, 1).value, "Name")
        self.assertEqual(messages.cell(0, 2).value, "Date")
        self.assertEqual(messages.cell(0, 3).value, "Direction")
        self.assertEqual(messages.cell(0, 4).value, "Message")
        self.assertEqual(messages.cell(0, 5).value, "Channel")

        self.assertEqual(messages.cell(1, 0).value, "+250788382382")
        self.assertEqual(messages.cell(1, 1).value, "Eric")
        self.assertEqual(messages.cell(1, 3).value, "OUT")
        self.assertEqual(messages.cell(1, 4).value, "What is your favorite color?")
        self.assertEqual(messages.cell(1, 5).value, "Test Channel")

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
        self.assertEquals(uuid(5), color['node'])
        self.assertEquals(incoming.text, color['text'])

    def test_export_results_with_surveyor_msgs(self):
        self.flow.update(self.definition)
        self.flow.start([], [self.contact])

        # no urn or channel
        Msg.create_incoming(None, None, "Blue", org=self.org, contact=self.contact)

        workbook = self.export_flow_results(self.flow)

        self.assertEqual(len(workbook.sheets()), 3)

        entries = workbook.sheets()[0]

        self.assertEqual(entries.cell(1, 0).value, "+250788382382")
        self.assertEqual(entries.cell(1, 1).value, "Eric")
        self.assertEqual(entries.cell(1, 5).value, "Blue")

        messages = workbook.sheets()[2]

        self.assertEqual(messages.cell(1, 0).value, "+250788382382")
        self.assertEqual(messages.cell(1, 1).value, "Eric")
        self.assertEqual(messages.cell(1, 3).value, "OUT")
        self.assertEqual(messages.cell(1, 4).value, "What is your favorite color?")
        self.assertEqual(messages.cell(1, 5).value, "Test Channel")

        self.assertEqual(messages.cell(2, 0).value, "")
        self.assertEqual(messages.cell(2, 1).value, "Eric")
        self.assertEqual(messages.cell(2, 3).value, "IN")
        self.assertEqual(messages.cell(2, 4).value, "Blue")
        self.assertEqual(messages.cell(2, 5).value, "")

    def test_export_results_with_no_responses(self):
        self.flow.update(self.definition)

        self.assertEqual(self.flow.get_total_contacts(), 0)
        self.assertEqual(self.flow.get_completed_percentage(), 0)

        workbook = self.export_flow_results(self.flow)

        self.assertEqual(len(workbook.sheets()), 2)

        # every sheet has only the head row
        for entries in workbook.sheets():
            self.assertEqual(entries.nrows, 1)
            self.assertEqual(entries.ncols, 8)

    def test_copy(self):
        # save our original flow
        self.flow.update(self.definition)

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
                               id=copy.pk,
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
                                           "actions": [{"type": "api", "webhook": "https://rapidpro.io/demo/coupon/"},
                                                       {"msg": {"base": "text to get @extra.coupon"}, "type": "reply"}]}],
                          "metadata": {"notes": []}})

        with patch('requests.post') as mock:
            mock.return_value = MockResponse(200, '{ "coupon": "NEXUS4" }')

            self.flow.start([], [self.contact])

            self.assertTrue(self.flow.steps())
            self.assertTrue(Msg.objects.all())
            msg = Msg.objects.all()[0]
            self.assertFalse("@extra.coupon" in msg.text)
            self.assertEquals(msg.text, "text to get NEXUS4")
            self.assertEquals(PENDING, msg.status)

    def test_parsing(self):
        # save this flow
        self.flow.update(self.definition)
        flow = Flow.objects.get(pk=self.flow.id)

        # should have created the appropriate RuleSet and ActionSet objects
        self.assertEquals(4, ActionSet.objects.all().count())

        entry = ActionSet.objects.get(uuid=uuid(1))
        actions = entry.get_actions()
        self.assertEquals(1, len(actions))
        self.assertEquals(ReplyAction(dict(base='What is your favorite color?')).as_json(), actions[0].as_json())
        self.assertEquals(entry.uuid, flow.entry_uuid)

        orange = ActionSet.objects.get(uuid=uuid(2))
        actions = orange.get_actions()
        self.assertEquals(1, len(actions))
        self.assertEquals(ReplyAction(dict(base='I love orange too! You said: @step.value which is category: @flow.color You are: @step.contact.tel SMS: @step Flow: @flow')).as_json(), actions[0].as_json())

        self.assertEquals(1, RuleSet.objects.all().count())
        ruleset = RuleSet.objects.get(uuid=uuid(5))
        self.assertEquals(entry.destination, ruleset.uuid)
        rules = ruleset.get_rules()
        self.assertEquals(4, len(rules))

        # check ordering
        self.assertEquals(uuid(2), rules[0].destination)
        self.assertEquals(uuid(12), rules[0].uuid)
        self.assertEquals(uuid(3), rules[1].destination)
        self.assertEquals(uuid(13), rules[1].uuid)
        self.assertEquals(uuid(4), rules[2].destination)
        self.assertEquals(uuid(14), rules[2].uuid)

        # check routing
        self.assertEquals(ContainsTest(test=dict(base="orange")).as_json(), rules[0].test.as_json())
        self.assertEquals(ContainsTest(test=dict(base="blue")).as_json(), rules[1].test.as_json())
        self.assertEquals(TrueTest().as_json(), rules[2].test.as_json())

        # and categories
        self.assertEquals("Orange", rules[0].category['base'])
        self.assertEquals("Blue", rules[1].category['base'])

        # back out as json
        json_dict = self.flow.as_json()

        self.maxDiff = None
        self.definition['version'] = CURRENT_EXPORT_VERSION
        self.definition['metadata']['name'] = self.flow.name
        self.definition['metadata']['saved_on'] = datetime_to_str(self.flow.saved_on)
        self.definition['metadata']['revision'] = 1
        self.definition['metadata']['expires'] = self.flow.expires_after_minutes
        self.definition['metadata']['id'] = self.flow.pk

        self.definition['flow_type'] = self.flow.flow_type
        self.assertEquals(json_dict, self.definition)

        # remove one of our actions and rules
        del self.definition['action_sets'][3]
        del self.definition['rule_sets'][0]['rules'][2]

        # update
        self.flow.update(self.definition)

        self.assertEquals(3, ActionSet.objects.all().count())

        entry = ActionSet.objects.get(uuid=uuid(1))
        actions = entry.get_actions()
        self.assertEquals(1, len(actions))
        self.assertEquals(ReplyAction(dict(base='What is your favorite color?')).as_json(), actions[0].as_json())
        self.assertEquals(entry.uuid, flow.entry_uuid)

        orange = ActionSet.objects.get(uuid=uuid(2))
        actions = orange.get_actions()
        self.assertEquals(1, len(actions))
        self.assertEquals(ReplyAction(dict(base='I love orange too! You said: @step.value which is category: @flow.color You are: @step.contact.tel SMS: @step Flow: @flow')).as_json(), actions[0].as_json())

        self.assertEquals(1, RuleSet.objects.all().count())
        ruleset = RuleSet.objects.get(uuid=uuid(5))
        self.assertEquals(entry.destination, ruleset.uuid)
        rules = ruleset.get_rules()
        self.assertEquals(3, len(rules))

        # check ordering
        self.assertEquals(uuid(2), rules[0].destination)
        self.assertEquals(uuid(3), rules[1].destination)

        # check routing
        self.assertEquals(ContainsTest(test=dict(base="orange")).as_json(), rules[0].test.as_json())
        self.assertEquals(ContainsTest(test=dict(base="blue")).as_json(), rules[1].test.as_json())

        # updating with a label name that is too long should truncate it
        self.definition['rule_sets'][0]['label'] = ''.join('W' for x in range(75))
        self.definition['rule_sets'][0]['operand'] = ''.join('W' for x in range(135))
        self.definition['rule_sets'][0]['webhook'] = ''.join('W' for x in range(265))
        self.flow.update(self.definition)

        # now check they are truncated to the max lengths
        ruleset = RuleSet.objects.get(uuid=uuid(5))
        self.assertEquals(64, len(ruleset.label))
        self.assertEquals(128, len(ruleset.operand))
        self.assertEquals(255, len(ruleset.webhook_url))

    def test_expanding(self):
        # save our original flow
        self.flow.update(self.definition)

        # add actions for groups and contacts
        definition = self.flow.as_json()

        # add actions for adding to a group and messaging a contact, we'll test how these expand
        action_set = ActionSet.objects.get(uuid=uuid(4))

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

    def assertTest(self, expected_test, expected_value, test, extra=None):
        runs = FlowRun.objects.filter(contact=self.contact)
        if runs:
            run = runs[0]
        else:
            run = FlowRun.create(self.flow, self.contact)

        # clear any extra on this run
        run.fields = ""

        context = run.flow.build_message_context(run.contact, None)
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
        runs = FlowRun.objects.filter(contact=self.contact)
        if runs:
            run = runs[0]
        else:
            run = FlowRun.create(self.flow, self.contact)

        tz = run.flow.org.get_tzinfo()
        context = run.flow.build_message_context(run.contact, None)

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

    def test_tests(self):
        sms = self.create_msg(contact=self.contact, text="GReen is my favorite!")
        self.sms = sms

        test = TrueTest()
        self.assertTest(True, sms.text, test)

        test = FalseTest()
        self.assertTest(False, None, test)

        test = ContainsTest(test=dict(base="Green"))
        self.assertTest(True, "GReen", test)

        sms.text = "Blue is my favorite"
        self.assertTest(False, None, test)

        sms.text = "Greenish is ok too"
        self.assertTest(False, None, test)

        # edit distance
        sms.text = "Greenn is ok though"
        self.assertTest(True, "Greenn", test)

        # variable substitution
        test = ContainsTest(test=dict(base="@extra.color"))
        sms.text = "my favorite color is GREEN today"
        self.assertTest(True, "GREEN", test, extra=dict(color="green"))

        test.test = dict(base="this THAT")
        sms.text = "this is good but won't match"
        self.assertTest(False, None, test)

        test.test = dict(base="this THAT")
        sms.text = "that and this is good and will match"
        self.assertTest(True, "this that", test)

        test = AndTest([TrueTest(), TrueTest()])
        self.assertTest(True, "that and this is good and will match that and this is good and will match", test)

        test = AndTest([TrueTest(), FalseTest()])
        self.assertTest(False, None, test)

        test = OrTest([TrueTest(), FalseTest()])
        self.assertTest(True, "that and this is good and will match", test)

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

        rule = Rule(uuid(4), None, None, None, test)
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
        self.assertTest(True, "كلمة", test) # we get "word"

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

        self.assertEquals(ReplyAction, Action.from_json(org, dict(type='reply', msg="hello world")).__class__)
        self.assertEquals(SendAction, Action.from_json(org, dict(type='send', msg="hello world", contacts=[], groups=[], variables=[])).__class__)

    def test_decimal_values(self):
        flow = self.flow
        flow.update(self.definition)

        rules = RuleSet.objects.get(uuid=uuid(5))

        # update our rule to include decimal parsing
        rules.set_rules_dict([Rule(uuid(12), {flow.base_language: "< 10"}, uuid(2), 'A', LtTest(10)).as_json(),
                              Rule(uuid(13), {flow.base_language: "> 10"}, uuid(3), 'A', GteTest(10)).as_json()])

        rules.save()

        # start the flow
        flow.start([], [self.contact])
        sms = self.create_msg(direction=INCOMING, contact=self.contact, text="My answer is 15")
        self.assertTrue(Flow.find_and_handle(sms))

        step = FlowStep.objects.get(step_uuid=uuid(5))
        self.assertEquals("> 10", step.rule_category)
        self.assertEquals(uuid(13), step.rule_uuid)
        self.assertEquals("15", step.rule_value)
        self.assertEquals(Decimal("15"), step.rule_decimal_value)

    def test_global_keywords_trigger_update(self):
        self.login(self.admin)
        flow = Flow.create(self.org, self.admin, "Flow")

        # update flow triggers
        post_data = dict()
        post_data['name'] = "Flow With Keyword Triggers"
        post_data['keyword_triggers'] = "it,changes,everything"
        post_data['expires_after_minutes'] = 60*12
        response = self.client.post(reverse('flows.flow_update', args=[flow.pk]), post_data, follow=True)

        flow_with_keywords = Flow.objects.get(name=post_data['name'])
        self.assertEquals(200, response.status_code)
        self.assertEquals(response.request['PATH_INFO'], reverse('flows.flow_list'))
        self.assertTrue(flow_with_keywords in response.context['object_list'].all())
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
        post_data['expires_after_minutes'] = 60*12
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
        response = self.client.get(reverse('flows.flow_editor', args=[other_flow.pk]))
        self.assertEquals(302, response.status_code)

        # get our create page
        response = self.client.get(reverse('flows.flow_create'))
        self.assertTrue(response.context['has_flows'])
        self.assertIn('flow_type', response.context['form'].fields)

        # add call channel
        twilio = Channel.objects.create(name="Twilio", channel_type='T', address="0785553434", role="C", org=self.org,
                                        created_by=self.user, modified_by=self.user, secret="56789", gcm_id="456")

        response = self.client.get(reverse('flows.flow_create'))
        self.assertTrue(response.context['has_flows'])
        self.assertIn('flow_type', response.context['form'].fields)  # shown because of call channel

        twilio.delete()

        # create a new regular flow
        response = self.client.post(reverse('flows.flow_create'), dict(name='Flow', flow_type='F', expires_after_minutes=5), follow=True)
        flow1 = Flow.objects.get(org=self.org, name="Flow")
        # add a trigger on this flow
        Trigger.objects.create(org=self.org, keyword='unique', flow=flow1,
                               created_by=self.admin, modified_by=self.admin)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(flow1.flow_type, 'F')
        self.assertEqual(flow1.expires_after_minutes, 5)

        # create a new surveyor flow
        self.client.post(reverse('flows.flow_create'), dict(name='Surveyor Flow', expires_after_minutes=5, flow_type='S'), follow=True)
        flow2 = Flow.objects.get(org=self.org, name="Surveyor Flow")
        self.assertEqual(flow2.flow_type, 'S')
        self.assertEqual(flow2.expires_after_minutes, 5)

        # make sure we don't get a start flow button for Android Surveys
        response = self.client.get(reverse('flows.flow_editor', args=[flow2.pk]))
        self.assertNotContains(response, "broadcast-rulesflow btn-primary")

        # test flows with triggers
        # create a new flow with one unformatted keyword
        post_data = dict()
        post_data['name'] = "Flow With Unformated Keyword Triggers"
        post_data['keyword_triggers'] = "this is,it"
        response = self.client.post(reverse('flows.flow_create'), post_data)
        self.assertTrue(response.context['form'].errors)
        self.assertTrue('"this is" must be a single word containing only letter and numbers' in response.context['form'].errors['keyword_triggers'])

        # create a new flow with one existing keyword
        post_data = dict()
        post_data['name'] = "Flow With Existing Keyword Triggers"
        post_data['keyword_triggers'] = "this,is,unique"
        response = self.client.post(reverse('flows.flow_create'), post_data)
        self.assertTrue(response.context['form'].errors)
        self.assertTrue('The keyword "unique" is already used for another flow' in response.context['form'].errors['keyword_triggers'])

        # create a new flow with keywords
        post_data = dict()
        post_data['name'] = "Flow With Good Keyword Triggers"
        post_data['keyword_triggers'] = "this,is,it"
        post_data['flow_type'] = 'F'
        post_data['expires_after_minutes'] = 30
        response = self.client.post(reverse('flows.flow_create'), post_data, follow=True)
        flow3 = Flow.objects.get(name=post_data['name'])

        self.assertEqual(200, response.status_code)
        self.assertEqual(response.request['PATH_INFO'], reverse('flows.flow_editor', args=[flow3.pk]))
        self.assertEqual(response.context['object'].triggers.count(), 3)

        # update flow triggers
        post_data = dict()
        post_data['name'] = "Flow With Keyword Triggers"
        post_data['keyword_triggers'] = "it,changes,everything"
        post_data['expires_after_minutes'] = 60*12
        response = self.client.post(reverse('flows.flow_update', args=[flow3.pk]), post_data, follow=True)
        flow3 = Flow.objects.get(name=post_data['name'])
        self.assertEquals(200, response.status_code)
        self.assertEquals(response.request['PATH_INFO'], reverse('flows.flow_list'))
        self.assertTrue(flow3 in response.context['object_list'].all())
        self.assertEquals(flow3.triggers.count(), 5)
        self.assertEquals(flow3.triggers.filter(is_archived=True).count(), 2)
        self.assertEquals(flow3.triggers.filter(is_archived=False).count(), 3)
        self.assertEquals(flow3.triggers.filter(is_archived=False).exclude(groups=None).count(), 0)

        # update flow with unformated keyword
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
        self.assertEquals(flow3.triggers.filter(is_archived=False).count(), 3)
        self.assertEquals(flow3.triggers.filter(is_archived=False).exclude(groups=None).count(), 1)
        self.assertEquals(flow3.triggers.filter(is_archived=False).exclude(groups=None)[0].keyword, "everything")

        # add some rules to first flow
        ActionSet.objects.all().delete()
        flow1.update(self.definition)
        self.assertEquals(4, ActionSet.objects.all().count())

        # can see ours
        response = self.client.get(reverse('flows.flow_results', args=[flow1.pk]))
        self.assertEquals(200, response.status_code)

        # check flow listing
        response = self.client.get(reverse('flows.flow_list'))
        self.assertEqual(list(response.context['object_list']), [flow1, flow3, flow2, self.flow])  # by last modified

        # start a contact on that flow
        flow = flow1
        flow.start([], [self.contact])

        # remove one of the contacts
        run = flow1.runs.get(contact=self.contact)
        response = self.client.post(reverse('flows.flow_results', args=[flow.pk]), data=dict(run=run.pk))
        self.assertEquals(200, response.status_code)
        self.assertFalse(FlowStep.objects.filter(run__contact=self.contact))

        # test getting the json
        response = self.client.get(reverse('flows.flow_json', args=[flow.pk]))
        json_dict = json.loads(response.content)['flow']

        # test setting the json
        json_dict['action_sets'] = [dict(uuid=uuid(1), x=1, y=1, destination=None,
                                         actions=[dict(type='reply', msg=dict(base='This flow is more like a broadcast'))])]
        json_dict['rule_sets'] = []
        json_dict['entry'] = uuid(1)

        response = self.client.post(reverse('flows.flow_json', args=[flow.pk]), json.dumps(json_dict), content_type="application/json")
        self.assertEquals(200, response.status_code)
        self.assertEquals(1, ActionSet.objects.all().count())

        actionset = ActionSet.objects.get()
        self.assertEquals(actionset.flow, flow)

        # can't save with an invalid uuid
        json_dict['metadata']['saved_on'] = datetime_to_str(timezone.now())
        json_dict['action_sets'][0]['destination'] = 'notthere'

        response = self.client.post(reverse('flows.flow_json', args=[flow.pk]), json.dumps(json_dict), content_type="application/json")
        self.assertEquals(200, response.status_code)
        flow = Flow.objects.get(pk=flow.pk)
        flow_json = flow.as_json()
        self.assertIsNone(flow_json['action_sets'][0]['destination'])

        # flow should still be there though
        flow = Flow.objects.get(pk=flow.pk)

        # should still have the original one, nothing changed
        response = self.client.get(reverse('flows.flow_json', args=[flow.pk]))
        self.assertEquals(200, response.status_code)
        json_dict = json.loads(response.content)

        # can't save against the other org's flow
        response = self.client.post(reverse('flows.flow_json', args=[other_flow.pk]), json.dumps(json_dict), content_type="application/json")
        self.assertEquals(302, response.status_code)

        # can't save with invalid json
        with self.assertRaises(ValueError):
            response = self.client.post(reverse('flows.flow_json', args=[flow.pk]), "badjson", content_type="application/json")

        # test simulation
        simulate_url = reverse('flows.flow_simulate', args=[flow.pk])

        test_contact = Contact.get_test_contact(self.admin)
        group = self.create_group("players", [test_contact])
        contact_field = ContactField.get_or_create(self.org, 'custom', 'custom')
        contact_field_value = Value.objects.create(contact=test_contact, contact_field=contact_field, org=self.org,
                                                   string_value="hey")

        response = self.client.get(simulate_url)
        self.assertEquals(response.status_code, 302)

        post_data = dict()
        post_data['has_refresh'] = True

        response = self.client.post(simulate_url, json.dumps(post_data), content_type="application/json")
        json_dict = json.loads(response.content)

        self.assertFalse(group in test_contact.all_groups.all())
        self.assertFalse(test_contact.values.all())

        self.assertEquals(len(json_dict.keys()), 5)
        self.assertEquals(len(json_dict['messages']), 3)
        self.assertEquals('Test Contact has entered the &quot;Flow&quot; flow', json_dict['messages'][0]['text'])
        self.assertEquals("This flow is more like a broadcast", json_dict['messages'][1]['text'])
        self.assertEquals("Test Contact has exited this flow", json_dict['messages'][2]['text'])

        group = self.create_group("fans", [test_contact])
        contact_field_value = Value.objects.create(contact=test_contact, contact_field=contact_field, org=self.org,
                                                   string_value="hey")

        post_data['new_message'] = "Ok, Thanks"
        post_data['has_refresh'] = False

        response = self.client.post(simulate_url, json.dumps(post_data), content_type="application/json")
        self.assertEquals(200, response.status_code)
        json_dict = json.loads(response.content)

        self.assertTrue(group in test_contact.all_groups.all())
        self.assertTrue(test_contact.values.all())
        self.assertEqual(test_contact.values.get(string_value='hey'), contact_field_value)

        self.assertEquals(len(json_dict.keys()), 5)
        self.assertTrue('status' in json_dict.keys())
        self.assertTrue('visited' in json_dict.keys())
        self.assertTrue('activity' in json_dict.keys())
        self.assertTrue('messages' in json_dict.keys())
        self.assertTrue('description' in json_dict.keys())
        self.assertEquals(json_dict['status'], 'success')
        self.assertEquals(json_dict['description'], 'Message sent to Flow')

        post_data['has_refresh'] = True

        response = self.client.post(simulate_url, json.dumps(post_data), content_type="application/json")
        self.assertEquals(200, response.status_code)
        json_dict = json.loads(response.content)

        self.assertEquals(len(json_dict.keys()), 5)
        self.assertTrue('status' in json_dict.keys())
        self.assertTrue('visited' in json_dict.keys())
        self.assertTrue('activity' in json_dict.keys())
        self.assertTrue('messages' in json_dict.keys())
        self.assertTrue('description' in json_dict.keys())
        self.assertEquals(json_dict['status'], 'success')
        self.assertEquals(json_dict['description'], 'Message sent to Flow')

        # test our copy view
        response = self.client.post(reverse('flows.flow_copy', args=[flow.pk]))
        flow_copy = Flow.objects.get(org=self.org, name="Copy of %s" % flow.name)
        self.assertRedirect(response, reverse('flows.flow_editor', args=[flow_copy.pk]))

        flow_label_1 = FlowLabel.objects.create(name="one", org=self.org, parent=None)
        flow_label_2 = FlowLabel.objects.create(name="two", org=self.org2, parent=None)

        # test update view
        response = self.client.post(reverse('flows.flow_update', args=[flow.pk]))
        self.assertEquals(200, response.status_code)
        self.assertEquals(5, len(response.context['form'].fields))
        self.assertTrue('name' in response.context['form'].fields)
        self.assertTrue('keyword_triggers' in response.context['form'].fields)
        self.assertTrue('ignore_triggers' in response.context['form'].fields)

        # test broadcast view
        response = self.client.get(reverse('flows.flow_broadcast', args=[flow.pk]))
        self.assertEquals(3, len(response.context['form'].fields))
        self.assertTrue('omnibox' in response.context['form'].fields)
        self.assertTrue('restart_participants' in response.context['form'].fields)

        post_data = dict()
        post_data['omnibox'] = "c-%d" % self.contact.pk
        post_data['restart_participants'] = 'on'

        count = Broadcast.objects.all().count()
        self.client.post(reverse('flows.flow_broadcast', args=[flow.pk]), post_data, follow=True)
        self.assertEquals(count + 1, Broadcast.objects.all().count())

        # we should have a flow start
        start = FlowStart.objects.get(flow=flow)

        # should be in a completed state
        self.assertEquals(COMPLETE, start.status)
        self.assertEquals(1, start.contact_count)

        # do so again but don't restart the participants
        del post_data['restart_participants']

        self.client.post(reverse('flows.flow_broadcast', args=[flow.pk]), post_data, follow=True)

        # should have a new flow start
        new_start = FlowStart.objects.filter(flow=flow).order_by('-created_on').first()
        self.assertNotEquals(start, new_start)
        self.assertEquals(COMPLETE, new_start.status)
        self.assertEquals(0, new_start.contact_count)

        # mark that start as incomplete
        new_start.status = 'S'
        new_start.save()

        # try to start again
        response = self.client.post(reverse('flows.flow_broadcast', args=[flow.pk]), post_data, follow=True)

        # should have an error now
        self.assertTrue(response.context['form'].errors)

        # shouldn't have a new flow start as validation failed
        self.assertFalse(FlowStart.objects.filter(flow=flow).exclude(id__lte=new_start.id))

        # test ivr flow creation
        self.channel.role = 'SRCA'
        self.channel.save()

        post_data = dict(name="Message flow", expires_after_minutes=5, flow_type='F')
        response = self.client.post(reverse('flows.flow_create'), post_data, follow=True)
        msg_flow = Flow.objects.get(name=post_data['name'])

        self.assertEquals(200, response.status_code)
        self.assertEquals(response.request['PATH_INFO'], reverse('flows.flow_editor', args=[msg_flow.pk]))
        self.assertEquals(msg_flow.flow_type, 'F')

        post_data = dict(name="Call flow", expires_after_minutes=5, flow_type='V')
        response = self.client.post(reverse('flows.flow_create'), post_data, follow=True)
        call_flow = Flow.objects.get(name=post_data['name'])

        self.assertEquals(200, response.status_code)
        self.assertEquals(response.request['PATH_INFO'], reverse('flows.flow_editor', args=[call_flow.pk]))
        self.assertEquals(call_flow.flow_type, 'V')

        # test creating a  flow with base language
        # create the language for our org
        language = Language.create(self.org, flow.created_by, "English", 'eng')
        self.org.primary_language = language
        self.org.save()

        post_data = dict(name="Language Flow", expires_after_minutes=5, base_language=language.iso_code, flow_type='F')
        response = self.client.post(reverse('flows.flow_create'), post_data, follow=True)
        language_flow = Flow.objects.get(name=post_data['name'])

        self.assertEquals(200, response.status_code)
        self.assertEquals(response.request['PATH_INFO'], reverse('flows.flow_editor', args=[language_flow.pk]))
        self.assertEquals(language_flow.base_language, language.iso_code)

    def test_views_viewers(self):
        #create a viewer
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

        response = self.client.get(reverse('flows.flow_editor', args=[self.flow.pk]))
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
        response = self.client.get(reverse('flows.flow_editor', args=[flow2.pk]))
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
        self.assertEquals('Invalid label name: @badlabel', json.loads(response.content)['description'])

    def test_flow_start_with_start_msg(self):
        # set our flow
        self.flow.update(self.definition)

        sms = self.create_msg(direction=INCOMING, contact=self.contact, text="I am coming")
        self.flow.start([], [self.contact], start_msg=sms)

        self.assertTrue(FlowRun.objects.filter(contact=self.contact))
        run = FlowRun.objects.filter(contact=self.contact).first()

        self.assertEquals(run.steps.all().count(), 2)
        actionset_step = run.steps.filter(step_type=ACTION_SET).first()
        ruleset_step = run.steps.filter(step_type=RULE_SET).first()

        # no messages on the ruleset step
        self.assertFalse(ruleset_step.messages.all())

        # should have 2 messages on the actionset step
        self.assertEquals(actionset_step.messages.all().count(), 2)

        # one is the start msg
        self.assertTrue(actionset_step.messages.filter(pk=sms.pk))

        # sms msg_type should be FLOW
        self.assertEquals(Msg.objects.get(pk=sms.pk).msg_type, FLOW)

    def test_multiple(self):
        # set our flow
        self.flow.update(self.definition)
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
        self.assertTrue(Flow.find_and_handle(incoming))

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
        self.assertTrue(Flow.find_and_handle(incoming))

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
        # set our flow
        self.flow.update(self.definition)
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
        self.assertTrue(Flow.find_and_handle(incoming))

        # now we should trigger the other flow as we are at our terminal flow
        self.assertTrue(Trigger.find_and_handle(other_incoming))


class ActionTest(TembaTest):

    def setUp(self):
        super(ActionTest, self).setUp()

        self.contact = self.create_contact('Eric', '+250788382382')
        self.contact2 = self.create_contact('Nic', '+250788383383')

        self.flow = Flow.create(self.org, self.admin, "Empty Flow", base_language='base')

        self.other_group = self.create_group("Other", [])

    def test_reply_action(self):
        msg = self.create_msg(direction=INCOMING, contact=self.contact, text="Green is my favorite")
        run = FlowRun.create(self.flow, self.contact)

        action = ReplyAction(dict(base="We love green too!"))
        action.execute(run, None, msg)
        msg = Msg.objects.get(contact=self.contact, direction='O')
        self.assertEquals("We love green too!", msg.text)

        Broadcast.objects.all().delete()

        action_json = action.as_json()
        action = ReplyAction.from_json(self.org, action_json)
        self.assertEquals(dict(base="We love green too!"), action.msg)

        action.execute(run, None, msg)

        response = msg.responses.get()
        self.assertEquals("We love green too!", response.text)
        self.assertEquals(self.contact, response.contact)

    def test_send_action(self):
        msg_body = "Hi @contact.name (@contact.state). @step.contact (@step.contact.state) is in the flow"

        self.contact.set_field('state', "WA", label="State")
        self.contact2.set_field('state', "GA", label="State")
        run = FlowRun.create(self.flow, self.contact)

        action = SendAction(dict(base=msg_body),
                            [], [self.contact2], [])
        action.execute(run, None, None)

        action_json = action.as_json()
        action = SendAction.from_json(self.org, action_json)
        self.assertEqual(action.msg['base'], msg_body)

        broadcast = Broadcast.objects.get()
        self.assertEqual(broadcast.get_messages().count(), 1)
        msg = broadcast.get_messages().first()
        self.assertEqual(msg.contact, self.contact2)
        self.assertEqual(msg.text, "Hi Nic (GA). Eric (WA) is in the flow")

        # empty message should be a no-op
        action = SendAction(dict(base=""), [], [self.contact], [])
        action.execute(run, None, None)
        self.assertEqual(Broadcast.objects.all().count(), 1)

        # try with a test contact and a group
        test_contact = Contact.get_test_contact(self.user)
        test_contact.name = "Mr Test"
        test_contact.save()
        test_contact.set_field('state', "IN", label="State")

        self.other_group.update_contacts([self.contact2], True)

        action = SendAction(dict(base=msg_body), [self.other_group], [self.contact], [])
        run = FlowRun.create(self.flow, test_contact)
        action.execute(run, None, None)

        # check the action description
        description = "Sent '{'base': u'Hi @contact.name (@contact.state). @step.contact (@step.contact.state) is in the flow'}' to Eric, Other"
        self.assertEqual(action.get_description(), description)

        # since we are test contact now, no new broadcasts
        self.assertEqual(Broadcast.objects.all().count(), 1)

        # but we should have logged instead
        logged = "Sending &#39;Hi @contact.name (@contact.state). Mr Test (IN) is in the flow&#39; to 2 contacts"
        self.assertEqual(ActionLog.objects.all().first().text, logged)

    @override_settings(SEND_EMAILS=True)
    def test_email_action(self):
        msg = self.create_msg(direction=INCOMING, contact=self.contact, text="Green is my favorite")
        run = FlowRun.create(self.flow, self.contact)

        action = EmailAction(["steve@apple.com"], "Subject", "Body")

        # check to and from JSON
        action_json = action.as_json()
        action = EmailAction.from_json(self.org, action_json)

        action.execute(run, None, msg)

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
        action = EmailAction(["@contact.name", "xyz", '@(SUBSTITUTE(LOWER(contact), " ", "") & "@nyaruka.com")'],
                             "@contact.name added in subject",
                             "@contact.name uses phone @contact.tel")

        action_json = action.as_json()
        action = EmailAction.from_json(self.org, action_json)

        action.execute(run, None, msg)

        self.assertEquals(len(mail.outbox), 2)
        self.assertEquals(mail.outbox[1].subject, "Eric added in subject")
        self.assertEquals(mail.outbox[1].body, "Eric uses phone 0788 382 382")
        self.assertEquals(mail.outbox[1].recipients(), ["eric@nyaruka.com"])  # invalid emails are ignored

        # check simulator reports invalid addresses
        test_contact = Contact.get_test_contact(self.user)
        test_run = FlowRun.create(self.flow, test_contact)

        action.execute(test_run, None, msg)

        logs = list(ActionLog.objects.order_by('pk'))
        self.assertEqual(logs[0].level, ActionLog.LEVEL_INFO)
        self.assertEqual(logs[0].text, "&quot;Test Contact uses phone (206) 555-0100&quot; would be sent to testcontact@nyaruka.com")
        self.assertEqual(logs[1].level, ActionLog.LEVEL_WARN)
        self.assertEqual(logs[1].text, "Some email address appear to be invalid: Test Contact, xyz")

        # check that all white space is replaced with single spaces in the subject
        test = EmailAction(["steve@apple.com"], "Allo \n allo\tmessage", "Email notification for allo allo")
        test.execute(run, None, msg)

        self.assertEquals(len(mail.outbox), 3)
        self.assertEquals(mail.outbox[2].subject, 'Allo allo message')
        self.assertEquals(mail.outbox[2].body, 'Email notification for allo allo')
        self.assertEquals(mail.outbox[2].recipients(), ["steve@apple.com"])

    def test_save_to_contact_action(self):
        sms = self.create_msg(direction=INCOMING, contact=self.contact, text="batman")
        test = SaveToContactAction.from_json(self.org, dict(type='save', label="Superhero Name", value='@step'))
        run = FlowRun.create(self.flow, self.contact)

        field = ContactField.objects.get(org=self.org, key="superhero_name")
        self.assertEquals("Superhero Name", field.label)

        test.execute(run, None, sms)

        # user should now have a nickname field with a value of batman
        contact = Contact.objects.get(id=self.contact.pk)
        self.assertEquals("batman", contact.get_field_raw('superhero_name'))

        # test clearing our value
        test = SaveToContactAction.from_json(self.org, test.as_json())
        test.value = ""
        test.execute(run, None, sms)
        contact = Contact.objects.get(id=self.contact.pk)
        self.assertEquals(None, contact.get_field_raw('superhero_name'))

        # test setting our name
        test = SaveToContactAction.from_json(self.org, dict(type='save', label="Name", value='', field='name'))
        test.value = "Eric Newcomer"
        test.execute(run, None, sms)
        contact = Contact.objects.get(id=self.contact.pk)
        self.assertEquals("Eric Newcomer", contact.name)
        run.contact = contact

        # test setting just the first name
        test = SaveToContactAction.from_json(self.org, dict(type='save', label="First Name", value='', field='first_name'))
        test.value = "Jen"
        test.execute(run, None, sms)
        contact = Contact.objects.get(id=self.contact.pk)
        self.assertEquals("Jen Newcomer", contact.name)

        # we should strip whitespace
        run.contact = contact
        test = SaveToContactAction.from_json(self.org, dict(type='save', label="First Name", value='', field='first_name'))
        test.value = " Jackson "
        test.execute(run, None, sms)
        contact = Contact.objects.get(id=self.contact.pk)
        self.assertEquals("Jackson Newcomer", contact.name)

        # first name works with a single word
        run.contact = contact
        contact.name = "Percy"
        contact.save()

        test = SaveToContactAction.from_json(self.org, dict(type='save', label="First Name", value='', field='first_name'))
        test.value = " Cole"
        test.execute(run, None, sms)
        contact = Contact.objects.get(id=self.contact.pk)
        self.assertEquals("Cole", contact.name)

        # test saving something really long to another field
        test = SaveToContactAction.from_json(self.org, dict(type='save', label="Last Message", value='', field='last_message'))
        test.value = "This is a long message, longer than 160 characters, longer than 250 characters, all the way up "\
                      "to 500 some characters long because sometimes people save entire messages to their contact " \
                      "fields and we want to enable that for them so that they can do what they want with the platform."
        test.execute(run, None, sms)
        contact = Contact.objects.get(id=self.contact.pk)
        self.assertEquals(test.value, contact.get_field('last_message').string_value)

        # test saving a contact's phone number
        test = SaveToContactAction.from_json(self.org, dict(type='save', label='Phone Number', field='tel_e164', value='@step'))

        # make sure they have a twitter urn first
        contact.urns.add(ContactURN.get_or_create(self.org, TWITTER_SCHEME, 'enewcomer'))
        self.assertIsNotNone(contact.urns.filter(path='enewcomer').first())

        # add another phone number to make sure it doesn't get removed too
        contact.urns.add(ContactURN.get_or_create(self.org, TEL_SCHEME, '+18005551212'))
        self.assertEquals(3, contact.urns.all().count())

        # create an inbound message on our original phone number
        sms = self.create_msg(direction=INCOMING, contact=self.contact,
                              text="+12065551212", contact_urn=contact.urns.filter(path='+250788382382').first())

        # create another contact with that phone number, to test stealing
        robbed = self.create_contact("Robzor", "+12065551212")

        test.execute(run, None, sms)

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
        run = FlowRun.create(self.flow, test_contact)
        test.execute(run, None, sms)

        # URN should be unchanged on the simulator contact
        test_contact = Contact.objects.get(id=test_contact.id)
        self.assertEquals(test_contact_urn, test_contact.urns.all().first())

    def test_set_language_action(self):
        action = SetLanguageAction('kli', 'Klingon')

        # check to and from JSON
        action_json = action.as_json()
        action = SetLanguageAction.from_json(self.org, action_json)

        self.assertEqual('kli', action.lang)
        self.assertEqual('Klingon', action.name)

        # execute our action and check we are Klingon now, eeektorp shnockahltip.
        run = FlowRun.create(self.flow, self.contact)
        action.execute(run, None, None)
        self.assertEquals('kli', Contact.objects.get(pk=self.contact.pk).language)

        # try setting the language to something thats not three characters
        action_json['lang'] = 'base'
        action_json['name'] = 'Default'
        action = SetLanguageAction.from_json(self.org, action_json)
        action.execute(run, None, None)

        # should clear the contacts language
        self.assertIsNone(Contact.objects.get(pk=self.contact.pk).language)

    def test_start_flow_action(self):
        orig_flow = self.create_flow()
        run = FlowRun.create(orig_flow, self.contact)

        self.flow.update(self.create_flow_definition())

        sms = self.create_msg(direction=INCOMING, contact=self.contact, text="Green is my favorite")

        action = StartFlowAction(self.flow)

        action_json = action.as_json()
        action = StartFlowAction.from_json(self.org, action_json)

        action.execute(run, None, sms, [])

        # our contact should now be in the flow
        self.assertTrue(FlowStep.objects.filter(run__flow=self.flow, run__contact=self.contact))
        self.assertTrue(Msg.objects.filter(contact=self.contact, direction='O', text='What is your favorite color?'))

    def test_group_actions(self):
        sms = self.create_msg(direction=INCOMING, contact=self.contact, text="Green is my favorite")
        run = FlowRun.create(self.flow, self.contact)

        group = self.create_group("Flow Group", [])

        action = AddToGroupAction([group, "@step.contact"])

        action_json = action.as_json()
        action = AddToGroupAction.from_json(self.org, action_json)

        action.execute(run, None, sms)

        # user should now be in the group
        self.assertTrue(group.contacts.filter(id=self.contact.pk))
        self.assertEquals(1, group.contacts.all().count())

        # we should have acreated a group with the name of the contact
        replace_group = ContactGroup.user_groups.get(name=self.contact.name)
        self.assertTrue(replace_group.contacts.filter(id=self.contact.pk))
        self.assertEquals(1, replace_group.contacts.all().count())

        # passing through twice doesn't change anything
        action.execute(run, None, sms)

        self.assertTrue(group.contacts.filter(id=self.contact.pk))
        self.assertEquals(1, group.contacts.all().count())

        action = DeleteFromGroupAction([group, "@step.contact"])
        action_json = action.as_json()
        action = DeleteFromGroupAction.from_json(self.org, action_json)

        action.execute(run, None, sms)

        # user should be gone now
        self.assertFalse(group.contacts.filter(id=self.contact.pk))
        self.assertEquals(0, group.contacts.all().count())
        self.assertFalse(replace_group.contacts.filter(id=self.contact.pk))
        self.assertEquals(0, replace_group.contacts.all().count())

        action.execute(run, None, sms)

        self.assertFalse(group.contacts.filter(id=self.contact.pk))
        self.assertEquals(0, group.contacts.all().count())

    def test_add_label_action(self):
        flow = self.flow
        msg = self.create_msg(direction=INCOMING, contact=self.contact, text="Green is my favorite")
        run = FlowRun.create(flow, self.contact)

        label = Label.get_or_create(self.org, self.user, "green label")

        action = AddLabelAction([label, "@step.contact"])

        action_json = action.as_json()
        action = AddLabelAction.from_json(self.org, action_json)

        # no message yet; such Add Label action on entry Actionset. No error should be raised
        action.execute(run, None, None)

        self.assertFalse(label.get_messages())
        self.assertEqual(label.get_visible_count(), 0)

        action.execute(run, None, msg)

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
        action.execute(run, None, msg)

        self.assertEqual(set(Msg.objects.get(pk=msg.pk).labels.all()), {label, new_label})
        self.assertEquals(Label.label_objects.get(pk=label.pk).get_visible_count(), 1)
        self.assertEquals(Label.label_objects.get(pk=new_label.pk).get_visible_count(), 1)

    @override_settings(SEND_WEBHOOKS=True)
    @patch('django.utils.timezone.now')
    @patch('requests.post')
    def test_api_action(self, mock_requests_post, mock_timezone_now):
        tz = pytz.timezone("Africa/Kigali")
        mock_requests_post.return_value = MockResponse(200, '{ "coupon": "NEXUS4" }')
        mock_timezone_now.return_value = tz.localize(datetime.datetime(2015, 10, 27, 16, 07, 30, 6))

        action = APIAction('http://example.com/callback.php')

        # check to and from JSON
        action_json = action.as_json()
        action = APIAction.from_json(self.org, action_json)

        self.assertEqual(action.webhook, 'http://example.com/callback.php')

        run = FlowRun.create(self.flow, self.contact)

        # test with no incoming message
        action.execute(run, None, None)

        # check webhook was called with correct payload
        mock_requests_post.assert_called_once_with('http://example.com/callback.php',
                                                   headers={'User-agent': "RapidPro"},
                                                   data={'run': run.pk,
                                                         'phone': u'+250788382382',
                                                         'text': None,
                                                         'flow': self.flow.pk,
                                                         'relayer': -1,
                                                         'step': 'None',
                                                         'values': '[]',
                                                         'time': '2015-10-27T14:07:30.000006Z',
                                                         'steps': '[]',
                                                         'channel': -1},
                                                   timeout=10)
        mock_requests_post.reset_mock()

        # check that run @extra was updated
        self.assertEqual(json.loads(run.fields), {'coupon': "NEXUS4"})

        # test with an incoming message
        msg = self.create_msg(direction=INCOMING, contact=self.contact, text="Green is my favorite")
        action.execute(run, None, msg)

        # check webhook was called with correct payload
        mock_requests_post.assert_called_once_with('http://example.com/callback.php',
                                                   headers={'User-agent': 'RapidPro'},
                                                   data={'run': run.pk,
                                                         'phone': u'+250788382382',
                                                         'text': "Green is my favorite",
                                                         'flow': self.flow.pk,
                                                         'relayer': msg.channel.pk,
                                                         'step': 'None',
                                                         'values': '[]',
                                                         'time': '2015-10-27T14:07:30.000006Z',
                                                         'steps': '[]',
                                                         'channel': msg.channel.pk},
                                                   timeout=10)

        # check simulator warns of webhook URL errors
        action = APIAction('http://example.com/callback.php?@contact.xyz')
        test_contact = Contact.get_test_contact(self.user)
        test_run = FlowRun.create(self.flow, test_contact)

        action.execute(test_run, None, None)

        event = WebHookEvent.objects.order_by('-pk').first()

        logs = list(ActionLog.objects.order_by('pk'))
        self.assertEqual(logs[0].level, ActionLog.LEVEL_WARN)
        self.assertEqual(logs[0].text, "URL appears to contain errors: Undefined variable: contact.xyz")
        self.assertEqual(logs[1].level, ActionLog.LEVEL_INFO)
        self.assertEqual(logs[1].text, "Triggered <a href='/api/v1/log/%d/' target='_log'>webhook event</a> - 200" % event.pk)


class FlowRunTest(TembaTest):

    def setUp(self):
        super(FlowRunTest, self).setUp()

        self.flow = self.create_flow()
        self.contact = self.create_contact("Ben Haggerty", "+250788123123")

    def test_field_normalization(self):
        fields = dict(field1="value1", field2="value2")
        (normalized, count) = FlowRun.normalize_fields(fields)
        self.assertEquals(fields, normalized)

        # field text too long
        fields['field2'] = "*" * 650
        (normalized, count) = FlowRun.normalize_fields(fields)
        self.assertEquals(640, len(normalized['field2']))

        # field name too long
        fields['field' + ("*" * 350)] = "short value"
        (normalized, count) = FlowRun.normalize_fields(fields)
        self.assertTrue('field' + ("*" * 250) in normalized)

        # too many fields
        for i in range(129):
            fields['field%d' % i] = 'value %d' % i
        (normalized, count) = FlowRun.normalize_fields(fields)
        self.assertEquals(128, count)
        self.assertEquals(128, len(normalized))

        fields = dict(numbers=["zero", "one", "two", "three"])
        (normalized, count) = FlowRun.normalize_fields(fields)
        self.assertEquals(5, count)
        self.assertEquals(dict(numbers={'0': "zero", '1': "one", '2': "two", '3': "three"}), normalized)

        fields = dict(united_states=dict(wa="Washington", nv="Nevada"), states=50)
        (normalized, count) = FlowRun.normalize_fields(fields)
        self.assertEquals(4, count)
        self.assertEquals(fields, normalized)

    def test_update_fields(self):
        run = FlowRun.create(self.flow, self.contact)

        # set our fields from an empty state
        new_values = dict(field1="value1", field2="value2")
        run.update_fields(new_values)

        new_values['__default__'] = 'field1: value1, field2: value2'

        self.assertEquals(run.field_dict(), new_values)

        run.update_fields(dict(field2="new value2", field3="value3"))
        new_values['field2'] = "new value2"
        new_values['field3'] = "value3"

        new_values['__default__'] = 'field1: value1, field2: new value2, field3: value3'

        self.assertEquals(run.field_dict(), new_values)

        run.update_fields(dict(field1=""))
        new_values['field1'] = ""
        new_values['__default__'] = 'field1: , field2: new value2, field3: value3'

        self.assertEquals(run.field_dict(), new_values)

    def test_is_completed(self):
        self.flow.start([], [self.contact])

        self.assertFalse(FlowRun.objects.get(contact=self.contact).is_completed())

        incoming = self.create_msg(direction=INCOMING, contact=self.contact, text="orange")
        Flow.find_and_handle(incoming)

        self.assertTrue(FlowRun.objects.get(contact=self.contact).is_completed())


class FlowLabelTest(SmartminTest):
    def setUp(self):
        self.user = self.create_user("tito")
        self.org = Org.objects.create(name="Nyaruka Ltd.", timezone="Africa/Kigali", created_by=self.user, modified_by=self.user)
        self.org.administrators.add(self.user)
        self.user.set_org(self.org)

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
        self.login(self.user)
        response = self.client.get(reverse('flows.flow_filter', args=[label.pk]))
        self.assertContains(response, "child")

    def test_create(self):
        create_url = reverse('flows.flowlabel_create')

        post_data = dict(name="label_one")

        self.login(self.user)
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

        self.login(self.user)
        response = self.client.get(delete_url)
        self.assertEquals(response.status_code, 200)


class WebhookTest(TembaTest):

    def setUp(self):
        super(WebhookTest, self).setUp()
        settings.SEND_WEBHOOKS = True

    def tearDown(self):
        super(WebhookTest, self).tearDown()
        settings.SEND_WEBHOOKS = False

    def test_webhook(self):
        self.flow = self.create_flow()
        self.contact = self.create_contact("Ben Haggerty", '+250788383383')

        run = FlowRun.create(self.flow, self.contact)

        # webhook ruleset comes first
        webhook = RuleSet.objects.create(flow=self.flow, uuid=uuid(100), x=0, y=0, ruleset_type=RuleSet.TYPE_WEBHOOK)
        webhook.webhook_url = "http://ordercheck.com/check_order.php?phone=@step.contact.tel_e164"
        webhook.webhook_action = "GET"
        webhook.set_rules_dict([Rule(uuid(15), dict(base="All Responses"), uuid(200), 'R', TrueTest()).as_json()])
        webhook.save()

        # and a ruleset to split off the results
        rules = RuleSet.objects.create(flow=self.flow, uuid=uuid(200), x=0, y=200, ruleset_type=RuleSet.TYPE_EXPRESSION)
        rules.set_rules_dict([Rule(uuid(12), dict(base="Valid"), uuid(2), 'A', ContainsTest(dict(base="valid"))).as_json(),
                              Rule(uuid(13), dict(base="Invalid"), uuid(3), 'A', ContainsTest(dict(base="invalid"))).as_json()])
        rules.save()

        webhook_step = FlowStep.objects.create(run=run, contact=run.contact, step_type=RULE_SET,
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
                self.assertEquals(dict(__default__='blank: , text: Get', text="Get", blank=""), run.field_dict())

                # assert our phone number got encoded
                self.assertEquals("http://ordercheck.com/check_order.php?phone=%2B250788383383", get.call_args[0][0])

                # now do a POST
                webhook.webhook_action = "POST"
                webhook.save()
                webhook.find_matching_rule(webhook_step, run, incoming)
                self.assertEquals(dict(__default__='blank: , text: Post', text="Post", blank=""), run.field_dict())

                self.assertEquals("http://ordercheck.com/check_order.php?phone=%2B250788383383", post.call_args[0][0])

        # remove @extra.blank from our text
        rules.operand = "@extra.text"
        rules.save()

        # clear our run's field dict
        run.fields = json.dumps(dict())
        run.save()

        rule_step = FlowStep.objects.create(run=run, contact=run.contact, step_type=RULE_SET,
                                       step_uuid=rules.uuid, arrived_on=timezone.now())

        with patch('requests.post') as mock:
            mock.return_value = MockResponse(200, '{ "text": "Valid" }')

            (match, value) = webhook.find_matching_rule(webhook_step, run, incoming)
            (match, value) = rules.find_matching_rule(rule_step, run, incoming)

            self.assertEquals(uuid(12), match.uuid)
            self.assertEquals("Valid", value)
            self.assertEquals(dict(__default__='text: Valid', text="Valid"), run.field_dict())

        with patch('requests.post') as mock:
            mock.return_value = MockResponse(200, '{ "text": "Valid", "order_number": "PX1001" }')

            (match, value) = webhook.find_matching_rule(webhook_step, run, incoming)
            (match, value) = rules.find_matching_rule(rule_step, run, incoming)
            self.assertEquals(uuid(12), match.uuid)
            self.assertEquals("Valid", value)
            self.assertEquals(dict(__default__='order_number: PX1001, text: Valid', text="Valid", order_number="PX1001"), run.field_dict())

            message_context = self.flow.build_message_context(self.contact, incoming)
            self.assertEquals(dict(text="Valid", order_number="PX1001", __default__='order_number: PX1001, text: Valid'), message_context['extra'])

        with patch('requests.post') as mock:
            mock.return_value = MockResponse(200, '{ "text": "Valid", "order_number": "PX1002" }')

            (match, value) = webhook.find_matching_rule(webhook_step, run, incoming)
            (match, value) = rules.find_matching_rule(rule_step, run, incoming)
            self.assertEquals(uuid(12), match.uuid)
            self.assertEquals("Valid", value)
            self.assertEquals(dict(__default__='order_number: PX1002, text: Valid', text="Valid", order_number="PX1002"), run.field_dict())

            message_context = self.flow.build_message_context(self.contact, incoming)
            self.assertEquals(dict(text="Valid", order_number="PX1002", __default__='order_number: PX1002, text: Valid'), message_context['extra'])

        with patch('requests.post') as mock:
            mock.return_value = MockResponse(200, "asdfasdfasdf")
            rule_step.run.fields = None
            rule_step.run.save()

            (match, value) = webhook.find_matching_rule(webhook_step, run, incoming)
            (match, value) = rules.find_matching_rule(rule_step, run, incoming)
            self.assertIsNone(match)
            self.assertIsNone(value)
            self.assertEquals("1001", incoming.text)

            message_context = self.flow.build_message_context(self.contact, incoming)
            self.assertEquals({'__default__': ''}, message_context['extra'])

        with patch('requests.post') as mock:
            mock.return_value = MockResponse(200, "12345")
            rule_step.run.fields = None
            rule_step.run.save()

            (match, value) = webhook.find_matching_rule(webhook_step, run, incoming)
            (match, value) = rules.find_matching_rule(rule_step, run, incoming)
            self.assertIsNone(match)
            self.assertIsNone(value)
            self.assertEquals("1001", incoming.text)

            message_context = self.flow.build_message_context(self.contact, incoming)
            self.assertEquals({'__default__': ''}, message_context['extra'])

        with patch('requests.post') as mock:
            mock.return_value = MockResponse(500, "Server Error")
            rule_step.run.fields = None
            rule_step.run.save()

            (match, value) = webhook.find_matching_rule(webhook_step, run, incoming)
            (match, value) = rules.find_matching_rule(rule_step, run, incoming)
            self.assertIsNone(match)
            self.assertIsNone(value)
            self.assertEquals("1001", incoming.text)


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
        json_dict = json.loads(response.content)

        self.assertEquals(len(json_dict.keys()), 5)
        self.assertEquals(len(json_dict['messages']), 2)
        self.assertEquals('Ben Haggerty has entered the &quot;Pick a Number&quot; flow', json_dict['messages'][0]['text'])
        self.assertEquals("Pick a number between 1-10.", json_dict['messages'][1]['text'])

        post_data['new_message'] = "3"
        post_data['has_refresh'] = False

        response = self.client.post(simulate_url, json.dumps(post_data), content_type="application/json")
        self.assertEquals(200, response.status_code)
        json_dict = json.loads(response.content)

        self.assertEquals(len(json_dict['messages']), 6)
        self.assertEquals("3", json_dict['messages'][2]['text'])
        self.assertEquals("Saved &#39;3&#39; as @flow.number", json_dict['messages'][3]['text'])
        self.assertEquals("You picked 3!", json_dict['messages'][4]['text'])
        self.assertEquals('Ben Haggerty has exited this flow', json_dict['messages'][5]['text'])


class FlowsTest(FlowFileTest):

    def clear_activity(self, flow):
        r = get_redis_connection()
        flow.clear_stats_cache()

    def test_sms_forms(self):
        flow = self.get_flow('sms-form')

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

    def test_get_columns_order(self):
        flow = self.get_flow('columns-order')

        export_columns = flow.get_columns()
        self.assertEquals(export_columns[0], RuleSet.objects.filter(flow=flow, label='Beer').first())
        self.assertEquals(export_columns[1], RuleSet.objects.filter(flow=flow, label='Name').first())
        self.assertEquals(export_columns[2], RuleSet.objects.filter(flow=flow, label='Color').first())

    def test_recent_messages(self):
        flow = self.get_flow('favorites')

        self.login(self.admin)
        recent_messages_url = reverse('flows.flow_recent_messages', args=[flow.pk])
        response = self.client.get(recent_messages_url)
        self.assertEquals([], json.loads(response.content))

        actionset = ActionSet.objects.filter(flow=flow, y=0).first()
        first_action_set_uuid = actionset.uuid
        first_action_set_destination = actionset.destination

        ruleset = RuleSet.objects.filter(flow=flow, label='Color').first()
        first_ruleset_uuid = ruleset.uuid

        other_rule = ruleset.get_rules()[-1]
        other_rule_destination = other_rule.destination
        other_rule_uuid = other_rule.uuid

        blue_rule = ruleset.get_rules()[-2]
        blue_rule_uuid = blue_rule.uuid
        blue_rule_destination = blue_rule.destination

        # use the right get params
        self.send_message(flow, 'chartreuse')
        get_params_entry = "?step=%s&destination=%s&rule=%s" % (first_action_set_uuid, first_action_set_destination, '')
        response = self.client.get(recent_messages_url + get_params_entry)
        response_json = json.loads(response.content)
        self.assertTrue(response_json)
        self.assertEquals(1, len(response_json))
        self.assertEquals("What is your favorite color?", response_json[0].get('text'))

        get_params_other_rule = "?step=%s&destination=%s&rule=%s" % (first_ruleset_uuid, other_rule_destination, other_rule_uuid)
        response = self.client.get(recent_messages_url + get_params_other_rule)
        response_json = json.loads(response.content)
        self.assertTrue(response_json)
        self.assertEquals(1, len(response_json))
        self.assertEquals("chartreuse", response_json[0].get('text'))

        # nothing yet for blue
        get_params_blue_rule = "?step=%s&destination=%s&rule=%s" % (first_ruleset_uuid, blue_rule_destination, blue_rule_uuid)
        response = self.client.get(recent_messages_url + get_params_blue_rule)
        self.assertEquals([], json.loads(response.content))

        # mixed wrong params
        get_params_mixed = "?step=%s&destination=%s&rule=%s" % (first_ruleset_uuid, first_action_set_destination, '')
        response = self.client.get(recent_messages_url + get_params_mixed)
        self.assertEquals([], json.loads(response.content))

        self.send_message(flow, 'mauve')

        response = self.client.get(recent_messages_url + get_params_entry)
        response_json = json.loads(response.content)
        self.assertTrue(response_json)
        self.assertEquals(1, len(response_json))
        self.assertEquals("What is your favorite color?", response_json[0].get('text'))

        response = self.client.get(recent_messages_url + get_params_other_rule)
        response_json = json.loads(response.content)
        self.assertTrue(response_json)
        self.assertEquals(2, len(response_json))
        self.assertEquals("mauve", response_json[0].get('text'))
        self.assertEquals("chartreuse", response_json[1].get('text'))

        response = self.client.get(recent_messages_url + get_params_blue_rule)
        self.assertEquals([], json.loads(response.content))

        response = self.client.get(recent_messages_url + get_params_mixed)
        self.assertEquals([], json.loads(response.content))

        self.send_message(flow, 'blue')

        response = self.client.get(recent_messages_url + get_params_entry)
        response_json = json.loads(response.content)
        self.assertTrue(response_json)
        self.assertEquals(1, len(response_json))
        self.assertEquals("What is your favorite color?", response_json[0].get('text'))

        response = self.client.get(recent_messages_url + get_params_other_rule)
        response_json = json.loads(response.content)
        self.assertTrue(response_json)
        self.assertEquals(2, len(response_json))
        self.assertEquals("mauve", response_json[0].get('text'))
        self.assertEquals("chartreuse", response_json[1].get('text'))

        response = self.client.get(recent_messages_url + get_params_blue_rule)
        response_json = json.loads(response.content)
        self.assertTrue(response_json)
        self.assertEquals(1, len(response_json))
        self.assertEquals("blue", response_json[0].get('text'))

        response = self.client.get(recent_messages_url + get_params_mixed)
        self.assertEquals([], json.loads(response.content))

    def test_completion(self):

        flow = self.get_flow('favorites')
        self.login(self.admin)

        response = self.client.get('%s?flow=%d' % (reverse('flows.flow_completion'), flow.pk))
        response = json.loads(response.content)

        def assertInResponse(response, data_key, key):
            found = False
            for item in response[data_key]:
                if key == item['name']:
                    found = True
            self.assertTrue(found, 'Key %s not found in %s' % (key, response))

        assertInResponse(response, 'message_completions', 'contact')
        assertInResponse(response, 'message_completions', 'contact.first_name')
        assertInResponse(response, 'message_completions', 'flow.color')
        assertInResponse(response, 'message_completions', 'flow.color.category')
        assertInResponse(response, 'message_completions', 'flow.color.text')
        assertInResponse(response, 'message_completions', 'flow.color.time')

        assertInResponse(response, 'function_completions', 'SUM')
        assertInResponse(response, 'function_completions', 'ABS')
        assertInResponse(response, 'function_completions', 'YEAR')

    def test_expiration(self):
        flow = self.get_flow('favorites')
        color = RuleSet.objects.get(label='Color', flow=flow)
        self.clear_activity(flow)

        contacts = []
        for i in range(6):
            contacts.append(self.create_contact("Run Contact %d" % i, "+25078838338%d" % i))

        # add our contacts to the flow
        for contact in contacts:
            self.send_message(flow, 'chartreuse', contact=contact)

        # should have six active flowruns
        (active, visited) = flow.get_activity()
        self.assertEquals(6, FlowRun.objects.filter(is_active=True).count())
        self.assertEquals(0, FlowRun.objects.filter(is_active=False).count())
        self.assertEquals(6, flow.get_total_runs())
        self.assertEquals(6, flow.get_total_contacts())
        self.assertEquals(6, active[color.uuid])

        # go expire them all
        FlowRun.do_expire_runs(FlowRun.objects.filter(is_active=True), batch_size=4)

        # should all be expired
        (active, visited) = flow.get_activity()
        self.assertEquals(0, FlowRun.objects.filter(is_active=True).count())
        self.assertEquals(6, FlowRun.objects.filter(is_active=False).count())
        self.assertEquals(6, flow.get_total_runs())
        self.assertEquals(6, flow.get_total_contacts())
        self.assertEquals(0, len(active))

    def test_activity(self):

        flow = self.get_flow('favorites')

        # clear our previous redis activity
        self.clear_activity(flow)

        other_action = ActionSet.objects.get(y=8, flow=flow)
        beer = RuleSet.objects.get(label='Beer', flow=flow)
        color = RuleSet.objects.get(label='Color', flow=flow)
        color_other_uuid = color.get_rules()[-1].uuid

        other_rule_to_msg = '%s:%s' % (color_other_uuid, other_action.uuid)
        msg_to_color_step = '%s:%s' % (other_action.uuid, color.uuid)

        # we don't know this shade of green, it should route us to the beginning again
        self.send_message(flow, 'chartreuse')
        (active, visited) = flow.get_activity()

        self.assertEquals(1, len(active))
        self.assertEquals(1, active[color.uuid])
        self.assertEquals(1, visited[other_rule_to_msg])
        self.assertEquals(1, visited[msg_to_color_step])
        self.assertEquals(1, flow.get_total_runs())
        self.assertEquals(1, flow.get_total_contacts())
        self.assertEquals(0, flow.get_completed_runs())
        self.assertEquals(0, flow.get_completed_percentage())

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

        # a new participant, showing distinct active counts and incremented path
        ryan = self.create_contact('Ryan Lewis', '+12065550725')
        self.send_message(flow, 'burnt sienna', contact=ryan)
        (active, visited) = flow.get_activity()
        self.assertEquals(2, len(active))
        self.assertEquals(1, active[color.uuid])
        self.assertEquals(1, active[beer.uuid])
        self.assertEquals(3, visited[other_rule_to_msg])
        self.assertEquals(3, visited[msg_to_color_step])
        self.assertEquals(2, flow.get_total_runs())
        self.assertEquals(2, flow.get_total_contacts())

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
        self.assertEquals(1, flow.get_completed_runs())
        self.assertEquals(50, flow.get_completed_percentage())

        # rebuild our flow stats and make sure they are the same
        flow.do_calculate_flow_stats()
        (active, visited) = flow.get_activity()
        self.assertEquals(1, len(active))
        self.assertEquals(3, visited[other_rule_to_msg])
        self.assertEquals(1, flow.get_completed_runs())
        self.assertEquals(50, flow.get_completed_percentage())

        # we are going to expire, but we want runs across two different flows
        # to make sure that our optimization for expiration is working properly
        cga_flow = self.get_flow('color_gender_age')
        self.assertEquals("What is your gender?", self.send_message(cga_flow, "Red"))
        self.assertEquals(1, len(cga_flow.get_activity()[0]))

        # expire the first contact's runs
        FlowRun.do_expire_runs(FlowRun.objects.filter(contact=self.contact))

        # no active runs for our contact
        self.assertEquals(0, FlowRun.objects.filter(contact=self.contact, is_active=True).count())

        # both of our flows should have reduced active contacts
        self.assertEquals(0, len(cga_flow.get_activity()[0]))

        # now we should only have one node with active runs, but the paths stay
        # the same since those are historical
        (active, visited) = flow.get_activity()
        self.assertEquals(1, len(active))
        self.assertEquals(3, visited[other_rule_to_msg])

        # our completion stats should remain the same
        self.assertEquals(1, flow.get_completed_runs())
        self.assertEquals(50, flow.get_completed_percentage())

        # our completion stats should remain the same
        self.assertEquals(1, flow.get_completed_runs())
        self.assertEquals(50, flow.get_completed_percentage())

        # check that we have the right number of steps and runs
        self.assertEquals(17, FlowStep.objects.filter(run__flow=flow).count())
        self.assertEquals(2, FlowRun.objects.filter(flow=flow).count())

        # now let's delete our contact, we'll still have one active node, but
        # our visit path counts will go down by two since he went there twice
        self.contact.release()
        (active, visited) = flow.get_activity()
        self.assertEquals(1, len(active))
        self.assertEquals(1, visited[msg_to_color_step])
        self.assertEquals(1, visited[other_rule_to_msg])
        self.assertEquals(1, flow.get_total_runs())
        self.assertEquals(1, flow.get_total_contacts())

        # he was also accounting for our completion rate, back to nothing
        self.assertEquals(0, flow.get_completed_runs())
        self.assertEquals(0, flow.get_completed_percentage())

        # advance ryan to the end to make sure our percentage accounts for one less contact
        self.send_message(flow, 'Turbo King', contact=ryan)
        self.send_message(flow, 'Ryan Lewis', contact=ryan)
        (active, visited) = flow.get_activity()
        self.assertEquals(0, len(active))
        self.assertEquals(1, flow.get_completed_runs())
        self.assertEquals(100, flow.get_completed_percentage())

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
        self.assertEquals(1, flow.get_total_runs())
        self.assertEquals(1, flow.get_total_contacts())
        self.assertEquals(1, flow.get_completed_runs())
        self.assertEquals(100, flow.get_completed_percentage())

        # but hammer should have created some simulation activity
        (active, visited) = flow.get_activity(simulation=True)
        self.assertEquals(0, len(active))
        self.assertEquals(2, visited[msg_to_color_step])
        self.assertEquals(2, visited[other_rule_to_msg])

        # delete our last contact to make sure activity is gone without first expiring, zeros abound
        ryan.release()
        (active, visited) = flow.get_activity()
        self.assertEquals(0, len(active))
        self.assertEquals(0, visited[msg_to_color_step])
        self.assertEquals(0, visited[other_rule_to_msg])
        self.assertEquals(0, flow.get_total_runs())
        self.assertEquals(0, flow.get_total_contacts())
        self.assertEquals(0, flow.get_completed_runs())
        self.assertEquals(0, flow.get_completed_percentage())

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
        self.assertEquals(1, flow.get_total_runs())
        self.assertEquals(1, flow.get_total_contacts())

        # set the run to be ready for expiration
        run = tupac.runs.first()
        run.expires_on = timezone.now() - timedelta(days=1)
        run.save()

        # now trigger the checking task and make sure it is removed from our activity
        from .tasks import check_flows_task
        check_flows_task()
        (active, visited) = flow.get_activity()
        self.assertEquals(0, len(active))
        self.assertEquals(1, flow.get_total_runs())
        self.assertEquals(1, flow.get_total_contacts())

    def test_destination_type(self):

        flow = self.get_flow('pick_a_number')

        # our start points to a ruleset
        start = ActionSet.objects.get(flow=flow, y=0)
        self.assertEquals(RULE_SET, start.destination_type)

        # and that ruleset points to an actionset
        ruleset = RuleSet.objects.get(uuid=start.destination)
        rule = ruleset.get_rules()[0]
        self.assertEquals(ACTION_SET, rule.destination_type)

        # point our rule to a ruleset
        passive = RuleSet.objects.get(flow=flow, label='passive')
        self.update_destination(flow, rule.uuid, passive.uuid)
        ruleset = RuleSet.objects.get(uuid=start.destination)
        self.assertEquals(RULE_SET, ruleset.get_rules()[0].destination_type)

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
        handled = Flow.find_and_handle(incoming)
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

    def test_rule_changes_under_us(self):
        flow = self.get_flow('favorites')
        self.send_message(flow, "RED", restart_participants=True)

        # at this point we are waiting for the response to the second question about beer

        # let's change that ruleset to instead be based on the contact name
        group_ruleset = RuleSet.objects.get(flow=flow, label='Beer')

        group_ruleset.operand = "@contact.beer"
        group_ruleset.ruleset_type = RuleSet.TYPE_CONTACT_FIELD
        group_ruleset.save()

        self.contact.set_field("beer", "Mutzig")

        # and send our last message with our name, we should:
        # 1) get fast forwarded to the next waiting ruleset about our name and have our message applied to that
        # 2) get an outgoing message about our beer choice
        # 3) get an outgoing message about our name
        responses = self.send_message(flow, "Eric")
        self.assertEquals(2, len(responses))
        self.assertEquals("Mmmmm... delicious Mutzig. If only they made red Mutzig! Lastly, what is your name?",
                          responses[0])
        self.assertEquals("Thanks Eric, we are all done!",
                          responses[1])

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
        with self.assertRaises(FlowException):
            self.send_message(flow, "1", assert_reply=False)

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
        with self.assertRaises(FlowException):
            self.send_message(flow, "2", assert_reply=False)

        flow.delete()

    def test_decimal_substitution(self):
        flow = self.get_flow('pick_a_number')
        self.assertEquals("You picked 3!", self.send_message(flow, "3"))

    def test_rules_first(self):
        flow = self.get_flow('rules_first')
        self.assertEquals(Flow.RULES_ENTRY, flow.entry_type)
        self.assertEquals("You've got to be kitten me", self.send_message(flow, "cats"))

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

    def test_substitution(self):
        flow = self.get_flow('substitution')

        self.contact.name = "Ben Haggerty"
        self.contact.save()

        runs = flow.start_msg_flow(Contact.objects.filter(id=self.contact.id))
        self.assertEquals(1, len(runs))
        self.assertEquals(1, self.contact.msgs.all().count())
        self.assertEquals('Hi Ben Haggerty, what is your phone number?', self.contact.msgs.all()[0].text)

        self.assertEquals("Thanks, you typed +250788123123", self.send_message(flow, "0788123123"))
        sms = Msg.objects.get(org=flow.org, contact__urns__path="+250788123123")
        self.assertEquals("Hi from Ben Haggerty! Your phone is 0788 123 123.", sms.text)

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

        mother = Contact.from_urn(self.org, TEL_SCHEME, "+250788383383")
        self.assertEquals("Judy Pottier", mother.name)
        self.assertTrue(mother.get_field_raw('expected_delivery_date').startswith('31-01-2014'))
        self.assertEquals("+12065552020", mother.get_field_raw('chw'))
        self.assertTrue(mother.user_groups.filter(name="Expecting Mothers"))

        pain_flow = self.get_flow('pain_flow')
        self.assertEquals("Your CHW will be in contact soon!", self.send_message(pain_flow, "yes", contact=mother))

        chw = self.contact
        sms = Msg.objects.filter(contact=chw).order_by('-created_on')[0]
        self.assertEquals("Please follow up with Judy Pottier, she has reported she is in pain.", sms.text)

    def test_flow_export_results(self):
        self.clear_storage()

        mother_flow = self.get_flow('new_mother')
        registration_flow = self.get_flow('mother_registration', dict(NEW_MOTHER_FLOW_ID=mother_flow.pk))

        # start our test contact down the flow
        self.assertEquals("What is her expected delivery date?",
                          self.send_message(registration_flow, "Test Mother", contact=Contact.get_test_contact(self.admin)))

        # then a real contact
        self.assertEquals("What is her expected delivery date?", self.send_message(registration_flow, "Judy Pottier"))
        self.assertEquals("That doesn't look like a valid date, try again.", self.send_message(registration_flow, "NO"))
        self.assertEquals("What is her phone number?", self.send_message(registration_flow, "31.1.2014"))
        self.assertEquals("Great, you've registered the new mother!", self.send_message(registration_flow, "0788 383 383"))

        # export the flow
        task = ExportFlowResultsTask.objects.create(org=self.org, created_by=self.admin, modified_by=self.admin)
        task.flows.add(registration_flow)
        task.do_export()

        task = ExportFlowResultsTask.objects.get(pk=task.id)

        # read it back in, check values
        from xlrd import open_workbook
        filename = "%s/test_orgs/%d/results_exports/%s.xls" % (settings.MEDIA_ROOT, self.org.pk, task.uuid)
        workbook = open_workbook(os.path.join(settings.MEDIA_ROOT, filename), 'rb')

        self.assertEquals(3, len(workbook.sheets()))
        entries = workbook.sheets()[0]
        self.assertEquals(2, entries.nrows)
        self.assertEquals(14, entries.ncols)

        # make sure our date hour is correct in our current timezone, we only look at hour as that
        # is what changes per timezone
        org_timezone = pytz.timezone(self.org.timezone)
        org_now = timezone.now().astimezone(org_timezone)
        self.assertEquals(org_now.hour, xldate_as_tuple(entries.cell(1, 4).value, 0)[3])

        # name category and value and raw
        self.assertEquals("All Responses", entries.cell(1, 5).value)
        self.assertEquals("Judy Pottier", entries.cell(1, 6).value)
        self.assertEquals("Judy Pottier", entries.cell(1, 7).value)

        # EDD category and value and raw
        self.assertEquals("is a date", entries.cell(1, 8).value)
        self.assertTrue(entries.cell(1, 9).value.startswith("31-01-2014"))
        self.assertEquals("31.1.2014", entries.cell(1, 10).value)

        # Phone category and value and raw
        self.assertEquals("phone", entries.cell(1, 11).value)
        self.assertEquals("+250788383383", entries.cell(1, 12).value)
        self.assertEquals("0788 383 383", entries.cell(1, 13).value)

        messages = workbook.sheets()[2]
        self.assertEquals(10, messages.nrows)
        self.assertEquals(6, messages.ncols)

        self.assertEqual(messages.cell(0, 0).value, "Phone")
        self.assertEqual(messages.cell(0, 1).value, "Name")
        self.assertEqual(messages.cell(0, 2).value, "Date")
        self.assertEqual(messages.cell(0, 3).value, "Direction")
        self.assertEqual(messages.cell(0, 4).value, "Message")
        self.assertEqual(messages.cell(0, 5).value, "Channel")

        self.assertEqual(messages.cell(1, 0).value, "+12065552020")
        self.assertEqual(messages.cell(1, 1).value, "Ben Haggerty")
        self.assertEqual(messages.cell(1, 3).value, "OUT")
        self.assertEqual(messages.cell(1, 4).value, "Thanks for registering a new mother, what is her name?")
        self.assertEqual(messages.cell(1, 5).value, "Test Channel")

        # assert the time is correct here as well
        self.assertEquals(org_now.hour, xldate_as_tuple(entries.cell(1, 3).value, 0)[3])

    def test_flow_export(self):
        flow = self.get_flow('favorites')

        # now let's export it
        self.login(self.admin)
        response = self.client.get(reverse('flows.flow_export', args=[flow.pk]))
        modified_on = flow.modified_on
        self.assertEquals(200, response.status_code)

        definition = json.loads(response.content)
        self.assertEqual(CURRENT_EXPORT_VERSION, definition.get('version', 0))
        self.assertEqual(1, len(definition.get('flows', [])))

        # try importing it and see that we have an updated flow
        Flow.import_flows(definition, self.org, self.admin)
        flow = Flow.objects.filter(name="%s" % flow.name).first()
        self.assertIsNotNone(flow)
        self.assertNotEqual(modified_on, flow.modified_on)

        # don't allow exports that reference other flows
        new_mother = self.get_flow('new_mother')
        flow = self.get_flow('references_other_flows',
                             substitutions=dict(START_FLOW=new_mother.pk,
                                                TRIGGER_FLOW=self.get_flow('pain_flow').pk))
        response = self.client.get(reverse('flows.flow_export', args=[flow.pk]))
        self.assertContains(response, "Sorry, this flow cannot be exported")
        self.assertContains(response, "New Mother")
        self.assertContains(response, "Pain Flow")

        # now try importing it into a completey different org
        trey = self.create_user("Trey Anastasio")
        trey_org = Org.objects.create(name="Gotta Jiboo", timezone="Africa/Kigali", created_by=trey, modified_by=trey)
        trey_org.administrators.add(trey)

        response = self.client.get(reverse('flows.flow_export', args=[new_mother.pk]))
        definition = json.loads(response.content)

        Flow.import_flows(definition, trey_org, trey)
        self.assertIsNotNone(Flow.objects.filter(org=trey_org, name="New Mother").first())

    def test_start_flow_action(self):
        self.import_file('flow-starts')
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

    def test_cross_language_import(self):
        spanish = Language.create(self.org, self.admin, "Spanish", 'spa')
        english = Language.create(self.org, self.admin, "English", 'eng')

        # import our localized flow into an org with no languages
        self.import_file('multi-language-flow')
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
        self.assertEquals("Mmmmm... delicious Turbo King. If only they made red Turbo King! Lastly, what is your name?", self.send_message(flow, "turbo"))

        # our new expiration should be later
        run = flow.runs.get()
        self.assertTrue(run.expires_on > starting_expiration)
        self.assertTrue(run.modified_on > starting_modified)

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
        flow.update_run_expirations()

        # check that our run is expired
        run = flow.runs.all()[0]
        self.assertFalse(run.is_active)

        # we will be starting a new run now, since the other expired
        self.assertEquals("I don't know that color. Try again.",
                          self.send_message(flow, "Michael Jordan", restart_participants=True))
        self.assertEquals(2, flow.runs.count())

    def test_parsing(self):

        # test a preprocess url
        flow = self.get_flow('preprocess')
        self.assertEquals('http://preprocessor.com/endpoint.php', flow.rule_sets.all().order_by('y')[0].webhook_url)

    def test_flow_loops(self):
        # this tests two flows that start each other
        flow1 = self.create_flow()
        flow2 = self.create_flow()

        # create an action on flow1 to start flow2
        flow1.update(dict(action_sets=[dict(uuid=uuid(1), x=1, y=1,
                                            actions=[dict(type='flow', id=flow2.pk)])]))
        flow2.update(dict(action_sets=[dict(uuid=uuid(2), x=1, y=1,
                                            actions=[dict(type='flow', id=flow1.pk)])]))

        # start the flow, shouldn't get into a loop, but both should get started
        flow1.start([], [self.contact])

        self.assertTrue(FlowRun.objects.get(flow=flow1, contact=self.contact))
        self.assertTrue(FlowRun.objects.get(flow=flow2, contact=self.contact))

    def test_ruleset_loops(self):
        self.import_file('ruleset_loop')

        flow1=Flow.objects.all()[1]
        flow2=Flow.objects.all()[0]

        # start the flow, should not get into a loop
        flow1.start([], [self.contact])

        self.assertTrue(FlowRun.objects.get(flow=flow1, contact=self.contact))
        self.assertTrue(FlowRun.objects.get(flow=flow2, contact=self.contact))

    def test_parent_child(self):
        from temba.campaigns.models import Campaign, CampaignEvent, EventFire

        favorites = self.get_flow('favorites')

        # do a dry run once so that the groups and fields get created
        group = self.create_group("Campaign", [])
        field = ContactField.get_or_create(self.org, "campaign_date", "Campaign Date")

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
        runs = flow.start_msg_flow(Contact.objects.filter(id=self.contact.id))
        self.assertEquals(1, len(runs))
        self.assertEquals(1, self.contact.msgs.all().count())
        self.assertEquals('You are not in the enrolled group.', self.contact.msgs.all()[0].text)

        enrolled_group = ContactGroup.create(self.org, self.user, "Enrolled")
        enrolled_group.update_contacts([self.contact], True)

        runs_started = flow.start_msg_flow(Contact.objects.filter(id=self.contact.id))
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
        self.assertTrue(isinstance(reply['msg']['base'], unicode))

        # now our replies are language dicts
        json_dict = favorites.as_json()
        reply = json_dict['action_sets'][1]['actions'][0]
        self.assertEquals('Good choice, I like @flow.color.category too! What is your favorite beer?', reply['msg']['base'])

        # now interact with the flow and make sure we get an appropriate resonse
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
        action['contacts'] = [dict(id=self.contact.pk)]
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

        flow_json = FlowRevision.migrate_definition(flow_json, flow.version_number, to_version=to_version)
        if 'definition' in flow_json:
            flow_json = flow_json['definition']

        flow.update(flow_json)
        return Flow.objects.get(pk=flow.pk)

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

    def test_ensure_current_version(self):
        flow_json = self.get_flow_json('call-me-maybe')['definition']
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

    def test_migrate_to_8(self):
        # file uses old style expressions
        flow_json = self.get_flow_json('old-expressions')

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
        flow_json = self.get_flow_json('call-me-maybe')

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
        voice_json = self.get_flow_json('call-me-maybe')
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

    def test_migrate_to_5_language(self):

        flow_json = self.get_flow_json('multi-language-flow')
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

    def test_migrate_to_5(self):

        settings.SEND_WEBHOOKS = True

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
        flow = self.migrate_flow(flow, to_version=5)

        # we should be sitting at a wait node
        ruleset = RuleSet.objects.get(uuid=step.step_uuid)
        self.assertEquals('wait_message', ruleset.ruleset_type)
        self.assertEquals('@step.value', ruleset.operand)

        # we should be pointing to a newly created webhook rule
        webhook = RuleSet.objects.get(flow=flow, uuid=ruleset.get_rules()[0].destination)
        self.assertEquals('webhook', webhook.ruleset_type)
        self.assertEquals('http://www.mywebhook.com/lookup', webhook.webhook_url)
        self.assertEquals('POST', webhook.webhook_action)
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
        self.assertEquals('@step.value|lower_case', beer_expression.operand)
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
        self.assertEquals('base', poll.base_language)

        # check replacement
        order_checker = self.org.flows.filter(name='Sample Flow - Order Status Checker').first()
        ruleset = order_checker.rule_sets.filter(y=298).first()
        self.assertEquals('https://app.rapidpro.io/demo/status/', ruleset.webhook_url)

        # our test user doesn't use an email address, check for Administrator for the email
        actionset = order_checker.action_sets.filter(y=991).first()
        self.assertEquals('Administrator', actionset.get_actions()[1].emails[0])


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
        flow = self.get_flow('call-channel-split')

        # trigger a missed call on our channel
        call = Call.create_call(self.channel, '+250788111222', timezone.now(), 0, Call.TYPE_IN_MISSED)

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
        self.assertTrue(Flow.find_and_handle(incoming))

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




