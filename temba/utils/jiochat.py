import hashlib
import json
import requests
import time

import six
from django.core.cache import cache
from django.utils.crypto import constant_time_compare
from django_redis import get_redis_connection

from temba.utils.http import HttpEvent

JIOCHAT_ACCESS_TOKEN_KEY = 'jiochat_channel_access_token:%s'
JIOCHAT_ACCESS_TOKEN_REFRESH_LOCK = 'jiochat_channel_access_token:refresh-lock:%s'


class JiochatClient:
    def __init__(self, channel_uuid, app_id, app_secret):
        self.channel_uuid = channel_uuid
        self.app_id = app_id
        self.app_secret = app_secret

    def get_access_token(self):
        r = get_redis_connection()
        lock_name = JIOCHAT_ACCESS_TOKEN_REFRESH_LOCK % self.channel_uuid

        with r.lock(lock_name, timeout=5):
            key = JIOCHAT_ACCESS_TOKEN_KEY % self.channel_uuid
            access_token = cache.get(key, None)
            return access_token

    def refresh_access_token(self):
        r = get_redis_connection()
        lock_name = JIOCHAT_ACCESS_TOKEN_REFRESH_LOCK % self.channel_uuid

        if not r.get(lock_name):
            with r.lock(lock_name, timeout=30):
                key = JIOCHAT_ACCESS_TOKEN_KEY % self.channel_uuid

                post_data = dict(grant_type='client_credentials', client_id=self.app_id, client_secret=self.app_secret)
                url = 'https://channels.jiochat.com/auth/token.action'

                response = self._request(url, 'POST', post_data, access_token=None)
                if response.status_code != 200:
                    return

                response_json = response.json()

                access_token = response_json['access_token']
                cache.set(key, access_token, timeout=7200)
                return access_token

    def verify_request(self, request, channel_secret):
        signature = request.GET.get('signature')
        timestamp = request.GET.get('timestamp')
        nonce = request.GET.get('nonce')
        echostr = request.GET.get('echostr')

        value = "".join(sorted([channel_secret, timestamp, nonce]))

        hash_object = hashlib.sha1(value.encode('utf-8'))
        signature_check = hash_object.hexdigest()

        return constant_time_compare(signature_check, signature), echostr

    def request_media(self, media_id):
        access_token = self.get_access_token()

        url = 'https://channels.jiochat.com/media/download.action'

        payload = dict(media_id=media_id)

        response = None

        attempts = 0
        while attempts < 4:
            response = self._request(url, 'GET', payload, access_token)

            # If we fail sleep for a bit then try again up to 4 times
            if response.status_code == 200:
                break
            else:
                attempts += 1
                time.sleep(.250)

        return response

    def get_user_detail(self, open_id):
        access_token = self.get_access_token()

        url = 'https://channels.jiochat.com/user/info.action?'

        payload = dict(openid=open_id)

        response = self._request(url, 'GET', payload, access_token)
        if response.status_code != 200:
            return dict()

        data = response.json()
        return data

    def send_message(self, data, start):
        from temba.channels.models import SendException

        access_token = self.get_access_token()

        url = 'https://channels.jiochat.com/custom/custom_send.action'
        event = HttpEvent('POST', url, json.dumps(data))

        try:
            response = self._request(url, 'POST_JSON', data, access_token)
            event.status_code = response.status_code
            event.response_body = response.text
        except Exception as e:
            raise SendException(six.text_type(e), event=event, start=start)

        if response.status_code != 200 and response.status_code != 201 and response.status_code != 202:
            raise SendException("Got non-200 response [%d] from JioChat" % response.status_code,
                                event=event, start=start)

        return response, event

    def _request(self, url, method='GET', params=None, access_token=None):
        from temba.channels.models import TEMBA_HEADERS

        headers = {'Authorization': 'Bearer ' + access_token} if access_token else {}
        headers.update(TEMBA_HEADERS)

        if method == 'POST_JSON':
            response = requests.post(url, json=params, headers=headers, timeout=15)
        elif method == 'POST':
            response = requests.post(url, data=params, headers=headers, timeout=15)
        else:
            response = requests.get(url, params=params, headers=headers, timeout=15)

        return response
