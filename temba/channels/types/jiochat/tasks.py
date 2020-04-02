from celery.task import task

from temba.channels.models import Channel

from .client import JioChatClient
from .type import JioChatType


@task(track_started=True, name="refresh_jiochat_access_tokens")
def refresh_jiochat_access_tokens():
    for channel in Channel.objects.filter(channel_type=JioChatType.code, is_active=True):
        client = JioChatClient.from_channel(channel)
        client.refresh_access_token(channel.id)
