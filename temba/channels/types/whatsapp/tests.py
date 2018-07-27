# -*- coding: utf-8 -*-
from __future__ import absolute_import, division, print_function, unicode_literals

from django.urls import reverse
from mock import patch

from temba.tests import TembaTest, MockResponse
from ...models import Channel
from .type import WhatsAppType
from django.forms import ValidationError
from .tasks import refresh_whatsapp_contacts


class WhatsAppTypeTest(TembaTest):

    def test_claim(self):
        Channel.objects.all().delete()

        url = reverse('channels.types.whatsapp.claim')
        self.login(self.admin)

        response = self.client.get(reverse('channels.channel_claim'))
        self.assertNotContains(response, url)

        response = self.client.get(url)
        self.assertEqual(200, response.status_code)
        post_data = response.context['form'].initial

        post_data['number'] = '1234'
        post_data['username'] = 'temba'
        post_data['password'] = 'tembapasswd'
        post_data['country'] = 'RW'
        post_data['base_url'] = 'https://whatsapp.foo.bar'

        # will fail with invalid phone number
        response = self.client.post(url, post_data)
        self.assertFormError(response, 'form', None, ["Please enter a valid phone number"])

        # valid number
        post_data['number'] = '0788123123'

        # try once with an error
        with patch('requests.post') as mock_post:
            mock_post.return_value = MockResponse(400, '{ "error": "true" }')
            response = self.client.post(url, post_data)
            self.assertEqual(200, response.status_code)
            self.assertFalse(Channel.objects.all())

        # then success
        with patch('requests.post') as mock_post:
            mock_post.return_value = MockResponse(200, '{ "error": "false" }')
            response = self.client.post(url, post_data)
            self.assertEqual(302, response.status_code)

        channel = Channel.objects.get()

        self.assertEqual('temba', channel.config['username'])
        self.assertEqual('tembapasswd', channel.config['password'])
        self.assertEqual('https://whatsapp.foo.bar', channel.config['base_url'])

        self.assertEqual('+250788123123', channel.address)
        self.assertEqual('RW', channel.country)
        self.assertEqual('WA', channel.channel_type)

        # test activating the channel
        with patch('requests.post') as mock_post:
            mock_post.side_effect = [
                MockResponse(200, '{ "error": false }'),
                MockResponse(200, '{ "error": false }'),
                MockResponse(200, '{ "error": false }')
            ]
            WhatsAppType().activate(channel)
            self.assertEqual(mock_post.call_args_list[0][1]['json']['payload']['set_settings']['webcallbacks']["1"],
                             'https://%s%s' % (channel.org.get_brand_domain(), reverse('courier.wa', args=[channel.uuid, 'receive'])))
            self.assertEqual(mock_post.call_args_list[1][1]['json']['payload']['set_allow_unsolicited_group_add'],
                             False)
            self.assertIn('set_settings', mock_post.call_args_list[2][1]['json']['payload'])

        with patch('requests.post') as mock_post:
            mock_post.side_effect = [MockResponse(400, '{ "error": true }')]

            try:
                WhatsAppType().activate(channel)
                self.fail("Should have thrown error activating channel")
            except ValidationError:
                pass

        with patch('requests.post') as mock_post:
            mock_post.side_effect = [
                MockResponse(200, '{ "error": "false" }'),
                MockResponse(400, '{ "error": "true" }')
            ]

            try:
                WhatsAppType().activate(channel)
                self.fail("Should have thrown error activating channel")
            except ValidationError:
                pass

        with patch('requests.post') as mock_post:
            mock_post.side_effect = [
                MockResponse(200, '{ "error": false }'),
                MockResponse(200, '{ "error": false }'),
                MockResponse(400, '{ "error": true }')
            ]

            try:
                WhatsAppType().activate(channel)
                self.fail("Should have thrown error activating channel")
            except ValidationError:
                pass

        # ok, test our refreshing
        refresh_url = reverse('channels.types.whatsapp.refresh', args=[channel.uuid])
        resp = self.client.get(refresh_url)
        self.assertEqual(405, resp.status_code)

        with patch('requests.post') as mock_post:
            mock_post.side_effect = [MockResponse(200, '{ "error": false }')]
            self.create_contact("Joe", urn="whatsapp:250788382382")
            self.client.post(refresh_url)

            self.assertEqual(mock_post.call_args_list[0][1]['json']['payload']['users'],
                             ['+250788382382'])

        with patch('requests.post') as mock_post:
            mock_post.side_effect = [MockResponse(400, '{ "error": true }')]
            try:
                refresh_whatsapp_contacts(channel.id)
                self.fail("Should have thrown exception")
            except Exception:
                pass
