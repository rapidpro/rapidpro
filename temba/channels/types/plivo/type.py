from __future__ import unicode_literals, absolute_import

import json
import time
import plivo
import six

from django.urls import reverse
from django.utils.translation import ugettext_lazy as _

from temba.channels.models import ChannelType, SendException, Channel
from temba.channels.types.plivo.views import ClaimView
from temba.contacts.models import TEL_SCHEME
from temba.msgs.models import WIRED
from temba.utils.http import HttpEvent


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
        config = channel.config_json()
        client = plivo.RestAPI(config[Channel.CONFIG_PLIVO_AUTH_ID],
                               config[Channel.CONFIG_PLIVO_AUTH_TOKEN])
        client.delete_application(params=dict(app_id=config[Channel.CONFIG_PLIVO_APP_ID]))

    def send(self, channel, msg, text):
        # url used for logs and exceptions
        url = 'https://api.plivo.com/v1/Account/%s/Message/' % channel.config[Channel.CONFIG_PLIVO_AUTH_ID]

        client = plivo.RestAPI(channel.config[Channel.CONFIG_PLIVO_AUTH_ID], channel.config[Channel.CONFIG_PLIVO_AUTH_TOKEN])
        status_url = "https://%s%s" % (channel.callback_domain, reverse('handlers.plivo_handler', args=['status', channel.uuid]))

        payload = {'src': channel.address.lstrip('+'),
                   'dst': msg.urn_path.lstrip('+'),
                   'text': text,
                   'url': status_url,
                   'method': 'POST'}

        event = HttpEvent('POST', url, json.dumps(payload))

        start = time.time()

        try:
            # TODO: Grab real request and response here
            plivo_response_status, plivo_response = client.send_message(params=payload)
            event.status_code = plivo_response_status
            event.response_body = plivo_response

        except Exception as e:  # pragma: no cover
            raise SendException(six.text_type(e), event=event, start=start)

        if plivo_response_status != 200 and plivo_response_status != 201 and plivo_response_status != 202:  # pragma: no cover
            raise SendException("Got non-200 response [%d] from API" % plivo_response_status,
                                event=event, start=start)

        external_id = plivo_response['message_uuid'][0]
        Channel.success(channel, msg, WIRED, start, event=event, external_id=external_id)
