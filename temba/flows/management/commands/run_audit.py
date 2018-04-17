# -*- coding: utf-8 -*-
from __future__ import absolute_import, division, print_function, unicode_literals

import time

from django.core.management.base import BaseCommand
from django.db import connection
from temba.flows.models import FlowRun


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
    null_events = []
    event_count_message_id_mismatch = []

    start = time.time()

    while True:
        run_batch = list(FlowRun.objects.filter(id__gt=max_run_id).order_by('id').select_related('flow').defer('fields')[:1000])
        if not run_batch:
            break

        for run in run_batch:
            if len(run.path) == 0:
                if run.flow.is_active:
                    empty_paths_active_flow.append(run.id)
                else:
                    empty_paths_inactive_flow.append(run.id)

            if run.events is None:
                null_events.append(run.id)

            if len(run.events or []) != len(set(run.message_ids or [])):
                event_count_message_id_mismatch.append(run.id)

        num_audited += len(run_batch)
        max_run_id = run_batch[-1].id
        time_taken = time.time() - start
        time_per_run = time_taken / num_audited
        time_remaining = (total_runs - num_audited) * time_per_run

        print(" > Audited %d / ~%d runs (est %d mins remaining)" % (num_audited, total_runs, int(time_remaining / 60)))

    print("Found %d runs" % num_audited)
    print("Found %d runs from active flows with empty paths: %s"
          % (len(empty_paths_active_flow), ids_to_string(empty_paths_active_flow)))
    print("Found %d runs from inactive flows with empty paths"
          % len(empty_paths_inactive_flow))
    print("Found %d runs with NULL events field" % len(null_events))
    print("Found %d runs with difference in event count vs message_ids: %s"
          % (len(event_count_message_id_mismatch), ids_to_string(event_count_message_id_mismatch)))


class Command(BaseCommand):  # pragma: no cover
    help = "Audits all runs"

    def handle(self, *args, **options):
        audit_runs()


def ids_to_string(id_list, limit=100):
    subset = id_list[:limit]
    str = ", ".join([str(i) for i in subset])
    if len(subset) < len(id_list):
        str += "..."

    return str
