import cProfile
import json
import pstats
import traceback
from io import StringIO

from django.conf import settings
from django.contrib import messages
from django.utils import timezone, translation

from temba.orgs.models import Org, User


class ExceptionMiddleware:
    def __init__(self, get_response=None):
        self.get_response = get_response

    def __call__(self, request):
        return self.get_response(request)

    def process_exception(self, request, exception):
        if settings.DEBUG:
            traceback.print_exc()

        return None


class OrgMiddleware:
    """
    Determines the org for this request and sets it on the request. Also sets request.branding for convenience.
    """

    session_key = "org_id"
    header_name = "X-Temba-Org"
    service_header_name = "X-Temba-Service-Org"
    select_related = ("parent",)

    def __init__(self, get_response=None):
        self.get_response = get_response

    def __call__(self, request):
        assert hasattr(request, "user"), "must be called after django.contrib.auth.middleware.AuthenticationMiddleware"

        request.org = self.determine_org(request)
        if request.org:
            # set our current role for this org
            request.role = request.org.get_user_role(request.user)

        request.branding = settings.BRAND

        # continue the chain, which in the case of the API will set request.org
        response = self.get_response(request)

        if request.org:
            # set a response header to make it easier to find the current org id
            response[self.header_name] = request.org.id

        return response

    def determine_org(self, request):
        user = request.user

        if not user.is_authenticated:
            return None

        # check for value in session
        org_id = request.session.get(self.session_key, None)

        # staff users alternatively can pass a service header
        if user.is_staff:
            org_id = request.headers.get(self.service_header_name, org_id)

        if org_id:
            org = Org.objects.filter(is_active=True, id=org_id).select_related(*self.select_related).first()

            # only use if user actually belongs to this org
            if org and (user.is_staff or org.has_user(user)):
                return org

        # otherwise if user only belongs to one org, we can use that
        user_orgs = User.get_orgs_for_request(request)
        if user_orgs.count() == 1:
            return user_orgs[0]

        return None


class TimezoneMiddleware:
    """
    Activates the timezone for the current org
    """

    def __init__(self, get_response=None):
        self.get_response = get_response

    def __call__(self, request):
        assert hasattr(request, "org"), "must be called after temba.middleware.OrgMiddleware"

        if request.org:
            timezone.activate(request.org.timezone)
        else:
            timezone.activate(settings.USER_TIME_ZONE)

        return self.get_response(request)


class LanguageMiddleware:
    """
    Activates the translation language for the current user
    """

    def __init__(self, get_response=None):
        self.get_response = get_response

    def __call__(self, request):
        assert hasattr(request, "user"), "must be called after django.contrib.auth.middleware.AuthenticationMiddleware"

        user = request.user

        if not user.is_authenticated:
            language = request.branding.get("language", settings.DEFAULT_LANGUAGE)
            translation.activate(language)
        else:
            translation.activate(user.settings.language)

        response = self.get_response(request)
        response.headers.setdefault("Content-Language", translation.get_language())
        return response


class ToastMiddleware:
    """
    Converts django messages into a response header for toasts
    """

    def __init__(self, get_response=None):
        self.get_response = get_response

    def __call__(self, request):
        response = self.get_response(request)

        # only work on spa requests and exclude redirects
        if response.status_code == 200:
            storage = messages.get_messages(request)
            toasts = []
            for message in storage:
                toasts.append(
                    {"level": "error" if message.level == messages.ERROR else "info", "text": str(message.message)}
                )
                message.used = False

            if toasts:
                response["X-Temba-Toasts"] = json.dumps(toasts)
        return response


class ProfilerMiddleware:  # pragma: no cover
    """
    Simple profile middleware to profile django views. To run it, add ?prof to
    the URL like this:

        http://localhost:8000/view/?prof

    Optionally pass the following to modify the output:

    ?sort => Sort the output by a given metric. Default is time.
        See http://docs.python.org/2/library/profile.html#pstats.Stats.sort_stats
        for all sort options.

    ?count => The number of rows to display. Default is 100.

    This is adapted from an example found here:
    http://www.slideshare.net/zeeg/django-con-high-performance-django-presentation.
    """

    def __init__(self, get_response=None):
        self.get_response = get_response

    def __call__(self, request):
        response = self.get_response(request)

        if self.can(request):
            self.profiler.create_stats()
            io = StringIO()
            stats = pstats.Stats(self.profiler, stream=io)
            stats.strip_dirs().sort_stats(request.GET.get("sort", "time"))
            stats.print_stats(int(request.GET.get("count", 100)))
            response.content = "<pre>%s</pre>" % io.getvalue()
        return response

    def can(self, request):
        return settings.DEBUG and "prof" in request.GET

    def process_view(self, request, callback, callback_args, callback_kwargs):
        if self.can(request):
            self.profiler = cProfile.Profile()
            args = (request,) + callback_args
            return self.profiler.runcall(callback, *args, **callback_kwargs)
