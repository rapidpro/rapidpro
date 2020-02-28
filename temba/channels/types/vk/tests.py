from django.urls import reverse

from temba.tests import TembaTest

from ...models import Channel
from .type import CONFIG_CALLBACK_VERIFICATION_STRING, CONFIG_COMMUNITY_NAME


class VKTypeTest(TembaTest):
    def setUp(self):
        super().setUp()

        self.channel = Channel.create(
            self.org,
            self.user,
            None,
            "VK",
            name="VK Community",
            address="12345",
            role="SR",
            schemes=["vk"],
            config={
                "auth_token": "09876543",
                "community_name": "Vk Community",
                "secret": "203ijwijwij2ej2eii02ie0i2e2e",
                "callback_verification_string": "12j323k",
            },
        )

    def test_claim(self):
        url = reverse("channels.types.vk.claim")
        self.login(self.admin)

        response = self.client.get(reverse("channels.channel_claim"))
        self.assertContains(response, url)

        token = "x" * 200

        data = {
            "community_access_token": token,
            "community_id": "123456",
            "community_name": "Temba",
            "callback_verification_string": "123456",
        }

        response = self.client.post(url, data, follow=True)

        channel = Channel.objects.get(address="123456")
        self.assertEqual(channel.config[Channel.CONFIG_AUTH_TOKEN], token)
        self.assertEqual(channel.config[CONFIG_COMMUNITY_NAME], "Temba")
        self.assertEqual(channel.config[CONFIG_CALLBACK_VERIFICATION_STRING], "123456")
        self.assertEqual(channel.address, "123456")
