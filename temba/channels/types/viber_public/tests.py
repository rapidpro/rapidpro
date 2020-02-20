from unittest.mock import patch

from django.test import override_settings
from django.urls import reverse

from temba.tests import MockResponse, TembaTest
from temba.utils import json

from ...models import Channel
from .views import CONFIG_WELCOME_MESSAGE


class ViberPublicTypeTest(TembaTest):
    def setUp(self):
        super().setUp()

        self.channel = Channel.create(
            self.org,
            self.user,
            None,
            "VP",
            name="Viber",
            address="12345",
            role="SR",
            schemes=["viber"],
            config={"auth_token": "abcd1234"},
        )

    @override_settings(IS_PROD=True)
    @patch("requests.post")
    def test_claim(self, mock_post):
        url = reverse("channels.types.viber_public.claim")

        self.login(self.admin)

        # check that claim page URL appears on claim list page
        response = self.client.get(reverse("channels.channel_claim"))
        self.assertContains(response, url)

        # try submitting with invalid token
        mock_post.return_value = MockResponse(400, json.dumps({"status": 3, "status_message": "Invalid token"}))
        response = self.client.post(url, {"auth_token": "invalid"})

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Error validating authentication token")

        # ok this time claim with a success
        mock_post.side_effect = [
            MockResponse(200, json.dumps({"status": 0, "status_message": "ok", "id": "viberId", "uri": "viberName"})),
            MockResponse(200, json.dumps({"status": 0, "status_message": "ok", "id": "viberId", "uri": "viberName"})),
            MockResponse(200, json.dumps({"status": 0, "status_message": "ok"})),
        ]

        self.client.post(url, {"auth_token": "123456"}, follow=True)

        # assert our channel got created
        channel = Channel.objects.get(address="viberId")
        self.assertEqual(channel.config["auth_token"], "123456")
        self.assertEqual(channel.name, "viberName")
        self.assertTrue(channel.get_type().has_attachment_support(channel))

        # should have been called with our webhook URL
        self.assertEqual(mock_post.call_args[0][0], "https://chatapi.viber.com/pa/set_webhook")

    @override_settings(IS_PROD=True)
    @patch("requests.post")
    def test_release(self, mock_post):
        mock_post.side_effect = [MockResponse(200, json.dumps({"status": 0, "status_message": "ok"}))]
        self.channel.release()

        self.assertEqual(mock_post.call_args[0][0], "https://chatapi.viber.com/pa/set_webhook")

    def test_update(self):
        update_url = reverse("channels.channel_update", args=[self.channel.id])
        response = self.client.get(update_url)
        self.assertLoginRedirect(response)

        self.login(self.admin)
        response = self.client.get(reverse("channels.channel_read", args=[self.channel.uuid]))

        self.assertContains(response, update_url)

        response = self.client.get(update_url)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["form"].fields["welcome_message"].initial, "")

        postdata = response.context["view"].derive_initial()
        postdata["welcome_message"] = "Welcome, please subscribe for more"

        response = self.client.post(update_url, postdata, follow=True)
        self.assertEqual(response.status_code, 200)

        self.channel.refresh_from_db()
        self.assertEqual(self.channel.config.get(CONFIG_WELCOME_MESSAGE, ""), "Welcome, please subscribe for more")

        response = self.client.get(update_url)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.context["form"].fields["welcome_message"].initial, "Welcome, please subscribe for more"
        )
