# -*- coding: utf-8 -*-
from __future__ import unicode_literals

# All valid GSM7 characters, table format
VALID_GSM7 = u"@£$¥èéùìòÇ\nØø\rÅåΔ_ΦΓΛΩΠΨΣΘΞ\x1bÆæßÉ !\"#¤%&'()*+,-./0123456789:;<=>" \
             u"?¡ABCDEFGHIJKLMNOPQRSTUVWXYZÄÖÑÜ`¿abcdefghijklmnopqrstuvwxyzäöñüà" \
             u"````````````````````^```````````````````{}`````\\````````````[~]`" \
             u"|````````````````````````````````````€``````````````````````````"

# Valid GSM7 chars as a set
GSM7_CHARS = {c for c in VALID_GSM7}


def is_gsm7(text):
    """
    Returns whether the passed in text can be represented in GSM7 character set
    """
    for c in text:
        if c not in GSM7_CHARS:
            return False

    return True
