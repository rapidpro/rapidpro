from __future__ import unicode_literals, absolute_import

import json

from django.urls import reverse
from mock import patch

from temba.channels.models import Channel
from temba.tests import TembaTest, MockResponse


class PlivoTypeTest(TembaTest):

    def test_claim(self):
        self.login(self.admin)

        # remove any existing channels
        self.org.channels.update(is_active=False)

        connect_plivo_url = reverse('orgs.org_plivo_connect')
        claim_plivo_url = reverse('channels.claim_plivo')

        # make sure plivo is on the claim page
        response = self.client.get(reverse('channels.channel_claim'))
        self.assertContains(response, "Plivo")
        self.assertContains(response, reverse('channels.claim_plivo'))

        with patch('requests.get') as plivo_get:
            plivo_get.return_value = MockResponse(400, {})
            response = self.client.get(claim_plivo_url)

            self.assertEqual(response.status_code, 302)

            response = self.client.get(claim_plivo_url, follow=True)

            self.assertEqual(response.request['PATH_INFO'], reverse('orgs.org_plivo_connect'))

        with patch('requests.get') as plivo_get:
            plivo_get.return_value = MockResponse(400, json.dumps(dict()))

            # try hit the claim page, should be redirected; no credentials in session
            response = self.client.get(claim_plivo_url, follow=True)
            self.assertFalse('account_trial' in response.context)
            self.assertContains(response, connect_plivo_url)

        # let's add a number already connected to the account
        with patch('requests.get') as plivo_get:
            with patch('requests.post') as plivo_post:
                plivo_get.return_value = MockResponse(
                    200,
                    json.dumps(
                        dict(objects=[
                            dict(number='16062681435', region="California, UNITED STATES"),
                            dict(number='8080', region='GUADALAJARA, MEXICO')
                        ])
                    )
                )
                plivo_post.return_value = MockResponse(202, json.dumps(dict(status='changed', app_id='app-id')))

                # make sure our numbers appear on the claim page
                response = self.client.get(claim_plivo_url)
                self.assertContains(response, "+1 606-268-1435")
                self.assertContains(response, "8080")
                self.assertContains(response, 'US')
                self.assertContains(response, 'MX')

                # claim it the US number
                session = self.client.session
                session[Channel.CONFIG_PLIVO_AUTH_ID] = 'auth-id'
                session[Channel.CONFIG_PLIVO_AUTH_TOKEN] = 'auth-token'
                session.save()

                self.assertTrue(Channel.CONFIG_PLIVO_AUTH_ID in self.client.session)
                self.assertTrue(Channel.CONFIG_PLIVO_AUTH_TOKEN in self.client.session)

                response = self.client.post(claim_plivo_url, dict(phone_number='+1 606-268-1435', country='US'))
                self.assertRedirects(response, reverse('public.public_welcome') + "?success")

                # make sure it is actually connected
                channel = Channel.objects.get(channel_type='PL', org=self.org)
                self.assertEqual(channel.role, Channel.ROLE_SEND + Channel.ROLE_RECEIVE)
                self.assertEqual(channel.config_json(), {
                    Channel.CONFIG_PLIVO_AUTH_ID: 'auth-id',
                    Channel.CONFIG_PLIVO_AUTH_TOKEN: 'auth-token',
                    Channel.CONFIG_PLIVO_APP_ID: 'app-id',
                    Channel.CONFIG_CALLBACK_DOMAIN: 'app.rapidpro.io'
                })
                self.assertEqual(channel.address, "+16062681435")
                # no more credential in the session
                self.assertFalse(Channel.CONFIG_PLIVO_AUTH_ID in self.client.session)
                self.assertFalse(Channel.CONFIG_PLIVO_AUTH_TOKEN in self.client.session)

        # delete existing channels
        Channel.objects.all().delete()

        with patch('temba.channels.views.plivo.RestAPI.get_account') as mock_plivo_get_account:
            with patch('temba.channels.views.plivo.RestAPI.create_application') as mock_plivo_create_application:

                with patch('temba.channels.types.plivo.views.plivo.RestAPI.get_number') as mock_plivo_get_number:
                    with patch('temba.channels.types.plivo.views.plivo.RestAPI.buy_phone_number') as mock_plivo_buy_phone_number:
                        mock_plivo_get_account.return_value = (200, MockResponse(200, json.dumps(dict())))

                        mock_plivo_create_application.return_value = (200, dict(app_id='app-id'))

                        mock_plivo_get_number.return_value = (400, MockResponse(400, json.dumps(dict())))

                        response_body = json.dumps({
                            'status': 'fulfilled',
                            'message': 'created',
                            'numbers': [{'status': 'Success', 'number': '27816855210'}],
                            'api_id': '4334c747-9e83-11e5-9147-22000acb8094'
                        })
                        mock_plivo_buy_phone_number.return_value = (201, MockResponse(201, response_body))

                        # claim it the US number
                        session = self.client.session
                        session[Channel.CONFIG_PLIVO_AUTH_ID] = 'auth-id'
                        session[Channel.CONFIG_PLIVO_AUTH_TOKEN] = 'auth-token'
                        session.save()

                        self.assertTrue(Channel.CONFIG_PLIVO_AUTH_ID in self.client.session)
                        self.assertTrue(Channel.CONFIG_PLIVO_AUTH_TOKEN in self.client.session)

                        response = self.client.post(claim_plivo_url, dict(phone_number='+1 606-268-1440', country='US'))
                        self.assertRedirects(response, reverse('public.public_welcome') + "?success")

                        # make sure it is actually connected
                        channel = Channel.objects.get(channel_type='PL', org=self.org)
                        self.assertEqual(channel.config_json(), {
                            Channel.CONFIG_PLIVO_AUTH_ID: 'auth-id',
                            Channel.CONFIG_PLIVO_AUTH_TOKEN: 'auth-token',
                            Channel.CONFIG_PLIVO_APP_ID: 'app-id',
                            Channel.CONFIG_CALLBACK_DOMAIN: 'app.rapidpro.io'
                        })
                        self.assertEqual(channel.address, "+16062681440")
                        # no more credential in the session
                        self.assertFalse(Channel.CONFIG_PLIVO_AUTH_ID in self.client.session)
                        self.assertFalse(Channel.CONFIG_PLIVO_AUTH_TOKEN in self.client.session)
