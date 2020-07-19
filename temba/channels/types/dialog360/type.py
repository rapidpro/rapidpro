import requests

from django.conf.urls import url
from django.forms import ValidationError
from django.urls import reverse
from django.utils.translation import ugettext_lazy as _

from temba.channels.models import Channel
from temba.channels.types.dialog360.views import ClaimView
from temba.contacts.models import WHATSAPP_SCHEME

from ...models import ChannelType


class Dialog360Type(ChannelType):
    """
    A 360 Dialog Channel Type
    """

    code = "D3"
    category = ChannelType.Category.SOCIAL_MEDIA

    courier_url = r"^d3/(?P<uuid>[a-z0-9\-]+)/(?P<action>receive)$"

    name = "360Dialog WhatsApp"
    icon = "icon-whatsapp"

    claim_blurb = _(
        """Activate your own enterprise WhatsApp account in Dialog 360 to communicate with your contacts. <a href="https://www.360dialog.com/">Learn more about 360Dialog WhatsApp</a>"""
    )
    claim_view = ClaimView

    schemes = [WHATSAPP_SCHEME]
    max_length = 4096
    attachment_support = True

    def send(self, channel, msg, text):  # pragma: no cover
        raise Exception("Sending WhatsApp messages is only possible via Courier")

    def activate(self, channel):
        domain = channel.org.get_brand_domain()
        headers = {"D360-API-KEY": channel.config[Channel.CONFIG_AUTH_TOKEN]}

        # first set our callbacks
        payload = {"url": "https://" + domain + reverse("courier.d3", args=[channel.uuid, "receive"])}
        resp = requests.post(
            channel.config[Channel.CONFIG_BASE_URL] + "/v1/configs/webhook", json=payload, headers=headers
        )

        if resp.status_code != 200:
            raise ValidationError(_("Unable to register callbacks: %s", resp.content))
