from django.urls import reverse

from temba.api.tests.mixins import APITestMixin
from temba.msgs.models import Media
from temba.tests import TembaTest


class APITest(APITestMixin, TembaTest):
    BASE_SESSION_QUERIES = 4  # number of queries required for any request using session auth
    BASE_TOKEN_QUERIES = 2  # number of queries required for any request using token auth

    def upload_media(self, user, filename: str):
        self.login(user)

        with open(filename, "rb") as data:
            response = self.client.post(
                reverse("api.v2.media") + ".json", {"file": data}, HTTP_X_FORWARDED_HTTPS="https"
            )
            self.assertEqual(201, response.status_code)

        return Media.objects.get(uuid=response.json()["uuid"])

    def assertResultsById(self, response, expected):
        self.assertEqual(response.status_code, 200)
        self.assertEqual([r["id"] for r in response.json()["results"]], [o.pk for o in expected])

    def assertResultsByUUID(self, response, expected):
        self.assertEqual(response.status_code, 200)
        self.assertEqual([r["uuid"] for r in response.json()["results"]], [str(o.uuid) for o in expected])

    def assert404(self, response):
        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.json(), {"detail": "Not found."})
