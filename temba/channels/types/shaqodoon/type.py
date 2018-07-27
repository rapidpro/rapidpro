# -*- coding: utf-8 -*-
from __future__ import absolute_import, division, print_function, unicode_literals

import time
import requests
import six

from django.utils.http import urlencode
from django.utils.translation import ugettext_lazy as _

from temba.channels.types.shaqodoon.views import ClaimView
from temba.contacts.models import TEL_SCHEME
from temba.msgs.models import WIRED
from temba.utils.http import HttpEvent, http_headers
from ...models import Channel, ChannelType, SendException


class ShaqodoonType(ChannelType):
    """
    An Shaqodoon channel
    """

    code = 'SQ'
    category = ChannelType.Category.PHONE

    name = "Shaqodoon"

    claim_blurb = _("""If you are based in Somalia, you can integrate with Shaqodoon to send
                       and receive messages on your shortcode.""")
    claim_view = ClaimView

    schemes = [TEL_SCHEME]
    max_length = 1600
    attachment_support = False

    configuration_blurb = _(
        """
        To finish configuring your Shaqodoon connection you'll need to provide Shaqodoon with the following delivery
        URL for incoming messages to {{ channel.address }}.
        """
    )

    configuration_urls = (
        dict(
            label=_(""),
            url="https://{{ channel.callback_domain }}{% url 'courier.sq' channel.uuid 'receive' %}"
        ),
    )

    def is_available_to(self, user):
        org = user.get_org()
        return org.timezone and six.text_type(org.timezone) in ['Africa/Mogadishu']

    def is_recommended_to(self, user):
        return self.is_available_to(user)

    def send(self, channel, msg, text):
        # requests are signed with a key built as follows:
        # signing_key = md5(username|password|from|to|msg|key|current_date)
        # where current_date is in the format: d/m/y H
        payload = {'from': channel.address.lstrip('+'), 'to': msg.urn_path.lstrip('+'),
                   'username': channel.config[Channel.CONFIG_USERNAME], 'password': channel.config[Channel.CONFIG_PASSWORD],
                   'msg': text}

        # build our send URL
        url = channel.config[Channel.CONFIG_SEND_URL] + "?" + urlencode(payload)
        start = time.time()

        event = HttpEvent('GET', url)

        try:
            # these guys use a self signed certificate
            response = requests.get(url, headers=http_headers(), timeout=15, verify=False)
            event.status_code = response.status_code
            event.response_body = response.text

        except Exception as e:
            raise SendException(six.text_type(e), event=event, start=start)

        if response.status_code != 200 and response.status_code != 201 and response.status_code != 202:
            raise SendException("Got non-200 response [%d] from API" % response.status_code,
                                event=event, start=start)

        Channel.success(channel, msg, WIRED, start, event=event)
