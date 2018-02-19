# -*- coding: utf-8 -*-
from __future__ import absolute_import, division, print_function, unicode_literals

import logging
import six
import django
import textwrap
import time
import traceback

from django.db.backends.utils import CursorWrapper


logger = logging.getLogger(__name__)


@six.python_2_unicode_compatible
class QueryTracker(object):  # pragma: no cover

    def print_stack(self, stack):
        for idx in range(0, self.stack_count):
            if idx < len(stack):
                print(stack[idx], end='')

    def __init__(self, sort_queries=True, skip_unique_queries=False, assert_query_count=None,
                 query_contains=None, stack_count=3):
        self.sort_queries = sort_queries
        self.stack_count = stack_count
        self.num_queries = assert_query_count
        self.skip_unique_queries = skip_unique_queries
        self.query_contains = query_contains

    def __enter__(self):

        self.old_wrapper = django.db.backends.utils.CursorWrapper
        self.old_debug_wrapper = django.db.backends.utils.CursorDebugWrapper

        queries = []
        self.queries = queries

        query_contains = self.query_contains

        class CursorTrackerWrapper(CursorWrapper):  # pragma: no cover

            def valid_stack(self, item):
                file_name = item[0]

                if 'temba' not in file_name:
                    return False

                if 'temba/utils/profiler' in file_name:
                    return False

                if 'flows/tests' in file_name:
                    return False

                return True

            def execute(self, sql, params=None):
                results = super(CursorTrackerWrapper, self).execute(sql, params)
                sql = self.db.ops.last_executed_query(self.cursor, sql, params)
                if query_contains and query_contains not in sql:
                    return results

                stack = reversed([s for s in traceback.extract_stack() if self.valid_stack(s)])
                stack = traceback.format_list(stack)
                queries.append((sql, stack))
                return results

            def executemany(self, sql, param_list):
                return super(CursorTrackerWrapper, self).executemany(sql, param_list)

        django.db.backends.utils.CursorWrapper = CursorTrackerWrapper
        django.db.backends.utils.CursorDebugWrapper = CursorTrackerWrapper

    def __exit__(self, exc_type, exc_val, exc_t):
        django.db.backends.utils.CursorWrapper = self.old_wrapper
        django.db.backends.utils.CursorDebugWrapper = self.old_debug_wrapper

        if self.num_queries and len(self.queries) != self.num_queries:
            if self.sort_queries:
                self.queries.sort()
                last = None
                count = 0
                for idx, query in enumerate(self.queries):
                    (sql, stack) = query

                    if (last != sql):
                        if self.skip_unique_queries:
                            if sql not in [s[0] for s in self.queries[idx + 1:]]:
                                last = sql
                                continue
                        if count:
                            print("\n%d QUERIES" % count)

                        count = 1
                        print('\n')
                        print('=' * 100)
                        for line in textwrap.wrap(sql, 100):
                            print(line)
                        print('=' * 100)
                        self.print_stack(stack)
                    else:
                        count += 1
                        print('  ' + '-' * 96)
                        self.print_stack(stack)
                    last = sql
            else:
                for query in self.queries:
                    (sql, stack) = query
                    for line in textwrap.wrap(sql, 100):
                        print(line)
                    print(stack, end='')

            if count:
                print("\n%d QUERIES" % count)

            raise AssertionError("Executed %d queries (expected %d)" % (len(self.queries), self.num_queries))

    def __str__(self):
        return self.__class__


def time_monitor(threshold):
    """
    Method decorator to time a method call and log an error if time exceeds the given threshold in milliseconds.
    """
    def _time_monitor(func):
        def wrapper(*args, **kwargs):
            start = time.time()

            result = func(*args, **kwargs)

            time_taken = int(1000 * (time.time() - start))
            if time_taken > threshold:
                logger.error('Call to %s took %d milliseconds.' % (func.__name__, time_taken), extra={'stack': True})

            return result
        return wrapper
    return _time_monitor
