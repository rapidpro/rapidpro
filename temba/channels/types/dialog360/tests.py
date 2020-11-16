from unittest.mock import patch

from django.forms import ValidationError
from django.urls import reverse

from temba.tests import MockResponse, TembaTest
from temba.templates.models import Template, TemplateTranslation

from ...models import Channel
from .type import Dialog360Type
from .tasks import refresh_360_templates


class Dialog360TypeTest(TembaTest):
    def test_claim(self):
        Channel.objects.all().delete()

        url = reverse("channels.types.dialog360.claim")
        self.login(self.admin)

        # make sure 360dialog is on the claim page
        response = self.client.get(reverse("channels.channel_claim"), follow=True)
        self.assertContains(response, url)

        response = self.client.get(url)
        self.assertEqual(200, response.status_code)
        post_data = response.context["form"].initial

        post_data["number"] = "1234"
        post_data["country"] = "RW"
        post_data["base_url"] = "https://ilhasoft.com.br/whatsapp"
        post_data["api_key"] = "123456789"

        # will fail with invalid phone number
        response = self.client.post(url, post_data)
        self.assertFormError(response, "form", None, ["Please enter a valid phone number"])

        # valid number
        post_data["number"] = "0788123123"

        # then success
        with patch("requests.post") as mock_post:
            mock_post.side_effect = [MockResponse(200, '{ "url": "https://ilhasoft.com.br/whatsapp" }')]

            response = self.client.post(url, post_data)
            self.assertEqual(302, response.status_code)

        channel = Channel.objects.get()

        self.assertEqual("123456789", channel.config[Channel.CONFIG_AUTH_TOKEN])
        self.assertEqual("https://ilhasoft.com.br/whatsapp", channel.config[Channel.CONFIG_BASE_URL])

        self.assertEqual("+250788123123", channel.address)
        self.assertEqual("RW", channel.country)
        self.assertEqual("D3", channel.channel_type)
        self.assertEqual(45, channel.tps)
        self.assertTrue(channel.get_type().has_attachment_support(channel))

        # test activating the channel
        with patch("requests.post") as mock_post:
            mock_post.side_effect = [MockResponse(200, '{ "url": "https://ilhasoft.com.br/whatsapp" }')]

            Dialog360Type().activate(channel)
            self.assertEqual(
                mock_post.call_args_list[0][1]["json"]["url"],
                "https://%s%s"
                % (channel.org.get_brand_domain(), reverse("courier.d3", args=[channel.uuid, "receive"])),
            )

        with patch("requests.post") as mock_post:
            mock_post.side_effect = [MockResponse(400, '{ "meta": { "success": false } }')]

            try:
                Dialog360Type().activate(channel)
                self.fail("Should have thrown error activating channel")
            except ValidationError:
                pass

        # test refresh_templates (same as channels.types.whatsapp.tests.WhatsAppTypeTest)
        with patch("requests.get") as mock_get:
            mock_get.side_effect = [
                MockResponse(
                    200,
                    """
                    {
                        "count": 10,
                        "filters": {},
                        "limit": 1000,
                        "offset": 0,
                        "sort": ["id"],
                        "total": 10,
                        "waba_templates": [
                            {
                                "name": "hello",
                                "components": [
                                {
                                    "type": "BODY",
                                    "text": "Hello {{1}}"
                                }
                                ],
                                "language": "en",
                                "status": "submitted",
                                "category": "ISSUE_RESOLUTION"
                            },
                            {
                                "name": "hello",
                                "components": [
                                {
                                    "type": "BODY",
                                    "text": "Hi {{1}}"
                                }
                                ],
                                "language": "en_GB",
                                "status": "submitted",
                                "category": "ISSUE_RESOLUTION"
                            },
                            {
                                "name": "hello",
                                "components": [
                                {
                                    "type": "BODY",
                                    "text": "Bonjour {{1}}"
                                }
                                ],
                                "language": "fr",
                                "status": "approved",
                                "category": "ISSUE_RESOLUTION"
                            },
                            {
                                "name": "goodbye",
                                "components": [
                                {
                                    "type": "BODY",
                                    "text": "Goodbye {{1}}, see you on {{2}}. See you later {{1}}"
                                }
                                ],
                                "language": "en",
                                "status": "submitted",
                                "category": "ISSUE_RESOLUTION"
                            },
                            {
                                "name": "workout_activity",
                                "components": [
                                {
                                    "type": "HEADER",
                                    "text": "Workout challenge week extra points!"
                                },
                                {
                                    "type": "BODY",
                                    "text": "Hey {{1}}, Week {{2}} workout is out now. Get your discount of {{3}} for the next workout by sharing this program to 3 people."
                                },
                                {
                                    "type": "FOOTER",
                                    "text": "Remember to drink water."
                                }
                                ],
                                "language": "en",
                                "status": "submitted",
                                "category": "ISSUE_RESOLUTION"
                            },
                            {
                                "name": "workout_activity_with_unsuported_variablet",
                                "components": [
                                {
                                    "type": "HEADER",
                                    "text": "Workout challenge week {{2}}, {{4}} extra points!"
                                },
                                {
                                    "type": "BODY",
                                    "text": "Hey {{1}}, Week {{2}} workout is out now. Get your discount of {{3}} for the next workout by sharing this program to 3 people."
                                },
                                {
                                    "type": "FOOTER",
                                    "text": "Remember to drink water."
                                }
                                ],
                                "language": "en",
                                "status": "submitted",
                                "category": "ISSUE_RESOLUTION"
                            },
                            {
                                "name": "missing_text_component",
                                "components": [
                                {
                                    "type": "HEADER",
                                    "format": "IMAGE",
                                    "example": {
                                    "header_handle": ["FOO"]
                                    }
                                }
                                ],
                                "language": "en",
                                "status": "approved",
                                "category": "ISSUE_RESOLUTION"
                            },
                            {
                                "name": "invalid_component",
                                "components": [
                                {
                                    "type": "RANDOM",
                                    "text": "Bonjour {{1}}"
                                }
                                ],
                                "language": "fr",
                                "status": "approved",
                                "category": "ISSUE_RESOLUTION"
                            },
                            {
                                "name": "invalid_status",
                                "components": [
                                {
                                    "type": "BODY",
                                    "text": "This is an unknown status, it will be ignored"
                                }
                                ],
                                "language": "en",
                                "status": "UNKNOWN",
                                "category": "ISSUE_RESOLUTION"
                            },
                            {
                                "name": "invalid_language",
                                "components": [
                                {
                                    "type": "BODY",
                                    "text": "This is an unknown language, it will be ignored"
                                }
                                ],
                                "language": "kli",
                                "status": "approved",
                                "category": "ISSUE_RESOLUTION"
                            }
                        ]
                    }
                    """,
                )
            ]
            refresh_360_templates()
            mock_get.assert_called_with(
                "https://ilhasoft.com.br/whatsapp/v1/configs/templates",
                {"D360-Api-Key": channel.config[Channel.CONFIG_AUTH_TOKEN], "Content-Type": "application/json",},
            )

            # should have 4 templates
            self.assertEqual(4, Template.objects.filter(org=self.org).count())
            # and 6 translations
            self.assertEqual(6, TemplateTranslation.objects.filter(channel=channel).count())

            # hit our template page
            response = self.client.get(reverse("channels.types.dialog360.templates", args=[channel.uuid]))

            # should have our template translations
            self.assertContains(response, "Bonjour")
            self.assertContains(response, "Hello")
            self.assertContains(response, reverse("channels.types.dialog360.sync_logs", args=[channel.uuid]))

        # deactivate our channel
        with self.settings(IS_PROD=True):
            channel.release()
