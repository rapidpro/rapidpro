from django.utils.translation import gettext_lazy as _

from temba.channels.views import AuthenticatedExternalClaimView
from temba.contacts.models import URN

from ...models import ChannelType, ConfigUI


class SMSCentralType(ChannelType):
    """
    An SMSCentral channel (http://smscentral.com.np/)
    """

    code = "SC"
    name = "SMSCentral"
    category = ChannelType.Category.PHONE

    courier_url = r"^sc/(?P<uuid>[a-z0-9\-]+)/(?P<action>receive)$"
    schemes = [URN.TEL_SCHEME]
    available_timezones = ["Asia/Kathmandu"]

    claim_blurb = _("Easily add a two way number you have configured with %(link)s using their APIs.") % {
        "link": '<a target="_blank" href="http://smscentral.com.np/">SMSCentral</a>'
    }
    claim_view = AuthenticatedExternalClaimView

    config_ui = ConfigUI(
        blurb=_("To finish configuring this channel, you'll need to notify SMSCentral of the following URL."),
        endpoints=[
            ConfigUI.Endpoint(
                courier="receive",
                label=_("Inbound URL"),
                help=_("This endpoint should be called by SMSCentral when new messages are received to your number."),
            ),
        ],
    )
