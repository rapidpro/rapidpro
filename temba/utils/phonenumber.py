from __future__ import absolute_import, division, unicode_literals

import regex

TEL_VALUE_REGEX = regex.compile(r'^[+ \d\-\(\)]*$')
CLEAN_SPECIAL_CHARS_REGEX = regex.compile(r'[+ \-\(\)]*')


def normalize_phonenumber(text):
    """
    Normalizes phone number - removes
    """

    if TEL_VALUE_REGEX.match(text):
        return CLEAN_SPECIAL_CHARS_REGEX.sub('', text)
    else:
        return None
