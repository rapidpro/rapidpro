from __future__ import unicode_literals

from django.conf import settings
from django.db import connection, reset_queries
from temba.utils import truncate
from timeit import default_timer

MAX_QUERIES_PRINT = 16


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
            print unicode(self)

    def __unicode__(self):
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
