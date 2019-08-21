import time

import requests
from django_redis import get_redis_connection

from django.utils.http import urlencode

from temba.channels.models import ChannelLog
from temba.channels.types.jiochat.client import JioChatClient
from temba.utils import json
from temba.utils.http import HttpEvent


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
        r = get_redis_connection()
        lock_name = self.token_refresh_lock % self.channel_uuid

        if not r.get(lock_name):
            with r.lock(lock_name, timeout=30):
                key = self.token_store_key % self.channel_uuid

                data = {"grant_type": "client_credential", "appid": self.app_id, "secret": self.app_secret}
                url = self.token_url

                event = HttpEvent("GET", url + "?" + urlencode(data))
                start = time.time()

                response = requests.get(url, params=data, timeout=15)
                event.status_code = response.status_code

                if response.status_code != 200:
                    event.response_body = response.content
                    ChannelLog.log_channel_request(
                        channel_id, f"Got non-200 response from {self.api_name}", event, start, True
                    )
                    return

                response_json = response.json()
                has_error = False
                if response_json.get("errcode", -1) != 0:
                    has_error = True

                event.response_body = json.dumps(response_json)
                ChannelLog.log_channel_request(
                    channel_id, f"Successfully fetched access token from {self.api_name}", event, start, has_error
                )

                access_token = response_json.get("access_token", "")
                expires = response_json.get("expires_in", 7200)
                if access_token:
                    r.set(key, access_token, ex=int(expires))
                    return access_token
