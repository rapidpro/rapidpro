from unittest.mock import patch

from requests import RequestException

from django.urls import reverse

from temba.classifiers.models import Classifier
from temba.request_logs.models import HTTPLog
from temba.tests import MockResponse, TembaTest

from .type import LuisType

INTENT_RESPONSE = """
[
  {
    "id": "b1a3c0ad-e912-4b55-a62e-6fcb77751bc5",
    "name": "Book Car",
    "typeId": 0,
    "readableType": "Intent Classifier"
  },
  {
    "id": "326d5197-2161-4daa-aa32-c42c7000c82c",
    "name": "Book Hotel",
    "typeId": 0,
    "readableType": "Intent Classifier"
  }
]
"""


class LuisTypeTest(TembaTest):
    def test_sync(self):
        # create classifier but don't sync the intents
        c = Classifier.create(
            self.org,
            self.user,
            LuisType.slug,
            "Booker",
            {
                LuisType.CONFIG_APP_ID: "12345",
                LuisType.CONFIG_PRIMARY_KEY: "sesame",
                LuisType.CONFIG_ENDPOINT_URL: "http://luis.api",
                LuisType.CONFIG_VERSION: "0.1",
            },
            sync=False,
        )

        with patch("requests.get") as mock_get:
            mock_get.return_value = MockResponse(400, '{ "error": "true" }')

            c.get_type().get_active_intents_from_api(c)
            self.assertEqual(HTTPLog.objects.filter(classifier=c).count(), 1)

            mock_get.side_effect = RequestException("Network is unreachable", response=MockResponse(100, ""))
            c.get_type().get_active_intents_from_api(c)
            self.assertEqual(HTTPLog.objects.filter(classifier=c).count(), 2)

        with patch("requests.get") as mock_get:
            mock_get.return_value = MockResponse(200, INTENT_RESPONSE)
            intents = c.get_type().get_active_intents_from_api(c)

            self.assertEqual(HTTPLog.objects.filter(classifier=c).count(), 3)
            self.assertEqual(2, len(intents))
            car = intents[0]
            self.assertEqual("Book Car", car.name)
            self.assertEqual("b1a3c0ad-e912-4b55-a62e-6fcb77751bc5", car.external_id)

    def test_connect(self):
        url = reverse("classifiers.classifier_connect")
        response = self.client.get(url)
        self.assertRedirect(response, "/users/login/")

        self.login(self.admin)
        response = self.client.get(url)

        # should have url for claiming our type
        url = reverse("classifiers.types.luis.connect")
        self.assertContains(response, url)

        response = self.client.get(url)
        post_data = response.context["form"].initial

        # will fail as we don't have anything filled out
        response = self.client.post(url, post_data)
        self.assertFormError(response, "form", "app_id", ["This field is required."])

        post_data["name"] = "Booker"
        post_data["app_id"] = "12345"
        post_data["version"] = "0.1"
        post_data["primary_key"] = "sesame"
        post_data["endpoint_url"] = "http://nyaruka.com/luis"

        # can't connect
        with patch("requests.get") as mock_get:
            mock_get.return_value = MockResponse(400, '{ "error": "true" }')
            response = self.client.post(url, post_data)
            self.assertEqual(200, response.status_code)
            self.assertFalse(Classifier.objects.all())

            self.assertContains(response, "Unable to get intents for your app")

        # all good
        with patch("requests.get") as mock_get:
            mock_get.side_effect = [MockResponse(200, '{ "error": "false" }'), MockResponse(200, INTENT_RESPONSE)]
            response = self.client.post(url, post_data)
            self.assertEqual(302, response.status_code)

            c = Classifier.objects.get()
            self.assertEqual("Booker", c.name)
            self.assertEqual("luis", c.classifier_type)
            self.assertEqual("sesame", c.config[LuisType.CONFIG_PRIMARY_KEY])
            self.assertEqual("http://nyaruka.com/luis", c.config[LuisType.CONFIG_ENDPOINT_URL])
            self.assertEqual("0.1", c.config[LuisType.CONFIG_VERSION])

            self.assertEqual(2, c.intents.all().count())
