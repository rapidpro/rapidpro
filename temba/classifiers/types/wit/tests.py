from unittest.mock import patch

from requests import RequestException

from django.urls import reverse

from temba.classifiers.models import Classifier
from temba.request_logs.models import HTTPLog
from temba.tests import MockResponse, TembaTest

from .client import Client
from .type import WitType

INTENT_RESPONSE = """
[
    {
        "id": "754569408690533",
        "name": "book_car"
    },
    {
        "id": "754569408690020",
        "name": "book_flight"
    }
]
"""


class ClientTest(TembaTest):
    @patch("requests.get")
    def test_get_intents(self, mock_get):
        mock_get.return_value = MockResponse(200, "[]")
        client = Client("sesame")
        intents, response = client.get_intents()

        self.assertEqual([], intents)
        self.assertEqual(200, response.status_code)
        mock_get.assert_called_once_with(
            "https://api.wit.ai/intents?v=20200513", headers={"Authorization": "Bearer sesame"}
        )


class WitTypeTest(TembaTest):
    def test_sync(self):
        # create classifier but don't sync the intents
        c = Classifier.create(
            self.org,
            self.user,
            WitType.slug,
            "Booker",
            {WitType.CONFIG_APP_ID: "12345", WitType.CONFIG_ACCESS_TOKEN: "sesame"},
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
            self.assertEqual("book_car", car.name)
            self.assertEqual("754569408690533", car.external_id)

    def test_delete(self):
        c = Classifier.create(
            self.org,
            self.user,
            WitType.slug,
            "Booker",
            {WitType.CONFIG_APP_ID: "12345", WitType.CONFIG_ACCESS_TOKEN: "sesame"},
        )

        # delete the classifier
        url = reverse("classifiers.classifier_delete", args=[c.uuid])
        response = self.client.post(url)
        self.assertRedirect(response, "/users/login/")

        self.login(self.admin)
        response = self.client.post(url)

        c.refresh_from_db()
        self.assertFalse(c.is_active)

        # reactivate
        c.is_active = True
        c.save()

        # add a dependency and try again
        flow = self.create_flow()
        flow.classifier_dependencies.add(c)

        with self.assertRaises(ValueError):
            self.client.post(url)

        c.refresh_from_db()
        self.assertTrue(c.is_active)

    def test_connect(self):
        url = reverse("classifiers.classifier_connect")
        response = self.client.get(url)
        self.assertRedirect(response, "/users/login/")

        self.login(self.admin)
        response = self.client.get(url)

        # should have url for claiming our type
        url = reverse("classifiers.types.wit.connect")
        self.assertContains(response, url)

        response = self.client.get(url)
        post_data = response.context["form"].initial

        # will fail as we don't have anything filled out
        response = self.client.post(url, post_data)
        self.assertFormError(response, "form", "app_id", ["This field is required."])

        # ok, will everything out
        post_data["name"] = "Booker"
        post_data["app_id"] = "12345"
        post_data["access_token"] = "sesame"

        # can't connect
        with patch("requests.get") as mock_get:
            mock_get.return_value = MockResponse(400, '{ "error": "true" }')
            response = self.client.post(url, post_data)

            self.assertEqual(200, response.status_code)
            self.assertFalse(Classifier.objects.all())
            self.assertContains(response, "Unable to access wit.ai with credentials")

        # all good
        with patch("requests.get") as mock_get:
            mock_get.side_effect = [
                MockResponse(200, '["intent", "wit$age_of_person"]'),
                MockResponse(200, INTENT_RESPONSE),
            ]

            response = self.client.post(url, post_data)
            self.assertEqual(302, response.status_code)
            c = Classifier.objects.get()
            self.assertEqual("Booker", c.name)
            self.assertEqual("wit", c.classifier_type)
            self.assertEqual("sesame", c.config[WitType.CONFIG_ACCESS_TOKEN])
            self.assertEqual("12345", c.config[WitType.CONFIG_APP_ID])

            # should have intents too
            self.assertEqual(2, c.intents.all().count())
