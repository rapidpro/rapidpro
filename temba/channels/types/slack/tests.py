import slack_sdk
from unittest.mock import patch
from temba.tests import TembaTest
from ...models import Channel
from django.urls import reverse


class SlackTypeTest(TembaTest):
    def setUp(self):
        super().setUp()

        self.channel = self.create_channel(
            "SL",
            "Slack",
            "12345",
            config={
                "user_token": "123456789:ABCDEFabcdef-1a2b3c4d",
                "bot_token": "123456789:ABCDEFabcdef-1a2b3c4d",
                "verification_token": "123456789:ABCDEFabcdef-1a2b3c4d",
            },
        )

    @patch("slack_sdk.WebClient.api_call")
    def test_claim(self, mock_api_call):
        url = reverse("channels.types.slack.claim")

        self.login(self.admin)

        # check that claim page URL appears on claim list page
        response = self.client.get(reverse("channels.channel_claim"))
        self.assertContains(response, url)

        # can fetch the claim page
        response = self.client.get(url)
        self.assertContains(response, "Connect Slack")

        # claim with invalid token
        mock_api_call.side_effect = slack_sdk.errors.SlackApiError("", "")
        response = self.client.post(url, {"user_token": "invalid"})
        self.assertEqual(200, response.status_code)
        self.assertEqual(
            "Your user token is invalid, please check and try again",
            response.context["form"].errors["user_token"][0],
        )

        mock_api_call.side_effect = slack_sdk.errors.SlackApiError("", "")
        response = self.client.post(url, {"bot_token": "invalid"})
        self.assertEqual(200, response.status_code)
        self.assertEqual(
            "Your bot user token is invalid, please check and try again",
            response.context["form"].errors["bot_token"][0],
        ),

        auth_test = {
            "ok": True,
            "url": "https://dummy-team-group.slack.com/",
            "team": "dummy-team",
            "user": "dummy",
            "team_id": "T0DUMMY",
            "user_id": "U0DUMMY",
            "is_enterprise_install": False,
        }

        mock_api_call.side_effect = None
        mock_api_call.return_value = auth_test

        response = self.client.post(
            url,
            {
                "user_token": "UTK0123456789ABCDEFabcdef-1a2b3c4d",
                "bot_token": "BTK0123456789ABCDEFabcdef-1a2b3c4d",
                "verification_token": "VTK0123456789ABCDEFabcdef-1a2b3c4d",
            },
        )
        channel = Channel.objects.get(address="dummy")
        self.assertEqual(channel.channel_type, "SL")
        self.assertEqual(
            channel.config,
            {
                "user_token": "UTK0123456789ABCDEFabcdef-1a2b3c4d",
                "bot_token": "BTK0123456789ABCDEFabcdef-1a2b3c4d",
                "verification_token": "VTK0123456789ABCDEFabcdef-1a2b3c4d",
            },
        ),

        config_url = reverse("channels.channel_configuration", args=[channel.uuid])
        self.assertRedirect(response, config_url)

        response = self.client.get(config_url)
        self.assertEqual(response.status_code, 200)

        response = self.client.post(
            url,
            {
                "user_token": "UTK0123456789ABCDEFabcdef-1a2b3c4d",
                "bot_token": "BTK0123456789ABCDEFabcdef-1a2b3c4d",
                "verification_token": "VTK0123456789ABCDEFabcdef-1a2b3c4d",
            },
        )
        self.assertEqual(
            "A slack channel for this bot already exists on your account.",
            response.context["form"].errors["user_token"][0],
        )

        contact = self.create_contact("Slack User", urns=["slack:1234"])

        # make sure we our slack channel satisfies as a send channel
        response = self.client.get(reverse("contacts.contact_read", args=[contact.uuid]))
        send_channel = response.context["send_channel"]
        self.assertIsNotNone(send_channel)
        self.assertEqual(send_channel.channel_type, "SL")
