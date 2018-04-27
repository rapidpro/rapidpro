# -*- coding: utf-8 -*-
from __future__ import absolute_import, division, print_function, unicode_literals

import pytz

from datetime import datetime
from mock import patch
from temba.channels.models import Channel
from temba.flows.models import Flow, FlowRun
from temba.msgs.models import Label, Msg
from temba.tests import TembaTest, skip_if_no_flowserver, MockResponse
from temba.values.models import Value
from .client import serialize_field, serialize_label, serialize_channel, get_client, FlowServerException
from . import trial


class SerializationTest(TembaTest):
    def test_serialize_field(self):
        gender = self.create_field('gender', "Gender", Value.TYPE_TEXT)
        age = self.create_field('age', "Age", Value.TYPE_NUMBER)

        self.assertEqual(serialize_field(gender), {
            'key': "gender",
            'name': "Gender",
            'value_type': "text"
        })
        self.assertEqual(serialize_field(age), {
            'key': "age",
            'name': "Age",
            'value_type': "number"
        })

    def test_serialize_label(self):
        spam = Label.get_or_create(self.org, self.admin, "Spam")
        self.assertEqual(serialize_label(spam), {'uuid': str(spam.uuid), 'name': "Spam"})

    def test_serialize_channel(self):
        self.assertEqual(serialize_channel(self.channel), {
            'uuid': str(self.channel.uuid),
            'name': "Test Channel",
            'address': '+250785551212',
            'roles': ['send', 'receive'],
            'schemes': ['tel'],
        })


class ClientTest(TembaTest):
    def setUp(self):
        super(ClientTest, self).setUp()

        self.gender = self.create_field('gender', "Gender", Value.TYPE_TEXT)
        self.age = self.create_field('age', "Age", Value.TYPE_NUMBER)
        self.contact = self.create_contact("Bob", number="+12345670987", urn='twitterid:123456785#bobby')
        self.testers = self.create_group("Testers", [self.contact])
        self.client = get_client()

    def test_add_contact_changed(self):
        twitter = Channel.create(self.org, self.admin, None, "TT", "Twitter", "nyaruka", schemes=['twitter', 'twitterid'])
        self.contact.set_preferred_channel(twitter)
        self.contact.urns.filter(scheme='twitterid').update(channel=twitter)
        self.contact.clear_urn_cache()

        with patch('django.utils.timezone.now', return_value=datetime(2018, 1, 18, 14, 24, 30, 0, tzinfo=pytz.UTC)):
            self.contact.set_field(self.admin, 'gender', "M")
            self.contact.set_field(self.admin, 'age', 36)

            self.assertEqual(self.client.request_builder(self.org, 1234).add_contact_changed(self.contact).request['events'], [{
                'type': "contact_changed",
                'created_on': "2018-01-18T14:24:30+00:00",
                'contact': {
                    'uuid': str(self.contact.uuid),
                    'name': 'Bob',
                    'language': None,
                    'timezone': 'UTC',
                    'urns': [
                        'twitterid:123456785?channel=%s#bobby' % str(twitter.uuid),
                        'tel:+12345670987?channel=%s' % str(self.channel.uuid)
                    ],
                    'fields': {
                        'gender': {'text': 'M'},
                        'age': {'text': '36', 'number': '36'},
                    },
                    'groups': [
                        {'uuid': str(self.testers.uuid), 'name': "Testers"}
                    ]
                }
            }])

    def test_add_environment_changed(self):
        with patch('django.utils.timezone.now', return_value=datetime(2018, 1, 18, 14, 24, 30, 0, tzinfo=pytz.UTC)):
            self.assertEqual(self.client.request_builder(self.org, 1234).add_environment_changed().request['events'], [{
                'type': "environment_changed",
                'created_on': "2018-01-18T14:24:30+00:00",
                'environment': {
                    'date_format': 'DD-MM-YYYY',
                    'languages': [],
                    'time_format': 'tt:mm',
                    'timezone': 'Africa/Kigali'
                }
            }])

    def test_add_run_expired(self):
        flow = self.get_flow('color')
        run, = flow.start([], [self.contact])
        run.set_interrupted()

        with patch('django.utils.timezone.now', return_value=datetime(2018, 1, 18, 14, 24, 30, 0, tzinfo=pytz.UTC)):

            self.assertEqual(self.client.request_builder(self.org, 1234).add_run_expired(run).request['events'], [{
                'type': "run_expired",
                'created_on': run.exited_on.isoformat(),
                'run_uuid': str(run.uuid)
            }])

    @patch('requests.post')
    def test_request_failure(self, mock_post):
        mock_post.return_value = MockResponse(400, '{"errors":["Bad request", "Doh!"]}')

        flow = self.get_flow('color')
        contact = self.create_contact("Joe", number='+29638356667')

        with self.assertRaises(FlowServerException) as e:
            self.client.request_builder(self.org, 1234).start_manual(contact, flow)

        self.assertEqual(str(e.exception), "Invalid request: Bad request\nDoh!")


class TrialTest(TembaTest):
    def setUp(self):
        super(TrialTest, self).setUp()

        self.contact = self.create_contact('Ben Haggerty', number='+12065552020')

    @skip_if_no_flowserver
    def test_resume_with_message(self):
        favorites = self.get_flow('favorites')

        run, = favorites.start([], [self.contact])

        # capture session state before resumption
        session_state1 = trial.reconstruct_session(run)

        self.assertEqual(len(session_state1['runs']), 1)
        self.assertEqual(session_state1['runs'][0]['flow']['uuid'], str(favorites.uuid))
        self.assertEqual(session_state1['contact']['uuid'], str(self.contact.uuid))
        self.assertNotIn('results', session_state1)
        self.assertNotIn('events', session_state1)

        # and then resume by replying
        msg1 = Msg.create_incoming(self.channel, 'tel:+12065552020', "I like red")
        run.refresh_from_db()

        resume1_output = trial.resume(self.org, session_state1, msg_in=msg1)

        self.assertEqual(trial.compare_run(run, resume1_output.session), {})

        # capture session state again
        session_state2 = trial.reconstruct_session(run)

        # and then resume by replying again
        msg2 = Msg.create_incoming(self.channel, 'tel:+12065552020', "ooh Primus",
                                   attachments=['image/jpeg:http://example.com/primus.jpg'])
        run.refresh_from_db()

        resume2_output = trial.resume(self.org, session_state2, msg_in=msg2)

        self.assertEqual(trial.compare_run(run, resume2_output.session), {})

        # simulate session not containing this run
        self.assertEqual(set(trial.compare_run(run, {'runs': []}).keys()), {'session'})

        # simulate differences in the path, results and events
        session_state2['runs'][0]['path'][0]['node_uuid'] = 'wrong node'
        session_state2['runs'][0]['results']['color']['value'] = 'wrong value'
        session_state2['runs'][0]['events'][0]['msg']['text'] = 'wrong text'

        self.assertEqual(set(trial.compare_run(run, session_state2).keys()), {'path', 'results', 'events'})

    @skip_if_no_flowserver
    def test_resume_with_message_in_subflow(self):
        self.get_flow('subflow')
        parent_flow = Flow.objects.get(org=self.org, name='Parent Flow')
        child_flow = Flow.objects.get(org=self.org, name='Child Flow')

        # start the parent flow and then trigger the subflow by picking an option
        parent_flow.start([], [self.contact])
        Msg.create_incoming(self.channel, 'tel:+12065552020', "color")

        parent_run, child_run = list(FlowRun.objects.order_by('created_on'))

        # capture session state before resumption
        session_state1 = trial.reconstruct_session(child_run)

        self.assertEqual(len(session_state1['runs']), 2)
        self.assertEqual(session_state1['runs'][0]['flow']['uuid'], str(parent_flow.uuid))
        self.assertEqual(session_state1['runs'][1]['flow']['uuid'], str(child_flow.uuid))
        self.assertEqual(session_state1['contact']['uuid'], str(self.contact.uuid))
        self.assertNotIn('results', session_state1)
        self.assertNotIn('events', session_state1)

        # and then resume by replying
        msg1 = Msg.create_incoming(self.channel, 'tel:+12065552020', "I like red")
        child_run.refresh_from_db()
        parent_run.refresh_from_db()

        # subflow run has completed
        self.assertIsNotNone(child_run.exited_on)

        resume1_output = trial.resume(self.org, session_state1, msg_in=msg1)

        self.assertEqual(trial.compare_run(child_run, resume1_output.session), {})
        self.assertEqual(trial.compare_run(parent_run, resume1_output.session), {})
