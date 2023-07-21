import logging
from datetime import timedelta

import pytz
from celery import shared_task

from django.conf import settings
from django.db.models import Count, Sum
from django.utils import timezone

from temba import mailroom
from temba.orgs.models import Org
from temba.utils.analytics import track
from temba.utils.crons import cron_task
from temba.utils.models import delete_in_batches

from .android import sync
from .models import Alert, Channel, ChannelCount, ChannelLog, SyncEvent

logger = logging.getLogger(__name__)


@shared_task
def sync_channel_fcm_task(cloud_registration_id, channel_id=None):  # pragma: no cover
    channel = Channel.objects.filter(id=channel_id).first()
    sync.sync_channel_fcm(cloud_registration_id, channel)


@cron_task()
def check_channel_alerts():
    """
    Run every 30 minutes.  Checks if any channels who are active have not been seen in that
    time.  Triggers alert in that case
    """
    Alert.check_alerts()


@cron_task()
def sync_old_seen_channels():
    from temba.channels.types.android import AndroidType

    now = timezone.now()
    window_end = now - timedelta(minutes=15)
    window_start = now - timedelta(days=7)
    old_seen_channels = Channel.objects.filter(
        is_active=True, channel_type=AndroidType.code, last_seen__lte=window_end, last_seen__gt=window_start
    )
    for channel in old_seen_channels:
        channel.trigger_sync()


@shared_task
def send_alert_task(alert_id, resolved):
    alert = Alert.objects.get(pk=alert_id)
    alert.send_email(resolved)


@shared_task
def interrupt_channel_task(channel_id):
    channel = Channel.objects.get(pk=channel_id)
    # interrupt the channel, any sessions using this channel for calls,
    # fail pending/queued messages and clear courier messages
    mailroom.queue_interrupt_channel(channel.org, channel=channel)


@cron_task()
def trim_sync_events():
    """
    Trims old Android sync events
    """

    trim_before = timezone.now() - settings.RETENTION_PERIODS["syncevent"]
    num_deleted = 0

    channels_with_events = (
        SyncEvent.objects.filter(created_on__lte=trim_before)
        .values("channel")
        .annotate(Count("id"))
        .filter(id__count__gt=1)
    )
    for result in channels_with_events:
        # trim older but always leave at least one per channel
        event_ids = list(
            SyncEvent.objects.filter(created_on__lte=trim_before, channel_id=result["channel"])
            .order_by("-created_on")
            .values_list("id", flat=True)[1:]
        )

        Alert.objects.filter(sync_event__in=event_ids).delete()
        SyncEvent.objects.filter(id__in=event_ids).delete()
        num_deleted += len(event_ids)

    return {"deleted": num_deleted}


@cron_task(lock_timeout=7200)
def trim_channel_logs():
    """
    Trims old channel logs
    """

    trim_before = timezone.now() - settings.RETENTION_PERIODS["channellog"]
    start = timezone.now()

    def can_continue():
        return (timezone.now() - start) < timedelta(hours=1)

    num_deleted = delete_in_batches(ChannelLog.objects.filter(created_on__lte=trim_before), post_delete=can_continue)

    return {"deleted": num_deleted}


@cron_task(lock_timeout=7200)
def squash_channel_counts():
    ChannelCount.squash()


@cron_task(lock_timeout=7200)
def track_org_channel_counts(now=None):
    """
    Run daily, logs to our analytics the number of incoming and outgoing messages/ivr messages per org that had
    more than one message received or sent in the previous day. This helps track engagement of orgs.
    """
    now = now or timezone.now()
    yesterday = (now.astimezone(pytz.utc) - timedelta(days=1)).date()

    stats = [
        dict(key="temba.msg_incoming", count_type=ChannelCount.INCOMING_MSG_TYPE),
        dict(key="temba.msg_outgoing", count_type=ChannelCount.OUTGOING_MSG_TYPE),
        dict(key="temba.ivr_incoming", count_type=ChannelCount.INCOMING_IVR_TYPE),
        dict(key="temba.ivr_outgoing", count_type=ChannelCount.OUTGOING_IVR_TYPE),
    ]

    # calculate each stat and track
    for stat in stats:
        org_counts = Org.objects.filter(
            channels__counts__day=yesterday, channels__counts__count_type=stat["count_type"]
        ).annotate(count=Sum("channels__counts__count"))

        for org in org_counts:
            admin = org.get_admins().first()
            if admin:
                track(admin, stat["key"], dict(count=org.count))
