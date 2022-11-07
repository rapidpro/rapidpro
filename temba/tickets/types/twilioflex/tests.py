from unittest.mock import patch

from django.urls import reverse

from temba.tests import MockResponse
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
    def test_connect(self):
        self.client.force_login(self.admin)
        data = {
            "ticketer_name": "org support",
            "account_sid": "AC123456789",
            "auth_token": "123456789",
            "chat_service_sid": "SI123456789",
            "flex_flow_sid": "FO123456789",
            "workspace_sid": "WS123456789",
        }

        with patch("requests.get") as mock_get:
            mock_get.return_value = MockResponse(200, "{}")
            response = self.client.post(self.connect_url, data=data)
            self.assertEqual(response.status_code, 302)

            ticketer = Ticketer.objects.order_by("id").last()
            self.assertEqual(data["ticketer_name"], ticketer.name)

            self.assertRedirect(response, reverse("tickets.ticket_list"))
