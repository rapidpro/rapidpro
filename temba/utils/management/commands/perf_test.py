from __future__ import unicode_literals

import fnmatch
import time

from django.conf import settings
from django.core.management.base import BaseCommand
from django.test import Client
from django.utils.http import urlquote_plus
from temba.contacts.models import ContactGroup
from temba.orgs.models import Org

# any request that takes longer than this number of seconds is considered a problem
TIME_LIMIT = (2, 3)

# number of times to request each URL to determine min/max times
REQUESTS_PER_URL = 3

# a org specific context used in URL generation
URL_CONTEXT_TEMPLATE = {
    'first-group': lambda org: ContactGroup.user_groups.filter(org=org).order_by('id').first().uuid,
    'last-group': lambda org: ContactGroup.user_groups.filter(org=org).order_by('-id').first().uuid
}

TEST_URLS = (
    '/api/v2/channels.json',
    '/api/v2/channel_events.json',
    '/api/v2/contacts.json',
    '/api/v2/contacts.json?deleted=true',
    '/api/v2/contacts.json?group={first-group}',
    '/api/v2/groups.json',
    '/api/v2/fields.json',
    '/api/v2/labels.json'
    '/api/v2/messages.json',
    '/api/v2/messages.json',
    '/api/v2/messages.json',
    '/api/v2/messages.json',
    '/api/v2/messages.json',
    '/api/v2/messages.json',
    '/api/v2/org.json',
    '/contact/',
    '/contact/?search=' + urlquote_plus('gender=F'),
    '/contact/?search=' + urlquote_plus('ward=Jebuaw or ward=Gumai or ward=Dundun'),
    '/contact/?search=' + urlquote_plus('gender=M and ward=Jebuaw or ward=Gumai'),
    '/contact/blocked/',
    '/contact/stopped/',
    '/contact/filter/{first-group}/',
    '/contact/filter/{last-group}/',
)


class Command(BaseCommand):  # pragma: no cover
    help = "Runs performance tests on a database generated with make_test_db"

    def add_arguments(self, parser):
        parser.add_argument('--include', type=str, action='store', dest='url_include_pattern', default=None)

    def handle(self, url_include_pattern, *args, **options):
        settings.ALLOWED_HOSTS = ('testserver',)

        self.client = Client()

        test_orgs = [Org.objects.first(), Org.objects.last()]

        for org in test_orgs:
            self.test_with_org(org, url_include_pattern)

    def test_with_org(self, org, url_include_pattern):
        self.stdout.write(self.style.MIGRATE_HEADING("Testing with org %s (#%d)" % (org.name, org.id)))

        url_context = {}
        for key, value in URL_CONTEXT_TEMPLATE.items():
            url_context[key] = value(org) if callable(value) else value

        # login in as org administrator
        self.client.force_login(org.administrators.get())

        for url in TEST_URLS:
            url = url.format(**url_context)

            if url_include_pattern and not fnmatch.fnmatch(url, url_include_pattern):
                continue

            self.stdout.write(" > %s " % url, ending='')

            min_time, max_time = self.request_times(url)

            self.stdout.write(self.color_times(min_time, max_time))

    def request_times(self, url):
        """
        Makes multiple requests to the given URL and returns the minimum and maximum times
        """
        times = []
        for r in range(REQUESTS_PER_URL):
            start_time = time.time()
            response = self.client.get(url)
            assert response.status_code == 200
            times.append(time.time() - start_time)

        return min(*times), max(*times)

    def color_times(self, min_time, max_time):
        time_str = "%.2f...%.2f" % (min_time, max_time)
        if max_time < TIME_LIMIT[0]:
            return self.style.SUCCESS(time_str)
        elif max_time < TIME_LIMIT[1]:
            return self.style.WARNING(time_str)
        else:
            return self.style.ERROR(time_str)
