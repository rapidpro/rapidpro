from django_redis import get_redis_connection

from django.utils import timezone

from celery.task import task

from temba.channels.models import ChannelLog
from temba.utils.http import HttpEvent
from temba.utils.locks import NonBlockingLock
from temba.utils.queues import nonoverlapping_task

from .models import IVRCall


@task(bind=True, name="start_call_task", max_retries=3)
def start_call_task(self, call_pk):
    call = IVRCall.objects.get(pk=call_pk)

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
        .filter(direction=IVRCall.OUTGOING, is_active=True)
    )

    for call in calls_to_retry:

        ChannelLog.log_ivr_interaction(call, "Retrying call", HttpEvent(method="INTERNAL", url=None))

        call.status = IVRCall.QUEUED
        call.next_attempt = None
        # reset the call
        call.started_on = None
        call.ended_on = None
        call.duration = 0
        call.modified_on = timezone.now()
        call.save()

        start_call_task.apply_async(kwargs={"call_pk": call.id}, queue="handler")
