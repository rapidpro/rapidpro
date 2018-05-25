from temba.tests import TembaTest
from .run_audit import has_none_string_in


class RunAuditTest(TembaTest):

    def test_has_none_string_in(self):
        self.assertTrue(has_none_string_in("None"))
        self.assertTrue(has_none_string_in({"foo": "None"}))
        self.assertTrue(has_none_string_in(["None"]))
        self.assertTrue(has_none_string_in({"foo": {"bar": ["None"]}}))

        self.assertFalse(has_none_string_in(None))
        self.assertFalse(has_none_string_in({"foo": None}))
        self.assertFalse(has_none_string_in("abc"))
        self.assertFalse(has_none_string_in("123"))
        self.assertFalse(has_none_string_in({"foo": {"bar": ["abc"]}}))
