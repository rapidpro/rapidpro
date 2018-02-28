# -*- coding: utf-8 -*-
from __future__ import absolute_import, division, print_function, unicode_literals

import json
import pytz
import six

from datetime import datetime, timedelta
from django.contrib.auth.models import Group
from django.contrib.gis.geos import GEOSGeometry
from django.core.urlresolvers import reverse
from django.db import connection
from django.utils import timezone
from django.utils.http import urlquote_plus
from mock import patch
from rest_framework.authtoken.models import Token
from rest_framework.exceptions import ValidationError
from rest_framework.test import APIClient
from temba.channels.models import Channel, ChannelEvent
from temba.contacts.models import Contact, ContactField, ContactGroup, TEL_SCHEME, TWITTER_SCHEME
from temba.flows.models import FlowLabel, FlowRun, RuleSet, ActionSet, Flow
from temba.locations.models import BoundaryAlias
from temba.msgs.models import Msg
from temba.orgs.models import Language
from temba.tests import TembaTest, AnonymousOrg
from temba.utils.dates import datetime_to_json_date
from temba.values.models import Value
from temba.api.models import APIToken
from uuid import uuid4
from .serializers import StringDictField, StringArrayField, PhoneArrayField, ChannelField, DateTimeField
from .serializers import MsgCreateSerializer


class APITest(TembaTest):

    def setUp(self):
        super(APITest, self).setUp()

        self.joe = self.create_contact("Joe Blow", "0788123123")

        self.channel2 = Channel.create(None, self.admin, 'RW', 'A', "Unclaimed Channel",
                                       claim_code="123123123", secret="123456", gcm_id="1234")

        self.call1 = ChannelEvent.objects.create(contact=self.joe,
                                                 channel=self.channel,
                                                 org=self.org,
                                                 event_type=ChannelEvent.TYPE_CALL_OUT_MISSED,
                                                 occurred_on=timezone.now())

        # this is needed to prevent REST framework from rolling back transaction created around each unit test
        connection.settings_dict['ATOMIC_REQUESTS'] = False

    def tearDown(self):
        super(APITest, self).tearDown()

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
        response.json()
        return response

    def postJSON(self, url, data):
        return self.client.post(url + ".json", json.dumps(data), content_type="application/json", HTTP_X_FORWARDED_HTTPS='https')

    def deleteJSON(self, url, query=None):
        url = url + ".json"
        if query:
            url = url + "?" + query

        return self.client.delete(url, content_type="application/json", HTTP_X_FORWARDED_HTTPS='https')

    def assertResultCount(self, response, count):
        self.assertEqual(count, response.json()['count'])

    def assertJSONArrayContains(self, response, key, value):
        if 'results' in response.json():
            for result in response.json()['results']:
                for v in result[key]:
                    if v == value:
                        return
        else:
            for v in response.json()[key]:
                if v == value:
                    return

        self.fail("Unable to find %s:%s in %s" % (key, value, response.json()))

    def assertJSON(self, response, key, value):
        if 'results' in response.json():
            for result in response.json()['results']:
                if result[key] == value:
                    return
        else:
            if response.json()[key] == value:
                return

        self.fail("Unable to find %s:%s in %s" % (key, value, response.json()))

    def assertNotJSON(self, response, key, value):
        if 'results' in response.json():
            for result in response.json()['results']:
                if result[key] == value:
                    self.fail("Found %s:%s in %s" % (key, value, response.json()))
        else:
            if response.json()[key] == value:
                self.fail("Found %s:%s in %s" % (key, value, response.json()))

        return

    def assertResponseError(self, response, field, message, status_code=400):
        self.assertEqual(status_code, response.status_code)

        body = response.json()
        self.assertTrue(message, field in body)
        self.assertTrue(message, isinstance(body[field], (list, tuple)))
        self.assertIn(message, body[field])

    def assert403(self, url):
        response = self.fetchHTML(url)
        self.assertEqual(403, response.status_code)

    def test_redirection(self):
        self.login(self.admin)

        # check the views which redirect
        self.assertRedirect(self.client.get('/api/v1/explorer/'), '/api/v2/explorer/', status_code=301)

        # check some removed endpoints
        expected_msg = "API v1 no longer exists. Please migrate to API v2. See http://testserver/api/v2/."
        self.assertContains(self.client.get('/api/v1/messages.json'), expected_msg, status_code=410)
        self.assertContains(self.client.get('/api/v1/runs.json'), expected_msg, status_code=410)

        # check docs at root
        self.assertContains(self.client.get('/api/v1/'), "API v1 has been replaced", status_code=405)

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

        self.assertEqual(phones_field.to_internal_value(['123', '234']), ['tel:123', 'tel:234'])
        self.assertEqual(phones_field.to_internal_value('123'), ['tel:123'])  # convert single string to array
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

    def test_api_authentication(self):
        url = reverse('api.v1.org') + '.json'

        # can't fetch endpoint with invalid token
        response = self.client.get(url, content_type="application/json",
                                   HTTP_X_FORWARDED_HTTPS='https', HTTP_AUTHORIZATION="Token 1234567890")
        self.assertEqual(response.status_code, 403)

        # can fetch endpoint with valid token
        response = self.client.get(url, content_type="application/json",
                                   HTTP_X_FORWARDED_HTTPS='https', HTTP_AUTHORIZATION="Token %s" % self.surveyor.api_token)
        self.assertEqual(response.status_code, 200)

        # but not if user is inactive
        self.surveyor.is_active = False
        self.surveyor.save()

        response = self.client.get(url, content_type="application/json",
                                   HTTP_X_FORWARDED_HTTPS='https', HTTP_AUTHORIZATION="Token %s" % self.surveyor.api_token)
        self.assertEqual(response.status_code, 403)

    def test_api_org(self):
        url = reverse('api.v1.org')

        # can't access, get 403
        self.assert403(url)

        # login as plain user
        self.login(self.user)
        self.assert403(url)

        # login as surveyor
        self.login(self.surveyor)

        # browse endpoint as HTML docs
        response = self.fetchHTML(url)
        self.assertEqual(response.status_code, 200)

        # fetch as JSON
        response = self.fetchJSON(url)
        self.assertEqual(200, response.status_code)
        self.assertEqual(response.json(), dict(name="Temba",
                                               country="RW",
                                               languages=[],
                                               primary_language=None,
                                               timezone="Africa/Kigali",
                                               date_style="day_first",
                                               anon=False))

        eng = Language.create(self.org, self.admin, "English", 'eng')
        Language.create(self.org, self.admin, "French", 'fra')
        self.org.primary_language = eng
        self.org.save()

        response = self.fetchJSON(url)
        self.assertEqual(200, response.status_code)
        self.assertEqual(response.json(), dict(name="Temba",
                                               country="RW",
                                               languages=["eng", "fra"],
                                               primary_language="eng",
                                               timezone="Africa/Kigali",
                                               date_style="day_first",
                                               anon=False))

    def test_api_boundaries(self):
        url = reverse('api.v1.boundaries')

        # 403 if not logged in
        self.assert403(url)

        # login as plain user
        self.login(self.user)
        self.assert403(url)

        # login as surveyor
        self.login(self.surveyor)

        # browse endpoint as HTML docs
        response = self.fetchHTML(url)
        self.assertEqual(response.status_code, 200)

        self.create_secondary_org()

        BoundaryAlias.create(self.org, self.admin, self.state1, "Kigali")
        BoundaryAlias.create(self.org, self.admin, self.state1, "Kigari")
        BoundaryAlias.create(self.org, self.admin, self.state2, "East Prov")
        BoundaryAlias.create(self.org2, self.admin2, self.state1, "Other Org")  # shouldn't be returned

        self.state1.simplified_geometry = GEOSGeometry('MULTIPOLYGON(((1 1, 1 -1, -1 -1, -1 1, 1 1)))')
        self.state1.save()

        # test with no params
        response = self.fetchJSON(url)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.json()['results']), 10)
        self.assertEqual(response.json()['results'][2], {
            'boundary': "1708283",
            'name': "Kigali City",
            'parent': "171496",
            'level': 1,
            'geometry': {
                'type': "MultiPolygon",
                'coordinates': [
                    [
                        [
                            [1.0, 1.0],
                            [1.0, -1.0],
                            [-1.0, -1.0],
                            [-1.0, 1.0],
                            [1.0, 1.0]
                        ]
                    ]
                ],
            },
        })

        # test with aliases instead of geometry
        response = self.fetchJSON(url, 'aliases=true')

        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.json()['results']), 10)
        self.assertEqual(response.json()['results'][2], {
            'boundary': "1708283",
            'name': "Kigali City",
            'parent': "171496",
            'level': 1,
            'aliases': ["Kigali", "Kigari"],
        })

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

        # login as surveyor
        self.login(self.surveyor)

        # test that this user has a token
        self.assertTrue(self.surveyor.api_token)

        # blow it away
        Token.objects.all().delete()

        # should create one lazily
        self.assertTrue(self.surveyor.api_token)

        # browse endpoint as HTML docs
        response = self.fetchHTML(url)
        self.assertEqual(response.status_code, 200)

        # create our test flow
        flow = self.get_flow('color')
        flow_ruleset1 = RuleSet.objects.get(flow=flow)

        # this time, a 200
        response = self.fetchJSON(url)
        self.assertEqual(200, response.status_code)

        # should contain our single flow in the response
        self.assertEqual(response.json()['results'][0], dict(flow=flow.pk,
                                                             uuid=flow.uuid,
                                                             name='Color Flow',
                                                             labels=[],
                                                             runs=0,
                                                             completed_runs=0,
                                                             participants=None,
                                                             rulesets=[dict(node=flow_ruleset1.uuid,
                                                                            id=flow_ruleset1.pk,
                                                                            response_type='C',
                                                                            ruleset_type='wait_message',
                                                                            label='color')],
                                                             created_on=datetime_to_json_date(flow.created_on),
                                                             expires=flow.expires_after_minutes,
                                                             archived=False))

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
        self.create_flow()

        response = self.fetchJSON(url)
        self.assertEqual(200, response.status_code)
        self.assertResultCount(response, 3)

        response = self.fetchJSON(url, "uuid=%s&uuid=%s" % (flow.uuid, flow2.uuid))
        self.assertEqual(200, response.status_code)
        self.assertResultCount(response, 2)

        response = self.fetchJSON(url, "flow=%d&flow=%d" % (flow.pk, flow2.pk))
        self.assertEqual(200, response.status_code)
        self.assertResultCount(response, 2)

        label2 = FlowLabel.create_unique("Surveys", self.org)
        label2.toggle_label([flow2], add=True)

        response = self.fetchJSON(url, "label=Polls&label=Surveys")
        self.assertEqual(200, response.status_code)
        self.assertResultCount(response, 2)

    def test_api_flow_definition(self):
        url = reverse('api.v1.flow_definition')
        self.login(self.surveyor)

        # load flow definition from test data
        flow = self.get_flow('pick_a_number')

        response = self.fetchJSON(url, "uuid=%s" % flow.uuid)
        self.assertEqual(1, response.json()['metadata']['revision'])
        self.assertEqual("Pick a Number", response.json()['metadata']['name'])
        self.assertEqual("F", response.json()['flow_type'])

        # make sure the version that is returned increments properly
        flow.update(flow.as_json())
        response = self.fetchJSON(url, "uuid=%s" % flow.uuid)
        self.assertEqual(2, response.json()['metadata']['revision'])

    def test_api_steps_empty(self):
        url = reverse('api.v1.steps')
        self.login(self.surveyor)

        flow = self.get_flow('color')

        # remove our entry node
        ActionSet.objects.get(uuid=flow.entry_uuid).delete()

        # and set our entry to be our ruleset
        flow.entry_type = Flow.RULES_ENTRY
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

        with patch.object(timezone, 'now', return_value=datetime(2015, 9, 15, 0, 0, 0, 0, pytz.UTC)):
            self.postJSON(url, data)

        run = FlowRun.objects.get()
        self.assertEqual(run.flow, flow)
        self.assertEqual(run.contact, self.joe)
        self.assertEqual(run.created_on, datetime(2015, 8, 25, 11, 9, 29, 88000, pytz.UTC))
        self.assertEqual(run.modified_on, datetime(2015, 9, 15, 0, 0, 0, 0, pytz.UTC))
        self.assertEqual(run.is_active, True)
        self.assertEqual(run.is_completed(), False)
        self.assertEqual(run.path, [
            {'node_uuid': flow.entry_uuid, 'arrived_on': '2015-08-25T11:09:30.088000+00:00'}
        ])

        # check flow stats
        self.assertEqual(flow.get_run_stats(),
                         {'total': 1, 'active': 1, 'completed': 0, 'expired': 0, 'interrupted': 0, 'completion': 0})

        # check flow activity
        self.assertEqual(flow.get_activity(), ({flow.entry_uuid: 1}, {}))

    def test_api_steps_media(self):
        url = reverse('api.v1.steps')

        # login as surveyor
        self.login(self.surveyor)

        flow = self.get_flow('media_survey')

        rulesets = RuleSet.objects.filter(flow=flow).order_by('y')
        ruleset_name = rulesets[0]
        ruleset_location = rulesets[1]
        ruleset_photo = rulesets[2]
        ruleset_video = rulesets[3]

        data = dict(
            flow=flow.uuid,
            revision=1,
            contact=self.joe.uuid,
            started='2015-08-25T11:09:29.088Z',
            submitted_by=self.admin.username,
            steps=[

                # the contact name
                dict(node=ruleset_name.uuid,
                     arrived_on='2015-08-25T11:11:30.000Z',
                     rule=dict(uuid=ruleset_name.get_rules()[0].uuid,
                               value="Marshawn",
                               category="All Responses",
                               text="Marshawn")),

                # location ruleset
                dict(node=ruleset_location.uuid,
                     arrived_on='2015-08-25T11:12:30.000Z',
                     rule=dict(uuid=ruleset_location.get_rules()[0].uuid,
                               category="All Responses",
                               media="geo:47.7579804,-121.0821648")),

                # a picture of steve
                dict(node=ruleset_photo.uuid,
                     arrived_on='2015-08-25T11:13:30.000Z',
                     rule=dict(uuid=ruleset_photo.get_rules()[0].uuid,
                               category="All Responses",
                               media="image/jpeg:http://testserver/media/steve.jpg")),

                # a video
                dict(node=ruleset_video.uuid,
                     arrived_on='2015-08-25T11:13:30.000Z',
                     rule=dict(uuid=ruleset_video.get_rules()[0].uuid,
                               category="All Responses",
                               media="video/mp4:http://testserver/media/snow.mp4")),
            ],
            completed=False)

        with patch.object(timezone, 'now', return_value=datetime(2015, 9, 16, 0, 0, 0, 0, pytz.UTC)):

            # first try posting without an encoding on the media type
            data['steps'][3]['rule']['media'] = 'video:http://testserver/media/snow.mp4'
            response = self.postJSON(url, data)
            self.assertEqual(400, response.status_code)
            error = response.json()['non_field_errors'][0]
            self.assertEqual("Invalid media type 'video': video:http://testserver/media/snow.mp4", error)

            # now update the video to an unrecognized type
            data['steps'][3]['rule']['media'] = 'unknown/mp4:http://testserver/media/snow.mp4'
            response = self.postJSON(url, data)
            self.assertEqual(400, response.status_code)
            error = response.json()['non_field_errors'][0]
            self.assertEqual("Invalid media type 'unknown': unknown/mp4:http://testserver/media/snow.mp4", error)

            # finally do a valid media
            data['steps'][3]['rule']['media'] = 'video/mp4:http://testserver/media/snow.mp4'
            response = self.postJSON(url, data)
            self.assertEqual(201, response.status_code)

        run = FlowRun.objects.get(flow=flow)

        # check our gps coordinates showed up properly, sooouie.
        msgs = run.get_messages().order_by('id')
        self.assertEqual(len(msgs), 4)
        self.assertEqual(msgs[0].text, "Marshawn")
        self.assertEqual(msgs[1].text, '47.7579804,-121.0821648')
        self.assertEqual(msgs[1].attachments, ['geo:47.7579804,-121.0821648'])
        self.assertTrue(msgs[2].attachments[0].startswith('image/jpeg:http'))
        self.assertTrue(msgs[2].attachments[0].endswith('.jpg'))
        self.assertTrue(msgs[3].attachments[0].startswith('video/mp4:http'))
        self.assertTrue(msgs[3].attachments[0].endswith('.mp4'))

    def test_api_steps(self):
        url = reverse('api.v1.steps')

        # can't access, get 403
        self.assert403(url)

        # login as plain user
        self.login(self.user)
        self.assert403(url)

        # login as surveyor
        self.login(self.surveyor)

        flow = self.get_flow('color')
        color_prompt = ActionSet.objects.get(x=1, y=1)
        color_ruleset = RuleSet.objects.get(label="color")
        orange_rule = color_ruleset.get_rules()[0]
        color_reply = ActionSet.objects.get(x=2, y=2)

        # add an update action
        definition = flow.as_json()
        new_node = {
            'uuid': str(uuid4()),
            'x': 100, 'y': 4,
            'actions': [
                {'type': 'save', 'field': 'tel_e164', 'value': '+12065551212'},
                {'type': 'del_group', 'group': {'name': 'Remove Me'}}
            ],
            'exit_uuid': str(uuid4()),
            'destination': None
        }

        # add a new action set
        definition['action_sets'].append(new_node)

        # point one of our nodes to it
        definition['action_sets'][1]['destination'] = new_node['uuid']

        flow.update(definition)
        data = dict(flow=flow.uuid,
                    revision=2,
                    contact=self.joe.uuid,
                    submitted_by=self.surveyor.username,
                    started='2015-08-25T11:09:29.088Z',
                    steps=[
                        dict(
                            node=color_prompt.uuid,
                            arrived_on='2015-08-25T11:09:30.088Z',
                            actions=[
                                dict(type="reply", msg="What is your favorite color?")
                            ]
                        )
                    ],
                    completed=False)

        # make our org brand different from the default brand
        # this is to make sure surveyor submissions work when
        # they deviate from DEFAULT_BRAND
        self.org.brand = 'other_brand'
        self.org.save()

        with patch.object(timezone, 'now', return_value=datetime(2015, 9, 15, 0, 0, 0, 0, pytz.UTC)):
            self.postJSON(url, data)

        run = FlowRun.objects.get()
        self.assertEqual(run.flow, flow)
        self.assertEqual(run.contact, self.joe)
        self.assertEqual(run.created_on, datetime(2015, 8, 25, 11, 9, 29, 88000, pytz.UTC))
        self.assertEqual(run.modified_on, datetime(2015, 9, 15, 0, 0, 0, 0, pytz.UTC))
        self.assertEqual(run.is_active, True)
        self.assertEqual(run.is_completed(), False)
        self.assertEqual(run.path, [
            {'node_uuid': color_prompt.uuid, 'arrived_on': '2015-08-25T11:09:30.088000+00:00'}
        ])

        # outgoing message for reply
        out_msgs = list(Msg.objects.filter(direction='O').order_by('pk'))
        self.assertEqual(len(out_msgs), 1)
        self.assertEqual(out_msgs[0].contact, self.joe)
        self.assertEqual(out_msgs[0].contact_urn, None)
        self.assertEqual(out_msgs[0].text, "What is your favorite color?")
        self.assertEqual(out_msgs[0].created_on, datetime(2015, 8, 25, 11, 9, 30, 88000, pytz.UTC))

        # check flow stats
        self.assertEqual(flow.get_run_stats(),
                         {'total': 1, 'active': 1, 'completed': 0, 'expired': 0, 'interrupted': 0, 'completion': 0})

        # check flow activity
        self.assertEqual(flow.get_activity(), ({color_prompt.uuid: 1}, {}))

        data = dict(
            flow=flow.uuid,
            revision=2,
            contact=self.joe.uuid,
            started='2015-08-25T11:09:29.088Z',
            submitted_by=self.surveyor.username,
            steps=[
                dict(
                    node=color_ruleset.uuid,
                    arrived_on='2015-08-25T11:11:30.088Z',
                    rule=dict(
                        uuid=orange_rule.uuid,
                        value="orange",
                        category="Orange",
                        text="I like orange"
                    )
                ),
                dict(
                    node=color_reply.uuid,
                    arrived_on='2015-08-25T11:13:30.088Z',
                    actions=[
                        dict(type="reply", msg="I love orange too!")
                    ]
                ),
                dict(
                    node=new_node['uuid'],
                    arrived_on='2015-08-25T11:15:30.088Z',
                    actions=[
                        dict(type="save", field="tel_e164", value="+12065551212"),
                        dict(type="del_group", group=dict(name="Remove Me"))
                    ]
                ),
            ],
            completed=True
        )

        with patch.object(timezone, 'now', return_value=datetime(2015, 9, 16, 0, 0, 0, 0, pytz.UTC)):
            self.postJSON(url, data)

        # run should be complete now
        run = FlowRun.objects.get()

        self.assertEqual(run.submitted_by, self.surveyor)
        self.assertEqual(run.modified_on, datetime(2015, 9, 16, 0, 0, 0, 0, pytz.UTC))
        self.assertEqual(run.is_active, False)
        self.assertEqual(run.is_completed(), True)
        self.assertEqual(run.path, [
            {'node_uuid': color_prompt.uuid, 'arrived_on': '2015-08-25T11:09:30.088000+00:00', 'exit_uuid': color_prompt.exit_uuid},
            {'node_uuid': color_ruleset.uuid, 'arrived_on': '2015-08-25T11:11:30.088000+00:00', 'exit_uuid': orange_rule.uuid},
            {'node_uuid': color_reply.uuid, 'arrived_on': '2015-08-25T11:13:30.088000+00:00', 'exit_uuid': color_reply.exit_uuid},
            {'node_uuid': new_node['uuid'], 'arrived_on': '2015-08-25T11:15:30.088000+00:00'}
        ])

        # joe should have an urn now
        self.assertIsNotNone(self.joe.urns.filter(path='+12065551212').first())

        # check results
        results = run.results
        self.assertEqual(len(results), 1)
        self.assertEqual(results['color']['node_uuid'], color_ruleset.uuid)
        self.assertEqual(results['color']['name'], "color")
        self.assertEqual(results['color']['category'], "Orange")
        self.assertEqual(results['color']['value'], "orange")
        self.assertEqual(results['color']['input'], "I like orange")
        self.assertIsNotNone(results['color']['created_on'])

        # check messages
        msgs = run.get_messages().order_by('pk')
        self.assertEqual(len(msgs), 3)
        self.assertEqual(msgs[0].direction, 'O')
        self.assertEqual(msgs[0].text, "What is your favorite color?")
        self.assertEqual(msgs[1].direction, 'I')
        self.assertEqual(msgs[1].contact, self.joe)
        self.assertEqual(msgs[1].contact_urn, None)
        self.assertEqual(msgs[1].text, "I like orange")
        self.assertEqual(msgs[2].direction, 'O')
        self.assertEqual(msgs[2].contact, self.joe)
        self.assertEqual(msgs[2].contact_urn, None)
        self.assertEqual(msgs[2].text, "I love orange too!")
        self.assertEqual(msgs[2].response_to, msgs[1])

        # check flow stats
        self.assertEqual(flow.get_run_stats(),
                         {'total': 1, 'active': 0, 'completed': 1, 'expired': 0, 'interrupted': 0, 'completion': 100})

        # check flow activity
        self.assertEqual(flow.get_activity(), ({},
                                               {color_reply.exit_uuid + ':' + new_node['uuid']: 1,
                                                orange_rule.uuid + ':' + color_reply.uuid: 1,
                                                color_prompt.exit_uuid + ':' + color_ruleset.uuid: 1}))

        # now lets remove our last action set
        definition['action_sets'].pop()
        definition['action_sets'][1]['destination'] = None
        flow.update(definition)

        # update a value for our missing node
        data = dict(
            flow=flow.uuid,
            revision=2,
            contact=self.joe.uuid,
            started='2015-08-26T11:09:29.088Z',
            steps=[
                dict(
                    node=new_node['uuid'],
                    arrived_on='2015-08-26T11:15:30.088Z',
                    actions=[
                        dict(type="save", field="tel_e164", value="+13605551212")
                    ]
                ),
            ],
            completed=True
        )

        with patch.object(timezone, 'now', return_value=datetime(2015, 9, 16, 0, 0, 0, 0, pytz.UTC)):

            # this version doesn't have our node
            data['revision'] = 3
            response = self.postJSON(url, data)
            self.assertEqual(400, response.status_code)
            self.assertResponseError(response, 'non_field_errors', "No such node with UUID %s in flow 'Color Flow'" % new_node['uuid'])

            # this version doesn't exist
            data['revision'] = 12
            response = self.postJSON(url, data)
            self.assertEqual(400, response.status_code)
            self.assertResponseError(response, 'non_field_errors', "Invalid revision: 12")

            # this one exists and has our node
            data['revision'] = 2
            response = self.postJSON(url, data)
            self.assertEqual(201, response.status_code)
            self.assertIsNotNone(self.joe.urns.filter(path='+13605551212').first())

            # submitted_by is optional
            self.assertEqual(FlowRun.objects.filter(submitted_by=None).count(), 1)

            # test with old name
            del data['revision']
            data['version'] = 2
            response = self.postJSON(url, data)
            self.assertEqual(201, response.status_code)
            self.assertIsNotNone(self.joe.urns.filter(path='+13605551212').first())

            # rule uuid not existing we should find the actual matching rule
            data = dict(
                flow=flow.uuid,
                revision=2,
                contact=self.joe.uuid,
                started='2015-08-25T11:09:29.088Z',
                submitted_by=self.admin.username,
                steps=[
                    dict(
                        node=color_ruleset.uuid,
                        arrived_on='2015-08-25T11:11:30.088Z',
                        rule=dict(
                            uuid='abc5fd71-027b-40e8-a819-151a0f8140e6',
                            value="orange",
                            category="Orange",
                            text="I like orange"
                        )
                    ),
                    dict(
                        node=color_reply.uuid,
                        arrived_on='2015-08-25T11:13:30.088Z',
                        actions=[
                            dict(type="reply", msg="I love orange too!")
                        ]
                    ),
                    dict(
                        node=new_node['uuid'],
                        arrived_on='2015-08-25T11:15:30.088Z',
                        actions=[
                            dict(type="save", field="tel_e164", value="+12065551212"),
                            dict(type="del_group", group=dict(name="Remove Me"))
                        ]
                    ),
                ],
                completed=True
            )

            response = self.postJSON(url, data)
            self.assertEqual(201, response.status_code)

            with patch('temba.flows.models.RuleSet.find_matching_rule') as mock_find_matching_rule:
                mock_find_matching_rule.return_value = None, None

                with self.assertRaises(ValueError):
                    self.postJSON(url, data)

    def test_api_contacts(self):
        url = reverse('api.v1.contacts')

        # 403 if not logged in
        self.assert403(url)

        # login as plain user
        self.login(self.user)
        self.assert403(url)

        # login as surveyor
        self.login(self.surveyor)

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
        self.assertEqual(400, response.status_code)

        # add a contact using deprecated phone field
        response = self.postJSON(url, dict(name='Snoop Dog', phone='+250788123123'))
        self.assertEqual(201, response.status_code)

        # should be one contact now
        contact = Contact.objects.get()

        # make sure our response contains the uuid
        self.assertContains(response, contact.uuid, status_code=201)

        # and that the contact fields were properly set
        self.assertEqual("+250788123123", contact.get_urn(TEL_SCHEME).path)
        self.assertEqual("Snoop Dog", contact.name)
        self.assertEqual(self.org, contact.org)

        Contact.objects.all().delete()

        # add a contact using urns field, also set language
        response = self.postJSON(url, dict(name='Snoop Dog', urns=['tel:+250788123456', 'twitter:snoop']))

        contact = Contact.objects.get()

        self.assertEqual(201, response.status_code)
        self.assertContains(response, contact.uuid, status_code=201)

        self.assertEqual("+250788123456", contact.get_urn(TEL_SCHEME).path)
        self.assertEqual("snoop", contact.get_urn(TWITTER_SCHEME).path)
        self.assertEqual("Snoop Dog", contact.name)
        self.assertEqual(None, contact.language)
        self.assertEqual(self.org, contact.org)

        # try to update the language to something longer than 3-letters
        response = self.postJSON(url, dict(name='Snoop Dog', urns=['tel:+250788123456'], language='ENGRISH'))
        self.assertEqual(400, response.status_code)
        self.assertResponseError(response, 'language', "Ensure this field has no more than 3 characters.")

        # try to update the language to something shorter than 3-letters
        response = self.postJSON(url, dict(name='Snoop Dog', urns=['tel:+250788123456'], language='X'))
        self.assertEqual(400, response.status_code)
        self.assertResponseError(response, 'language', "Ensure this field has at least 3 characters.")

        # now try 'eng' for English
        response = self.postJSON(url, dict(name='Snoop Dog', urns=['tel:+250788123456'], language='eng'))
        self.assertEqual(201, response.status_code)

        contact = Contact.objects.get()
        self.assertEqual('eng', contact.language)

        # update the contact using deprecated phone field
        response = self.postJSON(url, dict(name='Eminem', phone='+250788123456'))
        self.assertEqual(201, response.status_code)

        contact = Contact.objects.get()
        self.assertEqual("+250788123456", contact.get_urn(TEL_SCHEME).path)
        self.assertEqual("snoop", contact.get_urn(TWITTER_SCHEME).path)
        self.assertEqual("Eminem", contact.name)
        self.assertEqual('eng', contact.language)
        self.assertEqual(self.org, contact.org)

        # try to update with an unparseable phone number
        response = self.postJSON(url, dict(name='Eminem', phone='nope'))
        self.assertResponseError(response, 'phone', "Invalid phone number: 'nope'")

        # try to update with an with an invalid phone number
        response = self.postJSON(url, dict(name='Eminem', phone='+120012301'))
        self.assertResponseError(response, 'phone', "Invalid phone number: '+120012301'")

        # try to update with both phone and urns field
        response = self.postJSON(url, dict(name='Eminem', phone='+250788123456', urns=['tel:+250788123456']))
        self.assertResponseError(response, 'non_field_errors', "Cannot provide both urns and phone parameters together")

        # clearing the contact name is allowed
        response = self.postJSON(url, dict(name="", uuid=contact.uuid))
        self.assertEqual(201, response.status_code)
        contact = Contact.objects.get()
        self.assertIsNone(contact.name)

        # update the contact using uuid, URNs will remain the same
        response = self.postJSON(url, dict(name="Mathers", uuid=contact.uuid))
        self.assertEqual(201, response.status_code)

        contact = Contact.objects.get()
        self.assertEqual("+250788123456", contact.get_urn(TEL_SCHEME).path)
        self.assertEqual("snoop", contact.get_urn(TWITTER_SCHEME).path)
        self.assertEqual("Mathers", contact.name)
        self.assertEqual('eng', contact.language)
        self.assertEqual(self.org, contact.org)

        # update the contact using uuid, this time change the urns to just the phone number
        response = self.postJSON(url, dict(name="Mathers", uuid=contact.uuid, urns=['tel:+250788123456']))
        self.assertEqual(201, response.status_code)

        contact = Contact.objects.get()
        self.assertEqual("+250788123456", contact.get_urn(TEL_SCHEME).path)
        self.assertFalse(contact.get_urn(TWITTER_SCHEME))
        self.assertEqual("Mathers", contact.name)
        self.assertEqual('eng', contact.language)
        self.assertEqual(self.org, contact.org)

        # try to update a contact using an invalid UUID
        response = self.postJSON(url, dict(name="Mathers", uuid='nope', urns=['tel:+250788123456']))
        self.assertResponseError(response, 'uuid', "Unable to find contact with UUID: nope")

        # try to update a contact using an invalid URN
        response = self.postJSON(url, dict(name="Mathers", uuid=contact.uuid, urns=['uh:nope']))
        self.assertResponseError(response, 'urns', "Invalid URN: 'uh:nope'")

        with AnonymousOrg(self.org):
            # anon orgs can update contacts by uuid
            response = self.postJSON(url, dict(name="Anon", uuid=contact.uuid))
            self.assertEqual(201, response.status_code)

            contact = Contact.objects.get()
            self.assertEqual("Anon", contact.name)

            # but can't update phone
            response = self.postJSON(url, dict(name="Anon", uuid=contact.uuid, phone='+250788123456'))
            self.assertResponseError(response, 'non_field_errors', "Cannot update contact URNs on anonymous organizations")

            # or URNs
            response = self.postJSON(url, dict(name="Anon", uuid=contact.uuid, urns=['tel:+250788123456']))
            self.assertResponseError(response, 'non_field_errors', "Cannot update contact URNs on anonymous organizations")

        # finally try clearing our language
        response = self.postJSON(url, dict(phone='+250788123456', language=None))
        self.assertEqual(201, response.status_code)

        contact = Contact.objects.get()
        self.assertEqual(None, contact.language)

        # update the contact using urns field, matching on one URN, adding another
        response = self.postJSON(url, dict(name='Dr Dre', urns=['tel:+250788123456', 'twitter:drdre'], language='eng'))
        self.assertEqual(201, response.status_code)

        contact = Contact.objects.get()
        contact_urns = [six.text_type(urn) for urn in contact.urns.all().order_by('scheme', 'path')]
        self.assertEqual(["tel:+250788123456", "twitter:drdre"], contact_urns)
        self.assertEqual("Dr Dre", contact.name)
        self.assertEqual(self.org, contact.org)

        # try to update the contact with and un-parseable urn
        response = self.postJSON(url, dict(name='Dr Dre', urns=['tel250788123456']))
        self.assertResponseError(response, 'urns', "Invalid URN: 'tel250788123456'")

        # try to post a new group with a blank name
        response = self.postJSON(url, dict(phone='+250788123456', groups=["  "]))
        self.assertResponseError(response, 'groups', "This field may not be blank.")

        # try to post a new group with invalid name
        response = self.postJSON(url, dict(phone='+250788123456', groups=["+People"]))
        self.assertResponseError(response, 'groups', "Invalid group name: '+People'")

        # add contact to a new group by name
        response = self.postJSON(url, dict(phone='+250788123456', groups=["Music Artists"]))
        artists = ContactGroup.user_groups.get(name="Music Artists")
        self.assertEqual(201, response.status_code)
        self.assertEqual("Music Artists", artists.name)
        self.assertEqual(1, artists.contacts.count())
        self.assertEqual(1, artists.get_member_count())  # check trigger-based count

        # remove contact from a group by name
        response = self.postJSON(url, dict(phone='+250788123456', groups=[]))
        artists = ContactGroup.user_groups.get(name="Music Artists")
        self.assertEqual(201, response.status_code)
        self.assertEqual(0, artists.contacts.count())
        self.assertEqual(0, artists.get_member_count())

        # add contact to a existing group by UUID
        response = self.postJSON(url, dict(phone='+250788123456', group_uuids=[artists.uuid]))
        artists = ContactGroup.user_groups.get(name="Music Artists")
        self.assertEqual(201, response.status_code)
        self.assertEqual("Music Artists", artists.name)
        self.assertEqual(1, artists.contacts.count())
        self.assertEqual(1, artists.get_member_count())

        # specifying both groups and group_uuids should return error
        response = self.postJSON(url, dict(phone='+250788123456', groups=[artists.name], group_uuids=[artists.uuid]))
        self.assertEqual(400, response.status_code)

        # specifying invalid group_uuid should return error
        response = self.postJSON(url, dict(phone='+250788123456', group_uuids=['nope']))
        self.assertResponseError(response, 'group_uuids', "Unable to find contact group with uuid: nope")

        # can't add a contact to a group if they're blocked
        contact.block(self.user)
        response = self.postJSON(url, dict(phone='+250788123456', groups=["Dancers"]))
        self.assertEqual(response.status_code, 400)
        self.assertResponseError(response, 'non_field_errors', "Cannot add blocked contact to groups")

        contact.unblock(self.user)
        artists.contacts.add(contact)

        # try updating with a reserved word field
        response = self.postJSON(url, dict(phone='+250788123456', fields={"mailto": "andy@example.com"}))
        self.assertEqual(400, response.status_code)
        self.assertResponseError(response, 'fields', "Invalid contact field key: 'mailto' is a reserved word")

        # try updating a non-existent field
        response = self.postJSON(url, dict(phone='+250788123456', fields={"real_name": "Andy"}))
        self.assertEqual(201, response.status_code)
        self.assertIsNotNone(contact.get_field('real_name'))
        self.assertEqual("Andy", contact.get_field_display("real_name"))

        # create field and try again
        ContactField.get_or_create(self.org, self.user, 'real_name', "Real Name", value_type='T')
        response = self.postJSON(url, dict(phone='+250788123456', fields={"real_name": "Andy"}))
        contact = Contact.objects.get()
        self.assertContains(response, "Andy", status_code=201)
        self.assertEqual("Andy", contact.get_field_display("real_name"))

        # update field via label (deprecated but allowed)
        response = self.postJSON(url, dict(phone='+250788123456', fields={"Real Name": "Andre"}))
        contact = Contact.objects.get()
        self.assertContains(response, "Andre", status_code=201)
        self.assertEqual("Andre", contact.get_field_display("real_name"))

        # try when contact field have same key and label
        state = ContactField.get_or_create(self.org, self.user, 'state', "state", value_type='T')
        response = self.postJSON(url, dict(phone='+250788123456', fields={"state": "IL"}))
        self.assertContains(response, "IL", status_code=201)
        contact = Contact.objects.get()
        self.assertEqual("IL", contact.get_field_display("state"))
        self.assertEqual("Andre", contact.get_field_display("real_name"))

        # try when contact field is not active
        state.is_active = False
        state.save()
        response = self.postJSON(url, dict(phone='+250788123456', fields={"state": "VA"}))
        self.assertEqual(response.status_code, 201)
        self.assertEqual("VA", Value.objects.get(contact=contact, contact_field=state).string_value)   # unchanged

        drdre = Contact.objects.get()

        # add another contact
        jay_z = self.create_contact("Jay-Z", number="+250784444444")
        ContactField.get_or_create(self.org, self.admin, 'registration_date', "Registration Date", None, Value.TYPE_DATETIME)
        jay_z.set_field(self.user, 'registration_date', "31-12-2014 03:04:00")

        # try to update using URNs from two different contacts
        response = self.postJSON(url, dict(name="Iggy", urns=['tel:+250788123456', 'tel:+250784444444']))
        self.assertEqual(response.status_code, 400)
        self.assertResponseError(response, 'non_field_errors', "URNs are used by multiple contacts")

        # update URN using UUID - note this endpoint still allows numbers without country codes
        response = self.postJSON(url, dict(uuid=jay_z.uuid, name="Jay-Z", urns=['tel:0785555555']))
        self.assertEqual(response.status_code, 201)

        jay_z = Contact.objects.get(pk=jay_z.pk)
        self.assertEqual([six.text_type(u) for u in jay_z.urns.all()], ['tel:+250785555555'])

        # fetch all with blank query
        self.clear_cache()
        response = self.fetchJSON(url, "")
        self.assertEqual(200, response.status_code)

        resp_json = response.json()
        self.assertEqual(len(resp_json['results']), 2)

        self.assertEqual(resp_json['results'][1]['name'], "Dr Dre")
        self.assertEqual(resp_json['results'][1]['urns'], ['tel:+250788123456', 'twitter:drdre'])
        self.assertEqual(resp_json['results'][1]['fields'], {'real_name': "Andre", 'registration_date': None,
                                                             'state': 'VA'})
        self.assertEqual(resp_json['results'][1]['group_uuids'], [artists.uuid])
        self.assertEqual(resp_json['results'][1]['groups'], ["Music Artists"])
        self.assertEqual(resp_json['results'][1]['blocked'], False)
        self.assertEqual(resp_json['results'][1]['failed'], False)

        self.assertEqual(resp_json['results'][0]['name'], "Jay-Z")
        self.assertEqual(resp_json['results'][0]['fields'], {'real_name': None,
                                                             'registration_date': "2014-12-31T03:04:00+02:00",
                                                             'state': None})

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
        response = self.fetchJSON(url, 'urns=%s&urns=%s' % (urlquote_plus("tel:+250788123456"), urlquote_plus("tel:+250785555555")))
        self.assertResultCount(response, 2)

        # search deleted contacts
        response = self.fetchJSON(url, 'deleted=true')
        self.assertResultCount(response, 0)

        # search by group
        response = self.fetchJSON(url, "group=Music+Artists")
        self.assertResultCount(response, 1)
        self.assertContains(response, "Dr Dre")

        self.create_group('Actors', [jay_z])
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
            self.assertNotContains(response, '0785555555')

            # try to create a contact with an external URN
            response = self.postJSON(url, dict(urns=['ext:external-id'], name="Test Name"))
            self.assertEqual(response.status_code, 201)

            # assert that that contact now exists
            contact = Contact.objects.get(name="Test Name", urns__path='external-id', urns__scheme='ext')

            # remove it
            contact.delete()

        # check fetching deleted contacts
        drdre.release(self.user)
        response = self.fetchJSON(url, "deleted=true")
        self.assertEqual(200, response.status_code)
        self.assertEqual(len(response.json()['results']), 1)

        resp_json = response.json()
        self.assertEqual(resp_json['results'][0]['uuid'], drdre.uuid)
        self.assertIsNone(resp_json['results'][0]['name'])
        self.assertFalse(resp_json['results'][0]['urns'])
        self.assertFalse(resp_json['results'][0]['fields'])
        self.assertFalse(resp_json['results'][0]['group_uuids'])
        self.assertFalse(resp_json['results'][0]['groups'])
        self.assertIsNone(resp_json['results'][0]['blocked'])
        self.assertIsNone(resp_json['results'][0]['failed'])

        # add a naked contact
        response = self.postJSON(url, dict())
        self.assertIsNotNone(response.json()['uuid'])
        self.assertEqual(201, response.status_code)

        # create a contact with an email urn
        response = self.postJSON(url, dict(name='Snoop Dogg', urns=['mailto:snoop@foshizzle.com']))
        self.assertEqual(201, response.status_code)

        # lookup that contact from an urn
        contact = Contact.from_urn(self.org, "mailto:snoop@foshizzle.com")
        self.assertEqual('Snoop', contact.first_name(self.org))

        # find it via the api
        response = self.fetchJSON(url, 'urns=%s' % (urlquote_plus("mailto:snoop@foshizzle.com")))
        self.assertResultCount(response, 1)
        results = response.json()['results']
        self.assertEqual('Snoop Dogg', results[0]['name'])

        # add two existing contacts
        self.create_contact("Zinedine", number="+250788111222")
        self.create_contact("Rusell", number="+250788333444")

        # return error when trying to to create a new contact with many urns from different existing contacts
        response = self.postJSON(url, dict(name="Hart", urns=['tel:0788111222', 'tel:+250788333444']))
        self.assertResponseError(response, 'non_field_errors', "URNs are used by multiple contacts")

    def test_api_contacts_with_multiple_pages(self):
        url = reverse('api.v1.contacts')

        # bulk create more contacts than fits on one page
        contacts = []
        for c in range(0, 300):
            contacts.append(Contact(org=self.org, name="Minion %d" % (c + 1),
                                    created_by=self.admin, modified_by=self.admin))
        Contact.objects.all().delete()
        Contact.objects.bulk_create(contacts)

        # login as surveyor
        self.login(self.surveyor)

        # page is implicit
        response = self.fetchJSON(url)
        self.assertEqual(200, response.status_code)
        self.assertResultCount(response, 300)
        self.assertEqual(response.json()['results'][0]['name'], "Minion 300")

        Contact.objects.create(org=self.org, name="Minion 301", created_by=self.admin, modified_by=self.admin)

        # page 1 request always recalculates count
        response = self.fetchJSON(url, 'page=1')
        self.assertResultCount(response, 301)
        self.assertEqual(response.json()['results'][0]['name'], "Minion 301")

        Contact.objects.create(org=self.org, name="Minion 302", created_by=self.admin, modified_by=self.admin)

        # other page numbers won't
        response = self.fetchJSON(url, 'page=2')
        self.assertResultCount(response, 301)
        self.assertEqual(response.json()['results'][0]['name'], "Minion 52")

        # handle non-ascii chars in params
        response = self.fetchJSON(url, 'page=1&test=é')
        self.assertResultCount(response, 302)

        Contact.objects.create(org=self.org, name="Minion 303", created_by=self.admin, modified_by=self.admin)

        # should force calculation for new query (e != é)
        response = self.fetchJSON(url, 'page=2&test=e')
        self.assertResultCount(response, 303)

    @patch.object(ContactField, "MAX_ORG_CONTACTFIELDS", new=10)
    def test_api_fields(self):
        url = reverse('api.v1.contactfields')

        # 403 if not logged in
        self.assert403(url)

        # login as plain user
        self.login(self.user)
        self.assert403(url)

        # login as surveyor
        self.login(self.surveyor)

        # browse endpoint as HTML docs
        response = self.fetchHTML(url)
        self.assertEqual(response.status_code, 200)

        # no fields yet
        response = self.fetchJSON(url)
        self.assertResultCount(response, 0)

        # add a field
        response = self.postJSON(url, dict(label='Real Age', value_type='T'))
        self.assertEqual(201, response.status_code)

        # should be one field now
        field = ContactField.objects.get()
        self.assertEqual('Real Age', field.label)
        self.assertEqual('T', field.value_type)
        self.assertEqual('real_age', field.key)
        self.assertEqual(self.org, field.org)

        # update that field to change value type
        response = self.postJSON(url, dict(key='real_age', label='Actual Age', value_type='N'))
        self.assertEqual(201, response.status_code)
        field = ContactField.objects.get()
        self.assertEqual('Actual Age', field.label)
        self.assertEqual('N', field.value_type)
        self.assertEqual('real_age', field.key)
        self.assertEqual(self.org, field.org)

        # update with invalid value type
        response = self.postJSON(url, dict(key='real_age', value_type='X'))
        self.assertEqual(400, response.status_code)
        self.assertResponseError(response, 'value_type', "Invalid field value type")

        # update without label
        response = self.postJSON(url, dict(key='real_age', value_type='N'))
        self.assertEqual(400, response.status_code)
        self.assertResponseError(response, 'label', "This field is required.")

        # update without value type
        response = self.postJSON(url, dict(key='real_age', label='Actual Age'))
        self.assertEqual(400, response.status_code)
        self.assertResponseError(response, 'value_type', "This field is required.")

        # create with invalid label
        response = self.postJSON(url, dict(label='!@#', value_type='T'))
        self.assertEqual(400, response.status_code)
        self.assertResponseError(response, 'label', "Field can only contain letters, numbers and hypens")

        # create with label that would be an invalid key
        response = self.postJSON(url, dict(label='Name', value_type='T'))
        self.assertEqual(400, response.status_code)
        self.assertResponseError(response, 'non_field_errors', "Generated key for 'Name' is invalid or a reserved name")

        # create with key specified
        response = self.postJSON(url, dict(key='real_age_2', label="Actual Age 2", value_type='N'))
        self.assertEqual(201, response.status_code)
        field = ContactField.objects.get(key='real_age_2')
        self.assertEqual(field.label, "Actual Age 2")
        self.assertEqual(field.value_type, 'N')

        # create with invalid key specified
        response = self.postJSON(url, dict(key='name', label='Real Name', value_type='T'))
        self.assertEqual(400, response.status_code)
        self.assertResponseError(response, 'key', "Field is invalid or a reserved name")

        ContactField.objects.all().delete()

        for i in range(ContactField.MAX_ORG_CONTACTFIELDS):
            ContactField.get_or_create(self.org, self.admin, 'field%d' % i, 'Field%d' % i)

        response = self.postJSON(url, dict(label='Real Age', value_type='T'))
        self.assertResponseError(response, 'non_field_errors',
                                 "This org has 10 contact fields and the limit is 10. "
                                 "You must delete existing ones before you can create new ones.")

    def test_api_authenticate(self):
        url = reverse('api.v1.authenticate')

        # fetch our html docs
        self.assertEqual(self.fetchHTML(url).status_code, 200)

        admin_group = Group.objects.get(name='Administrators')
        surveyor_group = Group.objects.get(name='Surveyors')

        # login an admin as an admin
        admin = self.client.post(url, dict(email='Administrator', password='Administrator', role='A')).json()
        self.assertEqual(1, len(admin))
        self.assertEqual('Temba', admin[0]['name'])
        self.assertIsNotNone(APIToken.objects.filter(key=admin[0]['token'], role=admin_group).first())

        # login an admin as a surveyor
        surveyor = self.client.post(url, dict(email='Administrator', password='Administrator', role='S')).json()
        self.assertEqual(1, len(surveyor))
        self.assertEqual('Temba', surveyor[0]['name'])
        self.assertIsNotNone(APIToken.objects.filter(key=surveyor[0]['token'], role=surveyor_group).first())

        # the keys should be different
        self.assertNotEqual(admin[0]['token'], surveyor[0]['token'])

        # configure our api client
        client = APIClient()

        surveyor_token = dict(HTTP_AUTHORIZATION='Token ' + surveyor[0]['token'])
        client.credentials(**surveyor_token)

        # surveyor token can get flows or contacts
        self.assertEqual(200, client.get(reverse('api.v1.flows') + '.json').status_code)
        self.assertEqual(200, client.get(reverse('api.v1.contacts') + '.json').status_code)

        # our surveyor can't login with an admin role
        response = self.client.post(url, dict(email='Surveyor', password='Surveyor', role='A')).json()
        self.assertEqual(0, len(response))

        # but they can with a surveyor role
        response = self.client.post(url, dict(email='Surveyor', password='Surveyor', role='S')).json()
        self.assertEqual(1, len(response))

        # and can fetch flows, contacts, and fields
        client.credentials(HTTP_AUTHORIZATION='Token ' + response[0]['token'])
        self.assertEqual(200, client.get(reverse('api.v1.flows') + '.json').status_code)
        self.assertEqual(200, client.get(reverse('api.v1.contacts') + '.json').status_code)
        self.assertEqual(200, client.get(reverse('api.v1.contactfields') + '.json').status_code)

    def test_message_serialization(self):
        """
        API v1 no longer has a messages endpoint but serializer is still used for creating messages from webhook
        responses
        """
        serializer = MsgCreateSerializer(org=self.org, user=self.admin, data={
            'urn': ["tel:+250964150000"],
            'contact': [self.joe.uuid],
            'text': "Hello1"
        })
        self.assertTrue(serializer.is_valid())

        broadcast = serializer.save()
        contact = Contact.objects.get(urns__path='+250964150000')
        self.assertEqual(set(broadcast.contacts.all()), {contact, self.joe})
        self.assertEqual(broadcast.text, {'base': 'Hello1'})

        # try again with explicit channel
        serializer = MsgCreateSerializer(org=self.org, user=self.admin, data={
            'urn': ["tel:+250964150000"],
            'text': "Hello2",
            'channel': self.channel.id
        })
        self.assertTrue(serializer.is_valid())

        broadcast = serializer.save()
        self.assertEqual(broadcast.channel, self.channel)
        self.assertEqual(broadcast.text, {'base': 'Hello2'})

        # try with channel that isn't ours
        serializer = MsgCreateSerializer(org=self.org, user=self.admin, data={
            'contact': [self.joe.uuid],
            'text': "Hello2",
            'channel': self.channel2.id
        })
        self.assertFalse(serializer.is_valid())

        # try with invalid phone number
        serializer = MsgCreateSerializer(org=self.org, user=self.admin, data={
            'text': "Hello2",
            'phone': '12'
        })
        self.assertFalse(serializer.is_valid())
