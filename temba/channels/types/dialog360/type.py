import requests

from django.forms import ValidationError
from django.urls import reverse
from django.utils.translation import ugettext_lazy as _

from temba.channels.models import Channel
from temba.channels.types.dialog360.views import ClaimView
from temba.contacts.models import URN

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
        "Activate your own enterprise WhatsApp account in %(link)s to communicate with your contacts. "
    ) % {"link": '<a href="https://www.360dialog.com/">360Dialog</a>'}
    claim_view = ClaimView

    schemes = [URN.WHATSAPP_SCHEME]
    max_length = 4096
    attachment_support = True

    def activate(self, channel):
        domain = channel.org.get_brand_domain()
        headers = {"D360-API-KEY": channel.config[Channel.CONFIG_AUTH_TOKEN], "Content-Type": "application/json"}

        # first set our callbacks
        payload = {"url": "https://" + domain + reverse("courier.d3", args=[channel.uuid, "receive"])}
        resp = requests.post(
            channel.config[Channel.CONFIG_BASE_URL] + "/v1/configs/webhook", json=payload, headers=headers
        )

        if resp.status_code != 200:
            raise ValidationError(_("Unable to register callbacks: %(resp)s"), params={"resp": resp.content})
