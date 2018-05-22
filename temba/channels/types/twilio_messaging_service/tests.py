# -*- coding: utf-8 -*-
from __future__ import absolute_import, division, print_function, unicode_literals

from django.urls import reverse
from mock import patch
from twilio import TwilioRestException

from temba.channels.views import TWILIO_SUPPORTED_COUNTRIES
from temba.orgs.models import ACCOUNT_SID, ACCOUNT_TOKEN, APPLICATION_SID
from temba.tests import TembaTest
from temba.tests.twilio import MockTwilioClient, MockRequestValidator


class TwilioMessagingServiceTypeTest(TembaTest):

    @patch('temba.ivr.clients.TwilioClient', MockTwilioClient)
    @patch('twilio.util.RequestValidator', MockRequestValidator)
    def test_claim(self):

        self.login(self.admin)

        claim_twilio_ms = reverse('channels.types.twilio_messaging_service.claim')

        # remove any existing channels
        self.org.channels.all().delete()

        # make sure twilio is on the claim page
        response = self.client.get(reverse('channels.channel_claim'))
        self.assertContains(response, "Twilio")

        response = self.client.get(claim_twilio_ms)
        self.assertEqual(response.status_code, 302)
        response = self.client.get(claim_twilio_ms, follow=True)
        self.assertEqual(response.request['PATH_INFO'], reverse('orgs.org_twilio_connect'))

        twilio_config = dict()
        twilio_config[ACCOUNT_SID] = 'account-sid'
        twilio_config[ACCOUNT_TOKEN] = 'account-token'
        twilio_config[APPLICATION_SID] = 'TwilioTestSid'

        self.org.config = twilio_config
        self.org.save()

        response = self.client.get(reverse('channels.channel_claim'))
        self.assertContains(response, claim_twilio_ms)

        response = self.client.get(claim_twilio_ms)
        self.assertIn('account_trial', response.context)
        self.assertFalse(response.context['account_trial'])

        with patch('temba.orgs.models.Org.get_twilio_client') as mock_get_twilio_client:
            mock_get_twilio_client.return_value = None

            response = self.client.get(claim_twilio_ms)
            self.assertRedirects(response, reverse('orgs.org_twilio_connect'))

            mock_get_twilio_client.side_effect = TwilioRestException(401, 'http://twilio', msg='Authentication Failure', code=20003)

            response = self.client.get(claim_twilio_ms)
            self.assertRedirects(response, reverse('orgs.org_twilio_connect'))

        with patch('temba.tests.twilio.MockTwilioClient.MockAccounts.get') as mock_get:
            mock_get.return_value = MockTwilioClient.MockAccount('Trial')

            response = self.client.get(claim_twilio_ms)
            self.assertIn('account_trial', response.context)
            self.assertTrue(response.context['account_trial'])

        response = self.client.get(claim_twilio_ms)
        self.assertEqual(response.context['form'].fields['country'].choices, list(TWILIO_SUPPORTED_COUNTRIES))
        self.assertContains(response, "icon-channel-twilio")

        response = self.client.post(claim_twilio_ms, dict())
        self.assertTrue(response.context['form'].errors)

        response = self.client.post(claim_twilio_ms, dict(country='US', messaging_service_sid='MSG-SERVICE-SID'))
        channel = self.org.channels.get()
        self.assertRedirects(response, reverse('channels.channel_configuration', args=[channel.uuid]))
        self.assertEqual(channel.channel_type, "TMS")

        channel_config = channel.config
        self.assertEqual(channel_config['messaging_service_sid'], 'MSG-SERVICE-SID')
        self.assertTrue(channel_config['account_sid'])
        self.assertTrue(channel_config['auth_token'])
