GSM7_BASIC = (
    "@£$¥èéùìòÇ\nØø\rÅåΔ_ΦΓΛΩΠΨΣΘΞ\x1bÆæßÉ !\"#¤%&'()*+,-./0123456789:;<=>"
    "?¡ABCDEFGHIJKLMNOPQRSTUVWXYZÄÖÑÜ`¿abcdefghijklmnopqrstuvwxyzäöñüà"
)

GSM7_EXTENDED = "^{}\\[~]|€"


# All valid GSM7 characters, table format
VALID_GSM7 = GSM7_BASIC + GSM7_EXTENDED

# Valid GSM7 chars as a set
GSM7_CHARS = {c for c in VALID_GSM7}
GSM7_BASIC_CHARS = {c for c in GSM7_BASIC}
GSM7_EXTENDED_CHARS = {c for c in GSM7_EXTENDED}

# Characters we replace in GSM7 with versions that can actually be encoded
GSM7_REPLACEMENTS = {
    "á": "a",
    "ê": "e",
    "ã": "a",
    "â": "a",
    "ç": "c",
    "í": "i",
    "î": "i",
    "ú": "u",
    "û": "u",
    "õ": "o",
    "ô": "o",
    "ó": "o",
    "Á": "A",
    "Â": "A",
    "Ã": "A",
    "À": "A",
    "Ç": "C",
    "È": "E",
    "Ê": "E",
    "Í": "I",
    "Î": "I",
    "Ì": "I",
    "Ó": "O",
    "Ô": "O",
    "Ò": "O",
    "Õ": "O",
    "Ú": "U",
    "Ù": "U",
    "Û": "U",
    # shit Word likes replacing automatically
    "’": "'",
    "‘": "'",
    "“": '"',
    "”": '"',
    "–": "-",
    "\xa0": " ",
    "\x09": " ",
}


def is_gsm7(text):
    """
    Returns whether the passed in text can be represented in GSM7 character set
    """
    for c in text:
        if c not in GSM7_CHARS:
            return False

    return True


def replace_non_gsm7_accents(text):
    """
    Give a string, replaces any accents that aren't GSM7 with a plain version. This generally
    takes the form of removing accents.
    """
    return "".join([GSM7_REPLACEMENTS.get(c, c) for c in text])


# Coding table from:
# http://snoops.roy202.org/testerman/browser/trunk/plugins/codecs/gsm0338.py

# default GSM 03.38 -> unicode
def_regular_decode_dict = {
    "\x00": "\u0040",  # COMMERCIAL AT
    "\x01": "\u00A3",  # POUND SIGN
    "\x02": "\u0024",  # DOLLAR SIGN
    "\x03": "\u00A5",  # YEN SIGN
    "\x04": "\u00E8",  # LATIN SMALL LETTER E WITH GRAVE
    "\x05": "\u00E9",  # LATIN SMALL LETTER E WITH ACUTE
    "\x06": "\u00F9",  # LATIN SMALL LETTER U WITH GRAVE
    "\x07": "\u00EC",  # LATIN SMALL LETTER I WITH GRAVE
    "\x08": "\u00F2",  # LATIN SMALL LETTER O WITH GRAVE
    "\x09": "\u00C7",  # LATIN CAPITAL LETTER C WITH CEDILLA
    # The Unicode page suggests this is a mistake: but
    # it's still in the latest version of the spec and
    # our implementation has to be exact.
    "\x0A": "\u000A",  # LINE FEED
    "\x0B": "\u00D8",  # LATIN CAPITAL LETTER O WITH STROKE
    "\x0C": "\u00F8",  # LATIN SMALL LETTER O WITH STROKE
    "\x0D": "\u000D",  # CARRIAGE RETURN
    "\x0E": "\u00C5",  # LATIN CAPITAL LETTER A WITH RING ABOVE
    "\x0F": "\u00E5",  # LATIN SMALL LETTER A WITH RING ABOVE
    "\x10": "\u0394",  # GREEK CAPITAL LETTER DELTA
    "\x11": "\u005F",  # LOW LINE
    "\x12": "\u03A6",  # GREEK CAPITAL LETTER PHI
    "\x13": "\u0393",  # GREEK CAPITAL LETTER GAMMA
    "\x14": "\u039B",  # GREEK CAPITAL LETTER LAMDA
    "\x15": "\u03A9",  # GREEK CAPITAL LETTER OMEGA
    "\x16": "\u03A0",  # GREEK CAPITAL LETTER PI
    "\x17": "\u03A8",  # GREEK CAPITAL LETTER PSI
    "\x18": "\u03A3",  # GREEK CAPITAL LETTER SIGMA
    "\x19": "\u0398",  # GREEK CAPITAL LETTER THETA
    "\x1A": "\u039E",  # GREEK CAPITAL LETTER XI
    "\x1C": "\u00C6",  # LATIN CAPITAL LETTER AE
    "\x1D": "\u00E6",  # LATIN SMALL LETTER AE
    "\x1E": "\u00DF",  # LATIN SMALL LETTER SHARP S (German)
    "\x1F": "\u00C9",  # LATIN CAPITAL LETTER E WITH ACUTE
    "\x20": "\u0020",  # SPACE
    "\x21": "\u0021",  # EXCLAMATION MARK
    "\x22": "\u0022",  # QUOTATION MARK
    "\x23": "\u0023",  # NUMBER SIGN
    "\x24": "\u00A4",  # CURRENCY SIGN
    "\x25": "\u0025",  # PERCENT SIGN
    "\x26": "\u0026",  # AMPERSAND
    "\x27": "\u0027",  # APOSTROPHE
    "\x28": "\u0028",  # LEFT PARENTHESIS
    "\x29": "\u0029",  # RIGHT PARENTHESIS
    "\x2A": "\u002A",  # ASTERISK
    "\x2B": "\u002B",  # PLUS SIGN
    "\x2C": "\u002C",  # COMMA
    "\x2D": "\u002D",  # HYPHEN-MINUS
    "\x2E": "\u002E",  # FULL STOP
    "\x2F": "\u002F",  # SOLIDUS
    "\x30": "\u0030",  # DIGIT ZERO
    "\x31": "\u0031",  # DIGIT ONE
    "\x32": "\u0032",  # DIGIT TWO
    "\x33": "\u0033",  # DIGIT THREE
    "\x34": "\u0034",  # DIGIT FOUR
    "\x35": "\u0035",  # DIGIT FIVE
    "\x36": "\u0036",  # DIGIT SIX
    "\x37": "\u0037",  # DIGIT SEVEN
    "\x38": "\u0038",  # DIGIT EIGHT
    "\x39": "\u0039",  # DIGIT NINE
    "\x3A": "\u003A",  # COLON
    "\x3B": "\u003B",  # SEMICOLON
    "\x3C": "\u003C",  # LESS-THAN SIGN
    "\x3D": "\u003D",  # EQUALS SIGN
    "\x3E": "\u003E",  # GREATER-THAN SIGN
    "\x3F": "\u003F",  # QUESTION MARK
    "\x40": "\u00A1",  # INVERTED EXCLAMATION MARK
    "\x41": "\u0041",  # LATIN CAPITAL LETTER A
    "\x42": "\u0042",  # LATIN CAPITAL LETTER B
    "\x43": "\u0043",  # LATIN CAPITAL LETTER C
    "\x44": "\u0044",  # LATIN CAPITAL LETTER D
    "\x45": "\u0045",  # LATIN CAPITAL LETTER E
    "\x46": "\u0046",  # LATIN CAPITAL LETTER F
    "\x47": "\u0047",  # LATIN CAPITAL LETTER G
    "\x48": "\u0048",  # LATIN CAPITAL LETTER H
    "\x49": "\u0049",  # LATIN CAPITAL LETTER I
    "\x4A": "\u004A",  # LATIN CAPITAL LETTER J
    "\x4B": "\u004B",  # LATIN CAPITAL LETTER K
    "\x4C": "\u004C",  # LATIN CAPITAL LETTER L
    "\x4D": "\u004D",  # LATIN CAPITAL LETTER M
    "\x4E": "\u004E",  # LATIN CAPITAL LETTER N
    "\x4F": "\u004F",  # LATIN CAPITAL LETTER O
    "\x50": "\u0050",  # LATIN CAPITAL LETTER P
    "\x51": "\u0051",  # LATIN CAPITAL LETTER Q
    "\x52": "\u0052",  # LATIN CAPITAL LETTER R
    "\x53": "\u0053",  # LATIN CAPITAL LETTER S
    "\x54": "\u0054",  # LATIN CAPITAL LETTER T
    "\x55": "\u0055",  # LATIN CAPITAL LETTER U
    "\x56": "\u0056",  # LATIN CAPITAL LETTER V
    "\x57": "\u0057",  # LATIN CAPITAL LETTER W
    "\x58": "\u0058",  # LATIN CAPITAL LETTER X
    "\x59": "\u0059",  # LATIN CAPITAL LETTER Y
    "\x5A": "\u005A",  # LATIN CAPITAL LETTER Z
    "\x5B": "\u00C4",  # LATIN CAPITAL LETTER A WITH DIAERESIS
    "\x5C": "\u00D6",  # LATIN CAPITAL LETTER O WITH DIAERESIS
    "\x5D": "\u00D1",  # LATIN CAPITAL LETTER N WITH TILDE
    "\x5E": "\u00DC",  # LATIN CAPITAL LETTER U WITH DIAERESIS
    "\x5F": "\u00A7",  # SECTION SIGN
    "\x60": "\u00BF",  # INVERTED QUESTION MARK
    "\x61": "\u0061",  # LATIN SMALL LETTER A
    "\x62": "\u0062",  # LATIN SMALL LETTER B
    "\x63": "\u0063",  # LATIN SMALL LETTER C
    "\x64": "\u0064",  # LATIN SMALL LETTER D
    "\x65": "\u0065",  # LATIN SMALL LETTER E
    "\x66": "\u0066",  # LATIN SMALL LETTER F
    "\x67": "\u0067",  # LATIN SMALL LETTER G
    "\x68": "\u0068",  # LATIN SMALL LETTER H
    "\x69": "\u0069",  # LATIN SMALL LETTER I
    "\x6A": "\u006A",  # LATIN SMALL LETTER J
    "\x6B": "\u006B",  # LATIN SMALL LETTER K
    "\x6C": "\u006C",  # LATIN SMALL LETTER L
    "\x6D": "\u006D",  # LATIN SMALL LETTER M
    "\x6E": "\u006E",  # LATIN SMALL LETTER N
    "\x6F": "\u006F",  # LATIN SMALL LETTER O
    "\x70": "\u0070",  # LATIN SMALL LETTER P
    "\x71": "\u0071",  # LATIN SMALL LETTER Q
    "\x72": "\u0072",  # LATIN SMALL LETTER R
    "\x73": "\u0073",  # LATIN SMALL LETTER S
    "\x74": "\u0074",  # LATIN SMALL LETTER T
    "\x75": "\u0075",  # LATIN SMALL LETTER U
    "\x76": "\u0076",  # LATIN SMALL LETTER V
    "\x77": "\u0077",  # LATIN SMALL LETTER W
    "\x78": "\u0078",  # LATIN SMALL LETTER X
    "\x79": "\u0079",  # LATIN SMALL LETTER Y
    "\x7A": "\u007A",  # LATIN SMALL LETTER Z
    "\x7B": "\u00E4",  # LATIN SMALL LETTER A WITH DIAERESIS
    "\x7C": "\u00F6",  # LATIN SMALL LETTER O WITH DIAERESIS
    "\x7D": "\u00F1",  # LATIN SMALL LETTER N WITH TILDE
    "\x7E": "\u00FC",  # LATIN SMALL LETTER U WITH DIAERESIS
    "\x7F": "\u00E0",  # LATIN SMALL LETTER A WITH GRAVE
}

# default GSM 03.38 escaped characters -> unicode
def_escape_decode_dict = {
    "\x0A": "\u000C",  # FORM FEED
    "\x14": "\u005E",  # CIRCUMFLEX ACCENT
    "\x28": "\u007B",  # LEFT CURLY BRACKET
    "\x29": "\u007D",  # RIGHT CURLY BRACKET
    "\x2F": "\u005C",  # REVERSE SOLIDUS
    "\x3C": "\u005B",  # LEFT SQUARE BRACKET
    "\x3D": "\u007E",  # TILDE
    "\x3E": "\u005D",  # RIGHT SQUARE BRACKET
    "\x40": "\u007C",  # VERTICAL LINE
    "\x65": "\u20AC",  # EURO SIGN
}

# Replacement characters, default is question mark. Used when it is not too
# important to ensure exact UTF-8 -> GSM -> UTF-8 equivilence, such as when
# humans read and write SMS. But for USSD and other M2M applications it's
# important to ensure the conversion is exact.
def_replace_encode_dict = {
    "\u00E7": "\x09",  # LATIN SMALL LETTER C WITH CEDILLA
    "\u0391": "\x41",  # GREEK CAPITAL LETTER ALPHA
    "\u0392": "\x42",  # GREEK CAPITAL LETTER BETA
    "\u0395": "\x45",  # GREEK CAPITAL LETTER EPSILON
    "\u0397": "\x48",  # GREEK CAPITAL LETTER ETA
    "\u0399": "\x49",  # GREEK CAPITAL LETTER IOTA
    "\u039A": "\x4B",  # GREEK CAPITAL LETTER KAPPA
    "\u039C": "\x4D",  # GREEK CAPITAL LETTER MU
    "\u039D": "\x4E",  # GREEK CAPITAL LETTER NU
    "\u039F": "\x4F",  # GREEK CAPITAL LETTER OMICRON
    "\u03A1": "\x50",  # GREEK CAPITAL LETTER RHO
    "\u03A4": "\x54",  # GREEK CAPITAL LETTER TAU
    "\u03A7": "\x58",  # GREEK CAPITAL LETTER CHI
    "\u03A5": "\x59",  # GREEK CAPITAL LETTER UPSILON
    "\u0396": "\x5A",  # GREEK CAPITAL LETTER ZETA
}

QUESTION_MARK = chr(0x3F)

# unicode -> default GSM 03.38
def_regular_encode_dict = dict((u, g) for g, u in def_regular_decode_dict.items())

# unicode -> default escaped GSM 03.38 characters
def_escape_encode_dict = dict((u, g) for g, u in def_escape_decode_dict.items())


def encode(input_, errors="strict"):  # pragma: needs cover
    """
    :type input_: unicode
    :return: string
    """
    result = []
    for c in input_:
        try:
            result.append(def_regular_encode_dict[c])
        except KeyError:
            if c in def_escape_encode_dict:
                # OK, let's encode it as an escaped characters
                result.append("\x1b")
                result.append(def_escape_encode_dict[c])
            else:
                if errors == "strict":
                    raise UnicodeError("Invalid GSM character")
                elif errors == "replace":
                    result.append(def_replace_encode_dict.get(c, QUESTION_MARK))
                elif errors == "ignore":
                    pass
                else:
                    raise UnicodeError("Unknown error handling")

    ret = "".join(result)
    return ret, len(ret)


def decode(input_, errors="strict"):  # pragma: needs cover
    """
    :type input_: str
    :return: unicode
    """
    result = []
    index = 0
    while index < len(input_):
        c = input_[index]
        index += 1
        if c == "\x1b":  # pragma: needs cover
            if index < len(input_):
                c = input_[index]
                index += 1
                result.append(def_escape_decode_dict.get(c, "\xa0"))
            else:
                result.append("\xa0")
        else:
            try:
                result.append(def_regular_decode_dict[c])
            except KeyError:  # pragma: needs cover
                # error handling: unassigned byte, must be > 0x7f
                if errors == "strict":
                    raise UnicodeError("Unrecognized GSM character")
                elif errors == "replace":
                    result.append("?")
                elif errors == "ignore":
                    pass
                else:
                    raise UnicodeError("Unknown error handling")

    ret = "".join(result)
    return ret, len(ret)


def calculate_num_segments(text):
    """
    Calculates the number of SMS segments it will take to send the passed in text. This automatically figures out if
    the text is GSM7 or UCS2 and then calculates how many segments it will break up into.

    UCS2 messages can be 70 characters at max, if more, each segment is 67
    GSM7 messages can be 160 characters at max, if more, each segment is 153

    """
    # are we ucs2?
    gsm7 = is_gsm7(text)

    # first figure out if we are multipart
    is_multipart = False
    segment_size = 0
    for c in text:
        if c in GSM7_EXTENDED_CHARS and gsm7:
            segment_size += 2
        else:
            segment_size += 1

        if not gsm7 and segment_size > 70:
            is_multipart = True
            break

        if gsm7 and segment_size > 160:
            is_multipart = True
            break

    # we aren't multipart, so just return a single segment
    if not is_multipart:
        return 1

    # our current segment count
    segment_count = 1
    segment_size = 0

    # calculate our total number of segments, we can't do simple division because multibyte extended chars
    # may land on a boundary between messages (`{` as character 153 will not fit and force another segment)
    for c in text:
        if c in GSM7_EXTENDED_CHARS and gsm7:
            segment_size += 2
        else:
            segment_size += 1

        if gsm7 and segment_size > 153:
            segment_size -= 153
            segment_count += 1

        if not gsm7 and segment_size > 67:
            segment_size -= 67
            segment_count += 1

    return segment_count
