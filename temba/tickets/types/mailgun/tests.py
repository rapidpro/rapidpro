import re
from unittest.mock import patch

from django.test.utils import override_settings
from django.urls import reverse

from temba.tests import TembaTest

from ...models import Ticketer
from .type import MailgunType


class MailgunTypeTest(TembaTest):
    def test_is_available(self):
        with override_settings(MAILGUN_API_KEY=""):
            self.assertFalse(MailgunType().is_available())
        with override_settings(MAILGUN_API_KEY="1234567"):
            self.assertTrue(MailgunType().is_available())

    @override_settings(MAILGUN_API_KEY="1234567")
    def test_connect(self):
        connect_url = reverse("tickets.types.mailgun.connect")

        response = self.client.get(connect_url)
        self.assertLoginRedirect(response)

        self.login(self.admin)

        response = self.client.get(connect_url)

        self.assertEqual(["to_address", "loc"], list(response.context["form"].fields.keys()))

        # will fail as we don't have anything filled out
        response = self.client.post(connect_url, {})
        self.assertFormError(response, "form", "to_address", ["This field is required."])

        # submitting will send a verification email
        with patch("temba.utils.email.send_temba_email") as mock_send_email:
            response = self.client.post(connect_url, {"to_address": "bob@acme.com"})

            email_args = mock_send_email.call_args[0]
            self.assertEqual(email_args[0], "Verify your email address for tickets")

            # extract code from email body
            code = re.search(r"code is (\w+)", email_args[1]).group(1)

            self.assertEqual("/tickets/types/mailgun/connect?verify=true", response.url)

            # nothing yet saved...
            self.assertEqual(0, Ticketer.objects.count())

        step2_url = response.url

        response = self.client.get(step2_url)
        self.assertEqual(["verification_code", "loc"], list(response.context["form"].fields.keys()))

        # submit without code...
        response = self.client.post(step2_url, {})
        self.assertFormError(response, "form", "verification_code", ["This field is required."])

        # submit with wrong code
        response = self.client.post(step2_url, {"verification_code": "XYZ"})
        self.assertFormError(response, "form", "verification_code", ["Code does not match, please check your email."])

        # submit with correct code
        response = self.client.post(step2_url, {"verification_code": code})

        ticketer = Ticketer.objects.get()

        self.assertRedirect(response, f"/ticket/filter/{ticketer.uuid}/")
        self.assertEqual(
            {
                "domain": "tickets.rapidpro.io",
                "api_key": "1234567",
                "to_address": "bob@acme.com",
                "brand_name": "RapidPro",
                "url_base": "https://app.rapidpro.io",
            },
            ticketer.config,
        )

        # submit again after code has been cleared
        response = self.client.post(step2_url, {"verification_code": "12341"})
        self.assertFormError(response, "form", "verification_code", ["No verification code found, please start over."])
