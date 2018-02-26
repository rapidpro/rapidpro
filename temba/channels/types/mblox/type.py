# -*- coding: utf-8 -*-
from __future__ import absolute_import, division, print_function, unicode_literals

import json
import time
import requests
import six

from django.utils.translation import ugettext_lazy as _

from temba.channels.views import AuthenticatedExternalClaimView
from temba.contacts.models import TEL_SCHEME
from temba.msgs.models import WIRED
from temba.utils.http import HttpEvent
from ...models import Channel, ChannelType, SendException


class MbloxType(ChannelType):
    """
    A Mblox channel (https://www.mblox.com/)
    """

    code = 'MB'
    category = ChannelType.Category.PHONE

    name = "Mblox"

    claim_blurb = _("""Easily add a two way number you have configured with <a href="https://www.mblox.com/">Mblox</a> using their APIs.""")

    claim_view = AuthenticatedExternalClaimView

    schemes = [TEL_SCHEME]
    max_length = 459
    attachment_support = False

    configuration_blurb = _(
        """
        As a last step you'll need to set the following callback URL on your Mblox account:
        """
    )

    configuration_urls = (
        dict(
            label=_("Callback URL"),
            url="https://{{ channel.callback_domain }}{% url 'courier.mb' channel.uuid 'receive' %}",
            description=_("This endpoint will be called by Mblox when new messages are received to your number and for delivery reports."),
        ),
    )

    def send(self, channel, msg, text):
        # build our payload
        payload = dict()
        payload['from'] = channel.address.lstrip('+')
        payload['to'] = [msg.urn_path.lstrip('+')]
        payload['body'] = text
        payload['delivery_report'] = 'per_recipient'

        request_body = json.dumps(payload)

        url = 'https://api.mblox.com/xms/v1/%s/batches' % channel.config[Channel.CONFIG_USERNAME]
        headers = {'Content-Type': 'application/json',
                   'Authorization': 'Bearer %s' % channel.config[Channel.CONFIG_PASSWORD]}

        start = time.time()

        event = HttpEvent('POST', url, request_body)

        try:
            response = requests.post(url, request_body, headers=headers, timeout=15)
            event.status_code = response.status_code
            event.response_body = response.text
        except Exception as e:  # pragma: no cover
            raise SendException(six.text_type(e), event=event, start=start)

        if response.status_code != 200 and response.status_code != 201 and response.status_code != 202:
            raise SendException("Got non-200 response [%d] from MBlox" % response.status_code,
                                event=event, start=start)

        # response in format:
        # {
        #  "id": "Oyi75urq5_yB",
        #  "to": [ "593997290044" ],
        #  "from": "18444651185",
        #  "canceled": false,
        #  "body": "Hello world.",
        #  "type": "mt_text",
        #  "created_at": "2016-03-30T17:55:03.683Z",
        #  "modified_at": "2016-03-30T17:55:03.683Z",
        #  "delivery_report": "none",
        #  "expire_at": "2016-04-02T17:55:03.683Z"
        # }

        external_id = None
        try:
            response_json = response.json()
            external_id = response_json['id']
        except Exception:  # pragma: no cover
            raise SendException("Unable to parse response body from MBlox",
                                event=event, start=start)

        Channel.success(channel, msg, WIRED, start, event=event, external_id=external_id)
