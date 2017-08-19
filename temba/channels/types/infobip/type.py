from __future__ import unicode_literals, absolute_import

import base64
import json
import requests
import six
import time

from django.utils.translation import ugettext_lazy as _

from temba.channels.types.infobip.views import ClaimView
from temba.contacts.models import TEL_SCHEME
from temba.utils.http import HttpEvent
from ...models import Channel, ChannelType, SendException, TEMBA_HEADERS


class InfobipType(ChannelType):
    """
    An Infobip channel (https://www.infobip.com/)
    """

    code = 'IB'
    category = ChannelType.Category.PHONE

    name = "Infobip"
    icon = 'icon-power-cord'

    claim_blurb = _("""Easily add a two way number you have configured with <a href="http://infobip.com">Infobip</a> using their APIs.""")
    claim_view = ClaimView

    schemes = [TEL_SCHEME]
    max_length = 1600
    attachment_support = False

    def send(self, channel, msg, text):
        from temba.msgs.models import SENT

        url = "https://api.infobip.com/sms/1/text/single"

        username = channel.config['username']
        password = channel.config['password']
        encoded_auth = base64.b64encode(username + ":" + password)

        headers = {'Content-Type': 'application/json', 'Accept': 'application/json',
                   'Authorization': 'Basic %s' % encoded_auth}
        headers.update(TEMBA_HEADERS)

        payload = {'from': channel.address.lstrip('+'), 'to': msg.urn_path.lstrip('+'),
                   'text': text}

        event = HttpEvent('POST', url, json.dumps(payload))
        events = [event]
        start = time.time()

        try:
            response = requests.post(url, json=payload, headers=headers, timeout=5)
            event.status_code = response.status_code
            event.response_body = response.text
        except Exception as e:
            raise SendException(u"Unable to send message: %s" % six.text_type(e),
                                events=events, start=start)

        if response.status_code != 200 and response.status_code != 201:
            raise SendException("Received non 200 status: %d" % response.status_code,
                                events=events, start=start)

        response_json = response.json()
        messages = response_json['messages']

        # if it wasn't successfully delivered, throw
        if int(messages[0]['status']['id']) != 0:  # pragma: no cover
            raise SendException("Received non-zero status code [%s]" % messages[0]['status']['id'],
                                events=events, start=start)

        external_id = messages[0]['messageid']

        Channel.success(channel, msg, SENT, start, events=events, external_id=external_id)
