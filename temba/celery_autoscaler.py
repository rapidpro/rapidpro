# -*- coding: utf-8 -*-
from __future__ import absolute_import, division, print_function, unicode_literals

import subprocess
from datetime import datetime
import tempfile

import regex

from django.conf import settings
from django.db import connection

from celery.worker.autoscale import Autoscaler
from celery.five import monotonic
from celery.utils.log import get_logger

from temba.utils import analytics


LOG = get_logger(__name__)


class SuperAutoscaler(Autoscaler):
    last_call = monotonic()

    cpu_stats = (0.0, 0.0, 0.0)
    max_cpu_bound_workers = 0
    max_memory_bound_workers = 0
    max_db_bound_workers = 0

    initial_memory_usage = None

    re_total = regex.compile(r'MemTotal:\s+(?P<total>\d+)\s+kB', flags=regex.V0)
    re_available = regex.compile(r'MemAvailable:\s+(?P<available>\d+)\s+kB', flags=regex.V0)

    def __init__(self, *args, **kwargs):
        super(SuperAutoscaler, self).__init__(*args, **kwargs)

        if settings.DEBUG is True:
            self._debug_log_file = tempfile.NamedTemporaryFile(prefix='autoscaler_', suffix='.log')

        # bootstrap
        self.initial_memory_usage = self._get_used_memory()

    def _debug(self, msg):
        if settings.DEBUG is True:
            print('{timestamp}: {msg}'.format(timestamp=datetime.now(), msg=msg), file=self._debug_log_file)

    def _maybe_scale(self, req=None):
        if self.should_run():
            self.collect_stats()

            analytics.gauge('temba.celery_active_workers_%s' % (self.bound_queues,), self.processes)

            logging_msg = '_maybe_scale => CUR: (%s) CON: (%s,%s), Qty: %s, CPU: %s, Mem: %s, Db: %s' % (
                self.processes, self.min_concurrency, self.max_concurrency, self.qty,
                self.max_cpu_bound_workers, self.max_memory_bound_workers, self.max_db_bound_workers
            )
            LOG.info(logging_msg)
            self._debug(logging_msg)

            max_target_procs = min(
                self.qty, self.max_concurrency, self.max_cpu_bound_workers, self.max_memory_bound_workers,
                self.max_db_bound_workers
            )
            if max_target_procs > self.processes:
                n = min((max_target_procs - self.processes), settings.AUTOSCALE_MAX_WORKER_INC_BY)
                self._debug('SCALE_UP => %s + %s = %s' % (self.processes, n, self.processes + n))
                self.scale_up(n)

                analytics.gauge('temba.celery_worker_scale_up_%s' % (self.bound_queues,), n)
                return True

            min_target_procs = max(self.min_concurrency, max_target_procs)
            if min_target_procs < self.processes:
                n = min((self.processes - min_target_procs), settings.AUTOSCALE_MAX_WORKER_DEC_BY)
                self._debug('SCALE_DOWN => %s - %s = %s' % (self.processes, n, self.processes - n))
                self.scale_down(n)

                analytics.gauge('temba.celery_worker_scale_down_%s' % (self.bound_queues,), n)
                return True

    def collect_stats(self):
        self.max_cpu_bound_workers = self._check_cpu_usage()
        self.max_memory_bound_workers = self._check_used_memory()
        self.max_db_bound_workers = self._check_query_execution_time()

        self.bound_queues = '_'.join(sorted([q.name for q in self.worker.consumer.task_consumer.queues]))

        if self.bound_queues == '':
            raise ValueError('Celery worker has no bound queues')

    def _check_cpu_usage(self):
        cpu_usage_data = subprocess.check_output(['grep', '-w', 'cpu', '/proc/stat']).split(' ')

        cur_stats = (float(cpu_usage_data[2]), float(cpu_usage_data[4]), float(cpu_usage_data[5]))

        cpu_usage = float(
            (self.cpu_stats[0] + self.cpu_stats[1] - cur_stats[0] - cur_stats[1]) * 100 /
            (self.cpu_stats[0] + self.cpu_stats[1] + self.cpu_stats[2] - cur_stats[0] - cur_stats[1] - cur_stats[2])
        )

        self.cpu_stats = cur_stats

        if self.processes > 0:
            if cpu_usage < settings.AUTOSCALE_MAX_CPU_USAGE:
                target_cpu_bound_workers = self.max_concurrency
            else:
                target_cpu_bound_workers = 1
        else:
            target_cpu_bound_workers = 1

        self._debug(
            '_cpu => %s %s %s' % (
                settings.AUTOSCALE_MAX_CPU_USAGE, cpu_usage, target_cpu_bound_workers
            )
        )

        return target_cpu_bound_workers

    def _check_used_memory(self):
        used_memory = self._get_used_memory()

        if self.processes > 0:
            if used_memory < settings.AUTOSCALE_MAX_USED_MEMORY:
                target_mem_bound_workers = self.max_concurrency
            else:
                target_mem_bound_workers = 1
        else:
            target_mem_bound_workers = 1

        self._debug(
            '_mem => %s %s %s' % (
                settings.AUTOSCALE_MAX_USED_MEMORY, used_memory, target_mem_bound_workers
            )
        )

        return target_mem_bound_workers

    def _get_used_memory(self):
        with open('/proc/meminfo', 'rb') as f:
            mem = f.read()
        mem_ratio = (
            int(self.re_available.search(mem).group("available")) // int(self.re_total.search(mem).group("total"))
        )
        used_memory = 100 * (1 - mem_ratio)
        return used_memory

    def _check_query_execution_time(self):
        with connection.cursor() as cursor:
            start_time = monotonic()
            cursor.execute(settings.AUTOSCALE_DB_PERFORMANCE_QUERY)
            cursor.fetchone()

            total_time = (monotonic() - start_time) * 1000.0

        if total_time < settings.AUTOSCALE_DB_QUERY_EXECUTION_MS:
            # if we are not limited by the db, scale to max_concurrency
            target_db_bound_workers = self.max_concurrency
        else:
            target_db_bound_workers = 1

        self._debug(
            '_db => %s %s %s' % (
                settings.AUTOSCALE_DB_QUERY_EXECUTION_MS, total_time, target_db_bound_workers
            )
        )
        return target_db_bound_workers

    def should_run(self):
        current_time = monotonic()

        if current_time - self.last_call > self.keepalive:
            self.last_call = current_time
            return True
        else:
            return False
