from django.utils.translation import gettext_lazy as _

from temba.channels.models import ChannelType, ConfigUI
from temba.channels.types.kaleyra.views import ClaimView
from temba.contacts.models import URN

CONFIG_ACCOUNT_SID = "account_sid"
CONFIG_API_KEY = "api_key"


class KaleyraType(ChannelType):
    """
    A Kaleyra Channel Type
    """

    code = "KWA"
    name = "Kaleyra WhatsApp"
    category = ChannelType.Category.SOCIAL_MEDIA

    courier_url = r"^kwa/(?P<uuid>[a-z0-9\-]+)/(?P<action>receive|status)$"
    schemes = [URN.WHATSAPP_SCHEME]

    claim_blurb = _(
        """Activate your own enterprise WhatsApp account in Kaleyra to communicate with your contacts. <a target="_blank" href="https://www.kaleyra.com/whatsapp/">Learn more about Kaleyra WhatsApp</a>"""
    )
    claim_view = ClaimView

    config_ui = ConfigUI(
        blurb=_(
            "To finish configuring this channel, you'll need to set the following callback URL on your Kaleyra "
            "account."
        ),
        endpoints=[
            ConfigUI.Endpoint(
                courier="receive",
                label=_("Receive URL"),
                help=_("To receive incoming messages, you need to set the receive URL for your Kaleyra account."),
            ),
        ],
    )
