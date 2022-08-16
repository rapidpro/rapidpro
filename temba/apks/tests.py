from unittest.mock import MagicMock

from django.core.files import File
from django.urls import reverse

from temba.tests import CRUDLTestMixin, TembaTest

from .models import Apk


class ApkCRUDLTest(CRUDLTestMixin, TembaTest):
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
        response = self.client.get(reverse("channels.types.android.claim"))
        self.assertContains(response, "<li>has new things</li>")

    def test_list(self):
        list_url = reverse("apks.apk_list")

        response = self.assertStaffOnly(list_url)

        self.assertContains(response, "Relayer Application APK")

    def test_create(self):
        create_url = reverse("apks.apk_create")

        self.assertStaffOnly(create_url)

    def test_update(self):
        update_url = reverse("apks.apk_update", args=[self.apk.id])

        response = self.assertStaffOnly(update_url)

        self.assertContains(response, "Relayer Application APK")
