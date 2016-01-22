from __future__ import absolute_import, unicode_literals

import logging

from django.conf import settings
from django.http import HttpResponseServerError
from rest_framework.views import exception_handler

logger = logging.getLogger(__name__)


def temba_exception_handler(exc, context):
    """
    Custom exception handler which prevents responding to API requests that error with an HTML error page
    """
    response = exception_handler(exc, context)

    if response or not getattr(settings, 'REST_HANDLE_EXCEPTIONS', False):
        return response
    else:
        # ensure exception still goes to Sentry
        logger.error('Exception in API request: %s' % unicode(exc), exc_info=True)

        # respond with simple message
        return HttpResponseServerError("Server Error. Site administrators have been notified.")
