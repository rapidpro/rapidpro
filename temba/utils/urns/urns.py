from enum import Enum

import phonenumbers

# Simple URN parser loosely based on RFC2141 (https://www.ietf.org/rfc/rfc2141.txt)

ESCAPES = {
    "#": "%23",
    "%": "%25",
    # '/': '%2F',  can't enable this until we fix our URNs with slashes
    "?": "%3F",
}


class ParsedURN:
    def __init__(self, scheme, path, query="", fragment=""):
        self.scheme = scheme
        self.path = path
        self.query = query
        self.fragment = fragment

    def __str__(self):
        s = escape(str(self.scheme)) + ":" + escape(str(self.path))
        if self.query:
            s += "?" + escape(str(self.query))
        if self.fragment:
            s += "#" + escape(str(self.fragment))
        return s


class State(Enum):
    scheme = 0
    path = 1
    query = 2
    fragment = 3


def parse_urn(urn):
    state = State.scheme

    buffers = {State.scheme: [], State.path: [], State.query: [], State.fragment: []}

    for c in urn:
        if c == ":":
            if state == State.scheme:
                state = State.path
                continue
        elif c == "?":
            if state == State.path:
                state = State.query
                continue
            else:
                raise ValueError("query component can only come after path component")
        elif c == "#":
            if state == State.path or state == State.query:
                state = State.fragment
                continue
            else:
                raise ValueError("fragment component can only come after path or query components")

        buffers[state].append(c)

    if len(buffers[State.scheme]) == 0:
        raise ValueError("scheme cannot be empty")
    if len(buffers[State.path]) == 0:
        raise ValueError("path cannot be empty")

    return ParsedURN(
        unescape("".join(buffers[State.scheme])),
        unescape("".join(buffers[State.path])),
        unescape("".join(buffers[State.query])),
        unescape("".join(buffers[State.fragment])),
    )


def escape(s):
    return "".join([ESCAPES.get(c, c) for c in s])


def unescape(s):
    for ch, esc in ESCAPES.items():
        s = s.replace(esc, ch, -1)
    return s


def parse_number(s: str, country_code: str) -> str:
    """
    Tries to parse the given string as a phone number and if successful returns it as E164
    """
    try:
        parsed = phonenumbers.parse(s, country_code or None)
    except phonenumbers.NumberParseException:
        raise ValueError("unable to parse number")

    # check if this is possible number, excluding local-only options
    if phonenumbers.is_possible_number_with_reason(parsed) != phonenumbers.ValidationResult.IS_POSSIBLE:
        raise ValueError("not a possible number")

    return phonenumbers.format_number(parsed, phonenumbers.PhoneNumberFormat.E164)
