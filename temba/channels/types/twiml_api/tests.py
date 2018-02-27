# -*- coding: utf-8 -*-
from __future__ import absolute_import, division, print_function, unicode_literals

from django.urls import reverse
from mock import patch

from temba.tests import TembaTest
from temba.tests.twilio import MockTwilioClient, MockRequestValidator


class TwimlAPITypeTest(TembaTest):

    @patch('temba.ivr.clients.TwilioClient', MockTwilioClient)
    @patch('twilio.util.RequestValidator', MockRequestValidator)
    def test_claim(self):
        self.login(self.admin)

        # remove any existing channels
        self.org.channels.update(is_active=False)

        claim_url = reverse('channels.types.twiml_api.claim')

        response = self.client.get(reverse('channels.channel_claim'))
        self.assertContains(response, "TwiML")
        self.assertContains(response, claim_url)

        # can fetch the claim page
        response = self.client.get(claim_url)
        self.assertEqual(200, response.status_code)
        self.assertContains(response, 'TwiML')

        response = self.client.post(claim_url, dict(number='5512345678', country='AA'))
        self.assertTrue(response.context['form'].errors)

        response = self.client.post(claim_url, dict(country='US', number='12345678', url='https://twilio.com', role='SR', account_sid='abcd1234', account_token='abcd1234'))
        channel = self.org.channels.all().first()
        self.assertRedirects(response, reverse('channels.channel_configuration', args=[channel.uuid]))
        self.assertEqual(channel.channel_type, "TW")
        self.assertEqual(
            channel.config, dict(
                ACCOUNT_TOKEN='abcd1234', send_url='https://twilio.com', ACCOUNT_SID='abcd1234',
                callback_domain=channel.callback_domain
            )
        )

        response = self.client.post(claim_url, dict(country='US', number='12345678', url='https://twilio.com', role='SR', account_sid='abcd4321', account_token='abcd4321'))
        channel = self.org.channels.all().first()
        self.assertRedirects(response, reverse('channels.channel_configuration', args=[channel.uuid]))
        self.assertEqual(channel.channel_type, "TW")
        self.assertEqual(
            channel.config, dict(
                ACCOUNT_TOKEN='abcd4321', send_url='https://twilio.com', ACCOUNT_SID='abcd4321',
                callback_domain=channel.callback_domain
            )
        )

        self.org.channels.update(is_active=False)

        response = self.client.post(claim_url, dict(country='US', number='8080', url='https://twilio.com', role='SR', account_sid='abcd1234', account_token='abcd1234'))
        channel = self.org.channels.all().first()
        self.assertRedirects(response, reverse('channels.channel_configuration', args=[channel.uuid]))
        self.assertEqual(channel.channel_type, "TW")
        self.assertEqual(
            channel.config, dict(
                ACCOUNT_TOKEN='abcd1234', send_url='https://twilio.com', ACCOUNT_SID='abcd1234',
                callback_domain=channel.callback_domain
            )
        )
