from unittest.mock import MagicMock

from django.core.files import File
from django.urls import reverse

from temba.tests import TembaTest

from .models import Apk


class ApkTest(TembaTest):
    def setUp(self):
        super().setUp()
        apk_file_mock = MagicMock(spec=File)
        apk_file_mock.name = "relayer.apk"

        self.apk = Apk.objects.create(
            apk_type="R", version="1.0", description="* has new things", apk_file=apk_file_mock
        )

    def tearDown(self):
        self.clear_storage()

    def test_claim_android(self):
        self.login(self.admin)
        response = self.client.get(reverse("channels.channel_claim_android"))
        self.assertContains(response, "<li>has new things</li>")

    def test_list(self):
        url = reverse("apks.apk_list")

        self.login(self.admin)
        response = self.client.get(url)
        self.assertLoginRedirect(response)

        self.login(self.customer_support)
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)

        self.login(self.superuser)
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Relayer Application APK")

    def test_create(self):
        url = reverse("apks.apk_create")

        self.login(self.admin)
        response = self.client.get(url)
        self.assertLoginRedirect(response)

        self.login(self.customer_support)
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)

        self.login(self.superuser)
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)

    def test_update(self):
        url = reverse("apks.apk_update", args=[self.apk.id])

        response = self.client.get(url)
        self.assertLoginRedirect(response)

        self.login(self.admin)
        response = self.client.get(url)
        self.assertLoginRedirect(response)

        self.login(self.customer_support)
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)

        self.login(self.superuser)
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Relayer Application APK")
