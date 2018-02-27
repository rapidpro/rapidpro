# -*- coding: utf-8 -*-
from __future__ import absolute_import, division, print_function, unicode_literals

import time

from django.utils.translation import ugettext_lazy as _
from temba.contacts.models import JIOCHAT_SCHEME
from temba.msgs.models import WIRED
from temba.utils.jiochat import JiochatClient
from .tasks import refresh_jiochat_access_tokens
from .views import ClaimView
from ...models import Channel, ChannelType


class JioChatType(ChannelType):
    """
    A JioChat channel (https://www.jiochat.com)
    """
    code = 'JC'
    category = ChannelType.Category.SOCIAL_MEDIA

    name = "JioChat"
    icon = 'icon-jiochat'

    claim_blurb = _("""Add a <a href="https://jiochat.me">JioChat</a> bot to send and receive messages to JioChat users
                for free. Your users will need an Android, Windows or iOS device and a JioChat account to send
                and receive messages.""")
    claim_view = ClaimView

    schemes = [JIOCHAT_SCHEME]
    max_length = 1600
    attachment_support = False
    free_sending = True

    configuration_blurb = _(
        """
        To finish configuring your JioChat connection, you'll need to enter the following webhook URL and token on JioChat Developer Center configuration
        """
    )

    configuration_urls = (
        dict(
            label=_("Webhook URL"),
            url="https://{{ channel.callback_domain }}{% url 'courier.jc' channel.uuid %}",
        ),
        dict(
            label=_("Token"),
            url="{{ channel.config.secret }}",
        )
    )

    def setup_periodic_tasks(self, sender):
        # automatically refresh the access token
        sender.add_periodic_task(3600, refresh_jiochat_access_tokens)

    def send(self, channel, msg, text):
        data = {'msgtype': 'text', 'touser': msg.urn_path, 'text': {'content': text}}

        client = JiochatClient(channel.uuid,
                               channel.config.get('jiochat_app_id'),
                               channel.config.get('jiochat_app_secret'))

        start = time.time()

        response, event = client.send_message(data, start)

        Channel.success(channel, msg, WIRED, start, event=event)
