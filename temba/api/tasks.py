from __future__ import print_function, unicode_literals

from celery.task import task
from datetime import timedelta

from django.conf import settings
from django.utils import timezone
from django_redis import get_redis_connection

from temba.utils.queues import nonoverlapping_task
from .models import WebHookEvent, WebHookResult, COMPLETE, FAILED, ERRORED, PENDING, FLOW


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
    print("** retrying errored webhook events")

    # get all events that have an error and need to be retried
    now = timezone.now()
    for event in WebHookEvent.objects.filter(status=ERRORED, next_attempt__lte=now).exclude(event=FLOW):
        deliver_event_task.delay(event.pk)

    # also get those over five minutes old that are still pending
    five_minutes_ago = now - timedelta(minutes=5)
    for event in WebHookEvent.objects.filter(status=PENDING, created_on__lte=five_minutes_ago).exclude(event=FLOW):
        deliver_event_task.delay(event.pk)

    # and any that were errored and haven't been retried for some reason
    fifteen_minutes_ago = now - timedelta(minutes=15)
    for event in WebHookEvent.objects.filter(status=ERRORED, modified_on__lte=fifteen_minutes_ago).exclude(event=FLOW):
        deliver_event_task.delay(event.pk)


@nonoverlapping_task(track_started=True, name='trim_webhook_event_task')
def trim_webhook_event_task():
    """
    Runs daily and clears any webhoook events older than settings.SUCCESS_LOGS_TRIM_TIME(default: 48) hours.
    """

    # keep success messages for only SUCCESS_LOGS_TRIM_TIME hours
    success_logs_trim_time = settings.SUCCESS_LOGS_TRIM_TIME

    # keep errors for 30 days
    error_logs_trim_time = settings.ERROR_LOGS_TRIM_TIME

    if success_logs_trim_time:
        two_days_ago = timezone.now() - timedelta(hours=success_logs_trim_time)
        WebHookEvent.objects.filter(created_on__lte=two_days_ago, status=COMPLETE).delete()

    if error_logs_trim_time:
        month_ago = timezone.now() - timedelta(hours=error_logs_trim_time)
        WebHookEvent.objects.filter(created_on__lte=month_ago).delete()
