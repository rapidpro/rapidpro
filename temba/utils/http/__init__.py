from django.conf import settings

from .logs import HttpLog  # noqa


def http_headers(extra=None):
    """
    Creates a dict of HTTP headers for outgoing requests
    """
    headers = settings.OUTGOING_REQUEST_HEADERS.copy()
    if extra:
        headers.update(extra)
    return headers
