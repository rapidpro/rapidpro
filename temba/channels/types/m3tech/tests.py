
from django.urls import reverse

from temba.tests import TembaTest

from ...models import Channel


class M3TechTypeTest(TembaTest):
    def test_claim(self):
        Channel.objects.all().delete()

        url = reverse("channels.types.m3tech.claim")

        self.login(self.admin)

        response = self.client.get(reverse("channels.channel_claim"))
        self.assertNotContains(response, url)

        self.org.timezone = "Asia/Karachi"
        self.org.save()

        # check that claim page URL appears on claim list page
        response = self.client.get(reverse("channels.channel_claim"))
        self.assertContains(response, url)

        # try to claim a channel
        response = self.client.get(url)
        post_data = response.context["form"].initial

        post_data["country"] = "PK"
        post_data["number"] = "250788123123"
        post_data["username"] = "user1"
        post_data["password"] = "pass1"

        response = self.client.post(url, post_data)

        channel = Channel.objects.get()

        self.assertEqual("PK", channel.country)
        self.assertEqual(post_data["username"], channel.config["username"])
        self.assertEqual(post_data["password"], channel.config["password"])
        self.assertEqual("+250788123123", channel.address)
        self.assertEqual("M3", channel.channel_type)

        config_url = reverse("channels.channel_configuration", args=[channel.uuid])
        self.assertRedirect(response, config_url)

        response = self.client.get(config_url)
        self.assertEqual(200, response.status_code)

        self.assertContains(response, reverse("courier.m3", args=[channel.uuid, "receive"]))
        self.assertContains(response, reverse("courier.m3", args=[channel.uuid, "sent"]))
        self.assertContains(response, reverse("courier.m3", args=[channel.uuid, "delivered"]))
        self.assertContains(response, reverse("courier.m3", args=[channel.uuid, "failed"]))

        Channel.objects.all().delete()

        response = self.client.get(url)
        post_data = response.context["form"].initial

        post_data["country"] = "PK"
        post_data["number"] = "20050"
        post_data["username"] = "user1"
        post_data["password"] = "pass1"

        response = self.client.post(url, post_data)

        channel = Channel.objects.get()

        self.assertEqual("PK", channel.country)
        self.assertEqual(post_data["username"], channel.config["username"])
        self.assertEqual(post_data["password"], channel.config["password"])
        self.assertEqual("20050", channel.address)
        self.assertEqual("M3", channel.channel_type)
