from datetime import datetime

import pytz
import regex

from temba.utils.dates import FULL_ISO8601_REGEX

UUID4_REGEX = regex.compile(
    r"[a-f0-9]{8}-?[a-f0-9]{4}-?4[a-f0-9]{3}-?[89ab][a-f0-9]{3}-?[a-f0-9]{12}", regex.IGNORECASE
)


class MatcherMixin(object):
    def __ne__(self, other):
        return not self.__eq__(other)

    def __repr__(self):
        return "<Any:%s>" % self.__class__.__name__


class String(MatcherMixin, str):
    def __new__(cls, pattern=None):
        s = str.__new__(cls, "xxx")
        s.pattern = pattern
        return s

    def __eq__(self, other):
        if not isinstance(other, str):
            return False
        if self.pattern and not regex.match(self.pattern, other):
            return False
        return True


class ISODate(String):
    """
    Matches any ISO8601 formatted datetime string
    """

    def __new__(cls):
        return super().__new__(cls, pattern=FULL_ISO8601_REGEX)


class UUID4String(String):
    """
    Matches any UUID v4 string
    """

    def __new__(cls):
        return super().__new__(cls, pattern=UUID4_REGEX)


class Dict(MatcherMixin, dict):
    """
    Matches any dict
    """

    def __eq__(self, other):
        return isinstance(other, dict)


class Datetime(MatcherMixin, datetime):
    """
    Matches any datetime
    """

    def __new__(cls):
        return datetime.__new__(cls, 2019, 10, 30, 13, 39, 30, 123456, pytz.UTC)

    def __eq__(self, other):
        return isinstance(other, datetime)
