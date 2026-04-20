from django.utils.translation import gettext_lazy as _

from temba.channels.views import AuthenticatedExternalClaimView
from temba.contacts.models import URN

from ...models import ChannelType, ConfigUI


class StartType(ChannelType):
    """
    An Start Mobile channel (https://bulk.startmobile.ua/)
    """

    code = "ST"
    name = "Start Mobile"
    category = ChannelType.Category.PHONE

    courier_url = r"^st/(?P<uuid>[a-z0-9\-]+)/(?P<action>receive)$"
    schemes = [URN.TEL_SCHEME]
    available_timezones = ["Europe/Kiev"]

    claim_blurb = _("Easily add a two way number you have configured with %(link)s using their APIs.") % {
        "link": '<a target="_blank" href="https://bulk.startmobile.ua/">Start Mobile</a>'
    }
    claim_view = AuthenticatedExternalClaimView

    config_ui = ConfigUI(
        blurb=_("To finish configuring this channel, you'll need to notify Start of the following receiving URL."),
        endpoints=[
            ConfigUI.Endpoint(
                courier="receive",
                label=_("Inbound URL"),
                help=_("This endpoint should be called by when new messages are received to your number."),
            ),
        ],
    )
