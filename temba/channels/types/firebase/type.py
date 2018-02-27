# -*- coding: utf-8 -*-
from __future__ import absolute_import, division, print_function, unicode_literals

import json
import requests
import six
import time

from django.utils.translation import ugettext_lazy as _
from temba.contacts.models import FCM_SCHEME
from temba.msgs.models import WIRED
from temba.utils.http import HttpEvent, http_headers
from .views import ClaimView
from ...models import Channel, ChannelType, SendException


class FirebaseCloudMessagingType(ChannelType):
    """
    A Firebase Cloud Messaging channel (https://firebase.google.com/docs/cloud-messaging/)
    """
    code = 'FCM'
    category = ChannelType.Category.API

    name = "Firebase Cloud Messaging"
    icon = 'icon-fcm'

    claim_blurb = _("""Add a <a href="https://firebase.google.com/docs/cloud-messaging/" target="_blank"> Firebase Cloud
    Messaging Channel</a> to send and receive messages. Your users will need an App to send and receive messages.""")
    claim_view = ClaimView

    schemes = [FCM_SCHEME]
    max_length = 10000
    attachment_support = False
    free_sending = True
    quick_reply_text_size = 36

    configuration_blurb = _(
        """
        To use your Firebase Cloud Messaging channel you'll have to POST to the following URLs with the parameters below.
        """
    )

    configuration_urls = (
        dict(
            label=_("Contact Register"),
            url="https://{{ channel.callback_domain }}{% url 'handlers.fcm_handler' 'register' channel.uuid %}",
            description=_("To register contacts, POST to the following URL with the parameters urn, fcm_token and optionally name."),
        ),
        dict(
            label=_("Receive URL"),
            url="https://{{ channel.callback_domain }}{% url 'handlers.fcm_handler' 'receive' channel.uuid %}",
            description=_("To handle incoming messages, POST to the following URL with the parameters from, msg and fcm_token."),
        ),
    )

    def send(self, channel, msg, text):
        start = time.time()

        url = 'https://fcm.googleapis.com/fcm/send'
        title = channel.config.get('FCM_TITLE')
        data = {
            'data': {
                'type': 'rapidpro',
                'title': title,
                'message': text,
                'message_id': msg.id
            },
            'content_available': False,
            'to': msg.auth,
            'priority': 'high'
        }

        if channel.config.get('FCM_NOTIFICATION'):
            data['notification'] = {
                'title': title,
                'body': text
            }
            data['content_available'] = True

        metadata = msg.metadata if hasattr(msg, 'metadata') else {}
        quick_replies = metadata.get('quick_replies', [])
        formatted_replies = [dict(title=item[:self.quick_reply_text_size], payload=item[:self.quick_reply_text_size])
                             for item in quick_replies]

        if quick_replies:
            data['data']['quick_replies'] = formatted_replies

        payload = json.dumps(data)
        headers = http_headers(extra={'Content-Type': 'application/json', 'Authorization': 'key=%s' % channel.config.get('FCM_KEY')})

        event = HttpEvent('POST', url, payload)

        try:
            response = requests.post(url, data=payload, headers=headers, timeout=5)
            result = json.loads(response.text) if response.status_code == 200 else None

            event.status_code = response.status_code
            event.response_body = response.text
        except Exception as e:  # pragma: no cover
            raise SendException(six.text_type(e), event, start=start)

        if result and 'success' in result and result.get('success') == 1:
            external_id = result.get('multicast_id')
            Channel.success(channel, msg, WIRED, start, events=[event], external_id=external_id)
        else:
            raise SendException("Got non-200 response [%d] from Firebase Cloud Messaging" % response.status_code,
                                event, start=start)
