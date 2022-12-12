from django.conf import settings
from django.utils import timezone

from temba.utils import chunk_list
from temba.utils.crons import cron_task

from .models import WebHookEvent


@cron_task()
def trim_webhook_events():
    """
    Trims old webhook events
    """

    num_deleted = 0

    if settings.RETENTION_PERIODS["webhookevent"]:
        trim_before = timezone.now() - settings.RETENTION_PERIODS["webhookevent"]
        event_ids = WebHookEvent.objects.filter(created_on__lte=trim_before).values_list("id", flat=True)
        for batch in chunk_list(event_ids, 1000):
            num_deleted, _ = WebHookEvent.objects.filter(id__in=batch).delete()

    return {"deleted": num_deleted}
