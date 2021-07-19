from temba.tests import TembaTest

from .type import InternalType


class InternalTypeTest(TembaTest):
    def test_is_available_to(self):
        # check we never show connect UI for this type
        self.assertFalse(InternalType().is_available_to(self.admin))
        self.assertEqual([], InternalType().get_urls())
