from django.utils.translation import gettext_lazy as _

from temba.channels.models import ChannelType, ConfigUI
from temba.channels.types.verboice.views import ClaimView
from temba.contacts.models import URN


class VerboiceType(ChannelType):
    code = "VB"
    name = "Verboice"
    category = ChannelType.Category.PHONE

    courier_url = r"^vb/(?P<uuid>[a-z0-9\-]+)/(?P<action>status|receive)$"
    schemes = [URN.TEL_SCHEME]

    claim_blurb = _(
        "Use a %(link)s connection to leverage in-country SIP connections for building voice (IVR) flows."
    ) % {"link": '<a target="_blank" href="http://verboice.instedd.org">Verboice</a>'}
    claim_view = ClaimView

    config_ui = ConfigUI(
        blurb=_(
            "To finish configuring your connection you'll need to set the following status callback URL for your Verboice "
            "project"
        ),
        endpoints=[
            ConfigUI.Endpoint(courier="status", label=_("Status Callback URL")),
        ],
    )

    def is_available_to(self, org, user):
        return False, False
