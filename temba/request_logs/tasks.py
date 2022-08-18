import logging

from django.conf import settings
from django.utils import timezone
from django.utils.timesince import timesince

from temba.utils.celery import nonoverlapping_task

from .models import HTTPLog


logger = logging.getLogger(__name__)


@nonoverlapping_task(track_started=True, name="trim_http_logs_task")
def trim_http_logs_task():
    trim_before = timezone.now() - settings.RETENTION_PERIODS["httplog"]
    num_deleted = 0
    start = timezone.now()

    logger.info(f"Deleting http logs which ended before {trim_before.isoformat()}...")

    while True:
        http_log_ids = HTTPLog.objects.filter(created_on__lte=trim_before).values_list("id", flat=True)[:1000]

        if not http_log_ids:
            break

        HTTPLog.objects.filter(id__in=http_log_ids).delete()
        num_deleted += len(http_log_ids)

        if num_deleted % 10000 == 0:  # pragma: no cover
            print(f" > Deleted {num_deleted} http logs")

    logger.info(f"Deleted {num_deleted} http logs in {timesince(start)}")
