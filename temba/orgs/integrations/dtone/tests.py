from unittest.mock import patch

from django.urls import reverse

from temba.tests import MockResponse, TembaTest

from ...models import IntegrationType
from .client import DTOneClient


class DTOneTypeTest(TembaTest):
    @patch("temba.orgs.integrations.dtone.client.DTOneClient.get_balances")
    def test_account(self, mock_get_balances):
        self.assertFalse(self.org.get_integrations(IntegrationType.Category.AIRTIME))

        self.login(self.admin)

        account_url = reverse("integrations.dtone.account")
        home_url = reverse("orgs.org_workspace")

        response = self.client.get(home_url)
        self.assertContains(response, "Connect your DT One account.")

        # formax includes form to connect account
        response = self.client.get(account_url, HTTP_X_FORMAX=True)
        self.assertEqual(["api_key", "api_secret", "disconnect", "loc"], list(response.context["form"].fields.keys()))

        # simulate credentials being rejected
        mock_get_balances.side_effect = DTOneClient.Exception(errors=[{"code": 1000401, "message": "Unauthorized"}])

        response = self.client.post(account_url, {"api_key": "key123", "api_secret": "wrong", "disconnect": "false"})

        self.assertContains(response, "Your DT One API key and secret seem invalid.")
        self.assertFalse(self.org.get_integrations(IntegrationType.Category.AIRTIME))

        # simulate credentials being accepted
        mock_get_balances.side_effect = None
        mock_get_balances.return_value = [{"available": 10, "unit": "USD", "unit_type": "CURRENCY"}]

        response = self.client.post(account_url, {"api_key": "key123", "api_secret": "sesame", "disconnect": "false"})
        self.assertNoFormErrors(response)

        # account should now be connected
        self.org.refresh_from_db()
        self.assertTrue(self.org.get_integrations(IntegrationType.Category.AIRTIME))
        self.assertEqual("key123", self.org.config["dtone_key"])
        self.assertEqual("sesame", self.org.config["dtone_secret"])

        # and that stated on home page
        response = self.client.get(home_url)
        self.assertContains(response, "Connected to your DT One account.")
        self.assertContains(response, reverse("airtime.airtimetransfer_list"))

        # formax includes the disconnect link
        response = self.client.get(account_url, HTTP_X_FORMAX=True)
        self.assertContains(response, f"{account_url}?disconnect=true")

        # now disconnect
        response = self.client.post(account_url, {"api_key": "", "api_secret": "", "disconnect": "true"})
        self.assertNoFormErrors(response)

        self.org.refresh_from_db()
        self.assertFalse(self.org.get_integrations(IntegrationType.Category.AIRTIME))
        self.assertNotIn("dtone_key", self.org.config)
        self.assertNotIn("dtone_secret", self.org.config)


class DTOneClientTest(TembaTest):
    def setUp(self):
        super().setUp()

        self.client = DTOneClient("key123", "sesame")

    @patch("requests.get")
    def test_get_balances(self, mock_get):
        mock_get.return_value = MockResponse(
            200, '[{"available":0,"credit_limit":0,"holding": 0,"id":25849,"unit":"USD","unit_type":"CURRENCY"}]'
        )

        self.assertEqual(
            [{"available": 0, "credit_limit": 0, "holding": 0, "id": 25849, "unit": "USD", "unit_type": "CURRENCY"}],
            self.client.get_balances(),
        )
        mock_get.assert_called_once_with("https://dvs-api.dtone.com/v1/balances", auth=("key123", "sesame"))

        # simulate using wrong credentials
        mock_get.return_value = MockResponse(401, '{"errors": [{"code": 1000401, "message": "Unauthorized"}]}')

        with self.assertRaises(DTOneClient.Exception) as error:
            self.client.get_balances()

        self.assertEqual(str(error.exception), "Unauthorized")
