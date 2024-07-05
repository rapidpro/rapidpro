import logging
from datetime import timedelta, timezone as tzone

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
from .models import Channel, ChannelCount, ChannelEvent, ChannelLog, SyncEvent
from .types.android import AndroidType

logger = logging.getLogger(__name__)


@shared_task
def sync_channel_fcm_task(cloud_registration_id, channel_id=None):  # pragma: no cover
    channel = Channel.objects.filter(id=channel_id).first()
    sync.sync_channel_fcm(cloud_registration_id, channel)


@cron_task()
def check_android_channels():
    from temba.notifications.incidents.builtin import ChannelDisconnectedIncidentType
    from temba.notifications.models import Incident

    last_half_hour = timezone.now() - timedelta(minutes=30)

    ongoing = Incident.objects.filter(incident_type=ChannelDisconnectedIncidentType.slug, ended_on=None).select_related(
        "channel"
    )

    for incident in ongoing:
        # if we've seen the channel since this incident started went out, then end it
        if incident.channel.last_seen > incident.started_on:
            incident.end()

    not_recently_seen = (
        Channel.objects.filter(channel_type=AndroidType.code, is_active=True, last_seen__lt=last_half_hour)
        .exclude(org=None)
        .exclude(last_seen=None)
        .select_related("org")
    )

    for channel in not_recently_seen:
        ChannelDisconnectedIncidentType.get_or_create(channel)


@shared_task
def interrupt_channel_task(channel_id):
    channel = Channel.objects.get(pk=channel_id)
    # interrupt the channel, any sessions using this channel for calls,
    # fail pending/queued messages and clear courier messages
    mailroom.queue_interrupt_channel(channel.org, channel=channel)


@cron_task(lock_timeout=7200)
def trim_channel_events():
    """
    Trims old channel events
    """

    trim_before = timezone.now() - settings.RETENTION_PERIODS["channelevent"]

    num_deleted = delete_in_batches(ChannelEvent.objects.filter(created_on__lte=trim_before))

    return {"deleted": num_deleted}


@cron_task()
def trim_channel_sync_events():
    """
    Trims old Android channel sync events
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
    yesterday = (now.astimezone(tzone.utc) - timedelta(days=1)).date()

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
