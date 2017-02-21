from __future__ import unicode_literals

import fnmatch
import json
import sys
import time

from collections import OrderedDict
from datetime import datetime
from django.conf import settings
from django.core.management.base import BaseCommand
from django.test import Client
from django.utils.http import urlquote_plus
from temba.contacts.models import ContactGroup
from temba.orgs.models import Org

# default number of times to request each URL to determine min/max times
DEFAULT_NUM_REQUESTS = 3

# default file to save timing results to
DEFAULT_RESULTS_FILE = '.perf_results'

# allow this maximum request time
ALLOWED_MAXIMUM = 3

# allow this much absolute change from previous results (50ms)
ALLOWED_CHANGE_MAXIMUM = 0.05

# allow this much percentage change from previous results
ALLOWED_CHANGE_PERCENTAGE = 5

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
    '/api/v2/labels.json',
    '/api/v2/messages.json?folder=incoming',
    '/api/v2/messages.json?folder=inbox',
    '/api/v2/messages.json?folder=flows',
    '/api/v2/messages.json?folder=archived',
    '/api/v2/messages.json?folder=outbox',
    '/api/v2/messages.json?folder=sent',
    '/api/v2/org.json',
    '/contact/',
    '/contact/?search=' + urlquote_plus('gender=F'),
    '/contact/?search=' + urlquote_plus('ward=Jebuaw or ward=Gumai or ward=Dundun or ward=Dinawa'),
    '/contact/?search=' + urlquote_plus('gender=M and district=Faskari and age<30'),
    '/contact/blocked/',
    '/contact/stopped/',
    '/contact/filter/{first-group}/',
    '/contact/filter/{first-group}/?search=' + urlquote_plus('gender=F'),
    '/contact/filter/{last-group}/',
    '/contact/omnibox/?search=George',
    '/contact/omnibox/?search=07009',
)


class URLResult(object):
    def __init__(self, org, url, times, prev_times):
        self.org = org
        self.url = url
        self.times = times

        self.min = min(*self.times)
        self.max = max(*self.times)
        self.exceeds_maximum = self.max > ALLOWED_MAXIMUM

        this_avg = sum(self.times) / len(self.times)
        prev_avg = sum(prev_times) / len(prev_times) if prev_times else None
        if prev_avg is not None:
            self.change = this_avg - prev_avg
            self.percentage_change = int(100 * self.change / prev_avg)
            self.exceeds_change = self.change > ALLOWED_CHANGE_MAXIMUM and self.percentage_change > ALLOWED_CHANGE_PERCENTAGE
        else:
            self.change = None
            self.percentage_change = None
            self.exceeds_change = False

    def is_pass(self):
        return not (self.exceeds_maximum or self.exceeds_change)

    def as_json(self):
        return {'org': self.org.id, 'url': self.url, 'times': self.times, 'pass': self.is_pass()}


class Command(BaseCommand):  # pragma: no cover
    help = "Runs performance tests on a database generated with make_test_db"

    def add_arguments(self, parser):
        parser.add_argument('--include', type=str, action='store', dest='url_include_pattern', default=None)
        parser.add_argument('--num-requests', type=int, action='store', dest='num_requests', default=DEFAULT_NUM_REQUESTS)
        parser.add_argument('--results-file', type=str, action='store', dest='results_file', default=DEFAULT_RESULTS_FILE)

    def handle(self, url_include_pattern, num_requests, results_file, *args, **options):
        # override some settings so that we behave more like a production instance
        settings.ALLOWED_HOSTS = ('testserver',)
        settings.DEBUG = False
        settings.TEMPLATES[0]['OPTIONS']['debug'] = False

        self.client = Client()
        started = datetime.utcnow()

        prev_times = self.load_previous_times(results_file)

        if not prev_times:
            self.stdout.write(self.style.WARNING("No previous results found for change calculation"))

        test_orgs = [Org.objects.first(), Org.objects.last()]

        tests = []

        for org in test_orgs:
            tests += self.test_with_org(org, url_include_pattern, num_requests, prev_times)

        self.save_results(results_file, started, tests)

        if any([not t.is_pass() for t in tests]):
            sys.exit(1)

    def test_with_org(self, org, url_include_pattern, num_requests, prev_times):
        self.stdout.write(self.style.MIGRATE_HEADING("Testing with org #%d" % org.id))

        url_context = {}
        for key, value in URL_CONTEXT_TEMPLATE.items():
            url_context[key] = value(org) if callable(value) else value

        # login in as an org administrator
        self.client.force_login(org.administrators.first())

        tests = []

        for url in TEST_URLS:
            url = url.format(**url_context)

            if url_include_pattern and not fnmatch.fnmatch(url, url_include_pattern):
                continue

            self.stdout.write(" > %s " % url, ending='')

            times = self.request_times(url, num_requests)
            prev_url_times = prev_times.get((org.id, url))

            result = URLResult(org, url, times, prev_url_times)
            tests.append(result)

            self.stdout.write(self.format_result(result))

        return tests

    def request_times(self, url, num_requests):
        """
        Makes multiple requests to the given URL and returns the times
        """
        times = []
        for r in range(num_requests):
            start_time = time.time()
            response = self.client.get(url)
            assert response.status_code == 200
            times.append(time.time() - start_time)
        return times

    def format_result(self, result):
        range_str = "%.3f...%.3f" % (result.min, result.max)
        if result.exceeds_maximum:
            range_str = self.style.ERROR(range_str)
        else:
            range_str = self.style.SUCCESS(range_str)

        if result.change:
            arrow = '\u25b2' if result.change > 0 else '\u25bc'
            style = self.style.ERROR if result.exceeds_change else self.style.SUCCESS
            change_str = style('%s %.3f, %d%%' % (arrow, result.change, result.percentage_change))
        else:
            change_str = 'change unknown'

        return "%s (%s)" % (range_str, change_str)

    def load_previous_times(self, results_file):
        try:
            with open(results_file, 'r') as f:
                times_by_org_and_url = OrderedDict()
                for test in json.load(f)['tests']:
                    times_by_org_and_url[(test['org'], test['url'])] = test['times']
                return times_by_org_and_url
        except (IOError, ValueError, KeyError):
            return {}

    def save_results(self, results_file, started, tests):
        with open(results_file, 'w') as f:
            json.dump({
                'started': started.isoformat(),
                'tests': [t.as_json() for t in tests]
            }, f, indent=4)
