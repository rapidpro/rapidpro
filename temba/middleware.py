# -*- coding: utf-8 -*-
from __future__ import absolute_import, division, print_function, unicode_literals

import cProfile
import pstats
import traceback

from django.conf import settings
from django.utils import timezone, translation
from io import StringIO
from temba.orgs.models import Org
from temba.contacts.models import Contact


class ExceptionMiddleware(object):

    def process_exception(self, request, exception):
        if settings.DEBUG:
            traceback.print_exc()

        return None


class OrgHeaderMiddleware(object):
    """
    Simple middleware to add a response header with the current org id, which can then be included in logs
    """
    def process_response(self, request, response):
        # if we have a user, log our org id
        if hasattr(request, 'user') and request.user.is_authenticated():
            org = request.user.get_org()
            if org:
                response['X-Temba-Org'] = org.id
        return response


class BrandingMiddleware(object):

    @classmethod
    def get_branding_for_host(cls, host):

        brand_key = host

        # ignore subdomains
        if len(brand_key.split('.')) > 2:  # pragma: needs cover
            brand_key = '.'.join(brand_key.split('.')[-2:])

        # prune off the port
        if ':' in brand_key:
            brand_key = brand_key[0:brand_key.rindex(':')]

        # override with site specific branding if we have that
        branding = settings.BRANDING.get(brand_key, None)

        if branding:
            branding['brand'] = brand_key
        else:
            # if that brand isn't configured, use the default
            branding = settings.BRANDING.get(settings.DEFAULT_BRAND)

        return branding

    def process_request(self, request):
        """
        Check for any branding options based on the current host
        """
        host = 'localhost'
        try:
            host = request.get_host()
        except Exception:  # pragma: needs cover
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
    """
    Resets Contact.set_simulation(False) for every request
    """
    def process_request(self, request):
        Contact.set_simulation(False)
        return None


class ProfilerMiddleware(object):  # pragma: no cover
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
            self.profiler = cProfile.Profile()
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
