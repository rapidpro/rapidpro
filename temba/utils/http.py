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


def body_for_http_request(data):
    """
    Given a raw HTTP request returns just the body. This just splits on double \r\n which demarkates the header and body
    of HTTP requests: https://developer.mozilla.org/en-US/docs/Web/HTTP/Messages
    """
    parts = data.split("\r\n\r\n", 1)
    if len(parts) != 2:
        return ""

    return parts[1]
