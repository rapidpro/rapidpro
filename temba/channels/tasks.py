from __future__ import unicode_literals

from django.conf import settings
from djcelery_transactions import task
from redis_cache import get_redis_connection
from temba.msgs.models import SEND_MSG_TASK
from temba.utils import dict_to_struct
from temba.utils.queues import pop_task
from temba.utils.mage import MageClient
from .models import Channel, Alert

@task(track_started=True, name='sync_channel_task')
def sync_channel_task(gcm_id, channel_id=None):  #pragma: no cover
    channel = Channel.objects.filter(pk=channel_id).first()
    Channel.sync_channel(gcm_id, channel)

@task(track_started=True, name='send_msg_task')
def send_msg_task():
    """
    Pops the next message off of our msg queue to send.
    """
    # pop off the next task
    task = pop_task(SEND_MSG_TASK)

    # it is possible we have no message to send, if so, just return
    if not task:
        return

    msg = dict_to_struct('MockMsg', task, datetime_fields=['delivered_on', 'sent_on', 'created_on',
                                                           'queued_on', 'next_attempt'])

    # send it off
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


@task(track_started=True, name='notify_mage_task')
def notify_mage_task(channel_uuid, action):
    mage = MageClient(settings.MAGE_API_URL, settings.MAGE_AUTH_TOKEN)
    if action == 'add':
        mage.add_twitter_stream(channel_uuid)
    elif action == 'remove':
        mage.remove_twitter_stream(channel_uuid)
    else:
        raise ValueError('Invalid action: %s' % action)
