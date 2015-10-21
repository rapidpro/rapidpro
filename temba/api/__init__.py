from __future__ import absolute_import, unicode_literals

from django.http import HttpResponseServerError
from rest_framework.views import exception_handler


def temba_exception_handler(exc):
    """
    Custom exception handler which prevents responding to API requests that error with an HTML error page
    """
    response = exception_handler(exc)

    if response:
        return response
    else:
        return HttpResponseServerError("Server Error. Site administrators have been notified.")
