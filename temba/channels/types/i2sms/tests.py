from django.urls import reverse

from temba.tests import TembaTest

from ...models import Channel


class I2SMSChannelTest(TembaTest):
    def test_claim(self):
        Channel.objects.all().delete()

        url = reverse("channels.types.i2sms.claim")
        self.login(self.admin)

        # check that claim page URL appears on claim list page
        response = self.client.get(reverse("channels.channel_claim"))
        self.assertContains(response, url)

        response = self.client.get(url)
        self.assertEqual(200, response.status_code)
        post_data = response.context["form"].initial

        post_data["number"] = "5259"
        post_data["country"] = "KE"
        post_data["channel_hash"] = "asdf-asdf-asdf-asdf-asdf"
        post_data["username"] = "temba"
        post_data["password"] = "opensesame"

        response = self.client.post(url, post_data)

        channel = Channel.objects.get()

        self.assertEqual("temba", channel.config["username"])
        self.assertEqual("opensesame", channel.config["password"])
        self.assertEqual("asdf-asdf-asdf-asdf-asdf", channel.config["channel_hash"])
        self.assertEqual("5259", channel.address)
        self.assertEqual("KE", channel.country)
        self.assertEqual("I2", channel.channel_type)

        config_url = reverse("channels.channel_configuration", args=[channel.uuid])
        self.assertRedirect(response, config_url)

        response = self.client.get(config_url)
        self.assertEqual(200, response.status_code)

        self.assertContains(response, reverse("courier.i2", args=[channel.uuid, "receive"]))
