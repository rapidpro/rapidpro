from unittest.mock import patch

from requests.exceptions import Timeout

from django.urls import reverse

from temba.tests.base import TembaTest
from temba.tickets.models import Ticketer

from .type import TwilioFlexType


class TwilioFlexTypeTest(TembaTest):
    def test_is_available_to(self):
        self.assertTrue(TwilioFlexType().is_available_to(self.admin))


class TwilioflexMixin(TembaTest):
    def setUp(self):
        super().setUp()
        self.connect_url = reverse("tickets.types.twilioflex.connect")


class TwilioflexViewTest(TwilioflexMixin):
    def check_exceptions(self, mock_choices, mock_request, timeout_msg, exception_msg):
        self.client.force_login(self.admin)
        check = [(Timeout(), timeout_msg), (Exception(), exception_msg)]
        for err, msg in check:

            def side_effect(*arg, **kwargs):
                raise err

            mock_request.side_effect = side_effect
            data = {
                "account_sid": "AC123456789",
                "auth_token": "123456789",
                "chat_service_sid": "SI123456789",
                "flex_flow_sid": "FO123456789",
                "workspace_sid": "WS123456789",
            }
            response = self.client.post(self.connect_url, data=data)
            self.assertEqual(response.status_code, 200)
            self.assertEqual(len(response.context["messages"]), 1)
            self.assertEqual([f"{m}" for m in response.context["messages"]][0], msg)

    @patch("random.choice")
    def test_form_valid(self, mock_choices):
        self.client.force_login(self.admin)
        data = {
            "account_sid": "AC123456789",
            "auth_token": "123456789",
            "chat_service_sid": "SI123456789",
            "flex_flow_sid": "FO123456789",
            "workspace_sid": "WS123456789",
        }
        response = self.client.post(self.connect_url, data=data)

        self.assertEqual(response.status_code, 302)

        ticketer = Ticketer.objects.order_by("id").last()
        self.assertEqual("Twilio Flex", ticketer.name)

        self.assertRedirect(response, reverse("tickets.ticket_list"))
