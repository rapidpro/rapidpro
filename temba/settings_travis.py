# -*- coding: utf-8 -*-
from __future__ import absolute_import, division, print_function, unicode_literals

from .settings import *  # noqa


# -----------------------------------------------------------------------------------
# Flowserver - on Travis we start a GoFlow instance at http://localhost:8080
# -----------------------------------------------------------------------------------
FLOW_SERVER_URL = 'http://localhost:8080'
