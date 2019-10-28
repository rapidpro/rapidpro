from unittest.mock import patch

import nexmo

from django.urls import reverse

from temba.channels.models import Channel
from temba.tests import MockResponse, TembaTest
from temba.utils import json

from .client import NexmoClient


def mock_json_response(status_code, data):
    return MockResponse(status_code, json.dumps(data), headers={"Content-Type": "application/json"})


class NexmoTypeTest(TembaTest):
    def test_claim(self):
        self.login(self.admin)

        claim_nexmo = reverse("channels.types.nexmo.claim")

        # remove any existing channels
        self.org.channels.update(is_active=False)

        # make sure nexmo is on the claim page
        response = self.client.get(reverse("channels.channel_claim"))
        self.assertContains(response, "Nexmo")

        response = self.client.get(claim_nexmo)
        self.assertEqual(response.status_code, 302)
        response = self.client.get(claim_nexmo, follow=True)
        self.assertEqual(response.request["PATH_INFO"], reverse("orgs.org_nexmo_connect"))

        self.org.connect_nexmo("nexmo-key", "nexmo-secret", self.admin)

        # hit the claim page, should now have a claim nexmo link
        response = self.client.get(reverse("channels.channel_claim"))
        self.assertContains(response, claim_nexmo)

        # try adding a shortcode
        with patch("requests.get") as nexmo_get, patch("requests.post") as nexmo_post:
            nexmo_get.side_effect = [
                mock_json_response(200, {"count": 0, "numbers": []}),
                mock_json_response(
                    200,
                    {
                        "count": 1,
                        "numbers": [{"features": ["SMS"], "type": "mobile-lvn", "country": "US", "msisdn": "8080"}],
                    },
                ),
                mock_json_response(
                    200,
                    {
                        "count": 1,
                        "numbers": [{"features": ["SMS"], "type": "mobile-lvn", "country": "US", "msisdn": "8080"}],
                    },
                ),
            ]
            nexmo_post.return_value = mock_json_response(200, {"error-code": "200"})
            response = self.client.post(claim_nexmo, dict(country="US", phone_number="8080"))
            self.assertRedirects(response, reverse("public.public_welcome") + "?success")
            channel = Channel.objects.filter(address="8080").first()
            self.assertTrue(Channel.ROLE_SEND in channel.role)
            self.assertTrue(Channel.ROLE_RECEIVE in channel.role)
            self.assertFalse(Channel.ROLE_ANSWER in channel.role)
            self.assertFalse(Channel.ROLE_CALL in channel.role)
            Channel.objects.all().delete()

        # try buying a number not on the account
        with patch("requests.get") as nexmo_get, patch("requests.post") as nexmo_post:
            nexmo_get.side_effect = [
                mock_json_response(200, {"count": 0, "numbers": []}),
                mock_json_response(200, {"count": 0, "numbers": []}),
                mock_json_response(
                    200,
                    {
                        "count": 1,
                        "numbers": [
                            {"features": ["SMS"], "type": "mobile", "country": "US", "msisdn": "+12065551212"}
                        ],
                    },
                ),
            ]
            nexmo_post.return_value = mock_json_response(200, {"error-code": "200"})

            response = self.client.post(claim_nexmo, dict(country="US", phone_number="+12065551212"))
            self.assertRedirects(response, reverse("public.public_welcome") + "?success")

            channel = Channel.objects.filter(address="+12065551212").first()
            self.assertTrue(Channel.ROLE_SEND in channel.role)
            self.assertTrue(Channel.ROLE_RECEIVE in channel.role)
            Channel.objects.all().delete()

        # try failing to buy a number not on the account
        with patch("requests.get") as nexmo_get, patch("requests.post") as nexmo_post:
            nexmo_get.side_effect = [
                mock_json_response(200, {"count": 0, "numbers": []}),
                mock_json_response(200, {"count": 0, "numbers": []}),
            ]
            nexmo_post.side_effect = Exception("Error")
            response = self.client.post(claim_nexmo, dict(country="US", phone_number="+12065551212"))
            self.assertTrue(response.context["form"].errors)
            self.assertContains(
                response,
                "There was a problem claiming that number, "
                "please check the balance on your account. "
                "Note that you can only claim numbers after "
                "adding credit to your Nexmo account.",
            )
            Channel.objects.all().delete()

        # let's add a number already connected to the account
        with patch("requests.get") as nexmo_get, patch("requests.post") as nexmo_post:
            nexmo_get.return_value = mock_json_response(
                200,
                {
                    "count": 1,
                    "numbers": [
                        {"features": ["SMS", "VOICE"], "type": "mobile-lvn", "country": "US", "msisdn": "13607884540"}
                    ],
                },
            )
            nexmo_post.side_effect = [
                mock_json_response(200, {"error-code": "200", "id": "myappid", "keys": {"private_key": "private"}}),
                mock_json_response(200, {"error-code": "200"}),
            ]

            # make sure our number appears on the claim page
            response = self.client.get(claim_nexmo)
            self.assertNotIn("account_trial", response.context)
            self.assertContains(response, "360-788-4540")

            # claim it
            response = self.client.post(claim_nexmo, dict(country="US", phone_number="13607884540"))
            self.assertRedirects(response, reverse("public.public_welcome") + "?success")

            # make sure it is actually connected
            channel = Channel.objects.get(channel_type="NX", org=self.org)
            self.assertTrue(Channel.ROLE_SEND in channel.role)
            self.assertTrue(Channel.ROLE_RECEIVE in channel.role)
            self.assertTrue(Channel.ROLE_ANSWER in channel.role)
            self.assertTrue(Channel.ROLE_CALL in channel.role)

            self.assertEqual(channel.config[Channel.CONFIG_NEXMO_API_KEY], "nexmo-key")
            self.assertEqual(channel.config[Channel.CONFIG_NEXMO_API_SECRET], "nexmo-secret")
            self.assertEqual(channel.config[Channel.CONFIG_NEXMO_APP_ID], "myappid")
            self.assertEqual(channel.config[Channel.CONFIG_NEXMO_APP_PRIVATE_KEY], "private")

            # test the update page for nexmo
            update_url = reverse("channels.channel_update", args=[channel.pk])
            response = self.client.get(update_url)

            # try changing our address
            updated = response.context["form"].initial
            updated["address"] = "MTN"
            updated["alert_email"] = "foo@bar.com"

            response = self.client.post(update_url, updated)
            channel = Channel.objects.get(pk=channel.id)

            self.assertEqual("MTN", channel.address)

            nexmo_get.reset_mock()
            nexmo_post.reset_mock()

            # add a canada number
            nexmo_get.return_value = mock_json_response(
                200,
                {
                    "count": 1,
                    "numbers": [{"features": ["SMS"], "type": "mobile-lvn", "country": "CA", "msisdn": "15797884540"}],
                },
            )
            nexmo_post.side_effect = None
            nexmo_post.return_value = mock_json_response(200, {"error-code": "200"})

            # make sure our number appears on the claim page
            response = self.client.get(claim_nexmo)
            self.assertNotIn("account_trial", response.context)
            self.assertContains(response, "579-788-4540")

            # claim it
            response = self.client.post(claim_nexmo, dict(country="CA", phone_number="15797884540"))

            self.assertRedirects(response, reverse("public.public_welcome") + "?success")

            # make sure it is actually connected
            self.assertTrue(Channel.objects.filter(channel_type="NX", org=self.org, address="+15797884540").first())

            # as is our old one
            self.assertTrue(Channel.objects.filter(channel_type="NX", org=self.org, address="MTN").first())

            config_url = reverse("channels.channel_configuration", args=[channel.uuid])
            response = self.client.get(config_url)
            self.assertEqual(200, response.status_code)

            self.assertContains(response, reverse("courier.nx", args=[channel.uuid, "receive"]))
            self.assertContains(response, reverse("courier.nx", args=[channel.uuid, "status"]))
            self.assertContains(response, reverse("mailroom.ivr_handler", args=[channel.uuid, "incoming"]))

    def test_deactivate(self):
        # convert our test channel to be a Nexmo channel
        self.org.connect_nexmo("TEST_KEY", "TEST_SECRET", self.admin)
        channel = self.org.channels.all().first()
        channel.channel_type = "NX"
        channel.config = {Channel.CONFIG_NEXMO_APP_ID: "myappid", Channel.CONFIG_NEXMO_APP_PRIVATE_KEY: "secret"}
        channel.save(update_fields=("channel_type", "config"))

        # mock a 404 response from Nexmo during deactivation
        with self.settings(IS_PROD=True):
            with patch("nexmo.Client.delete_application") as mock_delete_application:
                mock_delete_application.side_effect = nexmo.ClientError("404 response")

                # releasing shouldn't blow up on auth failures
                channel.release()
                channel.refresh_from_db()

                self.assertFalse(channel.is_active)

                mock_delete_application.assert_called_once_with(application_id="myappid")


class ClientTest(TembaTest):
    def setUp(self):
        super().setUp()

        self.client = NexmoClient("abc123", "asecret")

    @patch("nexmo.Client.get_balance")
    def test_check_credentials(self, mock_get_balance):
        mock_get_balance.side_effect = nexmo.AuthenticationError("401 not allowed")

        self.assertFalse(self.client.check_credentials())

        mock_get_balance.side_effect = None
        mock_get_balance.return_value = "12.35"

        self.assertTrue(self.client.check_credentials())

    @patch("nexmo.Client.get_account_numbers")
    def test_get_numbers(self, mock_get_account_numbers):
        mock_get_account_numbers.return_value = {"count": 2, "numbers": ["23463", "568658"]}

        self.assertEqual(self.client.get_numbers(pattern="+593"), ["23463", "568658"])

        mock_get_account_numbers.assert_called_once_with(params={"size": 10, "pattern": "593"})

    @patch("nexmo.Client.create_application")
    def test_create_application(self, mock_create_application):
        mock_create_application.return_value = {"id": "myappid", "keys": {"private_key": "tejh42gf3"}}

        app_id, app_private_key = self.client.create_application("rapidpro.io", "702cb3b5-8fec-4974-a87a-75234117c768")
        self.assertEqual(app_id, "myappid")
        self.assertEqual(app_private_key, "tejh42gf3")

        mock_create_application.assert_called_once_with(
            params={
                "name": "rapidpro.io/702cb3b5-8fec-4974-a87a-75234117c768",
                "type": "voice",
                "answer_url": "https://rapidpro.io/mr/ivr/c/702cb3b5-8fec-4974-a87a-75234117c768/incoming",
                "answer_method": "POST",
                "event_url": "https://rapidpro.io/mr/ivr/c/702cb3b5-8fec-4974-a87a-75234117c768/status",
                "event_method": "POST",
            }
        )

    @patch("nexmo.Client.delete_application")
    def test_delete_application(self, mock_delete_application):
        self.client.delete_application("myappid")

        mock_delete_application.assert_called_once_with(application_id="myappid")

    @patch("temba.channels.types.nexmo.client.NexmoClient.RATE_LIMIT_PAUSE", 0)
    @patch("nexmo.Client.get_account_numbers")
    def test_retry(self, mock_get_account_numbers):
        mock_get_account_numbers.side_effect = [
            nexmo.ClientError("420 response from tests.com"),
            {"count": 2, "numbers": ["23463", "568658"]},
        ]

        self.assertEqual(self.client.get_numbers(), ["23463", "568658"])
