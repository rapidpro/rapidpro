import json
from unittest.mock import call, patch

from requests import RequestException

from django.forms import ValidationError
from django.urls import reverse

from temba.request_logs.models import HTTPLog
from temba.templates.models import TemplateTranslation
from temba.templates.tasks import refresh_templates
from temba.tests import CRUDLTestMixin, MockResponse, TembaTest
from temba.utils.whatsapp.tasks import refresh_whatsapp_contacts

from ...models import Channel
from .tasks import refresh_whatsapp_tokens
from .type import (
    CONFIG_FB_ACCESS_TOKEN,
    CONFIG_FB_BUSINESS_ID,
    CONFIG_FB_NAMESPACE,
    CONFIG_FB_TEMPLATE_API_VERSION,
    CONFIG_FB_TEMPLATE_LIST_DOMAIN,
    WhatsAppLegacyType,
)


class WhatsAppLegacyTypeTest(CRUDLTestMixin, TembaTest):
    @patch("socket.gethostbyname", return_value="123.123.123.123")
    @patch("temba.channels.types.whatsapp_legacy.WhatsAppLegacyType.check_health")
    def test_claim(self, mock_health, mock_socket_hostname):
        mock_health.return_value = MockResponse(200, '{"meta": {"api_status": "stable", "version": "v2.35.2"}}')
        TemplateTranslation.objects.all().delete()
        Channel.objects.all().delete()

        url = reverse("channels.types.whatsapp_legacy.claim")
        self.login(self.admin)

        response = self.client.get(reverse("channels.channel_claim"))
        self.assertNotContains(response, url)

        response = self.client.get(url)
        self.assertEqual(200, response.status_code)
        post_data = response.context["form"].initial

        post_data["address"] = "1234"
        post_data["username"] = "temba"
        post_data["password"] = "tembapasswd"
        post_data["country"] = "RW"
        post_data["base_url"] = "https://textit.com/whatsapp"
        post_data["facebook_namespace"] = "my-custom-app"
        post_data["facebook_business_id"] = "1234"
        post_data["facebook_access_token"] = "token123"
        post_data["facebook_template_list_domain"] = "graph.facebook.com"
        post_data["facebook_template_list_api_version"] = ""

        # will fail with invalid phone number
        response = self.client.post(url, post_data)
        self.assertFormError(response.context["form"], None, ["Please enter a valid phone number"])

        # valid number
        post_data["address"] = "0788123123"

        # try once with an error
        with patch("requests.post") as mock_post:
            mock_post.return_value = MockResponse(400, '{ "error": "true" }')
            response = self.client.post(url, post_data)
            self.assertEqual(200, response.status_code)
            self.assertFalse(Channel.objects.all())

            self.assertContains(response, "check username and password")

        # Uncomment this when we activate back the checking of Facebook templates
        # # then FB failure
        # with patch("requests.post") as mock_post:
        #     with patch("requests.get") as mock_get:
        #         mock_post.return_value = MockResponse(200, '{"users": [{"token": "abc123"}]}')
        #         mock_get.return_value = MockResponse(400, '{"data": []}')
        #
        #         response = self.client.post(url, post_data)
        #         self.assertEqual(200, response.status_code)
        #         self.assertFalse(Channel.objects.all())
        #         mock_get.assert_called_with(
        #             "https://graph.facebook.com/v14.0/1234/message_templates", params={"access_token": "token123"}
        #         )
        #
        #         self.assertContains(response, "check user id and access token")

        # then success
        with patch("requests.post") as mock_post, patch("requests.get") as mock_get, patch(
            "requests.patch"
        ) as mock_patch:
            mock_post.return_value = MockResponse(200, '{"users": [{"token": "abc123"}]}')
            mock_get.return_value = MockResponse(200, '{"data": []}')
            mock_patch.return_value = MockResponse(200, '{"data": []}')

            response = self.client.post(url, post_data)
            self.assertEqual(302, response.status_code)

        channel = Channel.objects.get()

        self.assertEqual("graph.facebook.com", channel.config[CONFIG_FB_TEMPLATE_LIST_DOMAIN])
        self.assertEqual("temba", channel.config[Channel.CONFIG_USERNAME])
        self.assertEqual("tembapasswd", channel.config[Channel.CONFIG_PASSWORD])
        self.assertEqual("abc123", channel.config[Channel.CONFIG_AUTH_TOKEN])
        self.assertEqual("https://textit.com/whatsapp", channel.config[Channel.CONFIG_BASE_URL])
        self.assertNotIn(CONFIG_FB_TEMPLATE_API_VERSION, channel.config)

        self.assertEqual("+250788123123", channel.address)
        self.assertEqual("RW", channel.country)
        self.assertEqual("WA", channel.channel_type)
        self.assertEqual(45, channel.tps)

        # test activating the channel
        with patch("requests.patch") as mock_patch:
            mock_patch.side_effect = [MockResponse(200, '{ "error": false }'), MockResponse(200, '{ "error": false }')]
            WhatsAppLegacyType().activate(channel)
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
                WhatsAppLegacyType().activate(channel)
                self.fail("Should have thrown error activating channel")
            except ValidationError:
                pass

        with patch("requests.patch") as mock_patch:
            mock_patch.side_effect = [
                MockResponse(200, '{ "error": "false" }'),
                MockResponse(400, '{ "error": "true" }'),
            ]

            try:
                WhatsAppLegacyType().activate(channel)
                self.fail("Should have thrown error activating channel")
            except ValidationError:
                pass

        # ok, test our config page
        config_url = reverse("channels.channel_configuration", args=[channel.uuid])
        resp = self.client.get(config_url)
        self.assertEqual(200, resp.status_code)

        # ok, test our refreshing
        refresh_url = reverse("channels.types.whatsapp_legacy.refresh", args=[channel.uuid])
        resp = self.client.get(refresh_url)
        self.assertEqual(405, resp.status_code)

        with patch("requests.post") as mock_post:
            mock_post.side_effect = [MockResponse(200, '{ "error": false }')]
            self.assertFalse(channel.http_logs.filter(log_type=HTTPLog.WHATSAPP_CONTACTS_REFRESHED, is_error=False))
            self.create_contact("Joe", urns=["whatsapp:250788382382"])
            self.client.post(refresh_url)

            self.assertEqual(mock_post.call_args_list[0][1]["json"]["contacts"], ["+250788382382"])
            self.assertTrue(channel.http_logs.filter(log_type=HTTPLog.WHATSAPP_CONTACTS_REFRESHED, is_error=False))

        with patch("requests.post") as mock_post:
            mock_post.side_effect = [MockResponse(400, '{ "error": true }')]
            self.assertFalse(channel.http_logs.filter(log_type=HTTPLog.WHATSAPP_CONTACTS_REFRESHED, is_error=True))
            refresh_whatsapp_contacts(channel.id)
            self.assertTrue(channel.http_logs.filter(log_type=HTTPLog.WHATSAPP_CONTACTS_REFRESHED, is_error=True))

        # clear our FB ids, should cause refresh to be noop (but not fail)
        del channel.config[CONFIG_FB_BUSINESS_ID]
        channel.save(update_fields=["config", "modified_on"])
        refresh_templates()

        # deactivate our channel
        channel.release(self.admin)

    @patch("socket.gethostbyname", return_value="123.123.123.123")
    @patch("temba.channels.types.whatsapp_legacy.WhatsAppLegacyType.check_health")
    def test_duplicate_number_channels(self, mock_health, mock_socket_hostname):
        mock_health.return_value = MockResponse(200, '{"meta": {"api_status": "stable", "version": "v2.35.2"}}')
        TemplateTranslation.objects.all().delete()
        Channel.objects.all().delete()

        url = reverse("channels.types.whatsapp_legacy.claim")
        self.login(self.admin)

        response = self.client.get(reverse("channels.channel_claim"))
        self.assertNotContains(response, url)

        response = self.client.get(url)
        self.assertEqual(200, response.status_code)
        post_data = response.context["form"].initial

        post_data["address"] = "0788123123"
        post_data["username"] = "temba"
        post_data["password"] = "tembapasswd"
        post_data["country"] = "RW"
        post_data["base_url"] = "https://textit.com/whatsapp"
        post_data["facebook_namespace"] = "my-custom-app"
        post_data["facebook_business_id"] = "1234"
        post_data["facebook_access_token"] = "token123"
        post_data["facebook_template_list_domain"] = "graph.facebook.com"
        post_data["facebook_template_list_api_version"] = ""

        # will fail with invalid phone number
        response = self.client.post(url, post_data)

        with patch("requests.post") as mock_post, patch("requests.get") as mock_get, patch(
            "requests.patch"
        ) as mock_patch:
            mock_post.return_value = MockResponse(200, '{"users": [{"token": "abc123"}]}')
            mock_get.return_value = MockResponse(200, '{"data": []}')
            mock_patch.return_value = MockResponse(200, '{"data": []}')

            response = self.client.post(url, post_data)
            self.assertEqual(302, response.status_code)

        channel = Channel.objects.get()

        with patch("requests.post") as mock_post, patch("requests.get") as mock_get, patch(
            "requests.patch"
        ) as mock_patch:
            mock_post.return_value = MockResponse(200, '{"users": [{"token": "abc123"}]}')
            mock_get.return_value = MockResponse(200, '{"data": []}')
            mock_patch.return_value = MockResponse(200, '{"data": []}')

            response = self.client.post(url, post_data)
            self.assertEqual(200, response.status_code)
            self.assertFormError(response.context["form"], None, "This channel is already connected in this workspace.")

        channel.org = self.org2
        channel.save()

        with patch("requests.post") as mock_post, patch("requests.get") as mock_get, patch(
            "requests.patch"
        ) as mock_patch:
            mock_post.return_value = MockResponse(200, '{"users": [{"token": "abc123"}]}')
            mock_get.return_value = MockResponse(200, '{"data": []}')
            mock_patch.return_value = MockResponse(200, '{"data": []}')

            response = self.client.post(url, post_data)
            self.assertEqual(200, response.status_code)
            self.assertFormError(
                response.context["form"],
                None,
                "This channel is already connected in another workspace.",
            )

    def test_refresh_tokens(self):
        TemplateTranslation.objects.all().delete()
        Channel.objects.all().delete()

        channel = self.create_channel(
            "WA",
            "WhatsApp: 1234",
            "1234",
            config={
                Channel.CONFIG_BASE_URL: "https://textit.com/whatsapp",
                Channel.CONFIG_USERNAME: "temba",
                Channel.CONFIG_PASSWORD: "tembapasswd",
                Channel.CONFIG_AUTH_TOKEN: "authtoken123",
                CONFIG_FB_BUSINESS_ID: "1234",
                CONFIG_FB_ACCESS_TOKEN: "token123",
                CONFIG_FB_NAMESPACE: "my-custom-app",
                CONFIG_FB_TEMPLATE_LIST_DOMAIN: "graph.facebook.com",
            },
        )

        channel2 = self.create_channel(
            "WA",
            "WhatsApp: 1235",
            "1235",
            config={
                Channel.CONFIG_BASE_URL: "https://textit.com/whatsapp",
                Channel.CONFIG_USERNAME: "temba",
                Channel.CONFIG_PASSWORD: "tembapasswd",
                Channel.CONFIG_AUTH_TOKEN: "authtoken123",
                CONFIG_FB_BUSINESS_ID: "1234",
                CONFIG_FB_ACCESS_TOKEN: "token123",
                CONFIG_FB_NAMESPACE: "my-custom-app",
                CONFIG_FB_TEMPLATE_LIST_DOMAIN: "graph.facebook.com",
            },
        )

        # and fetching new tokens
        with patch("requests.post") as mock_post:
            mock_post.return_value = MockResponse(
                200,
                '{"users": [{"token": "abc345"}]}',
                headers={
                    "Authorization": "Basic dGVtYmE6dGVtYmFwYXNzd2Q=",
                    "WA-user": "temba",
                    "WA-pass": "tembapasswd",
                },
            )
            self.assertFalse(channel.http_logs.filter(log_type=HTTPLog.WHATSAPP_TOKENS_SYNCED, is_error=False))
            refresh_whatsapp_tokens()
            self.assertTrue(channel.http_logs.filter(log_type=HTTPLog.WHATSAPP_TOKENS_SYNCED, is_error=False))
            channel.refresh_from_db()
            self.assertEqual("abc345", channel.config[Channel.CONFIG_AUTH_TOKEN])
            # check channel username, password, basic auth are redacted in HTTP logs
            for log in channel.http_logs.all():
                self.assertIn("temba", json.dumps(log.get_display()))
                self.assertNotIn("tembapasswd", json.dumps(log.get_display()))
                self.assertNotIn("dGVtYmE6dGVtYmFwYXNzd2Q=", json.dumps(log.get_display()))

        with patch("requests.post") as mock_post:
            mock_post.side_effect = [
                MockResponse(
                    400,
                    '{ "error": true }',
                    headers={
                        "Authorization": "Basic dGVtYmE6dGVtYmFwYXNzd2Q=",
                        "WA-user": "temba",
                        "WA-pass": "tembapasswd",
                    },
                )
            ]
            self.assertFalse(channel.http_logs.filter(log_type=HTTPLog.WHATSAPP_TOKENS_SYNCED, is_error=True))
            refresh_whatsapp_tokens()
            self.assertTrue(channel.http_logs.filter(log_type=HTTPLog.WHATSAPP_TOKENS_SYNCED, is_error=True))
            channel.refresh_from_db()
            self.assertEqual("abc345", channel.config[Channel.CONFIG_AUTH_TOKEN])
            # check channel username, password, basic auth are redacted in HTTP logs
            for log in channel.http_logs.all():
                self.assertIn("temba", json.dumps(log.get_display()))
                self.assertNotIn("tembapasswd", json.dumps(log.get_display()))
                self.assertNotIn("dGVtYmE6dGVtYmFwYXNzd2Q=", json.dumps(log.get_display()))

        with patch("requests.post") as mock_post:
            mock_post.side_effect = [
                MockResponse(
                    200,
                    "",
                    headers={
                        "Authorization": "Basic dGVtYmE6dGVtYmFwYXNzd2Q=",
                        "WA-user": "temba",
                        "WA-pass": "tembapasswd",
                    },
                ),
                MockResponse(
                    200,
                    '{"users": [{"token": "abc098"}]}',
                    headers={
                        "Authorization": "Basic dGVtYmE6dGVtYmFwYXNzd2Q=",
                        "WA-user": "temba",
                        "WA-pass": "tembapasswd",
                    },
                ),
            ]
            refresh_whatsapp_tokens()

            channel.refresh_from_db()
            channel2.refresh_from_db()
            self.assertEqual("abc345", channel.config[Channel.CONFIG_AUTH_TOKEN])
            self.assertEqual("abc098", channel2.config[Channel.CONFIG_AUTH_TOKEN])
            # check channel username, password, basic auth are redacted in HTTP logs
            for log in channel.http_logs.all():
                self.assertIn("temba", json.dumps(log.get_display()))
                self.assertNotIn("tembapasswd", json.dumps(log.get_display()))
                self.assertNotIn("dGVtYmE6dGVtYmFwYXNzd2Q=", json.dumps(log.get_display()))
            for log in channel2.http_logs.all():
                self.assertIn("temba", json.dumps(log.get_display()))
                self.assertNotIn("tembapasswd", json.dumps(log.get_display()))
                self.assertNotIn("dGVtYmE6dGVtYmFwYXNzd2Q=", json.dumps(log.get_display()))

    @patch("socket.gethostbyname", return_value="123.123.123.123")
    @patch("temba.channels.types.whatsapp_legacy.WhatsAppLegacyType.check_health")
    def test_claim_self_hosted_templates(self, mock_health, mock_socket_hostname):
        mock_health.return_value = MockResponse(200, '{"meta": {"api_status": "stable", "version": "v2.35.2"}}')
        Channel.objects.all().delete()

        url = reverse("channels.types.whatsapp_legacy.claim")
        self.login(self.admin)

        response = self.client.get(reverse("channels.channel_claim"))
        self.assertNotContains(response, url)

        response = self.client.get(url)
        self.assertEqual(200, response.status_code)
        post_data = response.context["form"].initial

        post_data["address"] = "0788123123"
        post_data["username"] = "temba"
        post_data["password"] = "tembapasswd"
        post_data["country"] = "RW"
        post_data["base_url"] = "https://textit.com/whatsapp"
        post_data["facebook_namespace"] = "my-custom-app"
        post_data["facebook_business_id"] = "1234"
        post_data["facebook_access_token"] = "token123"
        post_data["facebook_template_list_domain"] = "example.org"
        post_data["facebook_template_list_api_version"] = "v3.3"

        with patch("requests.post") as mock_post, patch("requests.get") as mock_get:
            mock_post.return_value = MockResponse(200, '{"users": [{"token": "abc123"}]}')
            mock_get.return_value = MockResponse(400, '{"data": []}')

            response = self.client.post(url, post_data)
            self.assertEqual(200, response.status_code)
            self.assertFalse(Channel.objects.all())
            mock_get.assert_called_with(
                "https://example.org/v3.3/1234/message_templates", params={"access_token": "token123"}
            )

            self.assertContains(response, "check user id and access token")

        # success claim
        with patch("requests.post") as mock_post, patch("requests.get") as mock_get, patch(
            "requests.patch"
        ) as mock_patch:
            mock_post.return_value = MockResponse(200, '{"users": [{"token": "abc123"}]}')
            mock_get.return_value = MockResponse(200, '{"data": []}')
            mock_patch.return_value = MockResponse(200, '{"data": []}')

            response = self.client.post(url, post_data)
            self.assertEqual(302, response.status_code)
            mock_get.assert_called_with(
                "https://example.org/v3.3/1234/message_templates", params={"access_token": "token123"}
            )

        channel = Channel.objects.get()

        self.assertEqual("example.org", channel.config[CONFIG_FB_TEMPLATE_LIST_DOMAIN])
        self.assertEqual("temba", channel.config[Channel.CONFIG_USERNAME])
        self.assertEqual("tembapasswd", channel.config[Channel.CONFIG_PASSWORD])
        self.assertEqual("abc123", channel.config[Channel.CONFIG_AUTH_TOKEN])
        self.assertEqual("https://textit.com/whatsapp", channel.config[Channel.CONFIG_BASE_URL])
        self.assertEqual("v3.3", channel.config[CONFIG_FB_TEMPLATE_API_VERSION])

        self.assertEqual("+250788123123", channel.address)
        self.assertEqual("RW", channel.country)
        self.assertEqual("WA", channel.channel_type)
        self.assertEqual(45, channel.tps)

    @patch("requests.get")
    def test_fetch_templates(self, mock_get):
        channel = self.create_channel(
            "WA",
            "WhatsApp: 1234",
            "1234",
            config={
                Channel.CONFIG_BASE_URL: "https://textit.com/whatsapp",
                Channel.CONFIG_USERNAME: "temba",
                Channel.CONFIG_PASSWORD: "tembapasswd",
                Channel.CONFIG_AUTH_TOKEN: "authtoken123",
                CONFIG_FB_BUSINESS_ID: "1234",
                CONFIG_FB_ACCESS_TOKEN: "token123",
                CONFIG_FB_NAMESPACE: "my-custom-app",
                CONFIG_FB_TEMPLATE_LIST_DOMAIN: "graph.facebook.com",
            },
        )

        mock_get.side_effect = [
            RequestException("Network is unreachable", response=MockResponse(100, "")),
            MockResponse(400, '{ "meta": { "success": false } }'),
            MockResponse(200, '{"data": ["foo", "bar"]}'),
            MockResponse(
                200,
                '{"data": ["foo"], "paging": {"next": "https://graph.facebook.com/v14.0/1234/message_templates?cursor=MjQZD"} }',
            ),
            MockResponse(200, '{"data": ["bar"], "paging": {"next": null} }'),
        ]

        with self.assertRaises(RequestException):
            channel.type.fetch_templates(channel)

        self.assertEqual(1, HTTPLog.objects.filter(log_type=HTTPLog.WHATSAPP_TEMPLATES_SYNCED, is_error=True).count())

        with self.assertRaises(RequestException):
            channel.type.fetch_templates(channel)

        self.assertEqual(2, HTTPLog.objects.filter(log_type=HTTPLog.WHATSAPP_TEMPLATES_SYNCED, is_error=True).count())

        # check when no next page
        templates = channel.type.fetch_templates(channel)
        self.assertEqual(["foo", "bar"], templates)

        self.assertEqual(2, HTTPLog.objects.filter(log_type=HTTPLog.WHATSAPP_TEMPLATES_SYNCED, is_error=True).count())
        self.assertEqual(1, HTTPLog.objects.filter(log_type=HTTPLog.WHATSAPP_TEMPLATES_SYNCED, is_error=False).count())

        # check admin token is redacted in HTTP logs
        for log in HTTPLog.objects.all():
            self.assertNotIn("token123", json.dumps(log.get_display()))

        mock_get.assert_called_with(
            "https://graph.facebook.com/v14.0/1234/message_templates",
            params={"access_token": "token123", "limit": 255},
        )

        # check when templates across two pages
        templates = channel.type.fetch_templates(channel)
        self.assertEqual(["foo", "bar"], templates)

        mock_get.assert_has_calls(
            [
                call(
                    "https://graph.facebook.com/v14.0/1234/message_templates",
                    params={"access_token": "token123", "limit": 255},
                ),
                call(
                    "https://graph.facebook.com/v14.0/1234/message_templates?cursor=MjQZD",
                    params={"access_token": "token123", "limit": 255},
                ),
            ]
        )

    def test_check_health(self):
        channel = self.create_channel(
            "WA",
            "WhatsApp: 1234",
            "1234",
            config={
                Channel.CONFIG_BASE_URL: "https://textit.com/whatsapp",
                Channel.CONFIG_USERNAME: "temba",
                Channel.CONFIG_PASSWORD: "tembapasswd",
                Channel.CONFIG_AUTH_TOKEN: "authtoken123",
                CONFIG_FB_BUSINESS_ID: "1234",
                CONFIG_FB_ACCESS_TOKEN: "token123",
                CONFIG_FB_NAMESPACE: "my-custom-app",
                CONFIG_FB_TEMPLATE_LIST_DOMAIN: "graph.facebook.com",
            },
        )

        with patch("requests.get") as mock_get:
            mock_get.side_effect = [
                RequestException("Network is unreachable", response=MockResponse(100, "")),
                MockResponse(200, '{"meta": {"api_status": "stable", "version": "v2.35.2"}}'),
                MockResponse(401, ""),
            ]

            with patch("logging.Logger.debug") as mock_log_debug:
                channel.type.check_health(channel)
                self.assertEqual(1, mock_log_debug.call_count)
                self.assertEqual(
                    "Could not establish a connection with the WhatsApp server: Network is unreachable",
                    mock_log_debug.call_args[0][0],
                )

            channel.type.check_health(channel)
            mock_get.assert_called_with(
                "https://textit.com/whatsapp/v1/health", headers={"Authorization": "Bearer authtoken123"}
            )

            with patch("logging.Logger.debug") as mock_log_debug:
                channel.type.check_health(channel)
                self.assertEqual(1, mock_log_debug.call_count)
                self.assertEqual(
                    "Error checking API health: b''",
                    mock_log_debug.call_args[0][0],
                )
