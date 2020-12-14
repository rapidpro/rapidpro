from unittest.mock import patch

from django.test import override_settings
from django.urls import reverse

from temba.tests import MockResponse, TembaTest
from temba.triggers.models import Trigger
from temba.utils import json

from ...models import Channel


class FacebookTypeTest(TembaTest):
    def setUp(self):
        super().setUp()

        self.channel = Channel.create(
            self.org,
            self.user,
            None,
            "FB",
            name="Facebook",
            address="12345",
            role="SR",
            schemes=["facebook"],
            config={"auth_token": "09876543"},
        )

    def test_claim(self):
        url = reverse("channels.types.facebook.claim")

        self.login(self.admin)

        # Switched to FBA
        # # check that claim page URL appears on claim list page
        # response = self.client.get(reverse("channels.channel_claim"))
        # self.assertContains(response, url)

        # can fetch the claim page
        response = self.client.get(url)
        self.assertContains(response, "Connect Facebook")

        token = "x" * 200

        post_data = response.context["form"].initial
        post_data["page_access_token"] = token
        post_data["page_id"] = "123456"
        post_data["page_name"] = "Temba"

        response = self.client.post(url, post_data, follow=True)

        # assert our channel got created
        channel = Channel.objects.get(address="123456")
        self.assertEqual(channel.config[Channel.CONFIG_AUTH_TOKEN], token)
        self.assertEqual(channel.config[Channel.CONFIG_PAGE_NAME], "Temba")
        self.assertEqual(channel.address, "123456")

        # should be on our configuration page displaying our secret
        self.assertContains(response, channel.config[Channel.CONFIG_SECRET])

    @override_settings(IS_PROD=True)
    @patch("requests.delete")
    def test_release(self, mock_delete):
        mock_delete.return_value = MockResponse(200, json.dumps({"success": True}))
        self.channel.release()

        mock_delete.assert_called_once_with(
            "https://graph.facebook.com/v3.3/me/subscribed_apps", params={"access_token": "09876543"}
        )

    @override_settings(IS_PROD=True)
    def test_new_conversation_triggers(self):
        flow = self.create_flow()

        with patch("requests.post") as mock_post:
            mock_post.return_value = MockResponse(200, json.dumps({"success": True}))

            trigger = Trigger.create(self.org, self.admin, Trigger.TYPE_NEW_CONVERSATION, flow, self.channel)

            mock_post.assert_called_once_with(
                "https://graph.facebook.com/v3.3/12345/thread_settings",
                json={
                    "setting_type": "call_to_actions",
                    "thread_state": "new_thread",
                    "call_to_actions": [{"payload": "get_started"}],
                },
                headers={"Content-Type": "application/json"},
                params={"access_token": "09876543"},
            )
            mock_post.reset_mock()

            trigger.archive(self.admin)

            mock_post.assert_called_once_with(
                "https://graph.facebook.com/v3.3/12345/thread_settings",
                json={"setting_type": "call_to_actions", "thread_state": "new_thread", "call_to_actions": []},
                headers={"Content-Type": "application/json"},
                params={"access_token": "09876543"},
            )
            mock_post.reset_mock()

            trigger.restore(self.admin)

            mock_post.assert_called_once_with(
                "https://graph.facebook.com/v3.3/12345/thread_settings",
                json={
                    "setting_type": "call_to_actions",
                    "thread_state": "new_thread",
                    "call_to_actions": [{"payload": "get_started"}],
                },
                headers={"Content-Type": "application/json"},
                params={"access_token": "09876543"},
            )
            mock_post.reset_mock()
