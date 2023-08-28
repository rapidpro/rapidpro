from django.utils.translation import gettext_lazy as _

from temba.channels.types.macrokiosk.views import ClaimView
from temba.contacts.models import URN

from ...models import ChannelType, ConfigUI


class MacrokioskType(ChannelType):
    """
    An Macrokiok channel (http://www.macrokiosk.com/)
    """

    CONFIG_SENDER_ID = "macrokiosk_sender_id"
    CONFIG_SERVICE_ID = "macrokiosk_service_id"

    code = "MK"
    name = "Macrokiosk"
    category = ChannelType.Category.PHONE

    courier_url = r"^mk/(?P<uuid>[a-z0-9\-]+)/(?P<action>status|receive)$"
    schemes = [URN.TEL_SCHEME]
    available_timezones = ["Asia/Kuala_Lumpur"]

    claim_blurb = _("Easily add a two way number you have configured with %(link)s using their APIs.") % {
        "link": '<a target="_blank" href="http://macrokiosk.com/">Macrokiosk</a>'
    }
    claim_view = ClaimView

    config_ui = ConfigUI(
        blurb=_("To finish configuring this channel, you'll need to notify MACROKIOSK of the following URLs."),
        endpoints=[
            ConfigUI.Endpoint(
                courier="receive",
                label=_("Inbound URL"),
                help=_("This endpoint should be called by MACROKIOSK when new messages are received to your number."),
            ),
            ConfigUI.Endpoint(
                courier="status",
                label=_("DLR URL"),
                help=_(
                    "This endpoint should be called by MACROKIOSK when the message status changes. (delivery reports)"
                ),
            ),
        ],
    )
