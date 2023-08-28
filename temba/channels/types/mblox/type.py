from django.utils.translation import gettext_lazy as _

from temba.channels.views import AuthenticatedExternalClaimView
from temba.contacts.models import URN

from ...models import ChannelType, ConfigUI


class MbloxType(ChannelType):
    """
    A Mblox channel (https://www.mblox.com/)
    """

    code = "MB"
    name = "Mblox"
    category = ChannelType.Category.PHONE

    courier_url = r"^mb/(?P<uuid>[a-z0-9\-]+)/(?P<action>receive)$"
    schemes = [URN.TEL_SCHEME]

    claim_blurb = _("Easily add a two way number you have configured with %(link)s using their APIs.") % {
        "link": '<a target="_blank" href="https://www.mblox.com/">Mblox</a>'
    }
    claim_view = AuthenticatedExternalClaimView

    config_ui = ConfigUI(
        blurb=_(
            "To finish configuring this channel, you'll need to set the following callback URL on your Mblox account."
        ),
        endpoints=[
            ConfigUI.Endpoint(
                courier="receive",
                label=_("Callback URL"),
                help=_(
                    "This endpoint will be called by Mblox when new messages are received to your number and for delivery "
                    "reports."
                ),
            ),
        ],
    )
