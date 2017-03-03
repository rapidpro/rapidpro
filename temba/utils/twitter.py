from __future__ import absolute_import, print_function, unicode_literals

import json
import requests

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

    def _request(self, url, method='GET', params=None, api_call=None):
        """Internal request method"""
        method = method.lower()
        params = params or {}

        func = getattr(self.client, method)
        params, files = _transparent_params(params)

        requests_args = {}
        for k, v in self.client_args.items():
            # Maybe this should be set as a class variable and only done once?
            if k in ('timeout', 'allow_redirects', 'stream', 'verify'):
                requests_args[k] = v

        if method == 'get':
            requests_args['params'] = params
        else:
            requests_args.update({
                'data': params,
                'files': files,
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
