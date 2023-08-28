from django.utils.translation import gettext_lazy as _

from temba.contacts.models import URN

from ...models import ChannelType, ConfigUI
from .views import SUPPORTED_TIMEZONES, ClaimView


class MessageBirdType(ChannelType):
    """
    An MessageBird channel
    """

    code = "MBD"
    name = "Messagebird"
    category = ChannelType.Category.PHONE
    beta_only = True

    courier_url = r"^mbd/(?P<uuid>[a-z0-9\-]+)/(?P<action>receive|status)$"
    schemes = [URN.TEL_SCHEME]
    available_timezones = SUPPORTED_TIMEZONES

    claim_blurb = _("Connect your approved %(link)s channel") % {
        "link": '<a target="_blank" href="https://www.messagebird.com/">Messagebird</a>'
    }
    claim_view = ClaimView

    config_ui = ConfigUI(
        blurb=_(
            "To finish configuring this channel, you'll have to configure the Messagebird to send raw received SMS messages to "
            "the URL below either with a flow or by registering the webhook with them. Configure the status URL under "
            "Developer Settings to receive status updates for your messages."
        ),
        endpoints=[
            ConfigUI.Endpoint(
                courier="receive",
                label=_("Receive URL"),
                help=_("Webhook address for inbound messages to this address."),
            ),
            ConfigUI.Endpoint(
                courier="status",
                label=_("Status URL"),
                help=_("Webhook address for message status calls to this address."),
            ),
        ],
    )
