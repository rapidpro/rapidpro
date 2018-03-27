# -*- coding: utf-8 -*-
from __future__ import absolute_import, division, print_function, unicode_literals

import pytz

from datetime import datetime
from mock import patch
from temba.msgs.models import Label
from temba.tests import TembaTest, MockResponse
from temba.values.models import Value
from .client import serialize_field, serialize_label, get_client, FlowServerException


class SerializationTest(TembaTest):
    def test_serialize_field(self):
        gender = self.create_field('gender', "Gender", Value.TYPE_TEXT)
        age = self.create_field('age', "Age", Value.TYPE_DECIMAL)

        self.assertEqual(serialize_field(gender), {
            'key': "gender",
            'label': "Gender",
            'value_type': "text"
        })
        self.assertEqual(serialize_field(age), {
            'key': "age",
            'label': "Age",
            'value_type': "numeric"
        })

    def test_serialize_label(self):
        spam = Label.get_or_create(self.org, self.admin, "Spam")
        self.assertEqual(serialize_label(spam), {'uuid': str(spam.uuid), 'name': "Spam"})


class ClientTest(TembaTest):
    def setUp(self):
        super(ClientTest, self).setUp()

        self.gender = self.create_field('gender', "Gender", Value.TYPE_TEXT)
        self.age = self.create_field('age', "Age", Value.TYPE_DECIMAL)
        self.contact = self.create_contact("Bob", number="+12345670987")
        self.client = get_client()

    def test_add_contact_changed(self):

        with patch('django.utils.timezone.now', return_value=datetime(2018, 1, 18, 14, 24, 30, 0, tzinfo=pytz.UTC)):
            self.contact.set_field(self.admin, 'gender', "M")
            self.contact.set_field(self.admin, 'age', 36)

            self.assertEqual(self.client.request_builder(1234).add_contact_changed(self.contact).request['events'], [{
                'type': "contact_changed",
                'created_on': "2018-01-18T14:24:30+00:00",
                'contact': {
                    'uuid': str(self.contact.uuid),
                    'name': 'Bob',
                    'urns': ['tel:+12345670987'],
                    'fields': {
                        'gender': {'value': 'M', 'created_on': "2018-01-18T14:24:30+00:00"},
                        'age': {'value': '36', 'created_on': "2018-01-18T14:24:30+00:00"}
                    },
                    'group_uuids': [],
                    'language': None,
                    'channel_uuid': str(self.channel.uuid),
                    'timezone': 'UTC',
                }
            }])

    def test_add_environment_changed(self):
        with patch('django.utils.timezone.now', return_value=datetime(2018, 1, 18, 14, 24, 30, 0, tzinfo=pytz.UTC)):
            self.assertEqual(self.client.request_builder(1234).add_environment_changed(self.org).request['events'], [{
                'type': "environment_changed",
                'created_on': "2018-01-18T14:24:30+00:00",
                'environment': {
                    'date_format': 'dd-MM-yyyy',
                    'languages': [],
                    'time_format': 'hh:mm',
                    'timezone': 'Africa/Kigali'
                }
            }])

    def test_add_run_expired(self):
        flow = self.get_flow('color')
        run, = flow.start([], [self.contact])
        run.set_interrupted()

        with patch('django.utils.timezone.now', return_value=datetime(2018, 1, 18, 14, 24, 30, 0, tzinfo=pytz.UTC)):

            self.assertEqual(self.client.request_builder(1234).add_run_expired(run).request['events'], [{
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
            self.client.request_builder(1234).start_manual(self.org, contact, flow)

        self.assertEqual(str(e.exception), "Invalid request: Bad request\nDoh!")
