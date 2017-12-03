from __future__ import unicode_literals, absolute_import

import requests

from django.utils.translation import ugettext_lazy as _

from temba.channels.models import Channel
from temba.channels.types.whatsapp.views import ClaimView
from temba.contacts.models import WHATSAPP_SCHEME
from ...models import ChannelType
from django.urls import reverse
from django.forms import ValidationError


class WhatsAppType(ChannelType):
    """
    A WhatsApp Channel Type
    """
    code = 'WA'
    category = ChannelType.Category.SOCIAL_MEDIA

    name = "WhatsApp"
    icon = 'icon-channel-external'

    claim_blurb = _("""If you have an enterprise WhatsApp account, you can connect it to communicate with your contacts""")
    claim_view = ClaimView

    schemes = [WHATSAPP_SCHEME]
    max_length = 4096
    attachment_support = False

    def is_available_to(self, user):
        return user.groups.filter(name="Beta")

    def send(self, channel, msg, text):  # pragma: no cover
        raise Exception("Sending WhatsApp messages is only possible via Courier")

    def activate(self, channel):
        domain = channel.org.get_brand_domain()

        body = {
            'payload': {
                'set_settings': {
                    'webcallbacks': {
                        "0": "https://" + domain + reverse('courier.wa', args=[channel.uuid, 'status']),
                        "1": "https://" + domain + reverse('courier.wa', args=[channel.uuid, 'receive']),
                        "2": ""
                    }
                }
            }
        }

        resp = requests.post(channel.config_json()[Channel.CONFIG_BASE_URL] + '/api/control.php',
                             json=body,
                             auth=(channel.config_json()[Channel.CONFIG_USERNAME],
                                   channel.config_json()[Channel.CONFIG_PASSWORD]))

        if resp.status_code != 200:
            raise ValidationError(_("Unable to register callbacks: %s", resp.content))
