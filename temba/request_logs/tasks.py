from temba.utils.celery import nonoverlapping_task

from .models import HTTPLog


@nonoverlapping_task(track_started=True, name="trim_http_logs_task")
def trim_http_logs_task():
    HTTPLog.trim()
