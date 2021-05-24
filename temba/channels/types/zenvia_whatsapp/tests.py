from unittest.mock import patch

from django.forms import ValidationError
from django.urls import reverse

from temba.tests import MockResponse, TembaTest

from ...models import Channel
from .type import ZENVIA_MESSAGE_SUBSCRIPTION_ID, ZENVIA_STATUS_SUBSCRIPTION_ID, ZenviaWhatsAppType


class ZenviaWhatsAppTypeTest(TembaTest):
    def test_claim(self):
        Channel.objects.all().delete()

        self.login(self.admin)

        url = reverse("channels.types.zenvia_whatsapp.claim")

        # should see the general channel claim page
        response = self.client.get(reverse("channels.channel_claim"))
        self.assertContains(response, url)

        # try to claim a channel
        response = self.client.get(url)
        post_data = response.context["form"].initial

        post_data["token"] = "12345"
        post_data["country"] = "US"
        post_data["number"] = "(206) 555-1212"

        response = self.client.post(url, post_data)

        channel = Channel.objects.get()

        self.assertEqual("US", channel.country)
        self.assertTrue(channel.uuid)
        self.assertEqual("+12065551212", channel.address)
        self.assertEqual("12345", channel.config["api_key"])
        self.assertEqual("ZVW", channel.channel_type)
        self.assertEqual("Zenvia WhatsApp: +12065551212", channel.name)

        with patch("requests.post") as mock_patch:
            mock_patch.side_effect = [MockResponse(400, '{ "error": true }')]

            try:
                ZenviaWhatsAppType().activate(channel)
                self.fail("Should have thrown error activating channel")
            except ValidationError:
                pass

            self.assertFalse(channel.config.get(ZENVIA_MESSAGE_SUBSCRIPTION_ID))
            self.assertFalse(channel.config.get(ZENVIA_STATUS_SUBSCRIPTION_ID))

        with patch("requests.post") as mock_post:
            mock_post.side_effect = [
                MockResponse(200, '{"id": "message_123"}'),
                MockResponse(400, '{"error": "failed"}'),
            ]
            try:
                ZenviaWhatsAppType().activate(channel)
            except ValidationError:
                pass

            self.assertEqual("12345", mock_post.call_args_list[0][1]["headers"]["X-API-TOKEN"])

            self.assertEqual("message_123", channel.config.get(ZENVIA_MESSAGE_SUBSCRIPTION_ID))
            self.assertIsNone(channel.config.get(ZENVIA_STATUS_SUBSCRIPTION_ID))

        with patch("requests.delete") as mock_delete:
            mock_delete.return_value = MockResponse(204, "")

            # deactivate our channel
            channel.release(self.admin)

            self.assertEqual(1, mock_delete.call_count)
            self.assertEqual(
                "https://api.zenvia.com/v2/subscriptions/message_123", mock_delete.call_args_list[0][0][0]
            )
            self.assertEqual("12345", mock_delete.call_args_list[0][1]["headers"]["X-API-TOKEN"])

        # try to claim a channel
        response = self.client.get(url)
        post_data = response.context["form"].initial

        post_data["token"] = "12345"
        post_data["country"] = "US"
        post_data["number"] = "(206) 555-1212"

        response = self.client.post(url, post_data)

        channel = Channel.objects.filter(is_active=True).first()

        with patch("requests.post") as mock_post:
            mock_post.side_effect = [
                MockResponse(200, '{"id": "message_123"}'),
                MockResponse(200, '{"id": "status_123"}'),
            ]
            ZenviaWhatsAppType().activate(channel)

            self.assertEqual("12345", mock_post.call_args_list[0][1]["headers"]["X-API-TOKEN"])

            self.assertEqual("message_123", channel.config.get(ZENVIA_MESSAGE_SUBSCRIPTION_ID))
            self.assertEqual("status_123", channel.config.get(ZENVIA_STATUS_SUBSCRIPTION_ID))

        with patch("requests.delete") as mock_delete:
            mock_delete.return_value = MockResponse(400, "Error")

            # deactivate our channel
            channel.release(self.admin)

            self.assertEqual(2, mock_delete.call_count)
            self.assertEqual(
                "https://api.zenvia.com/v2/subscriptions/message_123", mock_delete.call_args_list[0][0][0]
            )
            self.assertEqual("12345", mock_delete.call_args_list[0][1]["headers"]["X-API-TOKEN"])
            self.assertEqual("https://api.zenvia.com/v2/subscriptions/status_123", mock_delete.call_args_list[1][0][0])
            self.assertEqual("12345", mock_delete.call_args_list[1][1]["headers"]["X-API-TOKEN"])
