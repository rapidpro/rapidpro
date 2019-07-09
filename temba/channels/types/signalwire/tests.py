from unittest.mock import patch

from django.forms import ValidationError
from django.urls import reverse

from temba.tests import MockResponse, TembaTest

from ...models import Channel
from .type import SignalWireType


class SignalWireTest(TembaTest):
    def test_claim(self):
        Channel.objects.all().delete()

        url = reverse("channels.types.signalwire.claim")
        self.login(self.admin)

        response = self.client.get(reverse("channels.channel_claim"))
        self.assertNotContains(response, url)

        # change to LA timezone
        self.org.timezone = "America/Los_Angeles"
        self.org.save(update_fields=["timezone"])

        response = self.client.get(reverse("channels.channel_claim"))
        self.assertContains(response, url)

        response = self.client.get(url)
        self.assertEqual(200, response.status_code)
        post_data = response.context["form"].initial

        post_data["country"] = "US"
        post_data["number"] = "2065551212"
        post_data["domain"] = "temba.signalwire.com"
        post_data["project_key"] = "key123"
        post_data["api_token"] = "token123"

        # try once with an error
        with patch("requests.get") as mock_get:
            mock_get.return_value = MockResponse(400, '{ "error": "true" }')
            response = self.client.post(url, post_data)
            self.assertEqual(200, response.status_code)
            self.assertFalse(Channel.objects.all())

            self.assertContains(response, "Unable to connect to SignalWire")

        # then with missing number
        with patch("requests.get") as mock_get:
            mock_get.return_value = MockResponse(200, "{}")
            response = self.client.post(url, post_data)
            self.assertEqual(200, response.status_code)
            self.assertFalse(Channel.objects.all())
            self.assertContains(response, "Unable to find phone")

        # success this time
        with patch("requests.get") as mock_get:
            mock_get.return_value = MockResponse(
                200, '{"incoming_phone_numbers": [{"sid": "abc123", "phone_number": "+12065551212"}]}'
            )
            response = self.client.post(url, post_data)
            self.assertEqual(302, response.status_code)
            mock_get.assert_called_with(
                "https://temba.signalwire.com/api/laml/2010-04-01/Accounts/key123/IncomingPhoneNumbers.json",
                auth=("key123", "token123"),
            )

        channel = Channel.objects.get()

        self.assertEqual("https://temba.signalwire.com/api/laml", channel.config["base_url"])
        self.assertEqual("key123", channel.config["account_sid"])
        self.assertEqual("token123", channel.config["auth_token"])

        self.assertEqual("+12065551212", channel.address)
        self.assertEqual("US", channel.country)
        self.assertEqual("SW", channel.channel_type)

        # test activating the channel
        with patch("requests.get") as mock_get:
            mock_get.return_value = MockResponse(400, '{ "error": "true" }')
            with self.assertRaises(ValidationError):
                SignalWireType().activate(channel)

        # test activating the channel
        with patch("requests.get") as mock_get:
            mock_get.return_value = MockResponse(200, "{}")
            with self.assertRaises(ValidationError):
                SignalWireType().activate(channel)

        # failure registering
        with patch("requests.get") as mock_get:
            mock_get.return_value = MockResponse(
                200, '{"incoming_phone_numbers": [{"sid": "abc123", "phone_number": "+12065551212"}]}'
            )

            with patch("requests.post") as mock_post:
                mock_post.return_value = MockResponse(400, "{}")
                with self.assertRaises(ValidationError):
                    SignalWireType().activate(channel)

        # success registering
        with patch("requests.get") as mock_get:
            mock_get.return_value = MockResponse(
                200, '{"incoming_phone_numbers": [{"sid": "abc123", "phone_number": "+12065551212"}]}'
            )

            with patch("requests.post") as mock_post:
                mock_post.return_value = MockResponse(200, "{}")
                SignalWireType().activate(channel)

                self.assertEqual(
                    mock_post.mock_calls[0][1][0],
                    "https://temba.signalwire.com/api/laml/2010-04-01/Accounts/key123/IncomingPhoneNumbers/abc123.json",
                )

        # deactivate our channel
        with self.settings(IS_PROD=True):
            channel.release()
