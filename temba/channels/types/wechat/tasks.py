from celery.task import task

from temba.channels.models import Channel
from temba.utils.wechat import WeChatClient


@task(track_started=True, name="refresh_wechat_access_tokens")
def refresh_wechat_access_tokens():  # pragma: needs cover
    channels = Channel.objects.filter(channel_type="WC", is_active=True)
    for channel in channels:
        client = WeChatClient.from_channel(channel)
        if client is not None:
            client.refresh_access_token(channel.id)
