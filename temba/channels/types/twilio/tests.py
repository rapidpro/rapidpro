from unittest.mock import patch

from twilio.base.exceptions import TwilioRestException

from django.urls import reverse

from temba.channels.models import Channel
from temba.orgs.models import Org
from temba.tests import TembaTest
from temba.tests.twilio import MockRequestValidator, MockTwilioClient


class TwilioTypeTest(TembaTest):
    @patch("temba.orgs.models.TwilioClient", MockTwilioClient)
    @patch("twilio.request_validator.RequestValidator", MockRequestValidator)
    def test_claim(self):
        self.login(self.admin)

        claim_twilio = reverse("channels.types.twilio.claim")

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
            self.assertRedirects(response, reverse("orgs.org_twilio_connect"))

            mock_get_twilio_client.side_effect = TwilioRestException(
                401, "http://twilio", msg="Authentication Failure", code=20003
            )

            response = self.client.get(claim_twilio)
            self.assertRedirects(response, reverse("orgs.org_twilio_connect"))

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

            with patch("temba.tests.twilio.MockTwilioClient.MockShortCodes.stream") as mock_short_codes:
                mock_short_codes.return_value = iter([])

                response = self.client.get(claim_twilio)
                self.assertContains(response, "206-234-5678")

                # claim it
                response = self.client.post(claim_twilio, dict(country="US", phone_number="12062345678"))
                self.assertRedirects(response, reverse("public.public_welcome") + "?success")

                # make sure it is actually connected
                channel = Channel.objects.get(channel_type="T", org=self.org)
                self.assertEqual(
                    channel.role, Channel.ROLE_CALL + Channel.ROLE_ANSWER + Channel.ROLE_SEND + Channel.ROLE_RECEIVE
                )
                self.assertEqual(channel.tps, 1)

                channel_config = channel.config
                self.assertEqual(channel_config[Channel.CONFIG_ACCOUNT_SID], "account-sid")
                self.assertEqual(channel_config[Channel.CONFIG_AUTH_TOKEN], "account-token")
                self.assertTrue(channel_config[Channel.CONFIG_APPLICATION_SID])
                self.assertTrue(channel_config[Channel.CONFIG_NUMBER_SID])

        # voice only number
        with patch("temba.tests.twilio.MockTwilioClient.MockPhoneNumbers.stream") as mock_numbers:
            mock_numbers.return_value = iter([MockTwilioClient.MockPhoneNumber("+554139087835")])

            with patch("temba.tests.twilio.MockTwilioClient.MockShortCodes.stream") as mock_short_codes:
                mock_short_codes.return_value = iter([])
                Channel.objects.all().delete()

                with patch("temba.tests.twilio.MockTwilioClient.MockPhoneNumbers.get") as mock_numbers_get:
                    mock_numbers_get.return_value = MockTwilioClient.MockPhoneNumber("+554139087835")

                    response = self.client.get(claim_twilio)
                    self.assertContains(response, "+55 41 3908-7835")

                    # claim it
                    mock_numbers.return_value = iter([MockTwilioClient.MockPhoneNumber("+554139087835")])
                    response = self.client.post(claim_twilio, dict(country="BR", phone_number="554139087835"))
                    self.assertRedirects(response, reverse("public.public_welcome") + "?success")

                    # make sure it is actually connected
                    channel = Channel.objects.get(channel_type="T", org=self.org)
                    self.assertEqual(channel.role, Channel.ROLE_CALL + Channel.ROLE_ANSWER)
                    self.assertEqual(channel.tps, 10)

        with patch("temba.tests.twilio.MockTwilioClient.MockPhoneNumbers.stream") as mock_numbers:
            mock_numbers.return_value = iter([MockTwilioClient.MockPhoneNumber("+4545335500")])

            with patch("temba.tests.twilio.MockTwilioClient.MockShortCodes.stream") as mock_short_codes:
                mock_short_codes.return_value = iter([])

                Channel.objects.all().delete()

                response = self.client.get(claim_twilio)
                self.assertContains(response, "45 33 55 00")
                self.assertEqual(mock_numbers.call_args_list[0][1], {"page_size": 1000})

                # claim it
                response = self.client.post(claim_twilio, dict(country="DK", phone_number="4545335500"))
                self.assertRedirects(response, reverse("public.public_welcome") + "?success")

                # make sure it is actually connected
                channel = Channel.objects.get(channel_type="T", org=self.org)
                self.assertEqual(channel.tps, 10)

        with patch("temba.tests.twilio.MockTwilioClient.MockPhoneNumbers.stream") as mock_numbers:
            mock_numbers.return_value = iter([])

            with patch("temba.tests.twilio.MockTwilioClient.MockShortCodes.stream") as mock_short_codes:
                mock_short_codes.return_value = iter([MockTwilioClient.MockShortCode("8080")])

                with patch("temba.tests.twilio.MockTwilioClient.MockShortCodes.get") as mock_short_codes_get:
                    mock_short_codes_get.return_value = MockTwilioClient.MockShortCode("8080")

                    Channel.objects.all().delete()

                    self.org.timezone = "America/New_York"
                    self.org.save()

                    response = self.client.get(claim_twilio)
                    self.assertContains(response, "8080")
                    self.assertContains(response, 'class="country">US')  # we look up the country from the timezone

                    # claim it
                    mock_short_codes.return_value = iter([MockTwilioClient.MockShortCode("8080")])
                    response = self.client.post(claim_twilio, dict(country="US", phone_number="8080"))
                    self.assertRedirects(response, reverse("public.public_welcome") + "?success")
                    self.assertEqual(mock_numbers.call_args_list[0][1], {"page_size": 1000})

                    # make sure it is actually connected
                    channel = Channel.objects.get(channel_type="T", org=self.org)
                    self.assertEqual(channel.tps, 100)

        twilio_channel = self.org.channels.all().first()
        # make channel support both sms and voice to check we clear both applications
        twilio_channel.role = Channel.ROLE_SEND + Channel.ROLE_RECEIVE + Channel.ROLE_ANSWER + Channel.ROLE_CALL
        twilio_channel.save()
        self.assertEqual("T", twilio_channel.channel_type)

        with self.settings(IS_PROD=True):
            with patch("temba.tests.twilio.MockTwilioClient.MockPhoneNumber.update") as mock_numbers:
                # our twilio channel removal should fail on bad auth
                mock_numbers.side_effect = TwilioRestException(
                    401, "http://twilio", msg="Authentication Failure", code=20003
                )
                self.client.post(reverse("channels.channel_delete", args=[twilio_channel.pk]))
                self.assertIsNotNone(self.org.channels.all().first())

                # or other arbitrary twilio errors
                mock_numbers.side_effect = TwilioRestException(400, "http://twilio", msg="Twilio Error", code=123)
                self.client.post(reverse("channels.channel_delete", args=[twilio_channel.pk]))
                self.assertIsNotNone(self.org.channels.all().first())

                # now lets be successful
                mock_numbers.side_effect = None
                self.client.post(reverse("channels.channel_delete", args=[twilio_channel.pk]))
                self.assertIsNone(self.org.channels.filter(is_active=True).first())
                self.assertEqual(
                    mock_numbers.call_args_list[-1][1], dict(voice_application_sid="", sms_application_sid="")
                )

    def test_update(self):
        update_url = reverse("channels.channel_update", args=[self.channel.id])

        self.login(self.admin)
        response = self.client.get(update_url)
        self.assertEqual(
            ["name", "address", "country", "alert_email", "allow_international", "loc"],
            list(response.context["form"].fields.keys()),
        )

    @patch("temba.orgs.models.TwilioClient", MockTwilioClient)
    @patch("twilio.request_validator.RequestValidator", MockRequestValidator)
    def test_deactivate(self):

        # make our channel of the twilio ilk
        self.org.connect_twilio("TEST_SID", "TEST_TOKEN", self.admin)
        twilio_channel = self.org.channels.all().first()
        twilio_channel.channel_type = "T"
        twilio_channel.save()

        # mock an authentication failure during the release process
        with self.settings(IS_PROD=True):
            with patch("temba.tests.twilio.MockTwilioClient.MockPhoneNumbers.update") as mock_numbers:
                mock_numbers.side_effect = TwilioRestException(
                    401, "http://twilio", msg="Authentication Failure", code=20003
                )

                # releasing shouldn't blow up on auth failures
                twilio_channel.release()
                twilio_channel.refresh_from_db()
                self.assertFalse(twilio_channel.is_active)
