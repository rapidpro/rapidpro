from unittest.mock import patch

from django.test import override_settings
from django.urls import reverse

from temba.tests import MockResponse, TembaTest
from temba.utils import json

from ...models import Channel


class FacebookTypeTest(TembaTest):
    def setUp(self):
        super().setUp()

        self.channel = Channel.create(
            self.org,
            self.user,
            None,
            "FBA",
            name="Facebook",
            address="12345",
            role="SR",
            schemes=["facebook"],
            config={"auth_token": "09876543"},
        )

    @override_settings(FACEBOOK_APPLICATION_ID="FB_APP_ID", FACEBOOK_APPLICATION_SECRET="FB_APP_SECRET")
    @patch("requests.get")
    def test_claim(self, mock_get):
        token = "x" * 200
        mock_get.side_effect = [
            MockResponse(200, json.dumps({"access_token": f"long-life-user-{token}"})),
            MockResponse(
                200,
                json.dumps({"data": [{"name": "Temba", "id": "123456", "access_token": f"page-long-life-{token}"}]}),
            ),
        ]

        url = reverse("channels.types.facebookapp.claim")

        self.login(self.admin)

        # Still hidden
        # # check that claim page URL appears on claim list page
        # response = self.client.get(reverse("channels.channel_claim"))
        # self.assertContains(response, url)

        # can fetch the claim page
        response = self.client.get(url)
        self.assertContains(response, "Connect Facebook")
        self.assertEqual(response.context["facebook_app_id"], "FB_APP_ID")
        self.assertEqual(response.context["claim_url"], url)

        post_data = response.context["form"].initial
        post_data["fb_user_id"] = "098765"
        post_data["user_access_token"] = token
        post_data["page_id"] = "123456"
        post_data["page_name"] = "Temba"

        response = self.client.post(url, post_data, follow=True)

        # assert our channel got created
        channel = Channel.objects.get(address="123456", channel_type="FBA")
        self.assertEqual(channel.config[Channel.CONFIG_AUTH_TOKEN], f"page-long-life-{token}")
        self.assertEqual(channel.config[Channel.CONFIG_PAGE_NAME], "Temba")
        self.assertEqual(channel.address, "123456")

        self.assertEqual(response.request["PATH_INFO"], reverse("channels.channel_read", args=[channel.uuid]))

        mock_get.assert_any_call(
            "https://graph.facebook.com/oauth/access_token",
            params={
                "grant_type": "fb_exchange_token",
                "client_id": "FB_APP_ID",
                "client_secret": "FB_APP_SECRET",
                "fb_exchange_token": token,
            },
        )
        mock_get.assert_any_call(
            "https://graph.facebook.com/v7.0/098765/accounts", params={"access_token": f"long-life-user-{token}"}
        )

    @override_settings(IS_PROD=True)
    @patch("requests.delete")
    def test_release(self, mock_delete):
        mock_delete.return_value = MockResponse(200, json.dumps({"success": True}))
        self.channel.release()

        mock_delete.assert_called_once_with(
            "https://graph.facebook.com/v7.0/12345/subscribed_apps", params={"access_token": "09876543"}
        )
