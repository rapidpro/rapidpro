import requests

from django.forms import ValidationError
from django.urls import reverse
from django.utils.translation import gettext_lazy as _

from temba.channels.types.mtn.views import ClaimView
from temba.contacts.models import URN

from ...models import Channel, ChannelType


class MtnType(ChannelType):
    """
    An MTN Developer Portal channel (https://developers.mtn.com/)
    """

    CP_ADDRESS = "cp_address"

    code = "MTN"
    category = ChannelType.Category.PHONE

    beta_only = True

    courier_url = r"^mtn/(?P<uuid>[a-z0-9\-]+)/(?P<action>status|receive)$"

    name = "MTN Developer Portal"

    claim_blurb = _("Easily add a two way number you have configured with %(link)s using their APIs.") % {
        "link": '<a href="https://developers.mtn.com/">MTN Developer Portal</a>'
    }

    claim_view = ClaimView

    schemes = [URN.TEL_SCHEME]
    max_length = 160
    show_config_page = False
    async_activation = False

    def get_token(self, channel):
        base_url = channel.config.get("api_host", "https://api.mtn.com")
        token_url = f"{base_url}/v1/oauth/access_token?grant_type=client_credentials"

        resp = requests.post(
            token_url,
            data={
                "client_id": channel.config.get(Channel.CONFIG_API_KEY),
                "client_secret": channel.config.get(Channel.CONFIG_AUTH_TOKEN),
            },
            headers={"Content-Type": "application/x-www-form-urlencoded", "Accept": "application/json"},
        )
        if int(resp.status_code / 100) != 2:
            raise ValidationError(_("Unable to get token: %s") % resp.content)

        return resp.json()["access_token"]

    def deactivate(self, channel):
        token = self.get_token(channel)

        base_url = channel.config.get("api_host", "https://api.mtn.com")
        mtn_subscription_id = channel.config.get("mtn_subscription_id")

        if mtn_subscription_id is not None:
            delete_subscription_url = (
                f"{base_url}/v2/messages/sms/outbound/{channel.address}/subscription/{mtn_subscription_id}"
            )
            # remove subscription
            resp = requests.delete(delete_subscription_url, headers={"Authorization": f"Bearer {token}"})
            if int(resp.status_code / 100) != 2:
                raise ValidationError(_("Unable to delete subscription callbacks: %s") % resp.content)

    def activate(self, channel):
        token = self.get_token(channel)

        base_url = channel.config.get("api_host", "https://api.mtn.com")
        domain = channel.org.get_brand_domain()

        payload = {
            "notifyUrl": "https://" + domain + reverse("courier.mtn", args=[channel.uuid, "receive"]),
            "targetSystem": domain,
        }

        subscription_url = f"{base_url}/v2/messages/sms/outbound/{channel.address}/subscription"

        response = requests.post(
            subscription_url,
            json=payload,
            headers={"Content-Type": "application/json", "Authorization": f"Bearer {token}"},
        )

        if int(response.status_code / 100) != 2:
            raise ValidationError(_("Unable to add subscription callbacks: %s") % response.content)

        channel.config["mtn_subscription_id"] = response.json()["data"]["id"]
        channel.save()
