from celery.task import task

from temba.channels.models import Channel

from .client import WeChatClient
from .type import WeChatType


@task(track_started=True, name="refresh_wechat_access_tokens")
def refresh_wechat_access_tokens():
    for channel in Channel.objects.filter(channel_type=WeChatType.code, is_active=True):
        client = WeChatClient.from_channel(channel)
        client.refresh_access_token(channel.id)
