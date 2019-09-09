from django.urls import reverse

from temba.airtime.models import AirtimeTransfer
from temba.tests import TembaTest


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
