from unittest.mock import patch

from django.urls import reverse

from temba.classifiers.models import Classifier
from temba.tests import MockResponse, TembaTest

from .type import LuisType


class LuisTypeTest(TembaTest):
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

        # ok, will everything out
        post_data["name"] = "Booker"
        post_data["app_id"] = "12345"
        post_data["version"] = "0.1"
        post_data["primary_key"] = "sesame"
        post_data["endpoint_url"] = "http://foo.bar/luis"

        # can't connect
        with patch("requests.get") as mock_get:
            mock_get.return_value = MockResponse(400, '{ "error": "true" }')
            response = self.client.post(url, post_data)
            self.assertEqual(200, response.status_code)
            self.assertFalse(Classifier.objects.all())

            self.assertContains(response, "Unable to get intents for your app")

        # all good
        with patch("requests.get") as mock_get:
            mock_get.return_value = MockResponse(200, '{ "error": "false" }')
            response = self.client.post(url, post_data)
            self.assertEqual(302, response.status_code)

            c = Classifier.objects.get()
            self.assertEqual("Booker", c.name)
            self.assertEqual("luis", c.classifier_type)
            self.assertEqual("sesame", c.config[LuisType.CONFIG_PRIMARY_KEY])
            self.assertEqual("http://foo.bar/luis", c.config[LuisType.CONFIG_ENDPOINT_URL])
            self.assertEqual("0.1", c.config[LuisType.CONFIG_VERSION])
