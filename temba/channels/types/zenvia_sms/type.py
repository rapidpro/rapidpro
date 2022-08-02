import requests

from django.forms import ValidationError
from django.urls import reverse
from django.utils.translation import gettext_lazy as _

from temba.channels.types.zenvia_whatsapp.views import ClaimView
from temba.contacts.models import URN

from ...models import Channel, ChannelType

ZENVIA_MESSAGE_SUBSCRIPTION_ID = "zenvia_message_subscription_id"
ZENVIA_STATUS_SUBSCRIPTION_ID = "zenvia_status_subscription_id"


class ZenviaSMSType(ChannelType):
    """
    An Zenvia SMS channel
    """

    code = "ZVS"
    category = ChannelType.Category.PHONE

    courier_url = r"^zvs/(?P<uuid>[a-z0-9\-]+)/(?P<action>receive|status)$"

    name = "Zenvia SMS"

    claim_blurb = _("If you have a %(link)s number, you can connect it to communicate with your contacts.") % {
        "link": '<a href="https://www.zenvia.com/">Zenvia SMS</a>'
    }

    claim_view = ClaimView

    schemes = [URN.TEL_SCHEME]
    max_length = 1600

    def update_webhook(self, channel, url, event_type):
        headers = {
            "X-API-TOKEN": channel.config[Channel.CONFIG_API_KEY],
            "Content-Type": "application/json",
        }

        conf_url = "https://api.zenvia.com/v2/subscriptions"

        # set our webhook
        payload = {
            "eventType": event_type,
            "webhook": {"url": url, "headers": {}},
            "status": "ACTIVE",
            "version": "v2",
            "criteria": {"channel": "sms"},
        }
        if event_type == "MESSAGE":
            payload["criteria"]["direction"] = "IN"

        resp = requests.post(conf_url, json=payload, headers=headers)

        if resp.status_code != 200:
            raise ValidationError(
                _("Unable to register webhook subscriptions: %(resp)s"), params={"resp": resp.content}
            )

        return resp.json()["id"]

    def deactivate(self, channel):
        headers = {
            "X-API-TOKEN": channel.config[Channel.CONFIG_API_KEY],
            "Content-Type": "application/json",
        }

        subscription_ids = [
            channel.config.get(ZENVIA_MESSAGE_SUBSCRIPTION_ID),
            channel.config.get(ZENVIA_STATUS_SUBSCRIPTION_ID),
        ]

        errored = False

        for subscription_id in subscription_ids:
            if not subscription_id:  # pragma: needs cover
                continue

            conf_url = f"https://api.zenvia.com/v2/subscriptions/{subscription_id}"
            resp = requests.delete(conf_url, headers=headers)

            if resp.status_code != 204:
                errored = True

        if errored:
            raise ValidationError(_("Unable to remove webhook subscriptions: %(resp)s"), params={"resp": resp.content})

    def activate(self, channel):
        domain = channel.org.get_brand_domain()

        receive_url = "https://" + domain + reverse("courier.zvs", args=[channel.uuid, "receive"])
        messageSubscriptionId = self.update_webhook(channel, receive_url, "MESSAGE")

        channel.config[ZENVIA_MESSAGE_SUBSCRIPTION_ID] = messageSubscriptionId

        status_url = "https://" + domain + reverse("courier.zvs", args=[channel.uuid, "status"])
        statusSubscriptionId = self.update_webhook(channel, status_url, "MESSAGE_STATUS")

        channel.config[ZENVIA_STATUS_SUBSCRIPTION_ID] = statusSubscriptionId

        channel.save()
