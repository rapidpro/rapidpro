# -*- coding: utf-8 -*-
from __future__ import absolute_import, division, print_function, unicode_literals

from .settings import *  # noqa


# -----------------------------------------------------------------------------------
# Flowserver - on Travis we start a GoFlow instance at http://localhost:8800
# -----------------------------------------------------------------------------------
FLOW_SERVER_URL = 'http://localhost:8800'

DATABASES = {
    'default': {
        'ENGINE': 'django.contrib.gis.db.backends.postgis',
        'NAME': 'temba',
        'USER': 'temba',
        'PASSWORD': 'temba',
        'HOST': 'localhost',
        'PORT': '',
        'ATOMIC_REQUESTS': True,
        'CONN_MAX_AGE': 60,
        'OPTIONS': {}
    }
}
