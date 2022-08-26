import requests
from django_redis import get_redis_connection

from django.utils import timezone

from temba.channels.models import Channel, ChannelLog
from temba.channels.types.jiochat.client import JioChatClient


class WeChatClient(JioChatClient):
    """
    Uses similar API as JioChat except makes GET requests to renew API tokens
    """

    api_name = "WeChat"
    api_slug = "wechat"
    token_url = "https://api.weixin.qq.com/cgi-bin/token"
    token_refresh_lock = "wechat_channel_access_token:refresh-lock:%s"
    token_store_key = "wechat_channel_access_token:%s"

    # we use GET for WeChat
    def refresh_access_token(self, channel_id):
        channel = Channel.objects.get(id=channel_id)
        r = get_redis_connection()
        lock_name = self.token_refresh_lock % self.channel_uuid

        if not r.get(lock_name):
            with r.lock(lock_name, timeout=30):
                key = self.token_store_key % self.channel_uuid

                start = timezone.now()
                response = requests.get(
                    self.token_url,
                    params={"grant_type": "client_credential", "appid": self.app_id, "secret": self.app_secret},
                    timeout=15,
                )
                ChannelLog.from_response(ChannelLog.LOG_TYPE_TOKEN_REFRESH, channel, response, start, timezone.now())

                if response.status_code != 200:
                    return

                response_json = response.json()
                access_token = response_json.get("access_token", "")
                expires = response_json.get("expires_in", 7200)
                if access_token:
                    r.set(key, access_token, ex=int(expires))
                    return access_token
