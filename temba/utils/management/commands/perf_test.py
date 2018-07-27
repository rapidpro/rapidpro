# -*- coding: utf-8 -*-
# coding=utf-8
from __future__ import absolute_import, division, print_function, unicode_literals

import fnmatch
import json
import sys
import time

from datetime import datetime, timedelta
from django.core.management.base import BaseCommand, CommandError
from django.test import Client
from django.utils.timezone import now
from django.utils.http import urlquote_plus
from temba.contacts.models import ContactGroup
from temba.orgs.models import Org
from temba.utils import datetime_to_str, get_datetime_format

# default number of times per org to request each URL to determine min/max times
DEFAULT_NUM_REQUESTS = 3

# default file to save timing results to
DEFAULT_RESULTS_FILE = '.perf_results'

# allow this maximum request time (milliseconds)
DEFAULT_ALLOWED_MAXIMUM = 3000

# allow this much absolute change from previous results (milliseconds)
ALLOWED_CHANGE_MAXIMUM = 50

# allow this much percentage change from previous results
ALLOWED_CHANGE_PERCENTAGE = 5

# a org specific context used in URL generation
URL_CONTEXT_TEMPLATE = {
    'first-group': lambda org: ContactGroup.user_groups.filter(org=org).order_by('id').first().uuid,
    'last-group': lambda org: ContactGroup.user_groups.filter(org=org).order_by('-id').first().uuid,
    '1-year-ago': lambda org: datetime_to_str(now() - timedelta(days=365), get_datetime_format(org.get_dayfirst())[0])
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
    '/contact/?search=2507001',
    '/contact/?search=' + urlquote_plus('name is Dave or tel has 2507001'),
    '/contact/?search=' + urlquote_plus('gender=F'),
    '/contact/?search=' + urlquote_plus('joined=""'),
    '/contact/?search=' + urlquote_plus('joined!=""'),
    '/contact/?search=' + urlquote_plus('district=Wudil or district=Anka or district=Zuru or district=Kaura or '
                                        'district=Giwa or district=Kalgo or district=Shanga or district=Bunza'),
    '/contact/?search=' + urlquote_plus('gender=M and state=Katsina and age<40 and joined>') + '{1-year-ago}',
    '/contact/?search=' + urlquote_plus('(gender=M and district=Faskari) or (gender=F and district=Zuru)'),
    '/contact/blocked/',
    '/contact/stopped/',
    '/contact/filter/{first-group}/',
    '/contact/filter/{first-group}/?search=' + urlquote_plus('gender=F'),
    '/contact/filter/{last-group}/',
    '/contact/omnibox/?search=',
    '/contact/omnibox/?search=George',
    '/contact/omnibox/?search=07009',
    '/msg/inbox/',
    '/msg/flow/',
    '/msg/flow/?search=' + urlquote_plus('poots seattle'),
    '/msg/archived/',
    '/msg/outbox/',
    '/msg/sent/',
    '/org/home/',
)


class URLResult(object):
    def __init__(self, url, times, allowed_max, prev_times):
        self.url = url
        self.times = times

        self.min = min(*self.times)
        self.max = max(*self.times)
        self.exceeds_maximum = self.max > allowed_max

        this_avg = sum(self.times) / len(self.times)
        prev_avg = sum(prev_times) / len(prev_times) if prev_times else None
        if prev_avg is not None:
            self.change = this_avg - prev_avg
            self.percentage_change = int(100 * self.change // prev_avg)
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
        parser.add_argument('--include', type=str, action='store', dest='include_pattern', default=None,
                            help="Only test URLs matching this pattern.")
        parser.add_argument('--orgs', type=str, action='store', dest='org_ids', default=None,
                            help="Comma separated database ids of orgs to test. Defaults to first and last org.")
        parser.add_argument('--org-limits', type=str, action='store', dest='org_limits', default=None,
                            help="Comma separated hard time limits for each org in milliseconds.")
        parser.add_argument('--num-requests', type=int, action='store', dest='num_requests', default=DEFAULT_NUM_REQUESTS,
                            help="Number of requests to make for each URL. Default is %d." % DEFAULT_NUM_REQUESTS)
        parser.add_argument('--results-file', type=str, action='store', dest='results_file', default=DEFAULT_RESULTS_FILE,
                            help="Path of file to write results to. Default is '%s'." % DEFAULT_RESULTS_FILE)
        parser.add_argument('--results-html', type=str, action='store', dest='results_html', default=None,
                            help="Path of file to write HTML results to. Default is none.")

    def handle(self, include_pattern, org_ids, org_limits, num_requests, results_file, results_html, *args, **options):
        self.client = Client()
        started = datetime.utcnow()

        prev_times = self.load_previous_times(results_file)

        if not prev_times:
            self.stdout.write(self.style.WARNING("No previous results found for change calculation"))

        # gather which orgs will be tested
        if org_ids:
            test_orgs = list(Org.objects.filter(id__in=org_ids.split(',')).order_by('id'))
        else:
            test_orgs = [Org.objects.first(), Org.objects.last()]

        # gather org specific maximum time limits
        if org_limits:
            org_allowed_maximums = [int(l) for l in org_limits.split(',')]
            if len(org_allowed_maximums) != len(test_orgs):
                raise CommandError("%d time limits provided for %d orgs" % (len(org_limits), len(test_orgs)))
        else:
            org_allowed_maximums = [DEFAULT_ALLOWED_MAXIMUM] * len(test_orgs)

        test_urls = [u for u in TEST_URLS if not include_pattern or fnmatch.fnmatch(u, include_pattern)]

        results = []

        for o, org in enumerate(test_orgs):
            org.allowed_max = org_allowed_maximums[o]

            # look for previous times for this org
            prev_org_times = prev_times.get(org.id, {})

            results.append(self.test_org(org, test_urls, num_requests, prev_org_times))

        self.save_results(results_file, started, results)

        if results_html:
            self.save_html_results(results_html, results)

        # exit with no-zero code if any test for any org failed
        for result in results:
            for test in result['tests']:
                if not test.is_pass():
                    sys.exit(1)

    def test_org(self, org, urls, num_requests, prev_times):
        """
        Tests the given URLs for the given org
        """
        self.stdout.write(self.style.MIGRATE_HEADING("Org #%d (%d contacts, %dms max allowed)"
                                                     % (org.id, org.org_contacts.count(), org.allowed_max)))

        # build a URL context for this org
        url_context = {}
        for key, value in URL_CONTEXT_TEMPLATE.items():
            url_context[key] = value(org) if callable(value) else value

        # login in as an org administrator
        self.client.force_login(org.administrators.first())

        tests = []
        for url in urls:
            formatted_url = url.format(**url_context)

            prev_url_times = prev_times.get(formatted_url)

            tests.append(self.test_url(formatted_url, num_requests, org.allowed_max, prev_url_times))

        return {'org': org, 'tests': tests}

    def test_url(self, url, num_requests, allowed_max, prev_times):
        """
        Test a single URL
        """
        self.stdout.write(" > %s " % url, ending='')

        url_times = []

        for r in range(num_requests):
            start_time = time.time()
            response = self.client.get(url)

            if response.status_code != 200:
                raise CommandError("URL %s returned an unexpected %d response" % (url, response.status_code))

            url_times.append(int(1000 * (time.time() - start_time)))

        result = URLResult(url, url_times, allowed_max, prev_times)

        self.stdout.write(self.format_result(result))
        return result

    def format_result(self, result):
        """
        Formats a result like 24...370 (▼ -1, -3%)
        """
        range_str = "%d...%d" % (result.min, result.max)
        if result.exceeds_maximum:
            range_str = self.style.ERROR(range_str)
        else:
            range_str = self.style.SUCCESS(range_str)

        if result.change:
            arrow = '\u25b2' if result.change > 0 else '\u25bc'
            style = self.style.ERROR if result.exceeds_change else self.style.SUCCESS
            change_str = style('%s %d, %d%%' % (arrow, result.change, result.percentage_change))
        elif result.change == 0:
            change_str = 'no change'
        else:
            change_str = 'change unknown'

        return "%s (%s)" % (range_str, change_str)

    def load_previous_times(self, results_file):
        """
        Extracts URL times from a previous results file so they can be compared with current times
        """
        try:
            with open(results_file, 'r') as f:
                org_times = {}
                for result in json.load(f)['results']:
                    org_times[result['org']] = {}
                    for test in result['tests']:
                        org_times[result['org']][test['url']] = test['times']
                return org_times
        except (IOError, ValueError, KeyError):
            return {}

    def save_results(self, path, started, results):
        with open(path, 'w') as f:
            json.dump({
                'started': started.isoformat(),
                'results': [
                    {'org': r['org'].id, 'tests': [t.as_json() for t in r['tests']]} for r in results
                ]
            }, f, indent=4)

    def save_html_results(self, path, results):
        header = """<table style="font-family: Arial, Helvetica, sans-serif; border-spacing: 0; border-collapse: separate;">"""
        footer = """</table>"""
        with open(path, 'w') as f:
            f.write(header)
            for result in results:
                self.write_org_html(f, result['org'], result['tests'])

            f.write(footer)

    def write_org_html(self, f, org, tests):
        f.write("""
        <tr style="background-color: #2f4970; color: white">
            <th style="padding: 5px" colspan="5">Org #%d (%d contacts, %dms max allowed)</th>
        </tr>
        <tr style="background-color: #f2f6fc; color: #2f4970">
            <th style="padding: 5px; text-align: left">URL</th>
            <th style="padding: 5px">Min (ms)</th>
            <th style="padding: 5px">Max (ms)</th>
            <th style="padding: 5px">Change (ms)</th>
            <th style="padding: 5px">Change (%%)</th>
        </tr>
        """ % (org.id, org.org_contacts.count(), org.allowed_max))

        for test in tests:
            if test.change:
                arrow = '&#8593; ' if test.change > 0 else '&#8595; '
                change = '%s %d' % (arrow, test.change)
                percentage_change = '%d' % test.percentage_change
            else:
                change = ''
                percentage_change = ''

            row_bg = 'dbffe3' if test.is_pass() else 'ffe0e0'
            max_bg = 'ffafaf' if test.exceeds_maximum else row_bg
            change_bg = 'ffafaf' if test.exceeds_change else row_bg

            f.write('<tr style="background-color: #%s">' % row_bg)
            f.write('<td style="padding: 5px">%s</td>' % test.url)
            f.write('<td style="padding: 5px">%d</td>' % test.min)
            f.write('<td style="padding: 5px; background-color: #%s">%d</td>' % (max_bg, test.max))
            f.write('<td style="padding: 5px">%s</td>' % change)
            f.write('<td style="padding: 5px; background-color: #%s"">%s</td>' % (change_bg, percentage_change))
            f.write('</tr>')
