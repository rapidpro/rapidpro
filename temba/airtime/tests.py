from unittest.mock import patch

from django.urls import reverse

from temba.airtime.dtone import DTOneClient
from temba.airtime.models import AirtimeTransfer
from temba.tests import AnonymousOrg, CRUDLTestMixin, MockResponse, TembaTest


class AirtimeCRUDLTest(TembaTest, CRUDLTestMixin):
    def setUp(self):
        super().setUp()

        contact = self.create_contact("Ben Haggerty", phone="+250700000003")

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
            contact=self.create_contact("Frank", phone="+12065552021", org=self.org2),
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
