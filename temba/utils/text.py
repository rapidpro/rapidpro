import regex
import sys

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

else:
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
