import logging
from datetime import datetime, timedelta

import pytz
from celery import shared_task
from django_redis import get_redis_connection

from django.conf import settings
from django.db.models import F, Prefetch
from django.utils import timezone
from django.utils.timesince import timesince

from temba.contacts.models import ContactField, ContactGroup
from temba.utils import chunk_list
from temba.utils.crons import cron_task

from .models import (
    ExportFlowResultsTask,
    Flow,
    FlowCategoryCount,
    FlowNodeCount,
    FlowPathCount,
    FlowRevision,
    FlowRun,
    FlowRunStatusCount,
    FlowSession,
    FlowStart,
    FlowStartCount,
)

FLOW_TIMEOUT_KEY = "flow_timeouts_%y_%m_%d"
logger = logging.getLogger(__name__)


@shared_task
def update_session_wait_expires(flow_id):
    """
    Update the wait_expires_on of any session currently waiting in the given flow
    """

    flow = Flow.objects.get(id=flow_id)
    session_ids = flow.sessions.filter(status=FlowSession.STATUS_WAITING).values_list("id", flat=True)

    for id_batch in chunk_list(session_ids, 1000):
        batch = FlowSession.objects.filter(id__in=id_batch)
        batch.update(wait_expires_on=F("wait_started_on") + timedelta(minutes=flow.expires_after_minutes))


@shared_task
def export_flow_results_task(export_id):
    """
    Export a flow to a file and e-mail a link to the user
    """
    ExportFlowResultsTask.objects.select_related("org", "created_by").prefetch_related(
        Prefetch("with_fields", ContactField.objects.order_by("name")),
        Prefetch("with_groups", ContactGroup.objects.order_by("name")),
    ).get(id=export_id).perform()


@cron_task(lock_timeout=7200)
def squash_flow_counts():
    FlowNodeCount.squash()
    FlowRunStatusCount.squash()
    FlowCategoryCount.squash()
    FlowStartCount.squash()
    FlowPathCount.squash()


@cron_task()
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


@cron_task()
def trim_flow_sessions():
    """
    Cleanup old flow sessions
    """

    trim_before = timezone.now() - settings.RETENTION_PERIODS["flowsession"]
    num_deleted = 0

    while True:
        session_ids = list(FlowSession.objects.filter(ended_on__lte=trim_before).values_list("id", flat=True)[:1000])
        if not session_ids:
            break

        # detach any flows runs that belong to these sessions
        FlowRun.objects.filter(session_id__in=session_ids).update(session_id=None)

        FlowSession.objects.filter(id__in=session_ids).delete()
        num_deleted += len(session_ids)

    return {"deleted": num_deleted}


@cron_task()
def trim_flow_starts() -> int:
    """
    Cleanup completed non-user created flow starts
    """

    trim_before = timezone.now() - settings.RETENTION_PERIODS["flowstart"]
    num_deleted = 0

    while True:
        start_ids = list(
            FlowStart.objects.filter(
                created_by=None,
                status__in=(FlowStart.STATUS_COMPLETE, FlowStart.STATUS_FAILED),
                modified_on__lte=trim_before,
            ).values_list("id", flat=True)[:1000]
        )
        if not start_ids:
            break

        # detach any flows runs that belong to these starts
        run_ids = FlowRun.objects.filter(start_id__in=start_ids).values_list("id", flat=True)[:100000]
        while len(run_ids) > 0:
            for chunk in chunk_list(run_ids, 1000):
                FlowRun.objects.filter(id__in=chunk).update(start_id=None)

            # reselect for our next batch
            run_ids = FlowRun.objects.filter(start_id__in=start_ids).values_list("id", flat=True)[:100000]

        FlowStart.contacts.through.objects.filter(flowstart_id__in=start_ids).delete()
        FlowStart.groups.through.objects.filter(flowstart_id__in=start_ids).delete()
        FlowStartCount.objects.filter(start_id__in=start_ids).delete()
        FlowStart.objects.filter(id__in=start_ids).delete()
        num_deleted += len(start_ids)

    return {"deleted": num_deleted}
