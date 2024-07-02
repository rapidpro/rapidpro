from unittest.mock import patch

from django.forms import ValidationError
from django.urls import reverse

from temba.tests import MockResponse, TembaTest

from ...models import Channel


class JustCallTypeTest(TembaTest):
    def test_claim(self):
        Channel.objects.all().delete()

        self.login(self.admin)

        url = reverse("channels.types.justcall.claim")

        # should see the general channel claim page
        response = self.client.get(reverse("channels.channel_claim"))
        self.assertContains(response, url)

        # try to claim a channel
        response = self.client.get(url)
        post_data = response.context["form"].initial

        post_data["number"] = "3071"
        post_data["country"] = "RW"
        post_data["api_key"] = "justcall_key"
        post_data["api_secret"] = "justcall_secret"

        # try once with an error
        with patch("requests.post") as mock_post:
            mock_post.return_value = MockResponse(400, '{ "message": "Failed try again later." }')
            with self.assertRaisesRegex(ValidationError, "Unable to add webhook to JustCall: Failed try again later."):
                response = self.client.post(url, post_data)
            self.assertFalse(Channel.objects.all())

        with patch("requests.post") as mock_post:
            mock_post.side_effect = [
                MockResponse(200, '{""}'),
                MockResponse(400, '{ "message": "Failed try again later." }'),
            ]
            with self.assertRaisesRegex(ValidationError, "Unable to add webhook to JustCall: Failed try again later."):
                response = self.client.post(url, post_data)
            self.assertFalse(Channel.objects.all())

        # success this time
        with patch("requests.post") as mock_post:
            mock_post.return_value = MockResponse(200, '{""}')
            response = self.client.post(url, post_data)
            self.assertEqual(302, response.status_code)

        channel = Channel.objects.get()

        self.assertEqual("RW", channel.country)
        self.assertTrue(channel.uuid)
        self.assertEqual(post_data["number"], channel.address)
        self.assertEqual("JCL", channel.channel_type)
        self.assertEqual(channel.config[Channel.CONFIG_API_KEY], post_data["api_key"])
        self.assertEqual(channel.config[Channel.CONFIG_SECRET], post_data["api_secret"])
