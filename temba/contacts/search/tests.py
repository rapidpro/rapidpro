from temba.mailroom import MailroomException
from temba.tests import TembaTest

from . import SearchException


class SearchExceptionTest(TembaTest):
    def test_str(self):
        tests = (
            (
                {
                    "error": "mismatched input '$' expecting {'(', TEXT, STRING}",
                    "code": "unexpected_token",
                    "extra": {"token": "$"},
                },
                "Invalid query syntax at '$'",
            ),
            (
                {"error": "can't convert 'XZ' to a number", "code": "invalid_number", "extra": {"value": "XZ"}},
                "Unable to convert 'XZ' to a number",
            ),
            (
                {"error": "can't convert 'AB' to a date", "code": "invalid_date", "extra": {"value": "AB"}},
                "Unable to convert 'AB' to a date",
            ),
            (
                {
                    "error": "'Cool Kids' is not a valid group name",
                    "code": "invalid_group",
                    "extra": {"value": "Cool Kids"},
                },
                "'Cool Kids' is not a valid group name",
            ),
            (
                {
                    "error": "'zzzzzz' is not a valid language code",
                    "code": "invalid_language",
                    "extra": {"value": "zzzz"},
                },
                "'zzzz' is not a valid language code",
            ),
            (
                {
                    "error": "contains operator on name requires token of minimum length 2",
                    "code": "invalid_partial_name",
                    "extra": {"min_token_length": "2"},
                },
                "Using ~ with name requires token of at least 2 characters",
            ),
            (
                {
                    "error": "contains operator on URN requires value of minimum length 3",
                    "code": "invalid_partial_urn",
                    "extra": {"min_value_length": "3"},
                },
                "Using ~ with URN requires value of at least 3 characters",
            ),
            (
                {
                    "error": "contains conditions can only be used with name or URN values",
                    "code": "unsupported_contains",
                    "extra": {"property": "uuid"},
                },
                "Can only use ~ with name or URN values",
            ),
            (
                {
                    "error": "comparisons with > can only be used with date and number fields",
                    "code": "unsupported_comparison",
                    "extra": {"property": "uuid", "operator": ">"},
                },
                "Can only use > with number or date values",
            ),
            (
                {
                    "error": "can't check whether 'uuid' is set or not set",
                    "code": "unsupported_setcheck",
                    "extra": {"property": "uuid", "operator": "!="},
                },
                "Can't check whether 'uuid' is set or not set",
            ),
            (
                {
                    "error": "can't resolve 'beers' to attribute, scheme or field",
                    "code": "unknown_property",
                    "extra": {"property": "beers"},
                },
                "Can't resolve 'beers' to a field or URN scheme",
            ),
            (
                {"error": "cannot query on redacted URNs", "code": "redacted_urns"},
                "Can't query on URNs in an anonymous workspace",
            ),
            ({"error": "no code here"}, "no code here",),
        )

        for response, message in tests:
            e = MailroomException("parse_query", None, response)
            e = SearchException.from_mailroom_exception(e)

            self.assertEqual(message, str(e))
