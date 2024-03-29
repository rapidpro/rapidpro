from django.urls import reverse

from temba.tests import TembaTest

from ...models import Channel


class JioChatTypeTest(TembaTest):
    def test_claim(self):
        url = reverse("channels.types.jiochat.claim")

        self.login(self.admin)

        # check that claim page URL appears on claim list page
        response = self.client.get(reverse("channels.channel_claim"))
        self.assertContains(response, url)

        # try to claim a channel
        response = self.client.get(url)
        post_data = response.context["form"].initial
        post_data["app_id"] = "foofoofoofoofoofoofoofoofoofoofoofoofoofoofoofoofoofoofoofoo"
        post_data["app_secret"] = "barbarbarbarbarbarbarbarbarbarbarbarbarbarbarbarbarbarbarbar"

        response = self.client.post(url, post_data)

        channel = Channel.objects.get(channel_type="JC")

        self.assertEqual(
            channel.config,
            {
                "jiochat_app_id": post_data["app_id"],
                "jiochat_app_secret": post_data["app_secret"],
                "secret": channel.config["secret"],
            },
        )

        config_url = reverse("channels.channel_configuration", args=[channel.uuid])
        self.assertRedirect(response, config_url)

        response = self.client.get(config_url)
        self.assertEqual(200, response.status_code)

        self.assertContains(response, reverse("courier.jc", args=[channel.uuid]))
        self.assertContains(response, channel.config[Channel.CONFIG_SECRET])

        # make sure we our jiochat channel satisfies as a send channel
        send_channel = self.org.get_send_channel()
        self.assertIsNotNone(send_channel)
        self.assertEqual(send_channel.channel_type, "JC")
