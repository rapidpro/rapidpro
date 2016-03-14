from __future__ import unicode_literals

from datetime import timedelta
from django.conf import settings
from django.utils import timezone
from djcelery_transactions import task
from enum import Enum
from redis_cache import get_redis_connection
from temba.msgs.models import SEND_MSG_TASK
from temba.utils import dict_to_struct
from temba.utils.queues import pop_task
from temba.utils.mage import MageClient
from .models import Channel, Alert, ChannelLog, ChannelCount


class MageStreamAction(Enum):
    activate = 1
    refresh = 2
    deactivate = 3


@task(track_started=True, name='sync_channel_task')
def sync_channel_task(gcm_id, channel_id=None):  #pragma: no cover
    channel = Channel.objects.filter(pk=channel_id).first()
    Channel.sync_channel(gcm_id, channel)


@task(track_started=True, name='send_msg_task')
def send_msg_task():
    """
    Pops the next message off of our msg queue to send.
    """
    logger = send_msg_task.get_logger()

    # pop off the next task
    task = pop_task(SEND_MSG_TASK)

    # it is possible we have no message to send, if so, just return
    if not task:
        return

    msg = dict_to_struct('MockMsg', task,
                         datetime_fields=['modified_on', 'sent_on', 'created_on', 'queued_on', 'next_attempt'])

    # send it off
    r = get_redis_connection()
    with r.lock('send_msg_%d' % msg.id, timeout=300):
        Channel.send_message(msg)


@task(track_started=True, name='check_channels_task')
def check_channels_task():
    """
    Run every 30 minutes.  Checks if any channels who are active have not been seen in that
    time.  Triggers alert in that case
    """
    r = get_redis_connection()

    # only do this if we aren't already checking campaigns
    key = 'check_channels'
    if not r.get(key):
        with r.lock(key, timeout=300):
            Alert.check_alerts()


@task(track_started=True, name='send_alert_task')
def send_alert_task(alert_id, resolved):
    alert = Alert.objects.get(pk=alert_id)
    alert.send_email(resolved)


@task(track_started=True, name='trim_channel_log_task')
def trim_channel_log_task():
    """
    Runs daily and clears any channel log items older than 48 hours.
    """
    two_days_ago = timezone.now() - timedelta(hours=48)
    ChannelLog.objects.filter(created_on__lte=two_days_ago).delete()


@task(track_started=True, name='notify_mage_task')
def notify_mage_task(channel_uuid, action):
    """
    Notifies Mage of a change to a Twitter channel. Having this in a djcelery_transactions task ensures that the channel
    db object is updated before Mage tries to fetch it
    """
    mage = MageClient(settings.MAGE_API_URL, settings.MAGE_AUTH_TOKEN)

    if action == MageStreamAction.activate:
        mage.activate_twitter_stream(channel_uuid)
    elif action == MageStreamAction.refresh:
        mage.refresh_twitter_stream(channel_uuid)
    elif action == MageStreamAction.deactivate:
        mage.deactivate_twitter_stream(channel_uuid)
    else:
        raise ValueError('Invalid action: %s' % action)

@task(track_started=True, name="squash_channelcounts")
def squash_channelcounts():
    r = get_redis_connection()

    key = 'squash_channelcounts'
    if not r.get(key):
        with r.lock(key, timeout=900):
            ChannelCount.squash_counts()
