from unittest.mock import patch

from twilio.base.exceptions import TwilioRestException

from django.urls import reverse

from temba.channels.models import Channel
from temba.orgs.models import Org
from temba.tests import TembaTest
from temba.tests.twilio import MockRequestValidator, MockTwilioClient


class TwilioWhatsappTypeTest(TembaTest):
    @patch("temba.orgs.models.TwilioClient", MockTwilioClient)
    @patch("twilio.request_validator.RequestValidator", MockRequestValidator)
    def test_claim(self):
        self.login(self.admin)

        claim_twilio = reverse("channels.types.twilio_whatsapp.claim")

        # remove any existing channels
        self.org.channels.update(is_active=False)

        # make sure twilio is on the claim page
        response = self.client.get(reverse("channels.channel_claim"))
        self.assertContains(response, "Twilio")

        response = self.client.get(claim_twilio)
        self.assertEqual(response.status_code, 302)
        response = self.client.get(claim_twilio, follow=True)
        self.assertEqual(response.request["PATH_INFO"], reverse("orgs.org_twilio_connect"))

        # attach a Twilio accont to the org
        self.org.config = {Org.CONFIG_TWILIO_SID: "account-sid", Org.CONFIG_TWILIO_TOKEN: "account-token"}
        self.org.save()

        # hit the claim page, should now have a claim twilio link
        response = self.client.get(reverse("channels.channel_claim"))
        self.assertContains(response, claim_twilio)

        response = self.client.get(claim_twilio)
        self.assertIn("account_trial", response.context)
        self.assertFalse(response.context["account_trial"])

        with patch("temba.orgs.models.Org.get_twilio_client") as mock_get_twilio_client:
            mock_get_twilio_client.return_value = None

            response = self.client.get(claim_twilio)
            self.assertRedirects(response, f'{reverse("orgs.org_twilio_connect")}?claim_type=twilio_whatsapp')

            mock_get_twilio_client.side_effect = TwilioRestException(
                401, "http://twilio", msg="Authentication Failure", code=20003
            )

            response = self.client.get(claim_twilio)
            self.assertRedirects(response, f'{reverse("orgs.org_twilio_connect")}?claim_type=twilio_whatsapp')

        with patch("temba.tests.twilio.MockTwilioClient.MockAccounts.get") as mock_get:
            mock_get.return_value = MockTwilioClient.MockAccount("Trial")

            response = self.client.get(claim_twilio)
            self.assertIn("account_trial", response.context)
            self.assertTrue(response.context["account_trial"])

        with patch("temba.tests.twilio.MockTwilioClient.MockPhoneNumbers.list") as mock_search:
            search_url = reverse("channels.channel_search_numbers")

            # try making empty request
            response = self.client.post(search_url, {})
            self.assertEqual(response.json(), [])

            # try searching for US number
            mock_search.return_value = [MockTwilioClient.MockPhoneNumber("+12062345678")]
            response = self.client.post(search_url, {"country": "US", "area_code": "206"})
            self.assertEqual(response.json(), ["+1 206-234-5678", "+1 206-234-5678", "+1 206-234-5678"])

            # try searching without area code
            response = self.client.post(search_url, {"country": "US", "area_code": ""})
            self.assertEqual(response.json(), ["+1 206-234-5678", "+1 206-234-5678", "+1 206-234-5678"])

            mock_search.return_value = []
            response = self.client.post(search_url, {"country": "US", "area_code": ""})
            self.assertEqual(
                response.json()["error"], "Sorry, no numbers found, please enter another area code and try again."
            )

            # try searching for non-US number
            mock_search.return_value = [MockTwilioClient.MockPhoneNumber("+442812345678")]
            response = self.client.post(search_url, {"country": "GB", "area_code": "028"})
            self.assertEqual(response.json(), ["+44 28 1234 5678", "+44 28 1234 5678", "+44 28 1234 5678"])

            mock_search.return_value = []
            response = self.client.post(search_url, {"country": "GB", "area_code": ""})
            self.assertEqual(
                response.json()["error"], "Sorry, no numbers found, please enter another pattern and try again."
            )

        with patch("temba.tests.twilio.MockTwilioClient.MockPhoneNumbers.stream") as mock_numbers:
            mock_numbers.return_value = iter([MockTwilioClient.MockPhoneNumber("+12062345678")])

            response = self.client.get(claim_twilio)
            self.assertContains(response, "206-234-5678")

            # claim it
            response = self.client.post(claim_twilio, dict(country="US", phone_number="12062345678"))
            self.assertFormError(
                response, "form", "phone_number", "Only existing Twilio WhatsApp number are supported"
            )

        with patch("temba.tests.twilio.MockTwilioClient.MockPhoneNumbers.stream") as mock_numbers:
            mock_numbers.return_value = iter([MockTwilioClient.MockPhoneNumber("+12062345678")])

            with patch("temba.tests.twilio.MockTwilioClient.MockPhoneNumbers.get") as mock_numbers_get:
                mock_numbers_get.return_value = MockTwilioClient.MockPhoneNumber("+12062345678")

                response = self.client.get(claim_twilio)
                self.assertContains(response, "206-234-5678")

                # claim it
                mock_numbers.return_value = iter([MockTwilioClient.MockPhoneNumber("+12062345678")])
                response = self.client.post(claim_twilio, dict(country="US", phone_number="+12062345678"))
                self.assertRedirects(response, reverse("public.public_welcome") + "?success")

                # make sure it is actually connected
                channel = Channel.objects.get(channel_type="TWA", org=self.org)
                self.assertEqual(channel.role, Channel.ROLE_SEND + Channel.ROLE_RECEIVE)

        twilio_channel = self.org.channels.all().first()
        # make channel support both sms and voice to check we clear both applications
        twilio_channel.role = Channel.ROLE_SEND + Channel.ROLE_RECEIVE + Channel.ROLE_ANSWER + Channel.ROLE_CALL
        twilio_channel.save()
        self.assertEqual("TWA", twilio_channel.channel_type)

        self.client.post(reverse("channels.channel_delete", args=[twilio_channel.pk]))
        self.assertIsNotNone(self.org.channels.all().first())
