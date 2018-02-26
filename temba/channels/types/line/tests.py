# -*- coding: utf-8 -*-
from __future__ import absolute_import, division, print_function, unicode_literals

import json

from django.urls import reverse
from mock import patch
from temba.tests import TembaTest, MockResponse
from ...models import Channel


class LineTypeTest(TembaTest):
    def setUp(self):
        super(LineTypeTest, self).setUp()

        self.channel = Channel.create(self.org, self.user, None, 'LN', name="LINE", address="12345",
                                      role="SR", schemes=['line'],
                                      config={'auth_token': 'abcdef098765', 'secret': '87654'})

    @patch('requests.get')
    def test_claim(self, mock_get):
        url = reverse('channels.types.line.claim')

        self.login(self.admin)

        # check that claim page URL appears on claim list page
        response = self.client.get(reverse('channels.channel_claim'))
        self.assertContains(response, url)

        mock_get.return_value = MockResponse(200, json.dumps({'channelId': 123456789, 'mid': 'u1234567890'}))

        payload = {'access_token': 'abcdef123456', 'secret': '123456'}

        response = self.client.post(url, payload, follow=True)

        channel = Channel.objects.get(address='u1234567890')
        self.assertRedirects(response, reverse('channels.channel_configuration', args=[channel.uuid]))
        self.assertEqual(channel.config, {
            'auth_token': 'abcdef123456',
            'secret': '123456',
            'channel_id': 123456789,
            'channel_mid': 'u1234567890'
        })

        response = self.client.post(url, payload, follow=True)
        self.assertContains(response, "A channel with this configuration already exists.")

        self.org.channels.update(is_active=False)

        mock_get.return_value = MockResponse(401, json.dumps(dict(error_desciption="invalid token")))
        payload = {'access_token': 'abcdef123456', 'secret': '123456'}

        response = self.client.post(url, payload, follow=True)
        self.assertContains(response, "invalid token")
