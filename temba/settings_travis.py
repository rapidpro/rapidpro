from __future__ import absolute_import, unicode_literals

from .settings import STATIC_URL
from .settings import *  # noqa

COMPRESS_ENABLED = True
COMPRESS_OFFLINE = True
COMPRESS_CSS_HASHING_METHOD = 'content'
COMPRESS_OFFLINE_CONTEXT = dict(STATIC_URL=STATIC_URL, base_template='frame.html', debug=False, testing=False)
