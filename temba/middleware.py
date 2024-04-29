import cProfile
import logging
import pstats
import traceback
from io import StringIO

from django.conf import settings
from django.utils import timezone, translation

from temba.orgs.models import Org
from temba.utils import brands

logger = logging.getLogger(__name__)


class ExceptionMiddleware:
    def __init__(self, get_response=None):
        self.get_response = get_response

    def __call__(self, request):
        response = self.get_response(request)

        return response

    def process_exception(self, request, exception):
        if settings.DEBUG:
            traceback.print_exc()

        return None


class BrandingMiddleware:
    def __init__(self, get_response=None):
        self.get_response = get_response

    def __call__(self, request):
        """
        Set branding for this request based on the current host
        """

        host = "localhost"
        try:
            host = request.get_host()
        except Exception as e:  # pragma: needs cover
            logger.error(f"Could not get host: {host}, {str(e)}", exc_info=True)

        request.branding = self.get_branding_for_host(host)

        return self.get_response(request)

    @classmethod
    def get_branding_for_host(cls, host: str) -> dict:
        # ignore subdomains
        if len(host.split(".")) > 2:  # pragma: needs cover
            host = ".".join(host.split(".")[-2:])

        # prune off the port
        if ":" in host:
            host = host[0 : host.rindex(":")]

        return brands.get_by_host(host)


class OrgMiddleware:
    """
    Determines the current org for this request and sets it on the user object on the request
    """

    def __init__(self, get_response=None):
        self.get_response = get_response

    def __call__(self, request):
        assert hasattr(request, "user"), "must be called after django.contrib.auth.middleware.AuthenticationMiddleware"

        request.org = self.determine_org(request)

        # continue the chain, which in the case of the API will set request.org
        response = self.get_response(request)

        # set a response header to make it easier to find the current org id
        if request.org:
            response["X-Temba-Org"] = request.org.id

        return response

    def determine_org(self, request):
        user = request.user

        if not user.is_authenticated:
            return None

        # check for value in session
        org_id = request.session.get("org_id", None)
        if org_id:
            org = Org.objects.filter(is_active=True, id=org_id).first()

            # only use if user actually belongs to this org
            if org and (user.is_staff or org.has_user(user)):
                return org

        # otherwise if user only belongs to one org, we can use that
        user_orgs = user.get_orgs()
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
