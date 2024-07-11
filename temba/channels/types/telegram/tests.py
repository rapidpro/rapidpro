from unittest.mock import patch

from django.urls import reverse

from temba.tests import MockResponse, TembaTest

from ...models import Channel
from .type import TelegramType


class TelegramTypeTest(TembaTest):
    def setUp(self):
        super().setUp()

        self.channel = self.create_channel(
            "TG",
            "Telegram",
            "12345",
            config={"auth_token": "123456789:BAEKbsOKAL23CXufXG4ksNV7Dq7e_1qi3j8"},
        )

    @patch("requests.post")
    @patch("requests.get")
    def test_claim(self, mock_get, mock_post):
        url = reverse("channels.types.telegram.claim")

        self.login(self.admin)

        # check that claim page URL appears on claim list page
        response = self.client.get(reverse("channels.channel_claim"))
        self.assertContains(response, url)

        # can fetch the claim page
        response = self.client.get(url)
        self.assertContains(response, "Connect Telegram")

        # claim with an invalid token
        mock_get.side_effect = [MockResponse(404, '{"ok": false, "error_code": 404,"description": "Not Found"}')]
        response = self.client.post(url, {"auth_token": "invalid"})
        self.assertEqual(200, response.status_code)
        self.assertEqual(
            "Your authentication token is invalid, please check and try again",
            response.context["form"].errors["auth_token"][0],
        )

        mock_get.side_effect = [
            MockResponse(
                200,
                '{"ok": true, "result": {"id": 123, "is_bot": true, "first_name": "Rapid", "last_name": "Bot", "username":"rapidbot"} }',
            ),
            MockResponse(
                200,
                '{"ok": true, "result": {"id": 123, "is_bot": true, "first_name": "Rapid", "last_name": "Bot", "username":"rapidbot"} }',
            ),
        ]
        mock_post.return_value = MockResponse(200, '{"ok": true, "result": "SUCCESS"}')

        response = self.client.post(url, {"auth_token": "184875172:BAEKbsOKAL23CXufXG4ksNV7Dq7e_1qi3j8"})
        channel = Channel.objects.get(address="rapidbot")
        self.assertEqual(channel.channel_type, "TG")
        self.assertEqual(
            channel.config,
            {
                "auth_token": "184875172:BAEKbsOKAL23CXufXG4ksNV7Dq7e_1qi3j8",
                "callback_domain": channel.callback_domain,
            },
        )

        self.assertRedirect(response, reverse("channels.channel_read", args=[channel.uuid]))
        self.assertEqual(302, response.status_code)

        response = self.client.post(url, {"auth_token": "184875172:BAEKbsOKAL23CXufXG4ksNV7Dq7e_1qi3j8"})
        self.assertEqual(
            "A telegram channel for this bot already exists on your account.",
            response.context["form"].errors["auth_token"][0],
        )

        # make sure we our telegram channel satisfies as a send channel
        send_channel = self.org.get_send_channel()
        self.assertIsNotNone(send_channel)
        self.assertEqual(send_channel.channel_type, "TG")

    @patch("requests.post")
    def test_release(self, mock_post):
        self.channel.release(self.admin)

        mock_post.assert_called_once_with(
            f"https://api.telegram.org/bot{self.channel.config['auth_token']}/deleteWebhook"
        )

    def test_get_error_ref_url(self):
        self.assertEqual("https://core.telegram.org/api/errors", TelegramType().get_error_ref_url(None, "420"))
