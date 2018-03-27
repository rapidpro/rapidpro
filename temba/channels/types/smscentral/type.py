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
from ...models import Channel, ChannelType, SendException


class SMSCentralType(ChannelType):
    """
    An SMSCentral channel (http://smscentral.com.np/)
    """

    code = 'SC'
    category = ChannelType.Category.PHONE

    name = "SMSCentral"
    icon = 'icon-channel-external'

    claim_blurb = _("""Easily add a two way number you have configured with <a href="http://smscentral.com.np/">SMSCentral</a> using their APIs.""")
    claim_view = AuthenticatedExternalClaimView

    schemes = [TEL_SCHEME]
    max_length = 1600
    max_tps = 1

    attachment_support = False

    configuration_blurb = _(
        """
        To finish configuring your SMSCentral connection you'll need to notify SMSCentral of the following URL.
        """
    )

    configuration_urls = (
        dict(
            label=_("Inbound URL"),
            url="https://{{ channel.callback_domain }}{% url 'courier.sc' channel.uuid 'receive' %}",
            description=_("This endpoint should be called by SMSCentral when new messages are received to your number.")
        ),
    )

    def is_available_to(self, user):
        org = user.get_org()
        return org.timezone and six.text_type(org.timezone) in ["Asia/Kathmandu"]

    def send(self, channel, msg, text):

        # strip a leading +
        mobile = msg.urn_path[1:] if msg.urn_path.startswith('+') else msg.urn_path

        payload = {
            'user': channel.config[Channel.CONFIG_USERNAME], 'pass': channel.config[Channel.CONFIG_PASSWORD], 'mobile': mobile, 'content': text,
        }

        url = 'http://smail.smscentral.com.np/bp/ApiSms.php'
        log_payload = urlencode(payload)

        event = HttpEvent('POST', url, log_payload)

        start = time.time()

        try:
            response = requests.post(url, data=payload, headers=http_headers(), timeout=30)
            event.status_code = response.status_code
            event.response_body = response.text

        except Exception as e:
            raise SendException(six.text_type(e), event=event, start=start)

        if response.status_code != 200 and response.status_code != 201 and response.status_code != 202:
            raise SendException("Got non-200 response [%d] from API" % response.status_code,
                                event=event, start=start)

        Channel.success(channel, msg, WIRED, start, event=event)
