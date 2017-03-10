from __future__ import absolute_import, print_function, unicode_literals

import six


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
