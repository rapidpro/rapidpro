from unittest.mock import patch

import requests

from django.test import override_settings
from django.urls import reverse

from temba.contacts.models import URN
from temba.tests import TembaTest

from ...models import Channel


def mocked_requests_get(*args, **kwargs):
    class MockedResponse:
        def __init__(self, data, status_code):
            self.data = data
            self.status_code = status_code

        def json(self):
            return self.data

    headers = kwargs["headers"]
    if not ("Authorization" in headers):
        return MockedResponse({}, 401)
    elif headers["Authorization"] == "Bot fake-valid-token":
        return MockedResponse({"username": "Rapidpro-Test-Bot-Do-Not-Use"}, 200)
    elif headers["Authorization"] == "Bot fake-network-error":
        raise requests.ConnectTimeout()
    else:
        return MockedResponse({}, 401)


class DiscordTypeTest(TembaTest):
    def setUp(self):
        super().setUp()

    @patch("requests.get", side_effect=mocked_requests_get)
    @override_settings(IS_PROD=True)
    def test_claim(self, mocked):
        url = reverse("channels.types.discord.claim")

        self.login(self.admin)

        # check that claim page URL appears on claim list page
        response = self.client.get(reverse("channels.channel_claim"))
        self.assertContains(response, url)

        # can fetch the claim page
        response = self.client.get(url)
        self.assertContains(response, "Connect Discord")

        # claim with an invalid token
        response = self.client.post(url, {"auth_token": "invalid", "proxy_url": "http://foo.bar"})
        self.assertEqual(200, response.status_code)
        self.assertEqual(
            "Couldn't log in using that bot token. Please check and try again",
            response.context["form"].errors["auth_token"][0],
        )

        # Test what happens if discord is unreachable
        response = self.client.post(url, {"auth_token": "fake-network-error", "proxy_url": "http://foo.bar"})
        self.assertEqual(200, response.status_code)
        self.assertEqual(
            "An error occurred accessing the Discord API. Please try again",
            response.context["form"].errors["auth_token"][0],
        )

        # Claim with a (fake) valid token
        response = self.client.post(url, {"auth_token": "fake-valid-token", "proxy_url": "http://foo.bar"})
        channel = Channel.objects.get(name="Rapidpro-Test-Bot-Do-Not-Use")
        self.assertEqual(channel.channel_type, "DS")
        self.assertEqual(
            channel.config,
            {
                "auth_token": "fake-valid-token",
                "send_url": "http://foo.bar/discord/rp/send",
                "callback_domain": channel.callback_domain,
            },
        )

        self.assertRedirect(response, reverse("channels.channel_read", args=[channel.uuid]))
        self.assertEqual(302, response.status_code)

        response = self.client.post(url, {"auth_token": "fake-valid-token", "proxy_url": "http://foo.bar"})
        self.assertEqual(
            "A Discord channel for this bot already exists on your account.",
            response.context["form"].errors["auth_token"][0],
        )

        contact = self.create_contact("Discord User", urn=URN.from_discord("750841288886321253"))

        # make sure we our telegram channel satisfies as a send channel
        response = self.client.get(reverse("contacts.contact_read", args=[contact.uuid]))
        send_channel = response.context["send_channel"]
        self.assertIsNotNone(send_channel)
        self.assertEqual(send_channel.channel_type, "DS")
        # Release the channel. We don't test it separately, so this gives us full coverage
        channel.release()
