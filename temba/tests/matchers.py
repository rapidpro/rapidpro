# -*- coding: utf-8 -*-
from __future__ import absolute_import, division, print_function, unicode_literals

import regex
import six

from temba.utils.dates import FULL_ISO8601_REGEX

UUID4_REGEX = regex.compile(r'[a-f0-9]{8}-?[a-f0-9]{4}-?4[a-f0-9]{3}-?[89ab][a-f0-9]{3}-?[a-f0-9]{12}', regex.IGNORECASE)


class MatcherMixin(object):
    def __ne__(self, other):
        return not self.__eq__(other)

    def __repr__(self):
        return '<Any:%s>' % self.__class__.__name__


class String(MatcherMixin, six.text_type):
    def __new__(cls, pattern=None):
        s = six.text_type.__new__(cls, "xxx")
        s.pattern = pattern
        return s

    def __eq__(self, other):
        if not isinstance(other, six.text_type):
            return False
        if self.pattern and not regex.match(self.pattern, other):
            return False
        return True


class ISODate(String):
    """
    Matches any ISO8601 formatted datetime string
    """
    def __new__(cls):
        return super(ISODate, cls).__new__(cls, pattern=FULL_ISO8601_REGEX)


class UUID4String(String):
    """
    Matches any UUID v4 string
    """
    def __new__(cls):
        return super(UUID4String, cls).__new__(cls, pattern=UUID4_REGEX)
