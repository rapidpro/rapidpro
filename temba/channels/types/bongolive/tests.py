from django.urls import reverse

from temba.tests import TembaTest

from ...models import Channel


class BongoLiveTypeTest(TembaTest):
    def test_claim(self):
        Channel.objects.all().delete()

        url = reverse("channels.types.bongolive.claim")

        self.login(self.admin)

        response = self.client.get(reverse("channels.channel_claim"))
        self.assertNotContains(response, url)

        self.org.timezone = "Africa/Dar_es_Salaam"
        self.org.save()

        # check that claim page URL appears on claim list page
        response = self.client.get(reverse("channels.channel_claim"))
        self.assertContains(response, url)

        response = self.client.get(url)
        self.assertEqual(200, response.status_code)

        post_data = response.context["form"].initial

        post_data["country"] = "TZ"
        post_data["number"] = "5259"
        post_data["username"] = "bongo"
        post_data["password"] = "password"

        response = self.client.post(url, post_data)

        channel = Channel.objects.get()

        self.assertEqual("bongo", channel.config[Channel.CONFIG_USERNAME])
        self.assertEqual("password", channel.config[Channel.CONFIG_PASSWORD])
        self.assertEqual("5259", channel.address)
        self.assertEqual("TZ", channel.country)
        self.assertEqual("BL", channel.channel_type)

        config_url = reverse("channels.channel_configuration", args=[channel.uuid])
        self.assertRedirect(response, config_url)

        response = self.client.get(config_url)
        self.assertEqual(200, response.status_code)

        self.assertContains(response, reverse("courier.bl", args=[channel.uuid]))
