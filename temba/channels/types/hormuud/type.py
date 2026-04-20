from django.utils.translation import gettext_lazy as _

from temba.channels.views import AuthenticatedExternalCallbackClaimView
from temba.contacts.models import URN

from ...models import ChannelType, ConfigUI


class HormuudType(ChannelType):
    """
    A Hormuud channel (https://www.hormuud.com/)
    """

    code = "HM"
    name = "Hormuud"
    category = ChannelType.Category.PHONE

    courier_url = r"^hm/(?P<uuid>[a-z0-9\-]+)/(?P<action>status|receive)$"
    schemes = [URN.TEL_SCHEME]
    available_timezones = ["Africa/Mogadishu"]

    claim_blurb = _(
        "If you are based in Somalia, you can get a number from %(link)s and connect it in a few simple steps."
    ) % {"link": '<a target="_blank" href="http://www.hormuud.com/">Hormuud</a>'}
    claim_view = AuthenticatedExternalCallbackClaimView

    config_ui = ConfigUI(
        blurb=_(
            "To finish configuring this channel, you'll need to notify Hormuud of the following URL for incoming "
            "(MO) messages."
        ),
        endpoints=[
            ConfigUI.Endpoint(courier="receive", label=_("Receive URL")),
        ],
    )
