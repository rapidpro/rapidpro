from __future__ import unicode_literals, absolute_import

import json
import requests
import six
import time

from django.conf import settings
from django.urls import reverse
from django.utils.translation import ugettext_lazy as _
from temba.contacts.models import VIBER_SCHEME
from temba.msgs.models import WIRED
from temba.utils.http import HttpEvent
from .views import ClaimView
from ...models import Channel, ChannelType, SendException, TEMBA_HEADERS


class ViberPublicType(ChannelType):
    """
    A Viber public account channel (https://www.viber.com/public-accounts/)
    """
    code = 'VP'
    category = ChannelType.Category.SOCIAL_MEDIA

    name = "Viber"
    icon = 'icon-viber'

    claim_blurb = _("""Connect a <a href="http://viber.com/en/">Viber</a> public channel to send and receive messages to
    Viber users for free. Your users will need an Android, Windows or iOS device and a Viber account to send and receive
    messages.""")
    claim_view = ClaimView

    scheme = VIBER_SCHEME
    max_length = 7000
    attachment_support = False
    free_sending = True

    def activate(self, channel):
        auth_token = channel.config_json()['auth_token']
        handler_url = "https://" + settings.TEMBA_HOST + reverse('handlers.viber_public_handler', args=[channel.uuid])

        requests.post('https://chatapi.viber.com/pa/set_webhook', json={
            'auth_token': auth_token,
            'url': handler_url,
            'event_types': ['delivered', 'failed', 'conversation_started']
        })

    def deactivate(self, channel):
        auth_token = channel.config_json()['auth_token']
        requests.post('https://chatapi.viber.com/pa/set_webhook', json={'auth_token': auth_token, 'url': ''})

    def send(self, channel, msg, text):
        url = 'https://chatapi.viber.com/pa/send_message'
        payload = {
            'auth_token': channel.config['auth_token'],
            'receiver': msg.urn_path,
            'text': text,
            'type': 'text',
            'tracking_data': msg.id
        }

        event = HttpEvent('POST', url, json.dumps(payload))

        start = time.time()

        headers = {'Accept': 'application/json'}
        headers.update(TEMBA_HEADERS)

        try:
            response = requests.post(url, json=payload, headers=headers, timeout=5)
            event.status_code = response.status_code
            event.response_body = response.text

            response_json = response.json()
        except Exception as e:
            raise SendException(six.text_type(e), event=event, start=start)

        if response.status_code not in [200, 201, 202]:
            raise SendException("Got non-200 response [%d] from API" % response.status_code,
                                event=event, start=start)

        # success is 0, everything else is a failure
        if response_json['status'] != 0:
            raise SendException("Got non-0 status [%d] from API" % response_json['status'],
                                event=event, fatal=True, start=start)

        external_id = response.json().get('message_token', None)
        Channel.success(channel, msg, WIRED, start, event=event, external_id=external_id)
