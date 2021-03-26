from urllib.parse import urljoin

import requests

from django.forms import ValidationError
from django.urls import reverse
from django.utils.translation import ugettext_lazy as _

from temba.channels.models import Channel
from temba.contacts.models import URN

from ...models import ChannelType
from .views import ClaimView


class TextItWhatsAppType(ChannelType):
    """
    Type for TextIt WhatsApp Service
    """

    code = "TXW"

    # uncommment when we launch
    # category = ChannelType.Category.SOCIAL_MEDIA

    courier_url = r"^txw/(?P<uuid>[a-z0-9\-]+)/(?P<action>receive)$"

    name = "TextIt WhatsApp"
    icon = "icon-whatsapp"

    show_config_page = False

    claim_blurb = _(
        "Easily use your own Enterprise WhatsApp account using %(link)s to communicate with your contacts. "
    ) % {"link": '<a href="https://whatsapp.textit.com/">TextIt WhatsApp Hosting</a>'}
    claim_view = ClaimView

    schemes = [URN.WHATSAPP_SCHEME]
    max_length = 4096
    attachment_support = True

    def update_webhook(self, channel, url):
        headers = {
            "Authorization": "Bearer " + channel.config[Channel.CONFIG_AUTH_TOKEN],
            "Content-Type": "application/json",
        }

        conf_url = urljoin(channel.config[Channel.CONFIG_BASE_URL], "/conf/webhook")

        # set our webhook
        payload = {"url": url}
        resp = requests.post(conf_url, json=payload, headers=headers)

        if resp.status_code != 200:
            raise ValidationError(_("Unable to register callbacks: %(resp)s"), params={"resp": resp.content})

    def deactivate(self, channel):
        self.update_webhook(channel, "")

    def activate(self, channel):
        domain = channel.org.get_brand_domain()
        webhook_url = "https://" + domain + reverse("courier.txw", args=[channel.uuid, "receive"])
        self.update_webhook(channel, webhook_url)
