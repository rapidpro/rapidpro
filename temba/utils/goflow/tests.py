# -*- coding: utf-8 -*-
from __future__ import absolute_import, division, print_function, unicode_literals

import pytz

from datetime import datetime
from mock import patch
from temba.channels.models import Channel
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
    def test_resume_and_compare(self):
        favorites = self.get_flow('favorites')

        run, = favorites.start([], [self.contact])

        # capture session state before resumption
        pre_session = trial.reconstruct_session(run)

        msg_in = Msg.create_incoming(self.channel, 'tel:+12065552020', "I like red",
                                     attachments=['image/jpeg:http://example.com/test.jpg'])
        run.refresh_from_db()

        resume_output = trial.resume(self.org, pre_session, msg_in=msg_in)
        new_session = resume_output.session

        self.assertTrue(trial.compare(run, new_session))

        # simulate differences in the path, results and events
        new_session['runs'][0]['path'][0]['node_uuid'] = 'wrong node'
        new_session['runs'][0]['results']['color']['value'] = 'wrong value'
        new_session['runs'][0]['events'][0]['msg']['text'] = 'wrong text'

        self.assertFalse(trial.compare(run, new_session))
