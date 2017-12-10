from __future__ import unicode_literals, absolute_import

import base64
import json
import requests
import six
import time

from django.urls import reverse
from django.utils.translation import ugettext_lazy as _
from temba.channels.views import AuthenticatedExternalCallbackClaimView
from temba.contacts.models import TEL_SCHEME
from temba.msgs.models import SENT
from temba.utils.http import HttpEvent, http_headers
from ...models import Channel, ChannelType, SendException


class InfobipType(ChannelType):
    """
    An Infobip channel (https://www.infobip.com/)
    """

    code = 'IB'
    category = ChannelType.Category.PHONE

    name = "Infobip"

    claim_blurb = _("""Easily add a two way number you have configured with <a href="http://infobip.com">Infobip</a> using their APIs.""")
    claim_view = AuthenticatedExternalCallbackClaimView

    schemes = [TEL_SCHEME]
    max_length = 1600
    attachment_support = False

    def send(self, channel, msg, text):
        url = "https://api.infobip.com/sms/1/text/advanced"

        username = channel.config['username']
        password = channel.config['password']
        encoded_auth = base64.b64encode(username + ":" + password)

        headers = http_headers(extra={
            'Content-Type': 'application/json',
            'Accept': 'application/json',
            'Authorization': 'Basic %s' % encoded_auth
        })

        # the event url InfoBip will forward delivery reports to
        status_url = 'https://%s%s' % (channel.callback_domain, reverse('courier.ib', args=[channel.uuid, 'delivered']))

        payload = {"messages": [
            {
                "from": channel.address.lstrip('+'),
                "destinations": [
                    {"to": msg.urn_path.lstrip('+'), "messageId": msg.id}
                ],
                "text": text,
                "notifyContentType": "application/json",
                "intermediateReport": True,
                "notifyUrl": status_url
            }
        ]}

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
        if int(messages[0]['status']['groupId']) not in [1, 3]:
            raise SendException("Received error status: %s" % messages[0]['status']['description'],
                                events=events, start=start)

        Channel.success(channel, msg, SENT, start, events=events)
