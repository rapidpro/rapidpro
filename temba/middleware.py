from __future__ import absolute_import, unicode_literals

import pstats
import traceback

from cStringIO import StringIO
from django.conf import settings
from django.db import transaction
from django.utils import timezone, translation
from temba.orgs.models import Org
from temba.contacts.models import Contact
from temba.settings import BRANDING, DEFAULT_BRAND, HOSTNAME

try:
    import cProfile as profile
except ImportError:
    import profile


class ExceptionMiddleware(object):

    def process_exception(self, request, exception):
        if settings.DEBUG:
            traceback.print_exc(exception)

        return None


class BrandingMiddleware(object):

    @classmethod
    def get_branding_for_host(cls, host):
        # ignore subdomains
        if len(host.split('.')) > 2:
            host = '.'.join(host.split('.')[-2:])

        # prune off the port
        if ':' in host:
            host = host[0:host.rindex(':')]

        # our default branding
        branding = BRANDING.get(HOSTNAME, BRANDING.get(DEFAULT_BRAND))

        # override with site specific branding if we have that
        site_branding = BRANDING.get(host, None)
        if site_branding:
            branding = branding.copy()
            branding.update(site_branding)

        # stuff in the incoming host
        branding['host'] = host

        return branding

    def process_request(self, request):
        """
        Check for any branding options based on the current host
        """
        host = 'localhost'
        try:
            host = request.get_host()
        except Exception:
            traceback.print_exc()

        request.branding = BrandingMiddleware.get_branding_for_host(host)


class ActivateLanguageMiddleware(object):

    def process_request(self, request):
        user = request.user
        language = request.branding.get('language', settings.DEFAULT_LANGUAGE)
        if user.is_anonymous() or user.is_superuser:
            translation.activate(language)

        else:
            user_settings = user.get_settings()
            translation.activate(user_settings.language)


class OrgTimezoneMiddleware(object):

    def process_request(self, request):
        user = request.user
        org = None

        if not user.is_anonymous():

            org_id = request.session.get('org_id', None)
            if org_id:
                org = Org.objects.filter(is_active=True, pk=org_id).first()

            # only set the org if they are still a user or an admin
            if org and (user.is_superuser or user.is_staff or user in org.get_org_users()):
                user.set_org(org)

            # otherwise, show them what orgs are available
            else:
                user_orgs = user.org_admins.all() | user.org_editors.all() | user.org_viewers.all() | user.org_surveyors.all()
                user_orgs = user_orgs.distinct('pk')

                if user_orgs.count() == 1:
                    user.set_org(user_orgs[0])

            org = request.user.get_org()

        if org:
            timezone.activate(org.timezone)
        else:
            timezone.activate(settings.USER_TIME_ZONE)

        return None


class FlowSimulationMiddleware(object):
    def process_request(self, request):
        Contact.set_simulation(False)
        return None


class ProfilerMiddleware(object):
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
    def can(self, request):
        return settings.DEBUG and 'prof' in request.GET

    def process_view(self, request, callback, callback_args, callback_kwargs):
        if self.can(request):
            self.profiler = profile.Profile()
            args = (request,) + callback_args
            return self.profiler.runcall(callback, *args, **callback_kwargs)

    def process_response(self, request, response):
        if self.can(request):
            self.profiler.create_stats()
            io = StringIO()
            stats = pstats.Stats(self.profiler, stream=io)
            stats.strip_dirs().sort_stats(request.GET.get('sort', 'time'))
            stats.print_stats(int(request.GET.get('count', 100)))
            response.content = '<pre>%s</pre>' % io.getvalue()
        return response


class NonAtomicGetsMiddleware(object):
    """
    Django's non_atomic_requests decorator gives us no way of enabling/disabling transactions depending on the request
    type. This middleware will make the current request non-atomic if an _non_atomic_gets attribute is set on the view
    function, and if the request method is GET.
    """
    def process_view(self, request, view_func, view_args, view_kwargs):
        if getattr(view_func, '_non_atomic_gets', False):
            if request.method.lower() == 'get':
                transaction.non_atomic_requests(view_func)
            else:
                view_func._non_atomic_requests = set()
        return None
