from __future__ import unicode_literals, absolute_import

import json

from django.contrib.auth.models import Group
from django.urls import reverse
from mock import patch
from temba.tests import TembaTest
from temba.utils.twitter import TwythonError
from ...models import Channel


class TwitterTypeTest(TembaTest):
    def setUp(self):
        self.channel = Channel.create(self.org, self.user, None, 'TT', name="Twitter", address="billy_bob",
                                      role="SR", scheme='twitter', config={})
        self.beta_channel = Channel.create(self.org, self.user, None, 'TT', name="Twitter Beta", address="beta_bob",
                                           role="SR", scheme='twitter',
                                           config={'api_key': 'ak1',
                                                   'api_secret': 'as1',
                                                   'access_token': 'at1',
                                                   'access_token_secret': 'ats1',
                                                   'handle_id': 'h123456',
                                                   'webhook_id': 'wh45678'})

    @patch('twython.Twython.get_authentication_tokens')
    @patch('temba.utils.mage.MageClient.activate_twitter_stream')
    @patch('twython.Twython.get_authorized_tokens')
    def test_claim(self, mock_get_authorized_tokens, mock_activate_twitter_stream, mock_get_authentication_tokens):
        self.login(self.admin)

        self.channel.delete()  # remove existing twitter channel

        claim_url = reverse('channels.channel_claim_twitter')

        mock_get_authentication_tokens.return_value = {
            'oauth_token': 'abcde',
            'oauth_token_secret': '12345',
            'auth_url': 'http://example.com/auth'
        }

        response = self.client.get(claim_url)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context['twitter_auth_url'], 'http://example.com/auth')
        self.assertEqual(self.client.session['twitter_oauth_token'], 'abcde')
        self.assertEqual(self.client.session['twitter_oauth_token_secret'], '12345')

        mock_activate_twitter_stream.return_value = {}

        mock_get_authorized_tokens.return_value = {
            'screen_name': 'billy_bob',
            'user_id': 123,
            'oauth_token': 'bcdef',
            'oauth_token_secret': '23456'
        }

        response = self.client.get(claim_url, {'oauth_verifier': 'vwxyz'}, follow=True)
        self.assertNotIn('twitter_oauth_token', self.client.session)
        self.assertNotIn('twitter_oauth_token_secret', self.client.session)
        self.assertEqual(response.status_code, 200)

        channel = response.context['object']
        self.assertEqual(channel.address, 'billy_bob')
        self.assertEqual(channel.name, '@billy_bob')
        config = json.loads(channel.config)
        self.assertEqual(config['handle_id'], 123)
        self.assertEqual(config['oauth_token'], 'bcdef')
        self.assertEqual(config['oauth_token_secret'], '23456')

        # re-add same account but with different auth credentials
        s = self.client.session
        s['twitter_oauth_token'] = 'cdefg'
        s['twitter_oauth_token_secret'] = '34567'
        s.save()

        mock_get_authorized_tokens.return_value = {
            'screen_name': 'billy_bob',
            'user_id': 123,
            'oauth_token': 'defgh',
            'oauth_token_secret': '45678'
        }

        response = self.client.get(claim_url, {'oauth_verifier': 'uvwxy'}, follow=True)
        self.assertEqual(response.status_code, 200)

        channel = response.context['object']
        self.assertEqual(channel.address, 'billy_bob')
        config = json.loads(channel.config)
        self.assertEqual(config['handle_id'], 123)
        self.assertEqual(config['oauth_token'], 'defgh')
        self.assertEqual(config['oauth_token_secret'], '45678')

    @patch('temba.utils.twitter.TembaTwython.subscribe_to_webhook')
    @patch('temba.utils.twitter.TembaTwython.register_webhook')
    @patch('twython.Twython.verify_credentials')
    def test_claim_beta(self, mock_verify_credentials, mock_register_webhook, mock_subscribe_to_webhook):
        self.login(self.admin)

        claim_url = reverse('channels.channel_claim')

        response = self.client.get(claim_url)
        self.assertNotContains(response, 'claim_twitter_beta')

        Group.objects.get(name="Beta").user_set.add(self.admin)

        response = self.client.get(claim_url)
        self.assertContains(response, 'claim_twitter_beta')

        claim_url = reverse('channels.channel_claim_twitter_beta')

        response = self.client.get(claim_url)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(list(response.context['form'].fields.keys()),
                         ['api_key', 'api_secret', 'access_token', 'access_token_secret', 'loc'])

        # try submitting empty form
        response = self.client.post(claim_url, {})
        self.assertEqual(response.status_code, 200)
        self.assertFormError(response, 'form', 'api_key', "This field is required.")
        self.assertFormError(response, 'form', 'api_secret', "This field is required.")
        self.assertFormError(response, 'form', 'access_token', "This field is required.")
        self.assertFormError(response, 'form', 'access_token_secret', "This field is required.")

        # try submitting with invalid credentials
        mock_verify_credentials.side_effect = TwythonError("Invalid credentials")

        response = self.client.post(claim_url, {'api_key': 'ak', 'api_secret': 'as', 'access_token': 'at', 'access_token_secret': 'ats'})
        self.assertEqual(response.status_code, 200)
        self.assertFormError(response, 'form', None, "The provided Twitter credentials do not appear to be valid.")

        # try submitting for handle which already has a channel
        mock_verify_credentials.side_effect = None
        mock_verify_credentials.return_value = {'id': '345678', 'screen_name': "billy_bob"}

        response = self.client.post(claim_url, {'api_key': 'ak', 'api_secret': 'as', 'access_token': 'at', 'access_token_secret': 'ats'})
        self.assertEqual(response.status_code, 200)
        self.assertFormError(response, 'form', None, "A Twitter channel already exists for that handle.")

        # try a valid submission
        mock_verify_credentials.return_value = {'id': '87654', 'screen_name': "jimmy"}
        mock_register_webhook.return_value = {'id': "1234567"}

        response = self.client.post(claim_url, {'api_key': 'ak', 'api_secret': 'as', 'access_token': 'at', 'access_token_secret': 'ats'})
        self.assertEqual(response.status_code, 302)

        channel = Channel.objects.get(address='jimmy')
        self.assertEqual(json.loads(channel.config), {'handle_id': '87654', 'api_key': 'ak', 'api_secret': 'as',
                                                      'access_token': 'at', 'access_token_secret': 'ats'})

        mock_register_webhook.assert_called_once_with('https://temba.ngrok.io/handlers/twitter/%s' % channel.uuid)
        mock_subscribe_to_webhook.assert_called_once_with("1234567")

    @patch('temba.utils.mage.MageClient._request')
    def test_release(self, mock_mage_request):
        # check that removing Twitter channel notifies Mage
        self.channel.release()

        mock_mage_request.assert_called_once_with('DELETE', 'twitter/%s' % self.channel.uuid)

    @patch('temba.utils.twitter.TembaTwython.delete_webhook')
    def test_release_beta(self, mock_delete_webhook):
        self.beta_channel.release()

        mock_delete_webhook.assert_called_once_with('wh45678')
