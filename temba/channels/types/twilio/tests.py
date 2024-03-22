from unittest.mock import patch

from twilio.base.exceptions import TwilioRestException

from django.urls import reverse

from temba.channels.models import Channel
from temba.contacts.models import URN
from temba.tests import TembaTest
from temba.tests.twilio import MockRequestValidator, MockTwilioClient

from .type import TwilioType


class TwilioTypeTest(TembaTest):
    @patch("temba.channels.types.twilio.views.TwilioClient", MockTwilioClient)
    @patch("temba.channels.types.twilio.type.TwilioClient", MockTwilioClient)
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
        self.assertEqual(response.request["PATH_INFO"], reverse("channels.types.twilio.connect"))

        # check the connect view has no initial set
        response = self.client.get(reverse("channels.types.twilio.connect"))
        self.assertEqual(200, response.status_code)
        self.assertEqual(list(response.context["form"].fields.keys()), ["account_sid", "account_token", "loc"])
        self.assertFalse(response.context["form"].initial)

        # attach a Twilio account to the session
        session = self.client.session
        session[TwilioType.SESSION_ACCOUNT_SID] = "account-sid"
        session[TwilioType.SESSION_AUTH_TOKEN] = "account-token"
        session.save()

        # hit the claim page, should now have a claim twilio link
        response = self.client.get(reverse("channels.channel_claim"))
        self.assertContains(response, claim_twilio)

        response = self.client.get(claim_twilio)
        self.assertIn("account_trial", response.context)
        self.assertFalse(response.context["account_trial"])

        with patch("temba.channels.types.twilio.views.ClaimView.get_twilio_client") as mock_get_twilio_client:
            mock_get_twilio_client.return_value = None

            response = self.client.get(claim_twilio)
            self.assertRedirects(response, f'{reverse("channels.types.twilio.connect")}?claim_type=twilio')

            mock_get_twilio_client.side_effect = TwilioRestException(
                401, "http://twilio", msg="Authentication Failure", code=20003
            )

            response = self.client.get(claim_twilio)
            self.assertRedirects(response, f'{reverse("channels.types.twilio.connect")}?claim_type=twilio')

        with patch("temba.tests.twilio.MockTwilioClient.MockAccounts.get") as mock_get:
            mock_get.return_value = MockTwilioClient.MockAccount("Trial")

            response = self.client.get(claim_twilio)
            self.assertIn("account_trial", response.context)
            self.assertTrue(response.context["account_trial"])

        with patch("temba.tests.twilio.MockTwilioClient.MockPhoneNumbers.list") as mock_search:
            search_url = reverse("channels.types.twilio.search")

            # try making empty request
            response = self.client.post(search_url, {})
            self.assertEqual(response.json(), [])

            # try searching for US number
            mock_search.return_value = [MockTwilioClient.MockPhoneNumber("+12062345678")]
            response = self.client.post(search_url, {"country": "US", "pattern": "206"})
            self.assertEqual(response.json(), ["+1 206-234-5678", "+1 206-234-5678", "+1 206-234-5678"])

            # try searching without area code
            response = self.client.post(search_url, {"country": "US", "pattern": ""})
            self.assertEqual(response.json(), ["+1 206-234-5678", "+1 206-234-5678", "+1 206-234-5678"])

            mock_search.return_value = []
            response = self.client.post(search_url, {"country": "US", "pattern": ""})
            self.assertEqual(
                response.json()["error"], "Sorry, no numbers found, please enter another area code and try again."
            )

            # try searching for non-US number
            mock_search.return_value = [MockTwilioClient.MockPhoneNumber("+442812345678")]
            response = self.client.post(search_url, {"country": "GB", "pattern": "028"})
            self.assertEqual(response.json(), ["+44 28 1234 5678", "+44 28 1234 5678", "+44 28 1234 5678"])

            mock_search.return_value = []
            response = self.client.post(search_url, {"country": "GB", "pattern": ""})
            self.assertEqual(
                response.json()["error"], "Sorry, no numbers found, please enter another pattern and try again."
            )

        with patch("temba.tests.twilio.MockTwilioClient.MockPhoneNumbers.stream") as mock_numbers:
            mock_numbers.return_value = iter([MockTwilioClient.MockPhoneNumber("+12062345678")])

            with patch("temba.tests.twilio.MockTwilioClient.MockShortCodes.stream") as mock_short_codes:
                mock_short_codes.return_value = iter([])

                response = self.client.get(claim_twilio)
                self.assertContains(response, "206-234-5678")
                self.assertFalse(self.org.supports_ivr())

                # claim it
                response = self.client.post(claim_twilio, dict(country="US", phone_number="12062345678"))
                self.assertRedirects(response, reverse("public.public_welcome") + "?success")

                # make sure it is actually connected
                channel = Channel.objects.get(channel_type="T", org=self.org)
                self.assertEqual(
                    channel.role, Channel.ROLE_CALL + Channel.ROLE_ANSWER + Channel.ROLE_SEND + Channel.ROLE_RECEIVE
                )
                self.assertEqual(channel.tps, 1)
                self.assertTrue(channel.supports_ivr())
                self.assertTrue(self.org.supports_ivr())

                channel_config = channel.config
                self.assertEqual(channel_config[Channel.CONFIG_ACCOUNT_SID], "account-sid")
                self.assertEqual(channel_config[Channel.CONFIG_AUTH_TOKEN], "account-token")
                self.assertTrue(channel_config[Channel.CONFIG_APPLICATION_SID])
                self.assertTrue(channel_config[Channel.CONFIG_NUMBER_SID])

                # no more credential in the session
                self.assertNotIn(TwilioType.SESSION_ACCOUNT_SID, self.client.session)
                self.assertNotIn(TwilioType.SESSION_AUTH_TOKEN, self.client.session)

        response = self.client.get(reverse("channels.types.twilio.connect"))
        self.assertEqual(302, response.status_code)
        self.assertRedirects(response, reverse("channels.types.twilio.claim"))

        response = self.client.get(reverse("channels.types.twilio.connect") + "?claim_type=foo")
        self.assertEqual(302, response.status_code)
        self.assertRedirects(response, reverse("channels.channel_claim"))

        response = self.client.get(reverse("channels.types.twilio.connect") + "?claim_type=twilio&reset_creds=reset")
        self.assertEqual(200, response.status_code)

        session = self.client.session
        session[TwilioType.SESSION_ACCOUNT_SID] = "account-sid"
        session[TwilioType.SESSION_AUTH_TOKEN] = "account-token"
        session.save()

        with patch("temba.tests.twilio.MockTwilioClient.MockPhoneNumbers.stream") as mock_numbers:
            mock_numbers.return_value = iter([MockTwilioClient.MockPhoneNumber("+12062345678")])

            with patch("temba.tests.twilio.MockTwilioClient.MockShortCodes.stream") as mock_short_codes:
                mock_short_codes.return_value = iter([])

                response = self.client.get(claim_twilio)
                self.assertContains(response, "206-234-5678")

                # claim it
                response = self.client.post(claim_twilio, dict(country="US", phone_number="12062345678"))
                self.assertFormError(
                    response.context["form"], None, "This channel is already connected in this workspace."
                )

                # make sure the schemes do not overlap, having a WA channel with the same number
                channel = Channel.objects.get(channel_type="T", org=self.org)
                channel.channel_type = "WA"
                channel.schemes = [URN.WHATSAPP_SCHEME]
                channel.save()

                # now we can clain the number already used for Twilio WhatsApp
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

        session = self.client.session
        session[TwilioType.SESSION_ACCOUNT_SID] = "account-sid"
        session[TwilioType.SESSION_AUTH_TOKEN] = "account-token"
        session.save()

        # voice only number
        with patch("temba.tests.twilio.MockTwilioClient.MockPhoneNumbers.stream") as mock_numbers:
            mock_numbers.return_value = iter([MockTwilioClient.MockPhoneNumber("+554139087835", sms=False, voice=True)])

            with patch("temba.tests.twilio.MockTwilioClient.MockShortCodes.stream") as mock_short_codes:
                mock_short_codes.return_value = iter([])
                Channel.objects.all().delete()

                with patch("temba.tests.twilio.MockTwilioClient.MockPhoneNumbers.get") as mock_numbers_get:
                    mock_numbers_get.return_value = MockTwilioClient.MockPhoneNumber(
                        "+554139087835", sms=False, voice=True
                    )

                    response = self.client.get(claim_twilio)
                    self.assertContains(response, "+55 41 3908-7835")

                    # claim it
                    mock_numbers.return_value = iter(
                        [MockTwilioClient.MockPhoneNumber("+554139087835", sms=False, voice=True)]
                    )
                    response = self.client.post(claim_twilio, dict(country="BR", phone_number="554139087835"))
                    self.assertRedirects(response, reverse("public.public_welcome") + "?success")

                    # make sure it is actually connected
                    channel = Channel.objects.get(channel_type="T", org=self.org)
                    self.assertEqual(channel.role, Channel.ROLE_CALL + Channel.ROLE_ANSWER)
                    self.assertEqual(channel.tps, 10)

        session = self.client.session
        session[TwilioType.SESSION_ACCOUNT_SID] = "account-sid"
        session[TwilioType.SESSION_AUTH_TOKEN] = "account-token"
        session.save()

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

        session = self.client.session
        session[TwilioType.SESSION_ACCOUNT_SID] = "account-sid"
        session[TwilioType.SESSION_AUTH_TOKEN] = "account-token"
        session.save()

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

        with patch("temba.tests.twilio.MockTwilioClient.MockPhoneNumber.update") as mock_numbers:
            # our twilio channel removal should fail on bad auth
            mock_numbers.side_effect = TwilioRestException(
                401, "http://twilio", msg="Authentication Failure", code=20003
            )
            self.client.post(reverse("channels.channel_delete", args=[twilio_channel.uuid]))
            self.assertIsNotNone(self.org.channels.all().first())

            # or other arbitrary twilio errors
            mock_numbers.side_effect = TwilioRestException(400, "http://twilio", msg="Twilio Error", code=123)
            self.client.post(reverse("channels.channel_delete", args=[twilio_channel.uuid]))
            self.assertIsNotNone(self.org.channels.all().first())

            # now lets be successful
            mock_numbers.side_effect = None
            self.client.post(reverse("channels.channel_delete", args=[twilio_channel.uuid]))
            self.assertIsNone(self.org.channels.filter(is_active=True).first())
            self.assertEqual(mock_numbers.call_args_list[-1][1], dict(voice_application_sid="", sms_application_sid=""))

    @patch("temba.channels.types.twilio.views.TwilioClient", MockTwilioClient)
    @patch("temba.channels.types.twilio.type.TwilioClient", MockTwilioClient)
    @patch("twilio.request_validator.RequestValidator", MockRequestValidator)
    def test_update(self):
        config = {
            Channel.CONFIG_ACCOUNT_SID: "TEST_SID",
            Channel.CONFIG_AUTH_TOKEN: "TEST_TOKEN",
        }
        twilio_channel = self.org.channels.all().first()
        twilio_channel.config = config
        twilio_channel.channel_type = "T"
        twilio_channel.save()

        update_url = reverse("channels.channel_update", args=[twilio_channel.id])

        self.login(self.admin)
        response = self.client.get(update_url)
        self.assertEqual(
            ["name", "allow_international", "account_sid", "auth_token", "loc"],
            list(response.context["form"].fields.keys()),
        )

        post_data = dict(name="Foo channel", allow_international=False, account_sid="ACC_SID", auth_token="ACC_Token")

        response = self.client.post(update_url, post_data)

        self.assertEqual(response.status_code, 302)

        twilio_channel.refresh_from_db()
        self.assertEqual(twilio_channel.name, "Foo channel")
        # we used the primary credentials returned on the account fetch even though we submit the others
        self.assertEqual(twilio_channel.config[Channel.CONFIG_ACCOUNT_SID], "AccountSid")
        self.assertEqual(twilio_channel.config[Channel.CONFIG_AUTH_TOKEN], "AccountToken")
        self.assertTrue(twilio_channel.check_credentials())

        with patch("temba.channels.types.twilio.type.TwilioType.check_credentials") as mock_check_credentials:
            mock_check_credentials.return_value = False

            response = self.client.post(update_url, post_data)
            self.assertFormError(response.context["form"], None, "Credentials don't appear to be valid.")

        # staff users see extra log policy field
        self.login(self.customer_support, choose_org=self.org)
        response = self.client.get(update_url)
        self.assertEqual(
            ["name", "log_policy", "allow_international", "account_sid", "auth_token", "loc"],
            list(response.context["form"].fields.keys()),
        )

    @patch("temba.channels.types.twilio.type.TwilioClient", MockTwilioClient)
    @patch("twilio.request_validator.RequestValidator", MockRequestValidator)
    def test_deactivate(self):
        # make our channel of the twilio ilk
        config = {
            Channel.CONFIG_ACCOUNT_SID: "TEST_SID",
            Channel.CONFIG_AUTH_TOKEN: "TEST_TOKEN",
        }
        twilio_channel = self.org.channels.all().first()
        twilio_channel.config = config
        twilio_channel.channel_type = "T"
        twilio_channel.save()

        # mock an authentication failure during the release process
        with patch("temba.tests.twilio.MockTwilioClient.MockPhoneNumbers.update") as mock_numbers:
            mock_numbers.side_effect = TwilioRestException(
                401, "http://twilio", msg="Authentication Failure", code=20003
            )

            # releasing shouldn't blow up on auth failures
            twilio_channel.release(self.admin)
            twilio_channel.refresh_from_db()
            self.assertFalse(twilio_channel.is_active)

    def test_get_error_ref_url(self):
        self.assertEqual("https://www.twilio.com/docs/api/errors/30006", TwilioType().get_error_ref_url(None, "30006"))

    @patch("temba.channels.types.twilio.type.TwilioClient", MockTwilioClient)
    @patch("twilio.request_validator.RequestValidator", MockRequestValidator)
    def test_check_credentials(self):
        self.assertTrue(TwilioType().check_credentials({"account_sid": "AccountSid", "auth_token": "AccountToken"}))

        with patch("temba.tests.twilio.MockTwilioClient.MockAccount.fetch") as mock_fetch:
            mock_fetch.side_effect = Exception("blah!")
            self.assertFalse(
                TwilioType().check_credentials({"account_sid": "AccountSid", "auth_token": "AccountToken"})
            )

    @patch("temba.channels.types.twilio.views.TwilioClient", MockTwilioClient)
    @patch("twilio.request_validator.RequestValidator", MockRequestValidator)
    def test_twilio_connect(self):
        with patch("temba.tests.twilio.MockTwilioClient.MockAccounts.get") as mock_get:
            mock_get.return_value = MockTwilioClient.MockAccount("Full")

            connect_url = reverse("channels.types.twilio.connect")

            self.login(self.admin)

            response = self.client.get(connect_url)
            self.assertEqual(200, response.status_code)
            self.assertEqual(list(response.context["form"].fields.keys()), ["account_sid", "account_token", "loc"])
            self.assertFalse(response.context["form"].initial)

            # try posting without an account token
            post_data = {"account_sid": "AccountSid"}
            response = self.client.post(connect_url, post_data)
            self.assertFormError(response.context["form"], "account_token", "This field is required.")

            # now add the account token and try again
            post_data["account_token"] = "AccountToken"

            # but with an unexpected exception
            with patch("temba.tests.twilio.MockTwilioClient.__init__") as mock:
                mock.side_effect = Exception("Unexpected")
                response = self.client.post(connect_url, post_data)
                self.assertEqual(
                    response.context["errors"][0],
                    "The Twilio account SID and Token seem invalid. Please check them again and retry.",
                )

            response = self.client.post(connect_url, post_data)

            self.assertIn(TwilioType.SESSION_ACCOUNT_SID, self.client.session)
            self.assertIn(TwilioType.SESSION_AUTH_TOKEN, self.client.session)
            self.assertEqual(self.client.session[TwilioType.SESSION_ACCOUNT_SID], "AccountSid")
            self.assertEqual(self.client.session[TwilioType.SESSION_AUTH_TOKEN], "AccountToken")

            # when the user submit the secondary token, we use it to get the primary one from the rest API
            with patch("temba.tests.twilio.MockTwilioClient.MockAccounts.get") as mock_get_primary:
                with patch("twilio.rest.api.v2010.account.AccountContext.fetch") as mock_account_fetch:
                    mock_get_primary.return_value = MockTwilioClient.MockAccount("Full", "PrimaryAccountToken")
                    mock_account_fetch.return_value = MockTwilioClient.MockAccount("Full", "PrimaryAccountToken")

                    response = self.client.post(connect_url, post_data)
                    self.assertEqual(response.status_code, 302)

                    response = self.client.post(connect_url, post_data, follow=True)
                    self.assertEqual(response.request["PATH_INFO"], reverse("channels.types.twilio.claim"))

                    self.assertIn(TwilioType.SESSION_ACCOUNT_SID, self.client.session)
                    self.assertIn(TwilioType.SESSION_AUTH_TOKEN, self.client.session)
                    self.assertEqual(self.client.session[TwilioType.SESSION_ACCOUNT_SID], "AccountSid")
                    self.assertEqual(self.client.session[TwilioType.SESSION_AUTH_TOKEN], "PrimaryAccountToken")

                    response = self.client.post(
                        f'{reverse("channels.types.twilio.connect")}?claim_type=twilio', post_data, follow=True
                    )
                    self.assertEqual(response.request["PATH_INFO"], reverse("channels.types.twilio.claim"))

                    response = self.client.post(
                        f'{reverse("channels.types.twilio.connect")}?claim_type=twilio_messaging_service',
                        post_data,
                        follow=True,
                    )
                    self.assertEqual(
                        response.request["PATH_INFO"], reverse("channels.types.twilio_messaging_service.claim")
                    )

                    response = self.client.post(
                        f'{reverse("channels.types.twilio.connect")}?claim_type=twilio_whatsapp',
                        post_data,
                        follow=True,
                    )
                    self.assertEqual(response.request["PATH_INFO"], reverse("channels.types.twilio_whatsapp.claim"))

                    response = self.client.post(
                        f'{reverse("channels.types.twilio.connect")}?claim_type=unknown', post_data, follow=True
                    )
                    self.assertEqual(response.request["PATH_INFO"], reverse("channels.channel_claim"))
