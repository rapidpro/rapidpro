# -*- coding: utf-8 -*-
from __future__ import absolute_import, division, print_function, unicode_literals

import telegram

from django.test import override_settings
from django.urls import reverse
from mock import patch
from temba.contacts.models import URN
from temba.tests import TembaTest
from ...models import Channel


class TelegramTypeTest(TembaTest):
    def setUp(self):
        super(TelegramTypeTest, self).setUp()

        self.channel = Channel.create(self.org, self.user, None, 'TG', name="Telegram", address="12345",
                                      role="SR", schemes=['telegram'],
                                      config={'auth_token': '123456789:BAEKbsOKAL23CXufXG4ksNV7Dq7e_1qi3j8'})

    @override_settings(IS_PROD=True)
    @patch('telegram.Bot.get_me')
    @patch('telegram.Bot.set_webhook')
    def test_claim(self, mock_set_webhook, mock_get_me):
        url = reverse('channels.types.telegram.claim')

        self.login(self.admin)

        # check that claim page URL appears on claim list page
        response = self.client.get(reverse('channels.channel_claim'))
        self.assertContains(response, url)

        # can fetch the claim page
        response = self.client.get(url)
        self.assertContains(response, "Connect Telegram")

        # claim with an invalid token
        mock_get_me.side_effect = telegram.TelegramError('Boom')
        response = self.client.post(url, {'auth_token': 'invalid'})
        self.assertEqual(200, response.status_code)
        self.assertEqual('Your authentication token is invalid, please check and try again',
                         response.context['form'].errors['auth_token'][0])

        user = telegram.User(123, 'Rapid', True)
        user.last_name = 'Bot'
        user.username = 'rapidbot'

        mock_get_me.side_effect = None
        mock_get_me.return_value = user
        mock_set_webhook.return_value = ''

        response = self.client.post(url, {'auth_token': '184875172:BAEKbsOKAL23CXufXG4ksNV7Dq7e_1qi3j8'})
        channel = Channel.objects.get(address="rapidbot")
        self.assertEqual(channel.channel_type, 'TG')
        self.assertEqual(channel.config, {
            'auth_token': '184875172:BAEKbsOKAL23CXufXG4ksNV7Dq7e_1qi3j8', 'callback_domain': channel.callback_domain
        })

        self.assertRedirect(response, reverse('channels.channel_read', args=[channel.uuid]))
        self.assertEqual(302, response.status_code)

        response = self.client.post(url, {'auth_token': '184875172:BAEKbsOKAL23CXufXG4ksNV7Dq7e_1qi3j8'})
        self.assertEqual('A telegram channel for this bot already exists on your account.',
                         response.context['form'].errors['auth_token'][0])

        contact = self.create_contact('Telegram User', urn=URN.from_telegram('1234'))

        # make sure we our telegram channel satisfies as a send channel
        response = self.client.get(reverse('contacts.contact_read', args=[contact.uuid]))
        send_channel = response.context['send_channel']
        self.assertIsNotNone(send_channel)
        self.assertEqual(send_channel.channel_type, 'TG')

    @override_settings(IS_PROD=True)
    @patch('telegram.Bot.delete_webhook')
    def test_release(self, mock_delete_webhook):
        self.channel.release()

        mock_delete_webhook.assert_called_once_with()
