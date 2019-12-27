from unittest.mock import patch

from django.contrib.auth.models import Group
from django.urls import reverse

from temba.classifiers.models import Classifier
from temba.tests import MockResponse, TembaTest

from .type import BotHubType

INTENT_RESPONSE = """
{
    "intents": [
        "intent",
        "positive",
        "negative"
    ]
}
"""


class BotHubTypeTest(TembaTest):
    def test_sync(self):
        c = Classifier.create(
            self.org,
            self.user,
            BotHubType.slug,
            "Booker",
            {BotHubType.CONFIG_ACCESS_TOKEN: "123456789", BotHubType.INTENT_URL: "https://nlp.bothub.it/info/"},
        )

        with patch("requests.get") as mock_get:
            mock_get.return_value = MockResponse(400, '{ "error": "true" }')
            logs = []
            with self.assertRaises(Exception):
                BotHubType.get_active_intents_from_api(c, logs)
                self.assertEqual(1, len(logs))

        with patch("requests.get") as mock_get:
            mock_get.return_value = MockResponse(200, INTENT_RESPONSE)
            logs = []
            intents = BotHubType.get_active_intents_from_api(c, logs)
            self.assertEqual(1, len(logs))
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
            self.assertEqual("123456789", c.config[BotHubType.CONFIG_ACCESS_TOKEN])
