from unittest.mock import patch

from temba.tests import MockResponse, TembaTest

from .client import DTOneClient


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

        self.assertEqual(str(error.exception), f"Unauthorized")
