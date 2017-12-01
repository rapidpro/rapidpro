from __future__ import absolute_import, print_function, unicode_literals

import base64
import hashlib
import hmac
import json
import requests

from django.conf import settings
from django.db.models import Model
from django.utils.http import urlencode
from twython import Twython
from twython import TwythonAuthError
from twython import TwythonError
from twython import TwythonRateLimitError
from twython.helpers import _transparent_params

from temba.utils.http import HttpEvent


class TembaTwython(Twython):  # pragma: no cover

    def __init__(self, *args, **kwargs):
        super(TembaTwython, self).__init__(*args, **kwargs)
        self.events = []

    @classmethod
    def from_channel(cls, channel):
        # could be passed a ChannelStruct or a Channel model instance
        config = channel.config_json() if isinstance(channel, Model) else channel.config

        # Twitter channels come in new (i.e. user app, webhook API) and classic (shared app, streaming API) flavors
        if 'api_key' in config:
            api_key, api_secret = config['api_key'], config['api_secret']
            access_token, access_token_secret = config['access_token'], config['access_token_secret']
        else:
            api_key, api_secret = settings.TWITTER_API_KEY, settings.TWITTER_API_SECRET
            access_token, access_token_secret = config['oauth_token'], config['oauth_token_secret']

        return TembaTwython(api_key, api_secret, access_token, access_token_secret)

    def _request(self, url, method='GET', params=None, api_call=None):
        """Internal request method"""
        method = method.lower()
        params = params or {}

        func = getattr(self.client, method)
        params, files = (params, None) if 'event' in params else _transparent_params(params)

        requests_args = {}
        for k, v in self.client_args.items():
            # Maybe this should be set as a class variable and only done once?
            if k in ('timeout', 'allow_redirects', 'stream', 'verify'):
                requests_args[k] = v

        if method == 'get':
            requests_args['params'] = params
        else:
            requests_args.update({
                'data': json.dumps(params) if 'event' in params else params,
                'files': files
            })
        try:
            if method == 'get':
                event = HttpEvent(method, url + '?' + urlencode(params))
            else:
                event = HttpEvent(method, url, urlencode(params))
            self.events.append(event)

            response = func(url, **requests_args)
            event.status_code = response.status_code
            event.response_body = response.text

        except requests.RequestException as e:
            raise TwythonError(str(e))
        content = response.content.decode('utf-8')

        # create stash for last function intel
        self._last_call = {
            'api_call': api_call,
            'api_error': None,
            'cookies': response.cookies,
            'headers': response.headers,
            'status_code': response.status_code,
            'url': response.url,
            'content': content,
        }

        #  Wrap the json loads in a try, and defer an error
        #  Twitter will return invalid json with an error code in the headers
        json_error = False
        if content:
            try:
                try:
                    # try to get json
                    content = content.json()
                except AttributeError:
                    # if unicode detected
                    content = json.loads(content)
            except ValueError:
                json_error = True
                content = {}

        if response.status_code > 304:
            # If there is no error message, use a default.
            errors = content.get('errors',
                                 [{'message': 'An error occurred processing your request.'}])
            if errors and isinstance(errors, list):
                error_message = errors[0]['message']
            else:
                error_message = errors  # pragma: no cover
            self._last_call['api_error'] = error_message

            ExceptionType = TwythonError
            if response.status_code == 429:
                # Twitter API 1.1, always return 429 when rate limit is exceeded
                ExceptionType = TwythonRateLimitError  # pragma: no cover
            elif response.status_code == 401 or 'Bad Authentication data' in error_message:
                # Twitter API 1.1, returns a 401 Unauthorized or
                # a 400 "Bad Authentication data" for invalid/expired app keys/user tokens
                ExceptionType = TwythonAuthError

            raise ExceptionType(error_message,
                                error_code=response.status_code,
                                retry_after=response.headers.get('retry-after'))

        # if we have a json error here, then it's not an official Twitter API error
        if json_error and response.status_code not in (200, 201, 202):  # pragma: no cover
            raise TwythonError('Response was not valid JSON, unable to decode.')

        return content

    def get_webhooks(self):  # pragma: no cover
        """
        Returns all URLs and their statuses for the given app.

        Docs: https://dev.twitter.com/webhooks/reference/get/account_activity/webhooks
        """
        return self.get('account_activity/webhooks')

    def recheck_webhook(self, webhook_id):  # pragma: no cover
        """
        Triggers the challenge response check (CRC) for the given webhook's URL.

        Docs: https://dev.twitter.com/webhooks/reference/put/account_activity/webhooks
        """
        return self.request('account_activity/webhooks/%s' % webhook_id, method='PUT')

    def register_webhook(self, url):
        """
        Registers a new webhook URL for the given application context.

        Docs: https://dev.twitter.com/webhooks/reference/post/account_activity/webhooks
        """
        return self.post('account_activity/webhooks', params={'url': url})

    def delete_webhook(self, webhook_id):
        """
        Removes the webhook from the provided application's configuration.
        Docs: https://dev.twitter.com/webhooks/reference/del/account_activity/webhooks
        """
        return self.request('account_activity/webhooks/%s' % webhook_id, method='DELETE')

    def subscribe_to_webhook(self, webhook_id):
        """
        Subscribes the provided app to events for the provided user context.

        Docs: https://dev.twitter.com/webhooks/reference/post/account_activity/webhooks/subscriptions
        """
        return self.post('account_activity/webhooks/%s/subscriptions' % webhook_id)


def generate_twitter_signature(content, consumer_secret):
    token = hmac.new(bytes(consumer_secret.encode('ascii')), msg=content, digestmod=hashlib.sha256).digest()
    return 'sha256=' + base64.standard_b64encode(token)
