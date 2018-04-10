# -*- coding: utf-8 -*-
from __future__ import absolute_import, division, print_function, unicode_literals

from django.conf import settings

from elasticsearch import Elasticsearch

ES = Elasticsearch(hosts=[settings.ES_CONNECTION_URL])
