import json
import urllib.parse
import xml.sax.saxutils

# search range is set to 2/3 of test string length, we assume that 1/3 of the test is invariant
VARIABLE_RATIO = 2 / 3

HTTP_BODY_BOUNDARY = "\r\n\r\n"


def text(s, value, mask):
    """
    Redacts a value from the given text by replacing it with a mask.

    Variations of the value are generated, e.g. 252615518585 becomes 0252615518585 +252615518585 etc and if these are
    found in the text they are redacted. Variations include different encodings as well, e.g. %2B252615518585

    Contact identifying information is more volatile at the start of the value than the end. In this case contact
    identity is masked regardless of a different prefix: 0615518585 -> 0******** for +252615518585

    The process collects all matches and selects the one that has most characters replaced (the shortest).
    """

    redactions = []

    for variation in _variations(value):
        if variation in s:
            redactions.append(s.replace(variation, mask))
        else:
            redactions.append(_reverse_match(s, variation, mask))

    candidates = [r for r in redactions if r is not None]

    if candidates:
        # use the shortest, i.e. the one with most characters replaced
        return min(candidates, key=len), True

    return s, False


def http_trace(trace, value, json_keys, mask):
    """
    Redacts the values with the given key names in the JSON payload of an HTTP trace
    """

    *rest, body = trace.split(HTTP_BODY_BOUNDARY)
    keys_replaced = []

    try:
        json_body = json.loads(body)
    except ValueError:
        return trace, False

    redacted_body = _json_replace(json_body, json_keys, mask, keys_replaced)

    # reconstruct the trace
    rest.append(json.dumps(redacted_body))

    redacted = HTTP_BODY_BOUNDARY.join(rest)

    # finally do a regular text-level redaction of the value
    redacted, changed = text(redacted, value, mask)

    return redacted, changed or len(keys_replaced) > 0


def _json_replace(obj, keys, mask, keys_replaced):
    """
    Recursively looks for specified keys in JSON and replaces their values with mask if found
    """
    if isinstance(obj, dict):
        tmp = {}
        for k, v in obj.items():
            if k in keys:
                tmp[k] = mask
                keys_replaced.append(k)
            else:
                tmp[k] = _json_replace(v, keys, mask, keys_replaced)

        return tmp

    elif isinstance(obj, list):
        return [_json_replace(v, keys, mask, keys_replaced) for v in obj]

    else:
        return obj


def _reverse_match(s, value, mask):
    # reverse input and test
    r_value = value[::-1]
    r_text = s[::-1]

    search_range = int(len(value) * VARIABLE_RATIO)

    for cut_index in range(0, search_range, 1):

        cut_position = len(r_value) - cut_index
        tmp_str = r_value[0:cut_position]

        if tmp_str in r_text:
            return s.replace(tmp_str[::-1], mask)


def _variations(value):
    """
    Generates variations based on a given base value
    """

    variations = set()

    for fuzzer in FUZZERS:
        fuzzy_value = fuzzer(value)
        for encoder in ENCODERS:
            variations.add(encoder(fuzzy_value))

    return variations


def _add_plus_sign(value):
    return value if value.startswith("+") else f"+{value}"


def _add_zero_sign(value):
    return value if value.startswith("0") else f"0{value}"


def _switch_zero_and_plus(value):
    if value.startswith("0"):
        return f"+{value[1:]}"
    elif value.startswith("+"):
        return f"0{value[1:]}"
    else:
        return value


FUZZERS = (lambda x: x, _add_plus_sign, _add_zero_sign, _switch_zero_and_plus)
ENCODERS = (lambda x: x, urllib.parse.quote, json.dumps, xml.sax.saxutils.escape)
