from django.urls import reverse

from temba.tests import TembaTest

from ...models import Channel


class FreshChatTypeTest(TembaTest):
    def setUp(self):
        super().setUp()

        self.channel = Channel.create(
            self.org,
            self.user,
            None,
            "FC",
            name="FreshChat",
            address="12345",
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
        post_data["webhook_key"] = "-----BEGIN RSA PUBLIC KEY----- MIIBIDAQAB -----END RSA PUBLIC KEY-----"
        post_data["auth_token"] = "eyJVTI0LTm5WZ2Ut"
        post_data["agent_id"] = "c0534f78-b6e9-4f79-8853-11cedfc1f35b"
        post_data["title"] = "FreshChat"

        response = self.client.post(url, post_data, follow=True)
        # assert our channel got created
        channel = Channel.objects.get(address="c0534f78-b6e9-4f79-8853-11cedfc1f35b")
        self.assertEqual(channel.config[Channel.CONFIG_AUTH_TOKEN], "eyJVTI0LTm5WZ2Ut")
        self.assertEqual(channel.config[Channel.CONFIG_USERNAME], "c0534f78-b6e9-4f79-8853-11cedfc1f35b")
        self.assertEqual(
            channel.config[Channel.CONFIG_SECRET],
            "-----BEGIN RSA PUBLIC KEY----- MIIBIDAQAB -----END RSA PUBLIC KEY-----",
        )
        self.assertEqual(channel.address, "c0534f78-b6e9-4f79-8853-11cedfc1f35b")
