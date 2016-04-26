from __future__ import unicode_literals
"""
URN support functions, based on https://tools.ietf.org/html/rfc2141 but with following limitations:
    * Only supports URNs with scheme and path parts (no netloc, query, params or fragment)
    * No hex escaping in URN path
"""

import regex

SCHEME_REGEX = regex.compile(r"^[A-Za-z0-9][A-Za-z0-9\-]{0,31}$", regex.UNICODE | regex.V0)

# don't allow the reserved %/?# characters
PATH_REGEX = regex.compile(r"^[A-Za-z0-9\(\)\+\,\-\.\:\=\@\;\$\_\!\*\']+$", regex.UNICODE | regex.V0)


def to_parts(urn):
    """
    Parses a URN string (e.g. tel:+250783835665) into a tuple of scheme and path
    """
    try:
        scheme, path = urn.split(':', 1)
    except:
        raise ValueError("URN strings must contain scheme and path components")

    if not is_valid_scheme(scheme):
        raise ValueError("URN contains an invalid scheme component")

    if not is_valid_path(path):
        raise ValueError("URN contains an invalid path component")

    return scheme, path


def is_valid_scheme(scheme):
    return scheme and SCHEME_REGEX.match(scheme)


def is_valid_path(path):
    return path and PATH_REGEX.match(path)


def from_parts(scheme, path):
    """
    Formats a URN scheme and path as single URN string, e.g. tel:+250783835665
    """
    return '%s:%s' % (scheme, path)


def from_tel(path):
    return from_parts('tel', path)


def from_twitter(path):
    return from_parts('twitter', path)


def from_twilio(path):
    return from_parts('twilio', path)


def from_email(path):
    return from_parts('mailto', path)


def from_facebook(path):
    return from_parts('facebook', path)


def from_telegram(path):
    return from_parts('telegram', path)


def from_external(path):
    return from_parts('ext', path)
