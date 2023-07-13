from django.conf import settings
from django.utils import timezone

from temba.utils.crons import cron_task
from temba.utils.models import delete_in_batches

from .models import HTTPLog


@cron_task()
def trim_http_logs():
    trim_before = timezone.now() - settings.RETENTION_PERIODS["httplog"]

    num_deleted = delete_in_batches(HTTPLog.objects.filter(created_on__lte=trim_before))

    return {"deleted": num_deleted}
