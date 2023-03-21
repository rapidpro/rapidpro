from unittest.mock import patch

from django.urls import reverse

from temba.tests import MockResponse, TembaTest

from ...models import Channel


class BandwidthTypeTest(TembaTest):
    @patch("requests.post")
    def test_claim(self, mock_post):
        mock_post.return_value = MockResponse(
            200,
            "<ApplicationProvisioningResponse><Application><ApplicationId>e5a9e103-application_id</ApplicationId></Application></ApplicationProvisioningResponse>",
        )

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

        response = self.client.post(url, post_data)

        channel = Channel.objects.get()

        self.assertEqual("US", channel.country)
        self.assertEqual(post_data["username"], channel.config["username"])
        self.assertEqual(post_data["password"], channel.config["password"])
        self.assertEqual(post_data["account_id"], channel.config["account_id"])
        self.assertEqual("e5a9e103-application_id", channel.config["application_id"])
        self.assertEqual("250788123123", channel.address)
        self.assertEqual("BW", channel.channel_type)

        config_url = reverse("channels.channel_configuration", args=[channel.uuid])

        self.assertRedirect(response, config_url)

        self.assertEqual(
            mock_post.call_args_list[0][0][0], "https://dashboard.bandwidth.com/api/accounts/account-id/applications"
        )

        self.assertEqual(mock_post.call_args_list[0][1]["auth"][0], "user1")
        self.assertEqual(mock_post.call_args_list[0][1]["auth"][1], "pass1")
        self.assertEqual(
            mock_post.call_args_list[0][1]["data"],
            f"<Application><ServiceType>Messaging-V2</ServiceType><AppName>app.rapidpro.io/{channel.uuid}</AppName><InboundCallbackUrl>https://app.rapidpro.io/c/bw/{channel.uuid}/receive</InboundCallbackUrl><OutboundCallbackUrl>https://app.rapidpro.io/c/bw/{channel.uuid}/status</OutboundCallbackUrl><RequestedCallbackTypes><CallbackType>message-delivered</CallbackType><CallbackType>message-failed</CallbackType><CallbackType>message-sending</CallbackType></RequestedCallbackTypes></Application>",
        )

        response = self.client.get(config_url)
        self.assertEqual(200, response.status_code)
        self.assertContains(
            response, "https://dashboard.bandwidth.com/portal/r/a/account-id/applications/e5a9e103-application_id"
        )

        with patch("requests.delete") as mock_delete:
            mock_delete.side_effect = [MockResponse(200, "")]
            channel.release(self.admin)

            self.assertEqual(
                mock_delete.call_args[0][0],
                "https://dashboard.bandwidth.com/api/accounts/account-id/applications/e5a9e103-application_id",
            )
