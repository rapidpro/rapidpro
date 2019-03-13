import logging
from collections import defaultdict
from datetime import timedelta

from django_redis import get_redis_connection

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from celery.task import task

from temba.campaigns.models import CampaignEvent, EventFire
from temba.msgs.models import FIRE_EVENT, HANDLE_EVENT_TASK
from temba.utils import chunk_list
from temba.utils.cache import QueueRecord
from temba.utils.queues import Queue, nonoverlapping_task, push_task

logger = logging.getLogger(__name__)

EVENT_FIRES_TO_TRIM = 100_000


@nonoverlapping_task(track_started=True, name="check_campaigns_task", lock_key="check_campaigns")
def check_campaigns_task():
    """
    See if any event fires need to be triggered
    """
    from temba.flows.models import Flow

    unfired = EventFire.objects.filter(
        fired=None, scheduled__lte=timezone.now(), event__flow__flow_server_enabled=False
    ).select_related("event")
    unfired = unfired.values("id", "event_id", "event__flow_id")

    # group fire events by event so they can be batched
    fire_ids_by_event_id = defaultdict(list)
    event_flow_map = dict()
    for fire in unfired:
        event_flow_map[fire["event_id"]] = fire["event__flow_id"]
        fire_ids_by_event_id[fire["event_id"]].append(fire["id"])

    # fetch the flows used by all these event fires
    flows_by_id = {flow.id: flow for flow in Flow.objects.filter(id__in=event_flow_map.values())}

    queued_fires = QueueRecord("queued_event_fires")

    # create queued tasks
    for ev_id, fire_ids in fire_ids_by_event_id.items():
        flow_id = event_flow_map[ev_id]
        flow = flows_by_id[flow_id]

        # create sub-batches no no single task is too big
        for fire_id_batch in chunk_list(fire_ids, 500):

            # ignore any fires which were queued by previous calls to this task but haven't yet been marked as fired
            queued_fire_ids = queued_fires.filter_unqueued(fire_id_batch)

            if queued_fire_ids:
                try:
                    push_task(
                        flow.org_id, Queue.HANDLER, HANDLE_EVENT_TASK, dict(type=FIRE_EVENT, fires=queued_fire_ids)
                    )

                    queued_fires.set_queued(queued_fire_ids)
                except Exception:  # pragma: no cover
                    fire_ids_str = ",".join(str(f) for f in queued_fire_ids)
                    logger.error("Error queuing campaign event fires: %s" % fire_ids_str, exc_info=True)


@task(track_started=True, name="create_event_fires_task")  # pragma: no cover
def create_event_fires(event_id):

    # get a lock
    r = get_redis_connection()
    key = "event_fires_event_%d" % event_id

    with r.lock(key, timeout=300):
        try:

            with transaction.atomic():
                event = CampaignEvent.objects.filter(pk=event_id).first()
                if event:
                    EventFire.do_create_eventfires_for_event(event)

        except Exception as e:  # pragma: no cover
            # requeue our task to try again in five minutes
            create_event_fires(event_id).delay(countdown=60 * 5)

            # bubble up the exception so sentry sees it
            raise e


@nonoverlapping_task(track_started=True, name="trim_event_fires_task")
def trim_event_fires_task():
    start = timezone.now()
    boundary = timezone.now() - timedelta(days=settings.EVENT_FIRE_TRIM_DAYS)

    # first look for unfired fires that belong to inactive events
    trim_ids = list(
        EventFire.objects.filter(fired=None, event__is_active=False).values_list("id", flat=True)[:EVENT_FIRES_TO_TRIM]
    )

    # if we have trimmed all of our unfired inactive fires, look for old fired ones
    if len(trim_ids) < EVENT_FIRES_TO_TRIM:
        trim_ids += list(
            EventFire.objects.filter(fired__lt=boundary)
            .values_list("id", flat=True)
            .order_by("fired")[: EVENT_FIRES_TO_TRIM - len(trim_ids)]
        )

    for batch in chunk_list(trim_ids, 100):
        # use a bulk delete for performance reasons, nothing references EventFire
        EventFire.objects.filter(id__in=batch).delete()

    print(f"Deleted {len(trim_ids)} event fires in {timezone.now()-start}")
