from temba.tests import TembaTest

from .type import InternalType


class InternalTypeTest(TembaTest):
    def test_is_available_to(self):
        # this type is never displayed as an option on the connect page
        self.assertFalse(InternalType().is_available_to(self.user))
