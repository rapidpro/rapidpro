from datetime import timedelta

from django.conf import settings
from django.utils import timezone

from temba.utils import chunk_list
from temba.utils.celery import nonoverlapping_task

from .models import WebHookEvent, WebHookResult


@nonoverlapping_task(track_started=True, name="trim_webhook_event_task")
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
        event_ids = WebHookEvent.objects.filter(created_on__lte=success_log_later)
        event_ids = event_ids.values_list("id", flat=True)
        for batch in chunk_list(event_ids, 1000):
            for event in WebHookEvent.objects.filter(id__in=batch):
                event.release()

        result_ids = WebHookResult.objects.filter(created_on__lte=success_log_later, status_code__lt=400).values_list(
            "id", flat=True
        )
        for batch in chunk_list(result_ids, 1000):
            for result in WebHookResult.objects.filter(id__in=batch):
                result.release()

    if all_logs_trim_time:
        all_log_later = timezone.now() - timedelta(hours=all_logs_trim_time)
        event_ids = WebHookEvent.objects.filter(created_on__lte=all_log_later)
        event_ids = event_ids.values_list("id", flat=True)
        for batch in chunk_list(event_ids, 1000):
            for event in WebHookEvent.objects.filter(id__in=batch):
                event.release()

        result_ids = WebHookResult.objects.filter(created_on__lte=all_log_later).values_list("id", flat=True)
        for batch in chunk_list(result_ids, 1000):
            for result in WebHookResult.objects.filter(id__in=batch):
                result.release()
