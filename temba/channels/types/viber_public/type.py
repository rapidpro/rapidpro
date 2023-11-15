import requests

from django.urls import reverse
from django.utils.translation import gettext_lazy as _

from temba.contacts.models import URN

from ...models import ChannelType, ConfigUI
from .views import ClaimView, UpdateForm


class ViberPublicType(ChannelType):
    """
    A Viber public account channel (https://www.viber.com/public-accounts/)
    """

    code = "VP"
    name = "Viber"
    category = ChannelType.Category.SOCIAL_MEDIA

    unique_addresses = True

    courier_url = r"^vp/(?P<uuid>[a-z0-9\-]+)/receive$"
    schemes = [URN.VIBER_SCHEME]

    update_form = UpdateForm

    claim_view = ClaimView
    claim_blurb = _(
        "Connect a %(link)s public channel to send and receive messages to Viber users for free. Your users will need "
        "an Android, Windows or iOS device and a Viber account to send and receive messages."
    ) % {"link": '<a target="_blank" href="http://viber.com/en/">Viber</a>'}

    config_ui = ConfigUI(
        blurb=_("Your Viber channel is connected. If needed the webhook endpoints are listed below."),
        endpoints=[
            ConfigUI.Endpoint(courier="receive", label=_("Webhook URL")),
        ],
    )

    def activate(self, channel):
        auth_token = channel.config["auth_token"]
        handler_url = "https://" + channel.callback_domain + reverse("courier.vp", args=[channel.uuid])

        requests.post(
            "https://chatapi.viber.com/pa/set_webhook",
            json={
                "auth_token": auth_token,
                "url": handler_url,
                "event_types": ["delivered", "failed", "conversation_started"],
            },
        )

    def deactivate(self, channel):
        auth_token = channel.config["auth_token"]
        requests.post("https://chatapi.viber.com/pa/set_webhook", json={"auth_token": auth_token, "url": ""})

    def get_error_ref_url(self, channel, code: str) -> str:
        return "https://developers.viber.com/docs/api/rest-bot-api/#error-codes"
