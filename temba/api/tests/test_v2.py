# -*- coding: utf-8 -*-
from __future__ import absolute_import, unicode_literals

import json

from django.core.urlresolvers import reverse
from django.db import connection
from temba.contacts.models import Contact
from temba.flows.models import Flow
from temba.tests import TembaTest
from ..v2.serializers import format_datetime


class APITest(TembaTest):

    def setUp(self):
        super(APITest, self).setUp()

        self.joe = self.create_contact("Joe Blow", "0788123123")
        self.frank = self.create_contact("Frank", twitter="franky")
        self.test_contact = Contact.get_test_contact(self.user)

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

    def assertResultIds(self, response, ids):
        self.assertEqual(response.status_code, 200)
        self.assertEqual([r['id'] for r in response.json['results']], ids)

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

        # refresh runs which will have been modified by being interrupted
        joe_run1.refresh_from_db()
        joe_run2.refresh_from_db()
        frank_run1.refresh_from_db()

        # no filtering
        response = self.fetchJSON(url)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json['next'], None)
        self.assertResultIds(response, [joe_run3.pk, frank_run2.pk, frank_run1.pk, joe_run2.pk, joe_run1.pk])

        joe_run1_steps = list(joe_run1.steps.order_by('pk'))
        frank_run2_steps = list(frank_run2.steps.order_by('pk'))

        self.maxDiff = None

        self.assertEqual(response.json['results'][1], {'id': frank_run2.pk,
                                                       'flow': flow1.uuid,
                                                       'contact': self.frank.uuid,
                                                       'responded': False,
                                                       'steps': [{'node': '00000000-00000000-00000000-00000001',
                                                                  'arrived_on': format_datetime(frank_run2_steps[0].arrived_on),
                                                                  'left_on': format_datetime(frank_run2_steps[0].left_on),
                                                                  'text': None,
                                                                  'value': None,
                                                                  'category': None,
                                                                  'type': 'actionset'},
                                                                 {'node': '00000000-00000000-00000000-00000005',
                                                                  'arrived_on': format_datetime(frank_run2_steps[1].arrived_on),
                                                                  'left_on': None,
                                                                  'text': None,
                                                                  'value': None,
                                                                  'category': None,
                                                                  'type': 'ruleset'}],
                                                       'created_on': format_datetime(frank_run2.created_on),
                                                       'modified_on': format_datetime(frank_run2.modified_on),
                                                       'exited_on': None,
                                                       'exit_type': None})

        self.assertEqual(response.json['results'][4], {'id': joe_run1.pk,
                                                       'flow': flow1.uuid,
                                                       'contact': self.joe.uuid,
                                                       'responded': True,
                                                       'steps': [{'node': '00000000-00000000-00000000-00000001',
                                                                  'arrived_on': format_datetime(joe_run1_steps[0].arrived_on),
                                                                  'left_on': format_datetime(joe_run1_steps[0].left_on),
                                                                  'text': 'What is your favorite color?',
                                                                  'value': None,
                                                                  'category': None,
                                                                  'type': 'actionset'},
                                                                 {'node': '00000000-00000000-00000000-00000005',
                                                                  'arrived_on': format_datetime(joe_run1_steps[1].arrived_on),
                                                                  'left_on': format_datetime(joe_run1_steps[1].left_on),
                                                                  'text': 'it is blue',
                                                                  'value': 'blue',
                                                                  'category': "Blue",
                                                                  'type': 'ruleset'},
                                                                 {'node': '00000000-00000000-00000000-00000003',
                                                                  'arrived_on': format_datetime(joe_run1_steps[2].arrived_on),
                                                                  'left_on': format_datetime(joe_run1_steps[2].left_on),
                                                                  'text': 'Blue is sad. :(',
                                                                  'value': None,
                                                                  'category': None,
                                                                  'type': 'actionset'}],
                                                       'created_on': format_datetime(joe_run1.created_on),
                                                       'modified_on': format_datetime(joe_run1.modified_on),
                                                       'exited_on': format_datetime(joe_run1.exited_on),
                                                       'exit_type': 'completed'})

        # filter by flow
        response = self.fetchJSON(url, 'flow=%s' % flow1.uuid)
        self.assertResultIds(response, [frank_run2.pk, frank_run1.pk, joe_run2.pk, joe_run1.pk])

        # filter by invalid flow
        response = self.fetchJSON(url, 'flow=invalid')
        self.assertResultIds(response, [])

        # filter by flow + responded
        response = self.fetchJSON(url, 'flow=%s&responded=TrUe' % flow1.uuid)
        self.assertResultIds(response, [frank_run1.pk, joe_run1.pk])

        # filter by contact
        response = self.fetchJSON(url, 'contact=%s' % self.joe.uuid)
        self.assertResultIds(response, [joe_run3.pk, joe_run2.pk, joe_run1.pk])

        # filter by invalid contact
        response = self.fetchJSON(url, 'contact=invalid')
        self.assertResultIds(response, [])

        # filter by contact + responded
        response = self.fetchJSON(url, 'contact=%s&responded=yes' % self.joe.uuid)
        self.assertResultIds(response, [joe_run1.pk])

        # filter by after
        response = self.fetchJSON(url, 'after=%s' % format_datetime(frank_run1.modified_on))
        self.assertResultIds(response, [joe_run3.pk, frank_run2.pk, frank_run1.pk])

        # filter by before
        response = self.fetchJSON(url, 'before=%s' % format_datetime(frank_run1.modified_on))
        self.assertResultIds(response, [frank_run1.pk, joe_run2.pk, joe_run1.pk])

        # filter by invalid before
        response = self.fetchJSON(url, 'before=longago')
        self.assertResultIds(response, [])
