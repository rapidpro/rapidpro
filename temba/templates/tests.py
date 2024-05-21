from unittest.mock import patch
from zoneinfo import ZoneInfo

import requests

from django.urls import reverse

from temba.channels.models import Channel
from temba.notifications.incidents.builtin import ChannelTemplatesFailedIncidentType
from temba.notifications.models import Incident
from temba.orgs.models import Org, OrgRole
from temba.request_logs.models import HTTPLog
from temba.tests import CRUDLTestMixin, TembaTest

from .models import Template, TemplateTranslation
from .tasks import refresh_templates


class TemplateTest(TembaTest):
    def test_model(self):
        channel1 = self.create_channel("WA", "Channel 1", "1234")
        channel2 = self.create_channel("WA", "Channel 2", "2345")

        hello_eng = TemplateTranslation.get_or_create(
            channel1,
            "hello",
            locale="eng-US",
            status=TemplateTranslation.STATUS_PENDING,
            external_id="1234",
            external_locale="en_US",
            namespace="",
            components=[],
            variables=[],
        )
        self.assertIsNotNone(hello_eng.template)  # should have a template with name hello
        self.assertEqual("hello", hello_eng.template.name)

        modified_on = hello_eng.template.modified_on

        hello_fra = TemplateTranslation.get_or_create(
            channel1,
            "hello",
            locale="fra-FR",
            status=TemplateTranslation.STATUS_PENDING,
            external_id="5678",
            external_locale="fr_FR",
            namespace="",
            components=[],
            variables=[],
        )
        self.assertEqual(hello_fra.template, hello_fra.template)
        self.assertGreater(hello_fra.template.modified_on, modified_on)  # should be updated

        goodbye_fra = TemplateTranslation.get_or_create(
            channel1,
            "goodbye",
            locale="fra-FR",
            status=TemplateTranslation.STATUS_PENDING,
            external_id="6789",
            external_locale="fr_FR",
            namespace="foo_namespace",
            components=[],
            variables=[],
        )
        self.assertNotEqual(hello_fra.template, goodbye_fra.template)
        self.assertTrue("goodbye", goodbye_fra.template.name)

        goodbye_fra_other_channel = TemplateTranslation.get_or_create(
            channel2,
            "goodbye",
            locale="fra-FR",
            status=TemplateTranslation.STATUS_PENDING,
            external_id="6789",
            external_locale="fr_FR",
            namespace="foo_namespace",
            components=[],
            variables=[],
        )
        self.assertNotEqual(goodbye_fra, goodbye_fra_other_channel)
        self.assertEqual(goodbye_fra.template, goodbye_fra_other_channel.template)

        self.assertEqual(2, Template.objects.filter(org=self.org).count())
        self.assertEqual(3, TemplateTranslation.objects.filter(channel=channel1).count())
        self.assertEqual(1, TemplateTranslation.objects.filter(channel=channel2).count())

        # trim them
        TemplateTranslation.trim(channel1, [hello_eng, hello_fra])

        # non-included translations should be inactive now
        hello_eng.refresh_from_db()
        self.assertTrue(hello_eng.is_active)
        hello_fra.refresh_from_db()
        self.assertTrue(hello_fra.is_active)
        goodbye_fra.refresh_from_db()
        self.assertFalse(goodbye_fra.is_active)

        # but not for other channels
        goodbye_fra_other_channel.refresh_from_db()
        self.assertTrue(hello_eng.is_active)

    def test_update_local(self):
        channel = self.create_channel("WA", "Channel 1", "1234")

        TemplateTranslation.update_local(
            channel,
            [
                {
                    "name": "hello",
                    "components": [{"type": "BODY", "text": "Hello"}],
                    "language": "en",
                    "status": "APPROVED",
                    "id": "1234",
                },
                {
                    "name": "hello",
                    "components": [{"type": "BODY", "text": "Hola"}],
                    "language": "es",
                    "status": "PENDING",
                    "id": "2345",
                },
                {
                    "name": "goodbye",
                    "components": [{"type": "BODY", "text": "Goodbye"}],
                    "language": "en",
                    "status": "PENDING",
                    "id": "3456",
                },
            ],
        )

        self.assertEqual({"hello", "goodbye"}, set(Template.objects.values_list("name", flat=True)))
        self.assertEqual(3, TemplateTranslation.objects.filter(channel=channel, is_active=True).count())

        TemplateTranslation.update_local(
            channel,
            [
                {
                    "name": "hello",
                    "components": [{"type": "BODY", "text": "Hello"}],
                    "language": "en",
                    "status": "APPROVED",
                    "id": "1234",
                }
            ],
        )

        self.assertEqual({"hello", "goodbye"}, set(Template.objects.values_list("name", flat=True)))
        self.assertEqual(1, TemplateTranslation.objects.filter(channel=channel, is_active=True).count())
        self.assertEqual(2, TemplateTranslation.objects.filter(channel=channel, is_active=False).count())

    @patch("temba.templates.models.TemplateTranslation.update_local")
    @patch("temba.channels.types.twilio_whatsapp.TwilioWhatsappType.fetch_templates")
    @patch("temba.channels.types.dialog360.Dialog360Type.fetch_templates")
    @patch("temba.channels.types.dialog360_legacy.Dialog360LegacyType.fetch_templates")
    def test_refresh_task(
        self, mock_d3_fetch_templates, mock_d3c_fetch_templates, mock_twa_fetch_templates, mock_update_local
    ):
        org3 = Org.objects.create(
            name="Nyaruka 3",
            timezone=ZoneInfo("Africa/Kigali"),
            flow_languages=["eng", "kin"],
            created_by=self.admin,
            modified_by=self.admin,
        )
        org3.initialize()
        org3.add_user(self.admin, OrgRole.ADMINISTRATOR)
        org3.suspend()

        org4 = Org.objects.create(
            name="Nyaruka 4",
            timezone=ZoneInfo("Africa/Kigali"),
            flow_languages=["eng", "kin"],
            created_by=self.admin,
            modified_by=self.admin,
        )
        org4.initialize()
        org4.add_user(self.admin, OrgRole.ADMINISTRATOR)
        org4.release(self.admin)

        # channels on suspended org are ignored
        self.create_channel(
            "D3",
            "360Dialog channel",
            address="234",
            country="BR",
            config={
                Channel.CONFIG_BASE_URL: "https://example.com/whatsapp",
                Channel.CONFIG_AUTH_TOKEN: "123456789",
            },
            org=org3,
        )

        # channels on inactive org are ignored
        self.create_channel(
            "D3",
            "360Dialog channel",
            address="345",
            country="BR",
            config={
                Channel.CONFIG_BASE_URL: "https://example.com/whatsapp",
                Channel.CONFIG_AUTH_TOKEN: "123456789",
            },
            org=org4,
        )

        d3c_channel = self.create_channel(
            "D3C",
            "360Dialog channel",
            address="1234",
            country="BR",
            config={
                Channel.CONFIG_BASE_URL: "https://waba-v2.360dialog.io",
                Channel.CONFIG_AUTH_TOKEN: "123456789",
            },
        )
        self.create_channel(
            "D3",
            "360Dialog channel",
            address="1234",
            country="BR",
            config={
                Channel.CONFIG_BASE_URL: "https://example.com/whatsapp",
                Channel.CONFIG_AUTH_TOKEN: "123456789",
            },
        )

        self.create_channel(
            "TWA",
            "TWilio WhatsAPp channel",
            address="1234",
            country="US",
            config={
                Channel.CONFIG_BASE_URL: "https://example.com/whatsapp",
                Channel.CONFIG_AUTH_TOKEN: "123456789",
            },
        )

        def mock_fetch(ch):
            HTTPLog.objects.create(
                org=ch.org, channel=ch, log_type=HTTPLog.WHATSAPP_TEMPLATES_SYNCED, request_time=0, is_error=False
            )
            return [{"name": "hello"}]

        def mock_fail_fetch(ch):
            HTTPLog.objects.create(
                org=ch.org, channel=ch, log_type=HTTPLog.WHATSAPP_TEMPLATES_SYNCED, request_time=0, is_error=True
            )
            raise requests.ConnectionError("timeout")

        mock_d3_fetch_templates.side_effect = mock_fetch
        mock_d3c_fetch_templates.side_effect = mock_fetch
        mock_twa_fetch_templates.side_effect = mock_fetch
        mock_update_local.return_value = None

        refresh_templates()

        self.assertEqual(1, mock_d3_fetch_templates.call_count)
        self.assertEqual(1, mock_d3c_fetch_templates.call_count)
        self.assertEqual(1, mock_twa_fetch_templates.call_count)
        self.assertEqual(3, mock_update_local.call_count)
        self.assertEqual(0, Incident.objects.filter(incident_type=ChannelTemplatesFailedIncidentType.slug).count())

        # if one channel fails, others continue
        mock_d3c_fetch_templates.side_effect = mock_fail_fetch

        refresh_templates()

        self.assertEqual(2, mock_d3_fetch_templates.call_count)
        self.assertEqual(2, mock_d3c_fetch_templates.call_count)
        self.assertEqual(2, mock_twa_fetch_templates.call_count)
        self.assertEqual(5, mock_update_local.call_count)

        # one failure isn't enough to create an incident
        self.assertEqual(0, Incident.objects.filter(incident_type=ChannelTemplatesFailedIncidentType.slug).count())

        # but 5 will be
        refresh_templates()
        refresh_templates()
        refresh_templates()
        refresh_templates()

        self.assertEqual(
            1,
            Incident.objects.filter(
                incident_type=ChannelTemplatesFailedIncidentType.slug, channel=d3c_channel, ended_on=None
            ).count(),
        )

        # a successful fetch will clear it
        mock_d3c_fetch_templates.side_effect = mock_fetch

        refresh_templates()

        self.assertEqual(
            0,
            Incident.objects.filter(
                incident_type=ChannelTemplatesFailedIncidentType.slug, channel=d3c_channel, ended_on=None
            ).count(),
        )

        # other exception logged to sentry
        mock_d3c_fetch_templates.side_effect = Exception("boom")
        with patch("logging.Logger.error") as mock_log_error:
            refresh_templates()
            self.assertEqual(1, mock_log_error.call_count)
            self.assertEqual("Error refreshing whatsapp templates: boom", mock_log_error.call_args[0][0])


class TemplateTranslationCRUDLTest(CRUDLTestMixin, TembaTest):
    def test_channel(self):
        channel = self.create_channel("D3C", "360Dialog channel", address="1234", country="BR")
        tt1 = TemplateTranslation.get_or_create(
            channel,
            "hello",
            locale="eng-US",
            status=TemplateTranslation.STATUS_APPROVED,
            external_id="1234",
            external_locale="en_US",
            namespace="foo_namespace",
            components=[
                {
                    "type": "body",
                    "name": "body",
                    "content": "Hello {{1}}",
                    "variables": {"1": 0},
                    "params": [{"type": "text"}],
                }
            ],
            variables=[{"type": "text"}],
        )
        tt2 = TemplateTranslation.get_or_create(
            channel,
            "goodbye",
            locale="eng-US",
            status=TemplateTranslation.STATUS_PENDING,
            external_id="2345",
            external_locale="en_US",
            namespace="foo_namespace",
            components=[
                {
                    "type": "body",
                    "name": "body",
                    "content": "Goodbye {{1}}",
                    "variables": {"1": 0},
                    "params": [{"type": "text"}],
                }
            ],
            variables=[{"type": "text"}],
        )

        # and one for another channel
        TemplateTranslation.get_or_create(
            self.channel,
            "hello",
            locale="eng-US",
            status=TemplateTranslation.STATUS_PENDING,
            external_id="5678",
            external_locale="en_US",
            namespace="foo_namespace",
            components=[
                {
                    "type": "body",
                    "name": "body",
                    "content": "Goodbye {{1}}",
                    "variables": {"1": 0},
                    "params": [{"type": "text"}],
                }
            ],
            variables=[{"type": "text"}],
        )

        channel_url = reverse("templates.templatetranslation_channel", args=[channel.uuid])

        self.assertRequestDisallowed(channel_url, [None, self.agent, self.admin2])
        response = self.assertListFetch(channel_url, [self.user, self.editor, self.admin], context_objects=[tt2, tt1])

        self.assertContains(response, "Hello")
        self.assertContentMenu(channel_url, self.admin, ["Sync Logs"])

        response = self.client.get(reverse("templates.templatetranslation_channel", args=["1234567890-1234"]))
        self.assertEqual(404, response.status_code)
