from http.client import HTTPResponse
from io import BytesIO

import urllib3

from django.conf import settings


def http_headers(extra=None):
    """
    Creates a dict of HTTP headers for outgoing requests
    """
    headers = settings.OUTGOING_REQUEST_HEADERS.copy()
    if extra:
        headers.update(extra)
    return headers


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


def parse_response(data):
    """
    Parses a saved HTTP response trace, e.g. "HTTP/1.1 200 OK\r\n\r\n{\"errors\":[]}"
    """

    class BytesIOSocket:
        def __init__(self, content):
            self.handle = BytesIO(content)

        def makefile(self, mode):
            return self.handle

    response = HTTPResponse(BytesIOSocket(data.encode("utf-8")))
    response.begin()

    return urllib3.HTTPResponse.from_httplib(response)
