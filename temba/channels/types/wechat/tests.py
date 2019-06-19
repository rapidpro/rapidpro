from unittest.mock import patch

from django.test import override_settings
from django.urls import reverse

from temba.channels.types.wechat.tasks import refresh_wechat_access_tokens
from temba.contacts.models import URN
from temba.tests import MockResponse, TembaTest
from temba.utils.wechat import WeChatClient

from ...models import Channel, ChannelLog


class WeChatTypeTest(TembaTest):
    @override_settings(IP_ADDRESSES=("10.10.10.10", "172.16.20.30"))
    def test_claim(self):
        url = reverse("channels.types.wechat.claim")

        self.login(self.admin)

        # check that claim page URL appears on claim list page
        response = self.client.get(reverse("channels.channel_claim"))
        self.assertNotContains(response, url)

        # try to claim a channel
        response = self.client.get(url)
        post_data = response.context["form"].initial
        post_data["app_id"] = "foofoofoofoofoofoofoofoofoofoofoofoofoofoofoofoofoofoofoofoo"
        post_data["app_secret"] = "barbarbarbarbarbarbarbarbarbarbarbarbarbarbarbarbarbarbarbar"

        response = self.client.post(url, post_data)

        channel = Channel.objects.get(channel_type="WC")

        self.assertEqual(
            channel.config,
            {
                "wechat_app_id": post_data["app_id"],
                "wechat_app_secret": post_data["app_secret"],
                "secret": channel.config["secret"],
            },
        )

        config_url = reverse("channels.channel_configuration", args=[channel.uuid])
        self.assertRedirect(response, config_url)

        response = self.client.get(config_url)
        self.assertEqual(200, response.status_code)

        self.assertContains(response, reverse("courier.wc", args=[channel.uuid]))
        self.assertContains(response, channel.config[Channel.CONFIG_SECRET])

        # check we show the IP to whitelist
        self.assertContains(response, "10.10.10.10")
        self.assertContains(response, "172.16.20.30")

        contact = self.create_contact("WeChat User", urn=URN.from_wechat("1234"))

        # make sure we our jiochat channel satisfies as a send channel
        response = self.client.get(reverse("contacts.contact_read", args=[contact.uuid]))
        send_channel = response.context["send_channel"]
        self.assertIsNotNone(send_channel)
        self.assertEqual(send_channel.channel_type, "WC")

    @patch("requests.get")
    def test_refresh_wechat_tokens(self, mock_get):
        self.clear_cache()
        channel = Channel.create(
            self.org,
            self.user,
            None,
            "WC",
            None,
            "1212",
            config={
                "wechat_app_id": "app-id",
                "wechat_app_secret": "app-secret",
                "secret": Channel.generate_secret(32),
            },
            uuid="00000000-0000-0000-0000-000000001234",
        )

        mock_get.return_value = MockResponse(400, '{ "errcode": 40013, "error":"Failed" }')

        channel_client = WeChatClient.from_channel(channel)

        ChannelLog.objects.all().delete()
        self.assertFalse(ChannelLog.objects.all())
        refresh_wechat_access_tokens()

        self.assertEqual(ChannelLog.objects.filter(channel_id=channel.pk).count(), 1)
        self.assertTrue(ChannelLog.objects.filter(is_error=True).count(), 1)

        self.assertEqual(mock_get.call_count, 1)
        mock_get.reset_mock()
        mock_get.return_value = MockResponse(200, '{ "errcode": 10, "access_token":"ABC1234" }')

        refresh_wechat_access_tokens()

        self.assertEqual(ChannelLog.objects.filter(channel_id=channel.pk).count(), 2)
        self.assertTrue(ChannelLog.objects.filter(channel_id=channel.pk, is_error=True).count(), 2)
        self.assertEqual(mock_get.call_count, 1)

        mock_get.reset_mock()
        mock_get.return_value = MockResponse(200, '{ "errcode": 0, "access_token":"ABC1234" }')

        refresh_wechat_access_tokens()

        self.assertEqual(ChannelLog.objects.filter(channel_id=channel.pk).count(), 3)
        self.assertTrue(ChannelLog.objects.filter(channel_id=channel.pk, is_error=True).count(), 2)
        self.assertTrue(ChannelLog.objects.filter(channel_id=channel.pk, is_error=False).count(), 1)
        self.assertEqual(mock_get.call_count, 1)

        self.assertEqual(channel_client.get_access_token(), b"ABC1234")
        self.assertEqual(
            mock_get.call_args_list[0][1]["params"],
            {"secret": "app-secret", "grant_type": "client_credential", "appid": "app-id"},
        )
        self.login(self.admin)
        response = self.client.get(reverse("channels.channellog_list", args=[channel.uuid]) + "?others=1", follow=True)
        self.assertEqual(len(response.context["object_list"]), 3)

        mock_get.reset_mock()
        mock_get.return_value = MockResponse(200, '{ "access_token":"ABC1235" }')
