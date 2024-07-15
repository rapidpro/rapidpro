import requests

from django.forms import ValidationError
from django.urls import reverse
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from temba.channels.models import Channel
from temba.channels.types.dialog360.views import ClaimView
from temba.contacts.models import URN
from temba.request_logs.models import HTTPLog

from ...models import ChannelType, ConfigUI


class Dialog360Type(ChannelType):
    """
    A 360 Dialog Channel Type
    """

    code = "D3C"
    name = "360Dialog WhatsApp"
    category = ChannelType.Category.SOCIAL_MEDIA

    unique_addresses = True

    courier_url = r"^d3c/(?P<uuid>[a-z0-9\-]+)/(?P<action>receive)$"
    schemes = [URN.WHATSAPP_SCHEME]
    template_type = "whatsapp"

    claim_blurb = _(
        "Activate your own enterprise WhatsApp account in %(link)s (Cloud) to communicate with your contacts. "
    ) % {"link": '<a target="_blank" href="https://www.360dialog.com/">360Dialog</a>'}
    claim_view = ClaimView

    config_ui = ConfigUI()  # has own template

    def get_headers(self, channel):
        return {"D360-API-KEY": channel.config[Channel.CONFIG_AUTH_TOKEN], "Content-Type": "application/json"}

    def activate(self, channel):
        domain = channel.org.get_brand_domain()

        # first set our callbacks
        payload = {"url": "https://" + domain + reverse("courier.d3c", args=[channel.uuid, "receive"])}
        resp = requests.post(
            channel.config[Channel.CONFIG_BASE_URL] + "/v1/configs/webhook",
            json=payload,
            headers=self.get_headers(channel),
        )

        if resp.status_code != 200:
            raise ValidationError(_("Unable to register callbacks: %(resp)s"), params={"resp": resp.content})

    def fetch_templates(self, channel) -> list:
        templates_url = "%s/v1/configs/templates" % channel.config[Channel.CONFIG_BASE_URL]
        start = timezone.now()
        try:
            response = requests.get(templates_url, headers=self.get_headers(channel))
            response.raise_for_status()
            HTTPLog.from_response(HTTPLog.WHATSAPP_TEMPLATES_SYNCED, response, start, timezone.now(), channel=channel)
        except requests.RequestException as e:
            HTTPLog.from_exception(HTTPLog.WHATSAPP_TEMPLATES_SYNCED, e, start, channel=channel)
            raise e

        return response.json()["waba_templates"]

    def get_redact_values(self, channel) -> tuple:
        return (channel.config[Channel.CONFIG_AUTH_TOKEN],)
