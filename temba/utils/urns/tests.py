from typing import NamedTuple

from temba.tests import TembaTest

from .urns import parse_number, parse_urn


class URNTest(TembaTest):
    def test_parse_urn(self):
        class Test(NamedTuple):
            input: str = ""
            scheme: str = ""
            path: str = ""
            query: str = ""
            fragment: str = ""
            has_error: bool = False

        test_cases = (
            Test(input="scheme:path", scheme="scheme", path="path"),
            Test(input="scheme:path#frag", scheme="scheme", path="path", fragment="frag"),
            Test(input="scheme:path?query", scheme="scheme", path="path", query="query"),
            Test(input="scheme:path?query#frag", scheme="scheme", path="path", query="query", fragment="frag"),
            Test(
                input="scheme:path?bar=foo&bar=zap#frag",
                scheme="scheme",
                path="path",
                query="bar=foo&bar=zap",
                fragment="frag",
            ),
            Test(
                input="scheme:pa%25th?qu%23ery#fra%3Fg",
                scheme="scheme",
                path="pa%th",
                query="qu#ery",
                fragment="fra?g",
            ),
            Test(input="scheme:path:morepath", scheme="scheme", path="path:morepath"),
            Test(input="scheme:path:morepath?foo=bar", scheme="scheme", path="path:morepath", query="foo=bar"),
            # can't be empty
            Test(input="", has_error=True),
            # can't single part
            Test(input="xyz", has_error=True),
            # can't omit scheme or path
            Test(input=":path", has_error=True),
            Test(input="scheme:", has_error=True),
            # can't have multiple queries or fragments
            Test(input="scheme:path?query?query", has_error=True),
            Test(input="scheme:path#frag#frag", has_error=True),
            # can't have query after fragment
            Test(input="scheme:path#frag?query", has_error=True),
        )

        for tc in test_cases:
            p = None
            ex = None
            try:
                p = parse_urn(tc.input)
            except ValueError as e:
                ex = e

            if ex:
                self.assertTrue(tc.has_error, f"Failed parsing URN, got unxpected error: {str(ex)}")
            else:
                matches = (
                    p.scheme == tc.scheme and p.path == tc.path and p.query == tc.query and p.fragment == tc.fragment
                )
                self.assertTrue(
                    matches,
                    f"Failed parsing URN, got {p.scheme}|{p.path}|{p.query}|{p.fragment}, "
                    f"expected {tc.scheme}|{tc.path}|{tc.query}|{tc.fragment} for '{tc.input}'",
                )

                back_to_str = str(p)
                self.assertEqual(
                    back_to_str,
                    tc.input,
                    f"Failed stringifying URN, got '{back_to_str}', "
                    f"expected '{tc.input}' for {tc.scheme}|{tc.path}|{tc.query}|{tc.fragment}",
                )

    def test_parse_number(self):
        class Test(NamedTuple):
            input: str
            country: str
            parsed: str

        test_cases = (
            Test("+250788123123", "", "+250788123123"),  # international number fine without country
            Test("+250 788 123-123", "", "+250788123123"),  # fine if not E164 formatted
            Test("0788123123", "RW", "+250788123123"),
            Test("206 555 1212", "US", "+12065551212"),
            Test("12065551212", "US", "+12065551212"),  # country code but no +
            Test("5912705", "US", ""),  # is only possible as a local number so ignored
            Test("10000", "US", ""),
        )

        for tc in test_cases:
            if tc.parsed != "":
                parsed = parse_number(tc.input, tc.country)
                self.assertEqual(parsed, tc.parsed, f"result mismatch for '{tc.input}'")
            else:
                with self.assertRaises(ValueError):
                    parse_number(tc.input, tc.country)
