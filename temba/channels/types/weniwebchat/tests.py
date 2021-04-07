from django.urls import reverse

from temba.tests import TembaTest

from ...models import Channel
from .type import CONFIG_BASE_URL


class VKTypeTest(TembaTest):

    def test_claim(self):
        url = reverse("channels.types.weniwebchat.claim")
        self.login(self.admin)

        response = self.client.get(reverse("channels.channel_claim"))
        self.assertContains(response, url)

        data = {
            "name": "Weni Testing",
            "base_url": "https://google.com",
        }

        response = self.client.post(url, data, follow=True)

        channel = Channel.objects.get(name="Weni Testing")

        self.assertEqual(channel.config[CONFIG_BASE_URL], "https://google.com")
