# -*- coding: utf-8 -*-
from __future__ import absolute_import, division, print_function, unicode_literals

import json

from django.test import override_settings
from django.urls import reverse
from mock import patch
from temba.tests import TembaTest, MockResponse
from temba.triggers.models import Trigger
from ...models import Channel


class FacebookTypeTest(TembaTest):
    def setUp(self):
        super(FacebookTypeTest, self).setUp()

        self.channel = Channel.create(self.org, self.user, None, 'FB', name="Facebook", address="12345",
                                      role="SR", schemes=['facebook'], config={'auth_token': '09876543'})

    @override_settings(IS_PROD=True)
    @patch('requests.get')
    @patch('requests.post')
    def test_claim(self, mock_post, mock_get):
        url = reverse('channels.types.facebook.claim')

        self.login(self.admin)

        # check that claim page URL appears on claim list page
        response = self.client.get(reverse('channels.channel_claim'))
        self.assertContains(response, url)

        # can fetch the claim page
        response = self.client.get(url)
        self.assertContains(response, "Connect Facebook")

        token = 'x' * 200
        mock_get.return_value = MockResponse(400, json.dumps({'error': {'message': "Failed validation"}}))

        # try to claim facebook, should fail because our verification of the token fails
        response = self.client.post(url, {'page_access_token': token})

        # assert we got a normal 200 and it says our token is wrong
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Failed validation")

        # ok this time claim with a success
        mock_get.return_value = MockResponse(200, json.dumps({'name': "Temba", 'id': 10}))
        response = self.client.post(url, {'page_access_token': token}, follow=True)

        # assert our channel got created
        channel = Channel.objects.get(address='10')
        self.assertEqual(channel.config[Channel.CONFIG_AUTH_TOKEN], token)
        self.assertEqual(channel.config[Channel.CONFIG_PAGE_NAME], 'Temba')
        self.assertEqual(channel.address, '10')

        # should be on our configuration page displaying our secret
        self.assertContains(response, channel.config[Channel.CONFIG_SECRET])

        # test validating our secret
        handler_url = reverse('courier.fb', args=['invalid'])
        response = self.client.get(handler_url)
        self.assertEqual(response.status_code, 400)

        # test invalid token
        handler_url = reverse('courier.fb', args=[channel.uuid])
        payload = {'hub.mode': 'subscribe', 'hub.verify_token': 'invalid', 'hub.challenge': 'challenge'}
        response = self.client.get(handler_url, payload)
        self.assertEqual(response.status_code, 400)

        # test actual token
        payload['hub.verify_token'] = channel.config[Channel.CONFIG_SECRET]

        # try with unsuccessful callback to subscribe (this fails silently)
        mock_post.return_value = MockResponse(400, json.dumps({'success': True}))

        response = self.client.get(handler_url, payload)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'challenge')

        # assert we subscribed to events
        self.assertEqual(mock_post.call_count, 1)

        # but try again and we should try again
        mock_post.reset_mock()
        mock_post.return_value = MockResponse(200, json.dumps({'success': True}))

        response = self.client.get(handler_url, payload)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'challenge')

        # assert we subscribed to events
        self.assertEqual(mock_post.call_count, 1)

    @override_settings(IS_PROD=True)
    @patch('requests.delete')
    def test_release(self, mock_delete):
        mock_delete.return_value = MockResponse(200, json.dumps({'success': True}))
        self.channel.release()

        mock_delete.assert_called_once_with('https://graph.facebook.com/v2.5/me/subscribed_apps',
                                            params={'access_token': '09876543'})

    @override_settings(IS_PROD=True)
    @patch('requests.post')
    def test_new_conversation_triggers(self, mock_post):
        mock_post.return_value = MockResponse(200, json.dumps({'success': True}))

        flow = self.create_flow()

        trigger = Trigger.create(self.org, self.admin, Trigger.TYPE_NEW_CONVERSATION, flow, self.channel)

        mock_post.assert_called_once_with('https://graph.facebook.com/v2.6/12345/thread_settings', json={
            'setting_type': 'call_to_actions',
            'thread_state': 'new_thread',
            'call_to_actions': [{"payload": "get_started"}]
        }, headers={'Content-Type': 'application/json'}, params={'access_token': '09876543'})
        mock_post.reset_mock()

        trigger.archive(self.admin)

        mock_post.assert_called_once_with('https://graph.facebook.com/v2.6/12345/thread_settings', json={
            'setting_type': 'call_to_actions',
            'thread_state': 'new_thread',
            'call_to_actions': []
        }, headers={'Content-Type': 'application/json'}, params={'access_token': '09876543'})
        mock_post.reset_mock()

        trigger.restore(self.admin)

        mock_post.assert_called_once_with('https://graph.facebook.com/v2.6/12345/thread_settings', json={
            'setting_type': 'call_to_actions',
            'thread_state': 'new_thread',
            'call_to_actions': [{"payload": "get_started"}]
        }, headers={'Content-Type': 'application/json'}, params={'access_token': '09876543'})
        mock_post.reset_mock()
