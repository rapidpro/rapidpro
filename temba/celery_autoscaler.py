# -*- coding: utf-8 -*-
from __future__ import absolute_import, unicode_literals, print_function, division

import os
import subprocess
from datetime import datetime
import tempfile

import regex

from django.conf import settings

from celery.worker.autoscale import Autoscaler
from celery.five import monotonic

AUTOSCALE_RUN_TIMEOUT = os.environ.get('AUTOSCALE_RUN_TIMEOUT', 5)

AUTOSCALE_MAX_CPU_USAGE = os.environ.get('AUTOSCALE_MAX_CPU_USAGE', 75)
AUTOSCALE_MAX_USED_MEMORY = os.environ.get('AUTOSCALE_MAX_USED_MEMORY', 75)

AUTOSCALE_MAX_WORKER_INC_BY = os.environ.get('AUTOSCALE_MAX_WORKER_INC_BY', 4)
AUTOSCALE_MAX_WORKER_DEC_BY = os.environ.get('AUTOSCALE_MAX_WORKER_DEC_BY', 4)


class SuperAutoscaler(Autoscaler):
    last_call = monotonic()

    cpu_stats = (0.0, 0.0, 0.0)
    cpu_usage = 0
    used_memory = 0
    initial_memory_usage = None

    re_total = regex.compile(r'MemTotal:\s+(?P<total>\d+)\s+kB', flags=regex.V0)
    re_available = regex.compile(r'MemAvailable:\s+(?P<available>\d+)\s+kB', flags=regex.V0)

    def __init__(self, *args, **kwargs):
        super(SuperAutoscaler, self).__init__(*args, **kwargs)

        if settings.DEBUG is True:
            self._debug_log_file = tempfile.NamedTemporaryFile(prefix='autoscaler_', suffix='.log')

        # bootstrap
        self.initial_memory_usage = self._used_memory()

    def _debug(self, msg):
        if settings.DEBUG is True:
            print('{timestamp}: {msg}'.format(timestamp=datetime.now(), msg=msg), file=self._debug_log_file)

    def scale_up(self, n):
        self._debug('SCALE_UP => %s total %s' % (n, self.processes + n))
        super(SuperAutoscaler, self).scale_up(n)

    def scale_down(self, n):
        self._debug('SCALE_DOWN => %s total %s' % (n, self.processes - n))
        super(SuperAutoscaler, self).scale_down(n)

    def _maybe_scale(self, req=None):
        if self.should_run():
            self.collect_stats()

            procs = self.processes
            if procs > 0:
                cpu_usage_per_proc = self.cpu_usage / procs
                target_cpu_bound_workers = int(
                    ((AUTOSCALE_MAX_CPU_USAGE - self.cpu_usage) / cpu_usage_per_proc) + procs
                )

                mem_usage_per_proc = (self.used_memory - self.initial_memory_usage) / procs
                target_mem_bound_workers = int(
                    ((AUTOSCALE_MAX_USED_MEMORY - self.used_memory) / mem_usage_per_proc) + procs
                )

                self._debug(
                    '_cpu => %s %s %s %s' % (
                        AUTOSCALE_MAX_USED_MEMORY, self.cpu_usage, cpu_usage_per_proc, target_cpu_bound_workers
                    )
                )
                self._debug(
                    '_mem => %s %s %s %s' % (
                        AUTOSCALE_MAX_CPU_USAGE, self.used_memory, mem_usage_per_proc, target_mem_bound_workers
                    )
                )
            else:
                target_cpu_bound_workers = 1
                target_mem_bound_workers = 1

            self._debug(
                '_maybe_scale => CON: (%s,%s), Qty: %s, CPU: %s, Mem: %s, Cur: %s' % (
                    self.min_concurrency, self.max_concurrency, self.qty, target_cpu_bound_workers,
                    target_mem_bound_workers, procs
                )
            )

            max_target_procs = min(self.qty, self.max_concurrency, target_cpu_bound_workers, target_mem_bound_workers)
            self._debug('_max_target_scale => %s' % (max_target_procs - procs))
            if max_target_procs > procs:
                self.scale_up(min((max_target_procs - procs), AUTOSCALE_MAX_WORKER_INC_BY))
                return True

            min_target_procs = max(self.min_concurrency, max_target_procs)
            self._debug('_min_target_scale => %s' % (min_target_procs - procs))
            if min_target_procs < procs:
                self.scale_down(min((procs - min_target_procs), AUTOSCALE_MAX_WORKER_DEC_BY))
                return True

    def collect_stats(self):
        self.cpu_usage = self._cpu_usage()
        self.used_memory = self._used_memory()

    def _cpu_usage(self):
        cpu_usage_data = subprocess.check_output(['grep', '-w', 'cpu', '/proc/stat']).split(' ')

        cur_stats = (float(cpu_usage_data[2]), float(cpu_usage_data[4]), float(cpu_usage_data[5]))

        cpu_usage = float(
            (self.cpu_stats[0] + self.cpu_stats[1] - cur_stats[0] - cur_stats[1]) * 100 /
            (self.cpu_stats[0] + self.cpu_stats[1] + self.cpu_stats[2] - cur_stats[0] - cur_stats[1] - cur_stats[2])
        )

        self.cpu_stats = cur_stats
        self._debug('_cpu_usage => %s' % (cpu_usage, ))
        return cpu_usage

    def _used_memory(self):
        with open('/proc/meminfo', 'rb') as f:
            mem = f.read()

        mem_ratio = (
            int(self.re_available.search(mem).group("available")) / int(self.re_total.search(mem).group("total"))
        )

        used_memory = 100 * (1 - mem_ratio)

        self._debug('_used_memory => %s' % (used_memory, ))
        return used_memory

    def should_run(self):
        current_time = monotonic()

        if current_time - self.last_call > AUTOSCALE_RUN_TIMEOUT:
            self.last_call = current_time
            return True
        else:
            return False
