from unittest.mock import patch

from django.forms import ValidationError
from django.urls import reverse

from temba.tests import MockResponse, TembaTest

from ...models import Channel
from .type import Dialog360Type


class Dialog360TypeTest(TembaTest):
    def test_claim(self):
        Channel.objects.all().delete()

        url = reverse("channels.types.dialog360.claim")
        self.login(self.admin)

        # make sure 360dialog is on the claim page
        response = self.client.get(reverse("channels.channel_claim"), follow=True)
        self.assertContains(response, url)

        response = self.client.get(url)
        self.assertEqual(200, response.status_code)
        post_data = response.context["form"].initial

        post_data["number"] = "1234"
        post_data["country"] = "RW"
        post_data["base_url"] = "https://ilhasoft.com.br/whatsapp"
        post_data["api_key"] = "123456789"

        # will fail with invalid phone number
        response = self.client.post(url, post_data)
        self.assertFormError(response, "form", None, ["Please enter a valid phone number"])

        # valid number
        post_data["number"] = "0788123123"

        # then success
        with patch("requests.post") as mock_post:
            mock_post.side_effect = [MockResponse(200, '{ "url": "https://ilhasoft.com.br/whatsapp" }')]

            response = self.client.post(url, post_data)
            self.assertEqual(302, response.status_code)

        channel = Channel.objects.get()

        self.assertEqual("123456789", channel.config[Channel.CONFIG_AUTH_TOKEN])
        self.assertEqual("https://ilhasoft.com.br/whatsapp", channel.config[Channel.CONFIG_BASE_URL])

        self.assertEqual("+250788123123", channel.address)
        self.assertEqual("RW", channel.country)
        self.assertEqual("D3", channel.channel_type)
        self.assertEqual(45, channel.tps)
        self.assertTrue(channel.get_type().has_attachment_support(channel))

        # test activating the channel
        with patch("requests.post") as mock_post:
            mock_post.side_effect = [MockResponse(200, '{ "url": "https://ilhasoft.com.br/whatsapp" }')]

            Dialog360Type().activate(channel)
            self.assertEqual(
                mock_post.call_args_list[0][1]["json"]["url"],
                "https://%s%s"
                % (channel.org.get_brand_domain(), reverse("courier.d3", args=[channel.uuid, "receive"])),
            )

        with patch("requests.post") as mock_post:
            mock_post.side_effect = [MockResponse(400, '{ "meta": { "success": false } }')]

            try:
                Dialog360Type().activate(channel)
                self.fail("Should have thrown error activating channel")
            except ValidationError:
                pass

        # deactivate our channel
        with self.settings(IS_PROD=True):
            channel.release()
