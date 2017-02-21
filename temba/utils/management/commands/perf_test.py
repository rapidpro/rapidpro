from __future__ import unicode_literals

import fnmatch
import json
import time

from collections import OrderedDict
from datetime import datetime
from django.conf import settings
from django.core.management.base import BaseCommand
from django.test import Client
from django.utils.http import urlquote_plus
from temba.contacts.models import ContactGroup
from temba.orgs.models import Org

# time threshold for a warning and a failure
TIME_LIMITS = (0.5, 1)

# default number of times to request each URL to determine min/max times
DEFAULT_NUM_REQUESTS = 3

# default file to save timing results to
DEFAULT_RESULTS_FILE = '.perf_results'

# allow this much percentage change from previous results
ALLOWED_CHANGE_PERCENTAGE = 5

# allow this much absolute change from previous results (50ms)
ALLOWED_CHANGE_MAXIMUM = 0.05

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

        prev_results = self.load_previous_results(results_file)

        if not prev_results:
            self.stdout.write(self.style.WARNING("No previous results found for change calculation"))

        test_orgs = [Org.objects.first(), Org.objects.last()]

        tests = []
        for org in test_orgs:
            # look for previous results for this org
            prev_org_results = {}
            for test in prev_results.get('tests', []):
                if test['org'] == org.id:
                    prev_org_results = test['results']
                    break

            results = self.test_with_org(org, url_include_pattern, num_requests, prev_org_results)

            tests.append({'org': org.id, 'results': results})

        self.save_results(results_file, started, tests)

    def test_with_org(self, org, url_include_pattern, num_requests, prev_org_results):
        self.stdout.write(self.style.MIGRATE_HEADING("Testing with org #%d" % org.id))

        url_context = {}
        for key, value in URL_CONTEXT_TEMPLATE.items():
            url_context[key] = value(org) if callable(value) else value

        # login in as org administrator
        self.client.force_login(org.administrators.get())

        results = OrderedDict()

        for url in TEST_URLS:
            url = url.format(**url_context)

            if url_include_pattern and not fnmatch.fnmatch(url, url_include_pattern):
                continue

            self.stdout.write(" > %s " % url, ending='')

            times = self.request_times(url, num_requests)
            results[url] = times

            self.stdout.write(self.format_min_max(times), ending='')

            # do we have a previous result for this URL?
            prev_times = prev_org_results.get(url)
            prev_avg = sum(prev_times) / len(prev_times) if prev_times else None

            if prev_avg is not None:
                change = (prev_avg - sum(times) / len(times))
                percentage_change = int(100 * change / prev_avg)

                if abs(change) > ALLOWED_CHANGE_MAXIMUM:
                    if percentage_change > ALLOWED_CHANGE_PERCENTAGE:
                        self.stdout.write(' (' + self.style.ERROR('\u25b2 %d%%' % percentage_change) + ')', ending='')
                    elif percentage_change < -ALLOWED_CHANGE_PERCENTAGE:
                        self.stdout.write(' (' + self.style.SUCCESS('\u25bc %d%%' % percentage_change) + ')', ending='')

            self.stdout.write('')

        return results

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

    def load_previous_results(self, results_file):
        try:
            with open(results_file, 'r') as f:
                return json.load(f, object_pairs_hook=OrderedDict)
        except (IOError, ValueError):
            return {}

    def save_results(self, results_file, started, tests):
        with open(results_file, 'w') as f:
            json.dump({'started': started.isoformat(), 'tests': tests}, f, indent=4)

    def format_min_max(self, times):
        min_time, max_time = min(*times), max(*times)
        time_str = "%.3f...%.3f" % (min_time, max_time)
        if max_time < TIME_LIMITS[0]:
            return self.style.SUCCESS(time_str)
        elif max_time < TIME_LIMITS[1]:
            return self.style.WARNING(time_str)
        else:
            return self.style.ERROR(time_str)
