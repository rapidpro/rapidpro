from __future__ import unicode_literals, absolute_import

import json

from django.test import override_settings
from django.urls import reverse
from mock import patch
from temba.tests import TembaTest
from ...models import Channel
from temba.contacts.models import ContactURN
from .tasks import resolve_twitter_ids


class TwitterTypeTest(TembaTest):
    def setUp(self):
        super(TwitterTypeTest, self).setUp()

        self.channel = Channel.create(self.org, self.user, None, 'TT', name="Twitter", address="billy_bob",
                                      role="SR", scheme='twitter', config={})

    @patch('twython.Twython.lookup_user')
    def test_resolve(self, mock_lookup_user):
        self.joe = self.create_contact("joe", twitter="therealjoe")
        urn = self.joe.get_urns()[0]

        # test no return value, shouldn't affect contact URN
        mock_lookup_user.return_value = []
        resolve_twitter_ids()

        urn.refresh_from_db()
        self.assertIsNone(urn.display)
        self.assertEqual("twitter:therealjoe", urn.identity)
        self.assertEqual("therealjoe", urn.path)

        # test a real return value
        mock_lookup_user.return_value = [dict(screen_name="therealjoe", id="123456")]
        resolve_twitter_ids()

        urn.refresh_from_db()
        self.assertEqual("twitter:123456", urn.identity)
        self.assertEqual("123456", urn.path)
        self.assertEqual("therealjoe", urn.display)
        self.assertEqual("twitter:123456#therealjoe", urn.urn)

        # create another URN for the same display name
        urn2 = ContactURN.create(self.org, self.joe, "twitter:therealjoe")
        resolve_twitter_ids()

        # this urn should have been deleted
        self.assertEqual(0, ContactURN.objects.filter(id=urn2.id).count())

        # disconnect joe's current URN and try again
        ContactURN.objects.filter(id=urn.id).update(contact=None)
        urn3 = ContactURN.create(self.org, self.joe, "twitter:therealjoe")
        resolve_twitter_ids()

        # this time should prefer new URN
        urn3.refresh_from_db()
        self.assertEqual(0, ContactURN.objects.filter(id=urn.id).count())
        self.assertEqual(urn3.id, self.joe.get_urns()[0].id)

    @override_settings(IS_PROD=True)
    @patch('twython.Twython.get_authentication_tokens')
    @patch('temba.utils.mage.MageClient.activate_twitter_stream')
    @patch('twython.Twython.get_authorized_tokens')
    def test_claim(self, mock_get_authorized_tokens, mock_activate_twitter_stream, mock_get_authentication_tokens):
        url = reverse('channels.claim_twitter')

        mock_get_authentication_tokens.return_value = {
            'oauth_token': 'abcde',
            'oauth_token_secret': '12345',
            'auth_url': 'http://example.com/auth'
        }

        # can't access claim page if not logged in
        response = self.client.get(url)
        self.assertEqual(response.status_code, 302)

        self.login(self.user)

        # also can't access if just a regular user
        response = self.client.get(url)
        self.assertEqual(response.status_code, 302)

        self.login(self.admin)

        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)

        # check that claim page URL appears on claim list page
        response = self.client.get(reverse('channels.channel_claim'))
        self.assertContains(response, url)

        # can fetch the claim page
        response = self.client.get(url)
        self.assertContains(response, "Connect Twitter")
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

        # try re-adding a Twitter handle which already exists
        response = self.client.get(url, {'oauth_verifier': 'vwxyz'})
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "A Twitter channel for that handle already exists")

        mock_get_authorized_tokens.return_value = {
            'screen_name': 'jimmy',
            'user_id': 123,
            'oauth_token': 'bcdef',
            'oauth_token_secret': '23456'
        }

        # try adding a new Twitter handle
        response = self.client.get(url, {'oauth_verifier': 'vwxyz'}, follow=True)
        self.assertNotIn('twitter_oauth_token', self.client.session)
        self.assertNotIn('twitter_oauth_token_secret', self.client.session)
        self.assertEqual(response.status_code, 200)

        channel = response.context['object']
        self.assertEqual(channel.address, 'jimmy')
        self.assertEqual(channel.name, '@jimmy')
        config = json.loads(channel.config)
        self.assertEqual(config['handle_id'], 123)
        self.assertEqual(config['oauth_token'], 'bcdef')
        self.assertEqual(config['oauth_token_secret'], '23456')

    @override_settings(IS_PROD=True)
    @patch('temba.utils.mage.MageClient._request')
    def test_release(self, mock_mage_request):
        # check that removing Twitter channel notifies Mage
        self.channel.release()

        mock_mage_request.assert_called_once_with('DELETE', 'twitter/%s' % self.channel.uuid)
