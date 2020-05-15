from django.test.utils import override_settings

from temba.tests import TembaTest

from .type import MailgunType


class MailgunTypeTest(TembaTest):
    def test_is_available(self):
        with override_settings(MAILGUN_API_KEY=""):
            self.assertFalse(MailgunType().is_available())
        with override_settings(MAILGUN_API_KEY="1234567"):
            self.assertTrue(MailgunType().is_available())
