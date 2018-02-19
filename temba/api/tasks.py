# -*- coding: utf-8 -*-
from __future__ import absolute_import, division, print_function, unicode_literals

from celery.task import task
from datetime import timedelta

from django.conf import settings
from django.utils import timezone
from django_redis import get_redis_connection

from temba.utils import chunk_list
from temba.utils.queues import nonoverlapping_task
from .models import WebHookEvent, WebHookResult


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

            if event.status != WebHookEvent.STATUS_COMPLETE and event.status != WebHookEvent.STATUS_FAILED:
                result = event.deliver()

                # record our result.  We do this here and not in deliver() because we want to allow
                # testing of web hooks in the UI without having to actually create any model objects
                WebHookResult.record_result(event, result)


@task(track_started=True, name='retry_events_task')
def retry_events_task():  # pragma: no cover
    print("** retrying errored webhook events")

    # get all events that have an error and need to be retried
    now = timezone.now()
    for event in WebHookEvent.objects.filter(status=WebHookEvent.STATUS_ERRORED, next_attempt__lte=now).exclude(event=WebHookEvent.TYPE_FLOW):
        deliver_event_task.delay(event.pk)

    # also get those over five minutes old that are still pending
    five_minutes_ago = now - timedelta(minutes=5)
    for event in WebHookEvent.objects.filter(status=WebHookEvent.STATUS_PENDING, created_on__lte=five_minutes_ago).exclude(event=WebHookEvent.TYPE_FLOW):
        deliver_event_task.delay(event.pk)

    # and any that were errored and haven't been retried for some reason
    fifteen_minutes_ago = now - timedelta(minutes=15)
    for event in WebHookEvent.objects.filter(status=WebHookEvent.STATUS_ERRORED, modified_on__lte=fifteen_minutes_ago).exclude(event=WebHookEvent.TYPE_FLOW):
        deliver_event_task.delay(event.pk)


@nonoverlapping_task(track_started=True, name='trim_webhook_event_task')
def trim_webhook_event_task():
    """
    Runs daily and clears any webhoook events older than settings.SUCCESS_LOGS_TRIM_TIME(default: 48) hours.
    """

    # keep success messages for only SUCCESS_LOGS_TRIM_TIME hours
    success_logs_trim_time = settings.SUCCESS_LOGS_TRIM_TIME

    # keep errors for ALL_LOGS_TRIM_TIME days
    all_logs_trim_time = settings.ALL_LOGS_TRIM_TIME

    if success_logs_trim_time:
        success_log_later = timezone.now() - timedelta(hours=success_logs_trim_time)
        event_ids = WebHookEvent.objects.filter(created_on__lte=success_log_later, status=WebHookEvent.STATUS_COMPLETE)
        event_ids = event_ids.values_list('id', flat=True)
        for batch in chunk_list(event_ids, 1000):
            WebHookEvent.objects.filter(id__in=batch).delete()

    if all_logs_trim_time:
        all_log_later = timezone.now() - timedelta(hours=all_logs_trim_time)
        event_ids = WebHookEvent.objects.filter(created_on__lte=all_log_later)
        event_ids = event_ids.values_list('id', flat=True)
        for batch in chunk_list(event_ids, 1000):
            WebHookEvent.objects.filter(id__in=batch).delete()
