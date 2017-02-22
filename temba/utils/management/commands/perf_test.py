# coding=utf-8
from __future__ import unicode_literals

import fnmatch
import json
import sys
import time

from datetime import datetime
from django.core.management.base import BaseCommand
from django.test import Client
from django.utils.http import urlquote_plus
from temba.contacts.models import ContactGroup
from temba.orgs.models import Org

# default number of times per org to request each URL to determine min/max times
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
    def __init__(self, url, times, prev_times):
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
        return {'url': self.url, 'times': self.times, 'pass': self.is_pass()}


class Command(BaseCommand):  # pragma: no cover
    help = "Runs performance tests on a database generated with make_test_db"

    def add_arguments(self, parser):
        parser.add_argument('--include', type=str, action='store', dest='url_include_pattern', default=None)
        parser.add_argument('--num-requests', type=int, action='store', dest='num_requests', default=DEFAULT_NUM_REQUESTS)
        parser.add_argument('--results-file', type=str, action='store', dest='results_file', default=DEFAULT_RESULTS_FILE)
        parser.add_argument('--results-html', type=str, action='store', dest='results_html', default=None)

    def handle(self, url_include_pattern, num_requests, results_file, results_html, *args, **options):
        self.client = Client()
        started = datetime.utcnow()

        prev_times = self.load_previous_times(results_file)

        if not prev_times:
            self.stdout.write(self.style.WARNING("No previous results found for change calculation"))

        test_orgs = [Org.objects.first(), Org.objects.last()]

        results = self.run_tests(TEST_URLS, test_orgs, url_include_pattern, num_requests, prev_times)

        self.save_results(results_file, started, results)

        if results_html:
            self.save_html_results(results_html, results)

        if any([not r.is_pass() for r in results]):
            sys.exit(1)

    def run_tests(self, urls, orgs, url_include_pattern, requests_per_org, prev_times):
        """
        Tests the given URLs for the given orgs
        """
        # build a URL context for each org
        url_context_by_org = {}
        for org in orgs:
            url_context_by_org[org] = {}
            for key, value in URL_CONTEXT_TEMPLATE.items():
                url_context_by_org[org][key] = value(org) if callable(value) else value

        results = []
        for url in urls:
            if url_include_pattern and not fnmatch.fnmatch(url, url_include_pattern):
                continue

            prev_url_times = prev_times.get(url)

            results.append(self.test_url(url, orgs, url_context_by_org, requests_per_org, prev_url_times))

        return results

    def test_url(self, url, orgs, org_contexts, requests_per_org, prev_times):
        """
        Test a single URL for the given orgs
        """
        self.stdout.write(" > %s " % url, ending='')

        url_times = []

        for org in orgs:
            formatted_url = url.format(**org_contexts[org])

            # login in as an org administrator
            self.client.force_login(org.administrators.first())

            for r in range(requests_per_org):
                start_time = time.time()
                response = self.client.get(formatted_url)
                assert response.status_code == 200
                url_times.append(time.time() - start_time)

        result = URLResult(url, url_times, prev_times)

        self.stdout.write(self.format_result(result))
        return result

    def format_result(self, result):
        """
        Formats a result like 0.024...0.037 (▼ -0.001, -3%)
        """
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
        """
        Extracts URL times from a previous results file so they can be compared with current times
        """
        try:
            with open(results_file, 'r') as f:
                times_by_url = {}
                for test in json.load(f)['results']:
                    times_by_url[test['url']] = test['times']
                return times_by_url
        except (IOError, ValueError, KeyError):
            return {}

    def save_results(self, path, started, results):
        with open(path, 'w') as f:
            json.dump({
                'started': started.isoformat(),
                'results': [r.as_json() for r in results]
            }, f, indent=4)

    def save_html_results(self, path, results):
        header = """<table style="font-family: Arial, Helvetica, sans-serif; border-spacing: 0; border-collapse: separate;">
        <tr>
            <th style="padding: 5px; text-align: left">URL</th>
            <th style="padding: 5px">Min (secs)</th>
            <th style="padding: 5px">Max (secs)</th>
            <th style="padding: 5px">Change (secs)</th>
            <th style="padding: 5px">Change (%)</th>
        </tr>
        """
        footer = """</table>"""

        with open(path, 'w') as f:
            f.write(header)
            for result in results:
                if result.change:
                    arrow = '&#8593; ' if result.change > 0 else '&#8595; '
                    change = '%s %.3f' % (arrow, result.change)
                    percentage_change = '%d' % result.percentage_change
                else:
                    change = ''
                    percentage_change = ''

                row_bg = 'dbffe3' if result.is_pass() else 'ffe0e0'
                max_bg = 'ffafaf' if result.exceeds_maximum else row_bg
                change_bg = 'ffafaf' if result.exceeds_change else row_bg

                f.write('<tr style="background-color: #%s">' % row_bg)
                f.write('<td style="padding: 5px">%s</td>' % result.url)
                f.write('<td style="padding: 5px">%.3f</td>' % result.min)
                f.write('<td style="padding: 5px; background-color: #%s">%.3f</td>' % (max_bg, result.max))
                f.write('<td style="padding: 5px">%s</td>' % change)
                f.write('<td style="padding: 5px; background-color: #%s"">%s</td>' % (change_bg, percentage_change))
                f.write('</tr>')
            f.write(footer)
