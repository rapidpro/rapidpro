from __future__ import unicode_literals, absolute_import

import time
import requests
import six

from django.utils.http import urlencode
from django.utils.translation import ugettext_lazy as _

from temba.channels.types.clickatell.views import ClaimView
from temba.contacts.models import TEL_SCHEME
from temba.msgs.models import WIRED
from temba.utils.http import HttpEvent, http_headers
from ...models import Channel, ChannelType, SendException, Encoding


class ClickatellType(ChannelType):
    """
    A Clickatell channel (https://clickatell.com/)
    """

    code = 'CT'
    category = ChannelType.Category.PHONE

    name = "Clickatell"
    icon = "icon-channel-clickatell"

    claim_blurb = _("""Connect your <a href="http://clickatell.com/" target="_blank">Clickatell</a> number, we'll walk you
                           through the steps necessary to get your Clickatell connection working in a few minutes.""")
    claim_view = ClaimView

    schemes = [TEL_SCHEME]
    max_length = 420
    attachment_support = False

    def send(self, channel, msg, text):
        # determine our encoding
        encoding, text = Channel.determine_encoding(text, replace=True)

        # if this looks like unicode, ask clickatell to send as unicode
        if encoding == Encoding.UNICODE:
            unicode_switch = 1
        else:
            unicode_switch = 0

        url = 'https://api.clickatell.com/http/sendmsg'
        payload = {'api_id': channel.config[Channel.CONFIG_API_ID],
                   'user': channel.config[Channel.CONFIG_USERNAME],
                   'password': channel.config[Channel.CONFIG_PASSWORD],
                   'from': channel.address.lstrip('+'),
                   'concat': 3,
                   'callback': 7,
                   'mo': 1,
                   'unicode': unicode_switch,
                   'to': msg.urn_path.lstrip('+'),
                   'text': text}

        event = HttpEvent('GET', url + "?" + urlencode(payload))

        start = time.time()

        try:
            response = requests.get(url, params=payload, headers=http_headers(), timeout=5)
            event.status_code = response.status_code
            event.response_body = response.text

        except Exception as e:
            raise SendException(six.text_type(e), event=event, start=start)

        if response.status_code != 200 and response.status_code != 201 and response.status_code != 202:
            raise SendException("Got non-200 response [%d] from API" % response.status_code,
                                event=event, start=start)

        # parse out the external id for the message, comes in the format: "ID: id12312312312"
        external_id = None
        if response.text.startswith("ID: "):
            external_id = response.text[4:]

        Channel.success(channel, msg, WIRED, start, event=event, external_id=external_id)
