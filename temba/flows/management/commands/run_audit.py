# -*- coding: utf-8 -*-
from __future__ import absolute_import, division, print_function, unicode_literals

import json
import six
import time

from django.core.management.base import BaseCommand
from django.db import connection
from django.db.models import Count, Prefetch
from temba.flows.models import Flow, FlowRun


def audit_runs(max_id=0):  # pragma: no cover
    # get estimate of number of runs
    with connection.cursor() as c:
        c.execute("SELECT reltuples::BIGINT as rows FROM pg_class WHERE relname = '%s';" % FlowRun._meta.db_table)
        total_runs = c.fetchone()[0]

    print("Estimated total number of runs: %d" % total_runs)

    max_run_id = max_id
    num_audited = 0
    num_problems = 0

    problem_finders = {
        'unparseable_fields': has_unparseble_fields,
        # 'no_steps_for_active_flow': has_no_steps_for_active_flow,  # not worrying about this for now
        'less_events_than_message_ids': has_less_events_than_message_ids,
        'step_count_path_length_mismatch': has_step_count_path_length_mismatch
    }

    problem_log = open('run_problems.log', 'w')

    start = time.time()

    while True:
        run_batch = list(
            FlowRun.objects
            .filter(id__gt=max_run_id)
            .extra(select={'fields_raw': 'fields'})
            .prefetch_related(Prefetch('flow', queryset=Flow.objects.only('id', 'is_active')))
            .defer('fields')
            .order_by('id')[:5000]
        )
        if not run_batch:
            break

        step_counts = (
            FlowRun.objects.filter(id__in=[r.id for r in run_batch])
            .annotate(num_steps=Count('steps'))
            .values('id', 'num_steps')
        )
        step_counts = {sc['id']: sc['num_steps'] for sc in step_counts}

        for run in run_batch:
            run.num_steps = step_counts.get(run.id, 0)

            for problem_name, problem_finder in six.iteritems(problem_finders):
                if problem_finder(run):
                    msg = "Run #%d for flow #%d has problem: %s" % (run.id, run.flow.id, problem_name)
                    print("   ! %s" % msg)

                    problem_log.write(str(msg + "\n"))
                    problem_log.flush()

                    num_problems += 1

        num_audited += len(run_batch)
        max_run_id = run_batch[-1].id
        time_taken = time.time() - start
        time_per_run = time_taken / num_audited
        time_remaining = (total_runs - num_audited) * time_per_run

        print(" > Audited %d / ~%d runs (est %d mins remaining, %d problems found)" % (num_audited, total_runs, int(time_remaining / 60), num_problems))

    print("Finished run audit in %.1f secs" % (time.time() - start))

    problem_log.close()


def has_unparseble_fields(run):
    if run.fields_raw is not None:
        try:
            json.loads(run.fields_raw)
        except ValueError:
            return True
    return False


def has_no_steps_for_active_flow(run):
    return run.num_steps == 0 and run.flow.is_active


def has_step_count_path_length_mismatch(run):
    return min(len(run.path), 100) != min(run.num_steps, 100)  # take path trimming into account


def has_less_events_than_message_ids(run):
    return len(run.events or []) < len(set(run.message_ids or []))  # events might include purged messages


class Command(BaseCommand):  # pragma: no cover
    help = "Audits all runs"

    def handle(self, *args, **options):
        audit_runs()
