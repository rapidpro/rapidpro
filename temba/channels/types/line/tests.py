from django.urls import reverse

from temba.tests import TembaTest

from ...models import Channel
from .type import LineType


class LineTypeTest(TembaTest):
    def setUp(self):
        super().setUp()

        self.channel = Channel.create(
            self.org,
            self.user,
            None,
            "LN",
            name="LINE",
            address="12345",
            role="SR",
            schemes=["line"],
            config={"auth_token": "abcdef098765", "secret": "87654"},
        )

    def test_claim(self):
        url = reverse("channels.types.line.claim")

        self.login(self.admin)

        # check that claim page URL appears on claim list page
        response = self.client.get(reverse("channels.channel_claim"))
        self.assertContains(response, url)

        payload = {"access_token": "abcdef123456", "secret": "123456", "address": "123456789", "name": "Temba" * 20}

        response = self.client.post(url, payload, follow=True)
        self.assertFormError(
            response.context["form"], "name", "Ensure this value has at most 64 characters (it has 100)."
        )

        payload = {"access_token": "abcdef123456", "secret": "123456", "address": "123456789", "name": "Temba"}

        response = self.client.post(url, payload, follow=True)

        channel = Channel.objects.get(address="123456789")
        self.assertRedirects(response, reverse("channels.channel_configuration", args=[channel.uuid]))
        self.assertEqual(channel.config, {"auth_token": "abcdef123456", "secret": "123456", "channel_id": "123456789"})

        response = self.client.post(url, payload, follow=True)
        self.assertContains(response, "This channel is already connected in this workspace.")

    def test_get_error_ref_url(self):
        self.assertEqual(
            "https://developers.line.biz/en/reference/messaging-api/#error-responses",
            LineType().get_error_ref_url(None, "12"),
        )
