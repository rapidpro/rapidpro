import json
from unittest.mock import patch

from requests import RequestException

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

        channel = self.create_channel(
            "TWA",
            "channel",
            "1234",
            config={Channel.CONFIG_ACCOUNT_SID: "account-sid", Channel.CONFIG_AUTH_TOKEN: "auth-token"},
        )

        # status unkown, now translation template
        mock_get.side_effect = [
            MockResponse(
                200,
                json.dumps(
                    {
                        "whatsapp": {
                            "status": "unknown",
                        }
                    }
                ),
            ),
            RequestException("Network is unreachable", response=MockResponse(100, "")),
            MockResponse(
                200,
                json.dumps(
                    {
                        "whatsapp": {
                            "status": "approved",
                        }
                    }
                ),
            ),
            MockResponse(
                200,
                json.dumps(
                    {
                        "whatsapp": {
                            "status": "approved",
                        }
                    }
                ),
            ),
            MockResponse(
                200,
                json.dumps(
                    {
                        "whatsapp": {
                            "status": "approved",
                        }
                    }
                ),
            ),
            MockResponse(
                200,
                json.dumps(
                    {
                        "whatsapp": {
                            "status": "approved",
                        }
                    }
                ),
            ),
            MockResponse(
                200,
                json.dumps(
                    {
                        "whatsapp": {
                            "status": "approved",
                        }
                    }
                ),
            ),
        ]

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
        self.assertIsNone(trans)

        # getting status fails with request exception, has pending status
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
        self.assertEqual(TemplateTranslation.STATUS_PENDING, trans.status)

        # status approved
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

        trans = self.type.update_local(
            channel,
            {
                "friendly_name": "media_template",
                "language": "en",
                "links": {"approval_fetch": "https://content.twilio.com/v1/Content/HX1234501/ApprovalRequests"},
                "sid": "HX1234501",
                "types": {
                    "twilio/media": {
                        "body": "Template with media for {{2}} can have a link with variables",
                        "media": ["https://example.com/images/{{1}}.jpg"],
                    },
                },
                "url": "https://content.twilio.com/v1/Content/HX1234501",
                "variables": {"1": "for Product A", "2": "features A,B,C", "3": "id123"},
            },
        )

        self.assertIsNotNone(trans)
        self.assertEqual("media_template", trans.template.name)
        self.assertEqual(TemplateTranslation.STATUS_UNSUPPORTED, trans.status)
        self.assertEqual("", trans.namespace)
        self.assertEqual("eng", trans.locale)
        self.assertEqual("en", trans.external_locale)
        self.assertEqual("HX1234501", trans.external_id)
        self.assertEqual(
            [
                {
                    "type": "body",
                    "name": "body",
                    "content": "Template with media for {{2}} can have a link with variables",
                    "variables": {
                        "2": 0,
                    },
                    "params": [{"type": "text"}],
                }
            ],
            trans.components,
        )
        self.assertEqual([{"type": "text"}], trans.variables)

        trans = self.type.update_local(
            channel,
            {
                "friendly_name": "call_to_action_template",
                "language": "en",
                "links": {"approval_fetch": "https://content.twilio.com/v1/Content/HX1234500/ApprovalRequests"},
                "sid": "HX1234500",
                "types": {
                    "twilio/call-to-action": {
                        "actions": [
                            {"phone": "+12538678447", "title": "Call us", "type": "PHONE_NUMBER"},
                            {
                                "title": "Check site",
                                "type": "URL",
                                "url": "https://example.com/?wa_customer={{3}}",
                            },
                        ],
                        "body": "Call to action {{1}} and {{2}}",
                    }
                },
                "url": "https://content.twilio.com/v1/Content/HX1234500",
                "variables": {"1": "for Product A", "2": "features A,B,C", "3": "id123"},
            },
        )

        self.assertIsNotNone(trans)
        self.assertEqual("call_to_action_template", trans.template.name)
        self.assertEqual(TemplateTranslation.STATUS_APPROVED, trans.status)
        self.assertEqual("", trans.namespace)
        self.assertEqual("eng", trans.locale)
        self.assertEqual("en", trans.external_locale)
        self.assertEqual("HX1234500", trans.external_id)
        self.assertEqual(
            [
                {
                    "type": "body",
                    "name": "body",
                    "content": "Call to action {{1}} and {{2}}",
                    "variables": {
                        "1": 0,
                        "2": 1,
                    },
                    "params": [{"type": "text"}, {"type": "text"}],
                },
                {
                    "type": "button/phone_number",
                    "name": "button.0",
                    "content": "+12538678447",
                    "display": "Call us",
                    "variables": {},
                    "params": [],
                },
                {
                    "type": "button/url",
                    "name": "button.1",
                    "content": "https://example.com/?wa_customer={{3}}",
                    "display": "Check site",
                    "variables": {
                        "3": 2,
                    },
                    "params": [{"type": "text"}],
                },
            ],
            trans.components,
        )
        self.assertEqual([{"type": "text"}, {"type": "text"}, {"type": "text"}], trans.variables)

        # template sharing variables between components
        trans = self.type.update_local(
            channel,
            {
                "friendly_name": "quick_reply_template_shared_variable",
                "language": "en",
                "links": {"approval_fetch": "https://content.twilio.com/v1/Content/HX1234503/ApprovalRequests"},
                "sid": "HX1234503",
                "types": {
                    "twilio/quick-reply": {
                        "actions": [
                            {"id": "subscribe", "title": "Subscribe {{2}}"},
                            {"id": "stop", "title": "Stop promotions"},
                            {"id": "help", "title": "Help for {{3}}"},
                        ],
                        "body": "Welcome {{1}}, we have new features such as {{3}}, Subscribe for more info",
                    }
                },
                "url": "https://content.twilio.com/v1/Content/HX1234503",
                "variables": {"1": "Product A", "2": "Product B", "3": "Product C"},
            },
        )

        self.assertIsNotNone(trans)
        self.assertEqual("quick_reply_template_shared_variable", trans.template.name)
        self.assertEqual(TemplateTranslation.STATUS_APPROVED, trans.status)
        self.assertEqual("", trans.namespace)
        self.assertEqual("eng", trans.locale)
        self.assertEqual("en", trans.external_locale)
        self.assertEqual("HX1234503", trans.external_id)
        self.assertEqual(
            [
                {
                    "type": "body",
                    "name": "body",
                    "content": "Welcome {{1}}, we have new features such as {{3}}, Subscribe for more info",
                    "variables": {
                        "1": 0,
                        "3": 1,
                    },
                    "params": [{"type": "text"}, {"type": "text"}],
                },
                {
                    "type": "button/quick_reply",
                    "name": "button.0",
                    "content": "Subscribe {{2}}",
                    "variables": {"2": 2},
                    "params": [{"type": "text"}],
                },
                {
                    "type": "button/quick_reply",
                    "name": "button.1",
                    "content": "Stop promotions",
                    "variables": {},
                    "params": [],
                },
                {
                    "type": "button/quick_reply",
                    "name": "button.2",
                    "content": "Help for {{3}}",
                    "variables": {"3": 1},
                    "params": [{"type": "text"}],
                },
            ],
            trans.components,
        )
        self.assertEqual([{"type": "text"}, {"type": "text"}, {"type": "text"}], trans.variables)

        # not supported components parts
        trans = self.type.update_local(
            channel,
            {
                "friendly_name": "quick_reply_template_not_supported",
                "language": "en",
                "links": {"approval_fetch": "https://content.twilio.com/v1/Content/HX1234503/ApprovalRequests"},
                "sid": "HX1234504",
                "types": {
                    "twilio/call-to-action": {
                        "actions": [
                            {
                                "title": "Example",
                                "type": "RANDOM",
                            },
                        ],
                        "body": "Random type",
                    },
                    "twilio/blah": {
                        "actions": [
                            {
                                "title": "Check site",
                                "type": "URL",
                                "url": "https://example.com/?wa_customer={{3}}",
                            },
                        ],
                        "body": "unsupported twilio/blah",
                    },
                },
                "url": "https://content.twilio.com/v1/Content/HX1234504",
                "variables": {},
            },
        )
        self.assertIsNotNone(trans)
        self.assertEqual("quick_reply_template_not_supported", trans.template.name)
        self.assertEqual(TemplateTranslation.STATUS_UNSUPPORTED, trans.status)
        self.assertEqual("", trans.namespace)
        self.assertEqual("eng", trans.locale)
        self.assertEqual("en", trans.external_locale)
        self.assertEqual("HX1234504", trans.external_id)
        self.assertEqual(
            [
                {
                    "type": "body",
                    "name": "body",
                    "content": "Random type",
                    "variables": {},
                    "params": [],
                },
                {
                    "type": "body",
                    "name": "body",
                    "content": "unsupported twilio/blah",
                    "variables": {},
                    "params": [],
                },
            ],
            trans.components,
        )
        self.assertEqual([], trans.variables)
