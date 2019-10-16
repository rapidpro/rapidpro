from unittest.mock import patch

from django.urls import reverse

from temba.airtime.dtone import DTOneClient
from temba.airtime.models import AirtimeTransfer
from temba.tests import MockResponse, TembaTest


class AirtimeCRUDLTest(TembaTest):
    def setUp(self):
        super().setUp()

        self.contact = self.create_contact("Ben Haggerty", "+12065552020")
        self.airtime = AirtimeTransfer.objects.create(
            org=self.org,
            recipient="+12065552020",
            amount="100",
            contact=self.contact,
            created_by=self.admin,
            modified_by=self.admin,
        )

    def test_list(self):
        list_url = reverse("airtime.airtimetransfer_list")

        self.login(self.user)
        response = self.client.get(list_url)
        self.assertRedirect(response, "/users/login/")

        self.login(self.editor)
        response = self.client.get(list_url)
        self.assertEqual(200, response.status_code)
        self.assertIn(self.airtime, response.context["object_list"])

        self.login(self.admin)
        response = self.client.get(list_url)
        self.assertEqual(200, response.status_code)
        self.assertIn(self.airtime, response.context["object_list"])

    def test_read(self):
        read_url = reverse("airtime.airtimetransfer_read", args=[self.airtime.id])

        self.login(self.user)
        response = self.client.get(read_url)
        self.assertRedirect(response, "/users/login/")

        self.login(self.editor)
        response = self.client.get(read_url)
        self.assertEqual(200, response.status_code)
        self.assertEqual(self.airtime, response.context["object"])

        self.login(self.admin)
        response = self.client.get(read_url)
        self.assertEqual(200, response.status_code)
        self.assertEqual(self.airtime, response.context["object"])


class DTOneClientTest(TembaTest):
    def setUp(self):
        super().setUp()

        self.client = DTOneClient("mrrapid", "234325")

    @patch("temba.airtime.dtone.DTOneClient._request_key")
    @patch("requests.post")
    def test_ping(self, mock_post, mock_request_key):
        mock_request_key.return_value = "123456"
        mock_post.return_value = MockResponse(
            200,
            "info_txt=pong\r\n"
            "authentication_key=123456\r\n"
            "error_code=0\r\n"
            "error_txt=Transaction successful\r\n",
        )

        response = self.client.ping()

        self.assertEqual(
            {
                "authentication_key": "123456",
                "error_code": "0",
                "error_txt": "Transaction successful",
                "info_txt": "pong",
            },
            response,
        )
        mock_post.assert_called_once_with(
            "https://airtime-api.dtone.com/cgi-bin/shop/topup",
            {"login": "mrrapid", "key": "123456", "md5": "4ff2ddfae96f8d902eb7d5b2c7b490c9", "action": "ping"},
        )

    @patch("temba.airtime.dtone.DTOneClient._request_key")
    @patch("requests.post")
    def test_check_wallet(self, mock_post, mock_request_key):
        mock_request_key.return_value = "123456"
        mock_post.return_value = MockResponse(
            200,
            "type=Master\r\n"
            "authentication_key=123456\r\n"
            "error_code=0\r\n"
            "error_txt=Transaction successful\r\n"
            "balance=15000\r\n"
            "currency=RWF\r\n",
        )

        response = self.client.check_wallet()

        self.assertEqual(
            {
                "type": "Master",
                "authentication_key": "123456",
                "error_code": "0",
                "error_txt": "Transaction successful",
                "balance": "15000",
                "currency": "RWF",
            },
            response,
        )
        mock_post.assert_called_once_with(
            "https://airtime-api.dtone.com/cgi-bin/shop/topup",
            {"login": "mrrapid", "key": "123456", "md5": "4ff2ddfae96f8d902eb7d5b2c7b490c9", "action": "check_wallet"},
        )
