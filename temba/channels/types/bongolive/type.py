from django.utils.translation import gettext_lazy as _

from temba.contacts.models import URN

from ...models import ChannelType, ConfigUI
from .views import ClaimView


class BongoLiveType(ChannelType):
    """
    An Bongo Live channel type (https://www.bongolive.co.tz)
    """

    code = "BL"
    name = "Bongo Live"
    category = ChannelType.Category.PHONE

    courier_url = r"^bl/(?P<uuid>[a-z0-9\-]+)/receive$"
    schemes = [URN.TEL_SCHEME]
    available_timezones = ["Africa/Dar_es_Salaam"]

    claim_view = ClaimView
    claim_blurb = _("If you have an %(link)s number, you can quickly connect it using their APIs.") % {
        "link": '<a target="_blank" href="https://www.bongolive.co.tz/">Bongo Live</a>'
    }

    config_ui = ConfigUI(
        blurb=_("To finish configuring this channel, you'll need Bongo Live to use the following callback URL."),
        endpoints=[
            ConfigUI.Endpoint(
                courier="receive",
                label=_("Receive URL"),
                help=_(
                    "This URL should be called by Bongo Live when new messages are received or to report DLR status."
                ),
            ),
        ],
    )
