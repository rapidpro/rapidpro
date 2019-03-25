from unittest.mock import patch

from django.forms import ValidationError
from django.urls import reverse

from temba.templates.models import ChannelTemplate, Template
from temba.tests import MockResponse, TembaTest

from ...models import Channel
from .tasks import refresh_whatsapp_contacts, refresh_whatsapp_templates, refresh_whatsapp_tokens
from .type import WhatsAppType


class WhatsAppTypeTest(TembaTest):
    def test_claim(self):
        Channel.objects.all().delete()

        url = reverse("channels.types.whatsapp.claim")
        self.login(self.admin)

        response = self.client.get(reverse("channels.channel_claim"))
        self.assertNotContains(response, url)

        response = self.client.get(url)
        self.assertEqual(200, response.status_code)
        post_data = response.context["form"].initial

        post_data["number"] = "1234"
        post_data["username"] = "temba"
        post_data["password"] = "tembapasswd"
        post_data["country"] = "RW"
        post_data["base_url"] = "https://whatsapp.foo.bar"
        post_data["facebook_user_id"] = "1234"
        post_data["facebook_access_token"] = "token123"

        # will fail with invalid phone number
        response = self.client.post(url, post_data)
        self.assertFormError(response, "form", None, ["Please enter a valid phone number"])

        # valid number
        post_data["number"] = "0788123123"

        # try once with an error
        with patch("requests.post") as mock_post:
            mock_post.return_value = MockResponse(400, '{ "error": "true" }')
            response = self.client.post(url, post_data)
            self.assertEqual(200, response.status_code)
            self.assertFalse(Channel.objects.all())

        # then success
        with patch("requests.post") as mock_post:
            mock_post.return_value = MockResponse(200, '{"users": [{"token": "abc123"}]}')
            response = self.client.post(url, post_data)
            self.assertEqual(302, response.status_code)

        channel = Channel.objects.get()

        self.assertEqual("temba", channel.config[Channel.CONFIG_USERNAME])
        self.assertEqual("tembapasswd", channel.config[Channel.CONFIG_PASSWORD])
        self.assertEqual("abc123", channel.config[Channel.CONFIG_AUTH_TOKEN])
        self.assertEqual("https://whatsapp.foo.bar", channel.config[Channel.CONFIG_BASE_URL])

        self.assertEqual("+250788123123", channel.address)
        self.assertEqual("RW", channel.country)
        self.assertEqual("WA", channel.channel_type)
        self.assertTrue(channel.get_type().has_attachment_support(channel))

        # test activating the channel
        with patch("requests.patch") as mock_patch:
            mock_patch.side_effect = [MockResponse(200, '{ "error": false }'), MockResponse(200, '{ "error": false }')]
            WhatsAppType().activate(channel)
            self.assertEqual(
                mock_patch.call_args_list[0][1]["json"]["webhooks"]["url"],
                "https://%s%s"
                % (channel.org.get_brand_domain(), reverse("courier.wa", args=[channel.uuid, "receive"])),
            )
            self.assertEqual(
                mock_patch.call_args_list[1][1]["json"]["messaging_api_rate_limit"], ["15", "54600", "1000000"]
            )

        with patch("requests.patch") as mock_patch:
            mock_patch.side_effect = [MockResponse(400, '{ "error": true }')]

            try:
                WhatsAppType().activate(channel)
                self.fail("Should have thrown error activating channel")
            except ValidationError:
                pass

        with patch("requests.patch") as mock_patch:
            mock_patch.side_effect = [
                MockResponse(200, '{ "error": "false" }'),
                MockResponse(400, '{ "error": "true" }'),
            ]

            try:
                WhatsAppType().activate(channel)
                self.fail("Should have thrown error activating channel")
            except ValidationError:
                pass

        # ok, test our refreshing
        refresh_url = reverse("channels.types.whatsapp.refresh", args=[channel.uuid])
        resp = self.client.get(refresh_url)
        self.assertEqual(405, resp.status_code)

        with patch("requests.post") as mock_post:
            mock_post.side_effect = [MockResponse(200, '{ "error": false }')]
            self.create_contact("Joe", urn="whatsapp:250788382382")
            self.client.post(refresh_url)

            self.assertEqual(mock_post.call_args_list[0][1]["json"]["contacts"], ["+250788382382"])

        with patch("requests.post") as mock_post:
            mock_post.side_effect = [MockResponse(400, '{ "error": true }')]
            try:
                refresh_whatsapp_contacts(channel.id)
                self.fail("Should have thrown exception")
            except Exception:
                pass

        # and fetching new tokens
        with patch("requests.post") as mock_post:
            mock_post.return_value = MockResponse(200, '{"users": [{"token": "abc345"}]}')
            refresh_whatsapp_tokens()
            channel.refresh_from_db()
            self.assertEqual("abc345", channel.config[Channel.CONFIG_AUTH_TOKEN])

        with patch("requests.post") as mock_post:
            mock_post.side_effect = [MockResponse(400, '{ "error": true }')]
            refresh_whatsapp_tokens()
            channel.refresh_from_db()
            self.assertEqual("abc345", channel.config[Channel.CONFIG_AUTH_TOKEN])

        with patch("requests.get") as mock_get:
            mock_get.side_effect = [
                MockResponse(
                    200,
                    """
            {
              "data": [
              {
                "name": "hello",
                "content": "Hello {{1}}",
                "language": "en_US",
                "status": "PENDING",
                "category": "ISSUE_RESOLUTION",
                "id": "1234"
              },
              {
                "name": "hello",
                "content": "Bonjour {{1}}",
                "language": "fr",
                "status": "APPROVED",
                "category": "ISSUE_RESOLUTION",
                "id": "5678"
              },
              {
                "name": "goodbye",
                "content": "Goodbye {{1}}",
                "language": "en_US",
                "status": "PENDING",
                "category": "ISSUE_RESOLUTION",
                "id": "9012"
              }
            ],
            "paging": {
              "cursors": {
              "before": "MAZDZD",
              "after": "MjQZD"
            }
            }
            }""",
                )
            ]
            refresh_whatsapp_templates()

            # should have two templates
            self.assertEqual(2, Template.objects.filter(org=self.org).count())
            self.assertEqual(3, ChannelTemplate.objects.filter(channel=channel).count())
