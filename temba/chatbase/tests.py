from __future__ import unicode_literals

import json
from mock import patch
from django.core.urlresolvers import reverse
from temba.channels.models import Channel
from temba.chatbase.models import Chatbase
from temba.msgs.models import Msg
from temba.tests import TembaTest, MockResponse


class ChatbaseEventTest(TembaTest):
    def setUp(self):
        super(ChatbaseEventTest, self).setUp()

        self.channel.delete()

        self.contact = self.create_contact('Ben Haggerty', number=None, twitter='bob_marley')
        self.channel = Channel.create(self.org, self.user, None, 'TT', name="Twitter Channel",
                                      address="billy_bob", role="SR", scheme='twitter')
        self.msg1 = Msg.create_outgoing(self.org, self.admin, self.contact, "Hello, we heard from you.")
        self.msg2 = Msg.create_incoming(self.channel, 'twitter:bob_marley', 'Hello, world.')
        self.msg3 = Msg.create_incoming(self.channel, 'twitter:bob_marley', 'Hello, world (again).')
        self.msg4 = Msg.create_outgoing(self.org, self.admin, self.contact, "Hello, we heard from you (again).")

        self.org.connect_chatbase('agent_name', 'my_api_key', 'agent', True, False, '1.0', self.user)

        self.chatbase1 = Chatbase.create(org=self.org.id, channel=self.channel.id, msg=self.msg1.id,
                                         contact=self.contact.id)
        self.chatbase2 = Chatbase.create(org=self.org.id, channel=self.channel.id, msg=self.msg2.id,
                                         contact=self.contact.id)
        self.chatbase3 = Chatbase.create(org=self.org.id, channel=self.channel.id, msg=self.msg3.id,
                                         contact=self.contact.id)
        self.chatbase4 = Chatbase.create(org=self.org.id, channel=self.channel.id, msg=self.msg4.id,
                                         contact=self.contact.id)

    def test_create(self):
        self.assertTrue(self.chatbase1)
        self.assertEqual(self.chatbase1.channel, self.channel)
        self.assertEqual(self.chatbase2.channel, self.channel)
        self.assertEqual(self.chatbase1.msg, self.msg1)
        self.assertEqual(self.chatbase2.msg, self.msg2)
        self.assertEqual(self.chatbase3.msg, self.msg3)

    def test_trigger_chatbase_event(self):
        with patch('requests.post') as mock_post:
            with self.settings(SEND_CHATBASE=True):

                mock_post.return_value = MockResponse(200, json.dumps(dict(status=200, message_id=12345)))

                mock_chatbase1_data = self.chatbase1.data
                mock_chatbase1_response = self.chatbase1.response

                self.assertFalse(mock_chatbase1_data)
                self.assertFalse(mock_chatbase1_response)

                # Trigger first event
                self.chatbase1.trigger_chatbase_event()

                mock_chatbase1_data = self.chatbase1.data
                mock_chatbase1_response = self.chatbase1.response

                self.assertTrue(mock_chatbase1_data)
                self.assertTrue(mock_chatbase1_response)

                mock_chatbase1_data = json.loads(mock_chatbase1_data)
                mock_chatbase1_response = json.loads(mock_chatbase1_response)

                self.assertEqual(mock_chatbase1_data.get('platform'), 'Twitter Channel')
                self.assertEqual(mock_chatbase1_data.get('message'), 'Hello, we heard from you.')
                self.assertEqual(mock_chatbase1_data.get('api_key'), 'my_api_key')
                self.assertEqual(mock_chatbase1_data.get('type'), 'agent')

                self.assertEqual(mock_chatbase1_response.get('message_id'), 12345)
                self.assertEqual(mock_chatbase1_response.get('status'), 200)

                mock_chatbase2_data = self.chatbase2.data
                mock_chatbase2_response = self.chatbase2.response

                self.assertFalse(mock_chatbase2_data)
                self.assertFalse(mock_chatbase2_response)

                # Trigger second event
                self.chatbase2.trigger_chatbase_event()

                mock_chatbase2_data = self.chatbase2.data
                mock_chatbase2_response = self.chatbase2.response

                self.assertTrue(mock_chatbase2_data)
                self.assertTrue(mock_chatbase2_response)

                mock_chatbase2_data = json.loads(mock_chatbase2_data)
                mock_chatbase2_response = json.loads(mock_chatbase2_response)

                self.assertEqual(mock_chatbase2_data.get('platform'), 'Twitter Channel')
                self.assertEqual(mock_chatbase2_data.get('message'), 'Hello, world.')
                self.assertEqual(mock_chatbase2_data.get('api_key'), 'my_api_key')
                self.assertEqual(mock_chatbase2_data.get('type'), 'agent')

                self.assertEqual(mock_chatbase2_response.get('message_id'), 12345)
                self.assertEqual(mock_chatbase2_response.get('status'), 200)

        with patch('requests.post') as mock_post:
            with self.settings(SEND_CHATBASE=False):
                with self.assertRaises(Exception):
                    # Trigger third event
                    self.chatbase3.trigger_chatbase_event()

        with patch('requests.post') as mock_post:
            with self.settings(SEND_CHATBASE=True):
                mock_data = dict(status=400,
                                 reason="Error fetching parameter 'api_key': Missing or invalid field(s): 'api_key'")
                mock_post.return_value = MockResponse(200, json.dumps(mock_data))

                self.org.remove_chatbase_account(self.user)

                # Trigger fourth event
                self.chatbase4.trigger_chatbase_event()

                mock_chatbase4_data = self.chatbase4.data
                mock_chatbase4_response = self.chatbase4.response

                self.assertTrue(mock_chatbase4_data)
                self.assertTrue(mock_chatbase4_response)

                mock_chatbase4_data = json.loads(mock_chatbase4_data)
                mock_chatbase4_response = json.loads(mock_chatbase4_response)

                self.assertEqual(mock_chatbase4_data.get('message'), 'Hello, we heard from you (again).')
                self.assertEqual(mock_chatbase4_data.get('api_key'), '')
                self.assertEqual(mock_chatbase4_data.get('type'), '')

                self.assertEqual(mock_chatbase4_response.get('status'), 400)
                self.assertEqual(mock_chatbase4_response.get('reason'), "Error fetching parameter 'api_key': Missing "
                                                                        "or invalid field(s): 'api_key'")

    def test_list(self):
        list_url = reverse('chatbase.chatbase_list')
        self.login(self.user)

        response = self.client.get(list_url)
        self.assertRedirect(response, '/users/login/')

        self.login(self.editor)
        response = self.client.get(list_url)
        self.assertEqual(response.status_code, 200)
        self.assertTrue(self.chatbase1 in response.context['object_list'])

        self.login(self.admin)
        response = self.client.get(list_url)
        self.assertEqual(response.status_code, 200)
        self.assertTrue(self.chatbase2 in response.context['object_list'])

    def test_read(self):
        read_url = reverse('chatbase.chatbase_read', args=[self.chatbase1.pk])

        self.login(self.user)
        response = self.client.get(read_url)
        self.assertRedirect(response, '/users/login/')

        self.login(self.editor)
        response = self.client.get(read_url)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.chatbase1.pk, response.context['object'].pk)

        self.login(self.admin)
        response = self.client.get(read_url)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.chatbase1.pk, response.context['object'].pk)

        read_url = reverse('chatbase.chatbase_read', args=[self.chatbase2.pk])

        self.login(self.editor)
        response = self.client.get(read_url)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.chatbase2.pk, response.context['object'].pk)

        self.login(self.admin)
        response = self.client.get(read_url)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.chatbase2.pk, response.context['object'].pk)
