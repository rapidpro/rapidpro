from unittest.mock import patch

from django.conf import settings
from django.urls import reverse

from temba.msgs.models import Media
from temba.tests import mock_uuids

from . import APITest


class MediaEndpointTest(APITest):
    @mock_uuids
    def test_endpoint(self):
        endpoint_url = reverse("api.v2.media") + ".json"

        self.assertGetNotAllowed(endpoint_url)
        self.assertPostNotPermitted(endpoint_url, [None, self.user])
        self.assertDeleteNotAllowed(endpoint_url)

        def upload(user, filename: str):
            self.login(user)
            with open(filename, "rb") as data:
                return self.client.post(endpoint_url, {"file": data}, HTTP_X_FORWARDED_HTTPS="https")

        self.login(self.admin)
        response = self.client.post(endpoint_url, {}, HTTP_X_FORWARDED_HTTPS="https")
        self.assertResponseError(response, "file", "No file was submitted.")

        response = upload(self.agent, f"{settings.MEDIA_ROOT}/test_imports/simple.xlsx")
        self.assertResponseError(response, "file", "Unsupported file type.")

        with patch("temba.msgs.models.Media.MAX_UPLOAD_SIZE", 1024):
            response = upload(self.editor, f"{settings.MEDIA_ROOT}/test_media/snow.mp4")
            self.assertResponseError(response, "file", "Limit for file uploads is 0.0009765625 MB.")

        response = upload(self.admin, f"{settings.MEDIA_ROOT}/test_media/steve marten.jpg")
        self.assertEqual(201, response.status_code)
        self.assertEqual(
            {
                "uuid": "b97f69f7-5edf-45c7-9fda-d37066eae91d",
                "content_type": "image/jpeg",
                "url": f"{settings.STORAGE_URL}/orgs/{self.org.id}/media/b97f/b97f69f7-5edf-45c7-9fda-d37066eae91d/steve%20marten.jpg",
                "filename": "steve marten.jpg",
                "size": 7461,
            },
            response.json(),
        )

        media = Media.objects.get()
        self.assertEqual(Media.STATUS_READY, media.status)
