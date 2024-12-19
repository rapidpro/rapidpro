from unittest.mock import patch

from django.conf import settings
from django.urls import reverse

from temba.tests import CRUDLTestMixin, TembaTest, mock_uuids


class MediaCRUDLTest(CRUDLTestMixin, TembaTest):
    @mock_uuids
    def test_upload(self):
        upload_url = reverse("msgs.media_upload")

        def assert_upload(user, filename, expected_json):
            self.login(user)

            response = self.client.get(upload_url)
            self.assertEqual(response.status_code, 405)

            with open(filename, "rb") as data:
                response = self.client.post(upload_url, {"file": data}, HTTP_X_FORWARDED_HTTPS="https")

            self.assertEqual(response.status_code, 200)
            self.assertEqual(expected_json, response.json())

        assert_upload(
            self.admin,
            f"{settings.MEDIA_ROOT}/test_media/steve marten.jpg",
            {
                "uuid": "b97f69f7-5edf-45c7-9fda-d37066eae91d",
                "content_type": "image/jpeg",
                "type": "image/jpeg",
                "url": f"{settings.STORAGE_URL}/orgs/{self.org.id}/media/b97f/b97f69f7-5edf-45c7-9fda-d37066eae91d/steve%20marten.jpg",
                "name": "steve marten.jpg",
                "size": 7461,
            },
        )
        assert_upload(
            self.editor,
            f"{settings.MEDIA_ROOT}/test_media/snow.mp4",
            {
                "uuid": "14f6ea01-456b-4417-b0b8-35e942f549f1",
                "content_type": "video/mp4",
                "type": "video/mp4",
                "url": f"{settings.STORAGE_URL}/orgs/{self.org.id}/media/14f6/14f6ea01-456b-4417-b0b8-35e942f549f1/snow.mp4",
                "name": "snow.mp4",
                "size": 684558,
            },
        )
        assert_upload(
            self.editor,
            f"{settings.MEDIA_ROOT}/test_media/bubbles.m4a",
            {
                "uuid": "9295ebab-5c2d-4eb1-86f9-7c15ed2f3219",
                "content_type": "audio/mp4",
                "type": "audio/mp4",
                "url": f"{settings.STORAGE_URL}/orgs/{self.org.id}/media/9295/9295ebab-5c2d-4eb1-86f9-7c15ed2f3219/bubbles.m4a",
                "name": "bubbles.m4a",
                "size": 46468,
            },
        )
        with open(f"{settings.MEDIA_ROOT}/test_media/fake_jpg_svg_pencil.jpg", "rb") as data:
            response = self.client.post(upload_url, {"file": data}, HTTP_X_FORWARDED_HTTPS="https")
            self.assertEqual({"error": "Unsupported file type"}, response.json())

        # error message if you upload something unsupported
        with open(f"{settings.MEDIA_ROOT}/test_imports/simple.xlsx", "rb") as data:
            response = self.client.post(upload_url, {"file": data}, HTTP_X_FORWARDED_HTTPS="https")
            self.assertEqual({"error": "Unsupported file type"}, response.json())

        with open(f"{settings.MEDIA_ROOT}/test_media/pencil.svg", "rb") as data:
            response = self.client.post(upload_url, {"file": data}, HTTP_X_FORWARDED_HTTPS="https")
            self.assertEqual({"error": "Unsupported file type"}, response.json())

        # error message if upload is too big
        with patch("temba.msgs.models.Media.MAX_UPLOAD_SIZE", 1024):
            with open(f"{settings.MEDIA_ROOT}/test_media/snow.mp4", "rb") as data:
                response = self.client.post(upload_url, {"file": data}, HTTP_X_FORWARDED_HTTPS="https")
                self.assertEqual({"error": "Limit for file uploads is 0.0009765625 MB"}, response.json())

    def test_list(self):
        upload_url = reverse("msgs.media_upload")
        list_url = reverse("msgs.media_list")

        def upload(user, path):
            self.login(user)

            with open(path, "rb") as data:
                self.client.post(upload_url, {"file": data}, HTTP_X_FORWARDED_HTTPS="https")
                return self.org.media.filter(original=None).order_by("id").last()

        media1 = upload(self.admin, f"{settings.MEDIA_ROOT}/test_media/steve marten.jpg")
        media2 = upload(self.admin, f"{settings.MEDIA_ROOT}/test_media/bubbles.m4a")
        upload(self.admin2, f"{settings.MEDIA_ROOT}/test_media/bubbles.m4a")  # other org

        self.login(self.customer_support, choose_org=self.org)
        response = self.client.get(list_url)
        self.assertEqual([media2, media1], list(response.context["object_list"]))
