import time

import requests
from django_redis import get_redis_connection

from django.utils.http import urlencode

from temba.channels.models import ChannelLog
from temba.utils import json
from temba.utils.access_token import APIClient
from temba.utils.http import HttpEvent


class WeChatClient(APIClient):
    API_NAME = "WeChat"
    API_SLUG = "wechat"
    TOKEN_REFRESH_LOCK = "wechat_channel_access_token:refresh-lock:%s"
    TOKEN_STORE_KEY = "wechat_channel_access_token:%s"
    TOKEN_URL = "https://api.weixin.qq.com/cgi-bin/token"

    # we use GET for WeChat
    def refresh_access_token(self, channel_id):
        r = get_redis_connection()
        lock_name = self.TOKEN_REFRESH_LOCK % self.channel_uuid

        if not r.get(lock_name):
            with r.lock(lock_name, timeout=30):
                key = self.TOKEN_STORE_KEY % self.channel_uuid

                data = dict(grant_type="client_credential", appid=self.app_id, secret=self.app_secret)
                url = self.TOKEN_URL

                event = HttpEvent("GET", url + "?" + urlencode(data))
                start = time.time()

                response = requests.get(url, params=data, timeout=15)
                event.status_code = response.status_code

                if response.status_code != 200:
                    event.response_body = response.content
                    ChannelLog.log_channel_request(
                        channel_id, "Got non-200 response from %s" % self.API_NAME, event, start, True
                    )
                    return

                response_json = response.json()
                has_error = False
                if response_json.get("errcode", -1) != 0:
                    has_error = True

                event.response_body = json.dumps(response_json)
                ChannelLog.log_channel_request(
                    channel_id, "Successfully fetched access token from %s" % self.API_NAME, event, start, has_error
                )

                access_token = response_json.get("access_token", "")
                expires = response_json.get("expires_in", 7200)
                if access_token:
                    r.set(key, access_token, ex=int(expires))
                    return access_token
