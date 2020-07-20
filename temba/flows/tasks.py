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
    FlowStart,
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


@nonoverlapping_task(track_started=True, name="squash_flowcounts", lock_timeout=7200)
def squash_flowcounts():
    FlowNodeCount.squash()
    FlowRunCount.squash()
    FlowCategoryCount.squash()
    FlowPathRecentRun.prune()
    FlowStartCount.squash()
    FlowPathCount.squash()


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
    logger.info(f"Trimmed {count} flow revisions since {last_trim} in {elapsed}")


@nonoverlapping_task(track_started=True, name="trim_flow_sessions_and_starts")
def trim_flow_sessions_and_starts():
    trim_flow_sessions()
    trim_flow_starts()


def trim_flow_sessions():
    """
    Cleanup old flow sessions
    """
    threshold = timezone.now() - timedelta(days=settings.FLOW_SESSION_TRIM_DAYS)
    num_deleted = 0
    start = timezone.now()

    logger.info(f"Deleting flow sessions which ended before {threshold.isoformat()}...")

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
    logger.info(f"Deleted {num_deleted} flow sessions which ended before {threshold.isoformat()} in {elapsed}")


def trim_flow_starts():
    """
    Cleanup completed non-user created flow starts
    """
    threshold = timezone.now() - timedelta(days=7)
    num_deleted = 0
    start = timezone.now()

    logger.info(f"Deleting completed non-user created flow starts...")

    while True:
        start_ids = list(
            FlowStart.objects.filter(
                created_by=None,
                status__in=(FlowStart.STATUS_COMPLETE, FlowStart.STATUS_FAILED),
                modified_on__lte=threshold,
            ).values_list("id", flat=True)[:1000]
        )
        if not start_ids:
            break

        # detach any flows runs that belong to these starts
        FlowRun.objects.filter(start_id__in=start_ids).update(start_id=None)

        FlowStart.contacts.through.objects.filter(flowstart_id__in=start_ids).delete()
        FlowStart.groups.through.objects.filter(flowstart_id__in=start_ids).delete()
        FlowStartCount.objects.filter(start_id__in=start_ids).delete()
        FlowStart.objects.filter(id__in=start_ids).delete()
        num_deleted += len(start_ids)

        if num_deleted % 10000 == 0:  # pragma: no cover
            print(f" > Deleted {num_deleted} flow starts")

    elapsed = timesince(start)
    logger.info(f"Deleted {num_deleted} completed non-user created flow starts in {elapsed}")
