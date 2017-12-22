from __future__ import print_function, unicode_literals

import six

from array import array
from collections import defaultdict
from django.core.management.base import BaseCommand
from six.moves.urllib.parse import urlparse
from temba.api.models import WebHookResult


class Command(BaseCommand):  # pragma: no cover
    help = "Generates statistics for web hook calls"

    KEY_HOST = 'host'                    # host only
    KEY_HOST_AND_PATH = 'host_path'      # host and path
    KEY_HOST_AND_PATH_0 = 'host_path_0'  # host and first part of the path

    SORT_ALPHA = 'alpha'
    SORT_TIMEOUTS = 'timeouts'

    def add_arguments(self, parser):
        parser.add_argument('--key', type=str, action='store', dest='key_by', default=self.KEY_HOST_AND_PATH_0,
                            choices=(self.KEY_HOST, self.KEY_HOST_AND_PATH, self.KEY_HOST_AND_PATH_0),
                            help="Key to aggregate requests over")
        parser.add_argument('--sort', type=str, action='store', dest='sort_by', default=self.SORT_TIMEOUTS,
                            choices=(self.SORT_ALPHA, self.SORT_TIMEOUTS),
                            help="Sort aggregated results by")

    def handle(self, key_by, sort_by, *args, **options):
        self.analyze(key_by, sort_by)

    def analyze(self, key_by, sort_by):
        total_results = WebHookResult.objects.count()
        self.stdout.write("Found %d webhook results to analyze..." % total_results)

        item_statuses = defaultdict(lambda: defaultdict(int))
        item_times = defaultdict(lambda: array('i'))

        num_results = 0
        for result in WebHookResult.objects.only('url', 'status_code', 'request_time'):
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
            avg_time = sum(times) / total_requests

            num_non200s = sum([count for code, count in six.iteritems(status_counts) if code != -1 and int(code / 100) != 2])
            num_timeouts = status_counts.get(-1, 0)

            sorted_statuses = []
            for code, count in six.iteritems(status_counts):
                if code > 0:
                    sorted_statuses.append({'code': code, 'count': count, '%': (100 * count / total_requests)})

            sorted_statuses = sorted(sorted_statuses, key=lambda s: s['code'])

            items.append({
                'key': key,
                'times': {'avg': avg_time, 'min': min(times), 'max': max(times)},
                'statuses': sorted_statuses,
                'failures': {
                    'non200': {'count': num_non200s, '%': 100 * num_non200s / total_requests},
                    'timeouts': {'count': num_timeouts, '%': 100 * num_timeouts / total_requests}
                }
            })

        if sort_by == self.SORT_ALPHA:
            items = sorted(items, key=lambda i: i['key'])
        elif sort_by == self.SORT_TIMEOUTS:
            items = sorted(items, key=lambda i: i['failures']['timeouts']['count'], reverse=True)

        self._print_results(items)

    def _print_results(self, items):
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
