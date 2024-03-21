from unittest.mock import patch

from django.test.utils import override_settings
from django.urls import reverse

from temba.tests import TembaTest
from temba.tests.base import override_brand

from ...models import Channel


class KannelTypeTest(TembaTest):
    @patch("socket.gethostbyname", return_value="123.123.123.123")
    def test_claim(self, mock_socket_hostname):
        Channel.objects.all().delete()

        with override_brand(name="Temba", domain="temba.io"):
            self.login(self.admin)

            url = reverse("channels.types.kannel.claim")

            # should see the general channel claim page
            response = self.client.get(reverse("channels.channel_claim"))
            self.assertContains(response, url)

            # try to claim a channel
            response = self.client.get(url)
            post_data = response.context["form"].initial

            post_data["number"] = "3071"
            post_data["country"] = "RW"
            post_data["url"] = "http://nyaruka.com/cgi-bin/sendsms"
            post_data["verify_ssl"] = False
            post_data["encoding"] = Channel.ENCODING_SMART

            response = self.client.post(url, post_data)

            channel = Channel.objects.get()

            self.assertEqual("RW", channel.country)
            self.assertTrue(channel.uuid)
            self.assertEqual(post_data["number"], channel.address)
            self.assertEqual(post_data["url"], channel.config["send_url"])
            self.assertEqual(False, channel.config["verify_ssl"])
            self.assertEqual(Channel.ENCODING_SMART, channel.config[Channel.CONFIG_ENCODING])

            # make sure we generated a username and password
            self.assertTrue(channel.config["username"])
            self.assertTrue(channel.config["password"])
            self.assertEqual(channel.config[Channel.CONFIG_CALLBACK_DOMAIN], "temba.io")
            self.assertEqual("KN", channel.channel_type)

            config_url = reverse("channels.channel_configuration", args=[channel.uuid])
            self.assertRedirect(response, config_url)

            response = self.client.get(config_url)
            self.assertEqual(200, response.status_code)

            # our configuration page should list our receive URL
            self.assertContains(response, "https://temba.io" + reverse("courier.kn", args=[channel.uuid, "receive"]))

        with override_settings(ORG_LIMIT_DEFAULTS={"channels": 1}):
            response = self.client.post(url, post_data)
            self.assertFormError(
                response.context["form"],
                None,
                "This workspace has reached its limit of 1 channels. You must delete existing ones before you can create new ones.",
            )
