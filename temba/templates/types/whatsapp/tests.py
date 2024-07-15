from temba.tests import TembaTest

from ...models import TemplateTranslation
from .type import WhatsAppType


class WhatsAppTypeTest(TembaTest):
    def setUp(self):
        self.type = WhatsAppType()

        return super().setUp()

    def test_update_local_wa(self):
        # namespace in the channel config
        channel = self.create_channel("WA", "channel", "1234", config={"fb_namespace": "foo_namespace"})

        trans = self.type.update_local(
            channel,
            {
                "name": "hello",
                "components": [{"type": "BODY", "text": "Hello {{1}}"}],
                "language": "en",
                "status": "PENDING",
                "category": "ISSUE_RESOLUTION",
                "id": "1234",
            },
        )
        self.assertIsNotNone(trans)
        self.assertEqual("hello", trans.template.name)
        self.assertEqual(TemplateTranslation.STATUS_PENDING, trans.status)
        self.assertEqual("foo_namespace", trans.namespace)
        self.assertEqual("eng", trans.locale)
        self.assertEqual("en", trans.external_locale)
        self.assertEqual("1234", trans.external_id)
        self.assertEqual(
            [{"name": "body", "type": "body/text", "content": "Hello {{1}}", "variables": {"1": 0}}],
            trans.components,
        )
        self.assertEqual([{"type": "text"}], trans.variables)
        self.assertTrue(trans.is_supported)

        # try a template with multiple components
        trans = self.type.update_local(
            channel,
            {
                "name": "order_template",
                "components": [
                    {"type": "HEADER", "format": "TEXT", "text": "Your order!"},
                    {
                        "type": "BODY",
                        "text": "Sorry your order {{1}} took longer to deliver than expected.\nWe'll notify you about updates in the next {{2}} days.\n\nDo you have more question?",
                        "example": {"body_text": [["#123 for shoes", "3"]]},
                    },
                    {"type": "FOOTER", "text": "Thanks for your patience"},
                    {
                        "type": "BUTTONS",
                        "buttons": [
                            {"type": "QUICK_REPLY", "text": "Yes {{1}}"},
                            {"type": "QUICK_REPLY", "text": "No"},
                            {"type": "PHONE_NUMBER", "text": "Call center", "phone_number": "+1234"},
                            {
                                "type": "URL",
                                "text": "Check website",
                                "url": r"https:\/\/example.com\/?wa_customer={{1}}",
                                "example": [r"https:\/\/example.com\/?wa_customer=id_123"],
                            },
                            {
                                "type": "URL",
                                "text": "Check website",
                                "url": r"https:\/\/example.com\/help",
                                "example": [r"https:\/\/example.com\/help"],
                            },
                        ],
                    },
                ],
                "language": "en",
                "status": "APPROVED",
                "rejected_reason": "NONE",
                "category": "UTILITY",
            },
        )
        self.assertEqual("order_template", trans.template.name)
        self.assertEqual(
            [
                {"name": "header", "type": "header/text", "content": "Your order!", "variables": {}},
                {
                    "name": "body",
                    "type": "body/text",
                    "content": "Sorry your order {{1}} took longer to deliver than expected.\nWe'll notify you about updates in the next {{2}} days.\n\nDo you have more question?",
                    "variables": {"1": 0, "2": 1},
                },
                {
                    "name": "footer",
                    "type": "footer/text",
                    "content": "Thanks for your patience",
                    "variables": {},
                },
                {
                    "name": "button.0",
                    "type": "button/quick_reply",
                    "content": "Yes {{1}}",
                    "variables": {"1": 2},
                },
                {"name": "button.1", "type": "button/quick_reply", "content": "No", "variables": {}},
                {
                    "name": "button.2",
                    "type": "button/phone_number",
                    "content": "+1234",
                    "display": "Call center",
                    "variables": {},
                },
                {
                    "name": "button.3",
                    "type": "button/url",
                    "content": r"https:\/\/example.com\/?wa_customer={{1}}",
                    "display": "Check website",
                    "variables": {"1": 3},
                },
                {
                    "name": "button.4",
                    "type": "button/url",
                    "content": r"https:\/\/example.com\/help",
                    "display": "Check website",
                    "variables": {},
                },
            ],
            trans.components,
        )
        self.assertEqual([{"type": "text"}, {"type": "text"}, {"type": "text"}, {"type": "text"}], trans.variables)
        self.assertTrue(trans.is_supported)

        # try a template with non-text header
        trans = self.type.update_local(
            channel,
            {
                "name": "order_template",
                "components": [
                    {
                        "type": "HEADER",
                        "format": "IMAGE",
                    },
                    {"type": "BODY", "text": "Cat facts!"},
                ],
                "language": "en",
                "status": "APPROVED",
                "rejected_reason": "NONE",
                "category": "UTILITY",
            },
        )
        self.assertEqual(
            [
                {"name": "header", "type": "header/media", "content": "", "variables": {"1": 0}},
                {"name": "body", "type": "body/text", "content": "Cat facts!", "variables": {}},
            ],
            trans.components,
        )
        self.assertEqual([{"type": "image"}], trans.variables)
        self.assertTrue(trans.is_supported)

        # try unknown status - should be ignored completely
        trans = self.type.update_local(
            channel,
            {
                "name": "invalid_status",
                "components": [{"type": "BODY", "text": "Hello {{1}}"}],
                "language": "en",
                "status": "TOOCRAZY",
            },
        )
        self.assertIsNone(trans)

        # try unknown component type - should be saved but with status unsupported
        trans = self.type.update_local(
            channel,
            {
                "name": "invalid_component",
                "components": [{"type": "BODY", "text": "Hello {{1}}"}, {"type": "RANDOM", "text": "Yikes {{1}}"}],
                "language": "en",
                "status": "APPROVED",
                "category": "ISSUE_RESOLUTION",
                "id": "1233",
            },
        )
        self.assertEqual(TemplateTranslation.STATUS_APPROVED, trans.status)
        self.assertEqual(
            [{"name": "body", "type": "body/text", "content": "Hello {{1}}", "variables": {"1": 0}}],
            trans.components,
        )
        self.assertEqual([{"type": "text"}], trans.variables)
        self.assertFalse(trans.is_supported)

        # try non-text format header
        trans = self.type.update_local(
            channel,
            {
                "name": "invalid_component",
                "components": [{"type": "HEADER", "format": "TIKTOK", "example": {"header_handle": ["FOO"]}}],
                "language": "en",
                "status": "APPROVED",
                "category": "ISSUE_RESOLUTION",
                "id": "1233",
            },
        )
        self.assertEqual(TemplateTranslation.STATUS_APPROVED, trans.status)
        self.assertEqual(
            [{"name": "header", "type": "header/unknown", "content": "", "variables": {}}], trans.components
        )
        self.assertEqual([], trans.variables)
        self.assertFalse(trans.is_supported)

        # try unsupported button type
        trans = self.type.update_local(
            channel,
            {
                "category": "UTILITY",
                "components": [
                    {"type": "BODY", "text": "Hello"},
                    {"type": "BUTTONS", "buttons": [{"otp_type": "COPY_CODE", "text": "copy", "type": "OTP"}]},
                ],
                "language": "fr",
                "name": "login",
                "status": "approved",
                "id": "9030",
            },
        )
        self.assertEqual(TemplateTranslation.STATUS_APPROVED, trans.status)
        self.assertEqual([{"name": "body", "type": "body/text", "content": "Hello", "variables": {}}], trans.components)
        self.assertEqual([], trans.variables)
        self.assertFalse(trans.is_supported)

    def test_update_local_d3(self):
        # no namespace in channel config
        channel = self.create_channel("D3", "channel", "1234", config={})

        # no template id (so we use language/name) and namespace is on template itself
        trans = self.type.update_local(
            channel,
            {
                "name": "hello",
                "components": [{"type": "BODY", "text": "Hello {{1}}"}],
                "language": "en",
                "status": "pending",
                "namespace": "foo_namespace",
                "rejected_reason": "NONE",
                "category": "ISSUE_RESOLUTION",
            },
        )
        self.assertIsNotNone(trans)
        self.assertEqual("hello", trans.template.name)
        self.assertEqual(TemplateTranslation.STATUS_PENDING, trans.status)
        self.assertEqual("foo_namespace", trans.namespace)
        self.assertEqual("eng", trans.locale)
        self.assertEqual("en", trans.external_locale)
        self.assertIsNone(trans.external_id)
        self.assertEqual(
            [{"name": "body", "type": "body/text", "content": "Hello {{1}}", "variables": {"1": 0}}],
            trans.components,
        )
        self.assertEqual([{"type": "text"}], trans.variables)

    def test_extract_variables(self):
        self.assertEqual(["1", "2"], self.type._extract_variables("Hi {{2}} how are you? {{1}}"))
        self.assertEqual(["1", "2"], self.type._extract_variables("Hi {{1}} how are you? {{2}} {{1}}"))
        self.assertEqual([], self.type._extract_variables("Hi {there}. {{x}}"))

    def test_parse_language(self):
        self.assertEqual("eng", self.type._parse_language("en"))
        self.assertEqual("eng-US", self.type._parse_language("en_US"))
        self.assertEqual("fil", self.type._parse_language("fil"))
