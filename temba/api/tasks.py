from __future__ import unicode_literals

from datetime import timedelta
from django.utils import timezone
from djcelery_transactions import task
from redis_cache import get_redis_connection
from .models import WebHookEvent, WebHookResult, COMPLETE, FAILED, ERRORED, PENDING


@task(track_started=True, name='deliver_event_task')
def deliver_event_task(event_id):  # pragma: no cover
    # get a lock
    r = get_redis_connection()

    # try to acquire a lock, at most it will last 1 min
    key = 'deliver_event_%d' % event_id

    if not r.get(key):
        with r.lock(key, timeout=60):
            # load our event and try to deliver it
            event = WebHookEvent.objects.get(pk=event_id)

            if event.status != COMPLETE and event.status != FAILED:
                result = event.deliver()

                # record our result.  We do this here and not in deliver() because we want to allow
                # testing of web hooks in the UI without having to actually create any model objects
                WebHookResult.record_result(event, result)


@task(track_started=True, name='retry_events_task')
def retry_events_task():  # pragma: no cover
    print "** retrying errored webhook events"

    # get all events that have an error and need to be retried
    now = timezone.now()
    for event in WebHookEvent.objects.filter(status=ERRORED, next_attempt__lte=now):
        deliver_event_task.delay(event.pk)

    # also get those over five minutes old that are still pending
    five_minutes_ago = now - timedelta(minutes=5)
    for event in WebHookEvent.objects.filter(status=PENDING, created_on__lte=five_minutes_ago):
        deliver_event_task.delay(event.pk)

    # and any that were errored and haven't been retried for some reason
    fifteen_minutes_ago = now - timedelta(minutes=15)
    for event in WebHookEvent.objects.filter(status=ERRORED, modified_on__lte=fifteen_minutes_ago):
        deliver_event_task.delay(event.pk)
