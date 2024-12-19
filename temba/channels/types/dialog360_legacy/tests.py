import json
from unittest.mock import patch

from requests import RequestException

from django.forms import ValidationError
from django.urls import reverse

from temba.request_logs.models import HTTPLog
from temba.tests import CRUDLTestMixin, MockResponse, TembaTest

from ...models import Channel
from .type import Dialog360LegacyType


class Dialog360LegacyTypeTest(CRUDLTestMixin, TembaTest):
    @patch("temba.channels.types.dialog360_legacy.Dialog360LegacyType.check_health")
    def test_claim(self, mock_health):
        mock_health.return_value = MockResponse(200, '{"meta": {"api_status": "stable", "version": "2.35.4"}}')
        Channel.objects.all().delete()

        url = reverse("channels.types.dialog360_legacy.claim")
        self.login(self.admin)

        # make sure  360dialog is NOT on the claim page
        response = self.client.get(reverse("channels.channel_claim"), follow=True)
        self.assertNotContains(response, url)

        response = self.client.get(url)
        self.assertEqual(200, response.status_code)
        post_data = response.context["form"].initial

        post_data["number"] = "1234"
        post_data["country"] = "RW"
        post_data["api_key"] = "123456789"

        # will fail with invalid phone number
        response = self.client.post(url, post_data)
        self.assertFormError(response.context["form"], None, ["Please enter a valid phone number"])

        # valid number
        post_data["number"] = "0788123123"

        # then success
        with patch("socket.gethostbyname", return_value="123.123.123.123"), patch("requests.post") as mock_post:
            mock_post.side_effect = [MockResponse(200, '{ "url": "https://waba.360dialog.io" }')]

            response = self.client.post(url, post_data)
            self.assertEqual(302, response.status_code)

        channel = Channel.objects.get()

        self.assertEqual("123456789", channel.config[Channel.CONFIG_AUTH_TOKEN])
        self.assertEqual("https://waba.360dialog.io", channel.config[Channel.CONFIG_BASE_URL])

        self.assertEqual("+250788123123", channel.address)
        self.assertEqual("RW", channel.country)
        self.assertEqual("D3", channel.channel_type)
        self.assertEqual(45, channel.tps)

        # test activating the channel
        with patch("requests.post") as mock_post:
            mock_post.side_effect = [MockResponse(200, '{ "url": "https://waba.360dialog.io" }')]

            Dialog360LegacyType().activate(channel)
            self.assertEqual(
                mock_post.call_args_list[0][1]["json"]["url"],
                "https://%s%s"
                % (channel.org.get_brand_domain(), reverse("courier.d3", args=[channel.uuid, "receive"])),
            )

        with patch("requests.post") as mock_post:
            mock_post.side_effect = [MockResponse(400, '{ "meta": { "success": false } }')]

            try:
                Dialog360LegacyType().activate(channel)
                self.fail("Should have thrown error activating channel")
            except ValidationError:
                pass

        # deactivate our channel
        channel.release(self.admin)

    @patch("requests.get")
    def test_fetch_templates(self, mock_get):
        channel = self.create_channel(
            "D3",
            "360Dialog channel",
            address="1234",
            country="BR",
            config={
                Channel.CONFIG_BASE_URL: "https://example.com/whatsapp",
                Channel.CONFIG_AUTH_TOKEN: "123456789",
            },
        )

        mock_get.side_effect = [
            RequestException("Network is unreachable", response=MockResponse(100, "")),
            MockResponse(400, '{ "meta": { "success": false } }', headers={"D360-API-KEY": "123456789"}),
            MockResponse(200, '{"waba_templates": ["foo", "bar"]}', headers={"D360-API-KEY": "123456789"}),
        ]

        with self.assertRaises(RequestException):
            channel.type.fetch_templates(channel)

        self.assertEqual(1, HTTPLog.objects.filter(log_type=HTTPLog.WHATSAPP_TEMPLATES_SYNCED, is_error=True).count())

        with self.assertRaises(RequestException):
            channel.type.fetch_templates(channel)

        self.assertEqual(2, HTTPLog.objects.filter(log_type=HTTPLog.WHATSAPP_TEMPLATES_SYNCED, is_error=True).count())

        templates = channel.type.fetch_templates(channel)
        self.assertEqual(["foo", "bar"], templates)

        self.assertEqual(2, HTTPLog.objects.filter(log_type=HTTPLog.WHATSAPP_TEMPLATES_SYNCED, is_error=True).count())
        self.assertEqual(1, HTTPLog.objects.filter(log_type=HTTPLog.WHATSAPP_TEMPLATES_SYNCED, is_error=False).count())

        # check auth token is redacted in HTTP logs
        for log in HTTPLog.objects.all():
            self.assertNotIn("123456789", json.dumps(log.get_display()))

        mock_get.assert_called_with(
            "https://example.com/whatsapp/v1/configs/templates",
            headers={
                "D360-API-KEY": channel.config[Channel.CONFIG_AUTH_TOKEN],
                "Content-Type": "application/json",
            },
        )

    def test_check_health(self):
        channel = self.create_channel(
            "D3",
            "360Dialog channel",
            address="1234",
            country="BR",
            config={
                Channel.CONFIG_BASE_URL: "https://example.com/whatsapp",
                Channel.CONFIG_AUTH_TOKEN: "123456789",
            },
        )
        with patch("requests.get") as mock_get:
            mock_get.side_effect = [
                MockResponse(200, '{"meta": {"api_status": "stable", "version": "2.35.4"}}'),
                MockResponse(401, ""),
            ]
            channel.type.check_health(channel)
            mock_get.assert_called_with(
                "https://example.com/whatsapp/v1/health",
                headers={"D360-API-KEY": "123456789", "Content-Type": "application/json"},
            )

            with patch("logging.Logger.debug") as mock_log_debug:
                channel.type.check_health(channel)
                self.assertEqual(1, mock_log_debug.call_count)
                self.assertEqual(
                    "Error checking API health: b''",
                    mock_log_debug.call_args[0][0],
                )
