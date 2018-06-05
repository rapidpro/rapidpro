from django.utils import timezone

from celery.task import task

from temba.utils.queues import nonoverlapping_task

from .models import IVRCall


@task(name="start_call_task")
def start_call_task(call_pk):
    call = IVRCall.objects.get(pk=call_pk)
    call.do_start_call()


@nonoverlapping_task(track_started=True, name="check_calls_task", time_limit=900)
def check_calls_task():
    from .models import IVRCall

    now = timezone.now()

    calls_to_retry = (
        IVRCall.objects.filter(next_attempt__lte=now, retry_count__lte=IVRCall.MAX_RETRY_ATTEMPTS)
        .filter(direction=IVRCall.OUTGOING, is_active=True)
        .iterator()
    )

    for call in calls_to_retry:
        # TODO: which queue should be used for scheduling call retries?
        start_call_task.apply_async(kwargs={"call_pk": call.id})

        # reset the next_attempt, after we created the call task because task execution is asynchronous
        # this prevents scheduling multiple call retries while waiting for the upstream service callback request
        call.next_attempt = None
        call.save()
