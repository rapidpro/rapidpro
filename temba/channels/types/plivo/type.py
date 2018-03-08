# -*- coding: utf-8 -*-
from __future__ import absolute_import, division, print_function, unicode_literals

import json
import time
import requests
import six

from django.urls import reverse
from django.utils.translation import ugettext_lazy as _

from temba.channels.models import ChannelType, SendException, Channel
from temba.channels.types.plivo.views import ClaimView
from temba.contacts.models import TEL_SCHEME
from temba.msgs.models import WIRED
from temba.utils.http import HttpEvent, http_headers


class PlivoType(ChannelType):
    """
    An Plivo channel (https://www.plivo.com/)
    """

    code = 'PL'
    category = ChannelType.Category.PHONE

    name = "Plivo"
    icon = "icon-channel-plivo"

    claim_blurb = _("""Easily add a two way number you have configured with <a href="https://www.plivo.com/">Plivo</a> using their APIs.""")
    claim_view = ClaimView

    show_config_page = False

    schemes = [TEL_SCHEME]
    max_length = 1600

    def deactivate(self, channel):
        config = channel.config
        requests.delete("https://api.plivo.com/v1/Account/%s/Application/%s/" % (config[Channel.CONFIG_PLIVO_AUTH_ID], config[Channel.CONFIG_PLIVO_APP_ID]),
                        auth=(config[Channel.CONFIG_PLIVO_AUTH_ID], config[Channel.CONFIG_PLIVO_AUTH_TOKEN]),
                        headers=http_headers(extra={'Content-Type': "application/json"}))

    def send(self, channel, msg, text):
        auth_id = channel.config[Channel.CONFIG_PLIVO_AUTH_ID]
        auth_token = channel.config[Channel.CONFIG_PLIVO_AUTH_TOKEN]

        url = 'https://api.plivo.com/v1/Account/%s/Message/' % auth_id
        status_url = "https://%s%s" % (channel.callback_domain, reverse('handlers.plivo_handler', args=['status', channel.uuid]))

        payload = {'src': channel.address.lstrip('+'),
                   'dst': msg.urn_path.lstrip('+'),
                   'text': text,
                   'url': status_url,
                   'method': 'POST'}

        event = HttpEvent('POST', url, json.dumps(payload))
        headers = http_headers(extra={'Content-Type': "application/json"})

        start = time.time()

        try:
            # TODO: Grab real request and response here
            response = requests.post(url, json=payload, headers=headers, auth=(auth_id, auth_token))
            event.status_code = response.status_code
            event.response_body = response.json()

        except Exception as e:  # pragma: no cover
            raise SendException(six.text_type(e), event=event, start=start)

        if response.status_code not in [200, 201, 202]:  # pragma: no cover
            raise SendException("Got non-200 response [%d] from API" % response.status_code,
                                event=event, start=start)

        external_id = response.json()['message_uuid'][0]
        Channel.success(channel, msg, WIRED, start, event=event, external_id=external_id)
