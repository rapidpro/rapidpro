from unittest.mock import patch
from temba.tests import TembaTest
from ...models import Channel
from django.urls import reverse
from temba.tests import MockResponse, TembaTest
from temba.utils import json
from .type import TeamsType
from temba.request_logs.models import HTTPLog
from .tasks import refresh_teams_tokens

class TeamsTypeTest(TembaTest):
    def setUp(self):
        super().setUp()

        self.channel = self.create_channel(
            "TM",
            "Teams",
            "12345",
            config={
                "auth_token": "123456789:ABCDEFabcdef-1a2b3c4d",
                "bot_name": "bot_test",
                "app_password": "147852",
                "tenantID": "98741",
                "appID": "123456789",
                "botID": "12345",
            },
        )

    @patch("requests.post")
    def test_claim(self,mock_post):        
        url = reverse("channels.types.teams.claim")
        mock_post.return_value = MockResponse(200, json.dumps({"token_type": "Bearer","expires_in": 86399,"ext_expires_in": 86399,"access_token": "0123456789:ABCDEFabcdef-1a2b3c4d5e"}))
        self.login(self.admin)

        # check that claim page URL appears on claim list page
        response = self.client.get(reverse("channels.channel_claim"))
        self.assertContains(response, url)

        # can fetch the claim page
        response = self.client.get(url)
        self.assertContains(response, "Connect Teams")

        post_data = response.context["form"].initial
        post_data["bot_name"] = "Temba"
        post_data["bot_id"] = "45612"
        post_data["app_id"] = "123456"
        post_data["app_password"] = "a1b2c3"
        post_data["tenant_id"] = "4a5s6d6f"

        self.client.post(url, post_data)

        mock_post.assert_any_call(
            "https://login.microsoftonline.com/botframework.com/oauth2/v2.0/token",
            data={
                "client_id": "123456",
                "grant_type": "client_credentials",
                "scope": "https://api.botframework.com/.default",
                "client_secret": "a1b2c3"
            },
            headers={'Content-Type': 'application/x-www-form-urlencoded'}
        )

        # assert our channel got created
        channel = Channel.objects.get(address="45612")
        self.assertEqual(channel.config[TeamsType.CONFIG_TEAMS_BOT_NAME], "Temba")
        self.assertEqual(channel.config[TeamsType.CONFIG_TEAMS_APPLICATION_PASSWORD], "a1b2c3")
        self.assertEqual(channel.config[TeamsType.CONFIG_TEAMS_APPLICATION_ID], "123456")
        self.assertEqual(channel.address, "45612")

    def test_refresh_tokens(self):

        Channel.objects.all().delete()

        channel = self.create_channel(
            "TM",
            "Teams: 1234",
            "1234",
            config={
                Channel.CONFIG_AUTH_TOKEN: "authtoken123",
                TeamsType.CONFIG_TEAMS_APPLICATION_ID: "1234",
                TeamsType.CONFIG_TEAMS_BOT_ID: "1234",
                TeamsType.CONFIG_TEAMS_TENANT_ID: "4123",
                TeamsType.CONFIG_TEAMS_APPLICATION_PASSWORD: "a1b2n3",
                TeamsType.CONFIG_TEAMS_BOT_NAME: "test_bot",
            },
        )

        channel2 = self.create_channel(
            "TM",
            "Teams: 1235",
            "1235",
            config={
                Channel.CONFIG_AUTH_TOKEN: "authtoken125",
                TeamsType.CONFIG_TEAMS_APPLICATION_ID: "1235",
                TeamsType.CONFIG_TEAMS_BOT_ID: "1235",
                TeamsType.CONFIG_TEAMS_TENANT_ID: "4125",
                TeamsType.CONFIG_TEAMS_APPLICATION_PASSWORD: "a1b2n5",
                TeamsType.CONFIG_TEAMS_BOT_NAME: "test_bot2",
            },
        )

        # and fetching new tokens
        with patch("requests.post") as mock_post:
            mock_post.return_value = MockResponse(200, '{"access_token": "abc345"}')
            self.assertFalse(channel.http_logs.filter(log_type=HTTPLog.TEAMS_TOKENS_SYNCED, is_error=False))
            refresh_teams_tokens()
            self.assertTrue(channel.http_logs.filter(log_type=HTTPLog.TEAMS_TOKENS_SYNCED, is_error=False))
            channel.refresh_from_db()
            self.assertEqual("abc345", channel.config[Channel.CONFIG_AUTH_TOKEN])
        
        with patch("requests.post") as mock_post:
            mock_post.return_value = MockResponse(400, '{ "error": true }')
            self.assertFalse(channel.http_logs.filter(log_type=HTTPLog.TEAMS_TOKENS_SYNCED, is_error=True))
            refresh_teams_tokens()
            self.assertTrue(channel.http_logs.filter(log_type=HTTPLog.TEAMS_TOKENS_SYNCED, is_error=True))
            channel.refresh_from_db()
            self.assertEqual("abc345", channel.config[Channel.CONFIG_AUTH_TOKEN])

        with patch("requests.post") as mock_post:
            mock_post.side_effect = [MockResponse(200, ""), MockResponse(200, '{"access_token": "abc098"}')]
            refresh_teams_tokens()

            channel.refresh_from_db()
            channel2.refresh_from_db()
            self.assertEqual("abc345", channel.config[Channel.CONFIG_AUTH_TOKEN])
            self.assertEqual("abc098", channel2.config[Channel.CONFIG_AUTH_TOKEN])