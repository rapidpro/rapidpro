from unittest.mock import MagicMock

from django.core.files import File
from django.urls import reverse

from temba.tests import TembaTest

from .models import Apk


class ApkTest(TembaTest):
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

    def test_read(self):
        apk_file_mock = MagicMock(spec=File)
        apk_file_mock.name = "relayer.apk"

        apk = Apk.objects.create(apk_type="R", name="Relayer", description="Relayer v1.0", apk_file=apk_file_mock)

        url = reverse("apks.apk_read", args=[apk.id])

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

    def test_download(self):
        apk_file_mock = MagicMock(spec=File)
        apk_file_mock.name = "relayer.apk"

        apk = Apk.objects.create(apk_type="R", name="Relayer", description="Relayer v1.0", apk_file=apk_file_mock)

        url = reverse("apks.apk_download", args=[apk.id])

        response = self.client.get(url, follow=True)
        self.assertEqual(response.request["PATH_INFO"], apk.apk_file.url)
