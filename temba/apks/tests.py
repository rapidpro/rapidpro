from django.urls import reverse

from temba.tests import TembaTest


class ApkTest(TembaTest):
    def test_list(self):
        url = reverse("apks.apk_list")

        self.login(self.admin)
        response = self.client.get(url)
        self.assertLoginRedirect(response)

        self.login(self.superuser)
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)

    def test_create(self):
        url = reverse("apks.apk_create")

        self.login(self.admin)
        response = self.client.get(url)
        self.assertLoginRedirect(response)

        self.login(self.superuser)
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
