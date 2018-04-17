# -*- coding: utf-8 -*-
from __future__ import absolute_import, division, print_function, unicode_literals

import json
import time

from django.core.management.base import BaseCommand
from django.db import connection
from django.db.models import Count, Prefetch
from temba.flows.models import Flow, FlowRun


def audit_runs():  # pragma: no cover
    # get estimate of number of runs
    with connection.cursor() as c:
        c.execute("SELECT reltuples::BIGINT as rows FROM pg_class WHERE relname = '%s';" % FlowRun._meta.db_table)
        total_runs = c.fetchone()[0]

    print("Estimated total number of runs: %d" % total_runs)

    max_run_id = 0
    num_audited = 0

    empty_paths_active_flow = []
    empty_paths_inactive_flow = []
    path_len_step_count_mismatch = []
    event_count_message_id_mismatch = []
    unparseable_fields = []

    start = time.time()

    try:
        while True:
            run_batch = list(
                FlowRun.objects
                .filter(id__gt=max_run_id)
                .annotate(num_steps=Count('steps'))
                .extra(select={'fields_raw': 'fields'})
                .prefetch_related(Prefetch('flow', queryset=Flow.objects.only('id', 'is_active')))
                .defer('fields')
                .order_by('id')[:5000]
            )
            if not run_batch:
                break

            for run in run_batch:
                if run.num_steps > 0:  # don't include runs whose steps were purged when their flow was deleted
                    if len(run.path) == 0:
                        if run.flow.is_active:
                            empty_paths_active_flow.append(run.id)
                        else:
                            empty_paths_inactive_flow.append(run.id)

                    if len(run.path) != run.num_steps and run.num_steps < 100:  # don't include trimmed paths
                        path_len_step_count_mismatch.append(run.id)

                if len(run.events or []) < len(set(run.message_ids or [])):  # events might include purged messages
                    event_count_message_id_mismatch.append(run.id)

                if run.fields_raw is not None:
                    try:
                        json.loads(run.fields_raw)
                    except ValueError:
                        unparseable_fields.append(run.id)

            num_audited += len(run_batch)
            max_run_id = run_batch[-1].id
            time_taken = time.time() - start
            time_per_run = time_taken / num_audited
            time_remaining = (total_runs - num_audited) * time_per_run

            print(" > Audited %d / ~%d runs (est %d mins remaining)" % (num_audited, total_runs, int(time_remaining / 60)))
    except KeyboardInterrupt:
        pass

    print("Found:")
    print(" * %d runs" % num_audited)
    print(" * %d runs from active flows with steps but empty paths: %s"
          % (len(empty_paths_active_flow), ids_to_string(empty_paths_active_flow)))
    print(" * %d runs from inactive flows with steps but empty paths"
          % len(empty_paths_inactive_flow))
    print(" * %d runs with difference in path length vs step count: %s"
          % (len(path_len_step_count_mismatch), ids_to_string(path_len_step_count_mismatch)))
    print(" * %d runs with difference in event count vs message_ids: %s"
          % (len(event_count_message_id_mismatch), ids_to_string(event_count_message_id_mismatch)))
    print(" * %d runs with unparseable .fields column: %s" % (len(unparseable_fields), ids_to_string(unparseable_fields)))


class Command(BaseCommand):  # pragma: no cover
    help = "Audits all runs"

    def handle(self, *args, **options):
        audit_runs()


def ids_to_string(id_list, limit=100):
    subset = id_list[:limit]
    text = ", ".join([str(i) for i in subset])
    if len(subset) < len(id_list):
        text += "..."

    return text
