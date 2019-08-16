import logging
from datetime import timedelta

from django_redis import get_redis_connection

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from celery.task import task

from temba.campaigns.models import CampaignEvent, EventFire
from temba.utils import chunk_list
from temba.utils.celery import nonoverlapping_task

logger = logging.getLogger(__name__)

EVENT_FIRES_TO_TRIM = 100_000


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
