from unittest.mock import patch

from requests import RequestException

from django.forms import ValidationError
from django.urls import reverse

from temba.request_logs.models import HTTPLog
from temba.templates.models import Template, TemplateTranslation
from temba.tests import MockResponse, TembaTest

from ...models import Channel
from .tasks import (
    _calculate_variable_count,
    refresh_whatsapp_contacts,
    refresh_whatsapp_templates,
    refresh_whatsapp_tokens,
)
from .type import CONFIG_FB_BUSINESS_ID, CONFIG_FB_TEMPLATE_LIST_DOMAIN, WhatsAppType


class WhatsAppTypeTest(TembaTest):
    def test_calculate_variable_count(self):
        self.assertEqual(2, _calculate_variable_count("Hi {{1}} how are you? {{2}}"))
        self.assertEqual(2, _calculate_variable_count("Hi {{1}} how are you? {{2}} {{1}}"))
        self.assertEqual(0, _calculate_variable_count("Hi there."))

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
        post_data["base_url"] = "https://nyaruka.com/whatsapp"
        post_data["facebook_namespace"] = "my-custom-app"
        post_data["facebook_business_id"] = "1234"
        post_data["facebook_access_token"] = "token123"
        post_data["facebook_template_list_domain"] = "graph.facebook.com"

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

            self.assertContains(response, "check username and password")

        # then FB failure
        with patch("requests.post") as mock_post:
            with patch("requests.get") as mock_get:
                mock_post.return_value = MockResponse(200, '{"users": [{"token": "abc123"}]}')
                mock_get.return_value = MockResponse(400, '{"data": []}')

                response = self.client.post(url, post_data)
                self.assertEqual(200, response.status_code)
                self.assertFalse(Channel.objects.all())
                mock_get.assert_called_with(
                    "https://graph.facebook.com/v3.3/1234/message_templates", params={"access_token": "token123"}
                )

                self.assertContains(response, "check user id and access token")

        # then success
        with patch("requests.post") as mock_post:
            with patch("requests.get") as mock_get:
                mock_post.return_value = MockResponse(200, '{"users": [{"token": "abc123"}]}')
                mock_get.return_value = MockResponse(200, '{"data": []}')

                response = self.client.post(url, post_data)
                self.assertEqual(302, response.status_code)

        channel = Channel.objects.get()

        self.assertEqual("temba", channel.config[Channel.CONFIG_USERNAME])
        self.assertEqual("tembapasswd", channel.config[Channel.CONFIG_PASSWORD])
        self.assertEqual("abc123", channel.config[Channel.CONFIG_AUTH_TOKEN])
        self.assertEqual("https://nyaruka.com/whatsapp", channel.config[Channel.CONFIG_BASE_URL])

        self.assertEqual("+250788123123", channel.address)
        self.assertEqual("RW", channel.country)
        self.assertEqual("WA", channel.channel_type)
        self.assertEqual(45, channel.tps)
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
            self.assertFalse(channel.http_logs.filter(log_type=HTTPLog.WHATSAPP_CONTACTS_REFRESHED, is_error=False))
            self.create_contact("Joe", urn="whatsapp:250788382382")
            self.client.post(refresh_url)

            self.assertEqual(mock_post.call_args_list[0][1]["json"]["contacts"], ["+250788382382"])
            self.assertTrue(channel.http_logs.filter(log_type=HTTPLog.WHATSAPP_CONTACTS_REFRESHED, is_error=False))

        with patch("requests.post") as mock_post:
            mock_post.side_effect = [MockResponse(400, '{ "error": true }')]
            self.assertFalse(channel.http_logs.filter(log_type=HTTPLog.WHATSAPP_CONTACTS_REFRESHED, is_error=True))
            refresh_whatsapp_contacts(channel.id)
            self.assertTrue(channel.http_logs.filter(log_type=HTTPLog.WHATSAPP_CONTACTS_REFRESHED, is_error=True))

        # and fetching new tokens
        with patch("requests.post") as mock_post:
            mock_post.return_value = MockResponse(200, '{"users": [{"token": "abc345"}]}')
            self.assertFalse(channel.http_logs.filter(log_type=HTTPLog.WHATSAPP_TOKENS_SYNCED, is_error=False))
            refresh_whatsapp_tokens()
            self.assertTrue(channel.http_logs.filter(log_type=HTTPLog.WHATSAPP_TOKENS_SYNCED, is_error=False))
            channel.refresh_from_db()
            self.assertEqual("abc345", channel.config[Channel.CONFIG_AUTH_TOKEN])

        with patch("requests.post") as mock_post:
            mock_post.side_effect = [MockResponse(400, '{ "error": true }')]
            self.assertFalse(channel.http_logs.filter(log_type=HTTPLog.WHATSAPP_TOKENS_SYNCED, is_error=True))
            refresh_whatsapp_tokens()
            self.assertTrue(channel.http_logs.filter(log_type=HTTPLog.WHATSAPP_TOKENS_SYNCED, is_error=True))
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
                "components": [
                  {
                    "type": "BODY",
                    "text": "Hello {{1}}"
                  }
                ],
                "language": "en",
                "status": "PENDING",
                "category": "ISSUE_RESOLUTION",
                "id": "1234"
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
                "status": "APPROVED",
                "category": "ISSUE_RESOLUTION",
                "id": "5678"
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
                "status": "PENDING",
                "category": "ISSUE_RESOLUTION",
                "id": "9012"
              },
              {
                "name": "workout_activity",
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
                "status": "PENDING",
                "category": "ISSUE_RESOLUTION",
                "id": "9014"
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
                "status": "APPROVED",
                "category": "ISSUE_RESOLUTION",
                "id": "1233"
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
                "category": "ISSUE_RESOLUTION",
                "id": "9012"
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
                "status": "APPROVED",
                "category": "ISSUE_RESOLUTION",
                "id": "9018"
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
            mock_get.assert_called_with(
                "https://graph.facebook.com/v3.3/1234/message_templates",
                params={"access_token": "token123", "limit": 255},
            )

            # should have 4 templates
            self.assertEqual(4, Template.objects.filter(org=self.org).count())
            self.assertEqual(5, TemplateTranslation.objects.filter(channel=channel).count())

            # hit our template page
            response = self.client.get(reverse("channels.types.whatsapp.templates", args=[channel.uuid]))

            # should have our template translations
            self.assertContains(response, "Bonjour")
            self.assertContains(response, "Hello")
            self.assertContains(response, reverse("channels.types.whatsapp.sync_logs", args=[channel.uuid]))

            ct = TemplateTranslation.objects.get(template__name="goodbye", is_active=True)
            self.assertEqual(2, ct.variable_count)
            self.assertEqual("Goodbye {{1}}, see you on {{2}}. See you later {{1}}", ct.content)
            self.assertEqual("eng", ct.language)
            self.assertEqual(TemplateTranslation.STATUS_PENDING, ct.status)
            self.assertEqual("goodbye (eng) P: Goodbye {{1}}, see you on {{2}}. See you later {{1}}", str(ct))

            ct = TemplateTranslation.objects.get(template__name="workout_activity", is_active=True)
            self.assertEqual(4, ct.variable_count)
            self.assertEqual(
                "Workout challenge week {{2}}, {{4}} extra points!\n\nHey {{1}}, Week {{2}} workout is out now. Get your discount of {{3}} for the next workout by sharing this program to 3 people.\n\nRemember to drink water.",
                ct.content,
            )
            self.assertEqual("eng", ct.language)
            self.assertEqual(TemplateTranslation.STATUS_PENDING, ct.status)

            # assert that a template translation was created despite it being in an unknown language
            ct = TemplateTranslation.objects.get(template__name="invalid_language", is_active=True)
            self.assertEqual("kli", ct.language)
            self.assertEqual(TemplateTranslation.STATUS_UNSUPPORTED_LANGUAGE, ct.status)

        # clear our FB ids, should cause refresh to be noop (but not fail)
        del channel.config[CONFIG_FB_BUSINESS_ID]
        channel.save(update_fields=["config", "modified_on"])
        refresh_whatsapp_templates()

        # deactivate our channel
        with self.settings(IS_PROD=True):
            channel.release()

        # all our templates should be inactive now
        self.assertEqual(5, TemplateTranslation.objects.filter(channel=channel, is_active=False).count())

    def test_claim_self_hosted_templates(self):
        Channel.objects.all().delete()

        url = reverse("channels.types.whatsapp.claim")
        self.login(self.admin)

        response = self.client.get(reverse("channels.channel_claim"))
        self.assertNotContains(response, url)

        response = self.client.get(url)
        self.assertEqual(200, response.status_code)
        post_data = response.context["form"].initial

        post_data["number"] = "0788123123"
        post_data["username"] = "temba"
        post_data["password"] = "tembapasswd"
        post_data["country"] = "RW"
        post_data["base_url"] = "https://nyaruka.com/whatsapp"
        post_data["facebook_namespace"] = "my-custom-app"
        post_data["facebook_business_id"] = "1234"
        post_data["facebook_access_token"] = "token123"
        post_data["facebook_template_list_domain"] = "example.org"

        # success claim
        with patch("requests.post") as mock_post:
            with patch("requests.get") as mock_get:

                mock_post.return_value = MockResponse(200, '{"users": [{"token": "abc123"}]}')
                mock_get.return_value = MockResponse(200, '{"data": []}')

                response = self.client.post(url, post_data)
                self.assertEqual(302, response.status_code)
                mock_get.assert_called_with(
                    "https://example.org/v3.3/1234/message_templates", params={"access_token": "token123"}
                )

        # test the template syncing task calls the correct domain
        with patch("requests.get") as mock_get:
            mock_get.return_value = MockResponse(200, '{"data": []}')
            refresh_whatsapp_templates()
            mock_get.assert_called_with(
                "https://example.org/v3.3/1234/message_templates", params={"access_token": "token123", "limit": 255}
            )

        channel = Channel.objects.get()

        self.assertEqual("example.org", channel.config[CONFIG_FB_TEMPLATE_LIST_DOMAIN])
        self.assertEqual(1, channel.http_logs.filter(log_type=HTTPLog.WHATSAPP_TEMPLATES_SYNCED).count())
        self.assertEqual(1, channel.http_logs.filter(log_type=HTTPLog.WHATSAPP_TEMPLATES_SYNCED).count())

        # hit our sync logs page
        response = self.client.get(reverse("channels.types.whatsapp.sync_logs", args=[channel.uuid]))

        # should have our log
        self.assertContains(response, "WhatsApp Templates Synced")
        self.assertContains(response, reverse("channels.types.whatsapp.templates", args=[channel.uuid]))

        sync_log = channel.http_logs.filter(log_type=HTTPLog.WHATSAPP_TEMPLATES_SYNCED).first()
        log_url = reverse("request_logs.httplog_read", args=[sync_log.id])
        self.assertContains(response, log_url)

        response = self.client.get(log_url)
        self.assertContains(response, "200")
        self.assertContains(response, "https://example.org/v3.3/1234/message_templates")

        with patch("requests.get") as mock_get:
            # use fake response to simulate the exception request
            # See https://github.com/psf/requests/blob/eedd67462819f8dbf8c1c32e77f9070606605231/requests/exceptions.py#L17
            mock_get.side_effect = RequestException("Network is unreachable", response=MockResponse(100, ""))
            refresh_whatsapp_templates()

        sync_log = channel.http_logs.filter(log_type=HTTPLog.WHATSAPP_TEMPLATES_SYNCED, is_error=True).first()
        log_url = reverse("request_logs.httplog_read", args=[sync_log.id])
        response = self.client.get(log_url)
        self.assertContains(response, "Connection Error")
        self.assertContains(response, "https://example.org/v3.3/1234/message_templates")

        # sync logs not accessible by user from other org
        self.login(self.admin2)
        response = self.client.get(reverse("channels.types.whatsapp.sync_logs", args=[channel.uuid]))
        self.assertEqual(404, response.status_code)
