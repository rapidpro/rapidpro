# -*- coding: utf-8 -*-
from __future__ import absolute_import, division, print_function, unicode_literals

import random
import re
import string
from collections import Counter

import regex
import sys
import base64
from django.utils.encoding import force_text

from django.utils.text import slugify

CONTROL_CHARACTERES_REGEX = r"[\000-\010]|[\013-\014]|[\016-\037]"

#  http://www.unicode.org/faq/private_use.html#noncharacters
if sys.maxunicode > 65535:
    NON_CHARACTERES_REGEX = r"[\U0000FDD0-\U0000FDEF]"
    NON_CHARACTERES_REGEX += r"|[\U0000FFFE-\U0000FFFF]"
    NON_CHARACTERES_REGEX += r"|[\U0001FFFE-\U0001FFFF]"

    NON_CHARACTERES_REGEX += r"|[\U0002FFFE-\U0002FFFF]"
    NON_CHARACTERES_REGEX += r"|[\U0003FFFE-\U0003FFFF]"
    NON_CHARACTERES_REGEX += r"|[\U0004FFFE-\U0004FFFF]"

    NON_CHARACTERES_REGEX += r"|[\U0005FFFE-\U0005FFFF]"
    NON_CHARACTERES_REGEX += r"|[\U0006FFFE-\U0006FFFF]"
    NON_CHARACTERES_REGEX += r"|[\U0007FFFE-\U0007FFFF]"

    NON_CHARACTERES_REGEX += r"|[\U0008FFFE-\U0008FFFF]"
    NON_CHARACTERES_REGEX += r"|[\U0009FFFE-\U0009FFFF]"
    NON_CHARACTERES_REGEX += r"|[\U000AFFFE-\U000AFFFF]"

    NON_CHARACTERES_REGEX += r"|[\U000BFFFE-\U000BFFFF]"
    NON_CHARACTERES_REGEX += r"|[\U000CFFFE-\U000CFFFF]"
    NON_CHARACTERES_REGEX += r"|[\U000DFFFE-\U000DFFFF]"

    NON_CHARACTERES_REGEX += r"|[\U000EFFFE-\U000EFFFF]"
    NON_CHARACTERES_REGEX += r"|[\U000FFFFE-\U000FFFFF]"
    NON_CHARACTERES_REGEX += r"|[\U0010FFFE-\U0010FFFF]"

else:  # pragma: no cover
    NON_CHARACTERES_REGEX = r"[\uFDD0-\uFDEF]"
    NON_CHARACTERES_REGEX += r"|[\uFFFE-\uFFFF]"
    NON_CHARACTERES_REGEX += r"|\uD83F[\uDFFE-\uDFFF]"

    NON_CHARACTERES_REGEX += r"|\uD87F[\uDFFE-\uDFFF]"
    NON_CHARACTERES_REGEX += r"|\uD8BF[\uDFFE-\uDFFF]"
    NON_CHARACTERES_REGEX += r"|\uD8FF[\uDFFE-\uDFFF]"

    NON_CHARACTERES_REGEX += r"|\uD93F[\uDFFE-\uDFFF]"
    NON_CHARACTERES_REGEX += r"|\uD97F[\uDFFE-\uDFFF]"
    NON_CHARACTERES_REGEX += r"|\uD9BF[\uDFFE-\uDFFF]"

    NON_CHARACTERES_REGEX += r"|\uD9FF[\uDFFE-\uDFFF]"
    NON_CHARACTERES_REGEX += r"|\uDA3F[\uDFFE-\uDFFF]"
    NON_CHARACTERES_REGEX += r"|\uDA7F[\uDFFE-\uDFFF]"

    NON_CHARACTERES_REGEX += r"|\uDABF[\uDFFE-\uDFFF]"
    NON_CHARACTERES_REGEX += r"|\uDAFF[\uDFFE-\uDFFF]"
    NON_CHARACTERES_REGEX += r"|\uDB3F[\uDFFE-\uDFFF]"

    NON_CHARACTERES_REGEX += r"|\uDB7F[\uDFFE-\uDFFF]"
    NON_CHARACTERES_REGEX += r"|\uDBBF[\uDFFE-\uDFFF]"
    NON_CHARACTERES_REGEX += r"|\uDBFF[\uDFFE-\uDFFF]"


def clean_string(string_text):
    if string_text is None:
        return string_text

    # http://www.unicode.org/faq/private_use.html#noncharacters
    rexp = regex.compile(NON_CHARACTERES_REGEX, flags=regex.MULTILINE | regex.UNICODE | regex.V0)

    matches = 1
    while matches:
        (string_text, matches) = rexp.subn('\ufffd', string_text)

    rexp = regex.compile(CONTROL_CHARACTERES_REGEX, flags=regex.MULTILINE | regex.UNICODE | regex.V0)

    matches = 1
    while matches:
        (string_text, matches) = rexp.subn('', string_text)

    return string_text


def decode_base64(original):
    """
    Try to detect base64 messages by doing:
    * Check divisible by 4
    * check there's no whitespace
    * check it's at least 60 characters
    * check the decoded string contains at least 50% ascii

    Returns decoded base64 or the original string
    """
    stripped = original.replace('\r', '').replace('\n', '').strip()

    if len(stripped) < 60:
        return original

    if len(stripped) % 4 != 0:
        return original

    p = re.compile(r'^([a-zA-Z0-9+/=]{4})+$')
    if not p.match(stripped[:-4]):
        return original

    decoded = original
    try:
        decoded = force_text(base64.standard_b64decode(stripped), errors='ignore')
        count = Counter(decoded)
        letters = sum(count[letter] for letter in string.ascii_letters)
        if float(letters) / len(decoded) < 0.5:
            return original

    except Exception:
        return original

    return decoded


def truncate(text, max_len):
    """
    Truncates text to be less than max_len characters. If truncation is required, text ends with ...
    """
    if len(text) > max_len:
        return "%s..." % text[:(max_len - 3)]
    else:
        return text


def slugify_with(value, sep='_'):
    """
    Slugifies a value using a word separator other than -
    """
    return slugify(value).replace('-', sep)


def random_string(length):
    """
    Generates a random alphanumeric string
    """
    letters = "23456789ABCDEFGHJKLMNPQRSTUVWXYZ"  # avoid things that could be mistaken ex: 'I' and '1'
    return ''.join([random.choice(letters) for _ in range(length)])
