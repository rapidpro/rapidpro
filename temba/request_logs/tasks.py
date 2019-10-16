from .models import HTTPLog
from temba.utils.celery import nonoverlapping_task


@nonoverlapping_task(track_started=True, name="trim_http_logs_task")
def trim_http_logs_task():
    HTTPLog.trim()
