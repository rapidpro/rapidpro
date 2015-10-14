# -*- coding: utf-8 -*-
from __future__ import unicode_literals

# All valid GSM7 characters, table format
VALID_GSM7 = u"@£$¥èéùìòÇ\nØø\rÅåΔ_ΦΓΛΩΠΨΣΘΞ\x1bÆæßÉ !\"#¤%&'()*+,-./0123456789:;<=>" \
             u"?¡ABCDEFGHIJKLMNOPQRSTUVWXYZÄÖÑÜ`¿abcdefghijklmnopqrstuvwxyzäöñüà" \
             u"````````````````````^```````````````````{}`````\\````````````[~]`" \
             u"|````````````````````````````````````€``````````````````````````"

# Valid GSM7 chars as a set
GSM7_CHARS = {c for c in VALID_GSM7}

# Characters we replace in GSM7 with versions that can actually be encoded
GSM7_REPLACEMENTS = {u'á': 'a',
                     u'ê': 'e',
                     u'ã': 'a',
                     u'â': 'a',
                     u'ç': 'c',
                     u'í': 'i',
                     u'ú': 'u',
                     u'õ': 'o',
                     u'ô': 'o',
                     u'ó': 'o',

                     u'Á': 'A',
                     u'Â': 'A',
                     u'Ã': 'A',
                     u'À': 'A',
                     u'Ç': 'C',
                     u'È': 'E',
                     u'Ê': 'E',
                     u'Í': 'I',
                     u'Ó': 'O',
                     u'Ô': 'O',
                     u'Õ': 'O',
                     u'Ú': 'U',
                     u'Ù': 'U'}


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
    return ''.join([GSM7_REPLACEMENTS.get(c, c) for c in text])
