# -*- coding: utf-8 -*-
from __future__ import absolute_import, unicode_literals

import json
import pytz

from datetime import datetime
from django.contrib.auth.models import Group
from django.contrib.gis.geos import GEOSGeometry
from django.core.urlresolvers import reverse
from django.conf import settings
from django.db import connection
from django.test import override_settings
from django.utils import timezone
from mock import patch
from rest_framework import serializers
from rest_framework.test import APIClient
from temba.campaigns.models import Campaign, CampaignEvent
from temba.channels.models import Channel, ChannelEvent
from temba.contacts.models import Contact, ContactGroup, ContactField
from temba.flows.models import Flow, FlowRun, FlowLabel, ReplyAction
from temba.locations.models import BoundaryAlias
from temba.msgs.models import Broadcast, Label, Msg
from temba.tests import TembaTest, AnonymousOrg
from temba.values.models import Value
from ..models import APIToken, Resthook, WebHookEvent
from ..v2 import fields
from ..v2.serializers import format_datetime


NUM_BASE_REQUEST_QUERIES = 7  # number of db queries required for any API request


class APITest(TembaTest):

    def setUp(self):
        super(APITest, self).setUp()

        self.joe = self.create_contact("Joe Blow", "0788123123")
        self.frank = self.create_contact("Frank", twitter="franky")
        self.test_contact = Contact.get_test_contact(self.user)

        self.twitter = Channel.create(self.org, self.user, None, 'TT', name="Twitter Channel",
                                      address="billy_bob", role="SR", scheme='twitter')

        self.create_secondary_org()
        self.hans = self.create_contact("Hans Gruber", "+4921551511", org=self.org2)

        self.maxDiff = None

        # this is needed to prevent REST framework from rolling back transaction created around each unit test
        connection.settings_dict['ATOMIC_REQUESTS'] = False

    def tearDown(self):
        super(APITest, self).tearDown()

        connection.settings_dict['ATOMIC_REQUESTS'] = True

    def fetchHTML(self, url, query=None):
        if query:
            url += ('?' + query)

        return self.client.get(url, HTTP_X_FORWARDED_HTTPS='https')

    def fetchJSON(self, url, query=None, raw_url=False):
        if not raw_url:
            url += '.json'
            if query:
                url += ('?' + query)

        response = self.client.get(url, content_type="application/json", HTTP_X_FORWARDED_HTTPS='https')

        # this will fail if our response isn't valid json
        response.json = json.loads(response.content)
        return response

    def postJSON(self, url, data):
        response = self.client.post(url + ".json", json.dumps(data), content_type="application/json", HTTP_X_FORWARDED_HTTPS='https')
        if response.content:
            response.json = json.loads(response.content)
        return response

    def deleteJSON(self, url, query=None):
        url += ".json"
        if query:
            url = url + "?" + query

        response = self.client.delete(url, content_type="application/json", HTTP_X_FORWARDED_HTTPS='https')
        if response.content:
            response.json = json.loads(response.content)
        return response

    def assertEndpointAccess(self, url, query=None):
        self.client.logout()

        # 403 if not authenticated but can read docs
        response = self.fetchHTML(url, query)
        self.assertEqual(response.status_code, 403)

        # same for non-org user
        self.login(self.non_org_user)
        response = self.fetchHTML(url, query)
        self.assertEqual(response.status_code, 403)

        # same for plain user
        self.login(self.user)
        response = self.fetchHTML(url, query)
        self.assertEqual(response.status_code, 403)

        # 403 for JSON request too
        response = self.fetchJSON(url, query)
        self.assertResponseError(response, None, "You do not have permission to perform this action.", status_code=403)

        # 200 for administrator
        self.login(self.admin)
        response = self.fetchHTML(url, query)
        self.assertEqual(response.status_code, 200)

    def assertResultsById(self, response, expected):
        self.assertEqual(response.status_code, 200)
        self.assertEqual([r['id'] for r in response.json['results']], [o.pk for o in expected])

    def assertResultsByUUID(self, response, expected):
        self.assertEqual(response.status_code, 200)
        self.assertEqual([r['uuid'] for r in response.json['results']], [o.uuid for o in expected])

    def assertResponseError(self, response, field, expected_message, status_code=400):
        self.assertEqual(response.status_code, status_code)
        if field:
            self.assertIn(field, response.json)
            self.assertIsInstance(response.json[field], list)
            self.assertIn(expected_message, response.json[field])
        else:
            self.assertIsInstance(response.json, dict)
            self.assertIn('detail', response.json)
            self.assertEqual(response.json['detail'], expected_message)

    def test_serializer_fields(self):
        field = fields.LimitedListField(child=serializers.IntegerField(), source='test')

        self.assertEqual(field.to_internal_value([1, 2, 3]), [1, 2, 3])
        self.assertRaises(serializers.ValidationError, field.to_internal_value, list(range(101)))  # too long

        field = fields.ChannelField(source='test')
        field.context = {'org': self.org}

        self.assertEqual(field.to_internal_value(self.channel.uuid), self.channel)
        self.channel.is_active = False
        self.channel.save()
        self.assertRaises(serializers.ValidationError, field.to_internal_value, self.channel.uuid)

        field = fields.ContactField(source='test')
        field.context = {'org': self.org}

        self.assertEqual(field.to_internal_value(self.joe.uuid), self.joe)
        self.assertRaises(serializers.ValidationError, field.to_internal_value, [self.joe.uuid, self.frank.uuid])

        field = fields.ContactField(source='test', many=True)
        field.child_relation.context = {'org': self.org}

        self.assertEqual(field.to_internal_value([self.joe.uuid, self.frank.uuid]), [self.joe, self.frank])
        self.assertRaises(serializers.ValidationError, field.to_internal_value, self.joe.uuid)

        field = fields.ContactGroupField(source='test')
        field.context = {'org': self.org}
        group = self.create_group("Customers")

        self.assertEqual(field.to_internal_value(group.uuid), group)

        field = fields.FlowField(source='test')
        field.context = {'org': self.org}
        flow = self.create_flow()

        self.assertEqual(field.to_internal_value(flow.uuid), flow)

        field = fields.URNField(source='test')
        field.context = {'org': self.org}

        self.assertEqual(field.to_internal_value('tel:+1-800-123-4567'), 'tel:+18001234567')
        self.assertRaises(serializers.ValidationError, field.to_internal_value, '12345')  # un-parseable
        self.assertRaises(serializers.ValidationError, field.to_internal_value, 'tel:800-123-4567')  # no country code

    def test_authentication(self):
        def api_request(endpoint, token):
            response = self.client.get(endpoint + '.json', content_type="application/json",
                                       HTTP_X_FORWARDED_HTTPS='https', HTTP_AUTHORIZATION="Token %s" % token)
            response.json = json.loads(response.content)
            return response

        contacts_url = reverse('api.v2.contacts')
        campaigns_url = reverse('api.v2.campaigns')

        # can't fetch endpoint with invalid token
        response = api_request(contacts_url, "1234567890")
        self.assertResponseError(response, None, "Invalid token", status_code=403)

        token1 = APIToken.get_or_create(self.org, self.admin, Group.objects.get(name="Administrators"))
        token2 = APIToken.get_or_create(self.org, self.admin, Group.objects.get(name="Surveyors"))

        # can fetch campaigns endpoint with valid admin token
        response = api_request(campaigns_url, token1.key)
        self.assertEqual(response.status_code, 200)

        # but not with surveyor token
        response = api_request(campaigns_url, token2.key)
        self.assertResponseError(response, None, "You do not have permission to perform this action.", status_code=403)

        # but it can be used to access the contacts endpoint
        response = api_request(contacts_url, token2.key)
        self.assertEqual(response.status_code, 200)

        # if user loses access to the token's role, don't allow the request
        self.org.administrators.remove(self.admin)
        self.org.surveyors.add(self.admin)

        self.assertEqual(api_request(campaigns_url, token1.key).status_code, 403)
        self.assertEqual(api_request(contacts_url, token2.key).status_code, 200)  # other token unaffected

        # and if user is inactive, disallow the request
        self.admin.is_active = False
        self.admin.save()

        response = api_request(contacts_url, token2.key)
        self.assertResponseError(response, None, "User inactive or deleted", status_code=403)

    @override_settings(SECURE_PROXY_SSL_HEADER=('HTTP_X_FORWARDED_HTTPS', 'https'))
    def test_root(self):
        url = reverse('api.v2')

        # browse as HTML anonymously (should still show docs)
        response = self.fetchHTML(url)
        self.assertContains(response, "This is the <strong>under-development</strong> API v2", status_code=403)

        # try to browse as JSON anonymously
        response = self.fetchJSON(url)
        self.assertResponseError(response, None, "Authentication credentials were not provided.", status_code=403)

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
        self.assertEqual(response.json['runs'], 'https://testserver:80/api/v2/runs')  # endpoints are listed

    def test_explorer(self):
        url = reverse('api.v2.explorer')

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

    def test_pagination(self):
        url = reverse('api.v2.runs') + '.json'
        self.login(self.admin)

        # create 1255 test runs (5 full pages of 250 items + 1 partial with 5 items)
        flow = self.create_flow(uuid_start=0)
        FlowRun.objects.bulk_create([FlowRun(org=self.org, flow=flow, contact=self.joe) for r in range(1255)])
        actual_ids = list(FlowRun.objects.order_by('-pk').values_list('pk', flat=True))

        # give them all the same modified_on
        FlowRun.objects.all().update(modified_on=datetime(2015, 9, 15, 0, 0, 0, 0, pytz.UTC))

        returned_ids = []

        # fetch all full pages
        response = None
        for p in range(5):
            response = self.fetchJSON(url if p == 0 else response.json['next'], raw_url=True)

            self.assertEqual(len(response.json['results']), 250)
            self.assertIsNotNone(response.json['next'])

            returned_ids += [r['id'] for r in response.json['results']]

        # fetch final partial page
        response = self.fetchJSON(response.json['next'], raw_url=True)

        self.assertEqual(len(response.json['results']), 5)
        self.assertIsNone(response.json['next'])

        returned_ids += [r['id'] for r in response.json['results']]

        self.assertEqual(returned_ids, actual_ids)  # ensure all results were returned and in correct order

    def test_authenticate(self):
        url = reverse('api.v2.authenticate')

        # fetch as HTML
        response = self.fetchHTML(url)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context['form'].fields.keys(), ['username', 'password', 'role', 'loc'])

        admins = Group.objects.get(name='Administrators')
        surveyors = Group.objects.get(name='Surveyors')

        # try to authenticate with incorrect password
        response = self.client.post(url, {'username': "Administrator", 'password': "XXXX", 'role': 'A'})
        self.assertEqual(response.status_code, 403)

        # try to authenticate with invalid role
        response = self.client.post(url, {'username': "Administrator", 'password': "Administrator", 'role': 'X'})
        self.assertFormError(response, 'form', 'role', "Select a valid choice. X is not one of the available choices.")

        # authenticate an admin as an admin
        response = self.client.post(url, {'username': "Administrator", 'password': "Administrator", 'role': 'A'})

        # should have created a new token object
        token_obj1 = APIToken.objects.get(user=self.admin, role=admins)

        tokens = json.loads(response.content)['tokens']
        self.assertEqual(len(tokens), 1)
        self.assertEqual(tokens[0], {'org': {'id': self.org.pk, 'name': "Temba"}, 'token': token_obj1.key})

        # authenticate an admin as a surveyor
        response = self.client.post(url, {'username': "Administrator", 'password': "Administrator", 'role': 'S'})

        # should have created a new token object
        token_obj2 = APIToken.objects.get(user=self.admin, role=surveyors)

        tokens = json.loads(response.content)['tokens']
        self.assertEqual(len(tokens), 1)
        self.assertEqual(tokens[0], {'org': {'id': self.org.pk, 'name': "Temba"}, 'token': token_obj2.key})

        # the keys should be different
        self.assertNotEqual(token_obj1.key, token_obj2.key)

        client = APIClient()

        # campaigns can be fetched by admin token
        client.credentials(HTTP_AUTHORIZATION="Token " + token_obj1.key)
        self.assertEqual(client.get(reverse('api.v2.campaigns') + '.json').status_code, 200)

        # but not by an admin's surveyor token
        client.credentials(HTTP_AUTHORIZATION="Token " + token_obj2.key)
        self.assertEqual(client.get(reverse('api.v2.campaigns') + '.json').status_code, 403)

        # but their surveyor token can get flows or contacts
        # self.assertEqual(client.get(reverse('api.v2.flows') + '.json').status_code, 200)  # TODO re-enable when added
        self.assertEqual(client.get(reverse('api.v2.contacts') + '.json').status_code, 200)

        # our surveyor can't login with an admin role
        response = self.client.post(url, {'username': "Surveyor", 'password': "Surveyor", 'role': 'A'})
        tokens = json.loads(response.content)['tokens']
        self.assertEqual(len(tokens), 0)

        # but they can with a surveyor role
        response = self.client.post(url, {'username': "Surveyor", 'password': "Surveyor", 'role': 'S'})
        tokens = json.loads(response.content)['tokens']
        self.assertEqual(len(tokens), 1)

        token_obj3 = APIToken.objects.get(user=self.surveyor, role=surveyors)

        # and can fetch flows, contacts, and fields, but not campaigns
        client.credentials(HTTP_AUTHORIZATION="Token " + token_obj3.key)
        self.assertEqual(client.get(reverse('api.v2.flows') + '.json').status_code, 200)
        self.assertEqual(client.get(reverse('api.v2.contacts') + '.json').status_code, 200)
        self.assertEqual(client.get(reverse('api.v2.fields') + '.json').status_code, 200)
        self.assertEqual(client.get(reverse('api.v2.campaigns') + '.json').status_code, 403)

    @patch('temba.flows.models.FlowStart.create')
    def test_transactions(self, mock_flowstart_create):
        """
        Serializer writes are wrapped in a transaction. This test simulates FlowStart.create blowing up and checks that
        contacts aren't created.
        """
        mock_flowstart_create.side_effect = ValueError("DOH!")

        flow = self.create_flow()
        self.login(self.admin)
        try:
            self.postJSON(reverse('api.v2.flow_starts'), dict(flow=flow.uuid, urns=['tel:+12067791212']))
            self.fail()  # ensure exception is thrown
        except ValueError:
            pass

        self.assertFalse(Contact.objects.filter(urns__path='+12067791212'))

    def test_boundaries(self):
        url = reverse('api.v2.boundaries')

        self.assertEndpointAccess(url)

        BoundaryAlias.create(self.org, self.admin, self.state1, "Kigali")
        BoundaryAlias.create(self.org, self.admin, self.state1, "Kigari")
        BoundaryAlias.create(self.org, self.admin, self.state2, "East Prov")
        BoundaryAlias.create(self.org2, self.admin2, self.state1, "Other Org")  # shouldn't be returned

        self.state1.simplified_geometry = GEOSGeometry('MULTIPOLYGON(((1 1, 1 -1, -1 -1, -1 1, 1 1)))')
        self.state1.save()

        # test without geometry
        with self.assertNumQueries(NUM_BASE_REQUEST_QUERIES + 3):
            response = self.fetchJSON(url)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json['next'], None)
        self.assertEqual(len(response.json['results']), 10)
        self.assertEqual(response.json['results'][0], {
            'osm_id': "1708283",
            'name': "Kigali City",
            'parent': {'osm_id': "171496", 'name': "Rwanda"},
            'level': 1,
            'aliases': ["Kigali", "Kigari"],
            'geometry': None
        })

        # test without geometry
        with self.assertNumQueries(NUM_BASE_REQUEST_QUERIES + 3):
            response = self.fetchJSON(url, 'geometry=true')

        self.assertEqual(response.json['results'][0], {
            'osm_id': "1708283",
            'name': "Kigali City",
            'parent': {'osm_id': "171496", 'name': "Rwanda"},
            'level': 1,
            'aliases': ["Kigali", "Kigari"],
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
            }
        })

    def test_broadcasts(self):
        url = reverse('api.v2.broadcasts')

        self.assertEndpointAccess(url)

        reporters = self.create_group("Reporters", [self.joe, self.frank])

        bcast1 = Broadcast.create(self.org, self.admin, "Hello 1", [self.frank.get_urn('twitter')])
        bcast2 = Broadcast.create(self.org, self.admin, "Hello 2", [self.joe])
        bcast3 = Broadcast.create(self.org, self.admin, "Hello 3", [self.frank], status='S')
        bcast4 = Broadcast.create(self.org, self.admin, "Hello 4",
                                  [self.frank.get_urn('twitter'), self.joe, reporters], status='F')
        Broadcast.create(self.org2, self.admin2, "Different org...", [self.hans])

        # no filtering
        with self.assertNumQueries(NUM_BASE_REQUEST_QUERIES + 4):
            response = self.fetchJSON(url)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json['next'], None)
        self.assertResultsById(response, [bcast4, bcast3, bcast2, bcast1])
        self.assertEqual(response.json['results'][0], {
            'id': bcast4.pk,
            'urns': ["twitter:franky"],
            'contacts': [{'uuid': self.joe.uuid, 'name': self.joe.name}],
            'groups': [{'uuid': reporters.uuid, 'name': reporters.name}],
            'text': "Hello 4",
            'created_on': format_datetime(bcast4.created_on)
        })

        # filter by id
        response = self.fetchJSON(url, 'id=%d' % bcast3.pk)
        self.assertResultsById(response, [bcast3])

        # filter by after
        response = self.fetchJSON(url, 'after=%s' % format_datetime(bcast3.created_on))
        self.assertResultsById(response, [bcast4, bcast3])

        # filter by before
        response = self.fetchJSON(url, 'before=%s' % format_datetime(bcast2.created_on))
        self.assertResultsById(response, [bcast2, bcast1])

        with AnonymousOrg(self.org):
            # URNs shouldn't be included
            response = self.fetchJSON(url, 'id=%d' % bcast1.pk)
            self.assertEqual(response.json['results'][0]['urns'], None)

        # try to create new broadcast with no data at all
        response = self.postJSON(url, {})
        self.assertResponseError(response, 'text', "This field is required.")

        # try to create new broadcast with no recipients
        response = self.postJSON(url, {'text': "Hello"})
        self.assertResponseError(response, 'non_field_errors', "Must provide either urns, contacts or groups")

        # create new broadcast with all fields
        response = self.postJSON(url, {
            'text': "Hello",
            'urns': ["twitter:franky"],
            'contacts': [self.joe.uuid, self.frank.uuid],
            'groups': [reporters.uuid],
            'channel': self.channel.uuid
        })

        broadcast = Broadcast.objects.get(pk=response.json['id'])
        self.assertEqual(broadcast.text, "Hello")
        self.assertEqual(set(broadcast.urns.values_list('urn', flat=True)), {"twitter:franky"})
        self.assertEqual(set(broadcast.contacts.all()), {self.joe, self.frank})
        self.assertEqual(set(broadcast.groups.all()), {reporters})
        self.assertEqual(broadcast.channel, self.channel)

        # try sending as a suspended org
        self.org.set_suspended()
        response = self.postJSON(url, {'text': "Hello", 'urns': ["twitter:franky"]})
        self.assertResponseError(response, 'non_field_errors', "Sorry, your account is currently suspended. To enable "
                                                               "sending messages, please contact support.")

    def test_campaigns(self):
        url = reverse('api.v2.campaigns')

        self.assertEndpointAccess(url)

        reporters = self.create_group("Reporters", [self.joe, self.frank])
        campaign1 = Campaign.create(self.org, self.admin, "Reminders #1", reporters)
        campaign2 = Campaign.create(self.org, self.admin, "Reminders #2", reporters)

        # create campaign for other org
        spammers = ContactGroup.get_or_create(self.org2, self.admin2, "Spammers")
        Campaign.create(self.org2, self.admin2, "Cool stuff", spammers)

        # no filtering
        with self.assertNumQueries(NUM_BASE_REQUEST_QUERIES + 2):
            response = self.fetchJSON(url)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json['next'], None)
        self.assertResultsByUUID(response, [campaign2, campaign1])
        self.assertEqual(response.json['results'][0], {
            'uuid': campaign2.uuid,
            'name': "Reminders #2",
            'group': {'uuid': reporters.uuid, 'name': "Reporters"},
            'created_on': format_datetime(campaign2.created_on)
        })

        # filter by UUID
        response = self.fetchJSON(url, 'uuid=%s' % campaign1.uuid)
        self.assertResultsByUUID(response, [campaign1])

    def test_campaign_events(self):
        url = reverse('api.v2.campaign_events')

        self.assertEndpointAccess(url)

        flow = self.create_flow()
        reporters = self.create_group("Reporters", [self.joe, self.frank])
        registration = ContactField.get_or_create(self.org, self.admin, 'registration', "Registration")

        campaign1 = Campaign.create(self.org, self.admin, "Reminders", reporters)
        event1 = CampaignEvent.create_message_event(self.org, self.admin, campaign1, registration,
                                                    1, CampaignEvent.UNIT_DAYS, "Don't forget to brush your teeth")

        campaign2 = Campaign.create(self.org, self.admin, "Notifications", reporters)
        event2 = CampaignEvent.create_flow_event(self.org, self.admin, campaign2, registration,
                                                 6, CampaignEvent.UNIT_HOURS, flow, delivery_hour=12)

        # create event for another org
        joined = ContactField.get_or_create(self.org2, self.admin2, 'joined', "Joined On")
        spammers = ContactGroup.get_or_create(self.org2, self.admin2, "Spammers")
        spam = Campaign.create(self.org2, self.admin2, "Cool stuff", spammers)
        CampaignEvent.create_flow_event(self.org2, self.admin2, spam, joined,
                                        6, CampaignEvent.UNIT_HOURS, flow, delivery_hour=12)

        # no filtering
        with self.assertNumQueries(NUM_BASE_REQUEST_QUERIES + 4):
            response = self.fetchJSON(url)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json['next'], None)
        self.assertResultsByUUID(response, [event2, event1])
        self.assertEqual(response.json['results'][0], {
            'uuid': event2.uuid,
            'campaign': {'uuid': campaign2.uuid, 'name': "Notifications"},
            'relative_to': {'key': "registration", 'label': "Registration"},
            'offset': 6,
            'unit': 'hours',
            'delivery_hour': 12,
            'flow': {'uuid': flow.uuid, 'name': "Color Flow"},
            'message': None,
            'created_on': format_datetime(event2.created_on)
        })

        # filter by UUID
        response = self.fetchJSON(url, 'uuid=%s' % event1.uuid)
        self.assertResultsByUUID(response, [event1])

        # filter by campaign name
        response = self.fetchJSON(url, 'campaign=Reminders')
        self.assertResultsByUUID(response, [event1])

        # filter by campaign UUID
        response = self.fetchJSON(url, 'campaign=%s' % campaign1.uuid)
        self.assertResultsByUUID(response, [event1])

        # filter by invalid campaign
        response = self.fetchJSON(url, 'campaign=invalid')
        self.assertResultsByUUID(response, [])

    def test_channels(self):
        url = reverse('api.v2.channels')

        self.assertEndpointAccess(url)

        # create channel for other org
        Channel.create(self.org2, self.admin2, None, 'TT', name="Twitter Channel",
                       address="nyaruka", role="SR", scheme='twitter')

        # no filtering
        with self.assertNumQueries(NUM_BASE_REQUEST_QUERIES + 2):
            response = self.fetchJSON(url)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json['next'], None)
        self.assertResultsByUUID(response, [self.twitter, self.channel])
        self.assertEqual(response.json['results'][1], {
            'uuid': self.channel.uuid,
            'name': "Test Channel",
            'address': "+250785551212",
            'country': "RW",
            'device': {
                'name': "Nexus 5X",
                'network_type': None,
                'power_level': -1,
                'power_source': None,
                'power_status': None
            },
            'last_seen': format_datetime(self.channel.last_seen),
            'created_on': format_datetime(self.channel.created_on)
        })

        # filter by UUID
        response = self.fetchJSON(url, 'uuid=%s' % self.twitter.uuid)
        self.assertResultsByUUID(response, [self.twitter])

        # filter by address
        response = self.fetchJSON(url, 'address=billy_bob')
        self.assertResultsByUUID(response, [self.twitter])

    def test_channel_events(self):
        url = reverse('api.v2.channel_events')

        self.assertEndpointAccess(url)

        call1 = ChannelEvent.create(self.channel, "tel:0788123123", ChannelEvent.TYPE_CALL_IN_MISSED, timezone.now(), 0)
        call2 = ChannelEvent.create(self.channel, "tel:0788124124", ChannelEvent.TYPE_CALL_IN, timezone.now(), 36)
        call3 = ChannelEvent.create(self.channel, "tel:0788124124", ChannelEvent.TYPE_CALL_OUT_MISSED, timezone.now(), 0)
        call4 = ChannelEvent.create(self.channel, "tel:0788123123", ChannelEvent.TYPE_CALL_OUT, timezone.now(), 15)

        # no filtering
        with self.assertNumQueries(NUM_BASE_REQUEST_QUERIES + 3):
            response = self.fetchJSON(url)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json['next'], None)
        self.assertResultsById(response, [call4, call3, call2, call1])
        self.assertEqual(response.json['results'][0], {
            'id': call4.pk,
            'channel': {'uuid': self.channel.uuid, 'name': "Test Channel"},
            'type': "call-out",
            'contact': {'uuid': self.joe.uuid, 'name': self.joe.name},
            'time': format_datetime(call4.time),
            'duration': 15,
            'created_on': format_datetime(call4.created_on),
        })

        # filter by id
        response = self.fetchJSON(url, 'id=%d' % call1.pk)
        self.assertResultsById(response, [call1])

        # filter by contact
        response = self.fetchJSON(url, 'contact=%s' % self.joe.uuid)
        self.assertResultsById(response, [call4, call1])

        # filter by invalid contact
        response = self.fetchJSON(url, 'contact=invalid')
        self.assertResultsById(response, [])

        # filter by before
        response = self.fetchJSON(url, 'before=%s' % format_datetime(call3.created_on))
        self.assertResultsById(response, [call3, call2, call1])

        # filter by after
        response = self.fetchJSON(url, 'after=%s' % format_datetime(call2.created_on))
        self.assertResultsById(response, [call4, call3, call2])

    def test_contacts(self):
        url = reverse('api.v2.contacts')

        self.assertEndpointAccess(url)

        # create some more contacts (in addition to Joe and Frank)
        contact1 = self.create_contact("Ann", "0788000001", language='fre')
        contact2 = self.create_contact("Bob", "0788000002")
        contact3 = self.create_contact("Cat", "0788000003")
        contact4 = self.create_contact("Don", "0788000004", language='fre')

        contact1.set_field(self.user, 'nickname', "Annie", label="Nick name")
        contact4.set_field(self.user, 'nickname', "Donnie", label="Nick name")

        contact1.stop(self.user)
        contact2.block(self.user)
        contact3.release(self.user)

        # put some contacts in a group
        group = ContactGroup.get_or_create(self.org, self.admin, "Customers")
        group.update_contacts(self.user, [self.joe], add=True)  # add contacts separately for predictable modified_on
        group.update_contacts(self.user, [contact4], add=True)  # ordering

        contact1.refresh_from_db()
        contact4.refresh_from_db()
        self.joe.refresh_from_db()

        # create contact for other org
        hans = self.create_contact("Hans", "0788000004", org=self.org2)

        # no filtering
        with self.assertNumQueries(NUM_BASE_REQUEST_QUERIES + 6):
            response = self.fetchJSON(url)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json['next'], None)
        self.assertResultsByUUID(response, [contact4, self.joe, contact2, contact1, self.frank])
        self.assertEqual(response.json['results'][0], {
            'uuid': contact4.uuid,
            'name': "Don",
            'language': "fre",
            'urns': ["tel:+250788000004"],
            'groups': [{'uuid': group.uuid, 'name': group.name}],
            'fields': {'nickname': "Donnie"},
            'blocked': False,
            'stopped': False,
            'created_on': format_datetime(contact4.created_on),
            'modified_on': format_datetime(contact4.modified_on)
        })

        # filter by UUID
        response = self.fetchJSON(url, 'uuid=%s' % contact2.uuid)
        self.assertResultsByUUID(response, [contact2])

        # filter by URN
        response = self.fetchJSON(url, 'urn=tel%3A%2B250788000004')
        self.assertResultsByUUID(response, [contact4])

        # filter by group name
        response = self.fetchJSON(url, 'group=Customers')
        self.assertResultsByUUID(response, [contact4, self.joe])

        # filter by group UUID
        response = self.fetchJSON(url, 'group=%s' % group.uuid)
        self.assertResultsByUUID(response, [contact4, self.joe])

        # filter by invalid group
        response = self.fetchJSON(url, 'group=invalid')
        self.assertResultsByUUID(response, [])

        # filter by before
        response = self.fetchJSON(url, 'before=%s' % format_datetime(contact1.modified_on))
        self.assertResultsByUUID(response, [contact1, self.frank])

        # filter by after
        response = self.fetchJSON(url, 'after=%s' % format_datetime(self.joe.modified_on))
        self.assertResultsByUUID(response, [contact4, self.joe])

        # view the deleted contact
        response = self.fetchJSON(url, 'deleted=true')
        self.assertResultsByUUID(response, [contact3])
        self.assertEqual(response.json['results'][0], {
            'uuid': contact3.uuid,
            'name': None,
            'language': None,
            'urns': [],
            'groups': [],
            'fields': {},
            'blocked': None,
            'stopped': None,
            'created_on': format_datetime(contact3.created_on),
            'modified_on': format_datetime(contact3.modified_on)
        })

        with AnonymousOrg(self.org):
            # shouldn't include URNs
            response = self.fetchJSON(url, 'uuid=%s' % contact2.uuid)
            self.assertEqual(response.json['results'][0]['urns'], [])

        # try to post something other than an object
        response = self.postJSON(url, [])
        self.assertEqual(response.status_code, 400)

        # create an empty contact
        response = self.postJSON(url, {})
        self.assertEqual(response.status_code, 201)

        empty = Contact.objects.get(name=None)

        self.assertEqual(response.json, {
            'uuid': empty.uuid,
            'name': None,
            'language': None,
            'urns': [],
            'groups': [],
            'fields': {'nickname': None},
            'blocked': False,
            'stopped': False,
            'created_on': format_datetime(empty.created_on),
            'modified_on': format_datetime(empty.modified_on)
        })

        # create with all fields but empty
        response = self.postJSON(url, {'name': None, 'language': None, 'urns': [], 'groups': [], 'fields': {}})
        self.assertEqual(response.status_code, 201)

        jaqen = Contact.objects.filter(name=None, language=None).order_by('-pk').first()
        self.assertEqual(set(jaqen.urns.all()), set())
        self.assertEqual(set(jaqen.user_groups.all()), set())
        self.assertEqual(set(jaqen.values.all()), set())

        dyn_group = self.create_group("Dynamic Group", query="nickname has Ja")

        # create with all fields
        response = self.postJSON(url, {
            'name': "Jean",
            'language': "fre",
            'urns': ["tel:+250783333333", "twitter:JEAN"],
            'groups': [group.uuid],
            'fields': {'nickname': "Jado"}
        })
        self.assertEqual(response.status_code, 201)

        # URNs will be normalized
        jean = Contact.objects.filter(name="Jean", language='fre').order_by('-pk').first()
        self.assertEqual(set(jean.urns.values_list('urn', flat=True)), {"tel:+250783333333", "twitter:jean"})
        self.assertEqual(set(jean.user_groups.all()), {group, dyn_group})
        self.assertEqual(jean.get_field('nickname').string_value, "Jado")

        # create with invalid fields
        response = self.postJSON(url, {
            'name': "Jim",
            'language': "english",
            'urns': ["1234556789"],
            'groups': ["59686b4e-14bc-4160-9376-b649b218c806"],
            'fields': {'hmmm': "X"}
        })
        self.assertResponseError(response, 'language', "Ensure this field has no more than 3 characters.")
        self.assertResponseError(response, 'urns', "Invalid URN: 1234556789. Ensure phone numbers contain country codes.")
        self.assertResponseError(response, 'groups', "No such object with UUID: 59686b4e-14bc-4160-9376-b649b218c806")
        self.assertResponseError(response, 'fields', "Invalid contact field key: hmmm")

        # update an existing contact by UUID but don't provide any fields
        response = self.postJSON(url, {'uuid': jean.uuid})
        self.assertEqual(response.status_code, 201)

        # contact should be unchanged
        jean = Contact.objects.get(pk=jean.pk)
        self.assertEqual(jean.name, "Jean")
        self.assertEqual(jean.language, "fre")
        self.assertEqual(set(jean.urns.values_list('urn', flat=True)), {"tel:+250783333333", "twitter:jean"})
        self.assertEqual(set(jean.user_groups.all()), {group, dyn_group})
        self.assertEqual(jean.get_field('nickname').string_value, "Jado")

        # update by UUID and change all fields
        response = self.postJSON(url, {
            'uuid': jean.uuid,
            'name': "Jean II",
            'language': "eng",
            'urns': ["tel:+250784444444"],
            'groups': [],
            'fields': {'nickname': "John"}
        })
        self.assertEqual(response.status_code, 201)

        jean = Contact.objects.get(pk=jean.pk)
        self.assertEqual(jean.name, "Jean II")
        self.assertEqual(jean.language, "eng")
        self.assertEqual(set(jean.urns.values_list('urn', flat=True)), {'tel:+250784444444'})
        self.assertEqual(set(jean.user_groups.all()), set())
        self.assertEqual(jean.get_field('nickname').string_value, "John")

        # update by URN whilst changing URNs
        response = self.postJSON(url, {'urn': "tel:+250784444444", 'urns': ["tel:+250785555555"]})
        self.assertEqual(response.status_code, 201)

        # only URN should have changed
        jean = Contact.objects.get(pk=jean.pk)
        self.assertEqual(jean.name, "Jean II")
        self.assertEqual(jean.language, "eng")
        self.assertEqual(set(jean.urns.values_list('urn', flat=True)), {'tel:+250785555555'})
        self.assertEqual(set(jean.user_groups.all()), set())
        self.assertEqual(jean.get_field('nickname').string_value, "John")

        # create by URN identifier
        response = self.postJSON(url, {'urn': "twitter:BOBBY", 'name': "Bobby"})
        self.assertEqual(response.status_code, 201)

        # URN should be normalized
        bobby = Contact.objects.get(name="Bobby")
        self.assertEqual(set(bobby.urns.values_list('urn', flat=True)), {'twitter:bobby'})

        # if URNs list also provided, it takes precedence
        response = self.postJSON(url, {'urn': "twitter:jimmy", 'name': "Jimmy", 'urns': ["twitter:jimmy2"]})
        self.assertEqual(response.status_code, 201)

        jimmy = Contact.objects.get(name="Jimmy")
        self.assertEqual(set(jimmy.urns.values_list('urn', flat=True)), {'twitter:jimmy2'})

        # URN identifier is also normalized for updates
        response = self.postJSON(url, {'urn': "twitter:BOBBY", 'name': "Bobby II"})
        self.assertEqual(response.status_code, 201)

        bobby.refresh_from_db()
        self.assertEqual(bobby.name, "Bobby II")  # updated existing contact

        # try to create a contact with a URN belonging to another contact
        response = self.postJSON(url, {'name': "Robert", 'urns': ["twitter:bobby"]})
        self.assertEqual(response.status_code, 400)
        self.assertResponseError(response, 'non_field_errors', "Contact URN belongs to another contact: twitter:bobby")

        # try to update a contact with non-existent UUID
        response = self.postJSON(url, {'uuid': 'ad6acad9-959b-4d70-b144-5de2891e4d00'})
        self.assertResponseError(response, 'uuid', "No such contact with UUID: ad6acad9-959b-4d70-b144-5de2891e4d00")

        # try to update a contact in another org
        response = self.postJSON(url, {'uuid': hans.uuid})
        self.assertResponseError(response, 'uuid', "No such contact with UUID: %s" % hans.uuid)

        # try to add a contact to a dynamic group
        response = self.postJSON(url, {'uuid': jean.uuid, 'groups': [dyn_group.uuid]})
        self.assertResponseError(response, 'groups', "Can't add contact to dynamic group with UUID: %s" % dyn_group.uuid)

        # try to give a contact more than 100 URNs
        response = self.postJSON(url, {'uuid': jean.uuid, 'urns': ['twitter:bob%d' % u for u in range(101)]})
        self.assertResponseError(response, 'urns', "Exceeds maximum list size of 100")

        # try to move a blocked contact into a group
        jean.block(self.user)
        response = self.postJSON(url, {'uuid': jean.uuid, 'groups': [group.uuid]})
        self.assertResponseError(response, 'non_field_errors', "Blocked or stopped contacts can't be added to groups")

        with AnonymousOrg(self.org):
            # can't update via URN
            response = self.postJSON(url, {'urn': 'tel:+250785555555'})
            self.assertResponseError(response, 'urn', "Referencing by URN not allowed for anonymous organizations")

            # can't update contact URNs
            response = self.postJSON(url, {'uuid': jean.uuid, 'urns': ["tel:+250786666666"]})
            self.assertResponseError(response, 'non_field_errors',
                                     "Updating contact URNs not allowed for anonymous organizations")

            # can create with URNs
            response = self.postJSON(url, {'name': "Xavier", 'urns': ["tel:+250787777777"]})
            self.assertEqual(response.status_code, 201)

            xavier = Contact.objects.get(name="Xavier")
            self.assertEqual(set(xavier.urns.values_list('urn', flat=True)), {"tel:+250787777777"})

        # try an empty delete request
        response = self.deleteJSON(url, {})
        self.assertResponseError(response, None, "Must provide one of the following fields: urn, uuid")

        # delete a contact by UUID
        response = self.deleteJSON(url, 'uuid=%s' % jean.uuid)
        self.assertEqual(response.status_code, 204)

        jean.refresh_from_db()
        self.assertFalse(jean.is_active)

        # delete a contact by URN
        response = self.deleteJSON(url, 'urn=twitter:bobby')
        self.assertEqual(response.status_code, 204)

        bobby.refresh_from_db()
        self.assertFalse(bobby.is_active)

        # try to delete a contact in another org
        response = self.deleteJSON(url, 'uuid=%s' % hans.uuid)
        self.assertEqual(response.status_code, 404)

    def test_definitions(self):
        url = reverse('api.v2.definitions')

        self.assertEndpointAccess(url)

        self.import_file('subflow')
        flow = Flow.objects.filter(name='Parent Flow').first()

        # all flow dependencies and we should get the child flow
        response = self.fetchJSON(url, 'flow=%s' % flow.uuid)
        self.assertEqual(len(response.json['flows']), 2)
        self.assertEqual(response.json['flows'][0]['metadata']['name'], "Parent Flow")
        self.assertEqual(response.json['flows'][1]['metadata']['name'], "Child Flow")

        # export just the parent flow
        response = self.fetchJSON(url, 'flow=%s&dependencies=false' % flow.uuid)
        self.assertEqual(len(response.json['flows']), 1)
        self.assertEqual(response.json['flows'][0]['metadata']['name'], "Parent Flow")

        # import the clinic app which has campaigns
        self.import_file('the_clinic')

        # our catchall flow, all alone
        flow = Flow.objects.filter(name='Catch All').first()
        response = self.fetchJSON(url, 'flow=%s&dependencies=false' % flow.uuid)
        self.assertEqual(len(response.json['flows']), 1)
        self.assertEqual(len(response.json['campaigns']), 0)
        self.assertEqual(len(response.json['triggers']), 0)

        # with it's trigger dependency
        response = self.fetchJSON(url, 'flow_uuid=%s' % flow.uuid)
        self.assertEqual(len(response.json['flows']), 1)
        self.assertEqual(len(response.json['campaigns']), 0)
        self.assertEqual(len(response.json['triggers']), 1)

        # our registration flow, all alone
        flow = Flow.objects.filter(name='Register Patient').first()
        response = self.fetchJSON(url, 'flow=%s&dependencies=false' % flow.uuid)
        self.assertEqual(len(response.json['flows']), 1)
        self.assertEqual(len(response.json['campaigns']), 0)
        self.assertEqual(len(response.json['triggers']), 0)

        # touches a lot of stuff
        response = self.fetchJSON(url, 'flow=%s' % flow.uuid)
        self.assertEqual(len(response.json['flows']), 6)
        self.assertEqual(len(response.json['campaigns']), 1)
        self.assertEqual(len(response.json['triggers']), 2)

        # add our missed call flow
        missed_call = Flow.objects.filter(name='Missed Call').first()
        response = self.fetchJSON(url, 'flow=%s&flow=%s&dependencies=true' % (flow.uuid, missed_call.uuid))
        self.assertEqual(len(response.json['flows']), 7)
        self.assertEqual(len(response.json['campaigns']), 1)
        self.assertEqual(len(response.json['triggers']), 3)

        campaign = Campaign.objects.filter(name='Appointment Schedule').first()
        response = self.fetchJSON(url, 'campaign=%s&dependencies=false' % campaign.uuid)
        self.assertEqual(len(response.json['flows']), 0)
        self.assertEqual(len(response.json['campaigns']), 1)
        self.assertEqual(len(response.json['triggers']), 0)

        response = self.fetchJSON(url, 'campaign=%s' % campaign.uuid)
        self.assertEqual(len(response.json['flows']), 4)
        self.assertEqual(len(response.json['campaigns']), 1)
        self.assertEqual(len(response.json['triggers']), 1)

        # test deprecated param names
        response = self.fetchJSON(url, 'flow_uuid=%s&campaign_uuid=%s&dependencies=false' % (flow.uuid, campaign.uuid))
        self.assertEqual(len(response.json['flows']), 1)
        self.assertEqual(len(response.json['campaigns']), 1)
        self.assertEqual(len(response.json['triggers']), 0)

    def test_fields(self):
        url = reverse('api.v2.fields')

        self.assertEndpointAccess(url)

        ContactField.get_or_create(self.org, self.admin, 'nick_name', "Nick Name")
        ContactField.get_or_create(self.org, self.admin, 'registered', "Registered On", value_type=Value.TYPE_DATETIME)
        ContactField.get_or_create(self.org2, self.admin2, 'not_ours', "Something Else")

        # no filtering
        with self.assertNumQueries(NUM_BASE_REQUEST_QUERIES + 1):
            response = self.fetchJSON(url)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json['next'], None)
        self.assertEqual(response.json['results'], [
            {'key': 'registered', 'label': "Registered On", 'value_type': "datetime"},
            {'key': 'nick_name', 'label': "Nick Name", 'value_type': "text"}
        ])

        # filter by key
        response = self.fetchJSON(url, 'key=nick_name')
        self.assertEqual(response.json['results'], [{'key': 'nick_name', 'label': "Nick Name", 'value_type': "text"}])

    def test_flows(self):
        url = reverse('api.v2.flows')

        self.assertEndpointAccess(url)

        registration = self.create_flow(name="Registration", uuid_start=0)
        survey = self.create_flow(name="Survey", uuid_start=1000)

        # add a flow label
        reporting = FlowLabel.objects.create(org=self.org, name="Reporting")
        survey.labels.add(reporting)

        # run joe through through a flow
        survey.start([], [self.joe])
        self.create_msg(direction='I', contact=self.joe, text="it is blue").handle()

        # flow belong to other org
        self.create_flow(org=self.org2, name="Other", uuid_start=2000)

        # no filtering
        with self.assertNumQueries(NUM_BASE_REQUEST_QUERIES + 8):
            response = self.fetchJSON(url)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json['next'], None)
        self.assertEqual(response.json['results'], [
            {
                'uuid': survey.uuid,
                'name': "Survey",
                'archived': False,
                'labels': [{'uuid': reporting.uuid, 'name': "Reporting"}],
                'expires': 720,
                'runs': {'completed': 1, 'interrupted': 0, 'expired': 0},
                'created_on': format_datetime(survey.created_on)
            },
            {
                'uuid': registration.uuid,
                'name': "Registration",
                'archived': False,
                'labels': [],
                'expires': 720,
                'runs': {'completed': 0, 'interrupted': 0, 'expired': 0},
                'created_on': format_datetime(registration.created_on)
            }
        ])

        # filter by UUID
        response = self.fetchJSON(url, 'uuid=%s' % survey.uuid)
        self.assertResultsByUUID(response, [survey])

    def test_groups(self):
        url = reverse('api.v2.groups')

        self.assertEndpointAccess(url)

        customers = self.create_group("Customers", [self.frank])
        developers = self.create_group("Developers", query="isdeveloper = YES")

        # group belong to other org
        spammers = ContactGroup.get_or_create(self.org2, self.admin2, "Spammers")

        # no filtering
        with self.assertNumQueries(NUM_BASE_REQUEST_QUERIES + 3):
            response = self.fetchJSON(url)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json['next'], None)
        self.assertEqual(response.json['results'], [
            {'uuid': developers.uuid, 'name': "Developers", 'query': "isdeveloper = YES", 'count': 0},
            {'uuid': customers.uuid, 'name': "Customers", 'query': None, 'count': 1}
        ])

        # filter by UUID
        response = self.fetchJSON(url, 'uuid=%s' % customers.uuid)
        self.assertResultsByUUID(response, [customers])

        # try to create empty group
        response = self.postJSON(url, {})
        self.assertResponseError(response, 'name', "This field is required.")

        # create new group
        response = self.postJSON(url, {'name': "Reporters"})
        self.assertEqual(response.status_code, 201)

        reporters = ContactGroup.user_groups.get(name="Reporters")
        self.assertEqual(response.json, {'uuid': reporters.uuid, 'name': "Reporters", 'query': None, 'count': 0})

        # try to create another group with same name
        response = self.postJSON(url, {'name': "Reporters"})
        self.assertResponseError(response, 'non_field_errors', "Name must be unique")

        # try to create a group with invalid name
        response = self.postJSON(url, {'name': "!!!#$%^"})
        self.assertResponseError(response, 'name', "Name contains illegal characters or is longer than 64 characters")

        # try to create a group with name that's too long
        response = self.postJSON(url, {'name': "x" * 65})
        self.assertResponseError(response, 'name', "Ensure this field has no more than 64 characters.")

        # update group by UUID
        response = self.postJSON(url, {'uuid': reporters.uuid, 'name': "U-Reporters"})
        self.assertEqual(response.status_code, 201)

        reporters.refresh_from_db()
        self.assertEqual(reporters.name, "U-Reporters")

        # can't update group from other org
        response = self.postJSON(url, {'uuid': spammers.uuid, 'name': "Won't work"})
        self.assertResponseError(response, 'uuid', "No such object with UUID: %s" % spammers.uuid)

        # try an empty delete request
        response = self.deleteJSON(url, {})
        self.assertResponseError(response, None, "Must provide one of the following fields: uuid")

        # delete a group by UUID
        response = self.deleteJSON(url, 'uuid=%s' % reporters.uuid)
        self.assertEqual(response.status_code, 204)

        reporters.refresh_from_db()
        self.assertFalse(reporters.is_active)

        # try to delete a group in another org
        response = self.deleteJSON(url, 'uuid=%s' % spammers.uuid)
        self.assertEqual(response.status_code, 404)

    def test_labels(self):
        url = reverse('api.v2.labels')

        self.assertEndpointAccess(url)

        important = Label.get_or_create(self.org, self.admin, "Important")
        feedback = Label.get_or_create(self.org, self.admin, "Feedback")
        spam = Label.get_or_create(self.org2, self.admin2, "Spam")

        msg = self.create_msg(direction="I", text="Hello", contact=self.frank)
        important.toggle_label([msg], add=True)

        # no filtering
        with self.assertNumQueries(NUM_BASE_REQUEST_QUERIES + 1):
            response = self.fetchJSON(url)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json['next'], None)
        self.assertEqual(response.json['results'], [
            {'uuid': feedback.uuid, 'name': "Feedback", 'count': 0},
            {'uuid': important.uuid, 'name': "Important", 'count': 1}
        ])

        # filter by UUID
        response = self.fetchJSON(url, 'uuid=%s' % feedback.uuid)
        self.assertEqual(response.json['results'], [{'uuid': feedback.uuid, 'name': "Feedback", 'count': 0}])

        # try to create empty label
        response = self.postJSON(url, {})
        self.assertResponseError(response, 'name', "This field is required.")

        # create new label
        response = self.postJSON(url, {'name': "Interesting"})
        self.assertEqual(response.status_code, 201)

        interesting = Label.label_objects.get(name="Interesting")
        self.assertEqual(response.json, {'uuid': interesting.uuid, 'name': "Interesting", 'count': 0})

        # try to create another label with same name
        response = self.postJSON(url, {'name': "Interesting"})
        self.assertResponseError(response, 'non_field_errors', "Name must be unique")

        # try to create a label with invalid name
        response = self.postJSON(url, {'name': "!!!#$%^"})
        self.assertResponseError(response, 'name', "Name contains illegal characters or is longer than 64 characters")

        # try to create a label with name that's too long
        response = self.postJSON(url, {'name': "x" * 65})
        self.assertResponseError(response, 'name', "Ensure this field has no more than 64 characters.")

        # update label by UUID
        response = self.postJSON(url, {'uuid': interesting.uuid, 'name': "More Interesting"})
        self.assertEqual(response.status_code, 201)

        interesting.refresh_from_db()
        self.assertEqual(interesting.name, "More Interesting")

        # can't update label from other org
        response = self.postJSON(url, {'uuid': spam.uuid, 'name': "Won't work"})
        self.assertResponseError(response, 'uuid', "No such object with UUID: %s" % spam.uuid)

        # try an empty delete request
        response = self.deleteJSON(url, {})
        self.assertResponseError(response, None, "Must provide one of the following fields: uuid")

        # delete a label by UUID
        response = self.deleteJSON(url, 'uuid=%s' % interesting.uuid)
        self.assertEqual(response.status_code, 204)

        self.assertFalse(Label.label_objects.filter(uuid=interesting.uuid).exists())  # label deletes are for real

        # try to delete a label in another org
        response = self.deleteJSON(url, 'uuid=%s' % spam.uuid)
        self.assertEqual(response.status_code, 404)

    def assertMsgEqual(self, msg_json, msg, msg_type, msg_status, msg_visibility):
        self.assertEqual(msg_json, {
            'id': msg.pk,
            'broadcast': msg.broadcast,
            'contact': {'uuid': msg.contact.uuid, 'name': msg.contact.name},
            'urn': msg.contact_urn.urn,
            'channel': {'uuid': msg.channel.uuid, 'name': msg.channel.name},
            'direction': "in" if msg.direction == 'I' else "out",
            'type': msg_type,
            'status': msg_status,
            'archived': msg.visibility == 'A',
            'visibility': msg_visibility,
            'text': msg.text,
            'labels': [dict(name=l.name, uuid=l.uuid) for l in msg.labels.all()],
            'created_on': format_datetime(msg.created_on),
            'sent_on': format_datetime(msg.sent_on),
            'modified_on': format_datetime(msg.modified_on)
        })

    def test_messages(self):
        url = reverse('api.v2.messages')

        # make sure user rights are correct
        self.assertEndpointAccess(url, "folder=inbox")

        # make sure you have to pass in something to filter by
        response = self.fetchJSON(url)
        self.assertResponseError(response, None,
                                 "You must specify one of the contact, folder, label, broadcast, id parameters")

        # create some messages
        joe_msg1 = self.create_msg(direction='I', msg_type='F', text="Howdy", contact=self.joe)
        frank_msg1 = self.create_msg(direction='I', msg_type='I', text="Bonjour", contact=self.frank, channel=self.twitter)
        joe_msg2 = self.create_msg(direction='O', msg_type='I', text="How are you?", contact=self.joe, status='Q')
        frank_msg2 = self.create_msg(direction='O', msg_type='I', text="Ça va?", contact=self.frank, status='D')
        joe_msg3 = self.create_msg(direction='I', msg_type='F', text="Good", contact=self.joe)
        frank_msg3 = self.create_msg(direction='I', msg_type='I', text="Bien", contact=self.frank, channel=self.twitter, visibility='A')

        # add a surveyor message (no URN etc)
        joe_msg4 = self.create_msg(direction='O', msg_type='F', text="Surveys!", contact=self.joe, contact_urn=None,
                                   status='S', channel=None, sent_on=timezone.now())

        # add a deleted message
        deleted_msg = self.create_msg(direction='I', msg_type='I', text="!@$!%", contact=self.frank, visibility='D')

        # add a test contact message
        self.create_msg(direction='I', msg_type='F', text="Hello", contact=self.test_contact)

        # add message in other org
        self.create_msg(direction='I', msg_type='I', text="Guten tag!", contact=self.hans, org=self.org2)

        # label some of the messages, this will change our modified on as well for our `incoming` view
        label = Label.get_or_create(self.org, self.admin, "Spam")

        # we do this in two calls so that we can predict ordering later
        label.toggle_label([frank_msg3], add=True)
        label.toggle_label([frank_msg1], add=True)
        label.toggle_label([joe_msg3], add=True)

        frank_msg1.refresh_from_db(fields=('modified_on',))
        joe_msg3.refresh_from_db(fields=('modified_on',))

        # filter by inbox
        with self.assertNumQueries(NUM_BASE_REQUEST_QUERIES + 6):
            response = self.fetchJSON(url, 'folder=INBOX')

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json['next'], None)
        self.assertResultsById(response, [frank_msg1])
        self.assertMsgEqual(response.json['results'][0], frank_msg1, msg_type='inbox', msg_status='queued', msg_visibility='visible')

        # filter by incoming, should get deleted messages too
        with self.assertNumQueries(NUM_BASE_REQUEST_QUERIES + 6):
            response = self.fetchJSON(url, 'folder=incoming')

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json['next'], None)
        self.assertResultsById(response, [joe_msg3, frank_msg1, frank_msg3, deleted_msg, joe_msg1])
        self.assertMsgEqual(response.json['results'][0], joe_msg3, msg_type='flow', msg_status='queued', msg_visibility='visible')

        # filter by folder (flow)
        response = self.fetchJSON(url, 'folder=flows')
        self.assertResultsById(response, [joe_msg3, joe_msg1])

        # filter by folder (archived)
        response = self.fetchJSON(url, 'folder=archived')
        self.assertResultsById(response, [frank_msg3])

        # filter by folder (outbox)
        response = self.fetchJSON(url, 'folder=outbox')
        self.assertResultsById(response, [joe_msg2])

        # filter by folder (sent)
        response = self.fetchJSON(url, 'folder=sent')
        self.assertResultsById(response, [joe_msg4, frank_msg2])

        # filter by invalid view
        response = self.fetchJSON(url, 'folder=invalid')
        self.assertResultsById(response, [])

        # filter by id
        response = self.fetchJSON(url, 'id=%d' % joe_msg3.pk)
        self.assertResultsById(response, [joe_msg3])

        # filter by contact
        response = self.fetchJSON(url, 'contact=%s' % self.joe.uuid)
        self.assertResultsById(response, [joe_msg4, joe_msg3, joe_msg2, joe_msg1])

        # filter by invalid contact
        response = self.fetchJSON(url, 'contact=invalid')
        self.assertResultsById(response, [])

        # filter by label name
        response = self.fetchJSON(url, 'label=Spam')
        self.assertResultsById(response, [joe_msg3, frank_msg1])

        # filter by label UUID
        response = self.fetchJSON(url, 'label=%s' % label.uuid)
        self.assertResultsById(response, [joe_msg3, frank_msg1])

        # filter by invalid label
        response = self.fetchJSON(url, 'label=invalid')
        self.assertResultsById(response, [])

        # filter by before (inclusive)
        response = self.fetchJSON(url, 'folder=incoming&before=%s' % format_datetime(frank_msg1.modified_on))
        self.assertResultsById(response, [frank_msg1, frank_msg3, deleted_msg, joe_msg1])

        # filter by after (inclusive)
        response = self.fetchJSON(url, 'folder=incoming&after=%s' % format_datetime(frank_msg1.modified_on))
        self.assertResultsById(response, [joe_msg3, frank_msg1])

        # filter by broadcast
        broadcast = Broadcast.create(self.org, self.user, "A beautiful broadcast", [self.joe, self.frank])
        broadcast.send()
        response = self.fetchJSON(url, 'broadcast=%s' % broadcast.pk)

        expected = {m.pk for m in broadcast.msgs.all()}
        results = {m['id'] for m in response.json['results']}
        self.assertEqual(expected, results)

        # can't filter by more than one of contact, folder, label or broadcast together
        for query in ('contact=%s&label=Spam' % self.joe.uuid, 'label=Spam&folder=inbox',
                      'broadcast=12345&folder=inbox', 'broadcast=12345&label=Spam'):
            response = self.fetchJSON(url, query)
            self.assertResponseError(response, None,
                                     "You may only specify one of the contact, folder, label, broadcast parameters")

        with AnonymousOrg(self.org):
            # for anon orgs, don't return URN values
            response = self.fetchJSON(url, 'id=%d' % joe_msg3.pk)
            self.assertIsNone(response.json['results'][0]['urn'])

    def test_org(self):
        url = reverse('api.v2.org')

        self.assertEndpointAccess(url)

        # fetch as JSON
        response = self.fetchJSON(url)
        self.assertEqual(200, response.status_code)
        self.assertEqual(response.json, {
            'name': "Temba",
            'country': "RW",
            'languages': [],
            'primary_language': None,
            'timezone': "Africa/Kigali",
            'date_style': "day_first",
            'anon': False
        })

        self.org.set_languages(self.admin, ['eng', 'fre'], 'eng')

        response = self.fetchJSON(url)
        self.assertEqual(response.json, {
            'name': "Temba",
            'country': "RW",
            'languages': ["eng", "fre"],
            'primary_language': "eng",
            'timezone': "Africa/Kigali",
            'date_style': "day_first",
            'anon': False
        })

    def test_media(self):
        url = reverse('api.v2.media') + '.json'

        self.login(self.admin)

        def assert_media_upload(filename, ext):
            with open(filename, 'rb') as data:

                post_data = dict(media_file=data, extension=ext, HTTP_X_FORWARDED_HTTPS='https')
                response = self.client.post(url, post_data)

                self.assertEqual(response.status_code, 201)
                location = json.loads(response.content).get('location', None)
                self.assertIsNotNone(location)

                starts_with = 'https://%s/%s/%d/media/' % (settings.AWS_BUCKET_DOMAIN, settings.STORAGE_ROOT_DIR, self.org.pk)
                self.assertEqual(starts_with, location[0:len(starts_with)])
                self.assertEqual('.%s' % ext, location[-4:])

        assert_media_upload('%s/test_media/steve.marten.jpg' % settings.MEDIA_ROOT, 'jpg')
        assert_media_upload('%s/test_media/snow.mp4' % settings.MEDIA_ROOT, 'mp4')

        # missing file
        response = self.client.post(url, dict(), HTTP_X_FORWARDED_HTTPS='https')
        self.assertEqual(response.status_code, 400)
        self.clear_storage()

    def test_runs(self):
        url = reverse('api.v2.runs')

        self.assertEndpointAccess(url)

        # allow Frank to run the flow in French
        self.org.set_languages(self.admin, ['eng', 'fre'], 'eng')
        self.frank.language = 'fre'
        self.frank.save()

        flow1 = self.create_flow(uuid_start=0)
        flow2 = Flow.copy(flow1, self.user)

        joe_run1, = flow1.start([], [self.joe])
        frank_run1, = flow1.start([], [self.frank])
        self.create_msg(direction='I', contact=self.joe, text="it is blue").handle()
        self.create_msg(direction='I', contact=self.frank, text="Indigo").handle()

        joe_run2, = flow1.start([], [self.joe], restart_participants=True)
        frank_run2, = flow1.start([], [self.frank], restart_participants=True)
        joe_run3, = flow2.start([], [self.joe], restart_participants=True)

        # add a test contact run
        Contact.set_simulation(True)
        flow2.start([], [self.test_contact])
        Contact.set_simulation(False)

        # add a run for another org
        flow3 = self.create_flow(org=self.org2, user=self.admin2, uuid_start=10000)
        flow3.start([], [self.hans])

        # refresh runs which will have been modified by being interrupted
        joe_run1.refresh_from_db()
        joe_run2.refresh_from_db()
        frank_run1.refresh_from_db()
        frank_run2.refresh_from_db()

        # no filtering
        with self.assertNumQueries(NUM_BASE_REQUEST_QUERIES + 7):
            response = self.fetchJSON(url)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json['next'], None)
        self.assertResultsById(response, [joe_run3, joe_run2, frank_run2, frank_run1, joe_run1])

        joe_run1_steps = list(joe_run1.steps.order_by('pk'))
        frank_run2_steps = list(frank_run2.steps.order_by('pk'))

        joe_msgs = list(Msg.objects.filter(contact=self.joe).order_by('pk'))
        frank_msgs = list(Msg.objects.filter(contact=self.frank).order_by('pk'))

        self.assertEqual(response.json['results'][2], {
            'id': frank_run2.pk,
            'flow': {'uuid': flow1.uuid, 'name': "Color Flow"},
            'contact': {'uuid': self.frank.uuid, 'name': self.frank.name},
            'responded': False,
            'steps': [
                {
                    'node': "00000000-00000000-00000000-00000001",
                    'arrived_on': format_datetime(frank_run2_steps[0].arrived_on),
                    'left_on': format_datetime(frank_run2_steps[0].left_on),
                    'messages': [
                        {
                            'id': frank_msgs[3].id,
                            'broadcast': frank_msgs[3].broadcast_id,
                            'text': "Quelle est votre couleur préférée?"
                        }
                    ],
                    'text': "Quelle est votre couleur préférée?",
                    'value': None,
                    'category': None,
                    'type': 'actionset'
                },
                {
                    'node': "00000000-00000000-00000000-00000005",
                    'arrived_on': format_datetime(frank_run2_steps[1].arrived_on),
                    'left_on': None,
                    'messages': [],
                    'text': None,
                    'value': None,
                    'category': None,
                    'type': 'ruleset'
                }
            ],
            'created_on': format_datetime(frank_run2.created_on),
            'modified_on': format_datetime(frank_run2.modified_on),
            'exited_on': None,
            'exit_type': None
        })
        self.assertEqual(response.json['results'][4], {
            'id': joe_run1.pk,
            'flow': {'uuid': flow1.uuid, 'name': "Color Flow"},
            'contact': {'uuid': self.joe.uuid, 'name': self.joe.name},
            'responded': True,
            'steps': [
                {
                    'node': "00000000-00000000-00000000-00000001",
                    'arrived_on': format_datetime(joe_run1_steps[0].arrived_on),
                    'left_on': format_datetime(joe_run1_steps[0].left_on),
                    'messages': [
                        {
                            'id': joe_msgs[0].id,
                            'broadcast': joe_msgs[0].broadcast_id,
                            'text': "What is your favorite color?"
                        }
                    ],
                    'text': "What is your favorite color?",
                    'value': None,
                    'category': None,
                    'type': 'actionset'
                },
                {
                    'node': "00000000-00000000-00000000-00000005",
                    'arrived_on': format_datetime(joe_run1_steps[1].arrived_on),
                    'left_on': format_datetime(joe_run1_steps[1].left_on),
                    'messages': [
                        {
                            'id': joe_msgs[1].id,
                            'broadcast': joe_msgs[1].broadcast_id,
                            'text': "it is blue"
                        }
                    ],
                    'text': "it is blue",
                    'value': 'blue',
                    'category': "Blue",
                    'type': 'ruleset'
                },
                {
                    'node': "00000000-00000000-00000000-00000003",
                    'arrived_on': format_datetime(joe_run1_steps[2].arrived_on),
                    'left_on': format_datetime(joe_run1_steps[2].left_on),
                    'messages': [
                        {
                            'id': joe_msgs[2].id,
                            'broadcast': joe_msgs[2].broadcast_id,
                            'text': "Blue is sad. :("
                        }
                    ],
                    'text': "Blue is sad. :(",
                    'value': None,
                    'category': None,
                    'type': 'actionset'
                }
            ],
            'created_on': format_datetime(joe_run1.created_on),
            'modified_on': format_datetime(joe_run1.modified_on),
            'exited_on': format_datetime(joe_run1.exited_on),
            'exit_type': 'completed'
        })

        # check when all broadcasts have been purged
        Broadcast.objects.all().update(purged=True)
        Msg.objects.filter(direction='O').delete()

        with self.assertNumQueries(NUM_BASE_REQUEST_QUERIES + 9):
            response = self.fetchJSON(url)

        self.assertEqual(response.json['results'][2]['steps'][0]['messages'], [
            {
                'id': None,
                'broadcast': frank_msgs[3].broadcast_id,
                'text': "Quelle est votre couleur préférée?"
            }
        ])
        self.assertEqual(response.json['results'][2]['steps'][0]['text'], "Quelle est votre couleur préférée?")

        # filter by id
        response = self.fetchJSON(url, 'id=%d' % frank_run2.pk)
        self.assertResultsById(response, [frank_run2])

        # filter by flow
        response = self.fetchJSON(url, 'flow=%s' % flow1.uuid)
        self.assertResultsById(response, [joe_run2, frank_run2, frank_run1, joe_run1])

        # doesn't work if flow is inactive
        flow1.is_active = False
        flow1.save()

        response = self.fetchJSON(url, 'flow=%s' % flow1.uuid)
        self.assertResultsById(response, [])

        # restore to active
        flow1.is_active = True
        flow1.save()

        # filter by invalid flow
        response = self.fetchJSON(url, 'flow=invalid')
        self.assertResultsById(response, [])

        # filter by flow + responded
        response = self.fetchJSON(url, 'flow=%s&responded=TrUe' % flow1.uuid)
        self.assertResultsById(response, [frank_run1, joe_run1])

        # filter by contact
        response = self.fetchJSON(url, 'contact=%s' % self.joe.uuid)
        self.assertResultsById(response, [joe_run3, joe_run2, joe_run1])

        # filter by invalid contact
        response = self.fetchJSON(url, 'contact=invalid')
        self.assertResultsById(response, [])

        # filter by contact + responded
        response = self.fetchJSON(url, 'contact=%s&responded=yes' % self.joe.uuid)
        self.assertResultsById(response, [joe_run1])

        # filter by after
        response = self.fetchJSON(url, 'after=%s' % format_datetime(frank_run1.modified_on))
        self.assertResultsById(response, [joe_run3, joe_run2, frank_run2, frank_run1])

        # filter by before
        response = self.fetchJSON(url, 'before=%s' % format_datetime(frank_run1.modified_on))
        self.assertResultsById(response, [frank_run1, joe_run1])

        # filter by invalid before
        response = self.fetchJSON(url, 'before=longago')
        self.assertResultsById(response, [])

        # filter by invalid after
        response = self.fetchJSON(url, 'before=%s&after=thefuture' % format_datetime(frank_run1.modified_on))
        self.assertResultsById(response, [])

        # can't filter by both contact and flow together
        response = self.fetchJSON(url, 'contact=%s&flow=%s' % (self.joe.uuid, flow1.uuid))
        self.assertResponseError(response, None,
                                 "You may only specify one of the contact, flow parameters")

    def test_resthooks(self):
        url = reverse('api.v2.resthooks')
        self.assertEndpointAccess(url)

        # create some resthooks
        resthook1 = Resthook.get_or_create(self.org, 'new-mother', self.admin)
        resthook2 = Resthook.get_or_create(self.org, 'new-father', self.admin)
        resthook3 = Resthook.get_or_create(self.org, 'not-active', self.admin)
        resthook3.is_active = False
        resthook3.save()

        # create a resthook for another org
        other_org_resthook = Resthook.get_or_create(self.org2, 'spam', self.admin2)

        # no filtering
        with self.assertNumQueries(NUM_BASE_REQUEST_QUERIES + 1):
            response = self.fetchJSON(url)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json['next'], None)
        self.assertEqual(response.json['results'], [
            {
                'resthook': 'new-father',
                'created_on': format_datetime(resthook2.created_on),
                'modified_on': format_datetime(resthook2.modified_on),
            },
            {
                'resthook': 'new-mother',
                'created_on': format_datetime(resthook1.created_on),
                'modified_on': format_datetime(resthook1.modified_on),
            }
        ])

        # ok, let's look at subscriptions
        url = reverse('api.v2.resthook_subscribers')
        self.assertEndpointAccess(url)

        # try to create empty subscription
        response = self.postJSON(url, {})
        self.assertResponseError(response, 'resthook', "This field is required.")
        self.assertResponseError(response, 'target_url', "This field is required.")

        # try to create one for resthook in other org
        response = self.postJSON(url, dict(resthook='spam', target_url='https://foo.bar/'))
        self.assertResponseError(response, 'resthook', "No resthook with slug: spam")

        # create subscribers on each resthook
        response = self.postJSON(url, dict(resthook='new-mother', target_url='https://foo.bar/mothers'))
        self.assertEqual(response.status_code, 201)
        response = self.postJSON(url, dict(resthook='new-father', target_url='https://foo.bar/fathers'))
        self.assertEqual(response.status_code, 201)

        hook1_subscriber = resthook1.subscribers.get()
        hook2_subscriber = resthook2.subscribers.get()

        self.assertEqual(response.json, {
            'id': hook2_subscriber.id,
            'resthook': "new-father",
            'target_url': "https://foo.bar/fathers",
            'created_on': format_datetime(hook2_subscriber.created_on),
        })

        # create a subscriber on our other resthook
        other_org_subscriber = other_org_resthook.add_subscriber('https://bar.foo', self.admin2)

        # no filtering
        with self.assertNumQueries(NUM_BASE_REQUEST_QUERIES + 1):
            response = self.fetchJSON(url)

        self.assertEqual(response.json['results'], [
            {
                'id': hook2_subscriber.id,
                'resthook': "new-father",
                'target_url': "https://foo.bar/fathers",
                'created_on': format_datetime(hook2_subscriber.created_on),
            },
            {
                'id': hook1_subscriber.id,
                'resthook': "new-mother",
                'target_url': "https://foo.bar/mothers",
                'created_on': format_datetime(hook1_subscriber.created_on),
            }
        ])

        # filter by id
        response = self.fetchJSON(url, 'id=%d' % hook1_subscriber.id)
        self.assertResultsById(response, [hook1_subscriber])

        # filter by resthook
        response = self.fetchJSON(url, 'resthook=new-father')
        self.assertResultsById(response, [hook2_subscriber])

        # remove a subscriber
        response = self.deleteJSON(url, "id=%d" % hook2_subscriber.id)
        self.assertEqual(response.status_code, 204)

        # subscriber should no longer be active
        hook2_subscriber.refresh_from_db()
        self.assertFalse(hook2_subscriber.is_active)

        # try to delete without providing id
        response = self.deleteJSON(url, "")
        self.assertResponseError(response, None, "Must provide one of the following fields: id")

        # try to delete a subscriber from another org
        response = self.deleteJSON(url, "id=%d" % other_org_subscriber.id)
        self.assertEqual(response.status_code, 404)

        # ok, let's look at the events on this resthook
        url = reverse('api.v2.resthook_events')
        self.assertEndpointAccess(url)

        # create some events on our resthooks
        event1 = WebHookEvent.objects.create(org=self.org, resthook=resthook1, event='F',
                                             data=json.dumps(dict(event='new mother',
                                                                  values=json.dumps(dict(name="Greg")),
                                                                  steps=json.dumps(dict(uuid='abcde')))),
                                             created_by=self.admin, modified_by=self.admin)
        event2 = WebHookEvent.objects.create(org=self.org, resthook=resthook2, event='F',
                                             data=json.dumps(dict(event='new father',
                                                                  values=json.dumps(dict(name="Yo")),
                                                                  steps=json.dumps(dict(uuid='12345')))),
                                             created_by=self.admin, modified_by=self.admin)

        # no filtering
        with self.assertNumQueries(NUM_BASE_REQUEST_QUERIES + 1):
            response = self.fetchJSON(url)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json['results'], [
            {
                'resthook': "new-father",
                'created_on': format_datetime(event2.created_on),
                'data': dict(event='new father', values=dict(name="Yo"), steps=dict(uuid='12345'))
            },
            {
                'resthook': "new-mother",
                'created_on': format_datetime(event1.created_on),
                'data': dict(event='new mother', values=dict(name="Greg"), steps=dict(uuid='abcde'))
            }
        ])

    def test_flow_starts(self):
        url = reverse('api.v2.flow_starts')
        self.assertEndpointAccess(url)

        flow = self.get_flow('favorites')

        # update our flow to use @extra.first_name and @extra.last_name
        first_action = flow.action_sets.all().order_by('y')[0]
        first_action.actions = json.dumps([ReplyAction(dict(base="Hi @extra.first_name @extra.last_name, "
                                                                 "what's your favorite color?")).as_json()])
        first_action.save()

        # try to create an empty flow start
        response = self.postJSON(url, {})
        self.assertResponseError(response, 'flow', "This field is required.")

        # start a flow with the minimum required parameters
        response = self.postJSON(url, {'flow': flow.uuid, 'contacts': [self.joe.uuid]})
        self.assertEqual(response.status_code, 201)

        start1 = flow.starts.get(pk=response.json['id'])
        self.assertEqual(start1.flow, flow)
        self.assertEqual(set(start1.contacts.all()), {self.joe})
        self.assertEqual(set(start1.groups.all()), set())
        self.assertTrue(start1.restart_participants)
        self.assertIsNone(start1.extra)

        # check our first msg
        msg = Msg.objects.get(direction='O', contact=self.joe)
        self.assertEqual("Hi @extra.first_name @extra.last_name, what's your favorite color?", msg.text)

        # start a flow with all parameters
        extra = dict(first_name="Ryan", last_name="Lewis")
        hans_group = self.create_group("hans", contacts=[self.hans])
        response = self.postJSON(url, dict(urns=['tel:+12067791212'],
                                           contacts=[self.joe.uuid],
                                           groups=[hans_group.uuid],
                                           flow=flow.uuid,
                                           restart_participants=False,
                                           extra=extra))

        self.assertEqual(response.status_code, 201)

        # assert our new start
        start2 = flow.starts.get(pk=response.json['id'])
        self.assertEqual(start2.flow, flow)
        self.assertTrue(start2.contacts.filter(urns__path='+12067791212'))
        self.assertTrue(start2.contacts.filter(id=self.joe.id))
        self.assertTrue(start2.groups.filter(id=hans_group.id))
        self.assertFalse(start2.restart_participants)
        self.assertTrue(start2.extra, extra)

        msg = Msg.objects.get(direction='O', contact__urns__path='+12067791212')
        self.assertEqual("Hi Ryan Lewis, what's your favorite color?", msg.text)

        # try to start a flow with no contact/group/URN
        response = self.postJSON(url, dict(flow=flow.uuid, restart_participants=True))
        self.assertResponseError(response, 'non_field_errors', "Must specify at least one group, contact or URN")

        # invalid URN
        response = self.postJSON(url, dict(flow=flow.uuid, restart_participants=True, urns=['foo:bar'],
                                           contacts=[self.joe.uuid]))
        self.assertEqual(response.status_code, 400)

        # invalid contact uuid
        response = self.postJSON(url, dict(flow=flow.uuid, restart_participants=True, urns=['tel:+12067791212'],
                                           contacts=['abcde']))
        self.assertEqual(response.status_code, 400)

        # invalid group uuid
        response = self.postJSON(url, dict(flow=flow.uuid, restart_participants=True, urns=['tel:+12067791212'],
                                           groups=['abcde']))
        self.assertResponseError(response, 'groups', "No such object with UUID: abcde")

        # invalid flow uuid
        response = self.postJSON(url, dict(flow='abcde', restart_participants=True, urns=['tel:+12067791212']))
        self.assertResponseError(response, 'flow', "No such object with UUID: abcde")

        # too many groups
        group_uuids = []
        for g in range(101):
            group_uuids.append(self.create_group("Group %d" % g).uuid)

        response = self.postJSON(url, dict(flow=flow.uuid, restart_participants=True, groups=group_uuids))
        self.assertResponseError(response, 'groups', "Exceeds maximum list size of 100")

        # check our list
        anon_contact = Contact.objects.get(urns__path="+12067791212")

        response = self.fetchJSON(url)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json['next'], None)
        self.assertResultsById(response, [start2, start1])
        self.assertEqual(response.json['results'][0], {
            'id': start2.id,
            'flow': {'uuid': flow.uuid, 'name': 'Favorites'},
            'contacts': [
                {'uuid': self.joe.uuid, 'name': 'Joe Blow'},
                {'uuid': anon_contact.uuid, 'name': None}
            ],
            'groups': [
                {'uuid': hans_group.uuid, 'name': 'hans'}
            ],
            'restart_participants': False,
            'status': 'complete',
            'extra': {"first_name": "Ryan", "last_name": "Lewis"},
            'created_on': format_datetime(start2.created_on),
            'modified_on': format_datetime(start2.modified_on),
        })
