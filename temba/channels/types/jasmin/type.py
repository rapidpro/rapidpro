# -*- coding: utf-8 -*-
from __future__ import absolute_import, division, print_function, unicode_literals

import time
import re
import requests
import six

from django.urls import reverse
from django.utils.http import urlencode
from django.utils.translation import ugettext_lazy as _

from temba.channels.types.jasmin.views import ClaimView
from temba.contacts.models import TEL_SCHEME
from temba.msgs.models import WIRED
from temba.utils import gsm7
from temba.utils.http import HttpEvent
from ...models import Channel, ChannelType, SendException


class JasminType(ChannelType):
    """
    An Jasmin channel (http://www.jasminsms.com/)
    """

    code = 'JS'
    category = ChannelType.Category.PHONE

    name = "Jasmin"

    claim_blurb = _("""Connect your <a href="http://www.jasminsms.com/" target="_blank">Jasmin</a> instance that you have
                       already connected to an SMSC.""")
    claim_view = ClaimView

    schemes = [TEL_SCHEME]
    max_length = 1600
    attachment_support = False

    configuration_blurb = _(
        """
        As a last step you'll need to configure Jasmin to call the following URL for MO (incoming) messages.
        """
    )

    configuration_urls = (
        dict(
            label=_("Push Message URL"),
            url="https://{{ channel.callback_domain }}{% url 'courier.js' channel.uuid 'receive' %}",
            description=_("    This endpoint will be called by Jasmin when new messages are received to your number, it must be configured to be called as a POST"),
        ),
    )

    def send(self, channel, msg, text):
        # build our callback dlr url, jasmin will call this when our message is sent or delivered
        dlr_url = 'https://%s%s' % (channel.callback_domain, reverse('handlers.jasmin_handler', args=['status', channel.uuid]))

        # encode to GSM7
        encoded = gsm7.encode(text, 'replace')[0]

        # build our payload
        payload = {'from': channel.address.lstrip('+'), 'to': msg.urn_path.lstrip('+'),
                   'username': channel.config[Channel.CONFIG_USERNAME], 'dlr': 'yes',
                   'password': channel.config[Channel.CONFIG_PASSWORD], 'dlr-url': dlr_url, 'dlr-level': '2',
                   'dlr-method': 'POST', 'coding': '0', 'content': encoded}

        log_payload = payload.copy()
        log_payload['password'] = 'x' * len(log_payload['password'])

        log_url = channel.config[Channel.CONFIG_SEND_URL] + "?" + urlencode(log_payload)
        start = time.time()

        event = HttpEvent('GET', log_url, log_payload)

        try:
            response = requests.get(channel.config[Channel.CONFIG_SEND_URL], verify=True, params=payload, timeout=15)
            event.status_code = response.status_code
            event.response_body = response.text

        except Exception as e:
            raise SendException(six.text_type(e),
                                event=event, start=start)

        if response.status_code != 200 and response.status_code != 201 and response.status_code != 202:
            raise SendException("Got non-200 response [%d] from Jasmin" % response.status_code,
                                event=event, start=start)

        # save the external id, response should be in format:
        # Success "07033084-5cfd-4812-90a4-e4d24ffb6e3d"
        external_id = None
        match = re.match(r"Success \"(.*)\"", response.text)
        if match:
            external_id = match.group(1)

        Channel.success(channel, msg, WIRED, start, event=event, external_id=external_id)
