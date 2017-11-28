from __future__ import print_function, unicode_literals

import logging
import six
import time
import django

from django.conf import settings
from django.db import connection, reset_queries
from django.db.backends.utils import CursorWrapper
from temba.utils.text import truncate
from timeit import default_timer
import traceback
import textwrap


MAX_QUERIES_PRINT = 16

logger = logging.getLogger(__name__)


@six.python_2_unicode_compatible
class QueryTracker(object):  # pragma: no cover

    def print_stack(self, stack):
        for idx in range(0, self.stack_count):
            if idx < len(stack):
                print(stack[idx], end='')

    def __init__(self, sort_queries=True, skip_unique_queries=False, assert_query_count=None, query=None, stack_count=3):
        self.sort_queries = sort_queries
        self.stack_count = stack_count
        self.num_queries = assert_query_count
        self.skip_unique_queries = skip_unique_queries
        self.query = query

    def __enter__(self):

        self.old_wrapper = django.db.backends.utils.CursorWrapper
        self.old_debug_wrapper = django.db.backends.utils.CursorDebugWrapper

        queries = []
        self.queries = queries

        query = self.query

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
                if query and query not in sql:
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


@six.python_2_unicode_compatible
class SegmentProfiler(object):  # pragma: no cover
    """
    Used in a with block to profile a segment of code
    """
    def __init__(self, name, test=None, db_profile=True, assert_queries=None, assert_tx=None, force_profile=False):
        self.name = name

        self.test = test
        if self.test:
            self.test.segments.append(self)

        self.db_profile = db_profile
        self.assert_queries = assert_queries
        self.assert_tx = assert_tx

        self.old_debug = settings.DEBUG

        self.do_profile = force_profile or settings.DEBUG
        self.time_total = 0.0
        self.time_queries = 0.0
        self.queries = []

    def __enter__(self):
        if self.db_profile and self.do_profile:
            settings.DEBUG = True
            reset_queries()

        self.start_time = default_timer()

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.time_total = default_timer() - self.start_time

        if self.db_profile and self.do_profile:
            settings.DEBUG = self.old_debug
            self.queries = connection.queries
            self.num_tx = len([q for q in self.queries if q['sql'].startswith('SAVEPOINT')])

            reset_queries()

            # assert number of queries if specified
            if self.test and self.assert_queries is not None:
                self.test.assertEqual(len(self.queries), self.assert_queries)

            # assert number of transactions if specified
            if self.test and self.assert_tx is not None:
                self.test.assertEqual(self.num_tx, self.assert_tx)

        if not self.test and self.do_profile:
            print(six.text_type(self))

    def __str__(self):
        def format_query(q):
            return "Query [%s] %.3f secs" % (truncate(q['sql'], 75), float(q['time']))

        message = "Segment [%s] time: %.3f secs" % (self.name, self.time_total)
        if self.db_profile:
            num_queries = len(self.queries)
            time_db = sum([float(q['time']) for q in self.queries])

            message += ", %.3f secs db time, %d db queries, %d transaction(s)" % (time_db, num_queries, self.num_tx)

            # if we have only have a few queries, include them all in order of execution
            if len(self.queries) <= MAX_QUERIES_PRINT:
                message += ":"
                for query in self.queries:
                    message += "\n\t%s" % format_query(query)
            # if there are too many, only include slowest in order of duration
            else:
                message += ". %d slowest:" % MAX_QUERIES_PRINT
                slowest = sorted(list(self.queries), key=lambda q: float(q['time']), reverse=True)[:MAX_QUERIES_PRINT]
                for query in slowest:
                    message += "\n\t%s" % format_query(query)

        return message


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
