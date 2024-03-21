from unittest.mock import patch

import slack_sdk

from django.urls import reverse

from temba.tests import TembaTest

from ...models import Channel


class SlackTypeTest(TembaTest):
    def setUp(self):
        super().setUp()

        self.create_channel(
            "SL",
            "Slack12345",
            "B0T12345",
            org=self.org2,
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
        self.assertFormError(
            response.context["form"], "user_token", "Your user token is invalid, please check and try again"
        )

        mock_api_call.side_effect = slack_sdk.errors.SlackApiError("", "")
        response = self.client.post(url, {"bot_token": "invalid"})
        self.assertEqual(200, response.status_code)
        self.assertFormError(
            response.context["form"], None, "Your bot user token is invalid, please check and try again"
        )

        # test to claim a channel with a bot token that already exists for another workspace.
        auth_test = {
            "ok": True,
            "url": "https://12345-team-group.slack.com/",
            "team": "12345-team",
            "user": "dummy",
            "team_id": "T012345",
            "user_id": "U012345",
            "bot_id": "B0T12345",
            "is_enterprise_install": False,
        }

        mock_api_call.side_effect = None
        mock_api_call.return_value = auth_test

        response = self.client.post(
            url,
            {
                "user_token": "123456789:ABCDEFabcdef-1a2b3c4d",
                "bot_token": "123456789:ABCDEFabcdef-1a2b3c4d",
                "verification_token": "123456789:ABCDEFabcdef-1a2b3c4d",
            },
        )
        self.assertFormError(response.context["form"], None, "This channel is already connected in another workspace.")

        # test claim a channel with success
        auth_test = {
            "ok": True,
            "url": "https://dummy-team-group.slack.com/",
            "team": "dummy-team",
            "user": "dummy",
            "team_id": "T0DUMMY",
            "user_id": "U0DUMMY",
            "bot_id": "B0TDUMMY",
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
        channel = Channel.objects.get(address="B0TDUMMY")
        self.assertEqual(channel.channel_type, "SL")
        self.assertEqual(
            channel.config,
            {
                "user_token": "UTK0123456789ABCDEFabcdef-1a2b3c4d",
                "bot_token": "BTK0123456789ABCDEFabcdef-1a2b3c4d",
                "verification_token": "VTK0123456789ABCDEFabcdef-1a2b3c4d",
            },
        ),

        # test access config page
        config_url = reverse("channels.channel_configuration", args=[channel.uuid])
        self.assertRedirect(response, config_url)

        response = self.client.get(config_url)
        self.assertEqual(response.status_code, 200)

        # test to claim a channel with a bot token that already exists in this workspace
        response = self.client.post(
            url,
            {
                "user_token": "UTK0123456789ABCDEFabcdef-1a2b3c4d",
                "bot_token": "BTK0123456789ABCDEFabcdef-1a2b3c4d",
                "verification_token": "VTK0123456789ABCDEFabcdef-1a2b3c4d",
            },
        )
        self.assertFormError(response.context["form"], None, "This channel is already connected in this workspace.")

        # make sure we our slack channel satisfies as a send channel
        send_channel = self.org.get_send_channel()
        self.assertIsNotNone(send_channel)
        self.assertEqual(send_channel.channel_type, "SL")
