from django.core.files.uploadedfile import SimpleUploadedFile
from django.urls import reverse

from temba.tests import CRUDLTestMixin, TembaTest

from .models import Apk


class ApkCRUDLTest(CRUDLTestMixin, TembaTest):
    def setUp(self):
        super().setUp()

        self.apk = Apk.objects.create(
            apk_type="R",
            version="1.0",
            description="* has new things",
            apk_file=SimpleUploadedFile(
                "relayer.apk", content=b"APKDATA", content_type="application/vnd.android.package-archive"
            ),
        )

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
