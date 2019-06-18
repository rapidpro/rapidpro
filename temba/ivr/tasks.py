from datetime import timedelta

from django_redis import get_redis_connection

from django.utils import timezone

from celery.task import task

from temba.channels.models import Channel, ChannelLog
from temba.utils.celery import nonoverlapping_task
from temba.utils.http import HttpEvent
from temba.utils.locks import NonBlockingLock

from .models import IVRCall


@task(bind=True, name="start_call_task", max_retries=3)
def start_call_task(self, call_pk):
    call = IVRCall.objects.select_related("channel").get(pk=call_pk)

    lock_key = f"ivr_call_start_task_contact_{call.contact_id}"
    with NonBlockingLock(redis=get_redis_connection(), name=lock_key, timeout=60) as lock:
        lock.exit_if_not_locked()

        call.do_start_call()

    # ContextManager and Celery both use exceptions for flow control, we need to retry the task after the ContextManager
    if not lock.acquired:
        # Celery default Retry timeout is 3 minutes
        self.retry(countdown=180)


@nonoverlapping_task(track_started=True, name="check_calls_task", time_limit=900)
def check_calls_task():
    from .models import IVRCall

    now = timezone.now()

    calls_to_retry = (
        IVRCall.objects.filter(next_attempt__lte=now, retry_count__lte=IVRCall.MAX_RETRY_ATTEMPTS)
        .filter(status__in=IVRCall.RETRY_CALL)
        .filter(modified_on__gt=now - timedelta(days=IVRCall.IGNORE_PENDING_CALLS_OLDER_THAN_DAYS))
        .filter(direction=IVRCall.OUTGOING)
    )

    for call in calls_to_retry:

        ChannelLog.log_ivr_interaction(call, "Retrying call", HttpEvent(method="INTERNAL", url=None))

        call.status = IVRCall.PENDING
        call.next_attempt = None
        # reset the call
        call.started_on = None
        call.ended_on = None
        call.duration = 0
        call.modified_on = timezone.now()
        call.save(update_fields=("status", "next_attempt", "started_on", "ended_on", "duration", "modified_on"))

    if calls_to_retry:
        task_enqueue_call_events.apply_async()


@nonoverlapping_task(track_started=True, name="check_failed_calls_task", time_limit=900)
def check_failed_calls_task():
    from .models import IVRCall

    # calls that have failed and have a `error_count` value are going to be retried
    failed_calls_to_retry = (
        IVRCall.objects.filter(error_count__gte=1, error_count__lte=IVRCall.MAX_ERROR_COUNT)
        .filter(status__in=IVRCall.FAILED)
        .filter(modified_on__gt=timezone.now() - timedelta(days=IVRCall.IGNORE_PENDING_CALLS_OLDER_THAN_DAYS))
        .filter(direction=IVRCall.OUTGOING)
    )

    for call in failed_calls_to_retry:

        ChannelLog.log_ivr_interaction(call, "Retrying failed call", HttpEvent(method="INTERNAL", url=None))

        call.status = IVRCall.PENDING
        # reset the call
        call.started_on = None
        call.ended_on = None
        call.duration = 0
        call.modified_on = timezone.now()
        call.save(update_fields=("status", "next_attempt", "started_on", "ended_on", "duration", "modified_on"))

    if failed_calls_to_retry:
        task_enqueue_call_events.apply_async()


@nonoverlapping_task(track_started=True, name="task_enqueue_call_events", time_limit=900)
def task_enqueue_call_events():
    from .models import IVRCall

    r = get_redis_connection()

    pending_call_events = (
        IVRCall.objects.filter(status=IVRCall.PENDING)
        .filter(direction=IVRCall.OUTGOING)
        .filter(channel__is_active=True)
        .filter(modified_on__gt=timezone.now() - timedelta(days=IVRCall.IGNORE_PENDING_CALLS_OLDER_THAN_DAYS))
        .select_related("channel")
        .order_by("modified_on")[:1000]
    )

    for call in pending_call_events:

        # are we handling a call on a throttled channel ?
        max_concurrent_events = call.channel.config.get(Channel.CONFIG_MAX_CONCURRENT_EVENTS)

        if max_concurrent_events:
            channel_key = Channel.redis_active_events_key(call.channel_id)
            current_active_events = r.get(channel_key)

            # skip this call if are on the limit
            if current_active_events and int(current_active_events) >= max_concurrent_events:
                continue
            else:
                # we can start a new call event
                call.register_active_event()

        # enqueue the call
        ChannelLog.log_ivr_interaction(call, "Call queued internally", HttpEvent(method="INTERNAL", url=None))

        call.status = IVRCall.QUEUED
        call.save(update_fields=("status",))

        start_call_task.apply_async(kwargs={"call_pk": call.id})
