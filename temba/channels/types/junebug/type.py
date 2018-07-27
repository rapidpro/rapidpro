# -*- coding: utf-8 -*-
from __future__ import absolute_import, division, print_function, unicode_literals

import json
import time
import requests

from datetime import timedelta
from django.urls import reverse
from django.utils import timezone
from django.utils.translation import ugettext_lazy as _
from six import text_type

from temba.channels.models import ChannelType, Channel, SendException
from temba.channels.types.junebug.views import ClaimView
from temba.contacts.models import TEL_SCHEME
from temba.msgs.models import Msg, WIRED
from temba.ussd.models import USSDSession
from temba.utils.http import HttpEvent, http_headers


class JunebugType(ChannelType):
    """
    A Junebug channel
    """

    code = 'JN'
    category = ChannelType.Category.PHONE

    name = "Junebug"
    icon = "icon-junebug"

    claim_blurb = _("""Connect your <a href="https://junebug.praekelt.org/" target="_blank">Junebug</a> instance that you have already set up and configured.""")
    claim_view = ClaimView

    schemes = [TEL_SCHEME]
    max_length = 1600

    configuration_blurb = _(
        """
        As a last step you'll need to configure Junebug to call the following URL for MO (incoming) messages.
        """
    )

    configuration_urls = (
        dict(
            label=_("Push Message URL"),
            url="https://{{ channel.callback_domain }}{% url 'courier.jn' channel.uuid 'inbound' %}",
            description=_("This endpoint will be called by Junebug when new messages are received to your number, it must be configured to be called as a POST"),
        ),
    )

    def send(self, channel, msg, text):
        connection = None

        # if the channel config has specified and override hostname use that, otherwise use settings
        callback_domain = channel.config.get(Channel.CONFIG_RP_HOSTNAME_OVERRIDE, None)
        if not callback_domain:
            callback_domain = channel.callback_domain

        # the event url Junebug will relay events to
        event_url = 'http://%s%s' % (callback_domain, reverse('courier.jn', args=[channel.uuid, 'event']))

        is_ussd = Channel.get_type_from_code(channel.channel_type).category == ChannelType.Category.USSD

        # build our payload
        payload = {'event_url': event_url, 'content': text}

        secret = channel.config.get(Channel.CONFIG_SECRET)
        if secret is not None:
            payload['event_auth_token'] = secret

        if is_ussd:
            connection = USSDSession.objects.get_with_status_only(msg.connection_id)
            # make sure USSD responses are only valid for a short window
            response_expiration = timezone.now() - timedelta(seconds=180)
            external_id = None
            if msg.response_to_id and msg.created_on > response_expiration:
                external_id = Msg.objects.values_list('external_id', flat=True).filter(pk=msg.response_to_id).first()
            # NOTE: Only one of `to` or `reply_to` may be specified, use external_id if we have it.
            if external_id:
                payload['reply_to'] = external_id
            else:
                payload['to'] = msg.urn_path
            payload['channel_data'] = {
                'continue_session': connection and not connection.should_end or False,
            }
        else:
            payload['from'] = channel.address
            payload['to'] = msg.urn_path

        log_url = channel.config[Channel.CONFIG_SEND_URL]
        start = time.time()

        event = HttpEvent('POST', log_url, json.dumps(payload))
        headers = http_headers(extra={'Content-Type': 'application/json'})

        try:
            response = requests.post(
                channel.config[Channel.CONFIG_SEND_URL], verify=True,
                json=payload, timeout=15, headers=headers,
                auth=(channel.config[Channel.CONFIG_USERNAME],
                      channel.config[Channel.CONFIG_PASSWORD]))

            event.status_code = response.status_code
            event.response_body = response.text

        except Exception as e:
            raise SendException(text_type(e), event=event, start=start)

        if not (200 <= response.status_code < 300):
            raise SendException("Received a non 200 response %d from Junebug" % response.status_code,
                                event=event, start=start)

        data = response.json()

        if is_ussd and connection and connection.should_end:
            connection.close()

        try:
            message_id = data['result']['message_id']
            Channel.success(channel, msg, WIRED, start, event=event, external_id=message_id)
        except KeyError as e:
            raise SendException("Unable to read external message_id: %r" % (e,),
                                event=HttpEvent('POST', log_url,
                                                request_body=json.dumps(json.dumps(payload)),
                                                response_body=json.dumps(data)),
                                start=start)
