from temba.utils.celery import nonoverlapping_task

from .models import NotificationCount


@nonoverlapping_task(track_started=True, name="squash_notificationcounts", lock_timeout=7200)
def squash_notificationcounts():
    NotificationCount.squash()
