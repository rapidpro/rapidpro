from unittest.mock import patch

from django.forms import ValidationError
from django.test.utils import override_settings
from django.urls import reverse

from temba.tests import TembaTest
from temba.tests.base import override_brand
from temba.tests.requests import MockResponse

from ...models import Channel
from .type import MtnType


class MtnTypeTest(TembaTest):
    @patch("requests.delete")
    @patch("requests.post")
    def test_claim(self, mock_post, mock_delete):
        with override_brand(domain="temba.io"):
            mock_post.side_effect = [
                MockResponse(200, '{"access_token": "token-123"}'),
                MockResponse(201, '{"data": {"id": "sub-123"} }'),
                MockResponse(200, '{"access_token": "token-123"}'),
            ]

            mock_delete.return_value = MockResponse(200, '{"statusCode": "0000"}')

            Channel.objects.all().delete()

            self.login(self.admin)
            url = reverse("channels.types.mtn.claim")

            response = self.client.get(reverse("channels.channel_claim"))
            self.assertNotContains(response, url)

            self.make_beta(self.admin)

            # should see the general channel claim page
            response = self.client.get(reverse("channels.channel_claim"))
            self.assertContains(response, url)

            # try to claim a channel
            response = self.client.get(url)
            post_data = response.context["form"].initial

            post_data["number"] = "3071"
            post_data["country"] = "RW"
            post_data["consumer_key"] = "foofoofoofoofoofoofoofoofoofoofoofoofoofoofoofoofoofoofoofoo"
            post_data["consumer_secret"] = "barbarbarbarbarbarbarbarbarbarbarbarbarbarbarbarbarbarbarbar"
            post_data["cp_address"] = "FOO"

            response = self.client.post(url, post_data)

            channel = Channel.objects.get()

            self.assertEqual("RW", channel.country)
            self.assertTrue(channel.uuid)
            self.assertEqual(post_data["number"], channel.address)
            self.assertEqual("MTN", channel.channel_type)
            self.assertEqual(post_data["consumer_key"], channel.config[Channel.CONFIG_API_KEY])
            self.assertEqual(post_data["consumer_secret"], channel.config[Channel.CONFIG_AUTH_TOKEN])
            self.assertEqual(post_data["cp_address"], channel.config[MtnType.CP_ADDRESS])
            self.assertEqual("sub-123", channel.config["mtn_subscription_id"])

            self.assertEqual(mock_post.call_count, 2)
            self.assertEqual(
                mock_post.call_args_list[0][0][0],
                "https://api.mtn.com/v1/oauth/access_token?grant_type=client_credentials",
            )
            self.assertEqual(
                mock_post.call_args_list[0][1],
                {
                    "data": {
                        "client_id": "foofoofoofoofoofoofoofoofoofoofoofoofoofoofoofoofoofoofoofoo",
                        "client_secret": "barbarbarbarbarbarbarbarbarbarbarbarbarbarbarbarbarbarbarbar",
                    },
                    "headers": {"Content-Type": "application/x-www-form-urlencoded", "Accept": "application/json"},
                },
            )

            self.assertEqual(
                mock_post.call_args_list[1][0][0], "https://api.mtn.com/v2/messages/sms/outbound/3071/subscription"
            )
            self.assertEqual(
                mock_post.call_args_list[1][1],
                {
                    "json": {
                        "notifyUrl": f"https://temba.io/c/mtn/{channel.uuid}/receive",
                        "targetSystem": "temba.io",
                    },
                    "headers": {"Content-Type": "application/json", "Authorization": "Bearer token-123"},
                },
            )

            with override_settings(ORG_LIMIT_DEFAULTS={"channels": 1}):
                response = self.client.post(url, post_data)
                self.assertFormError(
                    response.context["form"],
                    None,
                    "This workspace has reached its limit of 1 channels. You must delete existing ones before you can create new ones.",
                )

            # release channel and deactivate the subscription
            channel.release(self.admin)
            self.assertEqual(mock_delete.call_count, 1)

            self.assertEqual(
                mock_delete.call_args_list[0][0][0],
                "https://api.mtn.com/v2/messages/sms/outbound/3071/subscription/sub-123",
            )

            with patch("requests.post") as mock_post:
                mock_post.return_value = MockResponse(400, '{"error": "Error"}')

                try:
                    MtnType().get_token(channel)
                    self.fail("Should have thrown error getting token for channel channel")
                except ValidationError:
                    pass

            with patch("requests.post") as mock_post:
                mock_post.side_effect = [
                    MockResponse(200, '{"access_token": "token-123"}'),
                    MockResponse(400, '{"error": "Error"}'),
                ]

                try:
                    MtnType().activate(channel)
                    self.fail("Should have thrown error activating channel")
                except ValidationError:
                    pass

            with patch("requests.post") as mock_post, patch("requests.delete") as mock_delete:
                mock_post.return_value = MockResponse(200, '{"access_token": "token-123"}')
                mock_delete.return_value = MockResponse(400, '{"error": "Error"}')

                try:
                    MtnType().deactivate(channel)
                    self.fail("Should have thrown error deactivating channel")
                except ValidationError:
                    pass
