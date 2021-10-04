from unittest.mock import call, patch

from django_redis import get_redis_connection
from requests import RequestException

from django.forms import ValidationError
from django.urls import reverse

from temba.request_logs.models import HTTPLog
from temba.templates.models import TemplateTranslation
from temba.tests import MockResponse, TembaTest
from temba.utils.whatsapp.tasks import refresh_whatsapp_contacts, refresh_whatsapp_templates

from ...models import Channel
from .tasks import refresh_whatsapp_tokens
from .type import (
    CONFIG_FB_ACCESS_TOKEN,
    CONFIG_FB_BUSINESS_ID,
    CONFIG_FB_NAMESPACE,
    CONFIG_FB_TEMPLATE_LIST_DOMAIN,
    WhatsAppType,
)


class WhatsAppTypeTest(TembaTest):
    @patch("temba.channels.types.whatsapp.WhatsAppType.check_health")
    def test_claim(self, mock_health):
        mock_health.return_value = MockResponse(200, '{"meta": {"api_status": "stable", "version": "v2.35.2"}}')
        TemplateTranslation.objects.all().delete()
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
        #             "https://graph.facebook.com/v3.3/1234/message_templates", params={"access_token": "token123"}
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
        refresh_whatsapp_templates()

        # deactivate our channel
        channel.release(self.admin)

    def test_refresh_tokens(self):
        TemplateTranslation.objects.all().delete()
        Channel.objects.all().delete()

        channel = self.create_channel(
            "WA",
            "WhatsApp: 1234",
            "1234",
            config={
                Channel.CONFIG_BASE_URL: "https://nyaruka.com/whatsapp",
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
                Channel.CONFIG_BASE_URL: "https://nyaruka.com/whatsapp",
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

        with patch("requests.post") as mock_post:
            mock_post.side_effect = [MockResponse(200, ""), MockResponse(200, '{"users": [{"token": "abc098"}]}')]
            refresh_whatsapp_tokens()

            channel.refresh_from_db()
            channel2.refresh_from_db()
            self.assertEqual("abc345", channel.config[Channel.CONFIG_AUTH_TOKEN])
            self.assertEqual("abc098", channel2.config[Channel.CONFIG_AUTH_TOKEN])

    @patch("temba.channels.types.whatsapp.WhatsAppType.check_health")
    def test_claim_self_hosted_templates(self, mock_health):
        mock_health.return_value = MockResponse(200, '{"meta": {"api_status": "stable", "version": "v2.35.2"}}')
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
        self.assertEqual("https://nyaruka.com/whatsapp", channel.config[Channel.CONFIG_BASE_URL])

        self.assertEqual("+250788123123", channel.address)
        self.assertEqual("RW", channel.country)
        self.assertEqual("WA", channel.channel_type)
        self.assertEqual(45, channel.tps)
        self.assertTrue(channel.get_type().has_attachment_support(channel))

    @patch("requests.get")
    def test_get_api_templates(self, mock_get):
        TemplateTranslation.objects.all().delete()
        Channel.objects.all().delete()

        channel = self.create_channel(
            "WA",
            "WhatsApp: 1234",
            "1234",
            config={
                Channel.CONFIG_BASE_URL: "https://nyaruka.com/whatsapp",
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
                '{"data": ["foo"], "paging": {"next": "https://graph.facebook.com/v3.3/1234/message_templates?cursor=MjQZD"} }',
            ),
            MockResponse(200, '{"data": ["bar"], "paging": {"next": null} }'),
        ]

        # RequestException check HTTPLog
        templates_data, no_error = WhatsAppType().get_api_templates(channel)
        self.assertEqual(1, HTTPLog.objects.filter(log_type=HTTPLog.WHATSAPP_TEMPLATES_SYNCED).count())
        self.assertFalse(no_error)
        self.assertEqual([], templates_data)

        # should be empty list with an error flag if fail with API
        templates_data, no_error = WhatsAppType().get_api_templates(channel)
        self.assertFalse(no_error)
        self.assertEqual([], templates_data)

        # success no error and list
        templates_data, no_error = WhatsAppType().get_api_templates(channel)
        self.assertTrue(no_error)
        self.assertEqual(["foo", "bar"], templates_data)

        mock_get.assert_called_with(
            "https://graph.facebook.com/v3.3/1234/message_templates",
            params={"access_token": "token123", "limit": 255},
        )

        # success no error and pagination
        templates_data, no_error = WhatsAppType().get_api_templates(channel)
        self.assertTrue(no_error)
        self.assertEqual(["foo", "bar"], templates_data)

        mock_get.assert_has_calls(
            [
                call(
                    "https://graph.facebook.com/v3.3/1234/message_templates",
                    params={"access_token": "token123", "limit": 255},
                ),
                call(
                    "https://graph.facebook.com/v3.3/1234/message_templates?cursor=MjQZD",
                    params={"access_token": "token123", "limit": 255},
                ),
            ]
        )

    @patch("temba.channels.types.whatsapp.WhatsAppType.check_health")
    @patch("temba.utils.whatsapp.tasks.update_local_templates")
    @patch("temba.channels.types.whatsapp.WhatsAppType.get_api_templates")
    def test_refresh_templates_task(self, mock_get_api_templates, update_local_templates_mock, mock_health):
        TemplateTranslation.objects.all().delete()
        Channel.objects.all().delete()

        # channel has namespace in the channel config
        channel = self.create_channel(
            "WA",
            "Channel",
            "1234",
            config={
                "fb_namespace": "foo_namespace",
                Channel.CONFIG_BASE_URL: "https://nyaruka.com/whatsapp",
            },
        )

        self.login(self.admin)
        mock_get_api_templates.side_effect = [
            ([], False),
            Exception("foo"),
            ([{"name": "hello"}], True),
            ([{"name": "hello"}], True),
        ]
        mock_health.return_value = MockResponse(200, '{"meta": {"api_status": "stable", "version": "v2.35.2"}}')
        update_local_templates_mock.return_value = None

        # should skip if locked
        r = get_redis_connection()
        with r.lock("refresh_whatsapp_templates", timeout=1800):
            refresh_whatsapp_templates()
            self.assertEqual(0, mock_get_api_templates.call_count)
            self.assertEqual(0, update_local_templates_mock.call_count)

        # should skip if fail with API
        refresh_whatsapp_templates()

        mock_get_api_templates.assert_called_with(channel)
        self.assertEqual(1, mock_get_api_templates.call_count)
        self.assertEqual(0, update_local_templates_mock.call_count)
        self.assertFalse(mock_health.called)

        # any exception
        refresh_whatsapp_templates()

        mock_get_api_templates.assert_called_with(channel)
        self.assertEqual(2, mock_get_api_templates.call_count)
        self.assertEqual(0, update_local_templates_mock.call_count)
        self.assertFalse(mock_health.called)

        # now it should refresh
        refresh_whatsapp_templates()

        mock_get_api_templates.assert_called_with(channel)
        self.assertEqual(3, mock_get_api_templates.call_count)
        update_local_templates_mock.assert_called_once_with(channel, [{"name": "hello"}])
        self.assertFalse(mock_health.called)

        channel.config.update(version="v1.0.0")
        channel.save()

        channel.refresh_from_db()

        # now it should refresh
        refresh_whatsapp_templates()

        mock_get_api_templates.assert_called_with(channel)
        self.assertEqual(4, mock_get_api_templates.call_count)
        self.assertTrue(mock_health.called)

        channel.refresh_from_db()

        self.assertEqual("v2.35.2", channel.config.get("version"))

    def test_message_templates_and_logs_views(self):
        channel = self.create_channel("WA", "Channel", "1234", config={"fb_namespace": "foo_namespace"})

        TemplateTranslation.get_or_create(
            channel,
            "hello",
            "eng",
            "US",
            "Hello {{1}}",
            1,
            TemplateTranslation.STATUS_APPROVED,
            "1234",
            "foo_namespace",
        )

        self.login(self.admin)
        # hit our template page
        response = self.client.get(reverse("channels.types.whatsapp.templates", args=[channel.uuid]))
        # should have our template translations
        self.assertContains(response, "Hello")
        self.assertContains(response, reverse("channels.types.whatsapp.sync_logs", args=[channel.uuid]))

        # Check if message templates link are in sync_logs view
        response = self.client.get(reverse("channels.types.whatsapp.sync_logs", args=[channel.uuid]))
        gear_links = response.context["view"].get_gear_links()
        self.assertEqual(gear_links[-1]["title"], "Message Templates")
        self.assertEqual(gear_links[-1]["href"], reverse("channels.types.whatsapp.templates", args=[channel.uuid]))

        # sync logs and message templates not accessible by user from other org
        self.login(self.admin2)
        response = self.client.get(reverse("channels.types.whatsapp.templates", args=[channel.uuid]))
        self.assertEqual(404, response.status_code)
        response = self.client.get(reverse("channels.types.whatsapp.sync_logs", args=[channel.uuid]))
        self.assertEqual(404, response.status_code)

    def test_check_health(self):
        channel = self.create_channel(
            "WA",
            "WhatsApp: 1234",
            "1234",
            config={
                Channel.CONFIG_BASE_URL: "https://nyaruka.com/whatsapp",
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

            with self.assertRaises(Exception):
                channel.get_type().check_health(channel)

            channel.get_type().check_health(channel)
            mock_get.assert_called_with(
                "https://nyaruka.com/whatsapp/v1/health", headers={"Authorization": "Bearer authtoken123"}
            )
            with self.assertRaises(Exception):
                channel.get_type().check_health(channel)
