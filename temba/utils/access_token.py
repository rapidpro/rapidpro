import time

import requests
from django_redis import get_redis_connection

from temba.channels.models import ChannelLog
from temba.utils import json
from temba.utils.http import HttpEvent, http_headers


class APIClient:
    API_NAME = None
    API_SLUG = None
    TOKEN_REFRESH_LOCK = None
    TOKEN_STORE_KEY = None
    TOKEN_URL = None

    def __init__(self, channel_uuid, app_id, app_secret):
        self.channel_uuid = channel_uuid
        self.app_id = app_id
        self.app_secret = app_secret

    @classmethod
    def from_channel(cls, channel):
        config = channel.config
        app_id = config.get("%s_app_id" % cls.API_SLUG, None)
        app_secret = config.get("%s_app_secret" % cls.API_SLUG, None)
        return cls(channel.uuid, app_id, app_secret)

    def get_access_token(self):
        r = get_redis_connection()
        lock_name = self.TOKEN_REFRESH_LOCK % self.channel_uuid

        with r.lock(lock_name, timeout=5):
            key = self.TOKEN_STORE_KEY % self.channel_uuid
            access_token = r.get(key)
            return access_token

    def refresh_access_token(self, channel_id):
        r = get_redis_connection()
        lock_name = self.TOKEN_REFRESH_LOCK % self.channel_uuid

        if not r.get(lock_name):
            with r.lock(lock_name, timeout=30):
                key = self.TOKEN_STORE_KEY % self.channel_uuid

                post_data = dict(grant_type="client_credentials", client_id=self.app_id, client_secret=self.app_secret)
                url = self.TOKEN_URL

                event = HttpEvent("POST", url, json.dumps(post_data))
                start = time.time()

                response = self._request(url, post_data, access_token=None)
                event.status_code = response.status_code

                if response.status_code != 200:
                    event.response_body = response.content
                    ChannelLog.log_channel_request(
                        channel_id, "Got non-200 response from %s" % self.API_NAME, event, start, True
                    )
                    return

                response_json = response.json()
                event.response_body = json.dumps(response_json)
                ChannelLog.log_channel_request(
                    channel_id, "Successfully fetched access token from %s" % self.API_NAME, event, start
                )

                access_token = response_json["access_token"]
                expires = response_json.get("expires_in", 7200)
                r.set(key, access_token, ex=int(expires))
                return access_token

    def _request(self, url, params=None, access_token=None):
        headers = http_headers(extra={"Authorization": "Bearer " + access_token} if access_token else {})
        response = requests.post(url, data=params, headers=headers, timeout=15)
        return response
