from __future__ import print_function, unicode_literals

from celery.task import task
from temba.channels.models import Channel
from temba.utils.jiochat import JiochatClient


@task(track_started=True, name='refresh_jiochat_access_tokens')
def refresh_jiochat_access_tokens():  # pragma: needs cover
    jiochat_channels = Channel.objects.filter(channel_type='JC', is_active=True)
    for channel in jiochat_channels:
        client = JiochatClient.from_channel(channel)
        if client is not None:
            client.refresh_access_token(channel.id)
