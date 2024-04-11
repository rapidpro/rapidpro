from temba.tests import TembaTest

from .whatsapp import WhatsAppType


class WhatsAppTypeTest(TembaTest):
    def setUp(self):
        self.type = WhatsAppType()

        return super().setUp()

    def test_extract_variables(self):
        self.assertEqual(["1", "2"], self.type._extract_variables("Hi {{2}} how are you? {{1}}"))
        self.assertEqual(["1", "2"], self.type._extract_variables("Hi {{1}} how are you? {{2}} {{1}}"))
        self.assertEqual([], self.type._extract_variables("Hi {there}. {{x}}"))

    def test_parse_language(self):
        self.assertEqual("eng", self.type._parse_language("en"))
        self.assertEqual("eng-US", self.type._parse_language("en_US"))
        self.assertEqual("fil", self.type._parse_language("fil"))
