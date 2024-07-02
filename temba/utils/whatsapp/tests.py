from unittest.mock import patch

import requests

from temba.channels.models import Channel
from temba.channels.types.whatsapp_legacy.type import (
    CONFIG_FB_ACCESS_TOKEN,
    CONFIG_FB_BUSINESS_ID,
    CONFIG_FB_NAMESPACE,
    CONFIG_FB_TEMPLATE_LIST_DOMAIN,
)
from temba.request_logs.models import HTTPLog
from temba.tests import TembaTest
from temba.tests.requests import MockResponse

from . import update_api_version


class WhatsAppUtilsTest(TembaTest):
    @patch("temba.channels.types.whatsapp_legacy.WhatsAppLegacyType.check_health")
    def test_update_api_version_whatsapp(self, mock_health):
        mock_health.return_value = MockResponse(200, '{"meta": {"api_status": "stable", "version": "v2.35.2"}}')

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

        update_api_version(channel)
        mock_health.assert_called_with(channel)

        channel.refresh_from_db()
        self.assertEqual("v2.35.2", channel.config.get("version"))

        self.assertEqual(0, HTTPLog.objects.filter(log_type=HTTPLog.WHATSAPP_CHECK_HEALTH).count())
        mock_health.side_effect = [requests.RequestException(response=MockResponse(401, "{}"))]
        update_api_version(channel)
        self.assertEqual(1, HTTPLog.objects.filter(log_type=HTTPLog.WHATSAPP_CHECK_HEALTH).count())

    @patch("temba.channels.types.dialog360_legacy.Dialog360LegacyType.check_health")
    def test_update_api_version_dialog360(self, mock_health):
        mock_health.return_value = MockResponse(200, '{"meta": {"api_status": "stable", "version": "2.35.4"}}')

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

        update_api_version(channel)
        mock_health.assert_called_with(channel)

        channel.refresh_from_db()
        self.assertEqual("v2.35.4", channel.config.get("version"))

        self.assertEqual(0, HTTPLog.objects.filter(log_type=HTTPLog.WHATSAPP_CHECK_HEALTH).count())
        mock_health.side_effect = [requests.RequestException(response=MockResponse(401, "{}"))]
        update_api_version(channel)
        self.assertEqual(1, HTTPLog.objects.filter(log_type=HTTPLog.WHATSAPP_CHECK_HEALTH).count())
