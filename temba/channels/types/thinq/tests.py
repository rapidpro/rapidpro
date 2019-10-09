from django.urls import reverse

from temba.tests import TembaTest

from ...models import Channel


class ThinQTest(TembaTest):
    def test_claim(self):
        Channel.objects.all().delete()

        self.org.timezone = "America/Los_Angeles"
        self.org.save()

        url = reverse("channels.types.thinq.claim")
        self.login(self.admin)

        # check that claim page URL appears on claim list page
        response = self.client.get(reverse("channels.channel_claim"))
        self.assertContains(response, url)

        response = self.client.get(url)
        self.assertEqual(200, response.status_code)
        post_data = response.context["form"].initial

        post_data["number"] = "arst"
        post_data["account_id"] = "1234"
        post_data["country"] = "US"
        post_data["token_user"] = "user1"
        post_data["token"] = "opensesame"

        response = self.client.post(url, post_data)
        self.assertIsNone(Channel.objects.first())

        post_data["number"] = "404-123-4567"
        response = self.client.post(url, post_data)

        channel = Channel.objects.get()

        self.assertEqual("1234", channel.config["account_id"])
        self.assertEqual("user1", channel.config["api_token_user"])
        self.assertEqual("opensesame", channel.config["api_token"])
        self.assertEqual("+14041234567", channel.address)
        self.assertEqual("US", channel.country)
        self.assertEqual(["tel"], channel.schemes)

        config_url = reverse("channels.channel_configuration", args=[channel.uuid])
        self.assertRedirect(response, config_url)

        response = self.client.get(config_url)
        self.assertEqual(200, response.status_code)

        self.assertContains(response, reverse("courier.tq", args=[channel.uuid, "receive"]))
        self.assertContains(response, reverse("courier.tq", args=[channel.uuid, "status"]))
