from __future__ import unicode_literals, absolute_import

import json
import time
import requests
import six

from django.utils.translation import ugettext_lazy as _

from temba.channels.types.macrokiosk.views import ClaimView
from temba.contacts.models import TEL_SCHEME
from temba.msgs.models import WIRED
from temba.utils.http import HttpEvent, http_headers
from ...models import Channel, ChannelType, SendException, Encoding


class MacrokioskType(ChannelType):
    """
    An Macrokiok channel (http://www.macrokiosk.com/)
    """

    code = 'MK'
    category = ChannelType.Category.PHONE

    name = "Macrokiosk"

    claim_blurb = _("""Easily add a two way number you have configured with <a href="http://macrokiosk.com/">Macrokiosk</a> using their APIs.""")
    claim_view = ClaimView

    schemes = [TEL_SCHEME]
    max_length = 1600

    attachment_support = False

    def is_available_to(self, user):
        org = user.get_org()
        return org.timezone and six.text_type(org.timezone) in ['Asia/Kuala_Lumpur']

    def send(self, channel, msg, text):
        # determine our encoding
        encoding, text = Channel.determine_encoding(text, replace=True)

        # if this looks like unicode, ask macrokiosk to send as unicode
        if encoding == Encoding.UNICODE:
            message_type = 5
        else:
            message_type = 0

        # strip a leading +
        recipient = msg.urn_path[1:] if msg.urn_path.startswith('+') else msg.urn_path

        data = {
            'user': channel.config[Channel.CONFIG_USERNAME], 'pass': channel.config[Channel.CONFIG_PASSWORD],
            'to': recipient, 'text': text, 'from': channel.config[Channel.CONFIG_MACROKIOSK_SENDER_ID],
            'servid': channel.config[Channel.CONFIG_MACROKIOSK_SERVICE_ID], 'type': message_type
        }

        url = 'https://www.etracker.cc/bulksms/send'
        payload = json.dumps(data)
        headers = http_headers(extra={'Content-Type': 'application/json', 'Accept': 'application/json'})

        event = HttpEvent('POST', url, payload)

        start = time.time()

        try:
            response = requests.post(url, json=data, headers=headers, timeout=30)
            event.status_code = response.status_code
            event.response_body = response.text

            external_id = response.json().get('msgid', None)

        except Exception as e:
            raise SendException(six.text_type(e), event=event, start=start)

        if response.status_code not in [200, 201, 202]:
            raise SendException("Got non-200 response [%d] from API" % response.status_code,
                                event=event, start=start)

        Channel.success(channel, msg, WIRED, start, event=event, external_id=external_id)
