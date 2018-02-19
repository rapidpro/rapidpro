# -*- coding: utf-8 -*-
from __future__ import absolute_import, division, print_function, unicode_literals

import logging
import six

from django.conf import settings
from django.http import HttpResponseServerError
from rest_framework import exceptions, status
from rest_framework.authentication import TokenAuthentication
from rest_framework.exceptions import APIException
from rest_framework.renderers import BrowsableAPIRenderer
from rest_framework.throttling import ScopedRateThrottle
from rest_framework.views import exception_handler
from .models import APIToken

logger = logging.getLogger(__name__)


class APITokenAuthentication(TokenAuthentication):
    """
    Simple token based authentication.

    Clients should authenticate by passing the token key in the "Authorization"
    HTTP header, prepended with the string "Token ".  For example:

        Authorization: Token 401f7ac837da42b97f613d789819ff93537bee6a
    """
    model = APIToken

    def authenticate_credentials(self, key):
        try:
            token = self.model.objects.get(is_active=True, key=key)
        except self.model.DoesNotExist:
            raise exceptions.AuthenticationFailed('Invalid token')

        if token.user.is_active:
            # set the org on this user
            token.user.set_org(token.org)

            return token.user, token

        raise exceptions.AuthenticationFailed('User inactive or deleted')


class OrgRateThrottle(ScopedRateThrottle):
    """
    Throttle class which rate limits across an org
    """
    def get_cache_key(self, request, view):
        ident = None
        if request.user.is_authenticated():
            org = request.user.get_org()
            if org:
                ident = org.pk

        return self.cache_format % {'scope': self.scope, 'ident': ident or self.get_ident(request)}


class DocumentationRenderer(BrowsableAPIRenderer):
    """
    The regular REST framework browsable API renderer includes a form on each endpoint. We don't provide that and
    instead have a separate API explorer page. This render then just displays the endpoint docs.
    """
    def get_context(self, data, accepted_media_type, renderer_context):
        view = renderer_context['view']
        request = renderer_context['request']
        response = renderer_context['response']
        renderer = self.get_default_renderer(view)

        return {
            'content': self.get_content(renderer, data, accepted_media_type, renderer_context),
            'view': view,
            'request': request,
            'response': response,
            'description': view.get_view_description(html=True),
            'name': self.get_name(view),
            'breadcrumblist': self.get_breadcrumbs(request),
        }

    def render(self, data, accepted_media_type=None, renderer_context=None):
        """
        Usually one customizes the browsable view by overriding the rest_framework/api.html template but we have two
        versions of the API to support with two different templates.
        """
        if not renderer_context:  # pragma: needs cover
            raise ValueError("Can't render without context")

        request_path = renderer_context['request'].path
        api_version = 1 if request_path.startswith('/api/v1') else 2

        self.template = 'api/v%d/api_root.html' % api_version

        return super(DocumentationRenderer, self).render(data, accepted_media_type, renderer_context)


class InvalidQueryError(APIException):
    """
    Exception class for invalid queries in list endpoints
    """
    status_code = status.HTTP_400_BAD_REQUEST


def temba_exception_handler(exc, context):
    """
    Custom exception handler which prevents responding to API requests that error with an HTML error page
    """
    response = exception_handler(exc, context)

    if response or not getattr(settings, 'REST_HANDLE_EXCEPTIONS', False):
        return response
    else:
        # ensure exception still goes to Sentry
        logger.error('Exception in API request: %s' % six.text_type(exc), exc_info=True)

        # respond with simple message
        return HttpResponseServerError("Server Error. Site administrators have been notified.")
