import logging
from datetime import datetime, timedelta

import iso8601
import pytz
from django_redis import get_redis_connection

from django.conf import settings
from django.utils import timezone
from django.utils.timesince import timesince

from celery.task import task

from temba.utils.celery import nonoverlapping_task

from .models import (
    ExportFlowResultsTask,
    FlowCategoryCount,
    FlowNodeCount,
    FlowPathCount,
    FlowPathRecentRun,
    FlowRevision,
    FlowRun,
    FlowRunCount,
    FlowSession,
    FlowStartCount,
)

FLOW_TIMEOUT_KEY = "flow_timeouts_%y_%m_%d"
logger = logging.getLogger(__name__)


@task(track_started=True, name="update_run_expirations_task")
def update_run_expirations_task(flow_id):
    """
    Update all of our current run expirations according to our new expiration period
    """
    for run in FlowRun.objects.filter(flow_id=flow_id, is_active=True):
        if run.path:
            last_arrived_on = iso8601.parse_date(run.path[-1]["arrived_on"])
            run.update_expiration(last_arrived_on)


@task(track_started=True, name="export_flow_results_task")
def export_flow_results_task(export_id):
    """
    Export a flow to a file and e-mail a link to the user
    """
    ExportFlowResultsTask.objects.select_related("org").get(id=export_id).perform()


@nonoverlapping_task(
    track_started=True, name="squash_flowpathcounts", lock_key="squash_flowpathcounts", lock_timeout=7200
)
def squash_flowpathcounts():
    FlowPathCount.squash()


@nonoverlapping_task(
    track_started=True, name="squash_flowruncounts", lock_key="squash_flowruncounts", lock_timeout=7200
)
def squash_flowruncounts():
    FlowNodeCount.squash()
    FlowRunCount.squash()
    FlowCategoryCount.squash()
    FlowPathRecentRun.prune()
    FlowStartCount.squash()


@nonoverlapping_task(track_started=True, name="trim_flow_revisions")
def trim_flow_revisions():
    start = timezone.now()

    # get when the last time we trimmed was
    r = get_redis_connection()
    last_trim = r.get(FlowRevision.LAST_TRIM_KEY)
    if not last_trim:
        last_trim = 0

    last_trim = datetime.utcfromtimestamp(int(last_trim)).astimezone(pytz.utc)
    count = FlowRevision.trim(last_trim)

    r.set(FlowRevision.LAST_TRIM_KEY, int(timezone.now().timestamp()))

    elapsed = timesince(start)
    print(f"Trimmed {count} flow revisions since {last_trim} in {elapsed}")


@nonoverlapping_task(track_started=True, name="trim_flow_sessions")
def trim_flow_sessions():
    """
    Cleanup old flow sessions
    """
    threshold = timezone.now() - timedelta(days=settings.FLOW_SESSION_TRIM_DAYS)
    num_deleted = 0
    start = timezone.now()

    print(f"Deleting flow sessions which ended before {threshold.isoformat()}...")

    while True:
        session_ids = list(FlowSession.objects.filter(ended_on__lte=threshold).values_list("id", flat=True)[:1000])
        if not session_ids:
            break

        # detach any flows runs that belong to these sessions
        FlowRun.objects.filter(session_id__in=session_ids).update(session_id=None)

        FlowSession.objects.filter(id__in=session_ids).delete()
        num_deleted += len(session_ids)

        if num_deleted % 10000 == 0:  # pragma: no cover
            print(f" > Deleted {num_deleted} flow sessions")

    elapsed = timesince(start)
    print(f"Deleted {num_deleted} flow sessions which ended before {threshold.isoformat()} in {elapsed}")
