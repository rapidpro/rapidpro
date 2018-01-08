from __future__ import unicode_literals, absolute_import

import time
import requests
import six

from django.utils.translation import ugettext_lazy as _

from temba.channels.views import AuthenticatedExternalClaimView
from temba.contacts.models import TEL_SCHEME
from temba.msgs.models import WIRED
from temba.utils.http import HttpEvent, http_headers
from ...models import Channel, ChannelType, SendException


class BlackmynaType(ChannelType):
    """
    An Blackmyna channel (https://blackmyna.com)
    """

    code = 'BM'
    category = ChannelType.Category.PHONE

    name = "Blackmyna"

    claim_blurb = _("""Easily add a two way number you have configured with <a href="http://blackmyna.com">Blackmyna</a> using their APIs.""")
    claim_view = AuthenticatedExternalClaimView

    schemes = [TEL_SCHEME]
    max_length = 1600
    attachment_support = False

    def is_available_to(self, user):
        org = user.get_org()
        return org.timezone and six.text_type(org.timezone) in ["Asia/Kathmandu"]

    def is_recommended_to(self, user):
        return self.is_available_to(user)

    def send(self, channel, msg, text):

        payload = {
            'address': msg.urn_path,
            'senderaddress': channel.address,
            'message': text,
        }

        url = 'http://api.blackmyna.com/2/smsmessaging/outbound'
        external_id = None
        start = time.time()

        event = HttpEvent('POST', url, payload)

        try:
            response = requests.post(url, data=payload, headers=http_headers(), timeout=30,
                                     auth=(channel.config[Channel.CONFIG_USERNAME], channel.config[Channel.CONFIG_PASSWORD]))
            # parse our response, should be JSON that looks something like:
            # [{
            #   "recipient" : recipient_number_1,
            #   "id" : Unique_identifier (universally unique identifier UUID)
            # }]
            event.status_code = response.status_code
            event.response_body = response.text

            response_json = response.json()

            # we only care about the first piece
            if response_json and len(response_json) > 0:
                external_id = response_json[0].get('id', None)

        except Exception as e:
            raise SendException(six.text_type(e), event=event, start=start)

        if response.status_code != 200 and response.status_code != 201 and response.status_code != 202:  # pragma: needs cover
            raise SendException("Got non-200 response [%d] from API" % response.status_code,
                                event=event, start=start)

        Channel.success(channel, msg, WIRED, start, event=event, external_id=external_id)
