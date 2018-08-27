import time

from django.core.management.base import BaseCommand
from django.db import connection
from django.db.models import Prefetch

from temba.flows.models import Flow, FlowRun
from temba.utils import json


def audit_runs(max_id=0):  # pragma: no cover
    # get estimate of number of runs
    with connection.cursor() as c:
        c.execute("SELECT reltuples::BIGINT as rows FROM pg_class WHERE relname = '%s';" % FlowRun._meta.db_table)
        total_runs = c.fetchone()[0]

    print("Estimated total number of runs: %d" % total_runs)

    if max_id:
        print("Resuming from maximum run id: %d" % max_id)

    max_run_id = max_id
    num_audited = 0
    num_problems = 0

    problem_finders = {
        "unparseable_fields": has_unparseble_fields,
        "has_duplicate_message_events": has_duplicate_message_events,
        "has_none_string_in_path": lambda r: has_none_string_in(r.path),
        "has_none_string_in_results": lambda r: has_none_string_in(r.results),
        "has_none_string_in_events": lambda r: has_none_string_in(r.events),
        # 'has_empty_path_for_active_flow': has_empty_path_for_active_flow,  # not worrying about this for now
    }

    problem_log = open("run_problems.log", "w")

    start = time.time()

    while True:
        run_batch = list(
            FlowRun.objects.filter(id__gt=max_run_id)
            .extra(select={"fields_raw": "fields"})
            .prefetch_related(Prefetch("flow", queryset=Flow.objects.only("id", "is_active")))
            .defer("fields")
            .order_by("id")[:5000]
        )
        if not run_batch:
            break

        for run in run_batch:
            for problem_name, problem_finder in problem_finders.items():
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

        print(
            " > Audited %d / ~%d runs (est %d mins remaining, %d problems found)"
            % (num_audited, total_runs, int(time_remaining / 60), num_problems)
        )

    print("Finished run audit in %.1f secs" % (time.time() - start))

    problem_log.close()


def has_unparseble_fields(run):
    if run.fields_raw is not None:
        try:
            json.loads(run.fields_raw)
        except ValueError:
            return True
    return False


def has_empty_path_for_active_flow(run):
    return len(run.path) == 0 and run.flow.is_active


def has_duplicate_message_events(run):
    seen_msg_uuids = set()
    for event in run.events or []:
        if event["type"] in ("msg_created", "msg_received"):
            msg_uuid = event["msg"].get("uuid")
            if msg_uuid:
                if msg_uuid in seen_msg_uuids:
                    return True
                seen_msg_uuids.add(msg_uuid)
    return False


class Command(BaseCommand):  # pragma: no cover
    help = "Audits all runs"

    def add_arguments(self, parser):
        parser.add_argument(
            "--resume-from", type=int, action="store", dest="resume_from", default=0, help="Resume from max run id"
        )

    def handle(self, resume_from, *args, **options):
        audit_runs(resume_from)


def has_none_string_in(json_frag):
    if isinstance(json_frag, dict):
        for k, v in json_frag.items():
            if has_none_string_in(v):
                return True
    elif isinstance(json_frag, list):
        for v in json_frag:
            if has_none_string_in(v):
                return True
    elif isinstance(json_frag, str):
        if json_frag == "None":
            return True

    return False
