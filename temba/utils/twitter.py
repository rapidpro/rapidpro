# -*- coding: utf-8 -*-
from __future__ import absolute_import, division, print_function, unicode_literals

import base64
import hashlib
import hmac
import json
import requests

from six.moves.urllib.parse import quote_plus

from django.conf import settings
from django.db.models import Model
from django.utils.http import urlencode
from django.utils.encoding import force_bytes, force_text
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
        config = channel.config if isinstance(channel, Model) else channel.config

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

    def get_webhooks(self, env_name):
        """
        Returns the webhooks currently active for this app. (Twitter claims there can only be one)
        Docs: https://developer.twitter.com/en/docs/accounts-and-users/subscribe-account-activity/api-reference/aaa-standard-all
        """
        return self.get('https://api.twitter.com/1.1/account_activity/all/%s/webhooks.json' % env_name)

    def delete_webhook(self, env_name):
        """
        Deletes the webhook for the current app / user and passed in environment name.
        Docs: https://developer.twitter.com/en/docs/accounts-and-users/subscribe-account-activity/api-reference/aaa-standard-all
        """
        # grab our current webhooks
        resp = self.get_webhooks(env_name)

        # if we have one, delete it
        if len(resp) > 0:
            self.request('https://api.twitter.com/1.1/account_activity/all/%s/webhooks/%s.json' % (env_name, resp[0]['id']), method='DELETE')

    def register_webhook(self, env_name, url):
        """
        Registers a new webhook URL for the given application context.
        Docs: https://developer.twitter.com/en/docs/accounts-and-users/subscribe-account-activity/api-reference/aaa-standard-all
        """
        set_webhook_url = 'https://api.twitter.com/1.1/account_activity/all/%s/webhooks.json?url=%s' % (env_name, quote_plus(url))
        return self.post(set_webhook_url)

    def subscribe_to_webhook(self, env_name):
        """
        Subscribes all user's events for this apps webhook
        Docs: https://developer.twitter.com/en/docs/accounts-and-users/subscribe-account-activity/api-reference/aaa-standard-all
        """
        return self.post('https://api.twitter.com/1.1/account_activity/all/%s/subscriptions.json' % env_name)


def generate_twitter_signature(content, consumer_secret):
    token = hmac.new(force_bytes(consumer_secret.encode('ascii')), msg=force_bytes(content), digestmod=hashlib.sha256).digest()
    return 'sha256=' + force_text(base64.standard_b64encode(token))
