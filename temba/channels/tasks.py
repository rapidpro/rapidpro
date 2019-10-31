import logging
from datetime import timedelta

from django.conf import settings
from django.utils import timezone

from celery.task import task

from temba.utils.celery import nonoverlapping_task

from .models import Alert, Channel, ChannelCount, ChannelLog, SyncEvent

logger = logging.getLogger(__name__)


@task(track_started=True, name="sync_channel_fcm_task")
def sync_channel_fcm_task(cloud_registration_id, channel_id=None):  # pragma: no cover
    channel = Channel.objects.filter(pk=channel_id).first()
    Channel.sync_channel_fcm(cloud_registration_id, channel)


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


@nonoverlapping_task(track_started=True, name="trim_sync_events_task")
def trim_sync_events_task():  # pragma: needs cover
    """
    Runs daily and clears any channel sync events that are older than 7 days
    """
    SyncEvent.trim()


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
