from unittest.mock import patch

from django_redis import get_redis_connection
from requests import RequestException

from django.forms import ValidationError
from django.urls import reverse

from temba.request_logs.models import HTTPLog
from temba.templates.models import TemplateTranslation
from temba.tests import CRUDLTestMixin, MockResponse, TembaTest
from temba.utils.whatsapp.tasks import refresh_whatsapp_templates

from ...models import Channel
from .type import Dialog360CloudType


class Dialog360CloudTypeTest(CRUDLTestMixin, TembaTest):
    def test_claim(self):
        Channel.objects.all().delete()

        url = reverse("channels.types.dialog360_cloud.claim")
        self.login(self.admin)

        # make sure 360dialog Cloud is on the claim page
        response = self.client.get(reverse("channels.channel_claim"), follow=True)
        self.assertContains(response, url)

        # should see the general channel claim page
        response = self.client.get(reverse("channels.channel_claim"))
        self.assertContains(response, url)

        response = self.client.get(url)
        self.assertEqual(200, response.status_code)
        post_data = response.context["form"].initial

        post_data["number"] = "1234"
        post_data["country"] = "RW"
        post_data["api_key"] = "123456789"

        # will fail with invalid phone number
        response = self.client.post(url, post_data)
        self.assertFormError(response, "form", None, ["Please enter a valid phone number"])

        # valid number
        post_data["number"] = "0788123123"

        # then success
        with patch("socket.gethostbyname", return_value="123.123.123.123"), patch("requests.post") as mock_post:
            mock_post.side_effect = [MockResponse(200, '{ "url": "https://waba-v2.360dialog.io" }')]

            response = self.client.post(url, post_data)
            self.assertEqual(302, response.status_code)

        channel = Channel.objects.get()

        self.assertEqual("123456789", channel.config[Channel.CONFIG_AUTH_TOKEN])
        self.assertEqual("https://waba-v2.360dialog.io", channel.config[Channel.CONFIG_BASE_URL])

        self.assertEqual("+250788123123", channel.address)
        self.assertEqual("RW", channel.country)
        self.assertEqual("D3C", channel.channel_type)
        self.assertEqual(45, channel.tps)

        # test activating the channel
        with patch("requests.post") as mock_post:
            mock_post.side_effect = [MockResponse(200, '{ "url": "https://waba-v2.360dialog.io" }')]

            Dialog360CloudType().activate(channel)
            self.assertEqual(
                mock_post.call_args_list[0][1]["json"]["url"],
                "https://%s%s"
                % (channel.org.get_brand_domain(), reverse("courier.d3c", args=[channel.uuid, "receive"])),
            )

        with patch("requests.post") as mock_post:
            mock_post.side_effect = [MockResponse(400, '{ "meta": { "success": false } }')]

            try:
                Dialog360CloudType().activate(channel)
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
            "D3C",
            "360Dialog channel",
            address="1234",
            country="BR",
            config={
                Channel.CONFIG_BASE_URL: "https://waba-v2.360dialog.io",
                Channel.CONFIG_AUTH_TOKEN: "123456789",
            },
        )

        mock_get.side_effect = [
            RequestException("Network is unreachable", response=MockResponse(100, "")),
            MockResponse(400, '{ "meta": { "success": false } }'),
            MockResponse(200, '{"waba_templates": ["foo", "bar"]}'),
        ]

        # RequestException check HTTPLog
        templates_data, no_error = Dialog360CloudType().get_api_templates(channel)
        self.assertEqual(1, HTTPLog.objects.filter(log_type=HTTPLog.WHATSAPP_TEMPLATES_SYNCED).count())
        self.assertFalse(no_error)
        self.assertEqual([], templates_data)

        # should be empty list with an error flag if fail with API
        templates_data, no_error = Dialog360CloudType().get_api_templates(channel)
        self.assertFalse(no_error)
        self.assertEqual([], templates_data)

        # success no error and list
        templates_data, no_error = Dialog360CloudType().get_api_templates(channel)
        self.assertTrue(no_error)
        self.assertEqual(["foo", "bar"], templates_data)

        mock_get.assert_called_with(
            "https://waba-v2.360dialog.io/v1/configs/templates",
            headers={
                "D360-API-KEY": channel.config[Channel.CONFIG_AUTH_TOKEN],
                "Content-Type": "application/json",
            },
        )

    @patch("temba.utils.whatsapp.tasks.update_local_templates")
    @patch("temba.channels.types.dialog360_cloud.Dialog360CloudType.get_api_templates")
    def test_refresh_templates_task(self, mock_get_api_templates, update_local_templates_mock):
        TemplateTranslation.objects.all().delete()
        Channel.objects.all().delete()

        channel = self.create_channel(
            "D3C",
            "360Dialog channel",
            address="1234",
            country="BR",
            config={
                Channel.CONFIG_BASE_URL: "https://waba-v2.360dialog.io",
                Channel.CONFIG_AUTH_TOKEN: "123456789",
            },
        )

        self.login(self.admin)
        mock_get_api_templates.side_effect = [([], False), Exception("foo"), ([{"name": "hello"}], True)]

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
            "D3C",
            "360Dialog channel",
            address="1234",
            country="BR",
            config={
                Channel.CONFIG_BASE_URL: "https://waba-v2.360dialog.io",
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

        sync_url = reverse("channels.types.dialog360_cloud.sync_logs", args=[channel.uuid])
        templates_url = reverse("channels.types.dialog360_cloud.templates", args=[channel.uuid])

        self.login(self.admin)
        response = self.client.get(templates_url)

        # should have our template translations
        self.assertContains(response, "Hello")
        self.assertContentMenu(templates_url, self.admin, ["Sync Logs"])

        # check if message templates link are in sync_logs view
        self.assertContentMenu(sync_url, self.admin, ["Message Templates"])

        # sync logs not accessible by user from other org
        self.login(self.admin2)
        response = self.client.get(templates_url)
        self.assertEqual(404, response.status_code)

        response = self.client.get(sync_url)
        self.assertEqual(404, response.status_code)
