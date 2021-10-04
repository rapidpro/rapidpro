from django.conf import settings
from django.utils import timezone

from temba.utils import chunk_list
from temba.utils.celery import nonoverlapping_task

from .models import WebHookEvent


@nonoverlapping_task(track_started=True, name="trim_webhook_event_task")
def trim_webhook_event_task():
    """
    Trims old webhook events
    """

    if settings.RETENTION_PERIODS["webhookevent"]:
        trim_before = timezone.now() - settings.RETENTION_PERIODS["webhookevent"]
        event_ids = WebHookEvent.objects.filter(created_on__lte=trim_before).values_list("id", flat=True)
        for batch in chunk_list(event_ids, 1000):
            WebHookEvent.objects.filter(id__in=batch).delete()
