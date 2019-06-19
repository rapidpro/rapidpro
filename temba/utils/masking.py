import json
import urllib.parse
import xml.sax.saxutils


def rev_match(input, test, anon_mask):
    # search range is set to 2/3 of test string length, we assume that 1/3 of the test is invariant
    VARIABLE_RATIO = 2 / 3

    # reverse input and test
    r_test = test[::-1]
    r_input = input[::-1]

    search_range = int(len(test) * VARIABLE_RATIO)

    for cut_index in range(0, search_range, 1):

        cut_position = len(r_test) - cut_index
        tmp_str = r_test[0:cut_position]

        if tmp_str in r_input:
            return input.replace(tmp_str[::-1], anon_mask)


def add_plus_sign(input):
    if input.startswith("+"):
        return input
    else:
        return f"+{input}"


def add_zero_sign(input):
    if input.startswith("0"):
        return input
    else:
        return f"0{input}"


def replace_plus_with_zero(input):
    if input.startswith("+"):
        return f"0{input[1:]}"
    else:
        return f"{input}"


def replace_zero_with_plus(input):
    if input.startswith("0"):
        return f"+{input[1:]}"
    else:
        return input


def apply_mask(input, test):
    """
    Masks contact identifying information in the input string with ANON_MASK

    For example:
      * contact identity: +252615518585
      * input: https://textit.in/c/sq/a7c4ae01-b6af-4dc4-9331-8aa2f01b99d4/receive?text=&to=378&from=0615518585&id=2
      * output: https://textit.in/c/sq/a7c4ae01-b6af-4dc4-9331-8aa2f01b99d4/receive?text=&to=378&from=0********&id=2

    Contact identifying information is more volatile at the start of the string than the end. In this case contact
    identity is masked regardless of a different prefix: 0615518585 -> 0******** for +252615518585

    To handle most known use cases process uses: fuzzers and encoders.

    Fuzzers are functions that convert test to a known variation. For example: 252615518585 -> +252615518585

    Encoders are functions that encode/escape test to a known variation: For example: +252615518585 -> %2B252615518585

    The process collects all valid matches and selects the one that has most characters replaced (is the shortest).
    """

    if not (test and input):
        return

    from temba.contacts.models import ContactURN

    matches = []

    fuzzers = (lambda x: x, add_plus_sign, add_zero_sign, replace_plus_with_zero, replace_zero_with_plus)

    encoders = (urllib.parse.quote, json.dumps, xml.sax.saxutils.escape, lambda x: x)

    for fuzzer in fuzzers:
        fuzzed_test = fuzzer(test)

        for encoder in encoders:
            encoded_test = encoder(fuzzed_test)

            if encoded_test in input:
                matches.append(input.replace(encoded_test, ContactURN.ANON_MASK))
            else:
                matches.append(rev_match(input, encoded_test, ContactURN.ANON_MASK))

    match_candidates = [match for match in matches if match is not None]

    if match_candidates:
        # the shortest match is the one with most characters replaced, use that one
        return min(match_candidates, key=len)
    else:
        # no matches
        return


def recursive_replace(obj, keys):
    from temba.contacts.models import ContactURN

    if isinstance(obj, dict):
        tmp = {}
        for k, v in obj.items():
            if isinstance(v, dict):
                tmp[k] = recursive_replace(v, keys)

            # replace values with ANON_MASK
            if k in keys:
                tmp[k] = ContactURN.ANON_MASK
            else:
                tmp[k] = recursive_replace(v, keys)

        return tmp

    elif isinstance(obj, list):
        return [recursive_replace(elem, keys) for elem in obj]
    else:
        return obj


def mask_dict_values(input, keys, delimiter=r"\r\n\r\n"):
    try:
        *rest, payload = input.split(delimiter)
        json_resp = json.loads(payload)

        anon_dict = recursive_replace(json_resp, keys=keys)

        # reconstruct the response
        rest.append(json.dumps(anon_dict))
        formatted_response = delimiter.join(rest)

        return formatted_response
    except ValueError:
        return
