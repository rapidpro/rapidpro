import json
from unittest.mock import patch

from temba.channels.models import Channel
from temba.templates.models import TemplateTranslation
from temba.tests import TembaTest
from temba.tests.requests import MockResponse

from .type import TwilioType


class WhatsAppTypeTest(TembaTest):
    def setUp(self):
        self.type = TwilioType()

        return super().setUp()

    def test_extract_variables(self):
        self.assertEqual(["1", "2"], self.type._extract_variables("Hi {{2}} how are you? {{1}}"))
        self.assertEqual(["1", "2"], self.type._extract_variables("Hi {{1}} how are you? {{2}} {{1}}"))
        self.assertEqual([], self.type._extract_variables("Hi {there}. {{x}}"))

    def test_parse_language(self):
        self.assertEqual("eng", self.type._parse_language("en"))
        self.assertEqual("eng-US", self.type._parse_language("en_US"))
        self.assertEqual("fil", self.type._parse_language("fil"))

    @patch("requests.get")
    def test_update_local_twa(self, mock_get):
        mock_get.return_value = MockResponse(
            200,
            json.dumps(
                {
                    "whatsapp": {
                        "category": "marketing",
                        "status": "approved",
                    }
                }
            ),
        )

        channel = self.create_channel(
            "TWA",
            "channel",
            "1234",
            config={Channel.CONFIG_ACCOUNT_SID: "account-sid", Channel.CONFIG_AUTH_TOKEN: "auth-token"},
        )

        trans = self.type.update_local(
            channel,
            {
                "friendly_name": "text_only_template",
                "language": "en",
                "links": {"approval_fetch": "https://content.twilio.com/v1/Content/HX1234502/ApprovalRequests"},
                "sid": "HX1234502",
                "types": {
                    "twilio/text": {
                        "body": "Hello {{1}}, this is text example only and can have variables replaces such as {{2}} and {{3}}"
                    },
                    "url": "https://content.twilio.com/v1/Content/HX1234502",
                    "variables": {"1": "for Product A", "2": "features A,B,C", "3": "id123"},
                },
            },
        )
        self.assertIsNotNone(trans)
        self.assertEqual("text_only_template", trans.template.name)
        self.assertEqual(TemplateTranslation.STATUS_APPROVED, trans.status)
        self.assertEqual("", trans.namespace)
        self.assertEqual("eng", trans.locale)
        self.assertEqual("en", trans.external_locale)
        self.assertEqual("HX1234502", trans.external_id)
        self.assertEqual(
            [
                {
                    "type": "body",
                    "name": "body",
                    "content": "Hello {{1}}, this is text example only and can have variables replaces such as {{2}} and {{3}}",
                    "variables": {"1": 0, "2": 1, "3": 2},
                    "params": [{"type": "text"}, {"type": "text"}, {"type": "text"}],
                }
            ],
            trans.components,
        )
        self.assertEqual([{"type": "text"}, {"type": "text"}, {"type": "text"}], trans.variables)
