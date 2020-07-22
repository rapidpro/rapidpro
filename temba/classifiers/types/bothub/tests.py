from unittest.mock import patch

from requests import RequestException

from django.contrib.auth.models import Group
from django.urls import reverse

from temba.classifiers.models import Classifier
from temba.request_logs.models import HTTPLog
from temba.tests import MockResponse, TembaTest

from .type import BothubType

INTENT_RESPONSE = """
{
    "intents": [
        "intent",
        "positive",
        "negative"
    ]
}
"""


class BothubTypeTest(TembaTest):
    def test_sync(self):
        # create classifier but don't sync the intents
        c = Classifier.create(
            self.org,
            self.user,
            BothubType.slug,
            "Booker",
            {BothubType.CONFIG_ACCESS_TOKEN: "123456789", BothubType.INTENT_URL: "https://nlp.bothub.it/info/"},
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
            self.assertEqual(3, len(intents))
            intent = intents[0]
            self.assertEqual("intent", intent.name)
            self.assertEqual("intent", intent.external_id)

    def test_connect(self):
        # add admin to beta group
        self.admin.groups.add(Group.objects.get(name="Beta"))

        url = reverse("classifiers.classifier_connect")
        response = self.client.get(url)
        self.assertRedirect(response, "/users/login/")

        self.login(self.admin)
        response = self.client.get(url)

        # should have url for claiming our type
        url = reverse("classifiers.types.bothub.connect")
        self.assertContains(response, url)

        response = self.client.get(url)
        post_data = response.context["form"].initial

        # will fail as we don't have anything filled out
        response = self.client.post(url, post_data)
        self.assertFormError(response, "form", "name", ["This field is required."])

        # ok, will everything out
        post_data["name"] = "Bothub Test Repository"
        post_data["access_token"] = "123456789"

        # can't connect
        with patch("requests.get") as mock_get:
            mock_get.return_value = MockResponse(400, '{ "error": "true" }')
            response = self.client.post(url, post_data)
            self.assertEqual(200, response.status_code)
            self.assertFalse(Classifier.objects.all())

            self.assertContains(response, "Unable to access bothub with credentials, please check and try again")

        # all good
        with patch("requests.get") as mock_get:
            mock_get.return_value = MockResponse(200, '{ "error": "false" }')
            response = self.client.post(url, post_data)
            self.assertEqual(302, response.status_code)

            c = Classifier.objects.get()
            self.assertEqual("Bothub Test Repository", c.name)
            self.assertEqual("bothub", c.classifier_type)
            self.assertEqual("123456789", c.config[BothubType.CONFIG_ACCESS_TOKEN])
