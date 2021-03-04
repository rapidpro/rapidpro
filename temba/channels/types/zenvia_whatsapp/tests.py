
from django.urls import reverse

from temba.tests import TembaTest

from ...models import Channel

class ZenviaWhatsAppTypeTest(TembaTest):
    def test_claim(self):
        Channel.objects.all().delete()

        self.login(self.admin)

        url = reverse("channels.types.zenvia_whatsapp.claim")

        # should see the general channel claim page
        response = self.client.get(reverse("channels.channel_claim"))
        self.assertContains(response, url)

        # try to claim a channel
        response = self.client.get(url)
        post_data = response.context["form"].initial

        post_data["api_key"] = "12345"
        post_data["country"] = "US"
        post_data["number"] = "(206) 555-1212"

        response = self.client.post(url, post_data)

        channel = Channel.objects.get()

        self.assertEqual("US", channel.country)
        self.assertTrue(channel.uuid)
        self.assertEqual("+12065551212", channel.address)
        self.assertEqual("12345", channel.config["api_key"])
        self.assertEqual("ZVW", channel.channel_type)
