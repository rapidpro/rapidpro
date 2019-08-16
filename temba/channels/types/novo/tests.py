import pytz

from django.urls import reverse

from temba.tests import TembaTest

from ...models import Channel


class NovoTypeTest(TembaTest):
    def test_claim(self):
        Channel.objects.all().delete()

        self.login(self.admin)
        url = reverse("channels.types.novo.claim")

        # shouldn't be able to see the claim novo page if we aren't part of that group
        response = self.client.get(reverse("channels.channel_claim"))
        self.assertNotContains(response, url)

        # but if we are in the proper time zone
        self.org.timezone = pytz.timezone("America/Port_of_Spain")
        self.org.save()

        response = self.client.get(reverse("channels.channel_claim"))
        self.assertContains(response, "Novo")
        self.assertContains(response, url)

        # try to claim a channel
        response = self.client.get(url)
        post_data = response.context["form"].initial

        post_data["shortcode"] = "1234"
        post_data["merchant_id"] = "smsAPI_Merchant"
        post_data["merchant_secret"] = "HmGWbdCFiJBj5bui"

        response = self.client.post(url, post_data)

        channel = Channel.objects.get()

        from .type import NovoType

        self.assertEqual("TT", channel.country)
        self.assertEqual("smsAPI_Merchant", channel.config[NovoType.CONFIG_MERCHANT_ID])
        self.assertEqual("HmGWbdCFiJBj5bui", channel.config[NovoType.CONFIG_MERCHANT_SECRET])
        self.assertEqual(post_data["shortcode"], channel.address)
        self.assertEqual("NV", channel.channel_type)

        config_url = reverse("channels.channel_configuration", args=[channel.uuid])
        self.assertRedirect(response, config_url)

        response = self.client.get(config_url)
        self.assertEqual(200, response.status_code)

        self.assertContains(response, reverse("courier.nv", args=[channel.uuid, "receive"]))
        self.assertContains(response, channel.config["secret"])
