
from mock import patch

from django.contrib.auth.models import Group
from django.test import override_settings
from django.urls import reverse

from temba.tests import TembaTest

from ...models import Channel


class TwitterTypeTest(TembaTest):
    def setUp(self):
        super().setUp()

        self.channel = Channel.create(
            self.org, self.user, None, "TT", name="Twitter", address="billy_bob", role="SR", config={}
        )

    @override_settings(IS_PROD=True)
    @patch("twython.Twython.get_authentication_tokens")
    @patch("temba.utils.mage.MageClient.activate_twitter_stream")
    @patch("twython.Twython.get_authorized_tokens")
    def test_claim(self, mock_get_authorized_tokens, mock_activate_twitter_stream, mock_get_authentication_tokens):
        url = reverse("channels.types.twitter.claim")

        mock_get_authentication_tokens.return_value = {
            "oauth_token": "abcde",
            "oauth_token_secret": "12345",
            "auth_url": "http://example.com/auth",
        }

        # can't access claim page if not logged in
        response = self.client.get(url)
        self.assertEqual(response.status_code, 302)

        Group.objects.get(name="Beta").user_set.add(self.admin)
        self.login(self.admin)

        # can fetch the claim page
        response = self.client.get(url)
        self.assertContains(response, "Connect Twitter")
        self.assertEqual(response.context["twitter_auth_url"], "http://example.com/auth")
        self.assertEqual(self.client.session["twitter_oauth_token"], "abcde")
        self.assertEqual(self.client.session["twitter_oauth_token_secret"], "12345")

        mock_activate_twitter_stream.return_value = {}

        mock_get_authorized_tokens.return_value = {
            "screen_name": "billy_bob",
            "user_id": 123,
            "oauth_token": "bcdef",
            "oauth_token_secret": "23456",
        }

        # try re-adding a Twitter handle which already exists
        response = self.client.get(url, {"oauth_verifier": "vwxyz"})
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "A Twitter channel for that handle already exists")

        mock_get_authorized_tokens.return_value = {
            "screen_name": "jimmy",
            "user_id": 123,
            "oauth_token": "bcdef",
            "oauth_token_secret": "23456",
        }

        # try adding a new Twitter handle
        response = self.client.get(url, {"oauth_verifier": "vwxyz"}, follow=True)
        self.assertNotIn("twitter_oauth_token", self.client.session)
        self.assertNotIn("twitter_oauth_token_secret", self.client.session)
        self.assertEqual(response.status_code, 200)

        channel = response.context["object"]
        self.assertEqual(channel.address, "jimmy")
        self.assertEqual(channel.name, "@jimmy")
        config = channel.config
        self.assertEqual(config["handle_id"], 123)
        self.assertEqual(config["oauth_token"], "bcdef")
        self.assertEqual(config["oauth_token_secret"], "23456")

    @override_settings(IS_PROD=True)
    @patch("temba.utils.mage.MageClient._request")
    def test_release(self, mock_mage_request):
        # check that removing Twitter channel notifies Mage
        self.channel.release()

        mock_mage_request.assert_called_once_with("DELETE", "twitter/%s" % self.channel.uuid)
