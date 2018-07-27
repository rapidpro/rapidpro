# -*- coding: utf-8 -*-
from __future__ import absolute_import, division, print_function, unicode_literals

import time
import requests
import six

from django.urls import reverse
from django.utils.http import urlencode
from django.utils.translation import ugettext_lazy as _

from temba.channels.views import AuthenticatedExternalCallbackClaimView
from temba.contacts.models import TEL_SCHEME
from temba.msgs.models import WIRED
from temba.utils.http import HttpEvent, http_headers
from ...models import Channel, ChannelType, SendException


class HighConnectionType(ChannelType):
    """
    An High Connection channel (http://www.highconnexion.com/en/)
    """

    code = 'HX'
    category = ChannelType.Category.PHONE

    name = "High Connection"
    slug = "high_connection"

    claim_blurb = _("""If you are based in France, you can purchase a number from High Connexion
                  <a href="http://www.highconnexion.com/en/">High Connection</a> and connect it in a few simple steps.""")
    claim_view = AuthenticatedExternalCallbackClaimView

    schemes = [TEL_SCHEME]
    max_length = 1500
    attachment_support = False

    configuration_blurb = _(
        """
        To finish configuring your connection you'll need to notify HighConnection of the following URL for incoming (MO) messages
        """
    )

    configuration_urls = (
        dict(
            label=_("Receive URL"),
            url="https://{{ channel.callback_domain }}{% url 'courier.hx' channel.uuid 'receive' %}",
        ),
    )

    def is_available_to(self, user):
        org = user.get_org()
        return org.timezone and six.text_type(org.timezone) in ["Europe/Paris"]

    def send(self, channel, msg, text):
        callback_domain = channel.callback_domain

        payload = {
            'accountid': channel.config[Channel.CONFIG_USERNAME],
            'password': channel.config[Channel.CONFIG_PASSWORD],
            'text': text,
            'to': msg.urn_path,
            'ret_id': msg.id,
            'datacoding': 8,
            'userdata': 'textit',
            'ret_url': 'https://%s%s' % (callback_domain, reverse('handlers.hcnx_handler', args=['status', channel.uuid])),
            'ret_mo_url': 'https://%s%s' % (callback_domain, reverse('handlers.hcnx_handler', args=['receive', channel.uuid]))
        }

        # build our send URL
        url = 'https://highpushfastapi-v2.hcnx.eu/api' + '?' + urlencode(payload)
        log_payload = urlencode(payload)
        start = time.time()

        event = HttpEvent('GET', url, log_payload)

        try:
            response = requests.get(url, headers=http_headers(), timeout=30)
            event.status_code = response.status_code
            event.response_body = response.text
        except Exception as e:
            raise SendException(six.text_type(e), event=event, start=start)

        if response.status_code != 200 and response.status_code != 201 and response.status_code != 202:
            raise SendException("Got non-200 response [%d] from API" % response.status_code,
                                event=event, start=start)

        Channel.success(channel, msg, WIRED, start, event=event)
