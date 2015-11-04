# -*- coding: utf-8 -*-

from __future__ import unicode_literals

import calendar
import json
import time
import uuid
import pytz
import xml.etree.ElementTree as ET

from datetime import datetime, timedelta
from django.conf import settings
from django.contrib.auth.models import Group
from django.core.exceptions import ValidationError
from django.core.urlresolvers import reverse
from django.test.utils import override_settings
from django.utils import timezone
from django.utils.http import urlquote_plus
from mock import patch
from redis_cache import get_redis_connection
from rest_framework.authtoken.models import Token
from rest_framework.test import APIClient
from temba.campaigns.models import Campaign, CampaignEvent, MESSAGE_EVENT, FLOW_EVENT
from temba.contacts.models import Contact, ContactField, ContactGroup, ContactURN, TEL_SCHEME, TWITTER_SCHEME
from temba.orgs.models import Org, ACCOUNT_SID, ACCOUNT_TOKEN, APPLICATION_SID, NEXMO_KEY, NEXMO_SECRET
from temba.orgs.models import ALL_EVENTS, NEXMO_UUID
from temba.channels.models import Channel, ChannelLog, SyncEvent, SEND_URL, SEND_METHOD, VUMI, KANNEL, NEXMO, TWILIO, \
    SMART_ENCODING, UNICODE_ENCODING
from temba.channels.models import PLIVO, PLIVO_AUTH_ID, PLIVO_AUTH_TOKEN, PLIVO_APP_ID, TEMBA_HEADERS
from temba.channels.models import API_ID, USERNAME, PASSWORD, CLICKATELL, SHAQODOON, M3TECH, YO
from temba.flows.models import Flow, FlowLabel, FlowRun, RuleSet
from temba.msgs.models import Broadcast, Call, Msg, WIRED, FAILED, SENT, DELIVERED, ERRORED, INCOMING
from temba.msgs.models import MSG_SENT_KEY, Label, SystemLabel, VISIBLE, ARCHIVED, DELETED
from temba.orgs.models import Language
from temba.tests import MockResponse, TembaTest, AnonymousOrg
from temba.triggers.models import Trigger
from temba.utils import dict_to_struct, datetime_to_json_date
from temba.values.models import Value, DATETIME
from twilio.util import RequestValidator
from twython import TwythonError
from urllib import urlencode
from urlparse import parse_qs
from .models import WebHookEvent, WebHookResult, APIToken, SMS_RECEIVED
from .serializers import DictionaryField, IntegerArrayField, StringArrayField, PhoneArrayField, ChannelField, FlowField


class APITest(TembaTest):

    def setUp(self):
        super(APITest, self).setUp()

        self.joe = self.create_contact("Joe Blow", "0788123123")

        self.channel2 = Channel.objects.create(name="Unclaimed Channel", claim_code="123123123",
                                               created_by=self.admin, modified_by=self.admin, country='RW',
                                               secret="123456", gcm_id="1234")

        self.call1 = Call.objects.create(contact=self.joe,
                                         channel=self.channel,
                                         org=self.org,
                                         call_type='mt_miss',
                                         time=timezone.now(),
                                         created_by=self.admin,
                                         modified_by=self.admin)

        settings.SECURE_PROXY_SSL_HEADER = ('HTTP_X_FORWARDED_HTTPS', 'https')
        settings.SESSION_COOKIE_SECURE = True

    def tearDown(self):
        super(APITest, self).tearDown()
        settings.SESSION_COOKIE_SECURE = False

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
        url = reverse('api.explorer')
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
        url = reverse('api')

        # browse as HTML anonymously
        response = self.fetchHTML(url)
        self.assertContains(response, "RapidPro API", status_code=403)  # still shows docs

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
        dict_field = DictionaryField(source='test')

        self.assertEqual(dict_field.from_native({'a': '123'}), {'a': '123'})
        self.assertRaises(ValidationError, dict_field.from_native, [])  # must be a dict
        self.assertRaises(ValidationError, dict_field.from_native, {123: '456'})  # keys and values must be strings
        self.assertRaises(ValidationError, dict_field.to_native, {})  # not writable

        ints_field = IntegerArrayField(source='test')

        self.assertEqual(ints_field.from_native([1, 2, 3]), [1, 2, 3])
        self.assertEqual(ints_field.from_native(123), [123])  # convert single number to array
        self.assertRaises(ValidationError, ints_field.from_native, {})  # must be a list
        self.assertRaises(ValidationError, ints_field.from_native, ['x'])  # items must be ints or longs
        self.assertRaises(ValidationError, ints_field.to_native, [])  # not writable

        strings_field = StringArrayField(source='test')

        self.assertEqual(strings_field.from_native(['a', 'b', 'c']), ['a', 'b', 'c'])
        self.assertEqual(strings_field.from_native('abc'), ['abc'])  # convert single string to array
        self.assertRaises(ValidationError, strings_field.from_native, {})  # must be a list
        self.assertRaises(ValidationError, strings_field.from_native, [123])  # items must be strings
        self.assertRaises(ValidationError, strings_field.to_native, [])  # not writable

        phones_field = PhoneArrayField(source='test')

        self.assertEqual(phones_field.from_native(['123', '234']), [('tel', '123'), ('tel', '234')])
        self.assertEqual(phones_field.from_native('123'), [('tel', '123')])  # convert single string to array
        self.assertRaises(ValidationError, phones_field.from_native, {})  # must be a list
        self.assertRaises(ValidationError, phones_field.from_native, [123])  # items must be strings
        self.assertRaises(ValidationError, phones_field.from_native, ['123'] * 101)  # 100 items max
        self.assertRaises(ValidationError, phones_field.to_native, [])  # not writable

        flow_field = FlowField(source='test')

        flow = self.create_flow()
        self.assertEqual(flow_field.from_native(flow.pk), flow)
        flow.is_active = False
        flow.save()
        self.assertRaises(ValidationError, flow_field.from_native, flow.pk)

        channel_field = ChannelField(source='test')

        self.assertEqual(channel_field.from_native(self.channel.pk), self.channel)
        self.channel.is_active = False
        self.channel.save()
        self.assertRaises(ValidationError, channel_field.from_native, self.channel.pk)

    @override_settings(REST_HANDLE_EXCEPTIONS=True)
    @patch('temba.api.views.FieldEndpoint.get_queryset')
    def test_api_error_handling(self, mock_get_queryset):
        mock_get_queryset.side_effect = ValueError("DOH!")

        self.login(self.admin)

        response = self.client.get(reverse('api.contactfields') + '.json', content_type="application/json", HTTP_X_FORWARDED_HTTPS='https')
        self.assertEqual(response.status_code, 500)
        self.assertEqual(response.content, "Server Error. Site administrators have been notified.")

    def test_api_org(self):
        url = reverse('api.org')

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
        url = reverse('api.flows')

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

        url = reverse('api.flow_definition')
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

    def test_api_steps(self):
        url = reverse('api.steps')

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

        print json.dumps(definition, indent=5)
        print flow.version_number

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
        out_msgs = list(Msg.objects.filter(direction='O').order_by('pk'))
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
        out_msgs = list(Msg.objects.filter(direction='O').order_by('pk'))
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
            self.assertEquals("No such node with UUID 00000000-00000000-00000000-00000020 in flow 'Color Flow'", response.json['steps'][0])

            # this version doesn't exist
            data['revision'] = 12
            response = self.postJSON(url, data)
            self.assertEquals(400, response.status_code)
            self.assertEquals('Invalid revision: 12', response.json['steps'][0])

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
        url = reverse('api.results')

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
        url = reverse('api.runs')

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

        # now test fetching them instead.....

        # no filtering
        response = self.fetchJSON(url)
        self.assertEquals(200, response.status_code)
        self.assertResultCount(response, 9)  # all the runs

        flow.start([], [Contact.get_test_contact(self.user)])  # create a run for a test contact

        response = self.fetchJSON(url)
        self.assertResultCount(response, 9)  # test contact's run not included

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
        self.assertResultCount(response, 8)

        # filter by flow UUID
        response = self.fetchJSON(url, "flow_uuid=%s" % flow.uuid)

        self.assertResultCount(response, 8)
        self.assertNotContains(response, flow_copy.uuid)

        # filter by phone (deprecated)
        response = self.fetchJSON(url, "phone=%2B250788123123")  # joe
        self.assertResultCount(response, 2)
        self.assertContains(response, self.joe.uuid)
        self.assertNotContains(response, contact.uuid)

        # filter by contact UUID
        response = self.fetchJSON(url, "contact=" + contact.uuid)
        self.assertResultCount(response, 7)
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
        self.assertResultCount(response, 7)
        self.assertContains(response, contact.uuid)
        self.assertNotContains(response, self.joe.uuid)

        # filter by group UUID
        response = self.fetchJSON(url, "group_uuids=%s" % players.uuid)
        self.assertResultCount(response, 7)
        self.assertContains(response, contact.uuid)
        self.assertNotContains(response, self.joe.uuid)

        # invalid dates
        response = self.fetchJSON(url, "before=01-01T00:00:00.000&after=01-01T00:00:00.000&channel=1,2")
        self.assertEquals(200, response.status_code)
        self.assertResultCount(response, 0)

    def test_api_channels(self):
        url = reverse('api.channels')

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

        # test removing Twitter channel with Mage client disabled
        with patch('temba.utils.mage.MageClient._request') as mock:
            mock.return_value = ""

            # create a Twitter channel and delete it
            twitter = Channel.create(self.org, self.user, None, 'TT')
            response = self.deleteJSON(url, "id=%d" % twitter.pk)
            self.assertEquals(204, response.status_code)

    def test_api_calls(self):
        url = reverse('api.calls')

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
        url = reverse('api.contacts')

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
        self.assertResponseError(response, 'language', "Ensure this value has at most 3 characters (it has 7).")

        # try to update the language to something shorter than 3-letters
        response = self.postJSON(url, dict(name='Snoop Dog', urns=['tel:+250788123456'], language='X'))
        self.assertEquals(400, response.status_code)
        self.assertResponseError(response, 'language', "Ensure this value has at least 3 characters (it has 1).")

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
        self.assertResponseError(response, 'groups', "Invalid group name: '  '")

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
        jay_z = self.create_contact("Jay-Z", number="123555")
        ContactField.get_or_create(self.org, 'registration_date', "Registration Date", None, DATETIME)
        jay_z.set_field('registration_date', "2014-12-31 03:04:00")

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
        url = reverse('api.contacts')

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
        url = reverse('api.contactfields')

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
        self.assertResponseError(response, 'label',
                                 "Invalid Field label: Field labels can only contain letters, numbers and hypens")

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
        self.assertResponseError(response, 'key', "Field key is invalid or is a reserved name")

    def test_api_contact_actions(self):
        url = reverse('api.contact_actions')

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

        # try adding all contacts to a group
        response = self.postJSON(url, dict(contacts=[contact1.uuid, contact2.uuid, contact3.uuid, contact4.uuid,
                                                     contact5.uuid, test_contact.uuid],
                                           action='add', group="Testers"))

        # error reporting that the deleted and test contacts are invalid
        self.assertResponseError(response, 'contacts',
                                 "Some contacts are invalid: %s, %s" % (contact5.uuid, test_contact.uuid))

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
        response = self.postJSON(url, dict(contacts=[contact1.uuid], action='add', group=''))
        self.assertResponseError(response, 'non_field_errors', "For action add you should also specify group or group_uuid")

        # try to block all contacts
        response = self.postJSON(url, dict(contacts=[contact1.uuid, contact2.uuid, contact3.uuid, contact4.uuid,
                                                     contact5.uuid, test_contact.uuid],
                                           action='block'))
        self.assertResponseError(response, 'contacts',
                                 "Some contacts are invalid: %s, %s" % (contact5.uuid, test_contact.uuid))

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
        url = reverse('api.messages')

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
        self.assertEquals(1, Msg.objects.all().count())

        broadcast = Broadcast.objects.get()
        self.assertEquals("test1", broadcast.text)
        self.assertEquals(self.admin.get_org(), broadcast.org)

        sms = Msg.objects.get()
        self.assertEquals("test1", sms.text)
        self.assertEquals("+250788123123", sms.contact.get_urn(TEL_SCHEME).path)
        self.assertEquals(self.admin.get_org(), sms.org)
        self.assertEquals(self.channel, sms.channel)
        self.assertEquals(broadcast, sms.broadcast)

        Msg.objects.all().delete()
        Broadcast.objects.all().delete()

        # add a broadcast with urns field
        response = self.postJSON(url, dict(text='test1', urn=['tel:+250788123123']))
        self.assertEquals(201, response.status_code)

        # should be one broadcast and one SMS
        self.assertEquals(1, Broadcast.objects.all().count())
        self.assertEquals(1, Msg.objects.all().count())

        broadcast = Broadcast.objects.get()
        self.assertEquals("test1", broadcast.text)
        self.assertEquals(self.admin.get_org(), broadcast.org)

        Msg.objects.all().delete()
        Broadcast.objects.all().delete()

        # add a broadcast using a contact uuid
        contact = Contact.objects.get(urns__path='+250788123123')
        response = self.postJSON(url, dict(text='test1', contact=[contact.uuid]))
        self.assertEquals(201, response.status_code)

        # should be one broadcast and one SMS
        self.assertEquals(1, Broadcast.objects.all().count())
        self.assertEquals(1, Msg.objects.all().count())

        broadcast = Broadcast.objects.get()
        self.assertEquals("test1", broadcast.text)
        self.assertEquals(self.admin.get_org(), broadcast.org)

        msg1 = Msg.objects.get()
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

        url = reverse('api.sms')

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
        msg3 = Msg.create_incoming(self.channel, (TEL_SCHEME, '0788123123'), "test3")
        msg4 = Msg.create_incoming(self.channel, (TEL_SCHEME, '0788123123'), "test4")

        flow = self.create_flow()
        flow.start([], [contact])
        msg5 = Msg.objects.get(msg_type='F')

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
        url = reverse('api.messages')
        self.login(self.admin)

        # add a broadcast
        response = self.postJSON(url, dict(phone=['250788123123', '250788123124'], text='test1'))
        self.assertEquals(201, response.status_code)

        # should be one broadcast and one SMS
        self.assertEquals(1, Broadcast.objects.all().count())
        self.assertEquals(2, Msg.objects.all().count())

        broadcast = Broadcast.objects.get()
        self.assertEquals("test1", broadcast.text)
        self.assertEquals(self.admin.get_org(), broadcast.org)

        msgs = Msg.objects.all().order_by('contact__urns__path')
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
        url = reverse('api.messages')
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
        url = reverse('api.sms')
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

        sms = Msg.objects.get()
        self.assertEquals(self.channel.pk, sms.channel.pk)

        # remove our channel
        org2 = Org.objects.create(name="Another Org", timezone="Africa/Kigali", created_by=self.admin, modified_by=self.admin)
        self.channel.org = org2
        self.channel.save()

        # can't send
        response = self.postJSON(url, dict(phone=['250788123123'], text='test1'))
        self.assertEquals(400, response.status_code)

    def test_api_message_actions(self):
        url = reverse('api.message_actions')

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
        self.assertResponseError(response, 'non_field_errors', "For action label you should also specify label or label_uuid")

        # archive all messages
        response = self.postJSON(url, dict(messages=[msg1.pk, msg2.pk, msg3.pk, msg4.pk], action='archive'))
        self.assertEquals(204, response.status_code)
        self.assertEqual(set(Msg.objects.filter(visibility=VISIBLE)), {msg4})  # ignored as is outgoing
        self.assertEqual(set(Msg.objects.filter(visibility=ARCHIVED)), {msg1, msg2, msg3})

        # un-archive message 1
        response = self.postJSON(url, dict(messages=[msg1.pk], action='unarchive'))
        self.assertEquals(204, response.status_code)
        self.assertEqual(set(Msg.objects.filter(visibility=VISIBLE)), {msg1, msg4})
        self.assertEqual(set(Msg.objects.filter(visibility=ARCHIVED)), {msg2, msg3})

        # delete messages 2 and 4
        response = self.postJSON(url, dict(messages=[msg2.pk], action='delete'))
        self.assertEquals(204, response.status_code)
        self.assertEqual(set(Msg.objects.filter(visibility=VISIBLE)), {msg1, msg4})  # 4 ignored as is outgoing
        self.assertEqual(set(Msg.objects.filter(visibility=ARCHIVED)), {msg3})
        self.assertEqual(set(Msg.objects.filter(visibility=DELETED)), {msg2})

        # can't un-archive a deleted message
        response = self.postJSON(url, dict(messages=[msg2.pk], action='unarchive'))
        self.assertEquals(204, response.status_code)
        self.assertEqual(set(Msg.objects.filter(visibility=DELETED)), {msg2})

        # try to provide a label for a non-labelling action
        response = self.postJSON(url, dict(messages=[msg1.pk, msg2.pk], action='archive', label='Test2'))
        self.assertResponseError(response, 'non_field_errors', "For action archive you should not specify label or label_uuid")

        # try to invoke an invalid action
        response = self.postJSON(url, dict(messages=[msg1.pk], action='like'))
        self.assertResponseError(response, 'action', "Invalid action name: like")

    def test_api_labels(self):
        url = reverse('api.labels')

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

        url = reverse('api.authenticate')

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
        self.assertEqual(200, client.get(reverse('api.campaigns') + '.json').status_code)

        # but not by an admin's surveyor token
        client.credentials(**surveyor_token)
        self.assertEqual(403, client.get(reverse('api.campaigns') + '.json').status_code)

        # but their surveyor token can get flows or contacts
        self.assertEqual(200, client.get(reverse('api.flows') + '.json').status_code)
        self.assertEqual(200, client.get(reverse('api.contacts') + '.json').status_code)

        # our surveyor can't login with an admin role
        response = json.loads(self.client.post(url, dict(email='Surveyor', password='Surveyor', role='A')).content)
        self.assertEqual(0, len(response))

        # but they can with a surveyor role
        response = json.loads(self.client.post(url, dict(email='Surveyor', password='Surveyor', role='S')).content)
        self.assertEqual(1, len(response))

        # and can fetch flows, contacts, and fields, but not campaigns
        client.credentials(HTTP_AUTHORIZATION='Token ' + response[0]['token'])
        self.assertEqual(200, client.get(reverse('api.flows') + '.json').status_code)
        self.assertEqual(200, client.get(reverse('api.contacts') + '.json').status_code)
        self.assertEqual(200, client.get(reverse('api.contactfields') + '.json').status_code)
        self.assertEqual(403, client.get(reverse('api.campaigns') + '.json').status_code)


    def test_api_broadcasts(self):
        url = reverse('api.broadcasts')

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
        url = reverse('api.campaigns')

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
        url = reverse('api.campaignevents')

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

        # create by campaign id (deprecated)
        response = self.postJSON(url, dict(campaign=campaign.pk, unit='W', offset=5, relative_to="EDD",
                                           delivery_hour=-1, message="Time to go to the clinic"))
        self.assertEqual(response.status_code, 201)

        event1 = CampaignEvent.objects.get()
        message_flow = Flow.objects.get()
        self.assertEqual(event1.event_type, MESSAGE_EVENT)
        self.assertEqual(event1.campaign, campaign)
        self.assertEqual(event1.offset, 5)
        self.assertEqual(event1.unit, 'W')
        self.assertEqual(event1.relative_to.label, "EDD")
        self.assertEqual(event1.delivery_hour, -1)
        self.assertEqual(event1.message, "Time to go to the clinic")
        self.assertEqual(event1.flow, message_flow)

        # create by campaign UUID and flow UUID
        color_flow = self.create_flow()
        response = self.postJSON(url, dict(campaign_uuid=campaign.uuid, unit='D', offset=3, relative_to="EDD",
                                           delivery_hour=9, flow_uuid=color_flow.uuid))
        self.assertEqual(response.status_code, 201)

        event2 = CampaignEvent.objects.get(flow=color_flow)
        self.assertEqual(event2.event_type, FLOW_EVENT)
        self.assertEqual(event2.campaign, campaign)
        self.assertEqual(event2.offset, 3)
        self.assertEqual(event2.unit, 'D')
        self.assertEqual(event2.relative_to.label, "EDD")
        self.assertEqual(event2.delivery_hour, 9)
        self.assertEqual(event2.message, None)

        # update an event by id (deprecated)
        response = self.postJSON(url, dict(event=event1.pk, unit='D', offset=30, relative_to="EDD",
                                           delivery_hour=-1, message="Time to go to the clinic. Thanks"))
        self.assertEqual(response.status_code, 201)

        event1 = CampaignEvent.objects.get(pk=event1.pk)
        self.assertEqual(event1.event_type, MESSAGE_EVENT)
        self.assertEqual(event1.campaign, campaign)
        self.assertEqual(event1.offset, 30)
        self.assertEqual(event1.unit, 'D')
        self.assertEqual(event1.relative_to.label, "EDD")
        self.assertEqual(event1.delivery_hour, -1)
        self.assertEqual(event1.message, "Time to go to the clinic. Thanks")
        self.assertEqual(event1.flow, message_flow)

        # update an event by UUID
        other_flow = Flow.copy(color_flow, self.user)
        response = self.postJSON(url, dict(uuid=event2.uuid, unit='W', offset=3, relative_to="EDD",
                                           delivery_hour=5, flow_uuid=other_flow.uuid))
        self.assertEqual(response.status_code, 201)

        event2 = CampaignEvent.objects.get(pk=event2.pk)
        self.assertEqual(event2.event_type, FLOW_EVENT)
        self.assertEqual(event2.campaign, campaign)
        self.assertEqual(event2.offset, 3)
        self.assertEqual(event2.unit, 'W')
        self.assertEqual(event2.relative_to.label, "EDD")
        self.assertEqual(event2.delivery_hour, 5)
        self.assertEqual(event2.message, None)
        self.assertEqual(event2.flow, other_flow)

        # try to specify campaign when updating event (not allowed)
        response = self.postJSON(url, dict(uuid=event2.uuid, campaign_uuid=campaign.uuid, unit='D', offset=3,
                                           relative_to="EDD", delivery_hour=9, flow_uuid=color_flow.uuid))
        self.assertEqual(response.status_code, 400)

        # fetch all events
        response = self.fetchJSON(url)
        self.assertResultCount(response, 2)
        self.assertEqual(response.json['results'][0]['uuid'], event2.uuid)
        self.assertEqual(response.json['results'][0]['campaign_uuid'], campaign.uuid)
        self.assertEqual(response.json['results'][0]['campaign'], campaign.pk)
        self.assertEqual(response.json['results'][0]['relative_to'], "EDD")
        self.assertEqual(response.json['results'][0]['offset'], 3)
        self.assertEqual(response.json['results'][0]['unit'], 'W')
        self.assertEqual(response.json['results'][0]['delivery_hour'], 5)
        self.assertEqual(response.json['results'][0]['flow_uuid'], other_flow.uuid)
        self.assertEqual(response.json['results'][0]['flow'], other_flow.pk)
        self.assertEqual(response.json['results'][0]['message'], None)
        self.assertEqual(response.json['results'][1]['uuid'], event1.uuid)
        self.assertEqual(response.json['results'][1]['flow_uuid'], None)
        self.assertEqual(response.json['results'][1]['flow'], None)
        self.assertEqual(response.json['results'][1]['message'], "Time to go to the clinic. Thanks")

        # delete event by UUID
        response = self.deleteJSON(url, "uuid=%s" % event1.uuid)
        self.assertEqual(response.status_code, 204)

        # check that we've been deleted
        self.assertFalse(CampaignEvent.objects.get(pk=event1.pk).is_active)

        # deleting again is a 404
        response = self.deleteJSON(url, "uuid=%s" % event1.uuid)
        self.assertEqual(response.status_code, 404)

    def test_api_groups(self):
        url = reverse('api.contactgroups')

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


class AfricasTalkingTest(TembaTest):

    def test_delivery(self):
        # change our channel to an africas talking channel
        self.channel.channel_type = 'AT'
        self.channel.uuid = 'asdf-asdf-asdf-asdf'
        self.channel.save()

        # ok, what happens with an invalid uuid?
        post_data = dict(id="external1", status="Success")
        response = self.client.post(reverse('api.africas_talking_handler', args=['delivery', 'not-real-uuid']), post_data)

        self.assertEquals(404, response.status_code)

        # ok, try with a valid uuid, but invalid message id
        delivery_url = reverse('api.africas_talking_handler', args=['delivery', self.channel.uuid])
        response = self.client.post(delivery_url, post_data)

        self.assertEquals(404, response.status_code)

        # ok, lets create an outgoing message to update
        joe = self.create_contact("Joe Biden", "+254788383383")
        broadcast = joe.send("Hey Joe, it's Obama, pick up!", self.admin)
        sms = broadcast.get_messages()[0]

        sms.external_id = "external1"
        sms.save()

        def assertStatus(sms, post_status, assert_status):
            post_data['status'] = post_status
            response = self.client.post(delivery_url, post_data)
            self.assertEquals(200, response.status_code)
            sms = Msg.objects.get(pk=sms.id)
            self.assertEquals(assert_status, sms.status)

        assertStatus(sms, 'Success', DELIVERED)
        assertStatus(sms, 'Sent', SENT)
        assertStatus(sms, 'Buffered', SENT)
        assertStatus(sms, 'Failed', FAILED)
        assertStatus(sms, 'Rejected', FAILED)

    def test_callback(self):
        # change our channel to an africas talking channel
        self.channel.channel_type = 'AT'
        self.channel.uuid = 'asdf-asdf-asdf-asdf'
        self.channel.country = "KE"
        self.channel.save()

        post_data = {'from':"0788123123", 'text':"Hello World"}
        callback_url = reverse('api.africas_talking_handler', args=['callback', self.channel.uuid])
        response = self.client.post(callback_url, post_data)

        self.assertEquals(200, response.status_code)

        # load our message
        sms = Msg.objects.get()
        self.assertEquals("+254788123123", sms.contact.get_urn(TEL_SCHEME).path)
        self.assertEquals(INCOMING, sms.direction)
        self.assertEquals(self.org, sms.org)
        self.assertEquals(self.channel, sms.channel)
        self.assertEquals("Hello World", sms.text)

    def test_send(self):
        self.channel.channel_type = 'AT'
        self.channel.config = json.dumps(dict(username='at-user', api_key='africa-key'))
        self.channel.save()

        joe = self.create_contact("Joe", "+250788383383")
        bcast = joe.send("Test message", self.admin, trigger_send=False)

        # our outgoing sms
        sms = bcast.get_messages()[0]

        try:
            settings.SEND_MESSAGES = True

            with patch('requests.post') as mock:
                mock.return_value = MockResponse(200, json.dumps(dict(SMSMessageData=dict(Recipients=[dict(messageId='msg1')]))))

                # manually send it off
                Channel.send_message(dict_to_struct('MsgStruct', sms.as_task_json()))

                # check the status of the message is now sent
                msg = bcast.get_messages()[0]
                self.assertEquals(SENT, msg.status)
                self.assertTrue(msg.sent_on)
                self.assertEquals('msg1', msg.external_id)

                # check that our from was set
                self.assertEquals(self.channel.address, mock.call_args[1]['data']['from'])

                self.clear_cache()

            # test with a non-dedicated shortcode
            self.channel.config = json.dumps(dict(username='at-user', api_key='africa-key', is_shared=True))
            self.channel.save()

            with patch('requests.post') as mock:
                mock.return_value = MockResponse(200, json.dumps(dict(SMSMessageData=dict(Recipients=[dict(messageId='msg1')]))))

                # manually send it off
                Channel.send_message(dict_to_struct('MsgStruct', sms.as_task_json()))

                # assert we didn't send the short code in our data
                self.assertTrue('from' not in mock.call_args[1]['data'])
                self.clear_cache()

            with patch('requests.post') as mock:
                mock.return_value = MockResponse(400, "Error", method='POST')

                # manually send it off
                Channel.send_message(dict_to_struct('MsgStruct', sms.as_task_json()))

                # message should be marked as an error
                msg = bcast.get_messages()[0]
                self.assertEquals(ERRORED, msg.status)
                self.assertEquals(1, msg.error_count)
                self.assertTrue(msg.next_attempt)
        finally:
            settings.SEND_MESSAGES = False

class ExternalTest(TembaTest):

    def test_status(self):
        # change our channel to an aggregator channel
        self.channel.channel_type = 'EX'
        self.channel.uuid = 'asdf-asdf-asdf-asdf'
        self.channel.save()

        # ok, what happens with an invalid uuid?
        data = dict(id="-1")
        response = self.client.post(reverse('api.external_handler', args=['sent', 'not-real-uuid']), data)

        self.assertEquals(400, response.status_code)

        # ok, try with a valid uuid, but invalid message id -1
        delivery_url = reverse('api.external_handler', args=['sent', self.channel.uuid])
        response = self.client.post(delivery_url, data)

        self.assertEquals(400, response.status_code)

        # ok, lets create an outgoing message to update
        joe = self.create_contact("Joe Biden", "+254788383383")
        broadcast = joe.send("Hey Joe, it's Obama, pick up!", self.admin)
        sms = broadcast.get_messages()[0]
        sms.save()

        data['id'] = sms.pk

        def assertStatus(sms, status, assert_status):
            response = self.client.post(reverse('api.external_handler', args=[status, self.channel.uuid]), data)
            self.assertEquals(200, response.status_code)
            sms = Msg.objects.get(pk=sms.id)
            self.assertEquals(assert_status, sms.status)

        assertStatus(sms, 'delivered', DELIVERED)
        assertStatus(sms, 'sent', SENT)
        assertStatus(sms, 'failed', FAILED)

    def test_receive(self):
        # change our channel to an external channel
        self.channel.channel_type = 'EX'
        self.channel.uuid = 'asdf-asdf-asdf-asdf'
        self.channel.country = 'BR'
        self.channel.save()

        data = {'from': '5511996458779', 'text': 'Hello World!'}
        callback_url = reverse('api.external_handler', args=['received', self.channel.uuid])
        response = self.client.post(callback_url, data)

        self.assertEquals(200, response.status_code)

        # load our message
        sms = Msg.objects.get()
        self.assertEquals("+5511996458779", sms.contact.get_urn(TEL_SCHEME).path)
        self.assertEquals(INCOMING, sms.direction)
        self.assertEquals(self.org, sms.org)
        self.assertEquals(self.channel, sms.channel)
        self.assertEquals("Hello World!", sms.text)

        data = {'from': "", 'text': "Hi there"}
        response = self.client.post(callback_url, data)

        self.assertEquals(400, response.status_code)

        Msg.objects.all().delete()

        # receive with a date
        data = {'from': '5511996458779', 'text': 'Hello World!', 'date': '2012-04-23T18:25:43.511Z'}
        callback_url = reverse('api.external_handler', args=['received', self.channel.uuid])
        response = self.client.post(callback_url, data)

        self.assertEquals(200, response.status_code)

        # load our message, make sure the date was saved properly
        sms = Msg.objects.get()
        self.assertEquals(2012, sms.created_on.year)
        self.assertEquals(18, sms.created_on.hour)

    def test_send(self):
        from temba.channels.models import EXTERNAL
        self.channel.channel_type = EXTERNAL
        self.channel.config = json.dumps({SEND_URL: 'http://foo.com/send', SEND_METHOD: 'POST'})
        self.channel.save()

        joe = self.create_contact("Joe", "+250788383383")
        bcast = joe.send("Test message", self.admin, trigger_send=False)

        # our outgoing sms
        sms = bcast.get_messages()[0]

        try:
            settings.SEND_MESSAGES = True

            with patch('requests.post') as mock:
                mock.return_value = MockResponse(200, "Sent")

                # manually send it off
                Channel.send_message(dict_to_struct('MsgStruct', sms.as_task_json()))

                # check the status of the message is now sent
                msg = bcast.get_messages()[0]
                self.assertEquals(WIRED, msg.status)
                self.assertTrue(msg.sent_on)

                self.clear_cache()

            with patch('requests.post') as mock:
                mock.return_value = MockResponse(400, "Error")

                # manually send it off
                Channel.send_message(dict_to_struct('MsgStruct', sms.as_task_json()))

                # message should be marked as an error
                msg = bcast.get_messages()[0]
                self.assertEquals(ERRORED, msg.status)
                self.assertEquals(1, msg.error_count)
                self.assertTrue(msg.next_attempt)
        finally:
            settings.SEND_MESSAGES = False


class YoTest(TembaTest):
    def setUp(self):
        super(YoTest, self).setUp()
        self.channel.channel_type = YO
        self.channel.uuid = 'asdf-asdf-asdf-asdf'
        self.channel.config = json.dumps(dict(username='test', password='sesame'))
        self.channel.save()

    def test_receive(self):
        callback_url = reverse('api.yo_handler', args=['received', self.channel.uuid])
        response = self.client.get(callback_url + "?sender=252788123123&message=Hello+World")

        self.assertEquals(200, response.status_code)

        # load our message
        sms = Msg.objects.get()
        self.assertEquals("+252788123123", sms.contact.get_urn(TEL_SCHEME).path)
        self.assertEquals(INCOMING, sms.direction)
        self.assertEquals(self.org, sms.org)
        self.assertEquals(self.channel, sms.channel)
        self.assertEquals("Hello World", sms.text)

        # fails if missing sender
        response = self.client.get(callback_url + "?sender=252788123123")
        self.assertEquals(400, response.status_code)

        # fails if missing message
        response = self.client.get(callback_url + "?message=Hello+World")
        self.assertEquals(400, response.status_code)

    def test_send(self):
        joe = self.create_contact("Joe", "+252788383383")
        bcast = joe.send("Test message", self.admin, trigger_send=False)

        # our outgoing sms
        sms = bcast.get_messages()[0]

        try:
            settings.SEND_MESSAGES = True

            with patch('requests.get') as mock:
                mock.return_value = MockResponse(200, "ybs_autocreate_status=OK")

                # manually send it off
                Channel.send_message(dict_to_struct('MsgStruct', sms.as_task_json()))

                # check the status of the message is now sent
                msg = bcast.get_messages()[0]
                self.assertEquals(SENT, msg.status)
                self.assertTrue(msg.sent_on)

                self.clear_cache()

            with patch('requests.get') as mock:
                mock.return_value = MockResponse(400, "Kaboom")

                # manually send it off
                Channel.send_message(dict_to_struct('MsgStruct', sms.as_task_json()))

                # message should be marked as an error
                msg = bcast.get_messages()[0]
                self.assertEquals(ERRORED, msg.status)
                self.assertEquals(1, msg.error_count)
                self.assertTrue(msg.next_attempt)

                self.clear_cache()

            with patch('requests.get') as mock:
                mock.return_value = MockResponse(200, "ybs_autocreate_status=ERROR&ybs_autocreate_message=" +
                                                      "YBS+AutoCreate+Subsystem%3A+Access+denied" +
                                                      "+due+to+wrong+authorization+code")

                # manually send it off
                Channel.send_message(dict_to_struct('MsgStruct', sms.as_task_json()))

                # message should be marked as an error
                msg = bcast.get_messages()[0]
                self.assertEquals(ERRORED, msg.status)
                self.assertEquals(1, msg.error_count)
                self.assertTrue(msg.next_attempt)
        finally:
            settings.SEND_MESSAGES = False


class ShaqodoonTest(TembaTest):

    def setUp(self):
        from temba.channels.models import USERNAME, PASSWORD, KEY

        super(ShaqodoonTest, self).setUp()

        # change our channel to an external channel
        self.channel.channel_type = SHAQODOON
        self.channel.uuid = 'asdf-asdf-asdf-asdf'
        self.channel.country = 'SO'
        self.channel.config = json.dumps({SEND_URL: 'http://foo.com/send',
                                          USERNAME: 'username', PASSWORD: 'password', KEY: 'key'})
        self.channel.save()

    def test_receive(self):
        data = {'from': '252788123456', 'text': 'Hello World!'}
        callback_url = reverse('api.shaqodoon_handler', args=['received', self.channel.uuid])
        response = self.client.post(callback_url, data)

        self.assertEquals(200, response.status_code)

        # load our message
        sms = Msg.objects.get()
        self.assertEquals("+252788123456", sms.contact.get_urn(TEL_SCHEME).path)
        self.assertEquals(INCOMING, sms.direction)
        self.assertEquals(self.org, sms.org)
        self.assertEquals(self.channel, sms.channel)
        self.assertEquals("Hello World!", sms.text)

    def test_send(self):
        joe = self.create_contact("Joe", "+250788383383")
        bcast = joe.send("Test message ☺", self.admin, trigger_send=False)

        # our outgoing sms
        sms = bcast.get_messages()[0]

        try:
            settings.SEND_MESSAGES = True

            with patch('requests.get') as mock:
                mock.return_value = MockResponse(200, "Sent")

                # manually send it off
                Channel.send_message(dict_to_struct('MsgStruct', sms.as_task_json()))

                # check the status of the message is now sent
                msg = bcast.get_messages()[0]
                self.assertEquals(WIRED, msg.status)
                self.assertTrue(msg.sent_on)

                self.clear_cache()

            with patch('requests.get') as mock:
                mock.return_value = MockResponse(400, "Error")

                # manually send it off
                Channel.send_message(dict_to_struct('MsgStruct', sms.as_task_json()))

                # message should be marked as an error
                msg = bcast.get_messages()[0]
                self.assertEquals(ERRORED, msg.status)
                self.assertEquals(1, msg.error_count)
                self.assertTrue(msg.next_attempt)
        finally:
            settings.SEND_MESSAGES = False


class M3TechTest(TembaTest):

    def setUp(self):
        from temba.channels.models import USERNAME, PASSWORD, KEY

        super(M3TechTest, self).setUp()

        # change our channel to an external channel
        self.channel.channel_type = M3TECH
        self.channel.uuid = 'asdf-asdf-asdf-asdf'
        self.channel.country = 'PK'
        self.channel.config = json.dumps({USERNAME: 'username', PASSWORD: 'password'})
        self.channel.save()

    def test_receive(self):
        data = {'from': '252788123456', 'text': 'Hello World!'}
        callback_url = reverse('api.m3tech_handler', args=['received', self.channel.uuid])
        response = self.client.post(callback_url, data)

        self.assertEquals(200, response.status_code)

        # load our message
        sms = Msg.objects.get()
        self.assertEquals("+252788123456", sms.contact.get_urn(TEL_SCHEME).path)
        self.assertEquals(INCOMING, sms.direction)
        self.assertEquals(self.org, sms.org)
        self.assertEquals(self.channel, sms.channel)
        self.assertEquals("Hello World!", sms.text)

    def test_send(self):
        joe = self.create_contact("Joe", "+250788383383")
        bcast = joe.send("Test message ☺", self.admin, trigger_send=False)

        # our outgoing sms
        sms = bcast.get_messages()[0]

        try:
            settings.SEND_MESSAGES = True

            with patch('requests.get') as mock:
                mock.return_value = MockResponse(200,
                                                 """[{"Response":"0"}]""")

                # manually send it off
                Channel.send_message(dict_to_struct('MsgStruct', sms.as_task_json()))

                # check the status of the message is now sent
                msg = bcast.get_messages()[0]
                self.assertEquals(WIRED, msg.status)
                self.assertTrue(msg.sent_on)

                self.clear_cache()

            with patch('requests.get') as mock:
                mock.return_value = MockResponse(400, "Error")

                # manually send it off
                Channel.send_message(dict_to_struct('MsgStruct', sms.as_task_json()))

                # message should be marked as an error
                msg = bcast.get_messages()[0]
                self.assertEquals(ERRORED, msg.status)
                self.assertEquals(1, msg.error_count)
                self.assertTrue(msg.next_attempt)

                self.clear_cache()

            with patch('requests.get') as mock:
                mock.return_value = MockResponse(200,
                                                 """[{"Response":"1"}]""")

                # manually send it off
                Channel.send_message(dict_to_struct('MsgStruct', sms.as_task_json()))

                # message should be marked as an error
                msg = bcast.get_messages()[0]
                self.assertEquals(ERRORED, msg.status)
                self.assertEquals(1, msg.error_count)
                self.assertTrue(msg.next_attempt)

                self.clear_cache()
        finally:
            settings.SEND_MESSAGES = False


class KannelTest(TembaTest):

    def test_status(self):
        from temba.channels.models import KANNEL

        # change our channel to a kannel aggregator and populate needed fields
        self.channel.channel_type = KANNEL
        self.channel.uuid = 'asdf-asdf-asdf-asdf'
        self.channel.save()

        # ok, what happens with an invalid uuid?
        data = dict(id="-1", status="4")
        response = self.client.post(reverse('api.kannel_handler', args=['status', 'not-real-uuid']), data)
        self.assertEquals(400, response.status_code)

        # ok, try with a valid uuid, but invalid message id -1
        delivery_url = reverse('api.kannel_handler', args=['status', self.channel.uuid])
        response = self.client.post(delivery_url, data)
        self.assertEquals(400, response.status_code)

        # ok, lets create an outgoing message to update
        joe = self.create_contact("Joe Biden", "+254788383383")
        broadcast = joe.send("Hey Joe, it's Obama, pick up!", self.admin)
        sms = broadcast.get_messages()[0]
        sms.save()

        data['id'] = sms.pk
        def assertStatus(sms, status, assert_status):
            data['status'] = status
            response = self.client.post(reverse('api.kannel_handler', args=['status', self.channel.uuid]), data)
            self.assertEquals(200, response.status_code)
            sms = Msg.objects.get(pk=sms.id)
            self.assertEquals(assert_status, sms.status)

        assertStatus(sms, '4', SENT)
        assertStatus(sms, '1', DELIVERED)
        assertStatus(sms, '16', FAILED)

    def test_receive(self):
        self.channel.channel_type = 'KN'
        self.channel.uuid = 'asdf-asdf-asdf-asdf'
        self.channel.country = 'RW'
        self.channel.save()

        data = {'sender': '0788383383', 'message': 'Hello World!', 'id':'external1', 'ts':int(calendar.timegm(time.gmtime()))}
        callback_url = reverse('api.kannel_handler', args=['receive', self.channel.uuid])
        response = self.client.post(callback_url, data)

        self.assertEquals(200, response.status_code)

        # load our message
        sms = Msg.objects.get()
        self.assertEquals("+250788383383", sms.contact.get_urn(TEL_SCHEME).path)
        self.assertEquals(INCOMING, sms.direction)
        self.assertEquals(self.org, sms.org)
        self.assertEquals(self.channel, sms.channel)
        self.assertEquals("Hello World!", sms.text)

    def test_send(self):
        self.channel.channel_type = KANNEL
        self.channel.config = json.dumps(dict(username='kannel-user', password='kannel-pass', send_url='http://foo/'))
        self.channel.uuid = uuid.uuid4()
        self.channel.save()

        joe = self.create_contact("Joe", "+250788383383")
        bcast = joe.send("Test message", self.admin, trigger_send=False)

        # our outgoing sms
        sms = bcast.get_messages()[0]

        try:
            settings.SEND_MESSAGES = True

            with patch('requests.get') as mock:
                mock.return_value = MockResponse(200, 'Accepted 201')

                # manually send it off
                Channel.send_message(dict_to_struct('MsgStruct', sms.as_task_json()))

                # check the status of the message is now sent
                msg = bcast.get_messages()[0]
                self.assertEquals(WIRED, msg.status)
                self.assertTrue(msg.sent_on)

                # assert verify was set to true
                self.assertTrue(mock.call_args[1]['verify'])

                self.clear_cache()

            self.channel.config = json.dumps(dict(username='kannel-user', password='kannel-pass',
                                                  encoding=SMART_ENCODING,
                                                  send_url='http://foo/', verify_ssl=False))
            self.channel.save()

            sms.text = "No capital accented È!"
            sms.save()

            with patch('requests.get') as mock:
                mock.return_value = MockResponse(200, 'Accepted 201')

                # manually send it off
                Channel.send_message(dict_to_struct('MsgStruct', sms.as_task_json()))

                # check the status of the message is now sent
                msg = bcast.get_messages()[0]
                self.assertEquals(WIRED, msg.status)
                self.assertTrue(msg.sent_on)

                # assert verify was set to true
                self.assertEquals('No capital accented E!', mock.call_args[1]['params']['text'])
                self.assertFalse('coding' in mock.call_args[1]['params'])
                self.clear_cache()

            sms.text = "Unicode. ☺"
            sms.save()

            with patch('requests.get') as mock:
                mock.return_value = MockResponse(200, 'Accepted 201')

                # manually send it off
                Channel.send_message(dict_to_struct('MsgStruct', sms.as_task_json()))

                # check the status of the message is now sent
                msg = bcast.get_messages()[0]
                self.assertEquals(WIRED, msg.status)
                self.assertTrue(msg.sent_on)

                # assert verify was set to true
                self.assertEquals("Unicode. ☺", mock.call_args[1]['params']['text'])
                self.assertEquals('2', mock.call_args[1]['params']['coding'])

                self.clear_cache()

            sms.text = "Normal"
            sms.save()

            with patch('requests.get') as mock:
                mock.return_value = MockResponse(200, 'Accepted 201')

                # manually send it off
                Channel.send_message(dict_to_struct('MsgStruct', sms.as_task_json()))

                # check the status of the message is now sent
                msg = bcast.get_messages()[0]
                self.assertEquals(WIRED, msg.status)
                self.assertTrue(msg.sent_on)

                # assert verify was set to true
                self.assertEquals("Normal", mock.call_args[1]['params']['text'])
                self.assertFalse('coding' in mock.call_args[1]['params'])

                self.clear_cache()

            self.channel.config = json.dumps(dict(username='kannel-user', password='kannel-pass',
                                                  encoding=UNICODE_ENCODING,
                                                  send_url='http://foo/', verify_ssl=False))
            self.channel.save()

            with patch('requests.get') as mock:
                mock.return_value = MockResponse(200, 'Accepted 201')

                # manually send it off
                Channel.send_message(dict_to_struct('MsgStruct', sms.as_task_json()))

                # check the status of the message is now sent
                msg = bcast.get_messages()[0]
                self.assertEquals(WIRED, msg.status)
                self.assertTrue(msg.sent_on)

                # assert verify was set to true
                self.assertEquals("Normal", mock.call_args[1]['params']['text'])
                self.assertEquals('2', mock.call_args[1]['params']['coding'])

                self.clear_cache()

            self.channel.config = json.dumps(dict(username='kannel-user', password='kannel-pass',
                                                  send_url='http://foo/', verify_ssl=False))
            self.channel.save()

            with patch('requests.get') as mock:
                mock.return_value = MockResponse(400, "Error")

                # manually send it off
                Channel.send_message(dict_to_struct('MsgStruct', sms.as_task_json()))

                # assert verify was set to False
                self.assertFalse(mock.call_args[1]['verify'])

                # message should be marked as an error
                msg = bcast.get_messages()[0]
                self.assertEquals(ERRORED, msg.status)
                self.assertEquals(1, msg.error_count)
                self.assertTrue(msg.next_attempt)
        finally:
            settings.SEND_MESSAGES = False


class NexmoTest(TembaTest):

    def setUp(self):
        super(NexmoTest, self).setUp()

        # change our channel to an aggregator channel
        self.channel.channel_type = 'NX'

        # on nexmo, the channel uuid is actually the nexmo number
        self.channel.uuid = '250788123123'
        self.channel.save()

        self.nexmo_uuid = str(uuid.uuid4())
        nexmo_config = {NEXMO_KEY: '1234', NEXMO_SECRET: '1234', NEXMO_UUID: self.nexmo_uuid}

        org = self.channel.org

        config = org.config_json()
        config.update(nexmo_config)
        org.config = json.dumps(config)
        org.save()

    def test_status(self):
        # ok, what happens with an invalid uuid and number
        data = dict(to='250788123111', messageId='external1')
        response = self.client.get(reverse('api.nexmo_handler', args=['status', 'not-real-uuid']), data)
        self.assertEquals(404, response.status_code)

        # ok, try with a valid uuid, but invalid message id -1, should return 200
        # these are probably multipart message callbacks, which we don't track
        data = dict(to='250788123123', messageId='-1')
        delivery_url = reverse('api.nexmo_handler', args=['status', self.nexmo_uuid])
        response = self.client.get(delivery_url, data)
        self.assertEquals(200, response.status_code)

        # ok, lets create an outgoing message to update
        joe = self.create_contact("Joe Biden", "+254788383383")
        broadcast = joe.send("Hey Joe, it's Obama, pick up!", self.admin)
        sms = broadcast.get_messages()[0]
        sms.external_id = 'external1'
        sms.save()

        data['messageId'] = 'external1'

        def assertStatus(sms, status, assert_status):
            data['status'] = status
            response = self.client.get(reverse('api.nexmo_handler', args=['status', self.nexmo_uuid]), data)
            self.assertEquals(200, response.status_code)
            sms = Msg.objects.get(pk=sms.id)
            self.assertEquals(assert_status, sms.status)

        assertStatus(sms, 'delivered', DELIVERED)
        assertStatus(sms, 'expired', FAILED)
        assertStatus(sms, 'failed', FAILED)
        assertStatus(sms, 'accepted', SENT)
        assertStatus(sms, 'buffered', SENT)

    def test_receive(self):
        data = dict(to='250788123123', msisdn='250788111222', text='Hello World!', messageId='external1')
        callback_url = reverse('api.nexmo_handler', args=['receive', self.nexmo_uuid])
        response = self.client.get(callback_url, data)

        self.assertEquals(200, response.status_code)

        # load our message
        sms = Msg.objects.get()
        self.assertEquals("+250788111222", sms.contact.get_urn(TEL_SCHEME).path)
        self.assertEquals(INCOMING, sms.direction)
        self.assertEquals(self.org, sms.org)
        self.assertEquals(self.channel, sms.channel)
        self.assertEquals("Hello World!", sms.text)
        self.assertEquals('external1', sms.external_id)

    def test_send(self):
        from temba.orgs.models import NEXMO_KEY, NEXMO_SECRET
        org_config = self.org.config_json()
        org_config[NEXMO_KEY] = 'nexmo_key'
        org_config[NEXMO_SECRET] = 'nexmo_secret'
        self.org.config = json.dumps(org_config)

        self.channel.channel_type = NEXMO
        self.channel.save()

        joe = self.create_contact("Joe", "+250788383383")
        bcast = joe.send("Test message", self.admin, trigger_send=False)

        # our outgoing sms
        sms = bcast.get_messages()[0]

        try:
            settings.SEND_MESSAGES = True
            r = get_redis_connection()

            with patch('requests.get') as mock:
                mock.return_value = MockResponse(200, json.dumps(dict(messages=[{'status':0, 'message-id':12}])), method='POST')

                # manually send it off
                Channel.send_message(dict_to_struct('MsgStruct', sms.as_task_json()))

                # check the status of the message is now sent
                msg = bcast.get_messages()[0]
                self.assertEquals(SENT, msg.status)
                self.assertTrue(msg.sent_on)
                self.assertEquals('12', msg.external_id)

                self.clear_cache()

                # test some throttling by sending six messages right after another
                start = time.time()
                for i in range(6):
                    Channel.send_message(dict_to_struct('MsgStruct', sms.as_task_json()))
                    r.delete(timezone.now().strftime(MSG_SENT_KEY))

                    msg = bcast.get_messages()[0]
                    self.assertEquals(SENT, msg.status)

                # assert we sent the messages out in a reasonable amount of time
                end = time.time()
                self.assertTrue(1.5 > end - start > 1, "Sending of six messages took: %f" % (end - start))

                self.clear_cache()

            with patch('requests.get') as mock:
                mock.return_value = MockResponse(200, json.dumps(dict(messages=[{'status':0, 'message-id':12}])), method='POST')

                sms.text = u"Unicode ☺"
                sms.save()

                # manually send it off
                Channel.send_message(dict_to_struct('MsgStruct', sms.as_task_json()))

                # check the status of the message is now sent
                msg = bcast.get_messages()[0]
                self.assertEquals(SENT, msg.status)
                self.assertTrue(msg.sent_on)
                self.assertEquals('12', msg.external_id)

                # assert that we were called with unicode
                mock.assert_called_once_with('https://rest.nexmo.com/sms/json',
                                             params={'from': u'250785551212',
                                                     'api_secret': u'1234',
                                                     'status-report-req': 1,
                                                     'to': u'250788383383',
                                                     'text': u'Unicode \u263a',
                                                     'api_key': u'1234',
                                                     'type': 'unicode'})

                self.clear_cache()

            with patch('requests.get') as mock:
                mock.return_value = MockResponse(400, "Error", method='POST')

                # manually send it off
                Channel.send_message(dict_to_struct('MsgStruct', sms.as_task_json()))

                # message should be marked as an error
                msg = bcast.get_messages()[0]
                self.assertEquals(ERRORED, msg.status)
                self.assertEquals(1, msg.error_count)
                self.assertTrue(msg.next_attempt)
        finally:
            settings.SEND_MESSAGES = False


class VumiTest(TembaTest):

    def setUp(self):
        super(VumiTest, self).setUp()

        self.channel.channel_type = 'VM'
        self.channel.uuid = unicode(uuid.uuid4())
        self.channel.save()

        self.trey = self.create_contact("Trey Anastasio", "250788382382")

    def test_delivery_reports(self):

        sms = self.create_msg(direction='O', text='Outgoing message', contact=self.trey, status=WIRED,
                              external_id=unicode(uuid.uuid4()),)

        data = dict(event_type='delivery_report',
                    event_id=unicode(uuid.uuid4()),
                    message_type='event',
                    delivery_status='failed',
                    user_message_id=sms.external_id)

        callback_url = reverse('api.vumi_handler', args=['event', self.channel.uuid])

        response = self.client.post(callback_url, json.dumps(data), content_type="application/json")
        self.assertEquals(200, response.status_code)

        # check that we've become errored
        sms = Msg.objects.get(pk=sms.pk)
        self.assertEquals(ERRORED, sms.status)

        # couple more failures should move to failure
        Msg.objects.filter(pk=sms.pk).update(status=WIRED)
        self.client.post(callback_url, json.dumps(data), content_type="application/json")

        Msg.objects.filter(pk=sms.pk).update(status=WIRED)
        self.client.post(callback_url, json.dumps(data), content_type="application/json")

        sms = Msg.objects.get(pk=sms.pk)
        self.assertEquals(FAILED, sms.status)

        # successful deliveries shouldn't stomp on failures
        del data['delivery_status']
        self.client.post(callback_url, json.dumps(data), content_type="application/json")
        sms = Msg.objects.get(pk=sms.pk)
        self.assertEquals(FAILED, sms.status)

        # if we are wired we can now be successful again
        Msg.objects.filter(pk=sms.pk).update(status=WIRED)
        self.client.post(callback_url, json.dumps(data), content_type="application/json")
        sms = Msg.objects.get(pk=sms.pk)
        self.assertEquals(DELIVERED, sms.status)

    def test_send(self):
        self.channel.channel_type = VUMI
        self.channel.config = json.dumps(dict(account_key='vumi-key', access_token='vumi-token', conversation_key='key'))
        self.channel.save()

        joe = self.create_contact("Joe", "+250788383383")
        reporters = self.create_group("Reporters", [joe])
        bcast = joe.send("Test message", self.admin, trigger_send=False)

        # our outgoing sms
        sms = bcast.get_messages()[0]
        r = get_redis_connection()

        try:
            settings.SEND_MESSAGES = True

            with patch('requests.put') as mock:
                mock.return_value = MockResponse(200, '{ "message_id": "1515" }')

                # manually send it off
                Channel.send_message(dict_to_struct('MsgStruct', sms.as_task_json()))

                # check the status of the message is now sent
                msg = bcast.get_messages()[0]
                self.assertEquals(WIRED, msg.status)
                self.assertTrue(msg.sent_on)
                self.assertEquals("1515", msg.external_id)
                self.assertEquals(1, mock.call_count)

                # should have a failsafe that it was sent
                self.assertTrue(r.sismember(timezone.now().strftime(MSG_SENT_KEY), str(msg.id)))

                # try sending again, our failsafe should kick in
                Channel.send_message(dict_to_struct('MsgStruct', sms.as_task_json()))

                # we shouldn't have been called again
                self.assertEquals(1, mock.call_count)

                # simulate Vumi calling back to us telling us it failed
                data = dict(event_type='delivery_report',
                            event_id=unicode(uuid.uuid4()),
                            message_type='event',
                            delivery_status='failed',
                            user_message_id=msg.external_id)
                callback_url = reverse('api.vumi_handler', args=['event', self.channel.uuid])
                self.client.post(callback_url, json.dumps(data), content_type="application/json")

                # get the message again
                msg = bcast.get_messages()[0]
                self.assertEquals(ERRORED, msg.status)
                self.assertTrue(msg.next_attempt)
                self.assertFalse(r.sismember(timezone.now().strftime(MSG_SENT_KEY), str(msg.id)))

            with patch('requests.put') as mock:
                mock.return_value = MockResponse(500, "Error")

                # manually send it off
                Channel.send_message(dict_to_struct('MsgStruct', sms.as_task_json()))

                # message should be marked as errored, we'll retry in a bit
                msg = bcast.get_messages()[0]
                self.assertEquals(ERRORED, msg.status)
                self.assertEquals(1, msg.error_count)
                self.assertTrue(msg.next_attempt > timezone.now())
                self.assertEquals(1, mock.call_count)

                self.clear_cache()

            with patch('requests.put') as mock:
                mock.return_value = MockResponse(503, "<html><body><h1>503 Service Unavailable</h1>")

                # manually send it off
                Channel.send_message(dict_to_struct('MsgStruct', sms.as_task_json()))

                # message should be marked as errored, we'll retry in a bit
                msg = bcast.get_messages()[0]
                self.assertEquals(ERRORED, msg.status)
                self.assertEquals(1, msg.error_count)
                self.assertTrue(msg.next_attempt > timezone.now())
                self.assertEquals(1, mock.call_count)

                # Joe shouldn't be failed and should still be in a group
                joe = Contact.objects.get(id=joe.id)
                self.assertFalse(joe.is_failed)
                self.assertTrue(ContactGroup.user_groups.filter(contacts=joe))

                self.clear_cache()

            with patch('requests.put') as mock:
                # set our next attempt as if we are trying anew
                msg.next_attempt = timezone.now()
                msg.save()

                mock.return_value = MockResponse(400, "User has opted out")

                # manually send it off
                Channel.send_message(dict_to_struct('MsgStruct', sms.as_task_json()))

                # message should be marked as failed
                msg = bcast.get_messages()[0]
                self.assertEquals(FAILED, msg.status)
                self.assertEquals(1, msg.error_count)
                self.assertTrue(msg.next_attempt < timezone.now())
                self.assertEquals(1, mock.call_count)

                # could should now be failed as well and in no groups
                joe = Contact.objects.get(id=joe.id)
                self.assertTrue(joe.is_failed)
                self.assertFalse(ContactGroup.user_groups.filter(contacts=joe))

        finally:
            settings.SEND_MESSAGES = False


class ZenviaTest(TembaTest):

    def test_status(self):
        # change our channel to a zenvia channel
        self.channel.channel_type = 'ZV'
        self.channel.uuid = 'asdf-asdf-asdf-asdf'
        self.channel.save()

        # ok, what happens with an invalid uuid?
        data = dict(id="-1", status="500")
        response = self.client.get(reverse('api.zenvia_handler', args=['status', 'not-real-uuid']), data)

        self.assertEquals(404, response.status_code)

        # ok, try with a valid uuid, but invalid message id -1
        delivery_url = reverse('api.zenvia_handler', args=['status', self.channel.uuid])
        response = self.client.get(delivery_url, data)

        self.assertEquals(404, response.status_code)

        # ok, lets create an outgoing message to update
        joe = self.create_contact("Joe Biden", "+254788383383")
        broadcast = joe.send("Hey Joe, it's Obama, pick up!", self.admin)
        sms = broadcast.get_messages()[0]
        sms.save()

        data['id'] = sms.pk

        def assertStatus(sms, status, assert_status):
            data['status'] = status
            response = self.client.get(delivery_url, data)
            self.assertEquals(200, response.status_code)
            sms = Msg.objects.get(pk=sms.id)
            self.assertEquals(assert_status, sms.status)

        assertStatus(sms, '120', DELIVERED)
        assertStatus(sms, '111', SENT)
        assertStatus(sms, '140', FAILED)
        assertStatus(sms, '999', FAILED)
        assertStatus(sms, '131', FAILED)

    def test_receive(self):
        # change our channel to zenvia channel
        self.channel.channel_type = 'ZV'
        self.channel.uuid = 'asdf-asdf-asdf-asdf'
        self.channel.country = 'BR'
        self.channel.save()

        data = { 'from':'5511996458779', 'date':'31/07/2013 14:45:00' }
        encoded_message = "?msg=H%E9llo World%21"

        callback_url = reverse('api.zenvia_handler', args=['receive', self.channel.uuid]) + encoded_message
        response = self.client.post(callback_url, data)

        self.assertEquals(200, response.status_code)

        # load our message
        sms = Msg.objects.get()
        self.assertEquals("+5511996458779", sms.contact.get_urn(TEL_SCHEME).path)
        self.assertEquals(INCOMING, sms.direction)
        self.assertEquals(self.org, sms.org)
        self.assertEquals(self.channel, sms.channel)
        self.assertEquals("Héllo World!", sms.text)

    def test_send(self):
        from temba.orgs.models import NEXMO_KEY, NEXMO_SECRET
        self.channel.config = json.dumps(dict(account='zv-account', code='zv-code'))
        self.channel.channel_type = 'ZV'
        self.channel.save()

        joe = self.create_contact("Joe", "+250788383383")
        bcast = joe.send("Test message", self.admin, trigger_send=False)

        # our outgoing sms
        sms = bcast.get_messages()[0]

        try:
            settings.SEND_MESSAGES = True

            with patch('requests.get') as mock:
                mock.return_value = MockResponse(200, '000-ok', method='GET')

                # manually send it off
                Channel.send_message(dict_to_struct('MsgStruct', sms.as_task_json()))

                # check the status of the message is now sent
                msg = bcast.get_messages()[0]
                self.assertEquals(WIRED, msg.status)
                self.assertTrue(msg.sent_on)

                self.clear_cache()

            with patch('requests.get') as mock:
                mock.return_value = MockResponse(400, "Error", method='POST')

                # manually send it off
                Channel.send_message(dict_to_struct('MsgStruct', sms.as_task_json()))

                # message should be marked as an error
                msg = bcast.get_messages()[0]
                self.assertEquals(ERRORED, msg.status)
                self.assertEquals(1, msg.error_count)
                self.assertTrue(msg.next_attempt)
        finally:
            settings.SEND_MESSAGES = False


class InfobipTest(TembaTest):

    def test_received(self):
        # change our channel to zenvia channel
        self.channel.channel_type = 'IB'
        self.channel.uuid = 'asdf-asdf-asdf-asdf'
        self.channel.address = '+2347030767144'
        self.channel.country = 'NG'
        self.channel.save()

        data = {'receiver': '2347030767144', 'sender': '2347030767143', 'text': 'Hello World' }
        encoded_message = urlencode(data)

        callback_url = reverse('api.infobip_handler', args=['received', self.channel.uuid]) + "?" + encoded_message
        response = self.client.get(callback_url)

        self.assertEquals(200, response.status_code)

        # load our message
        sms = Msg.objects.get()
        self.assertEquals('+2347030767143', sms.contact.get_urn(TEL_SCHEME).path)
        self.assertEquals(INCOMING, sms.direction)
        self.assertEquals(self.org, sms.org)
        self.assertEquals(self.channel, sms.channel)
        self.assertEquals("Hello World", sms.text)

        # try it with an invalid receiver, should fail as UUID and receiver id are mismatched
        data['receiver'] = '2347030767145'
        encoded_message = urlencode(data)

        callback_url = reverse('api.infobip_handler', args=['received', self.channel.uuid]) + "?" + encoded_message
        response = self.client.get(callback_url)

        # should get 404 as the channel wasn't found
        self.assertEquals(404, response.status_code)

    def test_delivered(self):
        # change our channel to zenvia channel
        self.channel.channel_type = 'IB'
        self.channel.uuid = 'asdf-asdf-asdf-asdf'
        self.channel.address = '+2347030767144'
        self.channel.country = 'NG'
        self.channel.save()

        contact = self.create_contact("Joe", '+2347030767143')
        sms = Msg.create_outgoing(self.org, self.user, contact, "Hi Joe")
        sms.external_id = '254021015120766124'
        sms.save()

        # mark it as delivered
        base_body = '<DeliveryReport><message id="254021015120766124" sentdate="2014/02/10 16:12:07" ' \
                    ' donedate="2014/02/10 16:13:00" status="STATUS" gsmerror="0" price="0.65" /></DeliveryReport>'
        delivery_url = reverse('api.infobip_handler', args=['delivered', self.channel.uuid])

        # assert our SENT status
        response = self.client.post(delivery_url, data=base_body.replace('STATUS', 'SENT'), content_type='application/xml')
        self.assertEquals(200, response.status_code)
        sms = Msg.objects.get()
        self.assertEquals(SENT, sms.status)

        # assert our DELIVERED status
        response = self.client.post(delivery_url, data=base_body.replace('STATUS', 'DELIVERED'), content_type='application/xml')
        self.assertEquals(200, response.status_code)
        sms = Msg.objects.get()
        self.assertEquals(DELIVERED, sms.status)

        # assert our FAILED status
        response = self.client.post(delivery_url, data=base_body.replace('STATUS', 'NOT_SENT'), content_type='application/xml')
        self.assertEquals(200, response.status_code)
        sms = Msg.objects.get()
        self.assertEquals(FAILED, sms.status)

    def test_send(self):
        self.channel.config = json.dumps(dict(username='ib-user', password='ib-password'))
        self.channel.channel_type = 'IB'
        self.channel.save()

        joe = self.create_contact("Joe", "+250788383383")
        bcast = joe.send("Test message", self.admin, trigger_send=False)

        # our outgoing sms
        sms = bcast.get_messages()[0]

        try:
            settings.SEND_MESSAGES = True

            with patch('requests.post') as mock:
                mock.return_value = MockResponse(200, json.dumps(dict(results=[{'status':0, 'messageid':12}])))

                # manually send it off
                Channel.send_message(dict_to_struct('MsgStruct', sms.as_task_json()))

                # check the status of the message is now sent
                msg = bcast.get_messages()[0]
                self.assertEquals(SENT, msg.status)
                self.assertTrue(msg.sent_on)
                self.assertEquals('12', msg.external_id)

                self.clear_cache()

            with patch('requests.post') as mock:
                mock.return_value = MockResponse(400, "Error", method='POST')

                # manually send it off
                Channel.send_message(dict_to_struct('MsgStruct', sms.as_task_json()))

                # message should be marked as an error
                msg = bcast.get_messages()[0]
                self.assertEquals(ERRORED, msg.status)
                self.assertEquals(1, msg.error_count)
                self.assertTrue(msg.next_attempt)
        finally:
            settings.SEND_MESSAGES = False


class BlackmynaTest(TembaTest):

    def test_received(self):
        self.channel.channel_type = 'BM'
        self.channel.uuid = 'asdf-asdf-asdf-asdf'
        self.channel.address = '1212'
        self.channel.country = 'NP'
        self.channel.save()

        data = {'to': '1212', 'from': '+977788123123', 'text': 'Hello World', 'smsc': 'NTNepal5002'}
        encoded_message = urlencode(data)

        callback_url = reverse('api.blackmyna_handler', args=['receive', self.channel.uuid]) + "?" + encoded_message
        response = self.client.get(callback_url)

        self.assertEquals(200, response.status_code)

        # load our message
        sms = Msg.objects.get()
        self.assertEquals('+977788123123', sms.contact.get_urn(TEL_SCHEME).path)
        self.assertEquals(INCOMING, sms.direction)
        self.assertEquals(self.org, sms.org)
        self.assertEquals(self.channel, sms.channel)
        self.assertEquals("Hello World", sms.text)

        # try it with an invalid receiver, should fail as UUID and receiver id are mismatched
        data['to'] = '1515'
        encoded_message = urlencode(data)

        callback_url = reverse('api.blackmyna_handler', args=['receive', self.channel.uuid]) + "?" + encoded_message
        response = self.client.get(callback_url)

        # should get 400 as the channel wasn't found
        self.assertEquals(400, response.status_code)

    def test_send(self):
        self.channel.config = json.dumps(dict(username='bm-user', password='bm-password'))
        self.channel.channel_type = 'BM'
        self.channel.save()

        joe = self.create_contact("Joe", "+977788123123")
        bcast = joe.send("Test message", self.admin, trigger_send=False)

        # our outgoing sms
        sms = bcast.get_messages()[0]

        try:
            settings.SEND_MESSAGES = True

            with patch('requests.post') as mock:
                mock.return_value = MockResponse(200, json.dumps([{'recipient': '+977788123123',
                                                                   'id': 'asdf-asdf-asdf-asdf'}]))

                # manually send it off
                Channel.send_message(dict_to_struct('MsgStruct', sms.as_task_json()))

                # check the status of the message is now sent
                msg = bcast.get_messages()[0]
                self.assertEquals(WIRED, msg.status)
                self.assertTrue(msg.sent_on)
                self.assertEquals('asdf-asdf-asdf-asdf', msg.external_id)

                self.clear_cache()

            # return 400
            with patch('requests.post') as mock:
                mock.return_value = MockResponse(400, "Error", method='POST')

                # manually send it off
                Channel.send_message(dict_to_struct('MsgStruct', sms.as_task_json()))

                # message should be marked as an error
                msg = bcast.get_messages()[0]
                self.assertEquals(ERRORED, msg.status)
                self.assertEquals(1, msg.error_count)
                self.assertTrue(msg.next_attempt)

            # return something that isn't JSON
            with patch('requests.post') as mock:
                mock.return_value = MockResponse(200, "Error", method='POST')

                # manually send it off
                Channel.send_message(dict_to_struct('MsgStruct', sms.as_task_json()))

                # message should be marked as an error
                msg = bcast.get_messages()[0]
                self.assertEquals(ERRORED, msg.status)
                self.assertEquals(1, msg.error_count)
                self.assertTrue(msg.next_attempt)

                # we should have "Error" in our error log
                log = ChannelLog.objects.filter(msg=sms).order_by('-pk')[0]
                self.assertEquals("Error", log.response)
                self.assertEquals(503, log.response_status)

        finally:
            settings.SEND_MESSAGES = False

    def test_status(self):
        self.channel.channel_type = 'BM'
        self.channel.uuid = uuid.uuid4()
        self.channel.save()

        # an invalid uuid
        data = dict(id='-1', status='10')
        response = self.client.get(reverse('api.blackmyna_handler', args=['status', 'not-real-uuid']), data)
        self.assertEquals(400, response.status_code)

        # a valid uuid, but invalid data
        status_url = reverse('api.blackmyna_handler', args=['status', self.channel.uuid])
        response = self.client.get(status_url, dict())
        self.assertEquals(400, response.status_code)

        response = self.client.get(status_url, data)
        self.assertEquals(400, response.status_code)

        # ok, lets create an outgoing message to update
        joe = self.create_contact("Joe Biden", "+254788383383")
        broadcast = joe.send("Hey Joe, it's Obama, pick up!", self.admin)
        sms = broadcast.get_messages()[0]
        sms.external_id = 'msg-uuid'
        sms.save()

        data['id'] = sms.external_id

        def assertStatus(sms, status, assert_status):
            sms.status = WIRED
            sms.save()
            data['status'] = status
            response = self.client.get(status_url, data)
            self.assertEquals(200, response.status_code)
            sms = Msg.objects.get(external_id=sms.external_id)
            self.assertEquals(assert_status, sms.status)

        assertStatus(sms, '0', WIRED)
        assertStatus(sms, '1', DELIVERED)
        assertStatus(sms, '2', FAILED)
        assertStatus(sms, '3', WIRED)
        assertStatus(sms, '4', WIRED)
        assertStatus(sms, '8', SENT)
        assertStatus(sms, '16', FAILED)


class SMSCentralTest(TembaTest):

    def test_received(self):
        self.channel.channel_type = 'SC'
        self.channel.uuid = 'asdf-asdf-asdf-asdf'
        self.channel.address = '1212'
        self.channel.country = 'NP'
        self.channel.save()

        data = {'mobile': '+977788123123', 'message': 'Hello World', 'telco': 'Ncell'}
        encoded_message = urlencode(data)

        callback_url = reverse('api.smscentral_handler', args=['receive', self.channel.uuid]) + "?" + encoded_message
        response = self.client.get(callback_url)

        self.assertEquals(200, response.status_code)

        # load our message
        sms = Msg.objects.get()
        self.assertEquals('+977788123123', sms.contact.get_urn(TEL_SCHEME).path)
        self.assertEquals(INCOMING, sms.direction)
        self.assertEquals(self.org, sms.org)
        self.assertEquals(self.channel, sms.channel)
        self.assertEquals("Hello World", sms.text)

        # try it with an invalid channel
        callback_url = reverse('api.smscentral_handler', args=['receive', '1234-asdf']) + "?" + encoded_message
        response = self.client.get(callback_url)

        # should get 400 as the channel wasn't found
        self.assertEquals(400, response.status_code)

    def test_send(self):
        self.channel.config = json.dumps(dict(username='sc-user', password='sc-password'))
        self.channel.channel_type = 'SC'
        self.channel.save()

        joe = self.create_contact("Joe", "+977788123123")
        bcast = joe.send("Test message", self.admin, trigger_send=False)

        # our outgoing sms
        sms = bcast.get_messages()[0]

        try:
            settings.SEND_MESSAGES = True

            with patch('requests.post') as mock:
                mock.return_value = MockResponse(200, '')

                # manually send it off
                Channel.send_message(dict_to_struct('MsgStruct', sms.as_task_json()))

                # check the status of the message is now sent
                msg = bcast.get_messages()[0]
                self.assertEquals(WIRED, msg.status)
                self.assertTrue(msg.sent_on)

                mock.assert_called_with('http://smail.smscentral.com.np/bp/ApiSms.php',
                                        data={'user': 'sc-user', 'pass': 'sc-password',
                                              'mobile': '977788123123', 'content': "Test message"},
                                        headers=TEMBA_HEADERS,
                                        timeout=30)

                self.clear_cache()

            # return 400
            with patch('requests.post') as mock:
                mock.return_value = MockResponse(400, "Error", method='POST')

                # manually send it off
                Channel.send_message(dict_to_struct('MsgStruct', sms.as_task_json()))

                # message should be marked as an error
                msg = bcast.get_messages()[0]
                self.assertEquals(ERRORED, msg.status)
                self.assertEquals(1, msg.error_count)
                self.assertTrue(msg.next_attempt)

        finally:
            settings.SEND_MESSAGES = False


class Hub9Test(TembaTest):

    def test_received(self):
        # change our channel to hub9 channel
        self.channel.channel_type = 'H9'
        self.channel.uuid = 'asdf-asdf-asdf-asdf'
        self.channel.address = '+6289881134567'
        self.channel.country = 'ID'
        self.channel.save()

        # http://localhost:8000/api/v1/hub9/received/9bbffaeb-3b12-4fe1-bcaa-fd50cce2ada2/?
        # userid=testusr&password=test&original=6289881134567&sendto=6282881134567
        # &messageid=99123635&message=Test+sending+sms
        data = {'userid': 'testusr', 'password': 'test', 'original':'6289881134560', 'sendto':'6289881134567', 'message': 'Hello World'}
        encoded_message = urlencode(data)

        callback_url = reverse('api.hub9_handler', args=['received', self.channel.uuid]) + "?" + encoded_message
        response = self.client.get(callback_url)

        self.assertEquals(200, response.status_code)

        # load our message
        sms = Msg.objects.get()
        self.assertEquals('+6289881134560', sms.contact.get_urn(TEL_SCHEME).path)
        self.assertEquals(INCOMING, sms.direction)
        self.assertEquals(self.org, sms.org)
        self.assertEquals(self.channel, sms.channel)
        self.assertEquals("Hello World", sms.text)

        # try it with an invalid receiver, should fail as UUID and receiver id are mismatched
        data['sendto'] = '6289881131111'
        encoded_message = urlencode(data)

        callback_url = reverse('api.hub9_handler', args=['received', self.channel.uuid]) + "?" + encoded_message
        response = self.client.get(callback_url)

        # should get 404 as the channel wasn't found
        self.assertEquals(404, response.status_code)

        # the case of 11 digits numer from hub9
        data = {'userid': 'testusr', 'password': 'test', 'original':'62811999374', 'sendto':'6289881134567', 'message': 'Hello Jakarta'}
        encoded_message = urlencode(data)

        callback_url = reverse('api.hub9_handler', args=['received', self.channel.uuid]) + "?" + encoded_message
        response = self.client.get(callback_url)

        self.assertEquals(200, response.status_code)

        # load our message
        sms = Msg.objects.all().order_by('-pk').first()
        self.assertEquals('+62811999374', sms.contact.raw_tel())
        self.assertEquals(INCOMING, sms.direction)
        self.assertEquals(self.org, sms.org)
        self.assertEquals(self.channel, sms.channel)
        self.assertEquals("Hello Jakarta", sms.text)

    def test_send(self):
        self.channel.config = json.dumps(dict(username='h9-user', password='h9-password'))
        self.channel.channel_type = 'H9'
        self.channel.save()

        joe = self.create_contact("Joe", "+250788383383")
        bcast = joe.send("Test message", self.admin, trigger_send=False)

        # our outgoing sms
        sms = bcast.get_messages()[0]

        try:
            settings.SEND_MESSAGES = True

            with patch('requests.get') as mock:
                mock.return_value = MockResponse(200, "000")

                # manually send it off
                Channel.send_message(dict_to_struct('MsgStruct', sms.as_task_json()))

                # check the status of the message is now sent
                msg = bcast.get_messages()[0]
                self.assertEquals(SENT, msg.status)
                self.assertTrue(msg.sent_on)

                self.clear_cache()

            with patch('requests.get') as mock:
                mock.return_value = MockResponse(400, "Error", method='POST')

                # manually send it off
                Channel.send_message(dict_to_struct('MsgStruct', sms.as_task_json()))

                # message should be marked as an error
                msg = bcast.get_messages()[0]
                self.assertEquals(ERRORED, msg.status)
                self.assertEquals(1, msg.error_count)
                self.assertTrue(msg.next_attempt)
        finally:
            settings.SEND_MESSAGES = False


class HighConnectionTest(TembaTest):

    def test_handler(self):
        # change our channel to high connection channel
        self.channel.channel_type = 'HX'
        self.channel.uuid = 'asdf-asdf-asdf-asdf'
        self.channel.address = '5151'
        self.channel.country = 'FR'
        self.channel.save()

        # http://localhost:8000/api/v1/hcnx/receive/asdf-asdf-asdf-asdf/?FROM=+33610346460&TO=5151&MESSAGE=Hello+World
        data = {'FROM': '+33610346460', 'TO': '5151', 'MESSAGE': 'Hello World', 'RECEPTION_DATE': '2015-04-02T14:26:06'}

        callback_url = reverse('api.hcnx_handler', args=['receive', self.channel.uuid])
        response = self.client.post(callback_url, data)

        self.assertEquals(200, response.status_code)

        # load our message
        msg = Msg.objects.get()
        self.assertEquals('+33610346460', msg.contact.get_urn(TEL_SCHEME).path)
        self.assertEquals(INCOMING, msg.direction)
        self.assertEquals(self.org, msg.org)
        self.assertEquals(self.channel, msg.channel)
        self.assertEquals("Hello World", msg.text)
        self.assertEquals(14, msg.created_on.astimezone(pytz.utc).hour)

        # try it with an invalid receiver, should fail as UUID isn't known
        callback_url = reverse('api.hcnx_handler', args=['receive', uuid.uuid4()])
        response = self.client.post(callback_url, data)

        # should get 400 as the channel wasn't found
        self.assertEquals(400, response.status_code)

        # create an outgoing message instead
        contact = msg.contact
        Msg.objects.all().delete()

        contact.send("outgoing message", self.admin)
        msg = Msg.objects.get()

        # now update the status via a callback
        data = {'ret_id': msg.id, 'status': '6'}
        encoded_message = urlencode(data)

        callback_url = reverse('api.hcnx_handler', args=['status', self.channel.uuid]) + "?" + encoded_message
        response = self.client.get(callback_url)

        self.assertEquals(200, response.status_code)

        msg = Msg.objects.get()
        self.assertEquals(DELIVERED, msg.status)

    def test_send(self):
        self.channel.config = json.dumps(dict(username='hcnx-user', password='hcnx-password'))
        self.channel.channel_type = 'HX'
        self.channel.uuid = 'asdf-asdf-asdf-asdf'
        self.channel.save()

        joe = self.create_contact("Joe", "+250788383383")
        bcast = joe.send("Test message", self.admin, trigger_send=False)

        # our outgoing sms
        msg = bcast.get_messages()[0]

        try:
            settings.SEND_MESSAGES = True

            with patch('requests.get') as mock:
                mock.return_value = MockResponse(200, "Sent")

                # manually send it off
                Channel.send_message(dict_to_struct('MsgStruct', msg.as_task_json()))

                # check the status of the message is now sent
                msg = bcast.get_messages()[0]
                self.assertEquals(WIRED, msg.status)
                self.assertTrue(msg.sent_on)

                self.clear_cache()

            with patch('requests.get') as mock:
                mock.return_value = MockResponse(400, "Error")

                # manually send it off
                Channel.send_message(dict_to_struct('MsgStruct', msg.as_task_json()))

                # message should be marked as an error
                msg = bcast.get_messages()[0]
                self.assertEquals(ERRORED, msg.status)
                self.assertEquals(1, msg.error_count)
                self.assertTrue(msg.next_attempt)
        finally:
            settings.SEND_MESSAGES = False


class TwilioTest(TembaTest):

    def test_receive(self):
        # change our channel to a twilio channel
        self.channel.channel_type = 'T'
        self.channel.save()

        # twilio test credentials
        account_sid = "ACe54dc36bfd2a3b483b7ed854b2dd40c1"
        account_token = "0b14d47901387c03f92253a4e4449d5e"
        application_sid = "AP6fe2069df7f9482a8031cb61dc155de2"

        self.channel.org.config = json.dumps({ACCOUNT_SID:account_sid, ACCOUNT_TOKEN:account_token, APPLICATION_SID:application_sid})
        self.channel.org.save()

        post_data = dict(To=self.channel.address, From='+250788383383', Body="Hello World")
        twilio_url = reverse('api.twilio_handler')

        try:
            response = self.client.post(twilio_url, post_data)
            self.fail("Invalid signature, should have failed")
        except ValidationError as e:
            pass

        # this time sign it appropriately, should work
        client = self.org.get_twilio_client()
        validator = RequestValidator(client.auth[1])
        signature = validator.compute_signature('https://' + settings.TEMBA_HOST + '/api/v1/twilio/', post_data)
        response = self.client.post(twilio_url, post_data, **{'HTTP_X_TWILIO_SIGNATURE': signature})

        self.assertEquals(201, response.status_code)

        # and we should have a new message
        msg1 = Msg.objects.get()
        self.assertEquals("+250788383383", msg1.contact.get_urn(TEL_SCHEME).path)
        self.assertEquals(INCOMING, msg1.direction)
        self.assertEquals(self.org, msg1.org)
        self.assertEquals(self.channel, msg1.channel)
        self.assertEquals("Hello World", msg1.text)

        # try with non-normalized number
        post_data['To'] = '0785551212'
        post_data['ToCountry'] = 'RW'
        signature = validator.compute_signature('https://' + settings.TEMBA_HOST + '/api/v1/twilio/', post_data)
        response = self.client.post(twilio_url, post_data, **{'HTTP_X_TWILIO_SIGNATURE': signature})
        self.assertEquals(201, response.status_code)

        # and we should have another new message
        msg2 = Msg.objects.exclude(pk=msg1.pk).get()
        self.assertEquals(self.channel, msg2.channel)

        # create an outgoing message instead
        contact = msg2.contact
        Msg.objects.all().delete()

        contact.send("outgoing message", self.admin)
        sms = Msg.objects.get()

        # now update the status via a callback
        twilio_url = "%s?action=callback&id=%d" % (twilio_url, sms.id)
        post_data['SmsStatus'] = 'sent'

        signature = validator.compute_signature('https://' + settings.TEMBA_HOST + '%s' % twilio_url, post_data)
        response = self.client.post(twilio_url, post_data, **{ 'HTTP_X_TWILIO_SIGNATURE': signature })

        self.assertEquals(200, response.status_code)

        sms = Msg.objects.get()
        self.assertEquals(SENT, sms.status)

        # try it with a failed SMS
        Msg.objects.all().delete()
        contact.send("outgoing message", self.admin)
        sms = Msg.objects.get()

        # now update the status via a callback
        twilio_url = "%s?action=callback&id=%d" % (twilio_url, sms.id)
        post_data['SmsStatus'] = 'failed'

        signature = validator.compute_signature('https://' + settings.TEMBA_HOST + '%s' % twilio_url, post_data)
        response = self.client.post(twilio_url, post_data, **{ 'HTTP_X_TWILIO_SIGNATURE': signature })

        self.assertEquals(200, response.status_code)
        sms = Msg.objects.get()
        self.assertEquals(FAILED, sms.status)

    def test_send(self):
        from temba.orgs.models import ACCOUNT_SID, ACCOUNT_TOKEN, APPLICATION_SID
        org_config = self.org.config_json()
        org_config[ACCOUNT_SID] = 'twilio_sid'
        org_config[ACCOUNT_TOKEN] = 'twilio_token'
        org_config[APPLICATION_SID] = 'twilio_sid'
        self.org.config = json.dumps(org_config)
        self.org.save()

        self.channel.channel_type = TWILIO
        self.channel.save()

        joe = self.create_contact("Joe", "+250788383383")
        bcast = joe.send("Test message", self.admin, trigger_send=False)

        # our outgoing sms
        sms = bcast.get_messages()[0]

        try:
            settings.SEND_MESSAGES = True

            with patch('twilio.rest.resources.Messages.create') as mock:
                mock.return_value = "Sent"

                # manually send it off
                Channel.send_message(dict_to_struct('MsgStruct', sms.as_task_json()))

                # check the status of the message is now sent
                msg = bcast.get_messages()[0]
                self.assertEquals(WIRED, msg.status)
                self.assertTrue(msg.sent_on)

                self.clear_cache()

            with patch('twilio.rest.resources.Messages.create') as mock:
                mock.side_effect = Exception("Failed to send message")

                # manually send it off
                Channel.send_message(dict_to_struct('MsgStruct', sms.as_task_json()))

                # message should be marked as an error
                msg = bcast.get_messages()[0]
                self.assertEquals(ERRORED, msg.status)
                self.assertEquals(1, msg.error_count)
                self.assertTrue(msg.next_attempt)

            # check that our channel log works as well
            self.login(self.admin)

            response = self.client.get(reverse('channels.channellog_list') + "?channel=%d" % (self.channel.pk))

            # there should be two log items for the two times we sent
            self.assertEquals(2, len(response.context['channellog_list']))

            # of items on this page should be right as well
            self.assertEquals(2, response.context['paginator'].count)

            # the counts on our relayer should be correct as well
            self.channel = Channel.objects.get(id=self.channel.pk)
            self.assertEquals(1, self.channel.get_error_log_count())
            self.assertEquals(1, self.channel.get_success_log_count())

            # view the detailed information for one of them
            response = self.client.get(reverse('channels.channellog_read', args=[ChannelLog.objects.all()[1].pk]))

            # check that it contains the log of our exception
            self.assertContains(response, "Failed to send message")

            # delete our error entry
            ChannelLog.objects.filter(is_error=True).delete()

            # our counts should be right
            # the counts on our relayer should be correct as well
            self.channel = Channel.objects.get(id=self.channel.pk)
            self.assertEquals(0, self.channel.get_error_log_count())
            self.assertEquals(1, self.channel.get_success_log_count())

        finally:
            settings.SEND_MESSAGES = False


class ClickatellTest(TembaTest):

    def test_receive_utf16(self):
        self.channel.channel_type = CLICKATELL
        self.channel.uuid = uuid.uuid4()
        self.channel.save()

        self.channel.org.config = json.dumps({API_ID:'12345', USERNAME:'uname', PASSWORD:'pword'})
        self.channel.org.save()

        data = {'to': self.channel.address,
                'from': '250788383383',
                'timestamp': '2012-10-10 10:10:10',
                'moMsgId': 'id1234'}

        encoded_message = urlencode(data)
        encoded_message += "&text=%00m%00e%00x%00i%00c%00o%00+%00k%00+%00m%00i%00s%00+%00p%00a%00p%00a%00s%00+%00n%00o%00+%00t%00e%00n%00%ED%00a%00+%00d%00i%00n%00e%00r%00o%00+%00p%00a%00r%00a%00+%00c%00o%00m%00p%00r%00a%00r%00n%00o%00s%00+%00l%00o%00+%00q%00+%00q%00u%00e%00r%00%ED%00a%00m%00o%00s%00.%00."
        encoded_message += "&charset=UTF-16BE"
        receive_url = reverse('api.clickatell_handler', args=['receive', self.channel.uuid]) + '?' + encoded_message

        response = self.client.get(receive_url)

        self.assertEquals(200, response.status_code)

        # and we should have a new message
        msg1 = Msg.objects.get()
        self.assertEquals("+250788383383", msg1.contact.get_urn(TEL_SCHEME).path)
        self.assertEquals(INCOMING, msg1.direction)
        self.assertEquals(self.org, msg1.org)
        self.assertEquals(self.channel, msg1.channel)
        self.assertEquals(u"mexico k mis papas no ten\xeda dinero para comprarnos lo q quer\xedamos..", msg1.text)
        self.assertEquals(2012, msg1.created_on.year)
        self.assertEquals('id1234', msg1.external_id)

    def test_receive(self):
        # change our channel to a clickatell channel
        self.channel.channel_type = CLICKATELL
        self.channel.uuid = uuid.uuid4()
        self.channel.save()

        self.channel.org.config = json.dumps({API_ID:'12345', USERNAME:'uname', PASSWORD:'pword'})
        self.channel.org.save()

        data = {'to': self.channel.address,
                'from': '250788383383',
                'text': "Hello World",
                'timestamp': '2012-10-10 10:10:10',
                'moMsgId': 'id1234'}

        encoded_message = urlencode(data)
        receive_url = reverse('api.clickatell_handler', args=['receive', self.channel.uuid]) + '?' + encoded_message

        response = self.client.get(receive_url)

        self.assertEquals(200, response.status_code)

        # and we should have a new message
        msg1 = Msg.objects.get()
        self.assertEquals("+250788383383", msg1.contact.get_urn(TEL_SCHEME).path)
        self.assertEquals(INCOMING, msg1.direction)
        self.assertEquals(self.org, msg1.org)
        self.assertEquals(self.channel, msg1.channel)
        self.assertEquals("Hello World", msg1.text)
        self.assertEquals(2012, msg1.created_on.year)

        # times are sent as GMT+2
        self.assertEquals(8, msg1.created_on.hour)
        self.assertEquals('id1234', msg1.external_id)

    def test_status(self):
        # change our channel to a clickatell channel
        self.channel.channel_type = CLICKATELL
        self.channel.uuid = uuid.uuid4()
        self.channel.save()

        self.channel.org.config = json.dumps({API_ID:'12345', USERNAME:'uname', PASSWORD:'pword'})
        self.channel.org.save()

        contact = self.create_contact("Joe", "+250788383383")
        sms = Msg.create_outgoing(self.org, self.user, contact, "test")
        sms.external_id = 'id1234'
        sms.save()

        data = {'apiMsgId': 'id1234', 'status': '001'}
        encoded_message = urlencode(data)

        callback_url = reverse('api.clickatell_handler', args=['status', self.channel.uuid]) + "?" + encoded_message
        response = self.client.get(callback_url)

        self.assertEquals(200, response.status_code)

        # reload our message
        sms = Msg.objects.get(pk=sms.pk)

        # make sure it is marked as failed
        self.assertEquals(FAILED, sms.status)

        # reset our status to WIRED
        sms.status = WIRED
        sms.save()

        # and do it again with a received state
        data = {'apiMsgId': 'id1234', 'status': '004'}
        encoded_message = urlencode(data)

        callback_url = reverse('api.clickatell_handler', args=['status', self.channel.uuid]) + "?" + encoded_message
        response = self.client.get(callback_url)

        # load our message
        sms = Msg.objects.all().order_by('-pk').first()

        # make sure it is marked as delivered
        self.assertEquals(DELIVERED, sms.status)

    def test_send(self):
        self.channel.config = json.dumps(dict(username='uname', password='pword', api_id='api1'))
        self.channel.channel_type = CLICKATELL
        self.channel.save()

        joe = self.create_contact("Joe", "+250788383383")
        bcast = joe.send("Test message", self.admin, trigger_send=False)

        # our outgoing sms
        sms = bcast.get_messages()[0]

        try:
            settings.SEND_MESSAGES = True

            with patch('requests.get') as mock:
                mock.return_value = MockResponse(200, "000")

                # manually send it off
                Channel.send_message(dict_to_struct('MsgStruct', sms.as_task_json()))

                # check the status of the message is now sent
                msg = bcast.get_messages()[0]
                self.assertEquals(WIRED, msg.status)
                self.assertTrue(msg.sent_on)

                self.clear_cache()

            with patch('requests.get') as mock:
                mock.return_value = MockResponse(400, "Error", method='POST')

                # manually send it off
                Channel.send_message(dict_to_struct('MsgStruct', sms.as_task_json()))

                # message should be marked as an error
                msg = bcast.get_messages()[0]
                self.assertEquals(ERRORED, msg.status)
                self.assertEquals(1, msg.error_count)
                self.assertTrue(msg.next_attempt)
        finally:
            settings.SEND_MESSAGES = False

class PlivoTest(TembaTest):
    def setUp(self):
        super(PlivoTest, self).setUp()

        # change our channel to plivo channel
        self.channel.channel_type = PLIVO
        self.channel.uuid = unicode(uuid.uuid4())

        plivo_config = {PLIVO_AUTH_ID:'plivo-auth-id',
                        PLIVO_AUTH_TOKEN:'plivo-auth-token',
                        PLIVO_APP_ID:'plivo-app-id'}

        self.channel.config = json.dumps(plivo_config)
        self.channel.save()

        self.joe = self.create_contact("Joe", "+250788383383")

    def test_receive(self):
        response = self.client.get(reverse('api.plivo_handler', args=['receive', 'not-real-uuid']), dict())
        self.assertEquals(400, response.status_code)

        data = dict(MessageUUID="msg-uuid", Text="Hey, there", To="254788383383", From="254788383383")
        receive_url = reverse('api.plivo_handler', args=['receive', self.channel.uuid])
        response = self.client.get(receive_url, data)
        self.assertEquals(400, response.status_code)

        data = dict(MessageUUID="msg-uuid", Text="Hey, there", To=self.channel.address.lstrip('+'), From="254788383383")
        response = self.client.get(receive_url, data)
        self.assertEquals(200, response.status_code)

        msg1 = Msg.objects.get()
        self.assertEquals("+254788383383", msg1.contact.get_urn(TEL_SCHEME).path)
        self.assertEquals(INCOMING, msg1.direction)
        self.assertEquals(self.org, msg1.org)
        self.assertEquals(self.channel, msg1.channel)
        self.assertEquals('Hey, there', msg1.text)

    def test_status(self):
        # an invalid uuid
        data = dict(MessageUUID="-1", Status="delivered", From=self.channel.address.lstrip('+'), To="254788383383")
        response = self.client.get(reverse('api.plivo_handler', args=['status', 'not-real-uuid']), data)
        self.assertEquals(400, response.status_code)

        # a valid uuid, but invalid data
        delivery_url = reverse('api.plivo_handler', args=['status', self.channel.uuid])
        response = self.client.get(delivery_url, dict())
        self.assertEquals(400, response.status_code)

        response = self.client.get(delivery_url, data)
        self.assertEquals(400, response.status_code)

        # ok, lets create an outgoing message to update
        joe = self.create_contact("Joe Biden", "+254788383383")
        broadcast = joe.send("Hey Joe, it's Obama, pick up!", self.admin)
        sms = broadcast.get_messages()[0]
        sms.external_id = 'msg-uuid'
        sms.save()

        data['MessageUUID'] = sms.external_id

        def assertStatus(sms, status, assert_status):
            sms.status = WIRED
            sms.save()
            data['Status'] = status
            response = self.client.get(delivery_url, data)
            self.assertEquals(200, response.status_code)
            sms = Msg.objects.get(external_id=sms.external_id)
            self.assertEquals(assert_status, sms.status)

        assertStatus(sms, 'queued', WIRED)
        assertStatus(sms, 'sent', SENT)
        assertStatus(sms, 'delivered', DELIVERED)
        assertStatus(sms, 'undelivered', SENT)
        assertStatus(sms, 'rejected', FAILED)

    def test_send(self):

        bcast = self.joe.send("Test message", self.admin, trigger_send=False)

        # our outgoing sms
        sms = bcast.get_messages()[0]

        try:
            settings.SEND_MESSAGES = True

            with patch('requests.post') as mock:
                mock.return_value = MockResponse(202,
                                                 json.dumps({"message": "message(s) queued",
                                                             "message_uuid": ["db3ce55a-7f1d-11e1-8ea7-1231380bc196"],
                                                             "api_id": "db342550-7f1d-11e1-8ea7-1231380bc196"}))


                # manually send it off
                Channel.send_message(dict_to_struct('MsgStruct', sms.as_task_json()))

                # check the status of the message is now sent
                msg = bcast.get_messages()[0]
                self.assertEquals(WIRED, msg.status)
                self.assertTrue(msg.sent_on)

                self.clear_cache()

            with patch('requests.get') as mock:
                mock.return_value = MockResponse(400, "Error", method='POST')

                # manually send it off
                Channel.send_message(dict_to_struct('MsgStruct', sms.as_task_json()))

                # message should be marked as an error
                msg = bcast.get_messages()[0]
                self.assertEquals(ERRORED, msg.status)
                self.assertEquals(1, msg.error_count)
                self.assertTrue(msg.next_attempt)
        finally:
            settings.SEND_MESSAGES = False


class TwitterTest(TembaTest):

    def test_send(self):
        self.channel.config = json.dumps({
            'oauth_token': 'abcdefghijklmnopqrstuvwxyz',
            'oauth_token_secret': '0123456789'
        })
        self.channel.channel_type = 'TT'
        self.channel.save()

        joe = self.create_contact("Joe", number="+250788383383", twitter="joe1981")
        testers = self.create_group("Testers", [joe])

        bcast = joe.send("This is a long message, longer than just 160 characters, it spans what was before "
                         "more than one message but which is now but one, solitary message, going off into the "
                         "Twitterverse to tweet away.",
                         self.admin, trigger_send=False)

        # our outgoing message
        msg = bcast.get_messages()[0]

        try:
            settings.SEND_MESSAGES = True

            with patch('twython.Twython.send_direct_message') as mock:
                mock.return_value = dict(id=1234567890)

                # manually send it off
                Channel.send_message(dict_to_struct('MsgStruct', msg.as_task_json()))

                # assert we were only called once
                self.assertEquals(1, mock.call_count)

                # check the status of the message is now sent
                msg = bcast.get_messages()[0]
                self.assertEquals(WIRED, msg.status)
                self.assertEquals('1234567890', msg.external_id)
                self.assertTrue(msg.sent_on)

                self.clear_cache()

            ChannelLog.objects.all().delete()

            with patch('twython.Twython.send_direct_message') as mock:
                mock.side_effect = TwythonError("Failed to send message")

                # manually send it off
                Channel.send_message(dict_to_struct('MsgStruct', msg.as_task_json()))

                # message should be marked as an error
                msg = bcast.get_messages()[0]
                self.assertEquals(ERRORED, msg.status)
                self.assertEquals(1, msg.error_count)
                self.assertTrue(msg.next_attempt)
                self.assertEquals("Failed to send message", ChannelLog.objects.get(msg=msg).description)

                self.clear_cache()

            ChannelLog.objects.all().delete()

            with patch('twython.Twython.send_direct_message') as mock:
                mock.side_effect = TwythonError("Different 403 error.", error_code=403)

                # manually send it off
                Channel.send_message(dict_to_struct('MsgStruct', msg.as_task_json()))

                # message should be marked as an error
                msg = bcast.get_messages()[0]
                self.assertEquals(ERRORED, msg.status)
                self.assertEquals(2, msg.error_count)
                self.assertTrue(msg.next_attempt)

                # should not fail the contact
                contact = Contact.objects.get(pk=joe.pk)
                self.assertFalse(contact.is_failed)
                self.assertEqual(contact.user_groups.count(), 1)

                # should record the right error
                self.assertTrue(ChannelLog.objects.get(msg=msg).description.find("Different 403 error") >= 0)

            with patch('twython.Twython.send_direct_message') as mock:
                mock.side_effect = TwythonError("You cannot send messages to users who are not following you.",
                                                error_code=403)

                # manually send it off
                Channel.send_message(dict_to_struct('MsgStruct', msg.as_task_json()))

                # should fail the message
                msg = bcast.get_messages()[0]
                self.assertEquals(FAILED, msg.status)
                self.assertEquals(2, msg.error_count)

                # should fail the contact permanently (i.e. removed from groups)
                contact = Contact.objects.get(pk=joe.pk)
                self.assertTrue(contact.is_failed)
                self.assertEqual(contact.user_groups.count(), 0)

                self.clear_cache()

            joe.is_failed = False
            joe.save()
            testers.update_contacts([joe], add=True)

            with patch('twython.Twython.send_direct_message') as mock:
                mock.side_effect = TwythonError("There was an error sending your message: You can't send direct messages to this user right now.",
                                                error_code=403)

                # manually send it off
                Channel.send_message(dict_to_struct('MsgStruct', msg.as_task_json()))

                # should fail the message
                msg = bcast.get_messages()[0]
                self.assertEquals(FAILED, msg.status)
                self.assertEquals(2, msg.error_count)

                # should fail the contact permanently (i.e. removed from groups)
                contact = Contact.objects.get(pk=joe.pk)
                self.assertTrue(contact.is_failed)
                self.assertEqual(contact.user_groups.count(), 0)

                self.clear_cache()

            joe.is_failed = False
            joe.save()
            testers.update_contacts([joe], add=True)

            with patch('twython.Twython.send_direct_message') as mock:
                mock.side_effect = TwythonError("Sorry, that page does not exist.", error_code=404)

                # manually send it off
                Channel.send_message(dict_to_struct('MsgStruct', msg.as_task_json()))

                # should fail the message
                msg = bcast.get_messages()[0]
                self.assertEqual(msg.status, FAILED)
                self.assertEqual(msg.error_count, 2)

                # should fail the contact permanently (i.e. removed from groups)
                contact = Contact.objects.get(pk=joe.pk)
                self.assertTrue(contact.is_failed)
                self.assertEqual(contact.user_groups.count(), 0)

                self.clear_cache()

        finally:
            settings.SEND_MESSAGES = False


class MageHandlerTest(TembaTest):

    def setUp(self):
        super(MageHandlerTest, self).setUp()

        self.org.webhook = u'{"url": "http://fake.com/webhook.php"}'
        self.org.webhook_events = ALL_EVENTS
        self.org.save()

        self.joe = self.create_contact("Joe", number="+250788383383")

        self.dyn_group = ContactGroup.create(self.org, self.user, "Bobs", query="name has Bob")

    def create_contact_like_mage(self, name, twitter):
        """
        Creates a contact as if it were created in Mage, i.e. no event/group triggering or cache updating
        """
        contact = Contact.objects.create(org=self.org, name=name, is_active=True, is_blocked=False,
                                         uuid=uuid.uuid4(), is_failed=False,
                                         modified_by=self.user, created_by=self.user,
                                         modified_on=timezone.now(), created_on=timezone.now())
        urn = ContactURN.objects.create(org=self.org, contact=contact,
                                        urn="twitter:%s" % twitter, scheme="twitter", path=twitter, priority="90")
        return contact, urn

    def create_message_like_mage(self, text, contact, contact_urn=None):
        """
        Creates a message as it if were created in Mage, i.e. no topup decrementing or cache updating
        """
        if not contact_urn:
            contact_urn = contact.get_urn(TEL_SCHEME)
        return Msg.objects.create(org=self.org, text=text, direction=INCOMING, created_on=timezone.now(),
                                  channel=self.channel, contact=contact, contact_urn=contact_urn)

    def test_handle_message(self):
        url = reverse('api.mage_handler', args=['handle_message'])
        headers = dict(HTTP_AUTHORIZATION='Token %s' % settings.MAGE_AUTH_TOKEN)

        msg_counts = SystemLabel.get_counts(self.org)
        self.assertEqual(0, msg_counts[SystemLabel.TYPE_INBOX])
        self.assertEqual(0, msg_counts[SystemLabel.TYPE_FLOWS])

        contact_counts = ContactGroup.get_system_group_counts(self.org)
        self.assertEqual(1, contact_counts[ContactGroup.TYPE_ALL])
        self.assertEqual(1000, self.org.get_credits_remaining())

        msg = self.create_message_like_mage(text="Hello 1", contact=self.joe)

        msg_counts = SystemLabel.get_counts(self.org)
        self.assertEqual(0, msg_counts[SystemLabel.TYPE_INBOX])

        contact_counts = ContactGroup.get_system_group_counts(self.org)
        self.assertEqual(1, contact_counts[ContactGroup.TYPE_ALL])

        self.assertEqual(1000, self.org.get_credits_remaining())

        # check that GET doesn't work
        response = self.client.get(url, dict(message_id=msg.pk), **headers)
        self.assertEqual(405, response.status_code)

        # check that POST does work
        response = self.client.post(url, dict(message_id=msg.pk, new_contact=False), **headers)
        self.assertEqual(200, response.status_code)

        # check that new message is handled and has a topup
        msg = Msg.objects.get(pk=msg.pk)
        self.assertEqual('H', msg.status)
        self.assertEqual(self.welcome_topup, msg.topup)

        # check for a web hook event
        event = json.loads(WebHookEvent.objects.get(org=self.org, event=SMS_RECEIVED).data)
        self.assertEqual(msg.id, event['sms'])

        msg_counts = SystemLabel.get_counts(self.org)
        self.assertEqual(1, msg_counts[SystemLabel.TYPE_INBOX])

        contact_counts = ContactGroup.get_system_group_counts(self.org)
        self.assertEqual(1, contact_counts[ContactGroup.TYPE_ALL])

        self.assertEqual(999, self.org.get_credits_remaining())

        # check that a message that has a topup, doesn't decrement twice
        msg = self.create_message_like_mage(text="Hello 2", contact=self.joe)
        msg.topup_id = self.org.decrement_credit()
        msg.save()

        self.client.post(url, dict(message_id=msg.pk, new_contact=False), **headers)
        msg_counts = SystemLabel.get_counts(self.org)
        self.assertEqual(2, msg_counts[SystemLabel.TYPE_INBOX])

        contact_counts = ContactGroup.get_system_group_counts(self.org)
        self.assertEqual(1, contact_counts[ContactGroup.TYPE_ALL])

        self.assertEqual(998, self.org.get_credits_remaining())

        # simulate scenario where Mage has added new contact with name that should put it into a dynamic group
        mage_contact, mage_contact_urn = self.create_contact_like_mage("Bob", "bobby81")
        msg = self.create_message_like_mage(text="Hello via Mage", contact=mage_contact, contact_urn=mage_contact_urn)

        response = self.client.post(url, dict(message_id=msg.pk, new_contact=True), **headers)
        self.assertEqual(200, response.status_code)

        msg = Msg.objects.get(pk=msg.pk)
        self.assertEqual('H', msg.status)
        self.assertEqual(self.welcome_topup, msg.topup)

        msg_counts = SystemLabel.get_counts(self.org)
        self.assertEqual(3, msg_counts[SystemLabel.TYPE_INBOX])

        contact_counts = ContactGroup.get_system_group_counts(self.org)
        self.assertEqual(2, contact_counts[ContactGroup.TYPE_ALL])

        self.assertEqual(997, self.org.get_credits_remaining())

        # check that contact ended up dynamic group
        self.assertEqual([mage_contact], list(self.dyn_group.contacts.order_by('name')))

        # check invalid auth key
        response = self.client.post(url, dict(message_id=msg.pk), **dict(HTTP_AUTHORIZATION='Token xyz'))
        self.assertEqual(401, response.status_code)

        # check rejection of empty or invalid msgId
        response = self.client.post(url, dict(), **headers)
        self.assertEqual(400, response.status_code)
        response = self.client.post(url, dict(message_id='xx'), **headers)
        self.assertEqual(400, response.status_code)

    def test_follow_notification(self):
        url = reverse('api.mage_handler', args=['follow_notification'])
        headers = dict(HTTP_AUTHORIZATION='Token %s' % settings.MAGE_AUTH_TOKEN)

        flow = self.create_flow()

        channel = Channel.create(self.org, self.user, None, 'TT', "Twitter Channel", address="billy_bob")

        Trigger.objects.create(created_by=self.user, modified_by=self.user, org=self.org,
                               trigger_type=Trigger.TYPE_FOLLOW, flow=flow, channel=channel)

        contact = self.create_contact("Mary Jo", twitter='mary_jo')
        urn = contact.get_urn(TWITTER_SCHEME)

        response = self.client.post(url, dict(channel_id=channel.id, contact_urn_id=urn.id), **headers)
        self.assertEqual(200, response.status_code)
        self.assertEqual(1, flow.runs.all().count())

        contact_counts = ContactGroup.get_system_group_counts(self.org)
        self.assertEqual(2, contact_counts[ContactGroup.TYPE_ALL])

        # simulate scenario where Mage has added new contact with name that should put it into a dynamic group
        mage_contact, mage_contact_urn = self.create_contact_like_mage("Bob", "bobby81")

        response = self.client.post(url, dict(channel_id=channel.id,
                                              contact_urn_id=mage_contact_urn.id, new_contact=True), **headers)
        self.assertEqual(200, response.status_code)
        self.assertEqual(2, flow.runs.all().count())

        # check that contact ended up dynamic group
        self.assertEqual([mage_contact], list(self.dyn_group.contacts.order_by('name')))

        # check contact count updated
        contact_counts = ContactGroup.get_system_group_counts(self.org)
        self.assertEqual(contact_counts[ContactGroup.TYPE_ALL], 3)


class WebHookTest(TembaTest):

    def setUp(self):
        super(WebHookTest, self).setUp()
        self.joe = self.create_contact("Joe Blow", "0788123123")
        settings.SEND_WEBHOOKS = True

    def tearDown(self):
        super(WebHookTest, self).tearDown()
        settings.SEND_WEBHOOKS = False

    def assertStringContains(self, test, str):
        self.assertTrue(str.find(test) >= 0, "'%s' not found in '%s'" % (test, str))

    def setupChannel(self):
        org = self.channel.org
        org.webhook = u'{"url": "http://fake.com/webhook.php"}'
        org.webhook_events = ALL_EVENTS
        org.save()

        self.channel.address = "+250788123123"
        self.channel.save()

    def test_call_deliveries(self):
        self.setupChannel()
        now = timezone.now()
        call = Call.objects.create(org=self.org,
                                   channel=self.channel,
                                   contact=self.joe,
                                   call_type=Call.TYPE_IN_MISSED,
                                   time=now,
                                   created_by=self.admin,
                                   modified_by=self.admin)

        self.setupChannel()

        with patch('requests.Session.send') as mock:
            # clear out which events we listen for, we still shouldnt be notified though we have a webhook
            self.channel.org.webhook_events = 0
            self.channel.org.save()

            mock.return_value = MockResponse(200, "Hello World")

            # trigger an event, shouldnn't fire as we don't have a webhook
            WebHookEvent.trigger_call_event(call)
            self.assertFalse(WebHookEvent.objects.all())

        self.setupChannel()

        with patch('requests.Session.send') as mock:
            mock.return_value = MockResponse(200, "Hello World")

            # trigger an event
            WebHookEvent.trigger_call_event(call)
            event = WebHookEvent.objects.get()

            self.assertEquals('C', event.status)
            self.assertEquals(1, event.try_count)
            self.assertFalse(event.next_attempt)

            result = WebHookResult.objects.get()
            self.assertStringContains("Event delivered successfully", result.message)
            self.assertStringContains("not JSON", result.message)
            self.assertEquals(200, result.status_code)
            self.assertEquals("Hello World", result.body)

            self.assertTrue(mock.called)
            args = mock.call_args_list[0][0]
            prepared_request = args[0]
            self.assertEquals(self.channel.org.get_webhook_url(), prepared_request.url)

            data = parse_qs(prepared_request.body)
            self.assertEquals('+250788123123', data['phone'][0])
            self.assertEquals(call.pk, int(data['call'][0]))
            self.assertEquals(0, int(data['duration'][0]))
            self.assertEquals(call.call_type, data['event'][0])
            self.assertTrue('time' in data)
            self.assertEquals(self.channel.pk, int(data['channel'][0]))

    def test_alarm_deliveries(self):
        sync_event = SyncEvent.objects.create(channel=self.channel,
                                              power_source='AC',
                                              power_status='CHARGING',
                                              power_level=85,
                                              network_type='WIFI',
                                              pending_message_count=5,
                                              retry_message_count=4,
                                              incoming_command_count=0,
                                              created_by=self.admin,
                                              modified_by=self.admin)

        self.setupChannel()

        with patch('requests.Session.send') as mock:
            # clear out which events we listen for, we still shouldnt be notified though we have a webhook
            self.channel.org.webhook_events = 0
            self.channel.org.save()

            mock.return_value = MockResponse(200, "Hello World")

            # trigger an event, shouldnn't fire as we don't have a webhook
            WebHookEvent.trigger_channel_alarm(sync_event)
            self.assertFalse(WebHookEvent.objects.all())

        self.setupChannel()

        with patch('requests.Session.send') as mock:
            now = timezone.now()
            mock.return_value = MockResponse(200, "")

            # trigger an event
            WebHookEvent.trigger_channel_alarm(sync_event)
            event = WebHookEvent.objects.get()

            self.assertEquals('C', event.status)
            self.assertEquals(1, event.try_count)
            self.assertFalse(event.next_attempt)

            result = WebHookResult.objects.get()
            self.assertStringContains("Event delivered successfully", result.message)
            self.assertEquals(200, result.status_code)
            self.assertEquals("", result.body)

            self.assertTrue(mock.called)
            args = mock.call_args_list[0][0]
            prepared_request = args[0]
            self.assertEquals(self.channel.org.get_webhook_url(), prepared_request.url)

            data = parse_qs(prepared_request.body)
            self.assertEquals(self.channel.pk, int(data['channel'][0]))
            self.assertEquals(85, int(data['power_level'][0]))
            self.assertEquals('AC', data['power_source'][0])
            self.assertEquals('CHARGING', data['power_status'][0])
            self.assertEquals('WIFI', data['network_type'][0])
            self.assertEquals(5, int(data['pending_message_count'][0]))
            self.assertEquals(4, int(data['retry_message_count'][0]))

    def test_flow_event(self):
        self.setupChannel()

        org = self.channel.org
        org.save()

        from temba.flows.models import ActionSet, APIAction, Flow
        flow = self.create_flow()

        # replace our uuid of 4 with the right thing
        actionset = ActionSet.objects.get(x=4)
        actionset.set_actions_dict([APIAction(org.get_webhook_url()).as_json()])
        actionset.save()

        with patch('requests.Session.send') as mock:
            # run a user through this flow
            flow.start([], [self.joe])

            # have joe reply with mauve, which will put him in the other category that triggers the API Action
            sms = self.create_msg(contact=self.joe, direction='I', status='H', text="Mauve")

            mock.return_value = MockResponse(200, "{}")
            Flow.find_and_handle(sms)

            # should have one event created
            event = WebHookEvent.objects.get()

            self.assertEquals('C', event.status)
            self.assertEquals(1, event.try_count)
            self.assertFalse(event.next_attempt)

            result = WebHookResult.objects.get()
            self.assertStringContains("successfully", result.message)
            self.assertEquals(200, result.status_code)

            self.assertTrue(mock.called)

            args = mock.call_args_list[0][0]
            prepared_request = args[0]
            self.assertStringContains(self.channel.org.get_webhook_url(), prepared_request.url)

            data = parse_qs(prepared_request.body)
            self.assertEquals(self.channel.pk, int(data['channel'][0]))
            self.assertEquals(actionset.uuid, data['step'][0])
            self.assertEquals(flow.pk, int(data['flow'][0]))

            values = json.loads(data['values'][0])

            self.assertEquals('Other', values[0]['category']['base'])
            self.assertEquals('color', values[0]['label'])
            self.assertEquals('Mauve', values[0]['text'])
            self.assertTrue(values[0]['time'])
            self.assertTrue(data['time'])

    def test_event_deliveries(self):
        sms = self.create_msg(contact=self.joe, direction='I', status='H', text="I'm gonna pop some tags")

        with patch('requests.Session.send') as mock:
            now = timezone.now()
            mock.return_value = MockResponse(200, "Hello World")

            # trigger an event, shouldnn't fire as we don't have a webhook
            WebHookEvent.trigger_sms_event(SMS_RECEIVED, sms, now)
            self.assertFalse(WebHookEvent.objects.all())

        self.setupChannel()

        with patch('requests.Session.send') as mock:
            # clear out which events we listen for, we still shouldnt be notified though we have a webhook
            self.channel.org.webhook_events = 0
            self.channel.org.save()

            now = timezone.now()
            mock.return_value = MockResponse(200, "Hello World")

            # trigger an event, shouldnn't fire as we don't have a webhook
            WebHookEvent.trigger_sms_event(SMS_RECEIVED, sms, now)
            self.assertFalse(WebHookEvent.objects.all())

        self.setupChannel()

        with patch('requests.Session.send') as mock:
            # remove all the org users
            self.org.administrators.clear()
            self.org.editors.clear()
            self.org.viewers.clear()

            mock.return_value = MockResponse(200, "Hello World")

            # trigger an event
            WebHookEvent.trigger_sms_event(SMS_RECEIVED, sms, now)
            event = WebHookEvent.objects.get()

            self.assertEquals('F', event.status)
            self.assertEquals(0, event.try_count)
            self.assertFalse(event.next_attempt)

            result = WebHookResult.objects.get()
            self.assertStringContains("No active user", result.message)
            self.assertEquals(0, result.status_code)

            self.assertFalse(mock.called)

            # what if they send weird json back?
            WebHookEvent.objects.all().delete()
            WebHookResult.objects.all().delete()

        # add ad manager back in
        self.org.administrators.add(self.admin)
        self.admin.set_org(self.org)

        with patch('requests.Session.send') as mock:
            mock.return_value = MockResponse(200, "Hello World")

            # trigger an event
            WebHookEvent.trigger_sms_event(SMS_RECEIVED, sms, now)
            event = WebHookEvent.objects.get()

            self.assertEquals('C', event.status)
            self.assertEquals(1, event.try_count)
            self.assertFalse(event.next_attempt)

            result = WebHookResult.objects.get()
            self.assertStringContains("Event delivered successfully", result.message)
            self.assertStringContains("not JSON", result.message)
            self.assertEquals(200, result.status_code)

            self.assertTrue(mock.called)

            WebHookEvent.objects.all().delete()
            WebHookResult.objects.all().delete()

        with patch('requests.Session.send') as mock:
            # valid json, but not our format
            bad_json = '{ "thrift_shops": ["Goodwill", "Value Village"] }'
            mock.return_value = MockResponse(200, bad_json)

            next_retry_earliest = timezone.now() + timedelta(minutes=4)
            next_retry_latest = timezone.now() + timedelta(minutes=6)

            WebHookEvent.trigger_sms_event(SMS_RECEIVED, sms, now)
            event = WebHookEvent.objects.get()

            self.assertEquals('C', event.status)
            self.assertEquals(1, event.try_count)
            self.assertFalse(event.next_attempt)

            self.assertTrue(mock.called)

            result = WebHookResult.objects.get()
            self.assertStringContains("Event delivered successfully", result.message)
            self.assertStringContains("ignoring", result.message)
            self.assertEquals(200, result.status_code)
            self.assertEquals(bad_json, result.body)

            WebHookEvent.objects.all().delete()
            WebHookResult.objects.all().delete()

        with patch('requests.Session.send') as mock:
            mock.return_value = MockResponse(200, '{ "phone": "+250788123123", "text": "I am success" }')

            WebHookEvent.trigger_sms_event(SMS_RECEIVED, sms, now)
            event = WebHookEvent.objects.get()

            self.assertEquals('C', event.status)
            self.assertEquals(1, event.try_count)
            self.assertFalse(event.next_attempt)

            result = WebHookResult.objects.get()
            self.assertEquals(200, result.status_code)

            self.assertTrue(mock.called)

            broadcast = Broadcast.objects.get()
            contact = Contact.get_or_create(self.org, self.admin, name=None, urns=[(TEL_SCHEME, "+250788123123")],
                                            incoming_channel=self.channel)
            self.assertTrue("I am success", broadcast.text)
            self.assertTrue(contact, broadcast.contacts.all())

            self.assertTrue(mock.called)
            args = mock.call_args_list[0][0]
            prepared_request = args[0]
            self.assertEquals(self.org.get_webhook_url(), prepared_request.url)

            data = parse_qs(prepared_request.body)
            self.assertEquals(self.joe.get_urn(TEL_SCHEME).path, data['phone'][0])
            self.assertEquals(sms.pk, int(data['sms'][0]))
            self.assertEquals(self.channel.pk, int(data['channel'][0]))
            self.assertEquals(SMS_RECEIVED, data['event'][0])
            self.assertEquals("I'm gonna pop some tags", data['text'][0])
            self.assertTrue('time' in data)

            WebHookEvent.objects.all().delete()
            WebHookResult.objects.all().delete()

        with patch('requests.Session.send') as mock:
            mock.return_value = MockResponse(500, "I am error")

            next_attempt_earliest = timezone.now() + timedelta(minutes=4)
            next_attempt_latest = timezone.now() + timedelta(minutes=6)

            WebHookEvent.trigger_sms_event(SMS_RECEIVED, sms, now)
            event = WebHookEvent.objects.get()

            self.assertEquals('E', event.status)
            self.assertEquals(1, event.try_count)
            self.assertTrue(event.next_attempt)
            self.assertTrue(next_attempt_earliest < event.next_attempt and next_attempt_latest > event.next_attempt)

            result = WebHookResult.objects.get()
            self.assertStringContains("Error", result.message)
            self.assertEquals(500, result.status_code)
            self.assertEquals("I am error", result.body)

            # make sure things become failures after three retries
            event.try_count = 2
            event.deliver()
            event.save()

            self.assertTrue(mock.called)

            self.assertEquals('F', event.status)
            self.assertEquals(3, event.try_count)
            self.assertFalse(event.next_attempt)

            result = WebHookResult.objects.get()
            self.assertStringContains("Error", result.message)
            self.assertEquals(500, result.status_code)
            self.assertEquals("I am error", result.body)
            self.assertEquals("http://fake.com/webhook.php", result.url)
            self.assertTrue(result.data.find("pop+some+tags") > 0)

            # check out our api log
            response = self.client.get(reverse('api.log'))
            self.assertRedirect(response, reverse('users.user_login'))

            response = self.client.get(reverse('api.log_read', args=[event.pk]))
            self.assertRedirect(response, reverse('users.user_login'))

            WebHookEvent.objects.all().delete()
            WebHookResult.objects.all().delete()

        # add a webhook header to the org
        self.channel.org.webhook = u'{"url": "http://fake.com/webhook.php", "headers": {"X-My-Header": "foobar", "Authorization": "Authorization: Basic QWxhZGRpbjpvcGVuIHNlc2FtZQ=="}, "method": "POST"}'
        self.channel.org.save()

        # check that our webhook settings have saved
        self.assertEquals('http://fake.com/webhook.php', self.channel.org.get_webhook_url())
        self.assertDictEqual({'X-My-Header': 'foobar', 'Authorization': 'Authorization: Basic QWxhZGRpbjpvcGVuIHNlc2FtZQ=='}, self.channel.org.get_webhook_headers())

        with patch('requests.Session.send') as mock:
            mock.return_value = MockResponse(200, "Boom")
            WebHookEvent.trigger_sms_event(SMS_RECEIVED, sms, now)
            event = WebHookEvent.objects.get()

            result = WebHookResult.objects.get()
            # both headers should be in the json-encoded url string
            self.assertStringContains('X-My-Header: foobar', result.request)
            self.assertStringContains('Authorization: Authorization: Basic QWxhZGRpbjpvcGVuIHNlc2FtZQ==', result.request)

    def test_webhook(self):
        response = self.client.get(reverse('api.webhook'))
        self.assertEquals(200, response.status_code)
        self.assertContains(response, "Simulator")

        response = self.client.get(reverse('api.webhook_simulator'))
        self.assertEquals(200, response.status_code)
        self.assertContains(response, "Log in")

        self.login(self.admin)
        response = self.client.get(reverse('api.webhook_simulator'))
        self.assertEquals(200, response.status_code)
        self.assertNotContains(response, "Log in")

    def test_tunnel(self):
        response = self.client.post(reverse('api.webhook_tunnel'), dict())
        self.assertEquals(302, response.status_code)

        self.login(self.non_org_user)

        with patch('requests.post') as mock:
            mock.return_value = MockResponse(200, '{ "phone": "+250788123123", "text": "I am success" }')

            response = self.client.post(reverse('api.webhook_tunnel'),
                                        dict(url="http://webhook.url/", data="phone=250788383383&values=foo&bogus=2"))
            self.assertEquals(200, response.status_code)
            self.assertContains(response, "I am success")
            self.assertTrue('values' in mock.call_args[1]['data'])
            self.assertTrue('phone' in mock.call_args[1]['data'])
            self.assertFalse('bogus' in mock.call_args[1]['data'])

            response = self.client.post(reverse('api.webhook_tunnel'), dict())
            self.assertEquals(400, response.status_code)
            self.assertTrue(response.content.find("Must include") >= 0)
