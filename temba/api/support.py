import logging

from rest_framework import exceptions, status
from rest_framework.authentication import BasicAuthentication, SessionAuthentication, TokenAuthentication
from rest_framework.exceptions import APIException
from rest_framework.pagination import CursorPagination
from rest_framework.renderers import BrowsableAPIRenderer
from rest_framework.throttling import ScopedRateThrottle

from django.conf import settings
from django.http import HttpResponseServerError

from temba.utils import str_to_bool

from .models import APIToken

logger = logging.getLogger(__name__)


class RequestAttributesMixin:
    """
    DRF authentication happens in the view level so request.org won't have been set by OrgMiddleware and needs to be
    passed back here.
    """

    def authenticate(self, request):
        result = super().authenticate(request)

        # result is either tuple of (user,token) or None
        org = result[1].org if result else None

        # set org on the original wrapped request object
        request._request.org = org
        return result


class APITokenAuthentication(RequestAttributesMixin, TokenAuthentication):
    """
    Simple token based authentication.

    Clients should authenticate by passing the token key in the "Authorization"
    HTTP header, prepended with the string "Token ".  For example:

        Authorization: Token 401f7ac837da42b97f613d789819ff93537bee6a
    """

    model = APIToken
    select_related = ("user", "user__settings", "org", "org__parent")

    def authenticate_credentials(self, key):
        try:
            token = self.model.objects.select_related(*self.select_related).get(is_active=True, key=key)
        except self.model.DoesNotExist:
            raise exceptions.AuthenticationFailed("Invalid token")

        if token.user.is_active:
            token.record_used()
            return token.user, token

        raise exceptions.AuthenticationFailed("Invalid token")


class APIBasicAuthentication(RequestAttributesMixin, BasicAuthentication):
    """
    Basic authentication.

    Clients should authenticate using HTTP Basic Authentication.

    Credentials: username:api_token
    """

    def authenticate_credentials(self, userid, password, request=None):
        try:
            token = APIToken.objects.get(is_active=True, key=password)
        except APIToken.DoesNotExist:
            raise exceptions.AuthenticationFailed("Invalid token or email")

        if token.user.username != userid:
            raise exceptions.AuthenticationFailed("Invalid token or email")

        if token.user.is_active:
            return token.user, token

        raise exceptions.AuthenticationFailed("Invalid token or email")


class APISessionAuthentication(SessionAuthentication):
    """
    Session authentication as used by the editor, explorer
    """


class OrgUserRateThrottle(ScopedRateThrottle):
    """
    Throttle class which rate limits at an org level or user level for staff users
    """

    def get_org_rate(self, request, by_token: bool):
        default_rates = settings.REST_FRAMEWORK.get("DEFAULT_THROTTLE_RATES", {})
        org_rates = {}
        if request.user.is_authenticated and by_token:
            org_rates = request.org.api_rates
        return {**default_rates, **org_rates}.get(self.scope)

    def allow_request(self, request, view):
        by_token = isinstance(request.auth, APIToken)

        # any request not using a token (e.g. editor, explorer) isn't subject to throttling
        if request.user.is_authenticated and not by_token:
            return True

        self.scope = getattr(view, self.scope_attr, None)

        # Determine the allowed request rate considering the org config
        self.rate = self.get_org_rate(request, by_token)
        self.num_requests, self.duration = self.parse_rate(self.rate)

        return super(ScopedRateThrottle, self).allow_request(request, view)

    def get_cache_key(self, request, view):
        org = request.org
        user = request.user
        ident = None

        if user.is_authenticated:
            ident = f"{org.id if org else 0}"  # scope to org

            # but staff users get their own scope within the org
            if user.is_staff:
                ident += f"-{user.id}"

        return self.cache_format % {"scope": self.scope, "ident": ident or self.get_ident(request)}


class DocumentationRenderer(BrowsableAPIRenderer):
    """
    The regular REST framework browsable API renderer includes a form on each endpoint. We don't provide that and
    instead have a separate API explorer page. This render then just displays the endpoint docs.
    """

    template = "api/docs.html"

    def get_context(self, data, accepted_media_type, renderer_context):
        view = renderer_context["view"]
        request = renderer_context["request"]
        response = renderer_context["response"]
        renderer = self.get_default_renderer(view)

        return {
            "content": self.get_content(renderer, data, accepted_media_type, renderer_context),
            "view": view,
            "request": request,
            "response": response,
            "description": view.get_view_description(html=True),
            "name": self.get_name(view),
        }


class CreatedOnCursorPagination(CursorPagination):
    ordering = ("-created_on", "-id")
    offset_cutoff = 100000


class ModifiedOnCursorPagination(CursorPagination):
    ordering = ("-modified_on", "-id")
    offset_cutoff = 100000

    def get_ordering(self, request, queryset, view):
        if str_to_bool(request.GET.get("reverse")):
            return "modified_on", "id"
        else:
            return self.ordering


class SentOnCursorPagination(CursorPagination):
    ordering = ("-sent_on", "-id")
    offset_cutoff = 100000


class DateJoinedCursorPagination(CursorPagination):
    ordering = ("-date_joined", "-id")
    offset_cutoff = 100000


class InvalidQueryError(APIException):
    """
    Exception class for invalid queries in list endpoints
    """

    status_code = status.HTTP_400_BAD_REQUEST


def temba_exception_handler(exc, context):
    """
    Custom exception handler which prevents responding to API requests that error with an HTML error page
    """
    from rest_framework.views import exception_handler

    response = exception_handler(exc, context)

    if response or not getattr(settings, "REST_HANDLE_EXCEPTIONS", False):
        return response
    else:
        # ensure exception still goes to Sentry
        logger.error("Exception in API request: %s" % str(exc), exc_info=True)

        # respond with simple message
        return HttpResponseServerError("Server Error. Site administrators have been notified.")
