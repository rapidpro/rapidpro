# -*- coding: utf-8 -*-
from __future__ import absolute_import, division, print_function, unicode_literals

import time

import phonenumbers
import requests
import six
from django.urls import reverse
from django.utils.http import urlencode

from django.utils.translation import ugettext_lazy as _

from temba.channels.types.kannel.views import ClaimView
from temba.contacts.models import TEL_SCHEME
from temba.msgs.models import WIRED
from temba.utils.http import HttpEvent
from ...models import Channel, ChannelType, SendException, Encoding


class KannelType(ChannelType):
    """
    An Kannel channel (http://www.kannel.org/)
    """

    code = 'KN'
    category = ChannelType.Category.PHONE

    name = "Kannel"
    icon = "icon-channel-kannel"

    claim_blurb = _("""Connect your <a href="http://www.kannel.org/" target="_blank">Kannel</a> instance, we'll walk you through
                       the steps necessary to get your SMSC connection working in a few minutes.""")
    claim_view = ClaimView

    schemes = [TEL_SCHEME]
    max_length = 1600

    attachment_support = False

    def send(self, channel, msg, text):
        # build our callback dlr url, kannel will call this when our message is sent or delivered
        dlr_url = 'https://%s%s?id=%d&status=%%d' % (channel.callback_domain, reverse('courier.kn', args=[channel.uuid, 'status']), msg.id)
        dlr_mask = 31

        # build our payload
        payload = dict()
        payload['from'] = channel.address
        payload['username'] = channel.config[Channel.CONFIG_USERNAME]
        payload['password'] = channel.config[Channel.CONFIG_PASSWORD]
        payload['text'] = text
        payload['to'] = msg.urn_path
        payload['dlr-url'] = dlr_url
        payload['dlr-mask'] = dlr_mask

        # if this a reply to a message, set a higher priority
        if msg.response_to_id:
            payload['priority'] = 1

        # should our to actually be in national format?
        use_national = channel.config.get(Channel.CONFIG_USE_NATIONAL, False)
        if use_national:
            # parse and remap our 'to' address
            parsed = phonenumbers.parse(msg.urn_path)
            payload['to'] = str(parsed.national_number)

        # figure out if we should send encoding or do any of our own substitution
        desired_encoding = channel.config.get(Channel.CONFIG_ENCODING, Channel.ENCODING_DEFAULT)

        # they want unicode, they get unicode!
        if desired_encoding == Channel.ENCODING_UNICODE:
            payload['coding'] = '2'
            payload['charset'] = 'utf8'

        # otherwise, if this is smart encoding, try to derive it
        elif desired_encoding == Channel.ENCODING_SMART:
            # if this is smart encoding, figure out what encoding we will use
            encoding, text = Channel.determine_encoding(text, replace=True)
            payload['text'] = text

            if encoding == Encoding.UNICODE:
                payload['coding'] = '2'
                payload['charset'] = 'utf8'

        log_payload = payload.copy()
        log_payload['password'] = 'x' * len(log_payload['password'])

        url = channel.config[Channel.CONFIG_SEND_URL]
        log_url = url
        if log_url.find("?") >= 0:  # pragma: no cover
            log_url += "&" + urlencode(log_payload)
        else:
            log_url += "?" + urlencode(log_payload)

        event = HttpEvent('GET', log_url)
        start = time.time()

        try:
            if channel.config.get(Channel.CONFIG_VERIFY_SSL, True):
                response = requests.get(url, verify=True, params=payload, timeout=15)
            else:
                response = requests.get(url, verify=False, params=payload, timeout=15)

            event.status_code = response.status_code
            event.response_body = response.text
        except Exception as e:
            raise SendException(six.text_type(e), event=event, start=start)

        if response.status_code != 200 and response.status_code != 201 and response.status_code != 202:
            raise SendException("Got non-200 response [%d] from Kannel" % response.status_code,
                                event=event, start=start)

        Channel.success(channel, msg, WIRED, start, event=event)
