import logging
from datetime import timedelta

from celery import shared_task

from django.utils import timezone

from temba.utils.crons import cron_task

from .models import BroadcastMsgCount, LabelCount, Media, Msg, SystemLabelCount

logger = logging.getLogger(__name__)


@cron_task()
def fail_old_android_messages():
    """
    Fails old Android that haven't sent because their relayer isn't syncing. Note that we can't fail non-Android
    messages here because they're still in Courier's queue.
    """

    too_old = Msg.objects.filter(
        created_on__lte=timezone.now() - timedelta(days=7),
        direction=Msg.DIRECTION_OUT,
        is_android=True,
        status__in=(Msg.STATUS_INITIALIZING, Msg.STATUS_QUEUED, Msg.STATUS_ERRORED),
    )
    num_failed = too_old.update(status=Msg.STATUS_FAILED, failed_reason=Msg.FAILED_TOO_OLD, modified_on=timezone.now())

    return {"failed": num_failed}


@cron_task(lock_timeout=7200)
def squash_msg_counts():
    SystemLabelCount.squash()
    LabelCount.squash()
    BroadcastMsgCount.squash()


@shared_task
def process_media_upload(media_id):
    Media.objects.get(id=media_id).process_upload()
