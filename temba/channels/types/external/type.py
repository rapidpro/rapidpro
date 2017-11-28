from __future__ import unicode_literals, absolute_import

import requests
import six
import time

from django.utils.translation import ugettext_lazy as _
from temba.msgs.models import WIRED
from temba.utils.http import HttpEvent, http_headers
from .views import ClaimView
from ...models import Channel, ChannelType, SendException


class ExternalType(ChannelType):
    """
    A external channel which speaks our own API language
    """
    code = 'EX'
    category = ChannelType.Category.PHONE

    name = "External API"
    icon = 'icon-power-cord'

    claim_blurb = _("""Use our pluggable API to connect an external service you already have.""")
    claim_view = ClaimView

    schemes = None  # can be any scheme
    max_length = 160
    attachment_support = False

    def send(self, channel, msg, text):
        payload = {
            'id': str(msg.id),
            'text': text,
            'to': msg.urn_path,
            'to_no_plus': msg.urn_path.lstrip('+'),
            'from': channel.address,
            'from_no_plus': channel.address.lstrip('+'),
            'channel': str(channel.id)
        }

        # build our send URL
        url = Channel.replace_variables(channel.config[Channel.CONFIG_SEND_URL], payload)
        start = time.time()

        method = channel.config.get(Channel.CONFIG_SEND_METHOD, 'POST')

        content_type = channel.config.get(Channel.CONFIG_CONTENT_TYPE, Channel.CONTENT_TYPE_URLENCODED)
        headers = http_headers(extra={'Content-Type': Channel.CONTENT_TYPES[content_type]})

        event = HttpEvent(method, url)

        if method in ('POST', 'PUT'):
            body = channel.config.get(Channel.CONFIG_SEND_BODY, Channel.CONFIG_DEFAULT_SEND_BODY)
            body = Channel.replace_variables(body, payload, content_type)
            event.request_body = body

        try:
            if method == 'POST':
                response = requests.post(url, data=body.encode('utf8'), headers=headers, timeout=5)
            elif method == 'PUT':
                response = requests.put(url, data=body.encode('utf8'), headers=headers, timeout=5)
            else:
                response = requests.get(url, headers=headers, timeout=5)

            event.status_code = response.status_code
            event.response_body = response.text

        except Exception as e:
            raise SendException(six.text_type(e), event=event, start=start)

        if response.status_code != 200 and response.status_code != 201 and response.status_code != 202:
            raise SendException("Got non-200 response [%d] from API" % response.status_code,
                                event=event, start=start)

        Channel.success(channel, msg, WIRED, start, event=event)
