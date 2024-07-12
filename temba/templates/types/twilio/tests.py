import json
from unittest.mock import patch

from requests import RequestException

from temba.channels.models import Channel
from temba.templates.models import TemplateTranslation
from temba.tests import TembaTest
from temba.tests.requests import MockResponse

from .type import TwilioType


class TwilioTypeTest(TembaTest):
    def setUp(self):
        self.type = TwilioType()

        return super().setUp()

    def test_extract_variables(self):
        self.assertEqual(["1", "2"], self.type._extract_variables("Hi {{2}} how are you? {{1}}"))
        self.assertEqual(["1", "2"], self.type._extract_variables("Hi {{1}} how are you? {{2}} {{1}}"))
        self.assertEqual(
            ["1", "flightnumber"],
            self.type._extract_variables("Hello this is an update for your flight: {{flightnumber}}. Thank you {{1}}"),
        )
        self.assertEqual(
            ["1"],
            self.type._extract_variables(
                "Hello this is an update for your flight: {{ flight number }}. Thank you {{1}}"
            ),
        )
        self.assertEqual([], self.type._extract_variables("Hi {there}. {{%#43}}"))

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
                },
                "url": "https://content.twilio.com/v1/Content/HX1234502",
                "variables": {"1": "for Product A", "2": "features A,B,C", "3": "id123"},
            },
        )
        self.assertIsNotNone(trans)
        self.assertEqual("text_only_template", trans.template.name)
        self.assertEqual(TemplateTranslation.STATUS_PENDING, trans.status)

        # mock for approved status
        mock_get.side_effect = None
        mock_get.return_value = MockResponse(
            200,
            json.dumps(
                {
                    "whatsapp": {
                        "status": "approved",
                    }
                }
            ),
        )

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
                },
                "url": "https://content.twilio.com/v1/Content/HX1234502",
                "variables": {"1": "for Product A", "2": "features A,B,C", "3": "id123"},
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
                    "name": "body",
                    "type": "body/text",
                    "content": "Hello {{1}}, this is text example only and can have variables replaces such as {{2}} and {{3}}",
                    "variables": {"1": 0, "2": 1, "3": 2},
                }
            ],
            trans.components,
        )
        self.assertEqual([{"type": "text"}, {"type": "text"}, {"type": "text"}], trans.variables)
        self.assertTrue(trans.is_supported)

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
        self.assertEqual(TemplateTranslation.STATUS_APPROVED, trans.status)
        self.assertEqual("", trans.namespace)
        self.assertEqual("eng", trans.locale)
        self.assertEqual("en", trans.external_locale)
        self.assertEqual("HX1234501", trans.external_id)
        self.assertEqual(
            [
                {
                    "name": "header",
                    "type": "header/media",
                    "content": "https://example.com/images/{{1}}.jpg",
                    "variables": {
                        "1": 0,
                    },
                },
                {
                    "name": "body",
                    "type": "body/text",
                    "content": "Template with media for {{2}} can have a link with variables",
                    "variables": {
                        "2": 1,
                    },
                },
            ],
            trans.components,
        )
        self.assertEqual([{"type": "text"}, {"type": "text"}], trans.variables)
        self.assertTrue(trans.is_supported)

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
                    "name": "body",
                    "type": "body/text",
                    "content": "Call to action {{1}} and {{2}}",
                    "variables": {
                        "1": 0,
                        "2": 1,
                    },
                },
                {
                    "name": "button.0",
                    "type": "button/phone_number",
                    "content": "+12538678447",
                    "display": "Call us",
                    "variables": {},
                },
                {
                    "name": "button.1",
                    "type": "button/url",
                    "content": "https://example.com/?wa_customer={{3}}",
                    "display": "Check site",
                    "variables": {
                        "3": 2,
                    },
                },
            ],
            trans.components,
        )
        self.assertEqual([{"type": "text"}, {"type": "text"}, {"type": "text"}], trans.variables)
        self.assertTrue(trans.is_supported)

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
                    "name": "body",
                    "type": "body/text",
                    "content": "Welcome {{1}}, we have new features such as {{3}}, Subscribe for more info",
                    "variables": {
                        "1": 0,
                        "3": 1,
                    },
                },
                {
                    "name": "button.0",
                    "type": "button/quick_reply",
                    "content": "Subscribe {{2}}",
                    "variables": {"2": 2},
                },
                {
                    "name": "button.1",
                    "type": "button/quick_reply",
                    "content": "Stop promotions",
                    "variables": {},
                },
                {
                    "name": "button.2",
                    "type": "button/quick_reply",
                    "content": "Help for {{3}}",
                    "variables": {"3": 1},
                },
            ],
            trans.components,
        )
        self.assertEqual([{"type": "text"}, {"type": "text"}, {"type": "text"}], trans.variables)
        self.assertTrue(trans.is_supported)

        # not supported components parts
        trans = self.type.update_local(
            channel,
            {
                "friendly_name": "quick_reply_template_not_supported",
                "language": "en",
                "links": {"approval_fetch": "https://content.twilio.com/v1/Content/HX1234504/ApprovalRequests"},
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
        self.assertEqual(TemplateTranslation.STATUS_APPROVED, trans.status)
        self.assertEqual("", trans.namespace)
        self.assertEqual("eng", trans.locale)
        self.assertEqual("en", trans.external_locale)
        self.assertEqual("HX1234504", trans.external_id)
        self.assertEqual(
            [
                {
                    "name": "body",
                    "type": "body/text",
                    "content": "Random type",
                    "variables": {},
                },
                {
                    "name": "body",
                    "type": "body/text",
                    "content": "unsupported twilio/blah",
                    "variables": {},
                },
            ],
            trans.components,
        )
        self.assertEqual([], trans.variables)
        self.assertFalse(trans.is_supported)

        # not supported whatsapp authentication
        trans = self.type.update_local(
            channel,
            {
                "friendly_name": "whatsapp_authentication_template",
                "language": "en",
                "links": {"approval_fetch": "https://content.twilio.com/v1/Content/HX1234505/ApprovalRequests"},
                "sid": "HX1234505",
                "types": {
                    "whatsapp/authentication": {
                        "body": "{{1}}",
                        "add_security_recommendation": True,
                        "code_expiration_minutes": 5,
                        "actions": [{"type": "COPY_CODE", "copy_code_text": "Copy Code"}],
                    },
                },
                "url": "https://content.twilio.com/v1/Content/HX1234505",
                "variables": {},
            },
        )
        self.assertIsNotNone(trans)
        self.assertEqual("whatsapp_authentication_template", trans.template.name)
        self.assertEqual(TemplateTranslation.STATUS_APPROVED, trans.status)
        self.assertEqual("", trans.namespace)
        self.assertEqual("eng", trans.locale)
        self.assertEqual("en", trans.external_locale)
        self.assertEqual("HX1234505", trans.external_id)
        self.assertEqual(
            [
                {
                    "name": "body",
                    "type": "body/text",
                    "content": "{{1}}",
                    "variables": {"1": 0},
                },
            ],
            trans.components,
        )
        self.assertEqual([{"type": "text"}], trans.variables)
        self.assertFalse(trans.is_supported)

        # twilio card
        trans = self.type.update_local(
            channel,
            {
                "friendly_name": "twilio_card_template",
                "language": "en",
                "links": {"approval_fetch": "https://content.twilio.com/v1/Content/HX1234506/ApprovalRequests"},
                "sid": "HX1234506",
                "types": {
                    "twilio/card": {
                        "body": None,
                        "media": [],
                        "subtitle": "This message is from an unverified business.",
                        "actions": [],
                        "title": "Your ticket for *{{1}}*\n*Time* - {{2}}\n*Venue* - {{3}}\n*Seats* - {{4}}",
                    }
                },
                "url": "https://content.twilio.com/v1/Content/HX1234506",
                "variables": {},
            },
        )
        self.assertIsNotNone(trans)
        self.assertEqual("twilio_card_template", trans.template.name)
        self.assertEqual(TemplateTranslation.STATUS_APPROVED, trans.status)
        self.assertEqual("", trans.namespace)
        self.assertEqual("eng", trans.locale)
        self.assertEqual("en", trans.external_locale)
        self.assertEqual("HX1234506", trans.external_id)
        self.assertEqual(
            [
                {
                    "name": "body",
                    "type": "body/text",
                    "content": "Your ticket for *{{1}}*\n*Time* - {{2}}\n*Venue* - {{3}}\n*Seats* - {{4}}",
                    "variables": {"1": 0, "2": 1, "3": 2, "4": 3},
                },
                {
                    "name": "footer",
                    "type": "footer/text",
                    "content": "This message is from an unverified business.",
                    "variables": {},
                },
            ],
            trans.components,
        )
        self.assertEqual([{"type": "text"}, {"type": "text"}, {"type": "text"}, {"type": "text"}], trans.variables)

        # whatsapp card
        trans = self.type.update_local(
            channel,
            {
                "friendly_name": "whatsapp_card_template",
                "language": "en",
                "links": {"approval_fetch": "https://content.twilio.com/v1/Content/HX1234509/ApprovalRequests"},
                "sid": "HX1234509",
                "types": {
                    "whatsapp/card": {
                        "body": "Congratulations, you have reached Elite status! Add code {{1}} for 10% off.",
                        "header_text": "This is a {{1}} card",
                        "footer": "To unsubscribe, reply Stop",
                        "actions": [
                            {"url": "https://example.com/?wa_customer={{3}}", "title": "Check site", "type": "URL"},
                            {"phone": "+15551234567", "title": "Call Us", "type": "PHONE_NUMBER"},
                        ],
                    }
                },
                "url": "https://content.twilio.com/v1/Content/HX1234509",
                "variables": {},
            },
        )
        self.assertIsNotNone(trans)
        self.assertEqual("whatsapp_card_template", trans.template.name)
        self.assertEqual(TemplateTranslation.STATUS_APPROVED, trans.status)
        self.assertEqual("", trans.namespace)
        self.assertEqual("eng", trans.locale)
        self.assertEqual("en", trans.external_locale)
        self.assertEqual("HX1234509", trans.external_id)
        self.assertEqual(
            [
                {
                    "name": "header",
                    "type": "header/text",
                    "content": "This is a {{1}} card",
                    "variables": {"1": 0},
                },
                {
                    "name": "body",
                    "type": "body/text",
                    "content": "Congratulations, you have reached Elite status! Add code {{1}} for 10% off.",
                    "variables": {"1": 0},
                },
                {
                    "name": "footer",
                    "type": "footer/text",
                    "content": "To unsubscribe, reply Stop",
                    "variables": {},
                },
                {
                    "name": "button.0",
                    "type": "button/url",
                    "content": "https://example.com/?wa_customer={{3}}",
                    "display": "Check site",
                    "variables": {"3": 1},
                },
                {
                    "name": "button.1",
                    "type": "button/phone_number",
                    "content": "+15551234567",
                    "display": "Call Us",
                    "variables": {},
                },
            ],
            trans.components,
        )
        self.assertEqual([{"type": "text"}, {"type": "text"}], trans.variables)

        # whatsapp card
        trans = self.type.update_local(
            channel,
            {
                "friendly_name": "whatsapp_card_qrs_template",
                "language": "en",
                "links": {"approval_fetch": "https://content.twilio.com/v1/Content/HX1234510/ApprovalRequests"},
                "sid": "HX1234510",
                "types": {
                    "whatsapp/card": {
                        "body": "Congratulations, you have reached Elite status! Add code {{1}} for 10% off.",
                        "header_text": "This is a {{1}} card",
                        "footer": "To unsubscribe, reply Stop",
                        "actions": [
                            {"id": "stop", "title": "Stop promotions", "type": "QUICK_REPLY"},
                            {"id": "help", "title": "Help for {{3}}", "type": "QUICK_REPLY"},
                        ],
                    }
                },
                "url": "https://content.twilio.com/v1/Content/HX1234510",
                "variables": {},
            },
        )
        self.assertIsNotNone(trans)
        self.assertEqual("whatsapp_card_qrs_template", trans.template.name)
        self.assertEqual(TemplateTranslation.STATUS_APPROVED, trans.status)
        self.assertEqual("", trans.namespace)
        self.assertEqual("eng", trans.locale)
        self.assertEqual("en", trans.external_locale)
        self.assertEqual("HX1234510", trans.external_id)
        self.assertEqual(
            [
                {
                    "name": "header",
                    "type": "header/text",
                    "content": "This is a {{1}} card",
                    "variables": {"1": 0},
                },
                {
                    "name": "body",
                    "type": "body/text",
                    "content": "Congratulations, you have reached Elite status! Add code {{1}} for 10% off.",
                    "variables": {"1": 0},
                },
                {
                    "name": "footer",
                    "type": "footer/text",
                    "content": "To unsubscribe, reply Stop",
                    "variables": {},
                },
                {
                    "type": "button/quick_reply",
                    "name": "button.0",
                    "content": "Stop promotions",
                    "variables": {},
                },
                {
                    "type": "button/quick_reply",
                    "name": "button.1",
                    "content": "Help for {{3}}",
                    "variables": {"3": 1},
                },
            ],
            trans.components,
        )
        self.assertEqual([{"type": "text"}, {"type": "text"}], trans.variables)

        # twilio list-picker
        trans = self.type.update_local(
            channel,
            {
                "friendly_name": "list_picker_template",
                "language": "en",
                "links": {"approval_fetch": "https://content.twilio.com/v1/Content/HX1234507/ApprovalRequests"},
                "sid": "HX1234507",
                "types": {
                    "twilio/list-picker": {
                        "body": "Owl Air Flash Sale! Hurry! Sale ends on {{1}}!",
                        "button": "Select a destination",
                        "items": [
                            {
                                "item": "SFO to NYC for $299",
                                "description": "Owl Air Flight 1337 to LGA",
                                "id": "SFO1337",
                            },
                            {
                                "item": "OAK to Denver for $149",
                                "description": "Owl Air Flight 5280 to DEN",
                                "id": "OAK5280",
                            },
                            {
                                "item": "LAX to Chicago for $199",
                                "description": "Owl Air Flight 96 to ORD",
                                "id": "LAX96",
                            },
                        ],
                    },
                },
                "url": "https://content.twilio.com/v1/Content/HX1234507",
                "variables": {},
            },
        )
        self.assertIsNotNone(trans)
        self.assertEqual("list_picker_template", trans.template.name)
        self.assertEqual(TemplateTranslation.STATUS_APPROVED, trans.status)
        self.assertEqual("", trans.namespace)
        self.assertEqual("eng", trans.locale)
        self.assertEqual("en", trans.external_locale)
        self.assertEqual("HX1234507", trans.external_id)
        self.assertEqual(
            [
                {
                    "name": "body",
                    "type": "body/text",
                    "content": "Owl Air Flash Sale! Hurry! Sale ends on {{1}}!",
                    "variables": {"1": 0},
                }
            ],
            trans.components,
        )
        self.assertEqual([{"type": "text"}], trans.variables)
        self.assertFalse(trans.is_supported)

        # twilio catalog
        trans = self.type.update_local(
            channel,
            {
                "friendly_name": "twilio_catalog_template",
                "language": "en",
                "links": {"approval_fetch": "https://content.twilio.com/v1/Content/HX1234508/ApprovalRequests"},
                "sid": "HX1234508",
                "types": {
                    "twilio/catalog": {
                        "id": "1017234312776586",
                        "body": "Hi, check out this menu {{1}}",
                        "subtitle": "Great deals",
                        "title": "The Menu: {{2}}",
                        "thumbnail_item_id": "48rme2i4po",
                        "items": [{"id": "48rme2i4po", "section_title": "veggies"}],
                    }
                },
                "url": "https://content.twilio.com/v1/Content/HX1234508",
                "variables": {},
            },
        )

        self.assertIsNotNone(trans)
        self.assertEqual("twilio_catalog_template", trans.template.name)
        self.assertEqual(TemplateTranslation.STATUS_APPROVED, trans.status)
        self.assertEqual("", trans.namespace)
        self.assertEqual("eng", trans.locale)
        self.assertEqual("en", trans.external_locale)
        self.assertEqual("HX1234508", trans.external_id)
        self.assertEqual(
            [
                {
                    "name": "body",
                    "type": "body/text",
                    "content": "Hi, check out this menu {{1}}",
                    "variables": {"1": 0},
                },
                {
                    "name": "body",
                    "type": "body/text",
                    "content": "The Menu: {{2}}",
                    "variables": {"2": 1},
                },
                {
                    "name": "footer",
                    "type": "footer/text",
                    "content": "Great deals",
                    "variables": {},
                },
            ],
            trans.components,
        )
        self.assertEqual([{"type": "text"}, {"type": "text"}], trans.variables)
        self.assertFalse(trans.is_supported)
