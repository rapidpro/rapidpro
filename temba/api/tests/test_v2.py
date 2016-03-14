# -*- coding: utf-8 -*-
from __future__ import absolute_import, unicode_literals

import json

from django.core.urlresolvers import reverse
from django.db import connection
from django.test import override_settings
from django.utils import timezone
from temba.channels.models import Channel
from temba.contacts.models import Contact, ContactGroup, ContactField, ContactURN
from temba.flows.models import Flow
from temba.msgs.models import Broadcast, Label
from temba.orgs.models import Language
from temba.tests import TembaTest
from temba.values.models import Value
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

    def fetchJSON(self, url, query=None):
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
        url = reverse('api.v2.contacts') + '.json'

        # can't fetch endpoint with invalid token
        response = self.client.get(url, content_type="application/json",
                                   HTTP_X_FORWARDED_HTTPS='https', HTTP_AUTHORIZATION="Token 1234567890")
        response.json = json.loads(response.content)

        self.assertResponseError(response, None, "Invalid token", status_code=403)

        # can fetch endpoint with valid token
        response = self.client.get(url, content_type="application/json",
                                   HTTP_X_FORWARDED_HTTPS='https', HTTP_AUTHORIZATION="Token %s" % self.admin.api_token)

        self.assertEqual(response.status_code, 200)

        # but not if user is inactive
        self.admin.is_active = False
        self.admin.save()

        response = self.client.get(url, content_type="application/json",
                                   HTTP_X_FORWARDED_HTTPS='https', HTTP_AUTHORIZATION="Token %s" % self.admin.api_token)
        response.json = json.loads(response.content)

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
            'created_on': format_datetime(bcast4.created_on),
            'status': "failed"
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

        customers = ContactGroup.get_or_create(self.org, self.admin, "Customers")
        developers = ContactGroup.get_or_create(self.org, self.admin, "Developers")
        spammers = ContactGroup.get_or_create(self.org2, self.admin2, "Spammers")

        developers.update_contacts(self.admin, [self.frank], add=True)

        # no filtering
        with self.assertNumQueries(NUM_BASE_REQUEST_QUERIES + 1):
            response = self.fetchJSON(url)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json['next'], None)
        self.assertEqual(response.json['results'], [
            {'uuid': developers.uuid, 'name': "Developers", 'count': 1},
            {'uuid': customers.uuid, 'name': "Customers", 'count': 0}
        ])

        # filter by UUID
        response = self.fetchJSON(url, 'uuid=%s' % customers.uuid)
        self.assertEqual(response.json['results'], [{'uuid': customers.uuid, 'name': "Customers", 'count': 0}])

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

    def assertMsgEqual(self, msg_json, msg, msg_type, msg_status, msg_visibility):
        self.assertEqual(msg_json, {
            'id': msg.pk,
            'broadcast': msg.broadcast,
            'contact': {'uuid': msg.contact.uuid, 'name': msg.contact.name},
            'urn': msg.contact_urn.urn,
            'channel': {'uuid': msg.channel.uuid, 'name': msg.channel.name },
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
        label.toggle_label([frank_msg1], add=True)
        label.toggle_label([joe_msg3], add=True)

        frank_msg1.refresh_from_db(fields=['modified_on'])
        joe_msg3.refresh_from_db(fields=['modified_on'])

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
        self.assertResultsById(response, [joe_msg3, frank_msg1, deleted_msg, frank_msg3, joe_msg1])
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
        self.assertResultsById(response, [frank_msg1, deleted_msg, frank_msg3, joe_msg1])

        # filter by after (inclusive)
        response = self.fetchJSON(url, 'folder=incoming&after=%s' % format_datetime(frank_msg1.modified_on))
        self.assertResultsById(response, [joe_msg3, frank_msg1])

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
        fre = Language.create(self.org, self.admin, "French", 'fre')
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

        # can't filter by both contact and flow together
        response = self.fetchJSON(url, 'contact=%s&flow=%s' % (self.joe.uuid, flow1.uuid))
        self.assertResponseError(response, None,
                                 "You may only specify one of the contact, flow parameters")
