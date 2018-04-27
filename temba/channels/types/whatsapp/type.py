# -*- coding: utf-8 -*-
from __future__ import absolute_import, division, print_function, unicode_literals

import requests
from django.conf.urls import url
from django.forms import ValidationError
from django.urls import reverse
from django.utils.translation import ugettext_lazy as _

from temba.channels.models import Channel
from temba.channels.types.whatsapp.views import ClaimView, RefreshView
from temba.contacts.models import WHATSAPP_SCHEME
from ...models import ChannelType


class WhatsAppType(ChannelType):
    """
    A WhatsApp Channel Type
    """
    code = 'WA'
    category = ChannelType.Category.SOCIAL_MEDIA

    courier_url = r'^wa/(?P<uuid>[a-z0-9\-]+)/(?P<action>receive|status)$'

    name = "WhatsApp"
    icon = 'icon-whatsapp'

    claim_blurb = _("""If you have an enterprise WhatsApp account, you can connect it to communicate with your contacts""")
    claim_view = ClaimView

    schemes = [WHATSAPP_SCHEME]
    max_length = 4096
    attachment_support = False

    def is_available_to(self, user):
        return user.groups.filter(name="Beta")

    def send(self, channel, msg, text):  # pragma: no cover
        raise Exception("Sending WhatsApp messages is only possible via Courier")

    def get_urls(self):
        return [
            self.get_claim_url(),
            url(r'^refresh/(?P<uuid>[a-z0-9\-]+)/?$', RefreshView.as_view(), name='refresh')
        ]

    def activate(self, channel):
        domain = channel.org.get_brand_domain()
        headers = {"Authorization": "Bearer %s" % channel.config[Channel.CONFIG_AUTH_TOKEN]}

        # first set our callbacks
        payload = {
            'settings': {
                'application': {
                    'webhooks': {
                        "url": "https://" + domain + reverse('courier.wa', args=[channel.uuid, 'receive'])
                    }
                }
            }
        }

        resp = requests.patch(channel.config[Channel.CONFIG_BASE_URL] + '/v1/settings/application',
                              json=payload, headers=headers)

        if resp.status_code != 200:
            raise ValidationError(_("Unable to register callbacks: %s", resp.content))

        # then make sure group chats are disabled
        payload = {
            "allow_unsolicited_add": False
        }

        resp = requests.patch(channel.config[Channel.CONFIG_BASE_URL] + '/v1/settings/groups',
                              json=payload, headers=headers)

        if resp.status_code != 200:
            raise ValidationError(_("Unable to configure channel: %s", resp.content))

        # TODO: Figure out what the new endpoints are for upping our quotas
        # payload = {
        #     "payload": {
        #         "set_settings": {
        #             "messaging_api_rate_limit": ["15", "54600", "1000000"],
        #             "unique_message_sends_rate_limit": ["15", "54600", "1000000"],
        #             "contacts_api_rate_limit": ["15", "54600", "1000000"]
        #         }
        #     }
        # }
        #
        # resp = requests.post(channel.config[Channel.CONFIG_BASE_URL] + '/api/control.php',
        #                      json=payload,
        #                      auth=(channel.config[Channel.CONFIG_USERNAME],
        #                            channel.config[Channel.CONFIG_PASSWORD]))
        #
        # if resp.status_code != 200:
        #     raise ValidationError(_("Unable to configure channel: %s", resp.content))
