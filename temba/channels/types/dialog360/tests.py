from unittest.mock import patch

from django_redis import get_redis_connection
from requests import RequestException

from django.forms import ValidationError
from django.urls import reverse

from temba.request_logs.models import HTTPLog
from temba.templates.models import TemplateTranslation
from temba.tests import MockResponse, TembaTest
from temba.utils.whatsapp.tasks import refresh_whatsapp_templates

from ...models import Channel
from .type import Dialog360Type


class Dialog360TypeTest(TembaTest):
    @patch("temba.channels.types.dialog360.Dialog360Type.check_health")
    def test_claim(self, mock_health):
        mock_health.return_value = MockResponse(200, '{"meta": {"api_status": "stable", "version": "2.35.4"}}')
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
        with patch("socket.gethostbyname", return_value="123.123.123.123"), patch("requests.post") as mock_post:
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

        # deactivate our channel
        channel.release(self.admin)

    @patch("requests.get")
    def test_get_api_templates(self, mock_get):
        TemplateTranslation.objects.all().delete()
        Channel.objects.all().delete()
        channel = self.create_channel(
            "D3",
            "360Dialog channel",
            address="1234",
            country="BR",
            config={
                Channel.CONFIG_BASE_URL: "https://example.com/whatsapp",
                Channel.CONFIG_AUTH_TOKEN: "123456789",
            },
        )

        mock_get.side_effect = [
            RequestException("Network is unreachable", response=MockResponse(100, "")),
            MockResponse(400, '{ "meta": { "success": false } }'),
            MockResponse(200, '{"waba_templates": ["foo", "bar"]}'),
        ]

        # RequestException check HTTPLog
        templates_data, no_error = Dialog360Type().get_api_templates(channel)
        self.assertEqual(1, HTTPLog.objects.filter(log_type=HTTPLog.WHATSAPP_TEMPLATES_SYNCED).count())
        self.assertFalse(no_error)
        self.assertEqual([], templates_data)

        # should be empty list with an error flag if fail with API
        templates_data, no_error = Dialog360Type().get_api_templates(channel)
        self.assertFalse(no_error)
        self.assertEqual([], templates_data)

        # success no error and list
        templates_data, no_error = Dialog360Type().get_api_templates(channel)
        self.assertTrue(no_error)
        self.assertEqual(["foo", "bar"], templates_data)

        mock_get.assert_called_with(
            "https://example.com/whatsapp/v1/configs/templates",
            headers={
                "D360-API-KEY": channel.config[Channel.CONFIG_AUTH_TOKEN],
                "Content-Type": "application/json",
            },
        )

    @patch("temba.channels.types.dialog360.Dialog360Type.check_health")
    @patch("temba.utils.whatsapp.tasks.update_local_templates")
    @patch("temba.channels.types.dialog360.Dialog360Type.get_api_templates")
    def test_refresh_templates_task(self, mock_get_api_templates, update_local_templates_mock, mock_health):
        TemplateTranslation.objects.all().delete()
        Channel.objects.all().delete()

        channel = self.create_channel(
            "D3",
            "360Dialog channel",
            address="1234",
            country="BR",
            config={
                Channel.CONFIG_BASE_URL: "https://example.com/whatsapp",
                Channel.CONFIG_AUTH_TOKEN: "123456789",
            },
        )

        self.login(self.admin)
        mock_get_api_templates.side_effect = [([], False), Exception("foo"), ([{"name": "hello"}], True)]

        update_local_templates_mock.return_value = None

        mock_health.return_value = MockResponse(200, '{"meta": {"api_status": "stable", "version": "2.35.4"}}')

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

        # any exception
        refresh_whatsapp_templates()

        mock_get_api_templates.assert_called_with(channel)
        self.assertEqual(2, mock_get_api_templates.call_count)
        self.assertEqual(0, update_local_templates_mock.call_count)

        # now it should refresh
        refresh_whatsapp_templates()

        mock_get_api_templates.assert_called_with(channel)
        self.assertEqual(3, mock_get_api_templates.call_count)
        update_local_templates_mock.assert_called_once_with(channel, [{"name": "hello"}])

    def test_message_templates_and_logs_views(self):
        channel = self.create_channel(
            "D3",
            "360Dialog channel",
            address="1234",
            country="BR",
            config={
                Channel.CONFIG_BASE_URL: "https://example.com/whatsapp",
                Channel.CONFIG_AUTH_TOKEN: "123456789",
            },
        )

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
        response = self.client.get(reverse("channels.types.dialog360.templates", args=[channel.uuid]))
        # should have our template translations
        self.assertContains(response, "Hello")
        self.assertContains(response, reverse("channels.types.dialog360.sync_logs", args=[channel.uuid]))

        # Check if message templates link are in sync_logs view
        response = self.client.get(reverse("channels.types.dialog360.sync_logs", args=[channel.uuid]))
        gear_links = response.context["view"].get_gear_links()
        self.assertEqual(gear_links[-1]["title"], "Message Templates")
        self.assertEqual(gear_links[-1]["href"], reverse("channels.types.dialog360.templates", args=[channel.uuid]))

        # sync logs not accessible by user from other org
        self.login(self.admin2)
        response = self.client.get(reverse("channels.types.dialog360.templates", args=[channel.uuid]))
        self.assertEqual(404, response.status_code)

        response = self.client.get(reverse("channels.types.dialog360.sync_logs", args=[channel.uuid]))
        self.assertEqual(404, response.status_code)

    def test_check_health(self):
        channel = self.create_channel(
            "D3",
            "360Dialog channel",
            address="1234",
            country="BR",
            config={
                Channel.CONFIG_BASE_URL: "https://example.com/whatsapp",
                Channel.CONFIG_AUTH_TOKEN: "123456789",
            },
        )
        with patch("requests.get") as mock_get:
            mock_get.side_effect = [
                MockResponse(200, '{"meta": {"api_status": "stable", "version": "2.35.4"}}'),
                MockResponse(401, '{"meta": {"api_status": "stable", "version": "2.35.4"}}'),
            ]
            channel.get_type().check_health(channel)
            mock_get.assert_called_with(
                "https://example.com/whatsapp/v1/health",
                headers={"D360-API-KEY": "123456789", "Content-Type": "application/json"},
            )
            with self.assertRaises(Exception):
                channel.get_type().check_health(channel)
