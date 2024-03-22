from django.urls import reverse

from temba.contacts.models import URN
from temba.tests import TembaTest
from temba.utils.text import generate_secret

from ...models import Channel


class KaleyraViewTest(TembaTest):
    def setUp(self):
        super().setUp()
        self.url = reverse("channels.types.kaleyra.claim")
        Channel.objects.all().delete()

    @property
    def valid_form(self):
        return {
            "country": "BR",
            "number": "31133087366",
            "account_sid": generate_secret(10),
            "api_key": generate_secret(10),
        }

    def submit_form(self, data):
        return self.client.post(self.url, data)

    def test_claim_page_is_available(self):
        self.login(self.admin)

        response = self.client.get(reverse("channels.channel_claim"), follow=True)
        self.assertContains(response, self.url)

        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)

    def test_claim(self):
        self.login(self.admin)

        post_data = self.valid_form
        response = self.submit_form(post_data)
        channel = Channel.objects.order_by("id").last()

        normalized_number = URN.normalize_number(post_data["number"], post_data["country"])
        self.assertEqual(channel.address, normalized_number)
        self.assertEqual(channel.country, post_data["country"])
        self.assertEqual(channel.config["api_key"], post_data["api_key"])
        self.assertEqual(channel.config["account_sid"], post_data["account_sid"])

        self.assertEqual(302, response.status_code)
        self.assertRedirect(response, reverse("channels.channel_configuration", args=[channel.uuid]))

    def test_required_fields(self):
        self.login(self.admin)

        response = self.submit_form({})
        self.assertEqual(200, response.status_code)
        self.assertFormError(response.context["form"], "number", "This field is required.")
        self.assertFormError(response.context["form"], "country", "This field is required.")
        self.assertFormError(response.context["form"], "account_sid", "This field is required.")
        self.assertFormError(response.context["form"], "api_key", "This field is required.")

    def test_invalid_phone_number(self):
        self.login(self.admin)

        post_data = self.valid_form
        post_data["number"] = "1234"  # invalid
        response = self.submit_form(post_data)
        self.assertEqual(200, response.status_code)
        self.assertFormError(response.context["form"], "number", ["Please enter a valid phone number"])
