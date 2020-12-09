from unittest.mock import patch

from django.forms import ValidationError
from django.urls import reverse

from temba.tests import MockResponse, TembaTest

from ...models import Channel
from .type import TextItWhatsAppType


class TextItWhatsappTypeTest(TembaTest):
    def test_claim(self):
        Channel.objects.all().delete()

        url = reverse("channels.types.textit_whatsapp.claim")
        self.login(self.admin)

        # make sure textit is on the claim page (not yet)
        # response = self.client.get(reverse("channels.channel_claim"), follow=True)
        # self.assertContains(response, url)

        response = self.client.get(url)
        self.assertEqual(200, response.status_code)
        post_data = response.context["form"].initial

        post_data["api_endpoint"] = "http://test.whatsapp.textit.com/v1"
        post_data["access_token"] = "123456789"

        # first bad token
        with patch("requests.get") as mock_get:
            mock_get.side_effect = [MockResponse(400, '{ "error": "not authorized"}')]
            response = self.client.post(url, post_data)
            self.assertTrue(response.context["form"].errors)

        # channel that isn't active yet
        with patch("requests.get") as mock_get:
            mock_get.side_effect = [
                MockResponse(
                    200, '{ "name": "SubsRus", "address": "+12065551212", "country": "US", "status": "requested" }'
                )
            ]
            response = self.client.post(url, post_data)
            self.assertTrue(response.context["form"].errors)

        # try again with success
        with patch("requests.post") as mock_post, patch("requests.get") as mock_get:
            mock_get.side_effect = [
                MockResponse(
                    200, '{ "name": "SubsRus", "address": "+12065551212", "country": "US", "status": "activated" }'
                )
            ]
            mock_post.side_effect = [
                MockResponse(200, '{ "status": "ok" }'),
            ]
            response = self.client.post(url, post_data)
            self.assertEqual(302, response.status_code)

        channel = Channel.objects.get()

        self.assertEqual("123456789", channel.config[Channel.CONFIG_AUTH_TOKEN])
        self.assertEqual("http://test.whatsapp.textit.com/", channel.config[Channel.CONFIG_BASE_URL])

        self.assertEqual("+12065551212", channel.address)
        self.assertEqual("US", channel.country)
        self.assertEqual("TXW", channel.channel_type)
        self.assertEqual("SubsRus: (206) 555-1212", channel.name)
        self.assertEqual(10, channel.tps)
        self.assertTrue(channel.get_type().has_attachment_support(channel))

        # test activating the channel
        with patch("requests.post") as mock_post:
            url = "https://%s%s" % (
                channel.org.get_brand_domain(),
                reverse("courier.txw", args=[channel.uuid, "receive"]),
            )
            mock_post.side_effect = [MockResponse(200, '{ "url": "%s" }' % url)]

            TextItWhatsAppType().activate(channel)
            self.assertEqual(mock_post.call_args_list[0][1]["json"]["url"], url)

        with patch("requests.post") as mock_post:
            mock_post.side_effect = [MockResponse(400, '{ "meta": { "success": false } }')]

            try:
                TextItWhatsAppType().activate(channel)
                self.fail("Should have thrown error activating channel")
            except ValidationError:
                pass

        # deactivate our channel
        with self.settings(IS_PROD=True):
            with patch("requests.post") as mock_post:
                mock_post.side_effect = [MockResponse(200, '{"url": "" }')]
                channel.release()
                self.assertEqual(mock_post.call_args_list[0][1]["json"]["url"], "")
