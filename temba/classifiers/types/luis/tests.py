import json
from unittest.mock import patch

from requests.exceptions import RequestException

from django.urls import reverse

from temba.classifiers.models import Classifier
from temba.request_logs.models import HTTPLog
from temba.tests import MockResponse, TembaTest

from .client import AuthoringClient, PredictionClient
from .type import LuisType

GET_APP_RESPONSE = """{
    "id": "1dc41f72-b9d9-4a38-8999-5acf78e3d17e",
    "name": "Test",
    "description": "",
    "versionsCount": 1,
    "createdDateTime": "2021-08-04T19:46:10Z",
    "endpoints": {
        "PRODUCTION": {
            "versionId": "0.1",
            "publishedDateTime": "2021-08-04T20:41:47Z"
        }
    },
    "endpointHitsCount": 0,
    "activeVersion": "0.1"
}"""
GET_VERSION_INTENTS_RESPONSE = """[
    {"id": "75d2e81c-e441-45ac", "name": "Book Flight", "typeId": 0, "readableType": "Intent Classifier"},
    {"id": "3c8e22d9-4f1c-4add", "name": "Book Hotel", "typeId": 0, "readableType": "Intent Classifier"}
]"""


class AuthoringClientTest(TembaTest):
    @patch("requests.get")
    def test_get_app(self, mock_get):
        client = AuthoringClient("http://nyaruka-authoring.luis.api", "235252111")

        mock_get.return_value = MockResponse(400, '{"error": "yes"}')

        with self.assertRaises(RequestException):
            client.get_app("123456")

        self.assertEqual(1, len(client.logs))
        self.assertEqual("http://nyaruka-authoring.luis.apiluis/api/v2.0/apps/123456", client.logs[0]["url"])

        mock_get.return_value = MockResponse(200, GET_APP_RESPONSE)
        app_info = client.get_app("123456")

        self.assertEqual(json.loads(GET_APP_RESPONSE), app_info)
        self.assertEqual(2, len(client.logs))

    @patch("requests.get")
    def test_get_version_intents(self, mock_get):
        client = AuthoringClient("http://nyaruka-authoring.luis.api", "235252111")

        mock_get.return_value = MockResponse(400, '{"error": "yes"}')

        with self.assertRaises(RequestException):
            client.get_version_intents("123456", "0.1")

        self.assertEqual(1, len(client.logs))
        self.assertEqual(
            "http://nyaruka-authoring.luis.apiluis/api/v2.0/apps/123456/versions/0.1/intents", client.logs[0]["url"]
        )

        mock_get.return_value = MockResponse(200, GET_VERSION_INTENTS_RESPONSE)
        app_info = client.get_version_intents("123456", "0.1")

        self.assertEqual(json.loads(GET_VERSION_INTENTS_RESPONSE), app_info)
        self.assertEqual(2, len(client.logs))


class PredictionClientTest(TembaTest):
    @patch("requests.get")
    def test_predict(self, mock_get):
        client = PredictionClient("http://nyaruka.luis.api", "235252111")

        mock_get.return_value = MockResponse(400, '{"error": "yes"}')

        with self.assertRaises(RequestException):
            client.predict("123456", "production", "hello")

        mock_get.return_value = MockResponse(200, """{"query": "Hello"}""")
        mock_get.reset_mock()

        client.predict("123456", "production", "hello")

        mock_get.assert_called_once_with(
            "http://nyaruka.luis.apiluis/prediction/v3.0/apps/123456/slots/production/predict?query=hello&subscription-key=235252111"
        )


class LuisTypeTest(TembaTest):
    @patch("requests.get")
    def test_sync(self, mock_get):
        # create classifier but don't sync the intents
        c = Classifier.create(
            self.org,
            self.user,
            LuisType.slug,
            "Booker",
            {
                LuisType.CONFIG_APP_ID: "1dc41f72-b9d9-4a38-8999-5acf78e3d17e",
                LuisType.CONFIG_AUTHORING_ENDPOINT: "http://nyaruka-authoring.luis.api",
                LuisType.CONFIG_AUTHORING_KEY: "sesame",
                LuisType.CONFIG_PREDICTION_ENDPOINT: "http://nyaruka.luis.api",
                LuisType.CONFIG_PREDICTION_KEY: "sesame",
                LuisType.CONFIG_SLOT: "production",
            },
            sync=False,
        )

        mock_get.side_effect = [MockResponse(400, '{"error": "yes"}')]

        c.get_type().get_active_intents_from_api(c)
        self.assertEqual(HTTPLog.objects.filter(classifier=c).count(), 1)

        mock_get.side_effect = [MockResponse(100, "")]

        c.get_type().get_active_intents_from_api(c)
        self.assertEqual(HTTPLog.objects.filter(classifier=c).count(), 2)

        mock_get.side_effect = [
            MockResponse(200, GET_APP_RESPONSE),
            MockResponse(200, GET_VERSION_INTENTS_RESPONSE),
        ]

        intents = c.get_type().get_active_intents_from_api(c)

        self.assertEqual(HTTPLog.objects.filter(classifier=c).count(), 4)
        self.assertEqual(2, len(intents))
        self.assertEqual("Book Flight", intents[0].name)
        self.assertEqual("75d2e81c-e441-45ac", intents[0].external_id)

    @patch("temba.classifiers.types.luis.views.AuthoringClient.get_app")
    @patch("temba.classifiers.types.luis.views.AuthoringClient.get_version_intents")
    @patch("temba.classifiers.types.luis.views.PredictionClient.predict")
    @patch("socket.gethostbyname")
    def test_connect(self, mock_get_host, mock_predict, mock_get_version_intents, mock_get_app):
        mock_get_host.return_value = "192.55.123.1"

        connect_url = reverse("classifiers.classifier_connect")
        response = self.client.get(connect_url)
        self.assertLoginRedirect(response)

        self.login(self.admin)
        response = self.client.get(connect_url)

        # should have url for claiming our type
        luis_url = reverse("classifiers.types.luis.connect")
        self.assertContains(response, luis_url)

        # will fail as we don't have anything filled out
        response = self.client.post(luis_url, {})
        self.assertFormError(response, "form", "name", ["This field is required."])
        self.assertFormError(response, "form", "app_id", ["This field is required."])
        self.assertFormError(response, "form", "authoring_endpoint", ["This field is required."])
        self.assertFormError(response, "form", "authoring_key", ["This field is required."])
        self.assertFormError(response, "form", "prediction_endpoint", ["This field is required."])
        self.assertFormError(response, "form", "prediction_key", ["This field is required."])
        self.assertFormError(response, "form", "slot", ["This field is required."])

        # simulate wrong authoring credentials
        mock_get_app.side_effect = RequestException(
            "Not authorized", response=MockResponse(401, '{ "error": "true" }')
        )

        response = self.client.post(
            luis_url,
            {
                "name": "Booker",
                "app_id": "12345",
                "authoring_endpoint": "http://nyaruka-authoring.luis.api",
                "authoring_key": "325253",
                "prediction_endpoint": "http://nyaruka.luis.api",
                "prediction_key": "456373",
                "slot": "staging",
            },
        )
        self.assertFormError(response, "form", "__all__", "Check authoring credentials: Not authorized")

        # simulate selected slot isn't published
        mock_get_app.side_effect = None
        mock_get_app.return_value = json.loads(GET_APP_RESPONSE)

        response = self.client.post(
            luis_url,
            {
                "name": "Booker",
                "app_id": "12345",
                "authoring_endpoint": "http://nyaruka-authoring.luis.api",
                "authoring_key": "325253",
                "prediction_endpoint": "http://nyaruka.luis.api",
                "prediction_key": "456373",
                "slot": "staging",
            },
        )
        self.assertFormError(response, "form", "__all__", "App has not yet been published to staging slot.")

        # simulate wrong prediction credentials
        mock_predict.side_effect = RequestException(
            "Not authorized", response=MockResponse(401, '{ "error": "true" }')
        )

        response = self.client.post(
            luis_url,
            {
                "name": "Booker",
                "app_id": "12345",
                "authoring_endpoint": "http://nyaruka-authoring.luis.api",
                "authoring_key": "325253",
                "prediction_endpoint": "http://nyaruka.luis.api",
                "prediction_key": "456373",
                "slot": "production",
            },
        )
        self.assertFormError(response, "form", "__all__", "Check prediction credentials: Not authorized")

        mock_get_version_intents.return_value = json.loads(GET_VERSION_INTENTS_RESPONSE)
        mock_predict.side_effect = None

        response = self.client.post(
            luis_url,
            {
                "name": "Booker",
                "app_id": "12345",
                "authoring_endpoint": "http://nyaruka-authoring.luis.api",
                "authoring_key": "325253",
                "prediction_endpoint": "http://nyaruka.luis.api",
                "prediction_key": "456373",
                "slot": "production",
            },
        )
        self.assertEqual(302, response.status_code)

        c = Classifier.objects.get()
        self.assertEqual("Booker", c.name)
        self.assertEqual("luis", c.classifier_type)
        self.assertEqual(
            {
                "app_id": "12345",
                "authoring_endpoint": "http://nyaruka-authoring.luis.api",
                "authoring_key": "325253",
                "prediction_endpoint": "http://nyaruka.luis.api",
                "prediction_key": "456373",
                "slot": "production",
            },
            c.config,
        )

        self.assertEqual(2, c.intents.all().count())
