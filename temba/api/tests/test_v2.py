# -*- coding: utf-8 -*-
from __future__ import absolute_import, unicode_literals

import json
import pytz

from datetime import datetime
from django.contrib.auth.models import Group
from django.core.urlresolvers import reverse
from django.conf import settings
from django.db import connection
from django.test import override_settings
from django.utils import timezone
from rest_framework.test import APIClient
from mock import patch
from temba.campaigns.models import Campaign, CampaignEvent
from temba.channels.models import Channel, ChannelEvent
from temba.contacts.models import Contact, ContactGroup, ContactField
from temba.flows.models import Flow, FlowRun
from temba.msgs.models import Broadcast, Label
from temba.orgs.models import Language
from temba.tests import TembaTest, AnonymousOrg
from temba.values.models import Value
from ..models import APIToken
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

    def assertEndpointAccess(self, url, query=None):
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
        # self.assertEqual(client.get(reverse('api.v1.flows') + '.json').status_code, 200)  # TODO re-enable when added
        self.assertEqual(client.get(reverse('api.v2.contacts') + '.json').status_code, 200)
        self.assertEqual(client.get(reverse('api.v2.fields') + '.json').status_code, 200)
        self.assertEqual(client.get(reverse('api.v2.campaigns') + '.json').status_code, 403)

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
        with self.assertNumQueries(NUM_BASE_REQUEST_QUERIES + 5):
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
        contact4 = self.create_contact("Don", "0788000004")

        contact1.set_field(self.user, 'nickname', "Annie", label="Nick name")

        contact1.fail()
        contact2.block(self.user)
        contact3.release(self.user)

        # put some contacts in a group
        group = ContactGroup.get_or_create(self.org, self.admin, "Customers")
        group.update_contacts(self.user, [self.joe], add=True)  # add contacts separately for predictable modified_on
        group.update_contacts(self.user, [contact1], add=True)  # ordering

        contact1.refresh_from_db()
        self.joe.refresh_from_db()

        # no filtering
        with self.assertNumQueries(NUM_BASE_REQUEST_QUERIES + 7):
            response = self.fetchJSON(url)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json['next'], None)
        self.assertResultsByUUID(response, [contact1, self.joe, contact2, contact4, self.frank])
        self.assertEqual(response.json['results'][0], {
            'uuid': contact1.uuid,
            'name': "Ann",
            'language': "fre",
            'urns': ["tel:+250788000001"],
            'groups': [{'uuid': group.uuid, 'name': group.name}],
            'fields': {'nickname': "Annie"},
            'blocked': False,
            'failed': True,
            'created_on': format_datetime(contact1.created_on),
            'modified_on': format_datetime(contact1.modified_on)
        })

        # filter by UUID
        response = self.fetchJSON(url, 'uuid=%s' % contact2.uuid)
        self.assertResultsByUUID(response, [contact2])

        # filter by URN
        response = self.fetchJSON(url, 'urn=tel%3A%2B250788000004')
        self.assertResultsByUUID(response, [contact4])

        # filter by group name
        response = self.fetchJSON(url, 'group=Customers')
        self.assertResultsByUUID(response, [contact1, self.joe])

        # filter by group UUID
        response = self.fetchJSON(url, 'group=%s' % group.uuid)
        self.assertResultsByUUID(response, [contact1, self.joe])

        # filter by invalid group
        response = self.fetchJSON(url, 'group=invalid')
        self.assertResultsByUUID(response, [])

        # filter by before
        response = self.fetchJSON(url, 'before=%s' % format_datetime(contact4.modified_on))
        self.assertResultsByUUID(response, [contact4, self.frank])

        # filter by after
        response = self.fetchJSON(url, 'after=%s' % format_datetime(self.joe.modified_on))
        self.assertResultsByUUID(response, [contact1, self.joe])

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

    def test_groups(self):
        url = reverse('api.v2.groups')

        self.assertEndpointAccess(url)

        customers = self.create_group("Customers", [self.frank])
        developers = self.create_group("Developers", query="isdeveloper = YES")

        # group belong to other org
        ContactGroup.get_or_create(self.org2, self.admin2, "Spammers")

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

    def test_labels(self):
        url = reverse('api.v2.labels')

        self.assertEndpointAccess(url)

        important = Label.get_or_create(self.org, self.admin, "Important")
        feedback = Label.get_or_create(self.org, self.admin, "Feedback")
        Label.get_or_create(self.org2, self.admin2, "Spam")

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
        with self.assertNumQueries(NUM_BASE_REQUEST_QUERIES + 7):
            response = self.fetchJSON(url, 'folder=INBOX')

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json['next'], None)
        self.assertResultsById(response, [frank_msg1])
        self.assertMsgEqual(response.json['results'][0], frank_msg1, msg_type='inbox', msg_status='queued', msg_visibility='visible')

        # filter by incoming, should get deleted messages too
        with self.assertNumQueries(NUM_BASE_REQUEST_QUERIES + 7):
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

        eng = Language.create(self.org, self.admin, "English", 'eng')
        Language.create(self.org, self.admin, "French", 'fre')
        self.org.primary_language = eng
        self.org.save()

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

    def test_runs_offset(self):
        url = reverse('api.v2.runs')

        self.assertEndpointAccess(url)

        flow1 = self.create_flow(uuid_start=0)

        for i in range(600):
            FlowRun.create(flow1, self.joe.pk)

        with patch.object(timezone, 'now', return_value=datetime(2015, 9, 15, 0, 0, 0, 0, pytz.UTC)):
            now = timezone.now()
            for r in FlowRun.objects.all():
                r.modified_on = now
                r.save()

        with self.settings(CURSOR_PAGINATION_OFFSET_CUTOFF=10):
            response = self.fetchJSON(url)
            self.assertEqual(len(response.json['results']), 250)
            self.assertTrue(response.json['next'])

            query = response.json['next'].split('?')[1]
            response = self.fetchJSON(url, query=query)

            self.assertEqual(len(response.json['results']), 250)
            self.assertTrue(response.json['next'])

            query = response.json['next'].split('?')[1]
            response = self.fetchJSON(url, query=query)

            self.assertEqual(len(response.json['results']), 250)
            self.assertTrue(response.json['next'])

            query = response.json['next'].split('?')[1]
            response = self.fetchJSON(url, query=query)

            self.assertEqual(len(response.json['results']), 250)
            self.assertTrue(response.json['next'])

        with self.settings(CURSOR_PAGINATION_OFFSET_CUTOFF=400):
            url = reverse('api.v2.runs')
            response = self.fetchJSON(url)
            self.assertEqual(len(response.json['results']), 250)
            self.assertTrue(response.json['next'])

            query = response.json['next'].split('?')[1]
            response = self.fetchJSON(url, query=query)

            self.assertEqual(len(response.json['results']), 250)
            self.assertTrue(response.json['next'])

            query = response.json['next'].split('?')[1]
            response = self.fetchJSON(url, query=query)

            self.assertEqual(len(response.json['results']), 200)
            self.assertIsNone(response.json['next'])

        with self.settings(CURSOR_PAGINATION_OFFSET_CUTOFF=5000):
            url = reverse('api.v2.runs')
            response = self.fetchJSON(url)
            self.assertEqual(len(response.json['results']), 250)
            self.assertTrue(response.json['next'])

            query = response.json['next'].split('?')[1]
            response = self.fetchJSON(url, query=query)

            self.assertEqual(len(response.json['results']), 250)
            self.assertTrue(response.json['next'])

            query = response.json['next'].split('?')[1]
            response = self.fetchJSON(url, query=query)

            self.assertEqual(len(response.json['results']), 100)
            self.assertIsNone(response.json['next'])

    def test_runs(self):
        url = reverse('api.v2.runs')

        self.assertEndpointAccess(url)

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

        # no filtering
        with self.assertNumQueries(NUM_BASE_REQUEST_QUERIES + 6):
            response = self.fetchJSON(url)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json['next'], None)
        self.assertResultsById(response, [joe_run3, frank_run2, frank_run1, joe_run2, joe_run1])

        joe_run1_steps = list(joe_run1.steps.order_by('pk'))
        frank_run2_steps = list(frank_run2.steps.order_by('pk'))

        self.assertEqual(response.json['results'][1], {
            'id': frank_run2.pk,
            'flow': {'uuid': flow1.uuid, 'name': "Color Flow"},
            'contact': {'uuid': self.frank.uuid, 'name': self.frank.name},
            'responded': False,
            'steps': [
                {
                    'node': "00000000-00000000-00000000-00000001",
                    'arrived_on': format_datetime(frank_run2_steps[0].arrived_on),
                    'left_on': format_datetime(frank_run2_steps[0].left_on),
                    'text': "What is your favorite color?",
                    'value': None,
                    'category': None,
                    'type': 'actionset'
                },
                {
                    'node': "00000000-00000000-00000000-00000005",
                    'arrived_on': format_datetime(frank_run2_steps[1].arrived_on),
                    'left_on': None,
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
                    'text': "What is your favorite color?",
                    'value': None,
                    'category': None,
                    'type': 'actionset'
                },
                {
                    'node': "00000000-00000000-00000000-00000005",
                    'arrived_on': format_datetime(joe_run1_steps[1].arrived_on),
                    'left_on': format_datetime(joe_run1_steps[1].left_on),
                    'text': 'it is blue',
                    'value': 'blue',
                    'category': "Blue",
                    'type': 'ruleset'
                },
                {
                    'node': "00000000-00000000-00000000-00000003",
                    'arrived_on': format_datetime(joe_run1_steps[2].arrived_on),
                    'left_on': format_datetime(joe_run1_steps[2].left_on),
                    'text': 'Blue is sad. :(',
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

        # filter by id
        response = self.fetchJSON(url, 'id=%d' % frank_run2.pk)
        self.assertResultsById(response, [frank_run2])

        # filter by flow
        response = self.fetchJSON(url, 'flow=%s' % flow1.uuid)
        self.assertResultsById(response, [frank_run2, frank_run1, joe_run2, joe_run1])

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
        self.assertResultsById(response, [joe_run3, frank_run2, frank_run1])

        # filter by before
        response = self.fetchJSON(url, 'before=%s' % format_datetime(frank_run1.modified_on))
        self.assertResultsById(response, [frank_run1, joe_run2, joe_run1])

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
