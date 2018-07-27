# -*- coding: utf-8 -*-
from __future__ import absolute_import, division, print_function, unicode_literals

from django.urls import reverse
from mock import patch

from temba.channels.models import Channel
from temba.tests import TembaTest, MockResponse


class NexmoTypeTest(TembaTest):

    @patch('temba.utils.nexmo.time.sleep')
    def test_claim(self, mock_time_sleep):
        mock_time_sleep.return_value = None
        self.login(self.admin)

        claim_nexmo = reverse('channels.types.nexmo.claim')

        # remove any existing channels
        self.org.channels.update(is_active=False)

        # make sure nexmo is on the claim page
        response = self.client.get(reverse('channels.channel_claim'))
        self.assertContains(response, "Nexmo")

        response = self.client.get(claim_nexmo)
        self.assertEqual(response.status_code, 302)
        response = self.client.get(claim_nexmo, follow=True)
        self.assertEqual(response.request['PATH_INFO'], reverse('orgs.org_nexmo_connect'))

        nexmo_config = dict(NEXMO_KEY='nexmo-key', NEXMO_SECRET='nexmo-secret', NEXMO_UUID='nexmo-uuid',
                            NEXMO_APP_ID='nexmo-app-id', NEXMO_APP_PRIVATE_KEY='nexmo-app-private-key')
        self.org.config = nexmo_config
        self.org.save()

        # hit the claim page, should now have a claim nexmo link
        response = self.client.get(reverse('channels.channel_claim'))
        self.assertContains(response, claim_nexmo)

        # try adding a shortcode
        with patch('requests.get') as nexmo_get:
            with patch('requests.post') as nexmo_post:
                nexmo_get.side_effect = [
                    MockResponse(200, '{"count":0,"numbers":[] }'),
                    MockResponse(200,
                                 '{"count":1,"numbers":[{"features": ["SMS"], "type":"mobile-lvn",'
                                 '"country":"US","msisdn":"8080"}] }'),
                    MockResponse(200,
                                 '{"count":1,"numbers":[{"features": ["SMS"], "type":"mobile-lvn",'
                                 '"country":"US","msisdn":"8080"}] }'),
                ]
                nexmo_post.return_value = MockResponse(200, '{"error-code": "200"}')
                response = self.client.post(claim_nexmo, dict(country='US', phone_number='8080'))
                self.assertRedirects(response, reverse('public.public_welcome') + "?success")
                channel = Channel.objects.filter(address='8080').first()
                self.assertTrue(Channel.ROLE_SEND in channel.role)
                self.assertTrue(Channel.ROLE_RECEIVE in channel.role)
                self.assertFalse(Channel.ROLE_ANSWER in channel.role)
                self.assertFalse(Channel.ROLE_CALL in channel.role)
                self.assertFalse(mock_time_sleep.called)
                Channel.objects.all().delete()

        # try buying a number not on the account
        with patch('requests.get') as nexmo_get:
            with patch('requests.post') as nexmo_post:
                nexmo_get.side_effect = [
                    MockResponse(200, '{"count":0,"numbers":[] }'),
                    MockResponse(200, '{"count":0,"numbers":[] }'),
                    MockResponse(200,
                                 '{"count":1,"numbers":[{"features": ["sms", "voice"], "type":"mobile",'
                                 '"country":"US","msisdn":"+12065551212"}] }'),
                ]
                nexmo_post.return_value = MockResponse(200, '{"error-code": "200"}')
                response = self.client.post(claim_nexmo, dict(country='US', phone_number='+12065551212'))
                self.assertRedirects(response, reverse('public.public_welcome') + "?success")
                channel = Channel.objects.filter(address='+12065551212').first()
                self.assertTrue(Channel.ROLE_SEND in channel.role)
                self.assertTrue(Channel.ROLE_RECEIVE in channel.role)
                self.assertTrue(Channel.ROLE_ANSWER in channel.role)
                self.assertTrue(Channel.ROLE_CALL in channel.role)
                Channel.objects.all().delete()

        # Try when we get 429 too many requests, we retry
        with patch('requests.get') as nexmo_get:
            with patch('requests.post') as nexmo_post:
                nexmo_get.side_effect = [
                    MockResponse(429, '{"error_code":429,"message":"max limit, retry later" }'),
                    MockResponse(200, '{"count":0,"numbers":[] }'),
                    MockResponse(429, '{"error_code":429,"message":"max limit, retry later" }'),
                    MockResponse(200, '{"count":0,"numbers":[] }'),
                    MockResponse(429, '{"error_code":429,"message":"max limit, retry later" }'),
                    MockResponse(200,
                                 '{"count":1,"numbers":[{"features": ["sms", "voice"], "type":"mobile",'
                                 '"country":"US","msisdn":"+12065551212"}] }'),
                ]
                nexmo_post.side_effect = [
                    MockResponse(429, '{"error_code":429,"message":"max limit, retry later" }'),
                    MockResponse(200, '{"error-code": "200"}'),
                    MockResponse(429, '{"error_code":429,"message":"max limit, retry later" }'),
                    MockResponse(200, '{"error-code": "200"}')
                ]
                response = self.client.post(claim_nexmo, dict(country='US', phone_number='+12065551212'))
                self.assertRedirects(response, reverse('public.public_welcome') + "?success")
                channel = Channel.objects.filter(address='+12065551212').first()
                self.assertTrue(Channel.ROLE_SEND in channel.role)
                self.assertTrue(Channel.ROLE_RECEIVE in channel.role)
                self.assertTrue(Channel.ROLE_ANSWER in channel.role)
                self.assertTrue(Channel.ROLE_CALL in channel.role)
                Channel.objects.all().delete()
                self.assertEqual(mock_time_sleep.call_count, 5)

        # try failing to buy a number not on the account
        with patch('requests.get') as nexmo_get:
            with patch('requests.post') as nexmo_post:
                nexmo_get.side_effect = [
                    MockResponse(200, '{"count":0,"numbers":[] }'),
                    MockResponse(200, '{"count":0,"numbers":[] }'),
                ]
                nexmo_post.side_effect = Exception('Error')
                response = self.client.post(claim_nexmo, dict(country='US', phone_number='+12065551212'))
                self.assertTrue(response.context['form'].errors)
                self.assertContains(response, "There was a problem claiming that number, "
                                              "please check the balance on your account. "
                                              "Note that you can only claim numbers after "
                                              "adding credit to your Nexmo account.")
                Channel.objects.all().delete()

        # let's add a number already connected to the account
        with patch('requests.get') as nexmo_get:
            with patch('requests.post') as nexmo_post:
                nexmo_get.return_value = MockResponse(200,
                                                      '{"count":1,"numbers":[{"features": ["SMS", "VOICE"], '
                                                      '"type":"mobile-lvn","country":"US","msisdn":"13607884540"}] }')
                nexmo_post.return_value = MockResponse(200, '{"error-code": "200"}')

                # make sure our number appears on the claim page
                response = self.client.get(claim_nexmo)
                self.assertNotIn('account_trial', response.context)
                self.assertContains(response, '360-788-4540')

                # claim it
                response = self.client.post(claim_nexmo, dict(country='US', phone_number='13607884540'))
                self.assertRedirects(response, reverse('public.public_welcome') + "?success")

                # make sure it is actually connected
                channel = Channel.objects.get(channel_type='NX', org=self.org)
                self.assertTrue(Channel.ROLE_SEND in channel.role)
                self.assertTrue(Channel.ROLE_RECEIVE in channel.role)
                self.assertTrue(Channel.ROLE_ANSWER in channel.role)
                self.assertTrue(Channel.ROLE_CALL in channel.role)

                channel_config = channel.config
                self.assertEqual(channel_config[Channel.CONFIG_NEXMO_API_KEY], 'nexmo-key')
                self.assertEqual(channel_config[Channel.CONFIG_NEXMO_API_SECRET], 'nexmo-secret')
                self.assertEqual(channel_config[Channel.CONFIG_NEXMO_APP_ID], 'nexmo-app-id')
                self.assertEqual(channel_config[Channel.CONFIG_NEXMO_APP_PRIVATE_KEY], 'nexmo-app-private-key')

                # test the update page for nexmo
                update_url = reverse('channels.channel_update', args=[channel.pk])
                response = self.client.get(update_url)

                # try changing our address
                updated = response.context['form'].initial
                updated['address'] = 'MTN'
                updated['alert_email'] = 'foo@bar.com'

                response = self.client.post(update_url, updated)
                channel = Channel.objects.get(pk=channel.id)

                self.assertEqual('MTN', channel.address)

                # add a canada number
                nexmo_get.return_value = MockResponse(200, '{"count":1,"numbers":[{"features": ["SMS", "VOICE"], "type":"mobile-lvn","country":"CA","msisdn":"15797884540"}] }')
                nexmo_post.return_value = MockResponse(200, '{"error-code": "200"}')

                # make sure our number appears on the claim page
                response = self.client.get(claim_nexmo)
                self.assertNotIn('account_trial', response.context)
                self.assertContains(response, '579-788-4540')

                # claim it
                response = self.client.post(claim_nexmo, dict(country='CA', phone_number='15797884540'))
                self.assertRedirects(response, reverse('public.public_welcome') + "?success")

                # make sure it is actually connected
                self.assertTrue(Channel.objects.filter(channel_type='NX', org=self.org, address='+15797884540').first())

                # as is our old one
                self.assertTrue(Channel.objects.filter(channel_type='NX', org=self.org, address='MTN').first())

                config_url = reverse('channels.channel_configuration', args=[channel.uuid])
                response = self.client.get(config_url)
                self.assertEqual(200, response.status_code)

                self.assertContains(response, reverse('courier.nx', args=[channel.uuid, 'receive']))
                self.assertContains(response, reverse('courier.nx', args=[channel.uuid, 'status']))
                self.assertContains(response, reverse('handlers.nexmo_call_handler', args=['answer', channel.uuid]))

                call_handler_event_url = reverse('handlers.nexmo_call_handler', args=['event', channel.uuid])
                response = self.client.get(call_handler_event_url)

                self.assertEqual(response.status_code, 200)
                self.assertEqual(len(response.content), 0)
