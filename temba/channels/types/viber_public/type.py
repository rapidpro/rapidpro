import requests

from django.urls import reverse
from django.utils.translation import gettext_lazy as _

from temba.contacts.models import URN

from ...models import ChannelType
from .views import ClaimView, UpdateForm


class ViberPublicType(ChannelType):
    """
    A Viber public account channel (https://www.viber.com/public-accounts/)
    """

    code = "VP"
    category = ChannelType.Category.SOCIAL_MEDIA

    courier_url = r"^vp/(?P<uuid>[a-z0-9\-]+)/receive$"

    name = "Viber"
    icon = "icon-viber"

    schemes = [URN.VIBER_SCHEME]
    max_length = 7000
    free_sending = True
    quick_reply_text_size = 36

    claim_view = ClaimView

    update_form = UpdateForm

    claim_blurb = _(
        "Connect a %(link)s public channel to send and receive messages to Viber users for free. Your users will need "
        "an Android, Windows or iOS device and a Viber account to send and receive messages."
    ) % {"link": '<a href="http://viber.com/en/">Viber</a>'}

    configuration_blurb = _("Your Viber channel is connected. If needed the webhook endpoints are listed below.")

    configuration_urls = (
        dict(label=_("Webhook URL"), url="https://{{ channel.callback_domain }}{% url 'courier.vp' channel.uuid %}"),
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
