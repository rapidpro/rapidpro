# -*- coding: utf-8 -*-
from __future__ import absolute_import, division, print_function, unicode_literals

import time
import requests
import six

from django.utils.http import urlencode
from django.utils.translation import ugettext_lazy as _

from temba.channels.views import AuthenticatedExternalClaimView
from temba.contacts.models import TEL_SCHEME
from temba.msgs.models import WIRED
from temba.utils.http import HttpEvent, http_headers
from ...models import Channel, ChannelType, SendException, Encoding


class RedRabbitType(ChannelType):
    """
    A RedRabbit channel (http://www.redrabbitsms.com/)
    """

    code = 'RR'
    category = ChannelType.Category.PHONE

    name = "Red Rabbit"

    claim_blurb = _("""Easily add a two way number you have configured with <a href="http://www.redrabbitsms.com/">Red Rabbit</a> using their APIs.""")

    claim_view = AuthenticatedExternalClaimView

    schemes = [TEL_SCHEME]
    max_length = 1600
    attachment_support = False

    def is_available_to(self, user):
        return False  # Hidden since it is MT only

    def send(self, channel, msg, text):
        encoding, text = Channel.determine_encoding(text, replace=True)

        # http://http1.javna.com/epicenter/gatewaysendG.asp?LoginName=xxxx&Password=xxxx&Tracking=1&Mobtyp=1&MessageRecipients=962796760057&MessageBody=hi&SenderName=Xxx
        params = {
            'LoginName': channel.config[Channel.CONFIG_USERNAME],
            'Password': channel.config[Channel.CONFIG_PASSWORD],
            'Tracking': 1,
            'Mobtyp': 1,
            'MessageRecipients': msg.urn_path.lstrip('+'),
            'MessageBody': text,
            'SenderName': channel.address.lstrip('+')
        }

        # we are unicode
        if encoding == Encoding.UNICODE:
            params['Msgtyp'] = 10 if len(text) >= 70 else 9
        elif len(text) > 160:
            params['Msgtyp'] = 5

        url = 'http://http1.javna.com/epicenter/GatewaySendG.asp'
        event = HttpEvent('GET', url + '?' + urlencode(params))
        start = time.time()

        try:
            response = requests.get(url, params=params, headers=http_headers(), timeout=15)
            event.status_code = response.status_code
            event.response_body = response.text

        except Exception as e:  # pragma: no cover
            raise SendException(six.text_type(e), event=event, start=start)

        Channel.success(channel, msg, WIRED, start, event=event)
