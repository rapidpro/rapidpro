from unittest.mock import patch

from django.urls import reverse

from temba.tests import TembaTest
from temba.tests.twilio import MockRequestValidator, MockTwilioClient

from .type import SomlengType


class SomlengTypeTest(TembaTest):
    def setUp(self):
        super().setUp()

        self.login(self.admin)

        # remove any existing channels
        self.org.channels.update(is_active=False)

    @patch("twilio.rest.Client", MockTwilioClient)
    @patch("twilio.request_validator.RequestValidator", MockRequestValidator)
    def test_channel_claim_page(self):
        claim_url = reverse("channels.types.somleng.claim")

        response = self.client.get(reverse("channels.channel_claim"))
        self.assertContains(response, "Somleng")
        self.assertContains(response, claim_url)

        # can fetch the claim page
        response = self.client.get(claim_url)
        self.assertEqual(200, response.status_code)
        self.assertContains(response, "Somleng")

    @patch("twilio.rest.Client", MockTwilioClient)
    @patch("twilio.request_validator.RequestValidator", MockRequestValidator)
    def test_channel_claim_form_error_invalid_data(self):
        claim_url = reverse("channels.types.somleng.claim")

        form_data = dict(number="5512345678", country="AA")

        response = self.client.post(claim_url, form_data)

        self.assertTrue(response.context["form"].errors)

        self.assertFormError(
            response.context["form"], "country", "Select a valid choice. AA is not one of the available choices."
        )
        self.assertFormError(response.context["form"], "url", "This field is required.")
        self.assertFormError(response.context["form"], "role", "This field is required.")

    @patch("twilio.rest.Client", MockTwilioClient)
    @patch("twilio.request_validator.RequestValidator", MockRequestValidator)
    @patch("socket.gethostbyname", return_value="123.123.123.123")
    def test_channel_claim_form_valid_data(self, mock_socket_hostname):
        claim_url = reverse("channels.types.somleng.claim")

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

    @patch("twilio.rest.Client", MockTwilioClient)
    @patch("twilio.request_validator.RequestValidator", MockRequestValidator)
    @patch("socket.gethostbyname", return_value="123.123.123.123")
    def test_channel_claim_form_valid_data_shortcode(self, mock_socket_hostname):
        claim_url = reverse("channels.types.somleng.claim")

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

    @patch("twilio.rest.Client", MockTwilioClient)
    @patch("twilio.request_validator.RequestValidator", MockRequestValidator)
    @patch("socket.gethostbyname", return_value="123.123.123.123")
    def test_channel_claim_form_without_account_sid(self, mock_socket_hostname):
        claim_url = reverse("channels.types.somleng.claim")

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

    def test_config(self):
        channel = self.create_channel("TW", "Somleng", "1234", role="SR")
        config_url = reverse("channels.channel_configuration", args=[channel.uuid])
        courier_receive_url = f"https://app.rapidpro.io/c/tw/{channel.uuid}/receive"
        courier_status_url = f"https://app.rapidpro.io/c/tw/{channel.uuid}/status"
        mailroom_incoming_url = f"https://app.rapidpro.io/mr/ivr/c/{channel.uuid}/incoming"
        mailroom_status_url = f"https://app.rapidpro.io/mr/ivr/c/{channel.uuid}/status"

        self.login(self.admin)

        response = self.client.get(config_url)
        self.assertContains(response, courier_receive_url)
        self.assertContains(response, courier_status_url)
        self.assertNotContains(response, mailroom_incoming_url)
        self.assertNotContains(response, mailroom_status_url)

        channel.role = "CA"
        channel.save(update_fields=("role",))

        response = self.client.get(config_url)
        self.assertNotContains(response, courier_receive_url)
        self.assertNotContains(response, courier_status_url)
        self.assertContains(response, mailroom_incoming_url)
        self.assertContains(response, mailroom_status_url)

    def test_get_error_ref_url(self):
        self.assertEqual("https://www.twilio.com/docs/api/errors/30006", SomlengType().get_error_ref_url(None, "30006"))
