# -*- coding: utf-8 -*-
from __future__ import absolute_import, division, print_function, unicode_literals

import six

from django.conf import settings


def http_headers(extra=None):
    """
    Creates a dict of HTTP headers for outgoing requests
    """
    headers = settings.OUTGOING_REQUEST_HEADERS.copy()
    if extra:
        headers.update(extra)
    return headers


@six.python_2_unicode_compatible
class HttpEvent(object):

    def __init__(self, method, url, request_body=None, status_code=None, response_body=None):
        self.method = method
        self.url = url
        self.status_code = status_code
        self.response_body = response_body
        self.request_body = request_body

    def __repr__(self):
        return self.__str__()

    def __str__(self):  # pragma: no cover
        return "%s %s %s %s %s" % (self.method, self.url, self.status_code, self.response_body, self.request_body)
