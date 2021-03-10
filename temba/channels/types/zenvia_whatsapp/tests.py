
from unittest.mock import patch

from django.urls import reverse

from django.forms import ValidationError

from temba.tests import MockResponse, TembaTest

from ...models import Channel
from .type import ZenviaWhatsAppType, ZENVIA_MESSAGE_SUBSCRIPTION_ID, ZENVIA_STATUS_SUBSCRIPTION_ID

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
            mock_post.side_effect = [MockResponse(200, '{"id": "message_123"}'), MockResponse(200, '{"id": "status_123"}')]
            ZenviaWhatsAppType().activate(channel)

            self.assertEqual("message_123", channel.config.get(ZENVIA_MESSAGE_SUBSCRIPTION_ID))
            self.assertEqual("status_123", channel.config.get(ZENVIA_STATUS_SUBSCRIPTION_ID))
