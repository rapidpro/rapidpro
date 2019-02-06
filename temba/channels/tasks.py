import logging
import time
from datetime import timedelta
from enum import Enum

from django_redis import get_redis_connection

from django.conf import settings
from django.utils import timezone

from celery.task import task

from temba.msgs.models import SEND_MSG_TASK
from temba.utils import dict_to_struct
from temba.utils.queues import Queue, complete_task, nonoverlapping_task, push_task, start_task

from .models import Alert, Channel, ChannelCount, ChannelLog

logger = logging.getLogger(__name__)


class MageStreamAction(Enum):
    activate = 1
    refresh = 2
    deactivate = 3


@task(track_started=True, name="sync_channel_fcm_task")
def sync_channel_fcm_task(cloud_registration_id, channel_id=None):  # pragma: no cover
    channel = Channel.objects.filter(pk=channel_id).first()
    Channel.sync_channel_fcm(cloud_registration_id, channel)


@task(track_started=True, name="send_msg_task")
def send_msg_task():
    """
    Pops the next message off of our msg queue to send.
    """
    # pop off the next task
    org_id, msg_tasks = start_task(SEND_MSG_TASK)

    # it is possible we have no message to send, if so, just return
    if not msg_tasks:  # pragma: needs cover
        return

    if not isinstance(msg_tasks, list):  # pragma: needs cover
        msg_tasks = [msg_tasks]

    r = get_redis_connection()

    # acquire a lock on our contact to make sure two sets of msgs aren't being sent at the same time
    try:
        with r.lock("send_contact_%d" % msg_tasks[0]["contact"], timeout=300):
            # send each of our msgs
            while msg_tasks:
                msg_task = msg_tasks.pop(0)
                msg = dict_to_struct(
                    "MockMsg",
                    msg_task,
                    datetime_fields=["modified_on", "sent_on", "created_on", "queued_on", "next_attempt"],
                )
                Channel.send_message(msg)

                # if there are more messages to send for this contact, sleep a second before moving on
                if msg_tasks:
                    time.sleep(1)

    finally:  # pragma: no cover
        # mark this worker as done
        complete_task(SEND_MSG_TASK, org_id)

        # if some msgs weren't sent for some reason, then requeue them for later sending
        if msg_tasks:
            # requeue any unsent msgs
            push_task(org_id, Queue.MSGS, SEND_MSG_TASK, msg_tasks)


@nonoverlapping_task(track_started=True, name="check_channels_task", lock_key="check_channels")
def check_channels_task():
    """
    Run every 30 minutes.  Checks if any channels who are active have not been seen in that
    time.  Triggers alert in that case
    """
    Alert.check_alerts()


@nonoverlapping_task(track_started=True, name="sync_old_seen_channels_task", lock_key="sync_old_seen_channels")
def sync_old_seen_channels_task():
    now = timezone.now()
    window_end = now - timedelta(minutes=15)
    window_start = now - timedelta(days=7)
    old_seen_channels = Channel.objects.filter(
        is_active=True, channel_type=Channel.TYPE_ANDROID, last_seen__lte=window_end, last_seen__gt=window_start
    )
    for channel in old_seen_channels:
        channel.trigger_sync()


@task(track_started=True, name="send_alert_task")
def send_alert_task(alert_id, resolved):
    alert = Alert.objects.get(pk=alert_id)
    alert.send_email(resolved)


@nonoverlapping_task(track_started=True, name="trim_channel_log_task")
def trim_channel_log_task():  # pragma: needs cover
    """
    Runs daily and clears any channel log items older than 48 hours.
    """

    # keep success messages for only SUCCESS_LOGS_TRIM_TIME hours
    success_logs_trim_time = settings.SUCCESS_LOGS_TRIM_TIME

    # keep all errors for ALL_LOGS_TRIM_TIME days
    all_logs_trim_time = settings.ALL_LOGS_TRIM_TIME

    if success_logs_trim_time:
        success_log_later = timezone.now() - timedelta(hours=success_logs_trim_time)
        ChannelLog.objects.filter(created_on__lte=success_log_later, is_error=False).delete()

    if all_logs_trim_time:
        all_log_later = timezone.now() - timedelta(hours=all_logs_trim_time)
        ChannelLog.objects.filter(created_on__lte=all_log_later).delete()


@nonoverlapping_task(
    track_started=True, name="squash_channelcounts", lock_key="squash_channelcounts", lock_timeout=7200
)
def squash_channelcounts():
    ChannelCount.squash()
