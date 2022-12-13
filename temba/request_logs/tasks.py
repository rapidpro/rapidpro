import logging

from django.conf import settings
from django.utils import timezone

from temba.utils.crons import cron_task

from .models import HTTPLog

logger = logging.getLogger(__name__)


@cron_task()
def trim_http_logs():
    trim_before = timezone.now() - settings.RETENTION_PERIODS["httplog"]
    num_deleted = 0

    while True:
        http_log_ids = HTTPLog.objects.filter(created_on__lte=trim_before).values_list("id", flat=True)[:1000]

        if not http_log_ids:
            break

        HTTPLog.objects.filter(id__in=http_log_ids).delete()
        num_deleted += len(http_log_ids)

    return {"deleted": num_deleted}
