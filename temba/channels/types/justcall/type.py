import requests

from django.forms import ValidationError
from django.urls import reverse
from django.utils.translation import gettext_lazy as _

from temba.contacts.models import URN

from ...models import Channel, ChannelType
from .views import ClaimView


class JustCallType(ChannelType):
    """
    A JustCall channel (https://justcall.io/)
    """

    code = "JCL"
    name = "JustCall"
    category = ChannelType.Category.PHONE

    courier_url = r"^jcl/(?P<uuid>[a-z0-9\-]+)/(?P<action>receive|status)$"
    schemes = [URN.TEL_SCHEME]

    claim_view = ClaimView
    claim_blurb = _("If you have a %(link)s number, you can quickly connect it using their APIs.") % {
        "link": '<a target="_blank" href="https://justcall.io/">JustCall</a>'
    }

    def activate(self, channel):
        api_key = channel.config[Channel.CONFIG_API_KEY]
        api_secret = channel.config[Channel.CONFIG_SECRET]

        domain = channel.org.get_brand_domain()

        receive_url = "https://" + domain + reverse("courier.jcl", args=[channel.uuid, "receive"])
        status_url = "https://" + domain + reverse("courier.jcl", args=[channel.uuid, "status"])

        resp = requests.post(
            "https://api.justcall.io/v1/webhooks/add",
            json={"topic_id": 2, "url": receive_url},
            headers={"Authorization": f"{api_key}:{api_secret}", "Accept": "application/json"},
        )

        if resp.status_code != 200:
            raise ValidationError(
                _("Unable to add webhook to JustCall: %(resp)s"), params={"resp": resp.json().get("message")}
            )

        resp = requests.post(
            "https://api.justcall.io/v1/webhooks/add",
            json={"topic_id": 9, "url": status_url},
            headers={"Authorization": f"{api_key}:{api_secret}", "Accept": "application/json"},
        )

        if resp.status_code != 200:
            raise ValidationError(
                _("Unable to add webhook to JustCall: %(resp)s"), params={"resp": resp.json().get("message")}
            )
