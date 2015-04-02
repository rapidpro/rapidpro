# -*- coding: utf-8 -*-

from __future__ import unicode_literals

import calendar
import json
import time
import uuid

from datetime import timedelta
from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.urlresolvers import reverse
from django.db import connection
from django.utils import timezone
from django.utils.http import urlquote_plus
from djorm_hstore.models import register_hstore_handler
from mock import patch
from redis_cache import get_redis_connection
from rest_framework.authtoken.models import Token
from temba.campaigns.models import Campaign, CampaignEvent, MESSAGE_EVENT
from temba.contacts.models import Contact, ContactField, ContactGroup, ContactURN, TEL_SCHEME, TWITTER_SCHEME
from temba.orgs.models import Org, OrgFolder, ACCOUNT_SID, ACCOUNT_TOKEN, APPLICATION_SID, NEXMO_KEY, NEXMO_SECRET
from temba.orgs.models import ALL_EVENTS, NEXMO_UUID
from temba.channels.models import Channel, SyncEvent, SEND_URL, SEND_METHOD, VUMI, KANNEL, NEXMO, TWILIO, SHAQODOON
from temba.channels.models import PLIVO, PLIVO_AUTH_ID, PLIVO_AUTH_TOKEN, PLIVO_APP_ID
from temba.channels.models import API_ID, USERNAME, PASSWORD, CLICKATELL
from temba.flows.models import Flow, FlowLabel, FlowRun, RuleSet
from temba.msgs.models import Broadcast, Call, Msg, WIRED, FAILED, SENT, DELIVERED, ERRORED, INCOMING, CALL_IN_MISSED
from temba.msgs.models import MSG_SENT_KEY, Label
from temba.tests import MockResponse, TembaTest, AnonymousOrg
from temba.triggers.models import Trigger, FOLLOW_TRIGGER
from temba.utils import dict_to_struct, datetime_to_json_date
from temba.values.models import Value
from twilio.util import RequestValidator
from twython import TwythonError
from urllib import urlencode
from .models import WebHookEvent, WebHookResult, SMS_RECEIVED


class APITest(TembaTest):

    def setUp(self):
        super(APITest, self).setUp()
        register_hstore_handler(connection)

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

    def fetchHTML(self, url):
        response = self.client.get(url, HTTP_X_FORWARDED_HTTPS='https')
        return response

    def postHTML(self, url, post_data):
        response = self.client.post(url, post_data, HTTP_X_FORWARDED_HTTPS='https')
        return response

    def fetchJSON(self, url, query=None):
        url = url + ".json"
        if query:
            url = url + "?" + query

        response = self.client.get(url, content_type="application/json", HTTP_X_FORWARDED_HTTPS='https')
        self.assertEquals(200, response.status_code)

        # this will fail if our response isn't valid json
        response.json = json.loads(response.content)
        return response

    def postJSON(self, url, data):
        response = self.client.post(url + ".json", json.dumps(data), content_type="application/json", HTTP_X_FORWARDED_HTTPS='https')
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
        self.assertIn(message, response.json[field])

    def assert403(self, url):
        response = self.fetchHTML(url)
        self.assertEquals(403, response.status_code)

    def test_api_explorer(self):
        url = reverse('api.explorer')
        response = self.fetchHTML(url)
        self.assertEquals(200, response.status_code)
        self.assertContains(response, "Log in to use the Explorer")

        # log in as plain user
        self.login(self.user)
        response = self.fetchHTML(url)
        self.assertEquals(200, response.status_code)
        self.assertContains(response, "Log in to use the Explorer")

        # log in a manager
        self.login(self.admin)
        response = self.fetchHTML(url)
        self.assertEquals(200, response.status_code)
        self.assertNotContains(response, "Log in to use the Explorer")

    def test_api_root(self):
        url = reverse('api')
        response = self.fetchHTML(url)
        content = response.content

        # log in as plain user
        self.login(self.user)
        response = self.fetchHTML(url)
        self.assertEquals(200, response.status_code)

        # log in a manager
        self.login(self.admin)
        response = self.fetchHTML(url)
        self.assertEquals(200, response.status_code)

    def test_api_flows(self):
        url = reverse('api.flows')

        # can't access, get 403
        self.assert403(url)

        # log in as plain user
        self.login(self.user)
        self.assert403(url)

        # log in a manager
        self.login(self.admin)

        # test that this user has a token
        self.assertTrue(self.admin.api_token)

        # blow it away
        Token.objects.all().delete()

        # should create one lazily
        self.assertTrue(self.admin.api_token)

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
                                                                          label='color')],
                                                           participants=0,
                                                           created_on=datetime_to_json_date(flow.created_on),
                                                           archived=False))

        response = self.fetchJSON(url)
        self.assertResultCount(response, 1)
        self.assertJSON(response, 'name', "Color Flow")

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

    def test_api_flow_update(self):
        url = reverse('api.flows')
        self.login(self.admin)

        # can't create a flow without a name
        response = self.postJSON(url, dict(name="", flow_type='F'))
        self.assertEqual(response.status_code, 400)

        # or without a type
        response = self.postJSON(url, dict(name="Hello World", flow_type=''))
        self.assertEqual(response.status_code, 400)

        # or invalid type
        response = self.postJSON(url, dict(name="Hello World", flow_type='X'))
        self.assertEqual(response.status_code, 400)

        # but we can create an empty flow
        response = self.postJSON(url, dict(name="Empty", flow_type='F'))
        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.json['name'], "Empty")

        # load flow definition from test data
        handle = open('%s/test_flows/pick_a_number.json' % settings.MEDIA_ROOT, 'r+')
        definition = json.loads(handle.read())
        handle.close()

        # and create flow with a definition
        response = self.postJSON(url, dict(name="Pick a number", flow_type='F', definition=definition))
        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.json['name'], "Pick a number")

        # make sure our flow is there as expected
        flow = Flow.objects.get(name='Pick a number')
        self.assertEqual(flow.flow_type, 'F')
        self.assertEqual(flow.action_sets.count(), 2)
        self.assertEqual(flow.rule_sets.count(), 1)

        # make local change
        flow.name = 'Something else'
        flow.flow_type = 'V'
        flow.save()

        # updating should overwrite local change
        response = self.postJSON(url, dict(uuid=flow.uuid, name="Pick a number", flow_type='F', definition=definition))
        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.json['name'], "Pick a number")

        # make sure our flow is there as expected
        flow = Flow.objects.get(name='Pick a number')
        self.assertEqual(flow.flow_type, 'F')

    def test_api_runs(self):
        url = reverse('api.runs')

        # can't access, get 403
        self.assert403(url)

        # log in as plain user
        self.login(self.user)
        self.assert403(url)

        # log in a manager
        self.login(self.admin)

        # create our test flow and a copy
        flow = self.create_flow()
        flow_copy = Flow.copy(flow, self.admin)

        # can't start with an invalid phone number
        response = self.postHTML(url, dict(flow=flow.pk, phone="asdf"))
        self.assertEquals(400, response.status_code)

        # can't start with invalid extra
        response = self.postHTML(url, dict(flow=flow.pk, phone="+250788123123", extra=dict(asdf=dict(asdf="asdf"))))
        self.assertEquals(400, response.status_code)

        # can't start without a flow
        response = self.postHTML(url, dict(phone="+250788123123"))
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

        # now fetch them instead...
        response = self.fetchJSON(url)
        self.assertEquals(200, response.status_code)
        self.assertResultCount(response, 9)
        self.assertContains(response, "+250788123124")
        self.assertContains(response, "+250788123123")

        # filter by id
        response = self.fetchJSON(url, "run=%d" % run.pk)
        self.assertEquals(200, response.status_code)
        self.assertResultCount(response, 1)
        fetched = json.loads(response.content)['results'][0]
        self.assertEqual(fetched['run'], run.pk)
        self.assertEqual(fetched['flow_uuid'], flow.uuid)
        self.assertEqual(fetched['contact'], self.joe.uuid)
        self.assertEqual(fetched['completed'], False)

        # filter by flow id (deprecated)
        response = self.fetchJSON(url, "flow=%d" % flow.pk)
        self.assertEquals(200, response.status_code)
        self.assertResultCount(response, 8)
        self.assertContains(response, "+250788123123")

        # filter by flow UUID
        response = self.fetchJSON(url, "flow_uuid=%s" % flow.uuid)
        self.assertEquals(200, response.status_code)
        self.assertResultCount(response, 8)
        self.assertContains(response, "+250788123123")

        # filter by phone (deprecated)
        response = self.fetchJSON(url, "phone=%2B250788123123")
        self.assertEquals(200, response.status_code)
        self.assertContains(response, "+250788123123")
        self.assertNotContains(response, "+250788123124")

        # filter by contact UUID
        response = self.fetchJSON(url, "contact=" + contact.uuid)
        self.assertEquals(200, response.status_code)
        self.assertContains(response, "+250788123124")
        self.assertNotContains(response, "+250788123123")

        # filter by group
        response = self.fetchJSON(url, "group=Players")
        self.assertEquals(200, response.status_code)
        self.assertNotContains(response, "+250788123124")
        self.assertNotContains(response, "+250788123123")

        players = self.create_group('Players', [])

        response = self.fetchJSON(url, "group=Players")
        self.assertEquals(200, response.status_code)
        self.assertNotContains(response, "+250788123124")
        self.assertNotContains(response, "+250788123123")

        response = self.fetchJSON(url, "group_uuids=%s" % players.uuid)
        self.assertEquals(200, response.status_code)
        self.assertNotContains(response, "+250788123124")
        self.assertNotContains(response, "+250788123123")

        players.contacts.add(contact)

        response = self.fetchJSON(url, "group=Players")
        self.assertEquals(200, response.status_code)
        self.assertContains(response, "+250788123124")
        self.assertNotContains(response, "+250788123123")

        response = self.fetchJSON(url, "group_uuids=%s" % players.uuid)
        self.assertEquals(200, response.status_code)
        self.assertContains(response, "+250788123124")
        self.assertNotContains(response, "+250788123123")

        # invalid dates
        response = self.fetchJSON(url, "before=01-01T00:00:00.000&after=01-01T00:00:00.000&channel=1,2")
        self.assertEquals(200, response.status_code)
        self.assertNotContains(response, "+250788123123")

    def test_api_channels(self):
        url = reverse('api.channels')

        # can't access, get 403
        self.assert403(url)

        # log in as plain user
        self.login(self.user)
        self.assert403(url)

        # log in a manager
        self.login(self.admin)

        # test that this user has a token
        self.assertTrue(self.admin.api_token)

        # blow it away
        Token.objects.all().delete()

        # should create one lazily
        self.assertTrue(self.admin.api_token)

        # this time, a 200
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
        response = self.postHTML(url, dict(claim_code="123123123", name="Claimed Channel"))
        self.assertEquals(400, response.status_code)

        # can't claim with an invalid phone number
        response = self.postHTML(url, dict(claim_code="123123123", name="Claimed Channel", phone="asdf"))
        self.assertEquals(400, response.status_code)

        # can't claim with an empty phone number
        response = self.postHTML(url, dict(claim_code="123123123", name="Claimed Channel", phone=""))
        self.assertEquals(400, response.status_code)

        # can't claim with an empty phone number
        response = self.postHTML(url, dict(claim_code="123123123", name="Claimed Channel", phone="9999999999"))
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
        response = self.postHTML(url, dict(claim_code="123123123", name="Claimed Channel", phone="250788123123"))
        self.assertEquals(400, response.status_code)

        # try with an empty claim code
        response = self.postHTML(url, dict(claim_code="  ", name="Claimed Channel"))
        self.assertEquals(400, response.status_code)

        # try without a claim code
        response = self.postHTML(url, dict(name="Claimed Channel"))
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
            twitter = Channel.objects.create(org=self.org, channel_type='TT', created_by=self.user, modified_by=self.user)
            response = self.deleteJSON(url, "id=%d" % twitter.pk)
            self.assertEquals(204, response.status_code)

    def test_api_calls(self):
        url = reverse('api.calls')

        # 403 if not logged in
        self.assert403(url)

        # log in
        self.login(self.user)
        self.assert403(url)

        # manager
        self.login(self.admin)

        # 200 this time
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

        # log in
        self.login(self.user)
        self.assert403(url)

        # manager
        self.login(self.admin)

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

        # try to update the language, which should fail as there are no languages on this org yet
        response = self.postJSON(url, dict(name='Snoop Dog', urns=['tel:+250788123456'], language='eng'))
        self.assertEquals(400, response.status_code)
        self.assertResponseError(response, 'language', "You do not have any languages configured for your organization.")

        # let's configure English on their org
        self.org.languages.create(iso_code='eng', name="English", created_by=self.admin, modified_by=self.admin)

        # try another language than one that is configured
        response = self.postJSON(url, dict(name='Snoop Dog', urns=['tel:+250788123456'], language='fre'))
        self.assertEquals(400, response.status_code)
        self.assertResponseError(response, 'language', "Language code 'fre' is not one of supported for organization. (eng)")

        # ok, now try english
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

        # try to post a invalid group name (deprecated)
        response = self.postJSON(url, dict(phone='+250788123456', groups=["  "]))
        self.assertResponseError(response, 'groups', "Invalid group name: '  '")

        # add contact to a new group by name (deprecated)
        response = self.postJSON(url, dict(phone='+250788123456', groups=["Music Artists"]))
        artists = ContactGroup.objects.get(name="Music Artists")
        self.assertEquals(201, response.status_code)
        self.assertEquals("Music Artists", artists.name)
        self.assertEqual(1, artists.contacts.count())
        self.assertEqual(1, artists.get_member_count())  # check cached value

        # remove contact from a group by name (deprecated)
        response = self.postJSON(url, dict(phone='+250788123456', groups=[]))
        artists = ContactGroup.objects.get(name="Music Artists")
        self.assertEquals(201, response.status_code)
        self.assertEqual(0, artists.contacts.count())
        self.assertEqual(0, artists.get_member_count())

        # add contact to a existing group by UUID
        response = self.postJSON(url, dict(phone='+250788123456', group_uuids=[artists.uuid]))
        artists = ContactGroup.objects.get(name="Music Artists")
        self.assertEquals(201, response.status_code)
        self.assertEquals("Music Artists", artists.name)
        self.assertEqual(1, artists.contacts.count())
        self.assertEqual(1, artists.get_member_count())

        # specifying both groups and group_uuids should return error
        response = self.postJSON(url, dict(phone='+250788123456', groups=[artists.name], group_uuids=[artists.uuid]))
        self.assertEquals(400, response.status_code)

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

        # fetch all with blank query
        response = self.fetchJSON(url, "")
        self.assertEquals(200, response.status_code)
        self.assertResultCount(response, 2)
        self.assertContains(response, "Dr Dre")
        self.assertContains(response, 'tel:+250788123456')
        self.assertContains(response, 'Andre')
        self.assertContains(response, "Jay-Z")
        self.assertContains(response, '123555')

        # search using deprecated phone field
        response = self.fetchJSON(url, "phone=%2B250788123456")
        self.assertResultCount(response, 1)
        self.assertContains(response, "Dr Dre")

        # non-matching phone number
        response = self.fetchJSON(url, "phone=%2B250788123000")
        self.assertResultCount(response, 0)

        # search using urns field
        response = self.fetchJSON(url, 'urns=' + urlquote_plus("tel:+250788123456"))
        self.assertResultCount(response, 1)
        self.assertContains(response, "Dr Dre")

        # search using urns list
        response = self.fetchJSON(url, 'urns=%s&urns=%s' % (urlquote_plus("tel:+250788123456"), urlquote_plus("tel:123555")))
        self.assertResultCount(response, 2)

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

        # check deleting a contact by UUID
        response = self.deleteJSON(url, 'uuid=' + drdre.uuid)
        self.assertEqual(response.status_code, 204)
        self.assertFalse(Contact.objects.get(pk=drdre.pk).is_active)

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

    def test_api_fields(self):
        url = reverse('api.contactfields')

        # 403 if not logged in
        self.assert403(url)

        # log in
        self.login(self.user)
        self.assert403(url)

        # manager
        self.login(self.admin)

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

        # update no-existent key
        response = self.postJSON(url, dict(key='real_age_2', label='Actual Age 2', value_type='N'))
        self.assertEquals(400, response.status_code)
        self.assertResponseError(response, 'key', "No such contact field key")

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

        response = self.postJSON(url, dict(label='Name', value_type='T'))
        self.assertEquals(400, response.status_code)
        self.assertResponseError(response, 'non_field_errors', "key for Name is a reserved name for contact fields")

    def test_api_messages(self):
        url = reverse('api.messages')

        # 403 if not logged in
        self.assert403(url)

        # log in
        self.login(self.user)
        self.assert403(url)

        # manager
        self.login(self.admin)

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

        sms = Msg.objects.get()
        self.assertEquals("test1", sms.text)
        self.assertEquals("+250788123123", sms.contact.get_urn(TEL_SCHEME).path)
        self.assertEquals(self.admin.get_org(), sms.org)
        self.assertEquals(self.channel, sms.channel)
        self.assertEquals(broadcast, sms.broadcast)
        
        # fetch by message id
        response = self.fetchJSON(url, "id=%d" % sms.pk)
        self.assertResultCount(response, 1)
        self.assertContains(response, "test1")

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

        # search by label
        response = self.fetchJSON(url, "label=Goo")
        self.assertEquals(200, response.status_code)
        self.assertResultCount(response, 0)

        label = Label.create_unique(self.org, self.user, "Goo")
        label.toggle_label([sms], add=True)

        response = self.fetchJSON(url, "label=Goo")
        self.assertEquals(200, response.status_code)
        self.assertResultCount(response, 1)

        response = self.fetchJSON(url, "status=Q&before=01T00:00:00.000&after=01-01T00:00:00.000&urn=%2B250788123123&channel=-1")
        self.assertEquals(200, response.status_code)
        self.assertNotContains(response, "test1")

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

        response = self.fetchJSON(url, "group=Players")
        self.assertEquals(200, response.status_code)
        self.assertResultCount(response, 1)

        response = self.fetchJSON(url, "group_uuids=%s" % players.uuid)
        self.assertEquals(200, response.status_code)
        self.assertResultCount(response, 1)

        # associate one of our messages with a flow run
        flow = self.create_flow()
        flow.start([], [contact])

        response = self.fetchJSON(url, "type=F")
        self.assertEquals(200, response.status_code)
        self.assertResultCount(response, 1)

        response = self.fetchJSON(url, "flow=%d" % flow.id)
        self.assertEquals(200, response.status_code)
        self.assertResultCount(response, 1)

        response = self.fetchJSON(url, "flow=99999")
        self.assertEquals(200, response.status_code)
        self.assertResultCount(response, 0)

        # search by broadcast id
        response = self.fetchJSON(url, "broadcast=%d" % broadcast.pk)
        self.assertEquals(200, response.status_code)
        self.assertResultCount(response, 1)

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
        self.assertJSON(response, 'phone', '+250788123123')
        self.assertJSON(response, 'urn', 'tel:+250788123123')
        self.assertJSON(response, 'phone', '+250788123124')
        self.assertJSON(response, 'urn', 'tel:+250788123124')

        label1 = Label.create_unique(self.org, self.user, "Goo")
        label1.toggle_label([msgs[0]], add=True)

        label2 = Label.create_unique(self.org, self.user, "Fiber")
        label2.toggle_label([msgs[1]], add=True)

        response = self.fetchJSON(url, "label=Goo&label=Fiber")
        self.assertResultCount(response, 2)

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

    def test_api_labels(self):
        url = reverse('api.labels')
        self.login(self.admin)

        # add a top-level labels
        response = self.postJSON(url, dict(name='Screened'))
        self.assertEquals(201, response.status_code)

        screened = Label.objects.get(name='Screened')
        self.assertIsNone(screened.parent)

        # can't create another with same name and parent
        response = self.postJSON(url, dict(name='Screened'))
        self.assertEquals(400, response.status_code)

        # add another with a different name
        response = self.postJSON(url, dict(name='Junk'))
        self.assertEquals(201, response.status_code)

        junk = Label.objects.get(name='Junk')
        self.assertIsNone(junk.parent)

        # add a sub-label
        response = self.postJSON(url, dict(name='Flagged', parent=screened.uuid))
        self.assertEquals(201, response.status_code)

        flagged = Label.objects.get(name='Flagged')
        self.assertEqual(flagged.parent, screened)

        # update changing name and setting parent to null
        response = self.postJSON(url, dict(uuid=flagged.uuid, name='Spam', parent=None))
        self.assertEquals(201, response.status_code)

        flagged = Label.objects.get(uuid=flagged.uuid)
        self.assertEqual(flagged.name, 'Spam')
        self.assertIsNone(flagged.parent)

        # update parent to another label
        response = self.postJSON(url, dict(uuid=flagged.uuid, name='Spam', parent=junk.uuid))
        self.assertEquals(201, response.status_code)

        flagged = Label.objects.get(uuid=flagged.uuid)
        self.assertEqual(flagged.name, 'Spam')
        self.assertEqual(flagged.parent, junk)

        # can't update name to something already used
        response = self.postJSON(url, dict(uuid=flagged.uuid, name='Screened'))
        self.assertEquals(400, response.status_code)

        # can't create a label with a parent that has a parent
        response = self.postJSON(url, dict(name='Interesting', parent=flagged.uuid))
        self.assertEquals(400, response.status_code)

        # now fetch all labels
        response = self.fetchJSON(url)
        self.assertResultCount(response, 3)

        # fetch by name
        response = self.fetchJSON(url, 'name=Screened')
        self.assertResultCount(response, 1)
        self.assertContains(response, "Screened")

        # fetch by uuid
        response = self.fetchJSON(url, 'uuid=%s' % screened.uuid)
        self.assertResultCount(response, 1)
        self.assertContains(response, "Screened")

        # fetch by parent
        response = self.fetchJSON(url, 'parent=%s' % junk.uuid)
        self.assertResultCount(response, 1)
        self.assertContains(response, "Spam")

    def test_api_broadcasts(self):
        url = reverse('api.broadcasts')
        self.login(self.admin)

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

        twitter = Channel.objects.create(org=self.org, name="Twitter", address="nyaruka", channel_type='TT',
                                         created_by=self.admin, modified_by=self.admin)

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

        # log in as plain user
        self.login(self.user)
        self.assert403(url)

        # log in a manager
        self.login(self.admin)

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
        response = self.postHTML(url, dict(name="MAMA Messages"))
        self.assertEquals(400, response.status_code)

        # can't create a campaign without a name
        response = self.postHTML(url, dict(group="Expecting Mothers"))
        self.assertEquals(400, response.status_code)

        # works with both
        response = self.postHTML(url, dict(name="MAMA Messages", group="Expecting Mothers"))
        self.assertEquals(201, response.status_code)

        # should have a campaign now
        self.assertEquals(1, Campaign.objects.all().count())
        campaign = Campaign.objects.get()
        self.assertEquals(self.org, campaign.org)
        self.assertEquals("MAMA Messages", campaign.name)
        self.assertEquals("Expecting Mothers", campaign.group.name)
        self.assertEquals(self.org, campaign.group.org)

        # try updating the campaign
        response = self.postHTML(url, dict(campaign=campaign.pk, name="Preggie Messages", group="Expecting Mothers"))
        self.assertEquals(201, response.status_code)

        campaign = Campaign.objects.get()
        self.assertEquals("Preggie Messages", campaign.name)

        # doesn't work with an invalid id
        response = self.postHTML(url, dict(campaign=999, name="Preggie Messages", group="Expecting Mothers"))
        self.assertEquals(400, response.status_code)

        # try to fetch them
        response = self.fetchJSON(url)
        self.assertResultCount(response, 1)

        self.assertJSON(response, 'campaign', campaign.pk)
        self.assertJSON(response, 'name', "Preggie Messages")

        url = reverse('api.campaignevents')

        # no events to start with
        response = self.fetchJSON(url)
        self.assertResultCount(response, 0)

        event_args = dict(campaign=campaign.pk, unit='W', offset=5, relative_to="EDD",
                          delivery_hour=-1, message="Time to go to the clinic")

        response = self.postHTML(url, event_args)
        self.assertEquals(201, response.status_code)

        event = CampaignEvent.objects.get()
        self.assertEquals(MESSAGE_EVENT, event.event_type)
        self.assertEquals(campaign, event.campaign)
        self.assertEquals(5, event.offset)
        self.assertEquals('W', event.unit)
        self.assertEquals("EDD", event.relative_to.label)
        self.assertEquals(-1, event.delivery_hour)
        self.assertEquals("Time to go to the clinic", event.message)

        # fetch our campaign events
        response = self.fetchJSON(url)

        self.assertResultCount(response, 1)
        self.assertJSON(response, "message", "Time to go to the clinic")

        # delete that event
        response = self.deleteJSON(url, "event=%d" % event.pk)
        self.assertEquals(204, response.status_code)

        # check that we've been deleted
        self.assertEquals(0, CampaignEvent.objects.filter(is_active=True).count())

        # but we are still around
        self.assertEquals(1, CampaignEvent.objects.all().count())

        # deleting again is a 404
        response = self.deleteJSON(url, "event=%d" % event.pk)
        self.assertEquals(404, response.status_code)

    def test_api_groups(self):
        url = reverse('api.contactgroups')

        # 403 if not logged in
        self.assert403(url)

        # log in
        self.login(self.user)
        self.assert403(url)

        # manager
        self.login(self.admin)

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

        data = {'from':"", 'text':"Hi there"}
        response = self.client.post(callback_url, data)

        self.assertEquals(400, response.status_code)

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
                mock.return_value = MockResponse(400, "Error")

                # manually send it off
                Channel.send_message(dict_to_struct('MsgStruct', sms.as_task_json()))

                # message should be marked as an error
                msg = bcast.get_messages()[0]
                self.assertEquals(ERRORED, msg.status)
                self.assertEquals(1, msg.error_count)
                self.assertTrue(msg.next_attempt)
                self.assertEquals(1, mock.call_count)
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
        self.assertEquals(14, msg.delivered_on.hour)

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
        finally:
            settings.SEND_MESSAGES = False


class ClickatellTest(TembaTest):

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
        self.assertEquals('id1234', msg1.external_id)

        data = {'apiMsgId': 'id1234', 'status': '001'}
        encoded_message = urlencode(data)

        callback_url = reverse('api.clickatell_handler', args=['status', self.channel.uuid]) + "?" + encoded_message
        response = self.client.get(callback_url)

        self.assertEquals(200, response.status_code)

        # load our message
        sms = Msg.objects.all().order_by('-pk').first()

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
        bcast = joe.send("Test message", self.admin, trigger_send=False)

        # our outgoing message
        msg = bcast.get_messages()[0]

        try:
            settings.SEND_MESSAGES = True

            with patch('twython.Twython.send_direct_message') as mock:
                mock.return_value = dict(id=1234567890)

                # manually send it off
                Channel.send_message(dict_to_struct('MsgStruct', msg.as_task_json()))

                # check the status of the message is now sent
                msg = bcast.get_messages()[0]
                self.assertEquals(WIRED, msg.status)
                self.assertEquals('1234567890', msg.external_id)
                self.assertTrue(msg.sent_on)

                self.clear_cache()

            with patch('twython.Twython.send_direct_message') as mock:
                mock.side_effect = TwythonError("Failed to send message")

                # manually send it off
                Channel.send_message(dict_to_struct('MsgStruct', msg.as_task_json()))

                # message should be marked as an error
                msg = bcast.get_messages()[0]
                self.assertEquals(ERRORED, msg.status)
                self.assertEquals(1, msg.error_count)
                self.assertTrue(msg.next_attempt)
        finally:
            settings.SEND_MESSAGES = False


class MageHandlerTest(TembaTest):

    def setUp(self):
        super(MageHandlerTest, self).setUp()

        self.org.webhook = "http://fake.com/webhook.php"
        self.org.webhook_events = ALL_EVENTS
        self.org.save()

        self.joe = self.create_contact("Joe", number="+250788383383")

        self.dyn_group = ContactGroup.create(self.org, self.user, "Bobs", query="name has Bob")

    def create_contact_like_mage(self, name, twitter):
        """
        Creates a contact as if it were created in Mage, i.e. no event/group triggering or cache updating
        """
        contact = Contact.objects.create(org=self.org, name=name, is_active=True, is_archived=False,
                                         uuid=uuid.uuid4(), status='N',
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

        self.assertEqual(0, self.org.get_folder_count(OrgFolder.msgs_inbox))
        self.assertEqual(1, self.org.get_folder_count(OrgFolder.contacts_all))
        self.assertEqual(1000, self.org.get_credits_remaining())

        msg = self.create_message_like_mage(text="Hello 1", contact=self.joe)

        self.assertEqual(0, self.org.get_folder_count(OrgFolder.msgs_inbox))
        self.assertEqual(1, self.org.get_folder_count(OrgFolder.contacts_all))
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

        self.assertEqual(1, self.org.get_folder_count(OrgFolder.msgs_inbox))
        self.assertEqual(1, self.org.get_folder_count(OrgFolder.contacts_all))
        self.assertEqual(999, self.org.get_credits_remaining())

        # check that a message that has a topup, doesn't decrement twice
        msg = self.create_message_like_mage(text="Hello 2", contact=self.joe)
        msg.topup_id = self.org.decrement_credit()
        msg.save()

        self.client.post(url, dict(message_id=msg.pk, new_contact=False), **headers)
        self.assertEqual(2, self.org.get_folder_count(OrgFolder.msgs_inbox))
        self.assertEqual(1, self.org.get_folder_count(OrgFolder.contacts_all))
        self.assertEqual(998, self.org.get_credits_remaining())

        # simulate scenario where Mage has added new contact with name that should put it into a dynamic group
        mage_contact, mage_contact_urn = self.create_contact_like_mage("Bob", "bobby81")
        msg = self.create_message_like_mage(text="Hello via Mage", contact=mage_contact, contact_urn=mage_contact_urn)

        response = self.client.post(url, dict(message_id=msg.pk, new_contact=True), **headers)
        self.assertEqual(200, response.status_code)

        msg = Msg.objects.get(pk=msg.pk)
        self.assertEqual('H', msg.status)
        self.assertEqual(self.welcome_topup, msg.topup)

        self.assertEqual(3, self.org.get_folder_count(OrgFolder.msgs_inbox))
        self.assertEqual(2, self.org.get_folder_count(OrgFolder.contacts_all))
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

        channel = Channel.objects.create(name="Twitter Channel", org=self.org,
                                         channel_type="TT", address="billy_bob", role="SR",
                                         secret="78901",
                                         created_by=self.user, modified_by=self.user)

        Trigger.objects.create(created_by=self.user, modified_by=self.user, org=self.org, trigger_type=FOLLOW_TRIGGER,
                               flow=flow, channel=channel)

        contact = self.create_contact("Mary Jo", twitter='mary_jo')
        urn = contact.get_urn(TWITTER_SCHEME)

        response = self.client.post(url, dict(channel_id=channel.id, contact_urn_id=urn.id), **headers)
        self.assertEqual(200, response.status_code)
        self.assertEqual(1, flow.runs.all().count())
        self.assertEqual(self.org.get_folder_count(OrgFolder.contacts_all), 2)

        # simulate scenario where Mage has added new contact with name that should put it into a dynamic group
        mage_contact, mage_contact_urn = self.create_contact_like_mage("Bob", "bobby81")

        response = self.client.post(url, dict(channel_id=channel.id,
                                              contact_urn_id=mage_contact_urn.id, new_contact=True), **headers)
        self.assertEqual(200, response.status_code)
        self.assertEqual(2, flow.runs.all().count())

        # check that contact ended up dynamic group
        self.assertEqual([mage_contact], list(self.dyn_group.contacts.order_by('name')))

        # check cached contact count updated
        self.assertEqual(self.org.get_folder_count(OrgFolder.contacts_all), 3)


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
        org.webhook = "http://fake.com/webhook.php"
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
                                   call_type=CALL_IN_MISSED,
                                   time=now,
                                   created_by=self.admin,
                                   modified_by=self.admin)

        self.setupChannel()

        with patch('requests.post') as mock:
            # clear out which events we listen for, we still shouldnt be notified though we have a webhook
            self.channel.org.webhook_events = 0
            self.channel.org.save()

            mock.return_value = MockResponse(200, "Hello World")

            # trigger an event, shouldnn't fire as we don't have a webhook
            WebHookEvent.trigger_call_event(call)
            self.assertFalse(WebHookEvent.objects.all())

        self.setupChannel()

        with patch('requests.post') as mock:
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
            kwargs = mock.call_args_list[0][1]
            self.assertEquals(self.channel.org.webhook, args[0])

            data = kwargs['data']
            self.assertEquals('+250788123123', data['phone'])
            self.assertEquals(call.pk, data['call'])
            self.assertEquals(0, data['duration'])
            self.assertEquals(call.call_type, data['event'])
            self.assertTrue('time' in data)
            self.assertEquals(self.channel.pk, data['channel'])

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

        with patch('requests.post') as mock:
            # clear out which events we listen for, we still shouldnt be notified though we have a webhook
            self.channel.org.webhook_events = 0
            self.channel.org.save()

            mock.return_value = MockResponse(200, "Hello World")

            # trigger an event, shouldnn't fire as we don't have a webhook
            WebHookEvent.trigger_channel_alarm(sync_event)
            self.assertFalse(WebHookEvent.objects.all())

        self.setupChannel()

        with patch('requests.post') as mock:
            now = timezone.now()
            mock.return_value = MockResponse(200, "")

            # trigger an event
            WebHookEvent.trigger_channel_alarm(sync_event)
            event = WebHookEvent.objects.get()

            self.assertEquals('C', event.status)
            self.assertEquals(1, event.try_count)
            self.assertFalse(event.next_attempt)

            result = WebHookResult.objects.get()
            self.assertStringContains("Event delivered successfully.", result.message)
            self.assertEquals(200, result.status_code)
            self.assertEquals("", result.body)

            self.assertTrue(mock.called)
            args = mock.call_args_list[0][0]
            kwargs = mock.call_args_list[0][1]
            self.assertEquals(self.channel.org.webhook, args[0])

            data = kwargs['data']
            self.assertEquals(self.channel.pk, data['channel'])
            self.assertEquals(85, data['power_level'])
            self.assertEquals('AC', data['power_source'])
            self.assertEquals('CHARGING', data['power_status'])
            self.assertEquals('WIFI', data['network_type'])
            self.assertEquals(5, data['pending_message_count'])
            self.assertEquals(4, data['retry_message_count'])

    def test_flow_event(self):
        self.setupChannel()

        org = self.channel.org
        org.save()

        from temba.flows.models import ActionSet, APIAction, Flow
        flow = self.create_flow()

        # replace our uuid of 4 with the right thing
        actionset = ActionSet.objects.get(x=4)
        actionset.set_actions_dict([APIAction(org.webhook).as_json()])
        actionset.save()

        with patch('requests.post') as mock:
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
            kwargs = mock.call_args_list[0][1]
            self.assertEquals(self.channel.org.webhook, args[0])

            data = kwargs['data']
            self.assertEquals(self.channel.pk, data['channel'])
            self.assertEquals(actionset.uuid, data['step'])
            self.assertEquals(flow.pk, data['flow'])

            values = json.loads(data['values'])

            self.assertEquals('Other', values[0]['category'])
            self.assertEquals('color', values[0]['label'])
            self.assertEquals('Mauve', values[0]['text'])
            self.assertTrue(values[0]['time'])
            self.assertTrue(data['time'])

    def test_event_deliveries(self):
        sms = self.create_msg(contact=self.joe, direction='I', status='H', text="I'm gonna pop some tags")

        with patch('requests.post') as mock:
            now = timezone.now()
            mock.return_value = MockResponse(200, "Hello World")

            # trigger an event, shouldnn't fire as we don't have a webhook
            WebHookEvent.trigger_sms_event(SMS_RECEIVED, sms, now)
            self.assertFalse(WebHookEvent.objects.all())

        self.setupChannel()

        with patch('requests.post') as mock:
            # clear out which events we listen for, we still shouldnt be notified though we have a webhook
            self.channel.org.webhook_events = 0
            self.channel.org.save()

            now = timezone.now()
            mock.return_value = MockResponse(200, "Hello World")

            # trigger an event, shouldnn't fire as we don't have a webhook
            WebHookEvent.trigger_sms_event(SMS_RECEIVED, sms, now)
            self.assertFalse(WebHookEvent.objects.all())

        self.setupChannel()

        with patch('requests.post') as mock:
            # remove all the org users
            self.org.administrators.remove(self.admin)
            self.org.administrators.remove(self.root)
            self.org.administrators.remove(self.user)

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

        with patch('requests.post') as mock:
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

        with patch('requests.post') as mock:
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

        with patch('requests.post') as mock:
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
            kwargs = mock.call_args_list[0][1]
            self.assertEquals(self.org.webhook, args[0])

            data = kwargs['data']
            self.assertEquals(self.joe.get_urn(TEL_SCHEME).path, data['phone'])
            self.assertEquals(sms.pk, data['sms'])
            self.assertEquals(self.channel.pk, data['channel'])
            self.assertEquals(SMS_RECEIVED, data['event'])
            self.assertEquals("I'm gonna pop some tags", data['text'])
            self.assertTrue('time' in data)

            WebHookEvent.objects.all().delete()
            WebHookResult.objects.all().delete()

        with patch('requests.post') as mock:
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

        self.login(self.non_org_manager)

        with patch('requests.post') as mock:
            mock.return_value = MockResponse(200, '{ "phone": "+250788123123", "text": "I am success" }')

            response = self.client.post(reverse('api.webhook_tunnel'), dict(url="http://webhook.url/", data="phone=250788383383"))
            self.assertEquals(200, response.status_code)
            self.assertContains(response, "I am success")

            response = self.client.post(reverse('api.webhook_tunnel'), dict())
            self.assertEquals(400, response.status_code)
            self.assertTrue(response.content.find("Must include") >= 0)

