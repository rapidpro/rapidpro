# -*- coding: utf-8 -*-
from __future__ import absolute_import, division, print_function, unicode_literals

import json

from django.urls import reverse
from mock import patch
from temba.tests import TembaTest, MockResponse
from ...models import Channel


class FirebaseCloudMessagingTypeTest(TembaTest):
    def setUp(self):
        super(FirebaseCloudMessagingTypeTest, self).setUp()

        self.channel = Channel.create(self.org, self.user, None, 'FCM', name="Firebase", address="87654",
                                      role="SR", schemes=['fcm'],
                                      config={'FCM_TITLE': 'Title', 'FCM_KEY': '87654'})

    @patch('requests.get')
    def test_claim(self, mock_get):
        url = reverse('channels.types.firebase.claim')

        self.login(self.admin)

        # check that claim page URL appears on claim list page
        response = self.client.get(reverse('channels.channel_claim'))
        self.assertContains(response, url)

        mock_get.return_value = MockResponse(
            200, json.dumps({'title': 'FCM Channel', 'key': 'abcde12345', 'send_notification': 'True'})
        )
        response = self.client.post(url, {
            'title': 'FCM Channel',
            'key': 'abcde12345',
            'send_notification': 'True'
        }, follow=True)

        channel = Channel.objects.get(address='abcde12345')
        self.assertRedirects(response, reverse('channels.channel_configuration', args=[channel.uuid]))
        self.assertEqual(channel.channel_type, "FCM")
        self.assertEqual(
            channel.config,
            {'FCM_KEY': 'abcde12345', 'FCM_TITLE': 'FCM Channel', 'FCM_NOTIFICATION': True}
        )
