# -*- coding: utf-8 -*-
from __future__ import absolute_import, division, print_function, unicode_literals

import json
import requests
import six
import time

from django.utils.translation import ugettext_lazy as _
from temba.contacts.models import LINE_SCHEME
from temba.msgs.models import WIRED
from temba.utils.http import HttpEvent, http_headers
from .views import ClaimView
from ...models import Channel, ChannelType, SendException


class LineType(ChannelType):
    """
    A LINE channel (https://line.me/)
    """
    code = 'LN'
    category = ChannelType.Category.SOCIAL_MEDIA

    name = "LINE"
    icon = 'icon-line'

    claim_blurb = _("""Add a <a href="https://line.me">LINE</a> bot to send and receive messages to LINE users
                for free. Your users will need an Android, Windows or iOS device and a LINE account to send
                and receive messages.""")
    claim_view = ClaimView

    schemes = [LINE_SCHEME]
    max_length = 1600
    attachment_support = False
    free_sending = True

    show_public_addresses = True

    def send(self, channel, msg, text):
        channel_access_token = channel.config.get(Channel.CONFIG_AUTH_TOKEN)

        data = json.dumps({'to': msg.urn_path, 'messages': [{'type': 'text', 'text': text}]})

        start = time.time()
        headers = http_headers(extra={
            'Content-Type': 'application/json',
            'Authorization': 'Bearer %s' % channel_access_token
        })
        send_url = 'https://api.line.me/v2/bot/message/push'

        event = HttpEvent('POST', send_url, data)

        try:
            response = requests.post(send_url, data=data, headers=headers)
            response.json()

            event.status_code = response.status_code
            event.response_body = response.text
        except Exception as e:
            raise SendException(six.text_type(e), event=event, start=start)

        if response.status_code not in [200, 201, 202]:  # pragma: needs cover
            raise SendException("Got non-200 response [%d] from Line" % response.status_code,
                                event=event, start=start)

        Channel.success(channel, msg, WIRED, start, event=event)
