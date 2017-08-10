from __future__ import unicode_literals, absolute_import

import json

from django.contrib.auth.models import Group
from django.test import override_settings
from django.urls import reverse
from mock import patch
from temba.tests import TembaTest
from temba.utils.twitter import TwythonError
from ...models import Channel
from temba.contacts.models import ContactURN
from .tasks import resolve_twitter_ids


class TwitterActivityTypeTest(TembaTest):
    def setUp(self):
        super(TwitterActivityTypeTest, self).setUp()

        self.channel = Channel.create(self.org, self.user, None, 'TWT', name="Twitter Beta", address="beta_bob",
                                      role="SR", schemes=['twitter'],
                                      config={'api_key': 'ak1',
                                              'api_secret': 'as1',
                                              'access_token': 'at1',
                                              'access_token_secret': 'ats1',
                                              'handle_id': 'h123456',
                                              'webhook_id': 'wh45678'})

    @override_settings(IS_PROD=True)
    @patch('temba.utils.twitter.TembaTwython.subscribe_to_webhook')
    @patch('temba.utils.twitter.TembaTwython.register_webhook')
    @patch('twython.Twython.verify_credentials')
    def test_claim(self, mock_verify_credentials, mock_register_webhook, mock_subscribe_to_webhook):
        url = reverse('channels.claim_twitter_activity')

        self.login(self.admin)

        # check that channel is only available to beta users
        response = self.client.get(reverse('channels.channel_claim'))
        self.assertNotContains(response, 'channels/claim/twitter_activity/')

        Group.objects.get(name="Beta").user_set.add(self.admin)

        response = self.client.get(reverse('channels.channel_claim'))
        self.assertContains(response, 'channels/claim/twitter_activity/')

        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Connect Twitter Activity API")

        self.assertEqual(list(response.context['form'].fields.keys()),
                         ['api_key', 'api_secret', 'access_token', 'access_token_secret', 'loc'])

        # try submitting empty form
        response = self.client.post(url, {})
        self.assertEqual(response.status_code, 200)
        self.assertFormError(response, 'form', 'api_key', "This field is required.")
        self.assertFormError(response, 'form', 'api_secret', "This field is required.")
        self.assertFormError(response, 'form', 'access_token', "This field is required.")
        self.assertFormError(response, 'form', 'access_token_secret', "This field is required.")

        # try submitting with invalid credentials
        mock_verify_credentials.side_effect = TwythonError("Invalid credentials")

        response = self.client.post(url, {'api_key': 'ak', 'api_secret': 'as', 'access_token': 'at', 'access_token_secret': 'ats'})
        self.assertEqual(response.status_code, 200)
        self.assertFormError(response, 'form', None, "The provided Twitter credentials do not appear to be valid.")

        # try submitting for handle which already has a channel
        mock_verify_credentials.side_effect = None
        mock_verify_credentials.return_value = {'id': '345678', 'screen_name': "beta_bob"}

        response = self.client.post(url, {'api_key': 'ak', 'api_secret': 'as', 'access_token': 'at', 'access_token_secret': 'ats'})
        self.assertEqual(response.status_code, 200)
        self.assertFormError(response, 'form', None, "A Twitter channel already exists for that handle.")

        # try a valid submission
        mock_verify_credentials.return_value = {'id': '87654', 'screen_name': "jimmy"}
        mock_register_webhook.return_value = {'id': "1234567"}

        response = self.client.post(url, {'api_key': 'ak', 'api_secret': 'as', 'access_token': 'at', 'access_token_secret': 'ats'})
        self.assertEqual(response.status_code, 302)

        channel = Channel.objects.get(address='jimmy')
        self.assertEqual(json.loads(channel.config), {'handle_id': '87654', 'api_key': 'ak', 'api_secret': 'as',
                                                      'access_token': 'at', 'access_token_secret': 'ats', 'webhook_id': '1234567'})

        mock_register_webhook.assert_called_once_with('https://temba.ngrok.io/handlers/twitter/%s' % channel.uuid)
        mock_subscribe_to_webhook.assert_called_once_with("1234567")

    @override_settings(IS_PROD=True)
    @patch('temba.utils.twitter.TembaTwython.delete_webhook')
    def test_release(self, mock_delete_webhook):
        self.channel.release()

        mock_delete_webhook.assert_called_once_with('wh45678')

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
