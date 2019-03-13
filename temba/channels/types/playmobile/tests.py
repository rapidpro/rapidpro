import pytz

from django.urls import reverse

from temba.tests import TembaTest

from ...models import Channel


class PlayMobileTypeTest(TembaTest):
    def test_claim(self):
        Channel.objects.all().delete()

        self.login(self.admin)
        url = reverse("channels.types.playmobile.claim")

        # shouldn't be able to see the claim play mobile page if we aren't part of that group
        response = self.client.get(reverse("channels.channel_claim"))
        self.assertNotContains(response, url)

        # but if we are in the proper time zone
        self.org.timezone = pytz.timezone("Asia/Tashkent")
        self.org.save()

        response = self.client.get(reverse("channels.channel_claim"))
        self.assertContains(response, "Play Mobile")
        self.assertContains(response, url)

        # try to claim a channel
        response = self.client.get(url)
        post_data = response.context["form"].initial

        post_data["base_url"] = "http://91.204.239.42:8083"
        post_data["shortcode"] = "1122"
        post_data["username"] = "unicef"
        post_data["password"] = "HmGWbdCFiJBj5bui"

        response = self.client.post(url, post_data)

        channel = Channel.objects.get()

        self.assertEqual("UZ", channel.country)
        self.assertEqual("http://91.204.239.42:8083", channel.config[Channel.CONFIG_BASE_URL])
        self.assertEqual("unicef", channel.config[Channel.CONFIG_USERNAME])
        self.assertEqual("HmGWbdCFiJBj5bui", channel.config[Channel.CONFIG_PASSWORD])
        self.assertEqual(post_data["shortcode"], channel.address)
        self.assertEqual("PM", channel.channel_type)

        config_url = reverse("channels.channel_configuration", args=[channel.uuid])
        self.assertRedirect(response, config_url)

        response = self.client.get(config_url)
        self.assertEqual(200, response.status_code)

        self.assertContains(response, reverse("courier.pm", args=[channel.uuid, "receive"]))
