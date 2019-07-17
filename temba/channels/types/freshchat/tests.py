from django.urls import reverse

from temba.tests import TembaTest

from ...models import Channel


class FrreshChatTypeTest(TembaTest):
    def setUp(self):
        super().setUp()

        self.channel = Channel.create(
            self.org,
            self.user,
            None,
            "FC",
            name="FreshChat",
            address="123456",
            role="SR",
            schemes=["freshchat"],
            config={
                "username": "c0534f78-b6e9-4f79-8853-11cedfc1f35b",
                "auth_token": "eyJVTI0LTm5WZ2Ut",
                "secret": "-----BEGIN RSA PUBLIC KEY----- MIIBIDAQAB -----END RSA PUBLIC KEY-----",
            },
        )

    def test_claim(self):
        url = reverse("channels.types.freshchat.claim")

        self.login(self.admin)

        # check that claim page URL appears on claim list page
        response = self.client.get(reverse("channels.channel_claim"))
        self.assertContains(response, url)

        # can fetch the claim page
        response = self.client.get(url)
        self.assertContains(response, "Connect FreshChat")

        post_data = response.context["form"].initial
        post_data["secret"] = "-----BEGIN RSA PUBLIC KEY----- MIIBIDAQAB -----END RSA PUBLIC KEY-----"
        post_data["auth_token"] = "eyJVTI0LTm5WZ2Ut"
        post_data["username"] = "c0534f78-b6e9-4f79-8853-11cedfc1f35b"

        response = self.client.post(url, post_data, follow=True)

        # assert our channel got created
        channel = Channel.objects.get(address="123456")
        self.assertEqual(channel.config[Channel.CONFIG_AUTH_TOKEN], "eyJVTI0LTm5WZ2Ut")
        self.assertEqual(channel.config[Channel.CONFIG_USERNAME], "c0534f78-b6e9-4f79-8853-11cedfc1f35b")
        self.assertEqual(
            channel.config[Channel.CONFIG_SECRET],
            "-----BEGIN RSA PUBLIC KEY----- MIIBIDAQAB -----END RSA PUBLIC KEY-----",
        )
        self.assertEqual(channel.address, "123456")
