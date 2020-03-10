from unittest.mock import patch

from django.urls import reverse

from temba.airtime.dtone import DTOneClient
from temba.airtime.models import AirtimeTransfer
from temba.tests import AnonymousOrg, CRUDLTestMixin, MigrationTest, MockResponse, TembaTest


class AirtimeCRUDLTest(TembaTest, CRUDLTestMixin):
    def setUp(self):
        super().setUp()

        contact = self.create_contact("Ben Haggerty", "+250700000003")

        self.transfer1 = AirtimeTransfer.objects.create(
            org=self.org,
            status=AirtimeTransfer.STATUS_SUCCESS,
            contact=contact,
            recipient="tel:+250700000003",
            currency="RWF",
            desired_amount="1100",
            actual_amount="1000",
        )
        self.transfer2 = AirtimeTransfer.objects.create(
            org=self.org,
            status=AirtimeTransfer.STATUS_FAILED,
            sender="tel:+250700000002",
            contact=contact,
            recipient="tel:+250700000003",
            currency="USD",
            desired_amount="1100",
            actual_amount="0",
        )

        # and a transfer for a different org
        self.other_org_transfer = AirtimeTransfer.objects.create(
            org=self.org2,
            status=AirtimeTransfer.STATUS_SUCCESS,
            contact=self.create_contact("Frank", "+12065552021", org=self.org2),
            recipient="tel:+12065552021",
            currency="USD",
            desired_amount="1",
            actual_amount="1",
        )

    def test_list(self):
        list_url = reverse("airtime.airtimetransfer_list")

        response = self.assertListFetch(
            list_url, allow_viewers=False, allow_editors=True, context_objects=[self.transfer2, self.transfer1]
        )
        self.assertContains(response, "Ben Haggerty")
        self.assertContains(response, "+250 700 000 003")

        with AnonymousOrg(self.org):
            response = self.requestView(list_url, self.admin)

            self.assertContains(response, "Ben Haggerty")
            self.assertNotContains(response, "+250 700 000 003")

    def test_read(self):
        read_url = reverse("airtime.airtimetransfer_read", args=[self.transfer1.id])

        response = self.assertReadFetch(
            read_url, allow_viewers=False, allow_editors=True, context_object=self.transfer1
        )
        self.assertContains(response, "Ben Haggerty")
        self.assertContains(response, "+250 700 000 003")
        self.assertTrue(response.context["show_logs"])

        with AnonymousOrg(self.org):
            response = self.requestView(read_url, self.admin)

            self.assertContains(response, "Ben Haggerty")
            self.assertNotContains(response, "+250 700 000 003")
            self.assertFalse(response.context["show_logs"])


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


class RecipientsToURNsMigrationTest(MigrationTest):
    app = "airtime"
    migrate_from = "0012_auto_20191015_1704"
    migrate_to = "0013_recipient_to_urn"

    def setUpBeforeMigration(self, apps):
        contact = self.create_contact("Ben Haggerty", "+250700000003")

        self.transfer1 = AirtimeTransfer.objects.create(
            org=self.org,
            status=AirtimeTransfer.STATUS_SUCCESS,
            contact=contact,
            recipient="+250700000003",
            currency="RWF",
            desired_amount="1100",
            actual_amount="1000",
        )
        self.transfer2 = AirtimeTransfer.objects.create(
            org=self.org,
            status=AirtimeTransfer.STATUS_SUCCESS,
            contact=contact,
            recipient="+250700000004",
            currency="RWF",
            desired_amount="1100",
            actual_amount="1000",
        )
        self.transfer3 = AirtimeTransfer.objects.create(
            org=self.org,
            status=AirtimeTransfer.STATUS_SUCCESS,
            contact=contact,
            recipient="tel:+250700000005",
            currency="RWF",
            desired_amount="1100",
            actual_amount="1000",
        )

    def test_migration(self):
        self.transfer1.refresh_from_db()
        self.transfer2.refresh_from_db()
        self.transfer3.refresh_from_db()

        self.assertEqual("tel:+250700000003", self.transfer1.recipient)
        self.assertEqual("tel:+250700000004", self.transfer2.recipient)
        self.assertEqual("tel:+250700000005", self.transfer3.recipient)
