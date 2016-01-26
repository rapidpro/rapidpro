# -*- coding: utf-8 -*-
from __future__ import absolute_import, unicode_literals

import json

from django.core.urlresolvers import reverse
from django.db import connection
from django.utils import timezone
from temba.channels.models import Channel
from temba.contacts.models import Contact, ContactGroup
from temba.flows.models import Flow
from temba.msgs.models import Label
from temba.tests import TembaTest
from ..v2.serializers import format_datetime


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

    def fetchJSON(self, url, query=None):
        url += '.json'
        if query:
            url += ('?' + query)

        response = self.client.get(url, content_type="application/json", HTTP_X_FORWARDED_HTTPS='https')

        # this will fail if our response isn't valid json
        response.json = json.loads(response.content)
        return response

    def assertEndpointAccess(self, url):
        # 403 if not authenticated but can read docs
        response = self.fetchHTML(url)
        self.assertEqual(response.status_code, 403)

        # same for plain user
        self.login(self.user)
        response = self.fetchHTML(url)
        self.assertEqual(response.status_code, 403)

        # 403 for JSON request too
        response = self.fetchJSON(url)
        self.assertEqual(response.status_code, 403)

        # 200 for administrator
        self.login(self.admin)
        response = self.fetchHTML(url)
        self.assertEqual(response.status_code, 200)

    def assertResultsById(self, response, expected):
        self.assertEqual(response.status_code, 200)
        self.assertEqual([r['id'] for r in response.json['results']], [o.pk for o in expected])

    def assertResultsByUUID(self, response, expected):
        self.assertEqual(response.status_code, 200)
        self.assertEqual([r['uuid'] for r in response.json['results']], [o.uuid for o in expected])

    def test_api_contacts(self):
        url = reverse('api.v2.contacts')

        self.assertEndpointAccess(url)

        # create some more contacts (in addition to Joe and Frank)
        contact1 = self.create_contact("Ann", "0788000001", language='fre')
        contact2 = self.create_contact("Bob", "0788000002")
        contact3 = self.create_contact("Cat", "0788000003")
        contact4 = self.create_contact("Don", "0788000004")

        contact1.fail()
        contact2.block()
        contact3.release()

        # put some contacts in a group
        group = ContactGroup.get_or_create(self.org, self.admin, "Customers")
        group.update_contacts([self.joe, contact1], add=True)

        # no filtering
        with self.assertNumQueries(13):
            response = self.fetchJSON(url)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json['next'], None)
        self.assertResultsByUUID(response, [contact1, self.joe, contact4, contact2, self.frank])
        self.assertEqual(response.json['results'][0], {
            'uuid': contact1.uuid,
            'name': "Ann",
            'language': "fre",
            'urns': ["tel:+250788000001"],
            'groups': [{'uuid': group.uuid, 'name': group.name}],
            'fields': {},
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

    def test_api_messages(self):
        url = reverse('api.v2.messages')

        self.assertEndpointAccess(url)

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

        # add a test contact message
        self.create_msg(direction='I', msg_type='F', text="Hello", contact=self.test_contact)

        # add message in other org
        self.create_msg(direction='I', msg_type='I', text="Guten tag!", contact=self.hans, org=self.org2)

        # label some of the messages
        label = Label.get_or_create(self.org, self.admin, "Spam")
        label.toggle_label([frank_msg1, joe_msg3], add=True)

        # no filtering
        with self.assertNumQueries(13):
            response = self.fetchJSON(url)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json['next'], None)
        self.assertResultsById(response, [joe_msg4, frank_msg3, joe_msg3, frank_msg2, joe_msg2, frank_msg1, joe_msg1])
        self.assertEqual(response.json['results'][0], {
            'id': joe_msg4.pk,
            'broadcast': None,
            'contact': {'uuid': self.joe.uuid, 'name': self.joe.name},
            'urn': None,
            'channel': None,
            'direction': "out",
            'type': "flow",
            'status': "sent",
            'archived': False,
            'text': "Surveys!",
            'labels': [],
            'created_on': format_datetime(joe_msg4.created_on),
            'sent_on': format_datetime(joe_msg4.sent_on),
            'delivered_on': None
        })
        self.assertEqual(response.json['results'][5], {
            'id': frank_msg1.pk,
            'broadcast': None,
            'contact': {'uuid': self.frank.uuid, 'name': self.frank.name},
            'urn': "twitter:franky",
            'channel': self.twitter.uuid,
            'direction': "in",
            'type': "inbox",
            'status': "queued",
            'archived': False,
            'text': "Bonjour",
            'labels': [{'uuid': label.uuid, 'name': "Spam"}],
            'created_on': format_datetime(frank_msg1.created_on),
            'sent_on': None,
            'delivered_on': None
        })

        # filter by folder (inbox)
        response = self.fetchJSON(url, 'folder=INBOX')
        self.assertResultsById(response, [frank_msg1])

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

    def test_api_runs(self):
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
        with self.assertNumQueries(12):
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
