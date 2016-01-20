# -*- coding: utf-8 -*-
from __future__ import absolute_import, unicode_literals

import json
import pytz
import xml.etree.ElementTree as ET

from datetime import datetime, timedelta
from django.conf import settings
from django.contrib.auth.models import Group
from django.core.urlresolvers import reverse
from django.db import connection
from django.test.utils import override_settings
from django.utils import timezone
from django.utils.http import urlquote_plus
from mock import patch
from rest_framework.authtoken.models import Token
from rest_framework.exceptions import ValidationError
from rest_framework.test import APIClient
from temba.campaigns.models import Campaign, CampaignEvent, MESSAGE_EVENT, FLOW_EVENT
from temba.channels.models import Channel, SyncEvent
from temba.contacts.models import Contact, ContactField, ContactGroup, TEL_SCHEME, TWITTER_SCHEME
from temba.flows.models import Flow, FlowLabel, FlowRun, RuleSet, ActionSet, RULE_SET
from temba.msgs.models import Broadcast, Call, Msg, Label, FAILED, ERRORED, VISIBLE, ARCHIVED, DELETED
from temba.orgs.models import Org, Language
from temba.tests import TembaTest, AnonymousOrg
from temba.utils import datetime_to_json_date
from temba.values.models import Value, DATETIME
from ..models import APIToken
from ..v1.serializers import StringDictField, StringArrayField, PhoneArrayField, ChannelField, DateTimeField


class APITest(TembaTest):

    def setUp(self):
        super(APITest, self).setUp()

        self.joe = self.create_contact("Joe Blow", "0788123123")

        self.channel2 = Channel.create(None, self.admin, 'RW', 'A', "Unclaimed Channel",
                                       claim_code="123123123", secret="123456", gcm_id="1234")

        self.call1 = Call.objects.create(contact=self.joe,
                                         channel=self.channel,
                                         org=self.org,
                                         call_type='mt_miss',
                                         time=timezone.now(),
                                         created_by=self.admin,
                                         modified_by=self.admin)

        settings.SECURE_PROXY_SSL_HEADER = ('HTTP_X_FORWARDED_HTTPS', 'https')
        settings.SESSION_COOKIE_SECURE = True

        # this is needed to prevent REST framework from rolling back transaction created around each unit test
        connection.settings_dict['ATOMIC_REQUESTS'] = False

    def tearDown(self):
        super(APITest, self).tearDown()
        settings.SESSION_COOKIE_SECURE = False

        connection.settings_dict['ATOMIC_REQUESTS'] = True

    def fetchHTML(self, url, query=None):
        if query:
            url += ('?' + query)

        return self.client.get(url, HTTP_X_FORWARDED_HTTPS='https')

    def fetchJSON(self, url, query=None):
        url += '.json'
        if query:
            url += ('?' + query)

        response = self.client.get(url, content_type="application/json", HTTP_X_FORWARDED_HTTPS='https')

        # this will fail if our response isn't valid json
        response.json = json.loads(response.content)
        return response

    def fetchXML(self, url, query=None):
        url += '.xml'
        if query:
            url += ('?' + query)

        response = self.client.get(url, content_type="application/xml", HTTP_X_FORWARDED_HTTPS='https')

        # this will fail if our response isn't valid XML
        response.xml = ET.fromstring(response.content)
        return response

    def postJSON(self, url, data):
        response = self.client.post(url + ".json", json.dumps(data), content_type="application/json", HTTP_X_FORWARDED_HTTPS='https')
        if response.content:
            response.json = json.loads(response.content)
        return response

    def deleteJSON(self, url, query=None):
        url = url + ".json"
        if query:
            url = url + "?" + query

        response = self.client.delete(url, content_type="application/json", HTTP_X_FORWARDED_HTTPS='https')
        if response.content:
            response.json = json.loads(response.content)
        return response

    def assertResultCount(self, response, count):
        self.assertEquals(count, response.json['count'])

    def assertJSONArrayContains(self, response, key, value):
        if 'results' in response.json:
            for result in response.json['results']:
                for v in result[key]:
                    if v == value: return
        else:
            for v in response.json[key]:
                if v == value: return

        self.fail("Unable to find %s:%s in %s" % (key, value, response.json))

    def assertJSON(self, response, key, value):
        if 'results' in response.json:
            for result in response.json['results']:
                if result[key] == value:
                    return
        else:
            if response.json[key] == value:
                return

        self.fail("Unable to find %s:%s in %s" % (key, value, response.json))

    def assertNotJSON(self, response, key, value):
        if 'results' in response.json:
            for result in response.json['results']:
                if result[key] == value:
                    self.fail("Found %s:%s in %s" % (key, value, response.json))
        else:
            if response.json[key] == value:
                self.fail("Found %s:%s in %s" % (key, value, response.json))

        return

    def assertResponseError(self, response, field, message, status_code=400):
        self.assertEquals(status_code, response.status_code)
        self.assertTrue(message, field in response.json)
        self.assertTrue(message, isinstance(response.json[field], (list, tuple)))
        self.assertIn(message, response.json[field])

    def assert403(self, url):
        response = self.fetchHTML(url)
        self.assertEquals(403, response.status_code)

    def test_api_explorer(self):
        url = reverse('api.v1.explorer')
        response = self.fetchHTML(url)
        self.assertEquals(200, response.status_code)
        self.assertContains(response, "Log in to use the Explorer")

        # login as non-org user
        self.login(self.non_org_user)
        response = self.fetchHTML(url)
        self.assertEquals(200, response.status_code)
        self.assertContains(response, "Log in to use the Explorer")

        # login as administrator
        self.login(self.admin)
        response = self.fetchHTML(url)
        self.assertEquals(200, response.status_code)
        self.assertNotContains(response, "Log in to use the Explorer")

    def test_api_root(self):
        url = reverse('api.v1')

        # browse as HTML anonymously
        response = self.fetchHTML(url)
        self.assertContains(response, "We provide a simple REST API", status_code=403)  # still shows docs

        # try to browse as JSON anonymously
        response = self.fetchJSON(url)
        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.json['detail'], "Authentication credentials were not provided.")

        # try to browse as XML anonymously
        response = self.fetchXML(url)
        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.xml.find('detail').text, "Authentication credentials were not provided.")

        # login as administrator
        self.login(self.admin)
        token = self.admin.api_token  # generates token for the user
        self.assertIsInstance(token, basestring)
        self.assertEqual(len(token), 40)

        with self.assertNumQueries(0):  # subsequent lookup of token comes from cache
            self.assertEqual(self.admin.api_token, token)

        # browse as HTML
        response = self.fetchHTML(url)
        self.assertContains(response, token, status_code=200)  # displays their API token

        # browse as JSON
        response = self.fetchJSON(url)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json['labels'], 'https://testserver:80/api/v1/labels')  # endpoints are listed

        # browse as XML
        response = self.fetchXML(url)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.xml.find('labels').text, 'https://testserver:80/api/v1/labels')

    def test_api_serializer_fields(self):
        dict_field = StringDictField(source='test')

        self.assertEqual(dict_field.to_internal_value({'a': '123'}), {'a': '123'})
        self.assertRaises(ValidationError, dict_field.to_internal_value, [])  # must be a dict
        self.assertRaises(ValidationError, dict_field.to_internal_value, {123: '456'})  # keys and values must be strings

        strings_field = StringArrayField(source='test')

        self.assertEqual(strings_field.to_internal_value(['a', 'b', 'c']), ['a', 'b', 'c'])
        self.assertEqual(strings_field.to_internal_value('abc'), ['abc'])  # convert single string to array
        self.assertRaises(ValidationError, strings_field.to_internal_value, {})  # must be a list

        phones_field = PhoneArrayField(source='test')

        self.assertEqual(phones_field.to_internal_value(['123', '234']), [('tel', '123'), ('tel', '234')])
        self.assertEqual(phones_field.to_internal_value('123'), [('tel', '123')])  # convert single string to array
        self.assertRaises(ValidationError, phones_field.to_internal_value, {})  # must be a list
        self.assertRaises(ValidationError, phones_field.to_internal_value, ['123'] * 101)  # 100 items max

        channel_field = ChannelField(source='test')

        self.assertEqual(channel_field.to_internal_value(self.channel.pk), self.channel)
        self.channel.is_active = False
        self.channel.save()
        self.assertRaises(ValidationError, channel_field.to_internal_value, self.channel.pk)

        date_field = DateTimeField(source='test')

        dt = pytz.timezone("Africa/Kigali").localize(datetime(2015, 12, 16, 12, 56, 30, 123456))
        self.assertEqual(date_field.to_representation(dt), '2015-12-16T10:56:30.123Z')
        self.assertEqual(date_field.to_representation(None), None)

    @override_settings(REST_HANDLE_EXCEPTIONS=True)
    @patch('temba.api.v1.views.FieldEndpoint.get_queryset')
    def test_api_error_handling(self, mock_get_queryset):
        mock_get_queryset.side_effect = ValueError("DOH!")

        self.login(self.admin)

        response = self.client.get(reverse('api.v1.contactfields') + '.json', content_type="application/json", HTTP_X_FORWARDED_HTTPS='https')
        self.assertEqual(response.status_code, 500)
        self.assertEqual(response.content, "Server Error. Site administrators have been notified.")

    def test_api_authentication(self):
        url = reverse('api.v1.org') + '.json'

        # can't fetch endpoint with invalid token
        response = self.client.get(url, content_type="application/json",
                                   HTTP_X_FORWARDED_HTTPS='https', HTTP_AUTHORIZATION="Token 1234567890")
        self.assertEqual(response.status_code, 403)

        # can fetch endpoint with valid token
        response = self.client.get(url, content_type="application/json",
                                   HTTP_X_FORWARDED_HTTPS='https', HTTP_AUTHORIZATION="Token %s" % self.admin.api_token)
        self.assertEqual(response.status_code, 200)

        # but not if user is inactive
        self.admin.is_active = False
        self.admin.save()

        response = self.client.get(url, content_type="application/json",
                                   HTTP_X_FORWARDED_HTTPS='https', HTTP_AUTHORIZATION="Token %s" % self.admin.api_token)
        self.assertEqual(response.status_code, 403)

    def test_api_org(self):
        url = reverse('api.v1.org')

        # can't access, get 403
        self.assert403(url)

        # login as plain user
        self.login(self.user)
        self.assert403(url)

        # login as editor
        self.login(self.editor)

        # browse endpoint as HTML docs
        response = self.fetchHTML(url)
        self.assertEqual(response.status_code, 200)

        # fetch as JSON
        response = self.fetchJSON(url)
        self.assertEqual(200, response.status_code)
        self.assertEqual(response.json, dict(name="Temba",
                                             country="RW",
                                             languages=[],
                                             primary_language=None,
                                             timezone="Africa/Kigali",
                                             date_style="day_first",
                                             anon=False))

        eng = Language.create(self.org, self.admin, "English", 'eng')
        fre = Language.create(self.org, self.admin, "French", 'fre')
        self.org.primary_language = eng
        self.org.save()

        response = self.fetchJSON(url)
        self.assertEqual(200, response.status_code)
        self.assertEqual(response.json, dict(name="Temba",
                                             country="RW",
                                             languages=["eng", "fre"],
                                             primary_language="eng",
                                             timezone="Africa/Kigali",
                                             date_style="day_first",
                                             anon=False))

    def test_api_flows(self):
        url = reverse('api.v1.flows')

        # can't access, get 403
        self.assert403(url)

        # login as non-org user
        self.login(self.non_org_user)
        self.assert403(url)

        # login as plain user
        self.login(self.user)
        self.assert403(url)

        # login as administrator
        self.login(self.admin)

        # test that this user has a token
        self.assertTrue(self.admin.api_token)

        # blow it away
        Token.objects.all().delete()

        # should create one lazily
        self.assertTrue(self.admin.api_token)

        # browse endpoint as HTML docs
        response = self.fetchHTML(url)
        self.assertEqual(response.status_code, 200)

        # create our test flow
        flow = self.create_flow()
        flow_ruleset1 = RuleSet.objects.get(flow=flow)

        # this time, a 200
        response = self.fetchJSON(url)
        self.assertEquals(200, response.status_code)

        # should contain our single flow in the response
        self.assertEqual(response.json['results'][0], dict(flow=flow.pk,
                                                           uuid=flow.uuid,
                                                           name='Color Flow',
                                                           labels=[],
                                                           runs=0,
                                                           completed_runs=0,
                                                           rulesets=[dict(node=flow_ruleset1.uuid,
                                                                          id=flow_ruleset1.pk,
                                                                          response_type='C',
                                                                          ruleset_type='wait_message',
                                                                          label='color')],
                                                           participants=0,
                                                           created_on=datetime_to_json_date(flow.created_on),
                                                           expires=flow.expires_after_minutes,
                                                           archived=False))

        # try fetching as XML
        response = self.fetchXML(url)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.xml.find('results').find('*').find('uuid').text, flow.uuid)
        self.assertEqual(response.xml.find('results').find('*').find('name').text, 'Color Flow')

        # filter by archived
        flow.is_archived = True
        flow.save()
        response = self.fetchJSON(url, "archived=Y")
        self.assertResultCount(response, 1)

        response = self.fetchJSON(url, "archived=N")
        self.assertResultCount(response, 0)

        # filter by label
        label = FlowLabel.create_unique("Polls", self.org)
        label.toggle_label([flow], add=True)

        response = self.fetchJSON(url, "label=Polls")
        self.assertResultCount(response, 1)

        response = self.fetchJSON(url, "label=Missing")
        self.assertResultCount(response, 0)

        # unarchive the flow
        flow.is_archived = False
        flow.save()

        flow2 = self.create_flow()
        flow3 = self.create_flow()

        response = self.fetchJSON(url)
        self.assertEquals(200, response.status_code)
        self.assertResultCount(response, 3)

        response = self.fetchJSON(url, "uuid=%s&uuid=%s" % (flow.uuid, flow2.uuid))
        self.assertEquals(200, response.status_code)
        self.assertResultCount(response, 2)

        response = self.fetchJSON(url, "flow=%d&flow=%d" % (flow.pk, flow2.pk))
        self.assertEquals(200, response.status_code)
        self.assertResultCount(response, 2)

        label2 = FlowLabel.create_unique("Surveys", self.org)
        label2.toggle_label([flow2], add=True)

        response = self.fetchJSON(url, "label=Polls&label=Surveys")
        self.assertEquals(200, response.status_code)
        self.assertResultCount(response, 2)

    def test_api_flow_definition(self):
        url = reverse('api.v1.flow_definition')
        self.login(self.admin)

        # load flow definition from test data
        flow = self.get_flow('pick_a_number')
        definition = self.get_flow_json('pick_a_number')['definition']
        response = self.fetchJSON(url, "uuid=%s" % flow.uuid)
        self.assertEquals(1, response.json['metadata']['revision'])
        self.assertEquals("Pick a Number", response.json['metadata']['name'])
        self.assertEquals("F", response.json['flow_type'])

        # make sure the version that is returned increments properly
        flow.update(flow.as_json())
        response = self.fetchJSON(url, "uuid=%s" % flow.uuid)
        self.assertEquals(2, response.json['metadata']['revision'])

        # now delete our flow, we'll create it from scratch below
        flow.delete()

        # post something that isn't an object
        response = self.postJSON(url, ['test'])
        self.assertResponseError(response, 'non_field_errors', "Request body should be a single JSON object")

        # can't create a flow without a name
        response = self.postJSON(url, dict(name="", version=6))
        self.assertEqual(response.status_code, 400)

        # but we can create an empty flow
        response = self.postJSON(url, dict(name="Empty", version=6))
        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.json['metadata']['name'], "Empty")

        # can't create a flow without a version
        response = self.postJSON(url, dict(name='No Version'))
        self.assertEqual(response.status_code, 400)

        # and create flow with a definition
        response = self.postJSON(url, dict(name="Pick a Number", definition=definition, version=6))
        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.json['metadata']['name'], "Pick a Number")

        # make sure our flow is there as expected
        flow = Flow.objects.get(name='Pick a Number')
        self.assertEqual(flow.flow_type, 'F')
        self.assertEqual(flow.action_sets.count(), 2)
        self.assertEqual(flow.rule_sets.count(), 2)

        # make local change
        flow.name = 'Something else'
        flow.flow_type = 'V'
        flow.save()

        response = self.postJSON(url, dict(uuid=flow.uuid, name="Pick a Number", flow_type='S',
                                           definition=definition, version=6))

        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.json['metadata']['name'], "Pick a Number")

        # make sure our flow is there as expected
        flow = Flow.objects.get(name='Pick a Number')
        self.assertEqual(flow.flow_type, 'S')

        # post a version 7 flow
        definition['metadata'] = dict(name='Version 7 flow', flow_type='S', uuid=flow.uuid)
        definition['version'] = 7
        response = self.postJSON(url, definition)
        self.assertEqual(response.status_code, 201)
        self.assertEqual(Flow.objects.get(uuid=flow.uuid).name, 'Version 7 flow')

        # No invalid type
        flow.delete()
        response = self.postJSON(url, dict(name="Hello World", definition=definition, flow_type='X', version=6))
        self.assertEqual(response.status_code, 400)

    def test_api_steps_empty(self):
        url = reverse('api.v1.steps')
        self.login(self.surveyor)

        flow = self.create_flow()

        # remove our entry node
        ActionSet.objects.get(uuid=flow.entry_uuid).delete()

        # and set our entry to be our ruleset
        flow.entry_type = RULE_SET
        flow.entry_uuid = RuleSet.objects.get().uuid
        flow.save()

        # post that someone arrived at this ruleset, but never replied
        data = dict(flow=flow.uuid,
                    revision=1,
                    contact=self.joe.uuid,
                    started='2015-08-25T11:09:29.088Z',
                    steps=[
                        dict(node=flow.entry_uuid,
                             arrived_on='2015-08-25T11:09:30.088Z',
                             actions=[],
                             rule=None)
                    ],
                    completed=False)

        reponse = None
        with patch.object(timezone, 'now', return_value=datetime(2015, 9, 15, 0, 0, 0, 0, pytz.UTC)):
            response = self.postJSON(url, data)

        run = FlowRun.objects.get()
        self.assertEqual(run.flow, flow)
        self.assertEqual(run.contact, self.joe)
        self.assertEqual(run.created_on, datetime(2015, 8, 25, 11, 9, 29, 88000, pytz.UTC))
        self.assertEqual(run.modified_on, datetime(2015, 9, 15, 0, 0, 0, 0, pytz.UTC))
        self.assertEqual(run.is_active, True)
        self.assertEqual(run.is_completed(), False)

        steps = list(run.steps.order_by('pk'))
        self.assertEqual(len(steps), 1)
        self.assertEqual(steps[0].step_uuid, flow.entry_uuid)
        self.assertEqual(steps[0].step_type, 'R')
        self.assertEqual(steps[0].rule_uuid, None)
        self.assertEqual(steps[0].rule_category, None)
        self.assertEqual(steps[0].rule_value, None)
        self.assertEqual(steps[0].rule_decimal_value, None)
        self.assertEqual(steps[0].arrived_on, datetime(2015, 8, 25, 11, 9, 30, 88000, pytz.UTC))
        self.assertEqual(steps[0].left_on, None)
        self.assertEqual(steps[0].next_uuid, None)

        # check flow stats
        self.assertEqual(flow.get_total_runs(), 1)
        self.assertEqual(flow.get_total_contacts(), 1)
        self.assertEqual(flow.get_completed_runs(), 0)

        # check flow activity
        self.assertEqual(flow.get_activity(), ({flow.entry_uuid: 1}, {}))



    def test_api_steps(self):
        url = reverse('api.v1.steps')

        # can't access, get 403
        self.assert403(url)

        # login as plain user
        self.login(self.user)
        self.assert403(url)

        # login as surveyor
        self.login(self.surveyor)

        uuid_start = 0
        flow = self.create_flow(uuid_start)

        # add an update action
        definition = flow.as_json()
        from temba.tests import uuid
        new_node_uuid = uuid(uuid_start + 20)

        # add a new action set
        definition['action_sets'].append(dict(uuid=new_node_uuid, x=100, y=4, destination=None,
                                              actions=[dict(type='save', field='tel_e164', value='+12065551212')]))

        # point one of our nodes to it
        definition['action_sets'][1]['destination'] = new_node_uuid

        flow.update(definition)
        data = dict(flow=flow.uuid,
                    revision=2,
                    contact=self.joe.uuid,
                    started='2015-08-25T11:09:29.088Z',
                    steps=[
                        dict(node='00000000-00000000-00000000-00000001',
                             arrived_on='2015-08-25T11:09:30.088Z',
                             actions=[
                                 dict(type="reply", msg="What is your favorite color?")
                             ])
                    ],
                    completed=False)

        with patch.object(timezone, 'now', return_value=datetime(2015, 9, 15, 0, 0, 0, 0, pytz.UTC)):
            self.postJSON(url, data)

        run = FlowRun.objects.get()
        self.assertEqual(run.flow, flow)
        self.assertEqual(run.contact, self.joe)
        self.assertEqual(run.created_on, datetime(2015, 8, 25, 11, 9, 29, 88000, pytz.UTC))
        self.assertEqual(run.modified_on, datetime(2015, 9, 15, 0, 0, 0, 0, pytz.UTC))
        self.assertEqual(run.is_active, True)
        self.assertEqual(run.is_completed(), False)

        steps = list(run.steps.order_by('pk'))
        self.assertEqual(len(steps), 1)
        self.assertEqual(steps[0].step_uuid, '00000000-00000000-00000000-00000001')
        self.assertEqual(steps[0].step_type, 'A')
        self.assertEqual(steps[0].rule_uuid, None)
        self.assertEqual(steps[0].rule_category, None)
        self.assertEqual(steps[0].rule_value, None)
        self.assertEqual(steps[0].rule_decimal_value, None)
        self.assertEqual(steps[0].arrived_on, datetime(2015, 8, 25, 11, 9, 30, 88000, pytz.UTC))
        self.assertEqual(steps[0].left_on, None)
        self.assertEqual(steps[0].next_uuid, None)

        # outgoing message for reply
        out_msgs = list(Msg.all_messages.filter(direction='O').order_by('pk'))
        self.assertEqual(len(out_msgs), 1)
        self.assertEqual(out_msgs[0].contact, self.joe)
        self.assertEqual(out_msgs[0].contact_urn, None)
        self.assertEqual(out_msgs[0].text, "What is your favorite color?")
        self.assertEqual(out_msgs[0].created_on, datetime(2015, 8, 25, 11, 9, 30, 88000, pytz.UTC))

        # check flow stats
        self.assertEqual(flow.get_total_runs(), 1)
        self.assertEqual(flow.get_total_contacts(), 1)
        self.assertEqual(flow.get_completed_runs(), 0)

        # check flow activity
        self.assertEqual(flow.get_activity(), ({u'00000000-00000000-00000000-00000001': 1}, {}))

        data = dict(flow=flow.uuid,
                    revision=2,
                    contact=self.joe.uuid,
                    started='2015-08-25T11:09:29.088Z',
                    steps=[
                        dict(node='00000000-00000000-00000000-00000005',
                             arrived_on='2015-08-25T11:11:30.088Z',
                             rule=dict(uuid='00000000-00000000-00000000-00000012',
                                       value="orange",
                                       category="Orange",
                                       text="I like orange")),
                        dict(node='00000000-00000000-00000000-00000002',
                             arrived_on='2015-08-25T11:13:30.088Z',
                             actions=[
                                 dict(type="reply", msg="I love orange too!")
                             ]),
                        dict(node=new_node_uuid,
                             arrived_on='2015-08-25T11:15:30.088Z',
                             actions=[
                                 dict(type="save", field="tel_e164", value="+12065551212")
                             ]),
                    ],
                    completed=True)

        with patch.object(timezone, 'now', return_value=datetime(2015, 9, 16, 0, 0, 0, 0, pytz.UTC)):
            self.postJSON(url, data)

        # run should be complete now
        run = FlowRun.objects.get()
        self.assertEqual(run.modified_on, datetime(2015, 9, 16, 0, 0, 0, 0, pytz.UTC))
        self.assertEqual(run.is_active, False)
        self.assertEqual(run.is_completed(), True)
        self.assertEqual(run.steps.count(), 4)

        # joe should have an urn now
        self.assertIsNotNone(self.joe.urns.filter(path='+12065551212').first())

        steps = list(run.steps.order_by('pk'))
        self.assertEqual(steps[0].left_on, datetime(2015, 8, 25, 11, 11, 30, 88000, pytz.UTC))
        self.assertEqual(steps[0].next_uuid, '00000000-00000000-00000000-00000005')

        self.assertEqual(steps[1].step_uuid, '00000000-00000000-00000000-00000005')
        self.assertEqual(steps[1].step_type, 'R')
        self.assertEqual(steps[1].rule_uuid, '00000000-00000000-00000000-00000012')
        self.assertEqual(steps[1].rule_category, 'Orange')
        self.assertEqual(steps[1].rule_value, "orange")
        self.assertEqual(steps[1].rule_decimal_value, None)
        self.assertEqual(steps[1].next_uuid, '00000000-00000000-00000000-00000002')
        self.assertEqual(steps[1].arrived_on, datetime(2015, 8, 25, 11, 11, 30, 88000, pytz.UTC))
        self.assertEqual(steps[1].left_on, datetime(2015, 8, 25, 11, 13, 30, 88000, pytz.UTC))
        self.assertEqual(steps[1].messages.count(), 1)

        # check value
        value = Value.objects.get(org=self.org)
        self.assertEqual(value.contact, self.joe)
        self.assertEqual(value.run, run)
        self.assertEqual(value.ruleset, RuleSet.objects.get(uuid='00000000-00000000-00000000-00000005'))
        self.assertEqual(value.rule_uuid, '00000000-00000000-00000000-00000012')
        self.assertEqual(value.string_value, 'orange')
        self.assertEqual(value.decimal_value, None)
        self.assertEqual(value.datetime_value, None)
        self.assertEqual(value.location_value, None)
        self.assertEqual(value.recording_value, None)
        self.assertEqual(value.category, 'Orange')

        step1_msgs = list(steps[1].messages.order_by('pk'))
        self.assertEqual(step1_msgs[0].contact, self.joe)
        self.assertEqual(step1_msgs[0].contact_urn, None)
        self.assertEqual(step1_msgs[0].text, "I like orange")

        self.assertEqual(steps[2].step_uuid, '00000000-00000000-00000000-00000002')
        self.assertEqual(steps[2].step_type, 'A')
        self.assertEqual(steps[2].rule_uuid, None)
        self.assertEqual(steps[2].rule_category, None)
        self.assertEqual(steps[2].rule_value, None)
        self.assertEqual(steps[2].rule_decimal_value, None)
        self.assertEqual(steps[2].arrived_on, datetime(2015, 8, 25, 11, 13, 30, 88000, pytz.UTC))
        self.assertEqual(steps[2].left_on, datetime(2015, 8, 25, 11, 15, 30, 88000, pytz.UTC))
        self.assertEqual(steps[2].next_uuid, new_node_uuid)

        # new outgoing message for reply
        out_msgs = list(Msg.all_messages.filter(direction='O').order_by('pk'))
        self.assertEqual(len(out_msgs), 2)
        self.assertEqual(out_msgs[1].contact, self.joe)
        self.assertEqual(out_msgs[1].contact_urn, None)
        self.assertEqual(out_msgs[1].text, "I love orange too!")
        self.assertEqual(out_msgs[1].created_on, datetime(2015, 8, 25, 11, 13, 30, 88000, pytz.UTC))
        self.assertEqual(out_msgs[1].response_to, step1_msgs[0])

        # check flow stats
        self.assertEqual(flow.get_total_runs(), 1)
        self.assertEqual(flow.get_total_contacts(), 1)
        self.assertEqual(flow.get_completed_runs(), 1)

        # check flow activity
        self.assertEqual(flow.get_activity(), ({},
                                               {'00000000-00000000-00000000-00000002:00000000-00000000-00000000-00000020': 1,
                                                '00000000-00000000-00000000-00000012:00000000-00000000-00000000-00000002': 1,
                                                '00000000-00000000-00000000-00000001:00000000-00000000-00000000-00000005': 1}))

        # now lets remove our last action set
        definition['action_sets'].pop()
        definition['action_sets'][1]['destination'] = None
        flow.update(definition)

        # update a value for our missing node
        data = dict(flow=flow.uuid,
                    revision=2,
                    contact=self.joe.uuid,
                    started='2015-08-26T11:09:29.088Z',
                    steps=[
                        dict(node=new_node_uuid,
                             arrived_on='2015-08-26T11:15:30.088Z',
                             actions=[
                                 dict(type="save", field="tel_e164", value="+13605551212")
                             ]),
                    ],
                    completed=True)

        with patch.object(timezone, 'now', return_value=datetime(2015, 9, 16, 0, 0, 0, 0, pytz.UTC)):

            # this version doesn't have our node
            data['revision'] = 3
            response = self.postJSON(url, data)
            self.assertEquals(400, response.status_code)
            self.assertResponseError(response, 'non_field_errors', "No such node with UUID 00000000-00000000-00000000-00000020 in flow 'Color Flow'")

            # this version doesn't exist
            data['revision'] = 12
            response = self.postJSON(url, data)
            self.assertEquals(400, response.status_code)
            self.assertResponseError(response, 'non_field_errors', "Invalid revision: 12")

            # this one exists and has our node
            data['revision'] = 2
            response = self.postJSON(url, data)
            self.assertEquals(201, response.status_code)
            self.assertIsNotNone(self.joe.urns.filter(path='+13605551212').first())

            # test with old name
            del data['revision']
            data['version'] = 2
            response = self.postJSON(url, data)
            self.assertEquals(201, response.status_code)
            self.assertIsNotNone(self.joe.urns.filter(path='+13605551212').first())

    def test_api_results(self):
        url = reverse('api.v1.results')

        # can't access, get 403
        self.assert403(url)

        # login as plain user
        self.login(self.user)
        self.assert403(url)

        # login as administrator
        self.login(self.admin)

        # all requests must be against a ruleset or field
        response = self.fetchJSON(url)
        self.assertEquals(400, response.status_code)
        self.assertResponseError(response, 'non_field_errors', "You must specify either a ruleset or contact field")

        # create our test flow and a contact field
        self.create_flow()
        contact_field = ContactField.objects.create(key='gender', label="Gender", org=self.org)
        ruleset = RuleSet.objects.get()

        # invalid ruleset id
        response = self.fetchJSON(url, 'ruleset=12345678')
        self.assertResponseError(response, 'ruleset', "No ruleset found with that UUID or id")

        # invalid ruleset UUID
        response = self.fetchJSON(url, 'ruleset=invalid-uuid')
        self.assertResponseError(response, 'ruleset', "No ruleset found with that UUID or id")

        # invalid field label
        response = self.fetchJSON(url, 'contact_field=born')
        self.assertResponseError(response, 'contact_field', "No contact field found with that label")

        # can't specify both ruleset and field
        response = self.fetchJSON(url, 'ruleset=%s&contact_field=Gender' % ruleset.uuid)
        self.assertResponseError(response, 'non_field_errors', "You must specify either a ruleset or contact field")

        # invalid segment JSON
        response = self.fetchJSON(url, 'contact_field=Gender&segment=%7B\"location\"%7D')
        self.assertResponseError(response, 'segment', "Invalid segment format, must be in JSON format")

        with patch('temba.values.models.Value.get_value_summary') as mock_value_summary:
            mock_value_summary.return_value = []

            response = self.fetchJSON(url, 'ruleset=%d' % ruleset.id)
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json, dict(results=[]))
            mock_value_summary.assert_called_with(ruleset=ruleset, segment=None)

            response = self.fetchJSON(url, 'ruleset=%s' % ruleset.uuid)
            self.assertEqual(response.status_code, 200)
            mock_value_summary.assert_called_with(ruleset=ruleset, segment=None)

            response = self.fetchJSON(url, 'contact_field=Gender')
            self.assertEqual(200, response.status_code)
            mock_value_summary.assert_called_with(contact_field=contact_field, segment=None)

            response = self.fetchJSON(url, 'contact_field=Gender&segment=%7B\"location\"%3A\"State\"%7D')
            self.assertEqual(200, response.status_code)
            mock_value_summary.assert_called_with(contact_field=contact_field, segment=dict(location="State"))

    def test_api_runs(self):
        url = reverse('api.v1.runs')

        # can't access, get 403
        self.assert403(url)

        # login as plain user
        self.login(self.user)
        self.assert403(url)

        # login as administrator
        self.login(self.admin)

        # browse endpoint as HTML docs
        response = self.fetchHTML(url)
        self.assertEqual(response.status_code, 200)

        # create our test flow and a copy
        flow = self.create_flow()
        flow_copy = Flow.copy(flow, self.admin)

        # can't start with an invalid phone number
        response = self.postJSON(url, dict(flow=flow.pk, phone="asdf"))
        self.assertEquals(400, response.status_code)

        # can't start with invalid extra
        response = self.postJSON(url, dict(flow=flow.pk, phone="+250788123123", extra=dict(asdf=dict(asdf="asdf"))))
        self.assertEquals(400, response.status_code)

        # can't start without a flow
        response = self.postJSON(url, dict(phone="+250788123123"))
        self.assertEquals(400, response.status_code)

        # can start with flow id and phone (deprecated and creates contact)
        response = self.postJSON(url, dict(flow=flow.pk, phone="+250788123123"))
        run = FlowRun.objects.get()
        self.assertEqual(201, response.status_code)
        self.assertEqual(1, FlowRun.objects.filter(contact__urns__path="+250788123123").count())

        # can start with flow UUID and phone
        response = self.postJSON(url, dict(flow_uuid=flow.uuid, phone="+250788123123"))
        self.assertEqual(201, response.status_code)
        self.assertEqual(2, FlowRun.objects.filter(contact__urns__path="+250788123123").count())

        # can provide extra
        response = self.postJSON(url, dict(flow_uuid=flow.uuid, phone="+250788123124", extra=dict(code="ONEZ")))
        self.assertEquals(201, response.status_code)
        run_with_extra = FlowRun.objects.get(contact__urns__path="+250788123124")
        self.assertEqual("ONEZ", run_with_extra.field_dict()['code'])

        contact = Contact.objects.get(urns__path='+250788123124')
        group = self.create_group("Group", [contact])

        # can do it with a contact UUID
        response = self.postJSON(url, dict(flow_uuid=flow.uuid, contact=contact.uuid))
        self.assertEquals(201, response.status_code)
        self.assertEquals(2, FlowRun.objects.filter(flow=flow.pk, contact=contact).count())

        # can do it with a list of contact UUIDs
        response = self.postJSON(url, dict(flow_uuid=flow.uuid, contacts=[contact.uuid]))
        self.assertEquals(201, response.status_code)
        self.assertEquals(3, FlowRun.objects.filter(flow=flow.pk, contact=contact).count())

        # can do it with a list of group UUIDs
        response = self.postJSON(url, dict(flow_uuid=flow.uuid, groups=[group.uuid]))
        self.assertEquals(201, response.status_code)
        self.assertEquals(4, FlowRun.objects.filter(flow=flow.pk, contact=contact).count())

        # can's restart participants if restart is False
        response = self.postJSON(url, dict(flow_uuid=flow.uuid, contacts=[contact.uuid], restart_participants=False))
        self.assertEquals(400, response.status_code)
        self.assertEquals(4, FlowRun.objects.filter(flow=flow.pk, contact=contact).count())

        # force participants to restart in flow if restart_participants is True
        response = self.postJSON(url, dict(flow_uuid=flow.uuid, contacts=[contact.uuid], restart_participants=True))
        self.assertEquals(201, response.status_code)
        self.assertEquals(5, FlowRun.objects.filter(flow=flow.pk, contact=contact).count())

        # create another against the copy of the flow
        response = self.postJSON(url, dict(flow=flow_copy.pk, contact=contact.uuid))
        self.assertEquals(201, response.status_code)

        # can't start flow with phone if got no tel channel
        self.channel.is_active = False
        self.channel.save()
        response = self.postJSON(url, dict(flow_uuid=flow.uuid, phone="+250788123123"))
        self.assertEqual(400, response.status_code)

        # but can with contact
        response = self.postJSON(url, dict(flow_uuid=flow.uuid, contact=contact.uuid))
        self.assertEquals(201, response.status_code)

        self.channel.is_active = True
        self.channel.save()

        with AnonymousOrg(self.org):
            # anon orgs can't start flows by phone
            response = self.postJSON(url, dict(flow_uuid=flow.uuid, phone="+250788123123"))
            self.assertResponseError(response, 'phone', "Cannot start flows by phone for anonymous organizations")

            # but can start them by contact UUID
            response = self.postJSON(url, dict(flow_uuid=flow.uuid, contact=contact.uuid))
            self.assertEquals(201, response.status_code)

        # now test fetching them instead.....

        # no filtering
        response = self.fetchJSON(url)
        self.assertEquals(200, response.status_code)
        self.assertResultCount(response, 10)  # all the runs

        flow.start([], [Contact.get_test_contact(self.user)])  # create a run for a test contact

        response = self.fetchJSON(url)
        self.assertResultCount(response, 10)  # test contact's run not included

        # filter by run id
        response = self.fetchJSON(url, "run=%d" % run.pk)
        self.assertResultCount(response, 1)
        self.assertEqual(response.json['results'][0]['run'], run.pk)
        self.assertEqual(response.json['results'][0]['flow_uuid'], flow.uuid)
        self.assertEqual(response.json['results'][0]['contact'], self.joe.uuid)
        self.assertEqual(response.json['results'][0]['completed'], False)
        self.assertEqual(response.json['results'][0]['expires_on'], datetime_to_json_date(run.expires_on))
        self.assertEqual(response.json['results'][0]['expired_on'], None)

        # filter by flow id (deprecated)
        response = self.fetchJSON(url, "flow=%d" % flow.pk)
        self.assertResultCount(response, 9)

        # filter by flow UUID
        response = self.fetchJSON(url, "flow_uuid=%s" % flow.uuid)

        self.assertResultCount(response, 9)
        self.assertNotContains(response, flow_copy.uuid)

        # filter by phone (deprecated)
        response = self.fetchJSON(url, "phone=%2B250788123123")  # joe
        self.assertResultCount(response, 2)
        self.assertContains(response, self.joe.uuid)
        self.assertNotContains(response, contact.uuid)

        # filter by contact UUID
        response = self.fetchJSON(url, "contact=" + contact.uuid)
        self.assertResultCount(response, 8)
        self.assertContains(response, contact.uuid)
        self.assertNotContains(response, self.joe.uuid)

        # filter by non-existent group name
        response = self.fetchJSON(url, "group=Players")
        self.assertResultCount(response, 0)

        players = self.create_group('Players', [])
        players.contacts.add(contact)
        self.clear_cache()

        # filter by group name
        response = self.fetchJSON(url, "group=Players")
        self.assertResultCount(response, 8)
        self.assertContains(response, contact.uuid)
        self.assertNotContains(response, self.joe.uuid)

        # filter by group UUID
        response = self.fetchJSON(url, "group_uuids=%s" % players.uuid)
        self.assertResultCount(response, 8)
        self.assertContains(response, contact.uuid)
        self.assertNotContains(response, self.joe.uuid)

        # invalid dates
        response = self.fetchJSON(url, "before=01-01T00:00:00.000&after=01-01T00:00:00.000&channel=1,2")
        self.assertEquals(200, response.status_code)
        self.assertResultCount(response, 0)

    def test_api_channels(self):
        url = reverse('api.v1.channels')

        # can't access, get 403
        self.assert403(url)

        # login as plain user
        self.login(self.user)
        self.assert403(url)

        # login as administrator
        self.login(self.admin)

        # browse endpoint as HTML docs
        response = self.fetchHTML(url)
        self.assertEqual(response.status_code, 200)

        # fetch all channels as JSON
        response = self.fetchJSON(url)
        self.assertEquals(200, response.status_code)

        # should contain our channel in the response
        self.assertContains(response, "Test Channel")

        # but not the unclaimed one
        self.assertNotContains(response, "Unclaimed Channel")

        response = self.fetchJSON(url)
        self.assertResultCount(response, 1)
        self.assertJSON(response, 'name', "Test Channel")
        self.assertNotJSON(response, 'name', "Unclaimed Channel")

        # can't claim without a phone number
        response = self.postJSON(url, dict(claim_code="123123123", name="Claimed Channel"))
        self.assertEquals(400, response.status_code)

        # can't claim with an invalid phone number
        response = self.postJSON(url, dict(claim_code="123123123", name="Claimed Channel", phone="asdf"))
        self.assertEquals(400, response.status_code)

        # can't claim with an empty phone number
        response = self.postJSON(url, dict(claim_code="123123123", name="Claimed Channel", phone=""))
        self.assertEquals(400, response.status_code)

        # can't claim with an empty phone number
        response = self.postJSON(url, dict(claim_code="123123123", name="Claimed Channel", phone="9999999999"))
        self.assertEquals(400, response.status_code)

        # can claim if everything is valid
        response = self.postJSON(url, dict(claim_code="123123123", name="Claimed Channel", phone="250788123123"))
        self.assertEquals(201, response.status_code)

        # should now have two channels
        self.assertEquals(2, Channel.objects.filter(org=self.org).count())

        # and should be tied to our org
        channel2 = Channel.objects.get(pk=self.channel2.pk)
        self.assertEquals(response.json['relayer'], self.channel2.pk)
        self.assertFalse('claim_code' in response.json)
        self.assertEquals("Claimed Channel", channel2.name)
        self.assertEquals(self.org, channel2.org)
        self.assertFalse(channel2.claim_code)

        # create a sync even for our channel
        SyncEvent.create(channel2, dict(p_sts='CHA', p_src='AC', p_lvl=90, net='WIFI', pending=[], retry=[], cc='RW'), [])

        response = self.fetchJSON(url)
        self.assertResultCount(response, 2)
        self.assertJSON(response, 'name', 'Test Channel')
        self.assertJSON(response, 'name', 'Claimed Channel')

        # trying to do so again should be an error of not finding the channel
        response = self.postJSON(url, dict(claim_code="123123123", name="Claimed Channel", phone="250788123123"))
        self.assertEquals(400, response.status_code)

        # try with an empty claim code
        response = self.postJSON(url, dict(claim_code="  ", name="Claimed Channel"))
        self.assertEquals(400, response.status_code)

        # try without a claim code
        response = self.postJSON(url, dict(name="Claimed Channel"))
        self.assertEquals(400, response.status_code)

        # list our channels again
        response = self.fetchJSON(url)
        self.assertEquals(200, response.status_code)
        self.assertContains(response, "Claimed Channel")

        # filter our channels
        response = self.fetchJSON(url, "country=RW&before=2030-01-01T00:00:00.000&after=2010-01-01T00:00:00.000")
        self.assertEquals(200, response.status_code)
        self.assertContains(response, "Claimed Channel")

        # filter our channel by phone
        response = self.fetchJSON(url, "phone=%2B250788123123")
        self.assertEquals(200, response.status_code)
        self.assertContains(response, "Claimed Channel")

        # invalid dates
        response = self.fetchJSON(url, "country=RW&before=01-01T00:00:00.000&after=01-01T00:00:00.000&channel=1,2")
        self.assertEquals(200, response.status_code)
        self.assertNotContains(response, "Claimed Channel")

        # not matching country
        response = self.fetchJSON(url, "country=KE")
        self.assertEquals(200, response.status_code)
        self.assertNotContains(response, "Claimed Channel")

        # delete a single channel
        response = self.deleteJSON(url, "phone=%2B250788123123")
        self.assertEquals(204, response.status_code)

        # check that we've been deleted
        channel2 = Channel.objects.get(pk=self.channel2.pk)
        self.assertFalse(channel2.org)
        self.assertFalse(channel2.is_active)

        # but our original one still works
        channel = Channel.objects.get(pk=self.channel.pk)
        self.assertTrue(channel.org)
        self.assertTrue(channel.is_active)

        # deleting again is a 404
        response = self.deleteJSON(url, "phone=%2B250788123123")
        self.assertEquals(404, response.status_code)

        # make our original channel active
        channel2.is_active = True
        channel2.save()

        # do full delete
        response = self.deleteJSON(url, "")
        self.assertEquals(204, response.status_code)

        channel2 = Channel.objects.get(pk=channel2.pk)
        channel = Channel.objects.get(pk=channel.pk)

        self.assertTrue(channel2.is_active)
        self.assertFalse(channel.is_active)

        # test with Twitter channel
        twitter = Channel.create(self.org, self.user, None, 'TT', name="@billy_bob")
        response = self.fetchJSON(url, "relayer=%d" % twitter.pk)
        self.assertEqual(response.json['results'], [{'pending_message_count': 0,
                                                     'name': '@billy_bob',
                                                     'phone': None,
                                                     'country': None,
                                                     'relayer': twitter.pk,
                                                     'power_status': None,
                                                     'power_source': None,
                                                     'power_level': -1,
                                                     'network_type': None,
                                                     'last_seen': datetime_to_json_date(twitter.last_seen)}])

        # check that removing Twitter channel notifies mage
        with patch('temba.utils.mage.MageClient._request') as mock:
            mock.return_value = ""
            response = self.deleteJSON(url, "id=%d" % twitter.pk)
            self.assertEquals(204, response.status_code)

    def test_api_calls(self):
        url = reverse('api.v1.calls')

        # 403 if not logged in
        self.assert403(url)

        # login as plain user
        self.login(self.user)
        self.assert403(url)

        # login as administrator
        self.login(self.admin)

        # browse endpoint as HTML docs
        response = self.fetchHTML(url)
        self.assertEqual(response.status_code, 200)

        # fetch all calls as JSON
        response = self.fetchJSON(url)
        self.assertEquals(200, response.status_code)

        # should have one call from joe
        self.assertContains(response, self.joe.get_urn().path)

        response = self.fetchJSON(url, "call_type=mt_miss&before=2030-01-01T00:00:00.000&after=2010-01-01T00:00:00.000&phone=%%2B250788123123&channel=%d" % self.channel.pk)
        self.assertEquals(200, response.status_code)
        self.assertContains(response, "mt_miss")

        response = self.fetchJSON(url, "status=Q&before=01T00:00:00.000&after=01-01T00:00:00.000&phone=%2B250788123123&channel=asdf&call=124")
        self.assertEquals(200, response.status_code)
        self.assertNotContains(response, "mt_miss")

    def test_api_contacts(self):
        url = reverse('api.v1.contacts')

        # 403 if not logged in
        self.assert403(url)

        # login as plain user
        self.login(self.user)
        self.assert403(url)

        # login as administrator
        self.login(self.admin)

        # browse endpoint as HTML docs
        response = self.fetchHTML(url)
        self.assertEqual(response.status_code, 200)

        # remove all our contacts
        Contact.objects.all().delete()

        # no contacts yet
        response = self.fetchJSON(url)
        self.assertResultCount(response, 0)

        # Invalid data
        response = self.postJSON(url, ['tel:+250788123123'])
        self.assertEquals(400, response.status_code)

        # add a contact using deprecated phone field
        response = self.postJSON(url, dict(name='Snoop Dog', phone='+250788123123'))
        self.assertEquals(201, response.status_code)

        # should be one contact now
        contact = Contact.objects.get()

        # make sure our response contains the uuid
        self.assertContains(response, contact.uuid, status_code=201)

        # and that the contact fields were properly set
        self.assertEquals("+250788123123", contact.get_urn(TEL_SCHEME).path)
        self.assertEquals("Snoop Dog", contact.name)
        self.assertEquals(self.org, contact.org)

        Contact.objects.all().delete()

        # add a contact using urns field, also set language
        response = self.postJSON(url, dict(name='Snoop Dog', urns=['tel:+250788123456', 'twitter:snoop']))

        contact = Contact.objects.get()

        self.assertEquals(201, response.status_code)
        self.assertContains(response, contact.uuid, status_code=201)

        self.assertEquals("+250788123456", contact.get_urn(TEL_SCHEME).path)
        self.assertEquals("snoop", contact.get_urn(TWITTER_SCHEME).path)
        self.assertEquals("Snoop Dog", contact.name)
        self.assertEquals(None, contact.language)
        self.assertEquals(self.org, contact.org)

        # try to update the language to something longer than 3-letters
        response = self.postJSON(url, dict(name='Snoop Dog', urns=['tel:+250788123456'], language='ENGRISH'))
        self.assertEquals(400, response.status_code)
        self.assertResponseError(response, 'language', "Ensure this field has no more than 3 characters.")

        # try to update the language to something shorter than 3-letters
        response = self.postJSON(url, dict(name='Snoop Dog', urns=['tel:+250788123456'], language='X'))
        self.assertEquals(400, response.status_code)
        self.assertResponseError(response, 'language', "Ensure this field has at least 3 characters.")

        # now try 'eng' for English
        response = self.postJSON(url, dict(name='Snoop Dog', urns=['tel:+250788123456'], language='eng'))
        self.assertEquals(201, response.status_code)

        contact = Contact.objects.get()
        self.assertEquals('eng', contact.language)

        # update the contact using deprecated phone field
        response = self.postJSON(url, dict(name='Eminem', phone='+250788123456'))
        self.assertEquals(201, response.status_code)

        contact = Contact.objects.get()
        self.assertEquals("+250788123456", contact.get_urn(TEL_SCHEME).path)
        self.assertEquals("snoop", contact.get_urn(TWITTER_SCHEME).path)
        self.assertEquals("Eminem", contact.name)
        self.assertEquals('eng', contact.language)
        self.assertEquals(self.org, contact.org)

        # try to update with an unparseable phone number
        response = self.postJSON(url, dict(name='Eminem', phone='nope'))
        self.assertResponseError(response, 'phone', "Invalid phone number: 'nope'")

        # try to update with an with an invalid phone number
        response = self.postJSON(url, dict(name='Eminem', phone='+120012301'))
        self.assertResponseError(response, 'phone', "Invalid phone number: '+120012301'")

        # try to update with both phone and urns field
        response = self.postJSON(url, dict(name='Eminem', phone='+250788123456', urns=['tel:+250788123456']))
        self.assertResponseError(response, 'non_field_errors', "Cannot provide both urns and phone parameters together")

        # update the contact using uuid, URNs will remain the same
        response = self.postJSON(url, dict(name="Mathers", uuid=contact.uuid))
        self.assertEquals(201, response.status_code)

        contact = Contact.objects.get()
        self.assertEquals("+250788123456", contact.get_urn(TEL_SCHEME).path)
        self.assertEquals("snoop", contact.get_urn(TWITTER_SCHEME).path)
        self.assertEquals("Mathers", contact.name)
        self.assertEquals('eng', contact.language)
        self.assertEquals(self.org, contact.org)

        # update the contact using uuid, this time change the urns to just the phone number
        response = self.postJSON(url, dict(name="Mathers", uuid=contact.uuid, urns=['tel:+250788123456']))
        self.assertEquals(201, response.status_code)

        contact = Contact.objects.get()
        self.assertEquals("+250788123456", contact.get_urn(TEL_SCHEME).path)
        self.assertFalse(contact.get_urn(TWITTER_SCHEME))
        self.assertEquals("Mathers", contact.name)
        self.assertEquals('eng', contact.language)
        self.assertEquals(self.org, contact.org)

        # try to update a contact using an invalid UUID
        response = self.postJSON(url, dict(name="Mathers", uuid='nope', urns=['tel:+250788123456']))
        self.assertResponseError(response, 'uuid', "Unable to find contact with UUID: nope")

        # try to update a contact using an invalid URN
        response = self.postJSON(url, dict(name="Mathers", uuid=contact.uuid, urns=['uh:nope']))
        self.assertResponseError(response, 'urns', "Invalid URN: 'uh:nope'")

        with AnonymousOrg(self.org):
            # anon orgs can't update contacts
            response = self.postJSON(url, dict(name="Mathers", uuid=contact.uuid))
            self.assertResponseError(response, 'non_field_errors', "Cannot update contacts on anonymous organizations, can only create")

        # finally try clearing our language
        response = self.postJSON(url, dict(phone='+250788123456', language=None))
        self.assertEquals(201, response.status_code)

        contact = Contact.objects.get()
        self.assertEquals(None, contact.language)

        # update the contact using urns field, matching on one URN, adding another
        response = self.postJSON(url, dict(name='Dr Dre', urns=['tel:+250788123456', 'twitter:drdre'], language='eng'))
        self.assertEquals(201, response.status_code)

        contact = Contact.objects.get()
        contact_urns = [urn.urn for urn in contact.urns.all().order_by('scheme', 'path')]
        self.assertEquals(["tel:+250788123456", "twitter:drdre"], contact_urns)
        self.assertEquals("Dr Dre", contact.name)
        self.assertEquals(self.org, contact.org)

        # try to update the contact with and un-parseable urn
        response = self.postJSON(url, dict(name='Dr Dre', urns=['tel250788123456']))
        self.assertResponseError(response, 'urns', "Unable to parse URN: 'tel250788123456'")

        # try to post a new group with a blank name
        response = self.postJSON(url, dict(phone='+250788123456', groups=["  "]))
        self.assertResponseError(response, 'groups', "This field may not be blank.")

        # try to post a new group with invalid name
        response = self.postJSON(url, dict(phone='+250788123456', groups=["+People"]))
        self.assertResponseError(response, 'groups', "Invalid group name: '+People'")

        # add contact to a new group by name
        response = self.postJSON(url, dict(phone='+250788123456', groups=["Music Artists"]))
        artists = ContactGroup.user_groups.get(name="Music Artists")
        self.assertEquals(201, response.status_code)
        self.assertEquals("Music Artists", artists.name)
        self.assertEqual(1, artists.contacts.count())
        self.assertEqual(1, artists.get_member_count())  # check trigger-based count

        # remove contact from a group by name
        response = self.postJSON(url, dict(phone='+250788123456', groups=[]))
        artists = ContactGroup.user_groups.get(name="Music Artists")
        self.assertEquals(201, response.status_code)
        self.assertEqual(0, artists.contacts.count())
        self.assertEqual(0, artists.get_member_count())

        # add contact to a existing group by UUID
        response = self.postJSON(url, dict(phone='+250788123456', group_uuids=[artists.uuid]))
        artists = ContactGroup.user_groups.get(name="Music Artists")
        self.assertEquals(201, response.status_code)
        self.assertEquals("Music Artists", artists.name)
        self.assertEqual(1, artists.contacts.count())
        self.assertEqual(1, artists.get_member_count())

        # specifying both groups and group_uuids should return error
        response = self.postJSON(url, dict(phone='+250788123456', groups=[artists.name], group_uuids=[artists.uuid]))
        self.assertEquals(400, response.status_code)

        # specifying invalid group_uuid should return error
        response = self.postJSON(url, dict(phone='+250788123456', group_uuids=['nope']))
        self.assertResponseError(response, 'group_uuids', "Unable to find contact group with uuid: nope")

        # can't add a contact to a group if they're blocked
        contact.block()
        response = self.postJSON(url, dict(phone='+250788123456', groups=["Dancers"]))
        self.assertEqual(response.status_code, 400)
        self.assertResponseError(response, 'non_field_errors', "Cannot add blocked contact to groups")

        contact.unblock()
        artists.contacts.add(contact)

        # try updating a non-existent field
        response = self.postJSON(url, dict(phone='+250788123456', fields={"real_name": "Andy"}))
        self.assertEquals(400, response.status_code)
        self.assertIsNone(contact.get_field('real_name'))

        # create field and try again
        ContactField.objects.create(org=self.org, key='real_name', label="Real Name", value_type='T')
        response = self.postJSON(url, dict(phone='+250788123456', fields={"real_name": "Andy"}))
        contact = Contact.objects.get()
        self.assertContains(response, "Andy", status_code=201)
        self.assertEquals("Andy", contact.get_field_display("real_name"))

        # update field via label (deprecated but allowed)
        response = self.postJSON(url, dict(phone='+250788123456', fields={"Real Name": "Andre"}))
        contact = Contact.objects.get()
        self.assertContains(response, "Andre", status_code=201)
        self.assertEquals("Andre", contact.get_field_display("real_name"))

        # try when contact field have same key and label
        state = ContactField.objects.create(org=self.org, key='state', label="state", value_type='T')
        response = self.postJSON(url, dict(phone='+250788123456', fields={"state": "IL"}))
        self.assertContains(response, "IL", status_code=201)
        contact = Contact.objects.get()
        self.assertEquals("IL", contact.get_field_display("state"))
        self.assertEquals("Andre", contact.get_field_display("real_name"))

        # try when contact field is not active
        ContactField.objects.filter(org=self.org, key='state').update(is_active=False)
        response = self.postJSON(url, dict(phone='+250788123456', fields={"state": "VA"}))
        self.assertContains(response, "Invalid", status_code=400)
        self.assertEquals("IL", Value.objects.get(contact=contact, contact_field=state).string_value)   # unchanged

        drdre = Contact.objects.get()

        # add another contact
        jay_z = self.create_contact("Jay-Z", number="123555")
        ContactField.get_or_create(self.org, 'registration_date', "Registration Date", None, DATETIME)
        jay_z.set_field('registration_date', "2014-12-31 03:04:00")

        # try to update using URNs from two different contacts
        response = self.postJSON(url, dict(name="Iggy", urns=['tel:+250788123456', 'tel:123555']))
        self.assertEqual(response.status_code, 400)
        self.assertResponseError(response, 'non_field_errors', "URNs are used by multiple contacts")

        # fetch all with blank query
        self.clear_cache()
        response = self.fetchJSON(url, "")
        self.assertEquals(200, response.status_code)
        self.assertEqual(len(response.json['results']), 2)

        self.assertEqual(response.json['results'][1]['name'], "Dr Dre")
        self.assertEqual(response.json['results'][1]['urns'], ['twitter:drdre', 'tel:+250788123456'])
        self.assertEqual(response.json['results'][1]['fields'], {'real_name': "Andre", 'registration_date': None})
        self.assertEqual(response.json['results'][1]['group_uuids'], [artists.uuid])
        self.assertEqual(response.json['results'][1]['groups'], ["Music Artists"])
        self.assertEqual(response.json['results'][1]['blocked'], False)
        self.assertEqual(response.json['results'][1]['failed'], False)

        self.assertEqual(response.json['results'][0]['name'], "Jay-Z")
        self.assertEqual(response.json['results'][0]['fields'], {'real_name': None,
                                                                 'registration_date': "2014-12-31T01:04:00.000000Z"})

        # search using deprecated phone field
        response = self.fetchJSON(url, "phone=%2B250788123456")
        self.assertResultCount(response, 1)
        self.assertContains(response, "Dr Dre")

        # non-matching phone number
        response = self.fetchJSON(url, "phone=%2B250788123000")
        self.assertResultCount(response, 0)

        # search using urns field
        response = self.fetchJSON(url, 'deleted=false&urns=' + urlquote_plus("tel:+250788123456"))
        self.assertResultCount(response, 1)
        self.assertContains(response, "Dr Dre")

        # search using urns list
        response = self.fetchJSON(url, 'urns=%s&urns=%s' % (urlquote_plus("tel:+250788123456"), urlquote_plus("tel:123555")))
        self.assertResultCount(response, 2)

        # search deleted contacts
        response = self.fetchJSON(url, 'deleted=true')
        self.assertResultCount(response, 0)

        # search by group
        response = self.fetchJSON(url, "group=Music+Artists")
        self.assertResultCount(response, 1)
        self.assertContains(response, "Dr Dre")

        actors = self.create_group('Actors', [jay_z])
        response = self.fetchJSON(url, "group=Music+Artists&group=Actors")
        self.assertResultCount(response, 2)

        response = self.fetchJSON(url, "group_uuids=%s" % artists.uuid)
        self.assertResultCount(response, 1)
        self.assertContains(response, "Dr Dre")

        # search using uuid
        response = self.fetchJSON(url, 'uuid=' + drdre.uuid)
        self.assertResultCount(response, 1)
        self.assertContains(response, "Dr Dre")

        response = self.fetchJSON(url, 'uuid=%s&uuid=%s' % (drdre.uuid, jay_z.uuid))
        self.assertResultCount(response, 2)

        after_dre = drdre.modified_on + timedelta(microseconds=2000)
        response = self.fetchJSON(url, 'after=' + datetime_to_json_date(after_dre))
        self.assertResultCount(response, 1)
        self.assertContains(response, "Jay-Z")

        before_jayz = jay_z.modified_on - timedelta(microseconds=2000)
        response = self.fetchJSON(url, 'before=' + datetime_to_json_date(before_jayz))
        self.assertResultCount(response, 1)
        self.assertContains(response, "Dr Dre")

        response = self.fetchJSON(url, 'after=%s&before=%s' % (datetime_to_json_date(after_dre),
                                                               datetime_to_json_date(before_jayz)))
        self.assertResultCount(response, 0)

        # check anon org case
        with AnonymousOrg(self.org):
            # check no phone numbers in response
            response = self.fetchJSON(url, "")
            self.assertResultCount(response, 2)
            self.assertContains(response, "Dr Dre")
            self.assertContains(response, 'Andre')
            self.assertNotContains(response, '0788123456')
            self.assertContains(response, "Jay-Z")
            self.assertNotContains(response, '123555')

            # try to create a contact with an external URN
            response = self.postJSON(url, dict(urns=['ext:external-id'], name="Test Name"))
            self.assertEqual(response.status_code, 201)

            # assert that that contact now exists
            contact = Contact.objects.get(name="Test Name", urns__path='external-id', urns__scheme='ext')

            # remove it
            contact.delete()

        # check deleting a contact by UUID
        response = self.deleteJSON(url, 'uuid=' + drdre.uuid)
        self.assertEqual(response.status_code, 204)
        self.assertFalse(Contact.objects.get(pk=drdre.pk).is_active)

        # fetching deleted contacts should now show drdre
        response = self.fetchJSON(url, "deleted=true")
        self.assertEquals(200, response.status_code)
        self.assertEqual(len(response.json['results']), 1)

        self.assertEquals(response.json['results'][0]['uuid'], drdre.uuid)
        self.assertIsNone(response.json['results'][0]['name'])
        self.assertFalse(response.json['results'][0]['urns'])
        self.assertFalse(response.json['results'][0]['fields'])
        self.assertFalse(response.json['results'][0]['group_uuids'])
        self.assertFalse(response.json['results'][0]['groups'])
        self.assertIsNone(response.json['results'][0]['blocked'])
        self.assertIsNone(response.json['results'][0]['failed'])

        # check deleting with wrong UUID gives 404
        response = self.deleteJSON(url, 'uuid=XYZ')
        self.assertEqual(response.status_code, 404)

        # check deleting a contact by URN
        response = self.deleteJSON(url, 'urns=tel:123555')
        self.assertEqual(response.status_code, 204)
        self.assertFalse(Contact.objects.get(pk=jay_z.pk).is_active)

        britney = self.create_contact("Britney", number='078222')

        # check blanket delete isn't allowed
        response = self.deleteJSON(url, '')
        self.assertEqual(response.status_code, 400)
        self.assertTrue(Contact.objects.get(pk=britney.pk).is_active)

        jason = self.create_contact("Jason", number="+250788334455")
        john = self.create_contact("John", number="+250788998877")

        # cannot update contact with a used phone
        response = self.postJSON(url, dict(phone="+250788998877", uuid=jason.uuid))
        self.assertEquals(400, response.status_code)

        # cannot update contact with a used phone
        response = self.postJSON(url, dict(urns=['tel:+250788998877'], uuid=jason.uuid))
        self.assertEquals(400, response.status_code)

        # check deleting by list of UUID
        response = self.deleteJSON(url, 'uuid=%s&uuid=%s' % (jason.uuid, john.uuid))
        self.assertEqual(response.status_code, 204)
        self.assertFalse(Contact.objects.get(pk=jason.pk).is_active)
        self.assertFalse(Contact.objects.get(pk=john.pk).is_active)

        shinonda = self.create_contact("Shinonda", number="+250788112233")
        chad = self.create_contact("Chad", number="+250788223344")

        response = self.deleteJSON(url, 'urns=%s&urns=%s' % (urlquote_plus("tel:+250788112233"), urlquote_plus("tel:+250788223344")))
        self.assertEqual(response.status_code, 204)
        self.assertFalse(Contact.objects.get(pk=shinonda.pk).is_active)
        self.assertFalse(Contact.objects.get(pk=chad.pk).is_active)

        # add a naked contact
        response = self.postJSON(url, dict())
        self.assertIsNotNone(json.loads(response.content)['uuid'])
        self.assertEquals(201, response.status_code)

    def test_api_contacts_with_multiple_pages(self):
        url = reverse('api.v1.contacts')

        # bulk create more contacts than fits on one page
        contacts = []
        for c in range(0, 300):
            contacts.append(Contact(org=self.org, name="Minion %d" % (c + 1),
                                    created_by=self.admin, modified_by=self.admin))
        Contact.objects.all().delete()
        Contact.objects.bulk_create(contacts)

        # login as administrator
        self.login(self.admin)

        # page is implicit
        response = self.fetchJSON(url)
        self.assertEqual(200, response.status_code)
        self.assertResultCount(response, 300)
        self.assertEqual(response.json['results'][0]['name'], "Minion 300")

        Contact.objects.create(org=self.org, name="Minion 301", created_by=self.admin, modified_by=self.admin)

        # page 1 request always recalculates count
        response = self.fetchJSON(url, 'page=1')
        self.assertResultCount(response, 301)
        self.assertEqual(response.json['results'][0]['name'], "Minion 301")

        Contact.objects.create(org=self.org, name="Minion 302", created_by=self.admin, modified_by=self.admin)

        # other page numbers won't
        response = self.fetchJSON(url, 'page=2')
        self.assertResultCount(response, 301)
        self.assertEqual(response.json['results'][0]['name'], "Minion 52")

        # handle non-ascii chars in params
        response = self.fetchJSON(url, 'page=1&test=é')
        self.assertResultCount(response, 302)

        Contact.objects.create(org=self.org, name="Minion 303", created_by=self.admin, modified_by=self.admin)

        # should force calculation for new query (e != é)
        response = self.fetchJSON(url, 'page=2&test=e')
        self.assertResultCount(response, 303)

    def test_api_fields(self):
        url = reverse('api.v1.contactfields')

        # 403 if not logged in
        self.assert403(url)

        # login as plain user
        self.login(self.user)
        self.assert403(url)

        # login as administrator
        self.login(self.admin)

        # browse endpoint as HTML docs
        response = self.fetchHTML(url)
        self.assertEqual(response.status_code, 200)

        # no fields yet
        response = self.fetchJSON(url)
        self.assertResultCount(response, 0)

        # add a field
        response = self.postJSON(url, dict(label='Real Age', value_type='T'))
        self.assertEquals(201, response.status_code)

        # should be one field now
        field = ContactField.objects.get()
        self.assertEquals('Real Age', field.label)
        self.assertEquals('T', field.value_type)
        self.assertEquals('real_age', field.key)
        self.assertEquals(self.org, field.org)

        # update that field to change value type
        response = self.postJSON(url, dict(key='real_age', label='Actual Age', value_type='N'))
        self.assertEquals(201, response.status_code)
        field = ContactField.objects.get()
        self.assertEquals('Actual Age', field.label)
        self.assertEquals('N', field.value_type)
        self.assertEquals('real_age', field.key)
        self.assertEquals(self.org, field.org)

        # update with invalid value type
        response = self.postJSON(url, dict(key='real_age', value_type='X'))
        self.assertEquals(400, response.status_code)
        self.assertResponseError(response, 'value_type', "Invalid field value type")

        # update without label
        response = self.postJSON(url, dict(key='real_age', value_type='N'))
        self.assertEquals(400, response.status_code)
        self.assertResponseError(response, 'label', "This field is required.")

        # update without value type
        response = self.postJSON(url, dict(key='real_age', label='Actual Age'))
        self.assertEquals(400, response.status_code)
        self.assertResponseError(response, 'value_type', "This field is required.")

        # create with invalid label
        response = self.postJSON(url, dict(label='!@#', value_type='T'))
        self.assertEquals(400, response.status_code)
        self.assertResponseError(response, 'label', "Field can only contain letters, numbers and hypens")

        # create with label that would be an invalid key
        response = self.postJSON(url, dict(label='Name', value_type='T'))
        self.assertEquals(400, response.status_code)
        self.assertResponseError(response, 'non_field_errors', "Generated key for 'Name' is invalid or a reserved name")

        # create with key specified
        response = self.postJSON(url, dict(key='real_age_2', label="Actual Age 2", value_type='N'))
        self.assertEquals(201, response.status_code)
        field = ContactField.objects.get(key='real_age_2')
        self.assertEqual(field.label, "Actual Age 2")
        self.assertEqual(field.value_type, 'N')

        # create with invalid key specified
        response = self.postJSON(url, dict(key='name', label='Real Name', value_type='T'))
        self.assertEquals(400, response.status_code)
        self.assertResponseError(response, 'key', "Field is invalid or a reserved name")

    def test_api_contact_actions(self):
        url = reverse('api.v1.contact_actions')

        # 403 if not logged in
        self.assert403(url)

        # login as plain user
        self.login(self.user)
        self.assert403(url)

        # login as administrator
        self.login(self.admin)

        # browse endpoint as HTML docs
        response = self.fetchHTML(url)
        self.assertEqual(response.status_code, 405)  # because endpoint doesn't support GET

        # create some contacts to act on
        self.joe.delete()
        contact1 = self.create_contact("Ann", '+250788000001')
        contact2 = self.create_contact("Bob", '+250788000002')
        contact3 = self.create_contact("Cat", '+250788000003')
        contact4 = self.create_contact("Don", '+250788000004')  # a blocked contact
        contact5 = self.create_contact("Eve", '+250788000005')  # a deleted contact
        contact4.block()
        contact5.release()
        test_contact = Contact.get_test_contact(self.user)

        group = ContactGroup.get_or_create(self.org, self.admin, "Testers")

        # start contacts in a flow
        flow = self.create_flow()
        flow.start([], [contact1, contact2, contact3])
        runs = FlowRun.objects.filter(flow=flow)

        # try adding more contacts to group than this endpoint is allowed to operate on at one time
        response = self.postJSON(url, dict(contacts=[unicode(x) for x in range(101)],
                                           action='add', group="Testers"))
        self.assertResponseError(response, 'contacts', "Maximum of 100 contacts allowed")

        # try adding all contacts to a group
        response = self.postJSON(url, dict(contacts=[contact1.uuid, contact2.uuid, contact3.uuid, contact4.uuid,
                                                     contact5.uuid, test_contact.uuid],
                                           action='add', group="Testers"))

        # error reporting that the deleted and test contacts are invalid
        self.assertResponseError(response, 'contacts',
                                 "Some UUIDs are invalid: %s, %s" % (contact5.uuid, test_contact.uuid))

        # try adding a blocked contact to a group
        response = self.postJSON(url, dict(contacts=[contact1.uuid, contact2.uuid, contact3.uuid, contact4.uuid],
                                           action='add', group="Testers"))

        # error reporting that the deleted and test contacts are invalid
        self.assertResponseError(response, 'non_field_errors', "Blocked cannot be added to groups: %s" % contact4.uuid)

        # add valid contacts to the group by name
        response = self.postJSON(url, dict(contacts=[contact1.uuid, contact2.uuid],
                                           action='add', group="Testers"))
        self.assertEquals(204, response.status_code)
        self.assertEqual(set(group.contacts.all()), {contact1, contact2})

        # try to add to a non-existent group
        response = self.postJSON(url, dict(contacts=[contact1.uuid], action='add', group='Spammers'))
        self.assertResponseError(response, 'group', "No such group: Spammers")

        # add contact 3 to a group by its UUID
        response = self.postJSON(url, dict(contacts=[contact3.uuid], action='add', group_uuid=group.uuid))
        self.assertEqual(response.status_code, 204)
        self.assertEqual(set(group.contacts.all()), {contact1, contact2, contact3})

        # try adding with invalid group UUID
        response = self.postJSON(url, dict(contacts=[contact3.uuid], action='add', group_uuid='nope'))
        self.assertResponseError(response, 'group_uuid', "No such group with UUID: nope")

        # remove contact 2 from group by its name
        response = self.postJSON(url, dict(contacts=[contact2.uuid], action='remove', group='Testers'))
        self.assertEqual(response.status_code, 204)
        self.assertEqual(set(group.contacts.all()), {contact1, contact3})

        # and remove contact 3 from group by its UUID
        response = self.postJSON(url, dict(contacts=[contact3.uuid], action='remove', group_uuid=group.uuid))
        self.assertEqual(response.status_code, 204)
        self.assertEqual(set(group.contacts.all()), {contact1})

        # try to add to group without specifying a group
        response = self.postJSON(url, dict(contacts=[contact1.uuid], action='add'))
        self.assertResponseError(response, 'non_field_errors', "For action add you should also specify group or group_uuid")
        response = self.postJSON(url, dict(contacts=[contact1.uuid], action='add'))
        self.assertResponseError(response, 'non_field_errors', "For action add you should also specify group or group_uuid")
        response = self.postJSON(url, dict(contacts=[contact1.uuid], action='add', group=''))
        self.assertResponseError(response, 'group', "This field may not be blank.")

        # try to block all contacts
        response = self.postJSON(url, dict(contacts=[contact1.uuid, contact2.uuid, contact3.uuid, contact4.uuid,
                                                     contact5.uuid, test_contact.uuid],
                                           action='block'))
        self.assertResponseError(response, 'contacts',
                                 "Some UUIDs are invalid: %s, %s" % (contact5.uuid, test_contact.uuid))

        # block all valid contacts
        response = self.postJSON(url, dict(contacts=[contact1.uuid, contact2.uuid, contact3.uuid, contact4.uuid],
                                           action='block'))
        self.assertEqual(response.status_code, 204)
        self.assertEqual(set(Contact.objects.filter(is_blocked=True)), {contact1, contact2, contact3, contact4})

        # unblock contact 1
        response = self.postJSON(url, dict(contacts=[contact1.uuid], action='unblock'))
        self.assertEqual(response.status_code, 204)
        self.assertEqual(set(Contact.objects.filter(is_blocked=False)), {contact1, contact5, test_contact})
        self.assertEqual(set(Contact.objects.filter(is_blocked=True)), {contact2, contact3, contact4})

        # expire contacts 1 and 2 from any active runs
        response = self.postJSON(url, dict(contacts=[contact1.uuid, contact2.uuid], action='expire'))
        self.assertEqual(response.status_code, 204)
        self.assertFalse(FlowRun.objects.filter(contact__in=[contact1, contact2], is_active=True).exists())
        self.assertTrue(FlowRun.objects.filter(contact=contact3, is_active=True).exists())

        # delete contacts 1 and 2
        response = self.postJSON(url, dict(contacts=[contact1.uuid, contact2.uuid], action='delete'))
        self.assertEqual(response.status_code, 204)
        self.assertEqual(set(Contact.objects.filter(is_active=False)), {contact1, contact2, contact5})
        self.assertEqual(set(Contact.objects.filter(is_active=True)), {contact3, contact4, test_contact})

        # try to provide a group for a non-group action
        response = self.postJSON(url, dict(contacts=[contact3.uuid], action='block', group='Testers'))
        self.assertResponseError(response, 'non_field_errors', "For action block you should not specify group or group_uuid")

        # try to invoke an invalid action
        response = self.postJSON(url, dict(contacts=[contact3.uuid], action='like'))
        self.assertResponseError(response, 'action', "Invalid action name: like")

    def test_api_messages(self):
        url = reverse('api.v1.messages')

        # 403 if not logged in
        self.assert403(url)

        # login as plain user
        self.login(self.user)
        self.assert403(url)

        # login as administrator
        self.login(self.admin)

        # browse endpoint as HTML docs
        response = self.fetchHTML(url)
        self.assertEqual(response.status_code, 200)

        # no broadcasts yet
        response = self.fetchJSON(url)
        self.assertResultCount(response, 0)

        # add a broadcast with deprecated phone field
        response = self.postJSON(url, dict(phone='250788123123', text='test1'))
        self.assertEquals(201, response.status_code)

        # should be one broadcast and one SMS
        self.assertEquals(1, Broadcast.objects.all().count())
        self.assertEquals(1, Msg.all_messages.all().count())

        broadcast = Broadcast.objects.get()
        self.assertEquals("test1", broadcast.text)
        self.assertEquals(self.admin.get_org(), broadcast.org)

        sms = Msg.all_messages.get()
        self.assertEquals("test1", sms.text)
        self.assertEquals("+250788123123", sms.contact.get_urn(TEL_SCHEME).path)
        self.assertEquals(self.admin.get_org(), sms.org)
        self.assertEquals(self.channel, sms.channel)
        self.assertEquals(broadcast, sms.broadcast)

        Msg.all_messages.all().delete()
        Broadcast.objects.all().delete()

        # add a broadcast with urns field
        response = self.postJSON(url, dict(text='test1', urn=['tel:+250788123123']))
        self.assertEquals(201, response.status_code)

        # should be one broadcast and one SMS
        self.assertEquals(1, Broadcast.objects.all().count())
        self.assertEquals(1, Msg.all_messages.all().count())

        broadcast = Broadcast.objects.get()
        self.assertEquals("test1", broadcast.text)
        self.assertEquals(self.admin.get_org(), broadcast.org)

        Msg.all_messages.all().delete()
        Broadcast.objects.all().delete()

        # add a broadcast using a contact uuid
        contact = Contact.objects.get(urns__path='+250788123123')
        response = self.postJSON(url, dict(text='test1', contact=[contact.uuid]))
        self.assertEquals(201, response.status_code)

        # should be one broadcast and one SMS
        self.assertEquals(1, Broadcast.objects.all().count())
        self.assertEquals(1, Msg.all_messages.all().count())

        broadcast = Broadcast.objects.get()
        self.assertEquals("test1", broadcast.text)
        self.assertEquals(self.admin.get_org(), broadcast.org)

        msg1 = Msg.all_messages.get()
        self.assertEquals("test1", msg1.text)
        self.assertEquals("+250788123123", msg1.contact.get_urn(TEL_SCHEME).path)
        self.assertEquals(self.admin.get_org(), msg1.org)
        self.assertEquals(self.channel, msg1.channel)
        self.assertEquals(broadcast, msg1.broadcast)

        # fetch by message id
        response = self.fetchJSON(url, "id=%d" % msg1.pk)
        self.assertResultCount(response, 1)
        self.assertEqual(response.json['results'][0]['id'], msg1.pk)
        self.assertEqual(response.json['results'][0]['broadcast'], msg1.broadcast.pk)
        self.assertEqual(response.json['results'][0]['text'], msg1.text)
        self.assertEqual(response.json['results'][0]['direction'], 'O')
        self.assertEqual(response.json['results'][0]['contact'], contact.uuid)
        self.assertEqual(response.json['results'][0]['urn'], 'tel:+250788123123')

        response = self.fetchJSON(url, "status=Q&before=2030-01-01T00:00:00.000&after=2010-01-01T00:00:00.000&phone=%%2B250788123123&channel=%d" % self.channel.pk)
        self.assertEquals(200, response.status_code)
        self.assertContains(response, "test1")
        self.assertContains(response, "tel:+250788123123")

        # filter by contact uuid
        response = self.fetchJSON(url, "status=Q&before=2030-01-01T00:00:00.000&after=2010-01-01T00:00:00.000&contact=%s" % contact.uuid)
        self.assertEquals(200, response.status_code)
        self.assertContains(response, "test1")
        self.assertContains(response, "tel:+250788123123")

        # bad dates, bad channel
        response = self.fetchJSON(url, "status=Q&before=01T00:00:00.000&after=01-01T00:00:00.000&phone=%2B250788123123&channel=-1&sms=124")
        self.assertEquals(200, response.status_code)
        self.assertNotContains(response, "test1")

        url = reverse('api.v1.sms')

        # search by deprecated phone field
        response = self.fetchJSON(url, "direction=O&status=Q&before=2030-01-01T00:00:00.000&after=2010-01-01T00:00:00.000&phone=%%2B250788123123&channel=%d" % self.channel.pk)
        self.assertEquals(200, response.status_code)
        self.assertContains(response, "test1")

        # search by urns field
        response = self.fetchJSON(url, "direction=O&status=Q&before=2030-01-01T00:00:00.000&after=2010-01-01T00:00:00.000&urn=tel:%%2B250788123123&channel=%d" % self.channel.pk)
        self.assertEquals(200, response.status_code)
        self.assertContains(response, "test1")

        response = self.fetchJSON(url, "direction=O&status=Q&before=2030-01-01T00:00:00.000&after=2010-01-01T00:00:00.000&urn=tel:%%2B250788123129&channel=%d" % self.channel.pk)
        self.assertEquals(200, response.status_code)
        self.assertResultCount(response, 0)

        # search by type
        response = self.fetchJSON(url, "type=F")
        self.assertEquals(200, response.status_code)
        self.assertResultCount(response, 0)

        response = self.fetchJSON(url, "type=I")
        self.assertEquals(200, response.status_code)
        self.assertResultCount(response, 1)

        response = self.fetchJSON(url, "status=Q&before=01T00:00:00.000&after=01-01T00:00:00.000&urn=%2B250788123123&channel=-1")
        self.assertEquals(200, response.status_code)
        self.assertNotContains(response, "test1")

        # search by text
        response = self.fetchJSON(url, "text=TEST")
        self.assertResultCount(response, 1)
        response = self.fetchJSON(url, "text=XXX")
        self.assertResultCount(response, 0)

        # search by group
        response = self.fetchJSON(url, "group=Players")
        self.assertEquals(200, response.status_code)
        self.assertResultCount(response, 0)

        players = self.create_group('Players', [])

        response = self.fetchJSON(url, "group=Players")
        self.assertEquals(200, response.status_code)
        self.assertResultCount(response, 0)

        response = self.fetchJSON(url, "group_uuids=%s" % players.uuid)
        self.assertEquals(200, response.status_code)
        self.assertResultCount(response, 0)

        players.contacts.add(contact)
        self.clear_cache()

        response = self.fetchJSON(url, "group=Players")
        self.assertEquals(200, response.status_code)
        self.assertResultCount(response, 1)

        response = self.fetchJSON(url, "group_uuids=%s" % players.uuid)
        self.assertEquals(200, response.status_code)
        self.assertResultCount(response, 1)

        # add some incoming messages and a flow message
        msg2 = Msg.create_incoming(self.channel, (TEL_SCHEME, '0788123123'), "test2")
        msg3 = Msg.create_incoming(self.channel, None, "test3", contact=self.joe)  # no URN
        msg4 = Msg.create_incoming(self.channel, (TEL_SCHEME, '0788123123'), "test4 (سلم)")

        flow = self.create_flow()
        flow.start([], [contact])
        msg5 = Msg.all_messages.get(msg_type='F')

        # check encoding
        response = self.fetchJSON(url, "id=%d" % msg4.pk)
        self.assertIn('\\u0633\\u0644\\u0645', response.content)
        self.assertEqual(response.json['results'][0]['text'], "test4 (\u0633\u0644\u0645)")

        # search by type
        response = self.fetchJSON(url, "type=F")
        self.assertEquals(200, response.status_code)
        self.assertEqual([m['id'] for m in response.json['results']], [msg5.pk])

        # search by direction
        response = self.fetchJSON(url, "direction=I")
        self.assertEquals(200, response.status_code)
        self.assertEqual([m['id'] for m in response.json['results']], [msg4.pk, msg3.pk, msg2.pk])

        # search by flow
        response = self.fetchJSON(url, "flow=%d" % flow.id)
        self.assertEqual([m['id'] for m in response.json['results']], [msg5.pk])

        response = self.fetchJSON(url, "flow=99999")
        self.assertResultCount(response, 0)

        # search by label
        response = self.fetchJSON(url, "label=Goo")
        self.assertResultCount(response, 0)

        label1 = Label.get_or_create(self.org, self.user, "Goo")
        label1.toggle_label([msg2, msg3], add=True)
        label2 = Label.get_or_create(self.org, self.user, "Boo")
        label2.toggle_label([msg2, msg4], add=True)
        label3 = Label.get_or_create(self.org, self.user, "Roo")
        label3.toggle_label([msg3, msg4], add=True)

        response = self.fetchJSON(url, "label=Goo&label=Boo")  # Goo or Boo
        self.assertEqual([m['id'] for m in response.json['results']], [msg4.pk, msg3.pk, msg2.pk])

        response = self.fetchJSON(url, "label=%2BGoo&label=%2BBoo")  # Goo and Boo
        self.assertEqual([m['id'] for m in response.json['results']], [msg2.pk])

        response = self.fetchJSON(url, "label=%2BGoo&label=Boo&label=Roo")  # Goo and (Boo or Roo)
        self.assertEqual([m['id'] for m in response.json['results']], [msg3.pk, msg2.pk])

        response = self.fetchJSON(url, "label=Goo&label=-Boo")  # Goo and not Boo
        self.assertEqual([m['id'] for m in response.json['results']], [msg3.pk])

        # search by broadcast id
        response = self.fetchJSON(url, "broadcast=%d" % broadcast.pk)
        self.assertEqual([m['id'] for m in response.json['results']], [msg1.pk])

        # check default ordering is -created_on
        response = self.fetchJSON(url, "")
        self.assertEqual([m['id'] for m in response.json['results']], [msg5.pk, msg4.pk, msg3.pk, msg2.pk, msg1.pk])

        # check archived status
        msg2.visibility = ARCHIVED
        msg2.save()
        msg3.visibility = DELETED
        msg3.save()
        response = self.fetchJSON(url, "")
        self.assertEqual([m['id'] for m in response.json['results']], [msg5.pk, msg4.pk, msg2.pk, msg1.pk])
        response = self.fetchJSON(url, "archived=1")
        self.assertEqual([m['id'] for m in response.json['results']], [msg2.pk])
        response = self.fetchJSON(url, "archived=fALsE")
        self.assertEqual([m['id'] for m in response.json['results']], [msg5.pk, msg4.pk, msg1.pk])

        # check anon org case
        with AnonymousOrg(self.org):
            response = self.fetchJSON(url, "status=Q&before=2030-01-01T00:00:00.000&after=2010-01-01T00:00:00.000&phone=%%2B250788123123&channel=%d" % self.channel.pk)
            self.assertEquals(200, response.status_code)
            self.assertContains(response, "test1")
            self.assertNotContains(response, "250788123123")

    def test_api_messages_multiple_contacts(self):
        url = reverse('api.v1.messages')
        self.login(self.admin)

        # add a broadcast
        response = self.postJSON(url, dict(phone=['250788123123', '250788123124'], text='test1'))
        self.assertEquals(201, response.status_code)

        # should be one broadcast and one SMS
        self.assertEquals(1, Broadcast.objects.all().count())
        self.assertEquals(2, Msg.all_messages.all().count())

        broadcast = Broadcast.objects.get()
        self.assertEquals("test1", broadcast.text)
        self.assertEquals(self.admin.get_org(), broadcast.org)

        msgs = Msg.all_messages.all().order_by('contact__urns__path')
        self.assertEquals(2, msgs.count())
        self.assertEquals("test1", msgs[0].text)
        self.assertEquals("+250788123123", msgs[0].contact.get_urn(TEL_SCHEME).path)
        self.assertEquals(broadcast, msgs[0].broadcast)
        self.assertEquals("test1", msgs[1].text)
        self.assertEquals("+250788123124", msgs[1].contact.get_urn(TEL_SCHEME).path)
        self.assertEquals(broadcast, msgs[1].broadcast)

        # fetch our messages list page
        response = self.fetchJSON(url)
        self.assertResultCount(response, 2)
        self.assertJSON(response, 'urn', 'tel:+250788123123')
        self.assertJSON(response, 'urn', 'tel:+250788123124')

    def test_api_messages_invalid_contact(self):
        url = reverse('api.v1.messages')
        self.login(self.admin)

        response = self.postJSON(url, dict(phone=['250788123123', '  '], text='test1'))
        self.assertEquals(400, response.status_code)

        response = self.postJSON(url, dict(phone=['250788123123', '++'], text='test1'))
        self.assertEquals(400, response.status_code)

        response = self.postJSON(url, dict(phone=['250788123123', dict(hello="world")], text='test1'))
        self.assertEquals(400, response.status_code)

        response = self.postJSON(url, dict(phone=dict(hello="world"), text='test1'))
        self.assertEquals(400, response.status_code)

    def test_api_messages_with_channel(self):
        url = reverse('api.v1.sms')
        self.login(self.admin)

        # invalid channel id
        response = self.postJSON(url, dict(channel=500, phone=['250788123123', '  '], text='test1'))
        self.assertEquals(400, response.status_code)

        # no theirs
        response = self.postJSON(url, dict(channel=self.channel2.pk, phone=['250788123123', '  '], text='test1'))
        self.assertEquals(400, response.status_code)

        # valid channel
        response = self.postJSON(url, dict(channel=self.channel.pk, phone=['250788123123'], text='test1'))
        self.assertEquals(201, response.status_code)

        sms = Msg.all_messages.get()
        self.assertEquals(self.channel.pk, sms.channel.pk)

        # remove our channel
        org2 = Org.objects.create(name="Another Org", timezone="Africa/Kigali", created_by=self.admin, modified_by=self.admin)
        self.channel.org = org2
        self.channel.save()

        # can't send
        response = self.postJSON(url, dict(phone=['250788123123'], text='test1'))
        self.assertEquals(400, response.status_code)

    def test_api_message_actions(self):
        url = reverse('api.v1.message_actions')

        # 403 if not logged in
        self.assert403(url)

        # login as plain user
        self.login(self.user)
        self.assert403(url)

        # login as administrator
        self.login(self.admin)

        # browse endpoint as HTML docs
        response = self.fetchHTML(url)
        self.assertEqual(response.status_code, 405)  # because endpoint doesn't support GET

        # create some messages to act on
        msg1 = Msg.create_incoming(self.channel, (TEL_SCHEME, '+250788123123'), 'Msg #1')
        msg2 = Msg.create_incoming(self.channel, (TEL_SCHEME, '+250788123123'), 'Msg #2')
        msg3 = Msg.create_incoming(self.channel, (TEL_SCHEME, '+250788123123'), 'Msg #3')
        msg4 = Msg.create_outgoing(self.org, self.user, self.joe, "Hi Joe")

        # add label by name to messages 1, 2 and 4
        response = self.postJSON(url, dict(messages=[msg1.pk, msg2.pk, msg4.pk], action='label', label='Test'))
        self.assertEquals(204, response.status_code)

        # check that label was created and applied to messages 1 and 2 but not 4 (because it's outgoing)
        label = Label.label_objects.get(name='Test')
        self.assertEqual(set(label.get_messages()), {msg1, msg2})

        # try to add an invalid label by name
        response = self.postJSON(url, dict(messages=[msg1.pk, msg2.pk], action='label', label='+Test'))
        self.assertResponseError(response, 'label', "Label name must not be blank or begin with + or -")

        # apply new label by its UUID to message 3
        response = self.postJSON(url, dict(messages=[msg3.pk], action='label', label_uuid=label.uuid))
        self.assertEquals(204, response.status_code)
        self.assertEqual(set(label.get_messages()), {msg1, msg2, msg3})

        # try to label with an invalid UUID
        response = self.postJSON(url, dict(messages=[msg1.pk], action='label', label_uuid='nope'))
        self.assertResponseError(response, 'label_uuid', "No such label with UUID: nope")

        # remove label from message 2 by name
        response = self.postJSON(url, dict(messages=[msg2.pk], action='unlabel', label='Test'))
        self.assertEquals(204, response.status_code)
        self.assertEqual(set(label.get_messages()), {msg1, msg3})

        # and remove from messages 1 and 3 by UUID
        response = self.postJSON(url, dict(messages=[msg1.pk, msg3.pk], action='unlabel', label_uuid=label.uuid))
        self.assertEquals(204, response.status_code)
        self.assertEqual(set(label.get_messages()), set())

        # try to label without specifying a label
        response = self.postJSON(url, dict(messages=[msg1.pk, msg2.pk], action='label'))
        self.assertResponseError(response, 'non_field_errors', "For action label you should also specify label or label_uuid")
        response = self.postJSON(url, dict(messages=[msg1.pk, msg2.pk], action='label', label=''))
        self.assertResponseError(response, 'label', "This field may not be blank.")

        # archive all messages
        response = self.postJSON(url, dict(messages=[msg1.pk, msg2.pk, msg3.pk, msg4.pk], action='archive'))
        self.assertEquals(204, response.status_code)
        self.assertEqual(set(Msg.all_messages.filter(visibility=VISIBLE)), {msg4})  # ignored as is outgoing
        self.assertEqual(set(Msg.all_messages.filter(visibility=ARCHIVED)), {msg1, msg2, msg3})

        # un-archive message 1
        response = self.postJSON(url, dict(messages=[msg1.pk], action='unarchive'))
        self.assertEquals(204, response.status_code)
        self.assertEqual(set(Msg.all_messages.filter(visibility=VISIBLE)), {msg1, msg4})
        self.assertEqual(set(Msg.all_messages.filter(visibility=ARCHIVED)), {msg2, msg3})

        # delete messages 2 and 4
        response = self.postJSON(url, dict(messages=[msg2.pk], action='delete'))
        self.assertEquals(204, response.status_code)
        self.assertEqual(set(Msg.all_messages.filter(visibility=VISIBLE)), {msg1, msg4})  # 4 ignored as is outgoing
        self.assertEqual(set(Msg.all_messages.filter(visibility=ARCHIVED)), {msg3})
        self.assertEqual(set(Msg.all_messages.filter(visibility=DELETED)), {msg2})

        # can't un-archive a deleted message
        response = self.postJSON(url, dict(messages=[msg2.pk], action='unarchive'))
        self.assertEquals(204, response.status_code)
        self.assertEqual(set(Msg.all_messages.filter(visibility=DELETED)), {msg2})

        # try to provide a label for a non-labelling action
        response = self.postJSON(url, dict(messages=[msg1.pk, msg2.pk], action='archive', label='Test2'))
        self.assertResponseError(response, 'non_field_errors', "For action archive you should not specify label or label_uuid")

        # try to invoke an invalid action
        response = self.postJSON(url, dict(messages=[msg1.pk], action='like'))
        self.assertResponseError(response, 'action', "Invalid action name: like")

    def test_api_labels(self):
        url = reverse('api.v1.labels')

        # login as administrator
        self.login(self.admin)

        # browse endpoint as HTML docs
        response = self.fetchHTML(url)
        self.assertEqual(response.status_code, 200)

        # add a label
        response = self.postJSON(url, dict(name='Screened'))
        self.assertEqual(201, response.status_code)

        # check it exists
        screened = Label.label_objects.get(name='Screened')
        self.assertIsNone(screened.folder)

        # can't create another with same name
        response = self.postJSON(url, dict(name='Screened'))
        self.assertEqual(400, response.status_code)

        # add another with a different name
        response = self.postJSON(url, dict(name='Junk'))
        self.assertEquals(201, response.status_code)

        junk = Label.label_objects.get(name='Junk')
        self.assertIsNone(junk.folder)

        # update changing name
        response = self.postJSON(url, dict(uuid=screened.uuid, name='Important'))
        self.assertEquals(201, response.status_code)

        screened = Label.label_objects.get(uuid=screened.uuid)
        self.assertEqual(screened.name, 'Important')

        # can't update name to something already used
        response = self.postJSON(url, dict(uuid=screened.uuid, name='Junk'))
        self.assertEquals(400, response.status_code)

        # can't update if UUID is invalid
        response = self.postJSON(url, dict(uuid='nope', name='Junk'))
        self.assertResponseError(response, 'uuid', "No such message label with UUID: nope")

        # now fetch all labels
        response = self.fetchJSON(url)
        self.assertResultCount(response, 2)

        # fetch by name
        response = self.fetchJSON(url, 'name=Important')
        self.assertResultCount(response, 1)
        self.assertContains(response, "Important")

        # fetch by uuid
        response = self.fetchJSON(url, 'uuid=%s' % junk.uuid)
        self.assertResultCount(response, 1)
        self.assertContains(response, "Junk")

    def test_api_authenticate(self):
        url = reverse('api.v1.authenticate')

        # fetch our html docs
        self.assertEqual(self.fetchHTML(url).status_code, 200)

        admin_group = Group.objects.get(name='Administrators')
        surveyor_group = Group.objects.get(name='Surveyors')

        # login an admin as an admin
        admin = json.loads(self.client.post(url, dict(email='Administrator', password='Administrator', role='A')).content)
        self.assertEqual(1, len(admin))
        self.assertEqual('Temba', admin[0]['name'])
        self.assertIsNotNone(APIToken.objects.filter(key=admin[0]['token'], role=admin_group).first())

        # login an admin as a surveyor
        surveyor = json.loads(self.client.post(url, dict(email='Administrator', password='Administrator', role='S')).content)
        self.assertEqual(1, len(surveyor))
        self.assertEqual('Temba', surveyor[0]['name'])
        self.assertIsNotNone(APIToken.objects.filter(key=surveyor[0]['token'], role=surveyor_group).first())

        # the keys should be different
        self.assertNotEqual(admin[0]['token'], surveyor[0]['token'])

        # don't do ssl auth check
        settings.SESSION_COOKIE_SECURE = False

        # configure our api client
        client = APIClient()

        admin_token = dict(HTTP_AUTHORIZATION='Token ' + admin[0]['token'])
        surveyor_token = dict(HTTP_AUTHORIZATION='Token ' + surveyor[0]['token'])

        # campaigns can be fetched by admin token
        client.credentials(**admin_token)
        self.assertEqual(200, client.get(reverse('api.v1.campaigns') + '.json').status_code)

        # but not by an admin's surveyor token
        client.credentials(**surveyor_token)
        self.assertEqual(403, client.get(reverse('api.v1.campaigns') + '.json').status_code)

        # but their surveyor token can get flows or contacts
        self.assertEqual(200, client.get(reverse('api.v1.flows') + '.json').status_code)
        self.assertEqual(200, client.get(reverse('api.v1.contacts') + '.json').status_code)

        # our surveyor can't login with an admin role
        response = json.loads(self.client.post(url, dict(email='Surveyor', password='Surveyor', role='A')).content)
        self.assertEqual(0, len(response))

        # but they can with a surveyor role
        response = json.loads(self.client.post(url, dict(email='Surveyor', password='Surveyor', role='S')).content)
        self.assertEqual(1, len(response))

        # and can fetch flows, contacts, and fields, but not campaigns
        client.credentials(HTTP_AUTHORIZATION='Token ' + response[0]['token'])
        self.assertEqual(200, client.get(reverse('api.v1.flows') + '.json').status_code)
        self.assertEqual(200, client.get(reverse('api.v1.contacts') + '.json').status_code)
        self.assertEqual(200, client.get(reverse('api.v1.contactfields') + '.json').status_code)
        self.assertEqual(403, client.get(reverse('api.v1.campaigns') + '.json').status_code)

    def test_api_broadcasts(self):
        url = reverse('api.v1.broadcasts')

        # login as administrator
        self.login(self.admin)

        # browse endpoint as HTML docs
        response = self.fetchHTML(url)
        self.assertEqual(response.status_code, 200)

        # try creating a broadcast with no recipients
        response = self.postJSON(url, dict(text='Hello X'))
        self.assertEqual(response.status_code, 400)

        # check creating by contact UUIDs
        frank = self.create_contact("Frank", number="0780000002", twitter="franky")
        response = self.postJSON(url, dict(contacts=[self.joe.uuid, frank.uuid], text="Hello 1"))
        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.json['text'], "Hello 1")
        self.assertEqual(response.json['status'], 'I')
        self.assertEqual(response.json['urns'], [])
        self.assertEqual(sorted(response.json['contacts']), sorted([self.joe.uuid, frank.uuid]))
        self.assertEqual(response.json['groups'], [])

        # message will have been sent in celery task
        broadcast1 = Broadcast.objects.get(pk=response.json['id'])
        self.assertEqual(broadcast1.recipient_count, 2)
        self.assertEqual(broadcast1.get_message_count(), 2)

        # try creating with invalid contact UUID
        response = self.postJSON(url, dict(contacts=['abc-123'], text="Hello X"))
        self.assertEqual(response.status_code, 400)

        # check creating by group UUIDs
        joe_and_frank = self.create_group("Joe and Frank", [self.joe, frank])
        response = self.postJSON(url, dict(groups=[joe_and_frank.uuid], text="Hello 2"))
        self.assertEqual(response.status_code, 201)
        broadcast2 = Broadcast.objects.get(text="Hello 2")
        self.assertEqual(broadcast2.recipient_count, 2)
        self.assertEqual(broadcast2.get_message_count(), 2)

        # try creating with invalid group UUID
        response = self.postJSON(url, dict(groups=['abc-123'], text="Hello X"))
        self.assertEqual(response.status_code, 400)

        # check creating by existing URNs (Joe and Frank's tel numbers)
        response = self.postJSON(url, dict(urns=['tel:0788123123', 'tel:0780000002'], text="Hello 3"))
        self.assertEqual(response.status_code, 201)
        broadcast3 = Broadcast.objects.get(text="Hello 3")
        self.assertEqual(broadcast3.recipient_count, 2)
        self.assertEqual(broadcast3.get_message_count(), 2)
        self.assertEqual(sorted([m.contact_urn.urn for m in broadcast3.get_messages()]), ['tel:+250780000002', 'tel:+250788123123'])

        # check creating by new URN
        response = self.postJSON(url, dict(urns=['tel:0780000003'], text="Hello 4"))
        self.assertEqual(response.status_code, 201)
        broadcast4 = Broadcast.objects.get(text="Hello 4")
        self.assertEqual(broadcast4.recipient_count, 1)
        self.assertEqual(broadcast4.get_message_count(), 1)
        msg = broadcast4.get_messages().first()
        self.assertIsNotNone(msg.contact)
        self.assertEqual(msg.contact_urn.urn, 'tel:+250780000003')

        # try creating with invalid URN
        response = self.postJSON(url, dict(urns=['myspace:123'], text="Hello X"))
        self.assertEqual(response.status_code, 400)

        # creating with 1 sendable and 1 unsendable URN
        response = self.postJSON(url, dict(urns=['tel:0780000003', 'twitter:bobby'], text="Hello 5"))
        self.assertEqual(response.status_code, 201)
        broadcast5 = Broadcast.objects.get(text="Hello 5")
        self.assertEqual(broadcast5.recipient_count, 2)
        self.assertEqual(broadcast5.get_message_count(), 1)
        self.assertEqual(broadcast5.get_messages().first().contact_urn.urn, 'tel:+250780000003')

        twitter = Channel.create(self.org, self.admin, None, 'TT', "Twitter", "nyaruka")

        # creating with a forced channel
        response = self.postJSON(url, dict(urns=['tel:0780000003', 'twitter:bobby'], text="Hello 6", channel=twitter.pk))
        self.assertEqual(response.status_code, 201)
        broadcast6 = Broadcast.objects.get(text="Hello 6")
        self.assertEqual(broadcast6.channel, twitter)
        self.assertEqual(broadcast6.recipient_count, 2)
        self.assertEqual(broadcast6.get_message_count(), 1)
        self.assertEqual(broadcast6.get_messages().first().contact_urn.urn, 'twitter:bobby')

        broadcast6.is_active = False
        broadcast6.save()

        # now fetch all broadcasts...
        response = self.fetchJSON(url)
        self.assertEqual(response.json['count'], 5)
        self.assertEqual([b['text'] for b in response.json['results']], ["Hello 5", "Hello 4", "Hello 3", "Hello 2", "Hello 1"])

        # fetch by id
        response = self.fetchJSON(url, 'id=%d,%d' % (broadcast2.pk, broadcast4.pk))
        self.assertEqual([b['text'] for b in response.json['results']], ["Hello 4", "Hello 2"])

        # fetch by after created_on
        response = self.fetchJSON(url, 'after=%s' % broadcast4.created_on.strftime('%Y-%m-%dT%H:%M:%S.%f'))
        self.assertEqual([b['text'] for b in response.json['results']], ["Hello 5", "Hello 4"])

        # fetch by after created_on
        response = self.fetchJSON(url, 'before=%s' % broadcast2.created_on.strftime('%Y-%m-%dT%H:%M:%S.%f'))
        self.assertEqual([b['text'] for b in response.json['results']], ["Hello 2", "Hello 1"])

        broadcast1.status = FAILED
        broadcast1.save()
        broadcast3.status = ERRORED
        broadcast3.save()

        # fetch by status
        response = self.fetchJSON(url, 'status=E,F')
        self.assertEqual([b['text'] for b in response.json['results']], ["Hello 3", "Hello 1"])

    def test_api_campaigns(self):
        url = reverse('api.v1.campaigns')

        # can't access, get 403
        self.assert403(url)

        # login as plain user
        self.login(self.user)
        self.assert403(url)

        # login as administrator
        self.login(self.admin)

        # browse endpoint as HTML docs
        response = self.fetchHTML(url)
        self.assertEqual(response.status_code, 200)

        # test that this user has a token
        self.assertTrue(self.admin.api_token)

        # blow it away
        Token.objects.all().delete()

        # should create one lazily
        self.assertTrue(self.admin.api_token)

        # this time, a 200
        response = self.fetchJSON(url)
        self.assertEquals(200, response.status_code)

        # shouldn't contain any campaigns contain our channel in the response
        self.assertResultCount(response, 0)

        # can't create a campaign without a group
        response = self.postJSON(url, dict(name="MAMA Messages"))
        self.assertEquals(400, response.status_code)

        # can't create a campaign without a name
        response = self.postJSON(url, dict(group="Expecting Mothers"))
        self.assertEquals(400, response.status_code)

        # works with both
        response = self.postJSON(url, dict(name="MAMA Messages", group="Expecting Mothers"))
        self.assertEquals(201, response.status_code)

        # should have a new group and new campaign now
        campaign1 = Campaign.objects.get()
        mothers = ContactGroup.user_groups.get()
        self.assertEqual(campaign1.org, self.org)
        self.assertEqual(campaign1.name, "MAMA Messages")
        self.assertEqual(campaign1.group, mothers)

        self.assertEqual(mothers.org, self.org)
        self.assertEqual(mothers.name, "Expecting Mothers")

        # can also create by group UUID
        response = self.postJSON(url, dict(name="MAMA Reminders", group_uuid=mothers.uuid))
        self.assertEqual(response.status_code, 201)

        campaign2 = Campaign.objects.get(name="MAMA Reminders")
        self.assertEqual(campaign2.group, mothers)

        # but error with invalid group UUID
        response = self.postJSON(url, dict(name="MAMA Reminders", group_uuid='xyz'))
        self.assertEqual(response.status_code, 400)

        # can update a campaign by id (deprecated)
        response = self.postJSON(url, dict(campaign=campaign1.pk, name="Preggie Messages", group="Expecting Mothers"))
        self.assertEqual(response.status_code, 201)

        campaign1 = Campaign.objects.get(pk=campaign1.pk)
        self.assertEqual(campaign1.name, "Preggie Messages")

        # doesn't work with an invalid id
        response = self.postJSON(url, dict(campaign=999, name="Preggie Messages", group="Expecting Mothers"))
        self.assertEqual(response.status_code, 400)

        # can also update a campaign by UUID
        response = self.postJSON(url, dict(uuid=campaign2.uuid, name="Preggie Reminders", group_uuid=mothers.uuid))
        self.assertEqual(response.status_code, 201)

        campaign2 = Campaign.objects.get(pk=campaign2.pk)
        self.assertEqual(campaign2.name, "Preggie Reminders")

        # doesn't work with an invalid UUID
        response = self.postJSON(url, dict(uuid='xyz', name="Preggie Messages", group="Expecting Mothers"))
        self.assertEqual(response.status_code, 400)

        # and doesn't work if you specify both UUID and id
        response = self.postJSON(url, dict(campaign=campaign1.pk, uuid='xyz', name="Preggie Messages", group="Expecting Mothers"))
        self.assertEqual(response.status_code, 400)

        # fetch all campaigns
        response = self.fetchJSON(url)
        self.assertResultCount(response, 2)
        self.assertEqual(response.json['results'][0]['name'], "Preggie Reminders")
        self.assertEqual(response.json['results'][0]['uuid'], campaign2.uuid)
        self.assertEqual(response.json['results'][0]['group_uuid'], campaign2.group.uuid)
        self.assertEqual(response.json['results'][0]['group'], campaign2.group.name)
        self.assertEqual(response.json['results'][0]['campaign'], campaign2.pk)
        self.assertEqual(response.json['results'][1]['name'], "Preggie Messages")

        # fetch by id (deprecated)
        response = self.fetchJSON(url, 'campaign=%d' % campaign1.pk)
        self.assertResultCount(response, 1)
        self.assertEqual(response.json['results'][0]['uuid'], campaign1.uuid)

        # fetch by UUID
        response = self.fetchJSON(url, 'uuid=%s' % campaign2.uuid)
        self.assertResultCount(response, 1)
        self.assertEqual(response.json['results'][0]['uuid'], campaign2.uuid)

    def test_api_campaign_events(self):
        url = reverse('api.v1.campaignevents')
        color_flow = self.create_flow()

        # can't access, get 403
        self.assert403(url)

        # login as plain user
        self.login(self.user)
        self.assert403(url)

        # login as administrator
        self.login(self.admin)

        # browse endpoint as HTML docs
        response = self.fetchHTML(url)
        self.assertEqual(response.status_code, 200)

        # no events to start with
        response = self.fetchJSON(url)
        self.assertResultCount(response, 0)

        mothers = ContactGroup.get_or_create(self.org, self.admin, "Expecting Mothers")
        campaign = Campaign.create(self.org, self.admin, "MAMA Reminders", mothers)

        # create by campaign id and flow id (deprecated)
        response = self.postJSON(url, dict(campaign=campaign.pk, unit='W', offset=5, relative_to="EDD",
                                           delivery_hour=-1, flow=color_flow.pk))
        self.assertEqual(response.status_code, 201)

        event1 = CampaignEvent.objects.get()
        self.assertEqual(event1.event_type, FLOW_EVENT)
        self.assertEqual(event1.campaign, campaign)
        self.assertEqual(event1.offset, 5)
        self.assertEqual(event1.unit, 'W')
        self.assertEqual(event1.relative_to.label, "EDD")
        self.assertEqual(event1.delivery_hour, -1)
        self.assertEqual(event1.message, None)
        self.assertEqual(event1.flow, color_flow)

        # try to create event with invalid campaign id and invalid flow id
        response = self.postJSON(url, dict(campaign=-123, unit='D', offset=3, relative_to="EDD",
                                           delivery_hour=9, flow=-234))
        self.assertResponseError(response, 'campaign', "No campaign with id -123")
        self.assertResponseError(response, 'flow', "No flow with id -234")

        # try to create event with invalid time unit
        response = self.postJSON(url, dict(campaign=campaign.pk, unit='X', offset=3, relative_to="EDD",
                                           delivery_hour=9, message="Time to go to the clinic"))
        self.assertResponseError(response, 'unit', "Must be one of M, H, D or W for Minute, Hour, Day or Week")

        # try to create event with invalid delivery hour
        response = self.postJSON(url, dict(campaign=campaign.pk, unit='D', offset=3, relative_to="EDD",
                                           delivery_hour=-2, message="Time to go to the clinic"))
        self.assertResponseError(response, 'delivery_hour', "Must be either -1 (for same hour) or 0-23")

        # try to create event with invalid contact field
        response = self.postJSON(url, dict(campaign=campaign.pk, unit='D', offset=3, relative_to="@!#!$",
                                           delivery_hour=-2, message="Time to go to the clinic"))
        self.assertResponseError(response, 'relative_to', "Cannot create contact field with key ''")

        # create by campaign UUID and flow UUID
        response = self.postJSON(url, dict(campaign_uuid=campaign.uuid, unit='D', offset=3, relative_to="EDD",
                                           delivery_hour=9, flow_uuid=color_flow.uuid))
        self.assertEqual(response.status_code, 201)

        event2 = CampaignEvent.objects.order_by('-pk').first()
        self.assertEqual(event2.event_type, FLOW_EVENT)
        self.assertEqual(event2.campaign, campaign)
        self.assertEqual(event2.offset, 3)
        self.assertEqual(event2.unit, 'D')
        self.assertEqual(event2.relative_to.label, "EDD")
        self.assertEqual(event2.delivery_hour, 9)
        self.assertEqual(event2.message, None)
        self.assertEqual(event2.flow, color_flow)

        # try to create event with invalid campaign UUID and invalid flow UUID
        response = self.postJSON(url, dict(campaign_uuid='nope', unit='D', offset=3, relative_to="EDD",
                                           delivery_hour=9, flow_uuid='nope'))
        self.assertResponseError(response, 'campaign_uuid', "No campaign with UUID nope")
        self.assertResponseError(response, 'flow_uuid', "No flow with UUID nope")

        # create an event for a message flow
        response = self.postJSON(url, dict(campaign_uuid=campaign.uuid, unit='D', offset=3, relative_to="EDD",
                                           delivery_hour=9, message="Time to go to the clinic"))
        self.assertEqual(response.status_code, 201)

        message_flow1 = Flow.objects.exclude(pk=color_flow.pk).get()
        event3 = CampaignEvent.objects.order_by('-pk').first()
        self.assertEqual(event3.event_type, MESSAGE_EVENT)
        self.assertEqual(event3.campaign, campaign)
        self.assertEqual(event3.offset, 3)
        self.assertEqual(event3.unit, 'D')
        self.assertEqual(event3.relative_to.label, "EDD")
        self.assertEqual(event3.delivery_hour, 9)
        self.assertEqual(event3.message, "Time to go to the clinic")
        self.assertEqual(event3.flow, message_flow1)

        # try to create event without flow or message
        response = self.postJSON(url, dict(campaign_uuid=campaign.uuid, unit='D', offset=3, relative_to="EDD",
                                           delivery_hour=9))
        self.assertResponseError(response, 'non_field_errors', "Must specify either a flow or a message for the event")

        # try to create event with both flow and message
        response = self.postJSON(url, dict(campaign_uuid=campaign.uuid, unit='D', offset=3, relative_to="EDD",
                                           delivery_hour=9, flow_uuid=color_flow.uuid, message="Time to go"))
        self.assertResponseError(response, 'non_field_errors', "Events cannot have both a message and a flow")

        # update an event by id (deprecated)
        response = self.postJSON(url, dict(event=event1.pk, unit='D', offset=30, relative_to="EDD",
                                           delivery_hour=-1, message="Time to go to the clinic. Thanks"))
        self.assertEqual(response.status_code, 201)

        message_flow2 = Flow.objects.exclude(pk__in=[color_flow.pk, message_flow1.pk]).get()
        event1.refresh_from_db()
        self.assertEqual(event1.event_type, MESSAGE_EVENT)
        self.assertEqual(event1.campaign, campaign)
        self.assertEqual(event1.offset, 30)
        self.assertEqual(event1.unit, 'D')
        self.assertEqual(event1.relative_to.label, "EDD")
        self.assertEqual(event1.delivery_hour, -1)
        self.assertEqual(event1.message, "Time to go to the clinic. Thanks")
        self.assertEqual(event1.flow, message_flow2)

        # update an event that is already a message event with a new message
        response = self.postJSON(url, dict(event=event3.pk, unit='D', offset=3, relative_to="EDD",
                                           delivery_hour=9, message="Time to go to the clinic. NOW!"))
        self.assertEqual(response.status_code, 201)

        event3.refresh_from_db()
        self.assertEqual(event3.event_type, MESSAGE_EVENT)
        self.assertEqual(event3.message, "Time to go to the clinic. NOW!")
        self.assertEqual(event3.flow, message_flow1)

        # try tp update an event by invalid id
        response = self.postJSON(url, dict(event=-123, unit='D', offset=30, relative_to="EDD",
                                           delivery_hour=-1, message="Time to go to the clinic. Thanks"))
        self.assertResponseError(response, 'event', "No event with id -123")

        # update an event by UUID
        other_flow = Flow.copy(color_flow, self.user)
        response = self.postJSON(url, dict(uuid=event2.uuid, unit='W', offset=3, relative_to="EDD",
                                           delivery_hour=5, flow_uuid=other_flow.uuid))
        self.assertEqual(response.status_code, 201)

        event2.refresh_from_db()
        self.assertEqual(event2.event_type, FLOW_EVENT)
        self.assertEqual(event2.campaign, campaign)
        self.assertEqual(event2.offset, 3)
        self.assertEqual(event2.unit, 'W')
        self.assertEqual(event2.relative_to.label, "EDD")
        self.assertEqual(event2.delivery_hour, 5)
        self.assertEqual(event2.message, None)
        self.assertEqual(event2.flow, other_flow)

        # try tp update an event by invalid UUID
        response = self.postJSON(url, dict(uuid='nope', unit='D', offset=30, relative_to="EDD",
                                           delivery_hour=-1, message="Time to go to the clinic. Thanks"))
        self.assertResponseError(response, 'uuid', "No event with UUID nope")

        # try to specify campaign when updating event (not allowed)
        response = self.postJSON(url, dict(uuid=event2.uuid, campaign_uuid=campaign.uuid, unit='D', offset=3,
                                           relative_to="EDD", delivery_hour=9, flow_uuid=color_flow.uuid))
        self.assertEqual(response.status_code, 400)

        # fetch all events
        response = self.fetchJSON(url)
        self.assertResultCount(response, 3)
        self.assertEqual(response.json['results'][0]['uuid'], event3.uuid)
        self.assertEqual(response.json['results'][0]['campaign_uuid'], campaign.uuid)
        self.assertEqual(response.json['results'][0]['campaign'], campaign.pk)
        self.assertEqual(response.json['results'][0]['relative_to'], "EDD")
        self.assertEqual(response.json['results'][0]['offset'], 3)
        self.assertEqual(response.json['results'][0]['unit'], 'D')
        self.assertEqual(response.json['results'][0]['delivery_hour'], 9)
        self.assertEqual(response.json['results'][0]['flow_uuid'], None)
        self.assertEqual(response.json['results'][0]['flow'], None)
        self.assertEqual(response.json['results'][0]['message'], "Time to go to the clinic. NOW!")
        self.assertEqual(response.json['results'][1]['uuid'], event2.uuid)
        self.assertEqual(response.json['results'][1]['flow_uuid'], other_flow.uuid)
        self.assertEqual(response.json['results'][1]['flow'], other_flow.pk)
        self.assertEqual(response.json['results'][1]['message'], None)

        # delete event by UUID
        response = self.deleteJSON(url, "uuid=%s" % event1.uuid)
        self.assertEqual(response.status_code, 204)

        # check that we've been deleted
        self.assertFalse(CampaignEvent.objects.get(pk=event1.pk).is_active)

        # deleting again is a 404
        response = self.deleteJSON(url, "uuid=%s" % event1.uuid)
        self.assertEqual(response.status_code, 404)

    def test_api_groups(self):
        url = reverse('api.v1.contactgroups')

        # 403 if not logged in
        self.assert403(url)

        # login as plain user
        self.login(self.user)
        self.assert403(url)

        # login as administrator
        self.login(self.admin)

        # browse endpoint as HTML docs
        response = self.fetchHTML(url)
        self.assertEqual(response.status_code, 200)

        # no groups yet
        response = self.fetchJSON(url)
        self.assertResultCount(response, 0)

        # add 2 groups
        joe = self.create_contact("Joe", number="123")
        frank = self.create_contact("Frank", number="1234")
        reporters = self.create_group("Reporters", [joe, frank])
        just_joe = self.create_group("Just Joe", [joe])

        # fetch all
        response = self.fetchJSON(url)
        self.assertResultCount(response, 2)

        # reverse order by created_on
        self.assertEqual(response.json['results'][0]['name'], "Just Joe")
        self.assertEqual(response.json['results'][1]['name'], "Reporters")

        # fetch by partial name
        response = self.fetchJSON(url, "name=Report")
        self.assertResultCount(response, 1)
        self.assertJSON(response, 'name', "Reporters")
        self.assertJSON(response, 'uuid', unicode(reporters.uuid))
        self.assertJSON(response, 'size', 2)

        # fetch by UUID
        response = self.fetchJSON(url, "uuid=%s" % just_joe.uuid)
        self.assertResultCount(response, 1)
        self.assertJSON(response, 'name', "Just Joe")
        self.assertJSON(response, 'uuid', unicode(just_joe.uuid))
        self.assertJSON(response, 'size', 1)

        just_frank = self.create_group("Just Frank", [frank])

        response = self.fetchJSON(url)
        self.assertResultCount(response, 3)

        # fetch filtering by UUID list
        response = self.fetchJSON(url, "uuid=%s&uuid=%s" % (just_joe.uuid, just_frank.uuid))
        self.assertResultCount(response, 2)
