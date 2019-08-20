import json
import urllib.parse
import xml.sax.saxutils

HTTP_BODY_BOUNDARY = "\r\n\r\n"


def text(s, value, mask):
    """
    Redacts a value from the given text by replacing it with a mask.

    Variations of the value are generated, e.g. 252615518585 becomes 0252615518585 +252615518585 etc and if these are
    found in the text they are redacted. Variations include different encodings as well, e.g. %2B252615518585

    Contact identifying information is more volatile at the start of the value than the end. In this case contact
    identity is masked regardless of a different prefix: 0615518585 -> 0******** for +252615518585
    """

    assert isinstance(s, str) and isinstance(value, str) and isinstance(mask, str)

    for variation in _variations(value):
        s = s.replace(variation, mask)

    return s


def http_trace(trace, value, json_keys, mask):
    """
    Redacts the values with the given key names in the JSON payload of an HTTP trace
    """

    *rest, body = trace.split(HTTP_BODY_BOUNDARY)

    try:
        json_body = json.loads(body)
    except ValueError:
        return trace

    redacted_body = _json_replace(json_body, json_keys, mask)

    # reconstruct the trace
    rest.append(json.dumps(redacted_body))

    redacted = HTTP_BODY_BOUNDARY.join(rest)

    # finally do a regular text-level redaction of the value
    return text(redacted, value, mask)


def _json_replace(obj, keys, mask):
    """
    Recursively looks for specified keys in JSON and replaces their values with mask if found
    """
    if isinstance(obj, dict):
        tmp = {}
        for k, v in obj.items():
            if k in keys:
                tmp[k] = mask
            else:
                tmp[k] = _json_replace(v, keys, mask)

        return tmp

    elif isinstance(obj, list):
        return [_json_replace(v, keys, mask) for v in obj]

    else:
        return obj


def _variations(value):
    """
    Generates variations based on a given base value
    """

    bases = {value}

    # include variations with 0 and + prepended, and replaced with the other
    if value.startswith("0"):
        bases.add("+" + value[1:])
    elif not value.startswith("+"):
        bases.add("0" + value)

    if value.startswith("+"):
        bases.add("0" + value[1:])
    elif not value.startswith("0"):
        bases.add("+" + value)

    max_trim = int(len(value) / 2)  # trim up to a half off the start

    for i in range(0, max_trim):
        bases.add(value[(i + 1) :])

    # for each base variation, generate new variations using different encodings
    variations = set()
    for b in bases:
        for encoder in ENCODERS:
            variations.add(encoder(b))

    # return in order of longest to shortest, a-z
    return sorted(variations, key=lambda x: (len(x), x), reverse=True)


ENCODERS = (
    lambda s: s,  # original
    urllib.parse.quote,  # URL with spaces as %20
    urllib.parse.quote_plus,  # URL with spaces as +
    xml.sax.saxutils.escape,  # XML/HTML reserved chars
    lambda s: json.dumps(s)[1:-1],  # JSON reserved chars
)
