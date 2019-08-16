from temba.tests import TembaTest

from .urns import parse_urn


class URNTest(TembaTest):
    def test_parse_urn(self):
        class Test(object):
            def __init__(self, input, scheme="", path="", query="", fragment="", has_error=False):
                self.input = input
                self.scheme = scheme
                self.path = path
                self.query = query
                self.fragment = fragment
                self.has_error = has_error

        test_cases = (
            Test("scheme:path", scheme="scheme", path="path"),
            Test("scheme:path#frag", scheme="scheme", path="path", fragment="frag"),
            Test("scheme:path?query", scheme="scheme", path="path", query="query"),
            Test("scheme:path?query#frag", scheme="scheme", path="path", query="query", fragment="frag"),
            Test(
                "scheme:path?bar=foo&bar=zap#frag",
                scheme="scheme",
                path="path",
                query="bar=foo&bar=zap",
                fragment="frag",
            ),
            Test("scheme:pa%25th?qu%23ery#fra%3Fg", scheme="scheme", path="pa%th", query="qu#ery", fragment="fra?g"),
            Test("scheme:path:morepath", scheme="scheme", path="path:morepath"),
            Test("scheme:path:morepath?foo=bar", scheme="scheme", path="path:morepath", query="foo=bar"),
            # can't be empty
            Test("", has_error=True),
            # can't single part
            Test("xyz", has_error=True),
            # can't omit scheme or path
            Test(":path", has_error=True),
            Test("scheme:", has_error=True),
            # can't have multiple queries or fragments
            Test("scheme:path?query?query", has_error=True),
            Test("scheme:path#frag#frag", has_error=True),
            # can't have query after fragment
            Test("scheme:path#frag?query", has_error=True),
        )

        for tc in test_cases:
            p = None
            ex = None
            try:
                p = parse_urn(tc.input)
            except ValueError as e:
                ex = e

            if ex:
                self.assertTrue(tc.has_error, "Failed parsing URN, got unxpected error: %s" % str(ex))
            else:
                matches = (
                    p.scheme == tc.scheme and p.path == tc.path and p.query == tc.query and p.fragment == tc.fragment
                )
                self.assertTrue(
                    matches,
                    "Failed parsing URN, got %s|%s|%s|%s, expected %s|%s|%s|%s for '%s'"
                    % (p.scheme, p.path, p.query, p.fragment, tc.scheme, tc.path, tc.query, tc.fragment, tc.input),
                )

                back_to_str = str(p)
                self.assertEqual(
                    back_to_str,
                    tc.input,
                    "Failed stringifying URN, got '%s', expected '%s' for %s|%s|%s|%s"
                    % (back_to_str, tc.input, tc.scheme, tc.path, tc.query, tc.fragment),
                )
