from django.test.utils import override_settings
from django.urls import reverse

from temba.tests import TembaTest

from ...models import Channel


class MtnTypeTest(TembaTest):
    def test_claim(self):
        Channel.objects.all().delete()

        # update the org brand to check the courier URL is set accordingly
        self.org.brand = "custom"
        self.org.save(update_fields=("brand",))

        self.login(self.admin)

        url = reverse("channels.types.mtn.claim")

        # should see the general channel claim page
        response = self.client.get(reverse("channels.channel_claim"))
        self.assertContains(response, url)

        # try to claim a channel
        response = self.client.get(url)
        post_data = response.context["form"].initial

        post_data["number"] = "3071"
        post_data["country"] = "RW"
        post_data["consumer_key"] = "foofoofoofoofoofoofoofoofoofoofoofoofoofoofoofoofoofoofoofoo"
        post_data["consumer_secret"] = "barbarbarbarbarbarbarbarbarbarbarbarbarbarbarbarbarbarbarbar"

        response = self.client.post(url, post_data)

        channel = Channel.objects.get()

        self.assertEqual("RW", channel.country)
        self.assertTrue(channel.uuid)
        self.assertEqual(post_data["number"], channel.address)
        self.assertEqual("MTN", channel.channel_type)
        self.assertEqual(post_data["consumer_key"], channel.config[Channel.CONFIG_API_KEY])
        self.assertEqual(post_data["consumer_secret"], channel.config[Channel.CONFIG_AUTH_TOKEN])

        config_url = reverse("channels.channel_configuration", args=[channel.uuid])
        self.assertRedirect(response, config_url)

        response = self.client.get(config_url)
        self.assertEqual(200, response.status_code)

        # our configuration page should list our receive URL
        self.assertContains(
            response, "https://custom-brand.io" + reverse("courier.mtn", args=[channel.uuid, "receive"])
        )

        self.assertContains(
            response, "https://custom-brand.io" + reverse("courier.mtn", args=[channel.uuid, "status"])
        )

        with override_settings(ORG_LIMIT_DEFAULTS={"channels": 1}):
            response = self.client.post(url, post_data)
            self.assertFormError(
                response,
                "form",
                None,
                "This workspace has reached its limit of 1 channels. You must delete existing ones before you can create new ones.",
            )
