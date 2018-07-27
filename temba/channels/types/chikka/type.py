# -*- coding: utf-8 -*-
from __future__ import absolute_import, division, print_function, unicode_literals

import time

import requests
import six
from datetime import timedelta
from django.utils import timezone

from django.utils.translation import ugettext_lazy as _

from temba.channels.types.chikka.views import ClaimView
from temba.contacts.models import TEL_SCHEME
from temba.msgs.models import Msg, WIRED
from temba.utils.http import HttpEvent, http_headers
from ...models import Channel, ChannelType, SendException


class ChikkaType(ChannelType):
    """
    An Chikka channel (http://www.jasminsms.com/)
    """

    code = 'CK'
    category = ChannelType.Category.PHONE

    name = "Chikka"

    claim_blurb = _("""If you are based in the Phillipines, you can integrate with Chikka to send
                       and receive messages on your shortcode.""")
    claim_view = ClaimView

    schemes = [TEL_SCHEME]
    max_length = 160
    attachment_support = False

    configuration_blurb = _(
        """
        To finish configuring your Chikka connection you need to set the following URLs in your Chikka account API settings.
        """
    )

    configuration_urls = (
        dict(
            label=_("Notification Receiver URL"),
            url="https://{{ channel.callback_domain }}{% url 'courier.ck' channel.uuid %}",
        ),
        dict(
            label=_("Message Receiver URL"),
            url="https://{{ channel.callback_domain }}{% url 'courier.ck' channel.uuid %}",
        )

    )

    def is_available_to(self, user):
        org = user.get_org()
        return org.timezone and six.text_type(org.timezone) in ['Asia/Manila']

    def send(self, channel, msg, text):

        payload = {
            'message_type': 'SEND',
            'mobile_number': msg.urn_path.lstrip('+'),
            'shortcode': channel.address,
            'message_id': msg.id,
            'message': text,
            'request_cost': 'FREE',
            'client_id': channel.config[Channel.CONFIG_USERNAME],
            'secret_key': channel.config[Channel.CONFIG_PASSWORD]
        }

        # if this is a response to a user SMS, then we need to set this as a reply
        # response ids are only valid for up to 24 hours
        response_window = timedelta(hours=24)
        if msg.response_to_id and msg.created_on > timezone.now() - response_window:
            response_to = Msg.objects.filter(id=msg.response_to_id).first()
            if response_to:
                payload['message_type'] = 'REPLY'
                payload['request_id'] = response_to.external_id

        # build our send URL
        url = 'https://post.chikka.com/smsapi/request'
        start = time.time()

        log_payload = payload.copy()
        log_payload['secret_key'] = 'x' * len(log_payload['secret_key'])

        event = HttpEvent('POST', url, log_payload)
        events = [event]

        try:
            response = requests.post(url, data=payload, headers=http_headers(), timeout=5)
            event.status_code = response.status_code
            event.response_body = response.text
        except Exception as e:
            raise SendException(six.text_type(e), event=event, start=start)

        # if they reject our request_id, send it as a normal send
        if response.status_code == 400 and 'request_id' in payload:
            error = response.json()
            if error.get('message', None) == 'BAD REQUEST' and error.get('description', None) == 'Invalid/Used Request ID':
                try:

                    # operate on a copy so we can still inspect our original call
                    payload = payload.copy()
                    del payload['request_id']
                    payload['message_type'] = 'SEND'

                    event = HttpEvent('POST', url, payload)
                    events.append(event)

                    response = requests.post(url, data=payload, headers=http_headers(), timeout=5)
                    event.status_code = response.status_code
                    event.response_body = response.text

                    log_payload = payload.copy()
                    log_payload['secret_key'] = 'x' * len(log_payload['secret_key'])

                except Exception as e:
                    raise SendException(six.text_type(e), events=events, start=start)

        if response.status_code != 200 and response.status_code != 201 and response.status_code != 202:
            raise SendException("Got non-200 response [%d] from API" % response.status_code,
                                events=events, start=start)

        Channel.success(channel, msg, WIRED, start, events=events)
