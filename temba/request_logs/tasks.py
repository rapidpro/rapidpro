from django.conf import settings
from django.utils import timezone

from temba.utils import chunk_list
from temba.utils.celery import nonoverlapping_task

from .models import HTTPLog


@nonoverlapping_task(track_started=True, name="trim_http_logs_task")
def trim_http_logs_task():
    trim_before = timezone.now() - settings.RETENTION_PERIODS["httplog"]
    ids = HTTPLog.objects.filter(created_on__lte=trim_before).values_list("id", flat=True)
    for chunk in chunk_list(ids, 1000):
        HTTPLog.objects.filter(id__in=chunk).delete()
