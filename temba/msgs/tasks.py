import logging
from datetime import timedelta

from celery import shared_task

from django.utils import timezone

from temba.utils.crons import cron_task

from .models import BroadcastMsgCount, LabelCount, Media, Msg, SystemLabelCount

logger = logging.getLogger(__name__)


@cron_task()
def fail_old_messages():
    """
    Looks for any stalled outgoing messages older than 1 week. These are typically from Android relayers which have
    stopped syncing, and would be confusing to go out.
    """
    one_week_ago = timezone.now() - timedelta(days=7)
    too_old = Msg.objects.filter(
        created_on__lte=one_week_ago,
        direction=Msg.DIRECTION_OUT,
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
