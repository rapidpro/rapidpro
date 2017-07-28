from __future__ import unicode_literals, absolute_import

import json
import requests
import six
import time

from django.utils.translation import ugettext_lazy as _
from temba.contacts.models import FCM_SCHEME
from temba.msgs.models import WIRED
from temba.utils.http import HttpEvent
from .views import ClaimView
from ...models import Channel, ChannelType, SendException, TEMBA_HEADERS


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

    scheme = FCM_SCHEME
    max_length = 10000
    attachment_support = False
    free_sending = True

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

        payload = json.dumps(data)
        headers = {'Content-Type': 'application/json', 'Authorization': 'key=%s' % channel.config.get('FCM_KEY')}
        headers.update(TEMBA_HEADERS)

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
