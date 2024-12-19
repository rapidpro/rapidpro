import itertools
import logging
from collections import defaultdict
from datetime import datetime, timedelta, timezone as tzone

from celery import shared_task
from django_redis import get_redis_connection

from django.conf import settings
from django.db.models import F
from django.utils import timezone
from django.utils.timesince import timesince

from temba import mailroom
from temba.utils.crons import cron_task
from temba.utils.models import delete_in_batches

from .models import Flow, FlowActivityCount, FlowCategoryCount, FlowRevision, FlowRun, FlowSession, FlowStartCount

logger = logging.getLogger(__name__)


@shared_task
def update_session_wait_expires(flow_id):
    """
    Update the wait_expires_on of any session currently waiting in the given flow
    """

    flow = Flow.objects.get(id=flow_id)
    session_ids = flow.sessions.filter(status=FlowSession.STATUS_WAITING).values_list("id", flat=True)

    for id_batch in itertools.batched(session_ids, 1000):
        batch = FlowSession.objects.filter(id__in=id_batch)
        batch.update(wait_expires_on=F("wait_started_on") + timedelta(minutes=flow.expires_after_minutes))


@cron_task(lock_timeout=7200)
def squash_activity_counts():
    FlowActivityCount.squash()


@cron_task(lock_timeout=7200)
def squash_flow_counts():
    FlowCategoryCount.squash()
    FlowStartCount.squash()


@cron_task()
def trim_flow_revisions():
    start = timezone.now()

    # get when the last time we trimmed was
    r = get_redis_connection()
    last_trim = r.get(FlowRevision.LAST_TRIM_KEY)
    if not last_trim:
        last_trim = 0

    last_trim = datetime.utcfromtimestamp(int(last_trim)).astimezone(tzone.utc)
    count = FlowRevision.trim(last_trim)

    r.set(FlowRevision.LAST_TRIM_KEY, int(timezone.now().timestamp()))

    elapsed = timesince(start)
    logger.info(f"Trimmed {count} flow revisions since {last_trim} in {elapsed}")


@cron_task()
def interrupt_flow_sessions():
    """
    Interrupt old flow sessions which have exceeded the absolute time limit
    """

    before = timezone.now() - timedelta(days=89)
    num_interrupted = 0

    # get old sessions and organize into lists by org
    by_org = defaultdict(list)
    sessions = (
        FlowSession.objects.filter(created_on__lte=before, status=FlowSession.STATUS_WAITING)
        .only("id", "org")
        .select_related("org")
        .order_by("id")
    )
    for session in sessions:
        by_org[session.org].append(session)

    for org, sessions in by_org.items():
        for batch in itertools.batched(sessions, 100):
            mailroom.queue_interrupt(org, sessions=batch)
            num_interrupted += len(sessions)

    return {"interrupted": num_interrupted}


@cron_task()
def trim_flow_sessions():
    """
    Cleanup ended flow sessions
    """

    trim_before = timezone.now() - settings.RETENTION_PERIODS["flowsession"]

    def pre_delete(session_ids):
        # detach any flows runs that belong to these sessions
        FlowRun.objects.filter(session_id__in=session_ids).update(session_id=None)

    num_deleted = delete_in_batches(FlowSession.objects.filter(ended_on__lte=trim_before), pre_delete=pre_delete)

    return {"deleted": num_deleted}
