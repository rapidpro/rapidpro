from django.utils.translation import gettext_lazy as _

from temba.channels.views import AuthenticatedExternalCallbackClaimView
from temba.contacts.models import URN

from ...models import ChannelType, ConfigUI


class HighConnectionType(ChannelType):
    """
    An High Connection channel (http://www.highconnexion.com/en/)
    """

    code = "HX"
    slug = "high_connection"
    name = "High Connection"
    category = ChannelType.Category.PHONE

    courier_url = r"^hx/(?P<uuid>[a-z0-9\-]+)/(?P<action>status|receive)$"
    schemes = [URN.TEL_SCHEME]
    available_timezones = ["Europe/Paris"]

    claim_blurb = _(
        "If you are based in France, you can purchase a number from %(link)s and connect it in a few simple steps."
    ) % {"link": '<a target="_blank" href="http://www.highconnexion.com/en/">High Connection</a>'}
    claim_view = AuthenticatedExternalCallbackClaimView

    config_ui = ConfigUI(
        blurb=_(
            "To finish configuring this channel, you'll need to notify HighConnection of the following URL for incoming "
            "(MO) messages."
        ),
        endpoints=[
            ConfigUI.Endpoint(courier="receive", label=_("Receive URL")),
        ],
    )
