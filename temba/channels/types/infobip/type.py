from django.utils.translation import gettext_lazy as _

from temba.channels.views import AuthenticatedExternalCallbackClaimView
from temba.contacts.models import URN

from ...models import ChannelType, ConfigUI


class InfobipType(ChannelType):
    """
    An Infobip channel (https://www.infobip.com/)
    """

    code = "IB"
    name = "Infobip"
    category = ChannelType.Category.PHONE

    courier_url = r"^ib/(?P<uuid>[a-z0-9\-]+)/(?P<action>delivered|receive)$"
    schemes = [URN.TEL_SCHEME]

    claim_blurb = _("Easily add a two way number you have configured with %(link)s using their APIs.") % {
        "link": '<a target="_blank" href="http://infobip.com">Infobip</a>'
    }
    claim_view = AuthenticatedExternalCallbackClaimView

    config_ui = ConfigUI(
        blurb=_(
            "To finish configuring this channel, you'll need to set the following callback URLs on the Infobip "
            "website under your account."
        ),
        endpoints=[
            ConfigUI.Endpoint(
                courier="receive",
                label=_("Received URL"),
                help=_(
                    "This endpoint should be called with a POST by Infobip when new messages are received to your number. "
                    "You can set the receive URL on your Infobip account by contacting your sales agent."
                ),
            ),
            ConfigUI.Endpoint(
                courier="delivered",
                label=_("Delivered URL"),
                help=_(
                    "This endpoint should be called with a POST by Infobip when a message has been to the final recipient. "
                    "(delivery reports) You can set the delivery callback URL on your Infobip account by contacting your "
                    "sales agent."
                ),
            ),
        ],
    )
