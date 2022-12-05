from django.urls import reverse

from temba.tests import TembaTest

from ...models import Channel


class BandwidthTypeTest(TembaTest):
    def test_claim(self):
        Channel.objects.all().delete()

        url = reverse("channels.types.bandwidth.claim")

        self.login(self.admin)

        response = self.client.get(reverse("channels.channel_claim"))
        self.assertNotContains(response, url)

        self.make_beta(self.admin)

        # check that claim page URL appears on claim list page
        response = self.client.get(reverse("channels.channel_claim"))
        self.assertContains(response, url)

        response = self.client.get(url)
        self.assertEqual(200, response.status_code)
        post_data = response.context["form"].initial

        post_data["country"] = "US"
        post_data["number"] = "250788123123"
        post_data["username"] = "user1"
        post_data["password"] = "pass1"
        post_data["account_id"] = "account-id"
        post_data["application_id"] = "app-id"

        response = self.client.post(url, post_data)

        channel = Channel.objects.get()

        self.assertEqual("US", channel.country)
        self.assertEqual(post_data["username"], channel.config["username"])
        self.assertEqual(post_data["password"], channel.config["password"])
        self.assertEqual(post_data["account_id"], channel.config["account_id"])
        self.assertEqual(post_data["application_id"], channel.config["application_id"])
        self.assertEqual("250788123123", channel.address)
        self.assertEqual("BW", channel.channel_type)

        config_url = reverse("channels.channel_configuration", args=[channel.uuid])
        self.assertRedirect(response, config_url)

        response = self.client.get(config_url)
        self.assertEqual(200, response.status_code)

        self.assertContains(response, reverse("courier.bw", args=[channel.uuid, "receive"]))
        self.assertContains(response, reverse("courier.bw", args=[channel.uuid, "status"]))
