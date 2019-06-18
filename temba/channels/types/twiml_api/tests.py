from unittest.mock import patch

from django.urls import reverse

from temba.tests import TembaTest
from temba.tests.twilio import MockRequestValidator, MockTwilioClient


class TwimlAPITypeTest(TembaTest):
    def setUp(self):
        super().setUp()

        self.login(self.admin)

        # remove any existing channels
        self.org.channels.update(is_active=False)

    @patch("temba.ivr.clients.TwilioClient", MockTwilioClient)
    @patch("twilio.request_validator.RequestValidator", MockRequestValidator)
    def test_channel_claim_page(self):
        claim_url = reverse("channels.types.twiml_api.claim")

        response = self.client.get(reverse("channels.channel_claim"))
        self.assertContains(response, "TwiML")
        self.assertContains(response, claim_url)

        # can fetch the claim page
        response = self.client.get(claim_url)
        self.assertEqual(200, response.status_code)
        self.assertContains(response, "TwiML")

    @patch("temba.ivr.clients.TwilioClient", MockTwilioClient)
    @patch("twilio.request_validator.RequestValidator", MockRequestValidator)
    def test_channel_claim_form_error_invalid_data(self):
        claim_url = reverse("channels.types.twiml_api.claim")

        form_data = dict(number="5512345678", country="AA")

        response = self.client.post(claim_url, form_data)

        self.assertTrue(response.context["form"].errors)

        self.assertFormError(
            response, "form", "country", "Select a valid choice. AA is not one of the available choices."
        )
        self.assertFormError(response, "form", "url", "This field is required.")
        self.assertFormError(response, "form", "role", "This field is required.")

    @patch("temba.ivr.clients.TwilioClient", MockTwilioClient)
    @patch("twilio.request_validator.RequestValidator", MockRequestValidator)
    def test_channel_claim_form_valid_data(self):
        claim_url = reverse("channels.types.twiml_api.claim")

        form_data = dict(
            country="US",
            number="12345678",
            url="https://twilio.com",
            role="SR",
            account_sid="abcd1234",
            account_token="abcd1234",
            max_concurrent_events=30,
        )
        response = self.client.post(claim_url, form_data)
        channel = self.org.channels.filter(is_active=True).first()

        self.assertRedirects(response, reverse("channels.channel_configuration", args=[channel.uuid]))
        self.assertEqual(channel.channel_type, "TW")

        expected_data = dict(
            auth_token="abcd1234",
            send_url="https://twilio.com",
            account_sid="abcd1234",
            max_concurrent_events=30,
            callback_domain=channel.callback_domain,
        )

        self.assertEqual(channel.config, expected_data)

        response = self.client.get(reverse("channels.channel_configuration", args=[channel.uuid]))
        self.assertContains(response, reverse("courier.tw", args=[channel.uuid, "receive"]))

    @patch("temba.ivr.clients.TwilioClient", MockTwilioClient)
    @patch("twilio.request_validator.RequestValidator", MockRequestValidator)
    def test_channel_claim_form_valid_data_shortcode(self):
        claim_url = reverse("channels.types.twiml_api.claim")

        form_data = dict(
            country="US",
            number="8080",
            url="https://twilio.com",
            role="SR",
            account_sid="abcd1234",
            account_token="abcd1234",
        )
        response = self.client.post(claim_url, form_data)

        channel = self.org.channels.filter(is_active=True).first()

        self.assertRedirects(response, reverse("channels.channel_configuration", args=[channel.uuid]))
        self.assertEqual(channel.channel_type, "TW")

        expected_data = dict(
            auth_token="abcd1234",
            send_url="https://twilio.com",
            account_sid="abcd1234",
            max_concurrent_events=None,
            callback_domain=channel.callback_domain,
        )

        self.assertEqual(channel.config, expected_data)

        response = self.client.get(reverse("channels.channel_configuration", args=[channel.uuid]))
        self.assertContains(response, reverse("courier.tw", args=[channel.uuid, "receive"]))

    @patch("temba.ivr.clients.TwilioClient", MockTwilioClient)
    @patch("twilio.request_validator.RequestValidator", MockRequestValidator)
    def test_channel_claim_form_without_account_sid(self):
        claim_url = reverse("channels.types.twiml_api.claim")

        form_data = dict(country="US", number="8080", url="https://twilio.com", role="SR", account_token="abcd1234")
        response = self.client.post(claim_url, form_data)

        channel = self.org.channels.filter(is_active=True).first()

        self.assertRedirects(response, reverse("channels.channel_configuration", args=[channel.uuid]))
        self.assertEqual(channel.channel_type, "TW")

        expected_data = dict(
            auth_token="abcd1234",
            send_url="https://twilio.com",
            account_sid=f"rapidpro_{channel.pk}",
            max_concurrent_events=None,
            callback_domain=channel.callback_domain,
        )

        self.assertEqual(channel.config, expected_data)

        response = self.client.get(reverse("channels.channel_configuration", args=[channel.uuid]))
        self.assertContains(response, reverse("courier.tw", args=[channel.uuid, "receive"]))
