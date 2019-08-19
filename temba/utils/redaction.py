import json
import urllib.parse
import xml.sax.saxutils

# search range is set to 2/3 of test string length, we assume that 1/3 of the test is invariant
VARIABLE_RATIO = 2 / 3

HTTP_DELIMITER = r"\r\n\r\n"


def redact(text, value, mask):
    """
    Masks contact identifying information in the given text

    For example:
      * contact identity: +252615518585
      * input: https://textit.in/c/sq/a7c4ae01-b6af-4dc4-9331-8aa2f01b99d4/receive?text=&to=378&from=0615518585&id=2
      * output: https://textit.in/c/sq/a7c4ae01-b6af-4dc4-9331-8aa2f01b99d4/receive?text=&to=378&from=0********&id=2

    Contact identifying information is more volatile at the start of the string than the end. In this case contact
    identity is masked regardless of a different prefix: 0615518585 -> 0******** for +252615518585

    To handle most known use cases process uses: fuzzers and encoders. Fuzzers are functions that convert test to a
    known variation. For example: 252615518585 -> +252615518585. Encoders are functions that encode/escape test to a
    known variation: For example: +252615518585 -> %2B252615518585

    The process collects all valid matches and selects the one that has most characters replaced (is the shortest).
    """

    matches = []

    fuzzers = (lambda x: x, add_plus_sign, add_zero_sign, switch_zero_and_plus)

    encoders = (lambda x: x, urllib.parse.quote, json.dumps, xml.sax.saxutils.escape)

    for fuzzer in fuzzers:
        fuzzed_test = fuzzer(value)

        for encoder in encoders:
            encoded_test = encoder(fuzzed_test)

            if encoded_test in text:
                matches.append(text.replace(encoded_test, mask))
            else:
                matches.append(rev_match(text, encoded_test, mask))

    match_candidates = [match for match in matches if match is not None]

    if match_candidates:
        # the shortest match is the one with most characters replaced, use that one
        return min(match_candidates, key=len), True

    return text, False


def rev_match(text, value, mask):
    # reverse input and test
    r_value = value[::-1]
    r_text = text[::-1]

    search_range = int(len(value) * VARIABLE_RATIO)

    for cut_index in range(0, search_range, 1):

        cut_position = len(r_value) - cut_index
        tmp_str = r_value[0:cut_position]

        if tmp_str in r_text:
            return text.replace(tmp_str[::-1], mask)


def add_plus_sign(value):
    return value if value.startswith("+") else f"+{value}"


def add_zero_sign(value):
    return value if value.startswith("0") else f"0{value}"


def switch_zero_and_plus(value):
    if value.startswith("0"):
        return f"+{value[1:]}"
    elif value.startswith("+"):
        return f"0{value[1:]}"
    else:
        return value


def redact_http_trace(trace, json_keys, mask):
    """
    Redacts the values with the given key names in the JSON payload of an HTTP trace
    """
    try:
        *rest, payload = trace.split(HTTP_DELIMITER)
        json_body = json.loads(payload)

        json_body = recursive_replace(json_body, json_keys, mask)

        # reconstruct the trace
        rest.append(json.dumps(json_body))

        return HTTP_DELIMITER.join(rest)
    except ValueError:
        return None


def recursive_replace(obj, keys, mask):
    from temba.contacts.models import ContactURN

    if isinstance(obj, dict):
        tmp = {}
        for k, v in obj.items():
            if isinstance(v, dict):
                tmp[k] = recursive_replace(v, keys, mask)

            # replace values with ANON_MASK
            if k in keys:
                tmp[k] = ContactURN.ANON_MASK
            else:
                tmp[k] = recursive_replace(v, keys, mask)

        return tmp

    elif isinstance(obj, list):
        return [recursive_replace(elem, keys, mask) for elem in obj]
    else:
        return obj
