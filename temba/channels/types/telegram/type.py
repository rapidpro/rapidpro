# -*- coding: utf-8 -*-
from __future__ import absolute_import, division, print_function, unicode_literals

import requests
import telegram
import time
import json

from django.urls import reverse
from django.utils.http import urlencode
from django.utils.translation import ugettext_lazy as _
from temba.contacts.models import TELEGRAM_SCHEME
from temba.msgs.models import Attachment, WIRED
from temba.utils.http import HttpEvent
from .views import ClaimView
from ...models import Channel, ChannelType, SendException


class TelegramType(ChannelType):
    """
    A Telegram bot channel
    """
    code = 'TG'
    category = ChannelType.Category.SOCIAL_MEDIA

    name = "Telegram"
    icon = 'icon-telegram'
    show_config_page = False

    claim_blurb = _("""Add a <a href="https://telegram.org">Telegram</a> bot to send and receive messages to Telegram
    users for free. Your users will need an Android, Windows or iOS device and a Telegram account to send and receive
    messages.""")
    claim_view = ClaimView

    schemes = [TELEGRAM_SCHEME]
    max_length = 1600
    attachment_support = True
    free_sending = True

    def activate(self, channel):
        config = channel.config
        bot = telegram.Bot(config['auth_token'])
        bot.set_webhook("https://" + channel.callback_domain + reverse('courier.tg', args=[channel.uuid]))

    def deactivate(self, channel):
        config = channel.config
        bot = telegram.Bot(config['auth_token'])
        bot.delete_webhook()

    def send(self, channel, msg, text):
        auth_token = channel.config['auth_token']
        send_url = 'https://api.telegram.org/bot%s/sendMessage' % auth_token
        post_body = {'chat_id': msg.urn_path, 'text': text}

        metadata = msg.metadata if hasattr(msg, 'metadata') else {}
        quick_replies = metadata.get('quick_replies', [])
        formatted_replies = json.dumps(dict(resize_keyboard=True, one_time_keyboard=True,
                                            keyboard=[[dict(text=item[:self.quick_reply_text_size])] for item in quick_replies]))

        if quick_replies:
            post_body['reply_markup'] = formatted_replies

        start = time.time()

        # for now we only support sending one attachment per message but this could change in future
        attachments = Attachment.parse_all(msg.attachments)
        attachment = attachments[0] if attachments else None

        if attachment:
            category = attachment.content_type.split('/')[0]
            if category == 'image':
                send_url = 'https://api.telegram.org/bot%s/sendPhoto' % auth_token
                post_body['photo'] = attachment.url
                post_body['caption'] = text
                del post_body['text']
            elif category == 'video':
                send_url = 'https://api.telegram.org/bot%s/sendVideo' % auth_token
                post_body['video'] = attachment.url
                post_body['caption'] = text
                del post_body['text']
            elif category == 'audio':
                send_url = 'https://api.telegram.org/bot%s/sendAudio' % auth_token
                post_body['audio'] = attachment.url
                post_body['caption'] = text
                del post_body['text']

        event = HttpEvent('POST', send_url, urlencode(post_body))

        try:
            response = requests.post(send_url, post_body)
            event.status_code = response.status_code
            event.response_body = response.text

            external_id = response.json()['result']['message_id']
        except Exception as e:
            raise SendException(str(e), event=event, start=start)

        Channel.success(channel, msg, WIRED, start, event=event, external_id=external_id)
