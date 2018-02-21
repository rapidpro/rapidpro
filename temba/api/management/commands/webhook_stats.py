# -*- coding: utf-8 -*-
from __future__ import absolute_import, division, print_function, unicode_literals

import six

from array import array
from collections import defaultdict
from datetime import timedelta
from django.core.management.base import BaseCommand, CommandParser
from django.utils import timezone
from six.moves.urllib.parse import urlparse
from temba.api.models import WebHookResult


class Command(BaseCommand):  # pragma: no cover
    help = "Generates statistics for web hook calls"

    COMMAND_SUMMARY = 'summary'
    COMMAND_SLOWEST = 'slowest'

    KEY_HOST = 'host'                    # host only
    KEY_HOST_AND_PATH = 'host_path'      # host and path
    KEY_HOST_AND_PATH_0 = 'host_path_0'  # host and first part of the path

    SORT_ALPHA = 'alpha'
    SORT_TIMEOUTS = 'timeouts'

    def add_arguments(self, parser):
        cmd = self
        subparsers = parser.add_subparsers(dest='command', help='Command to perform',
                                           parser_class=lambda **kw: CommandParser(cmd, **kw))

        summary_parser = subparsers.add_parser('summary', help='Summarizes URL based statistics')
        summary_parser.add_argument('--key', type=str, action='store', dest='key_by', default=self.KEY_HOST_AND_PATH_0,
                                    choices=(self.KEY_HOST, self.KEY_HOST_AND_PATH, self.KEY_HOST_AND_PATH_0),
                                    help="Key to aggregate requests over")
        summary_parser.add_argument('--sort', type=str, action='store', dest='sort_by', default=self.SORT_TIMEOUTS,
                                    choices=(self.SORT_ALPHA, self.SORT_TIMEOUTS),
                                    help="Sort aggregated results by")
        summary_parser.add_argument('--max-age', type=int, action='store', dest='max_age', default=0,
                                    help="Maximum age in minutes of results to include")

        slowest_parser = subparsers.add_parser('slowest', help='Lists slowest webhook calls')
        slowest_parser.add_argument('--max-age', type=int, action='store', dest='max_age', default=0,
                                    help="Maximum age in minutes of results to include")

    def handle(self, command, *args, **kwargs):
        if command == self.COMMAND_SUMMARY:
            self.handle_summary(kwargs['max_age'], kwargs['key_by'], kwargs['sort_by'])
        else:
            self.handle_slowest(kwargs['max_age'])

    def handle_summary(self, max_age, key_by, sort_by):
        results = WebHookResult.objects.all()

        if max_age > 0:
            created_after = timezone.now() - timedelta(minutes=max_age)
            results = results.filter(created_on__gt=created_after)

        total_results = results.count()
        self.stdout.write("Found %d webhook results to analyze..." % total_results)

        item_statuses = defaultdict(lambda: defaultdict(int))
        item_times = defaultdict(lambda: array('i'))

        num_results = 0
        for result in results.only('url', 'status_code', 'request_time'):
            item_key = self._url_to_key(result.url, key_by)

            item_statuses[item_key][result.status_code] += 1
            item_times[item_key].append(result.request_time)

            num_results += 1
            if num_results % 10000 == 0:
                self.stdout.write(" > Analyzed %d of %d results" % (num_results, total_results))

        # calculate stats for each unique item
        items = []
        for key, status_counts in six.iteritems(item_statuses):
            times = item_times[key]
            total_requests = len(times)
            avg_time = sum(times) // total_requests

            num_non200s = sum([count for code, count in six.iteritems(status_counts) if code != -1 and int(code // 100) != 2])
            num_timeouts = status_counts.get(-1, 0)

            sorted_statuses = []
            for code, count in six.iteritems(status_counts):
                if code > 0:
                    sorted_statuses.append({'code': code, 'count': count, '%': (100 * count // total_requests)})

            sorted_statuses = sorted(sorted_statuses, key=lambda s: s['code'])

            items.append({
                'key': key,
                'times': {'avg': avg_time, 'min': min(times), 'max': max(times)},
                'statuses': sorted_statuses,
                'failures': {
                    'non200': {'count': num_non200s, '%': 100 * num_non200s // total_requests},
                    'timeouts': {'count': num_timeouts, '%': 100 * num_timeouts // total_requests}
                }
            })

        if sort_by == self.SORT_ALPHA:
            items = sorted(items, key=lambda i: i['key'])
        elif sort_by == self.SORT_TIMEOUTS:
            items = sorted(items, key=lambda i: i['failures']['timeouts']['count'], reverse=True)

        self._print_summary(items)

    def handle_slowest(self, max_age):
        results = WebHookResult.objects.only('url', 'request_time').order_by('-request_time')

        if max_age > 0:
            created_after = timezone.now() - timedelta(minutes=max_age)
            results = results.filter(created_on__gt=created_after)

        for result in results[:25]:
            self.stdout.write("%s => %s secs" % (result.url, self._num_style(result.request_time)))

    def _print_summary(self, items):
        self.stdout.write("\nResponse Statistics:\n=================================")

        for item in items:
            self.stdout.write(self.style.MIGRATE_HEADING(item['key']))
            self.stdout.write(" * times: avg=%s, min=%s, max=%s" % (
                self._num_style(item['times']['avg']),
                self._num_style(item['times']['min']),
                self._num_style(item['times']['max'])
            ))

            self.stdout.write(" * timeouts: %s (%d%%)" % (
                self._num_style(item['failures']['timeouts']['count']),
                item['failures']['timeouts']['%']
            ))

            all_codes = ["%d=%s (%d%%)" % (s['code'], self._num_style(s['count']), s['%']) for s in item['statuses']]

            self.stdout.write(" * status codes:")
            self.stdout.write("   * non-200s: %s (%d%%)" % (
                self._num_style(item['failures']['non200']['count']),
                item['failures']['non200']['%']
            ))
            self.stdout.write("   * all: %s" % ', '.join(all_codes))

    def _url_to_key(self, url, by_key):
        parsed_url = urlparse(url)

        if by_key == self.KEY_HOST:
            return parsed_url.netloc
        elif by_key == self.KEY_HOST_AND_PATH:
            return parsed_url.netloc + parsed_url.path
        elif by_key == self.KEY_HOST_AND_PATH_0:
            key = parsed_url.netloc
            if parsed_url.path:
                path = parsed_url.path
                if path[0] == '/':
                    path = path[1:]

                key += ('/' + path.split('/')[0])
            return key
        else:
            return url

    def _num_style(self, v):
        return self.style.NOTICE(six.text_type(v))
