from django.utils.translation import gettext_lazy as _

from temba.channels.types.clickmobile.views import ClaimView
from temba.contacts.models import URN

from ...models import ChannelType, ConfigUI


class ClickMobileType(ChannelType):
    """
    A ClickMobile Channel Type https://www.click-mobile.com/
    """

    code = "CM"
    name = "Click Mobile"
    category = ChannelType.Category.PHONE

    courier_url = r"^cm/(?P<uuid>[a-z0-9\-]+)/(?P<action>receive)$"
    schemes = [URN.TEL_SCHEME]
    available_timezones = ["Africa/Accra", "Africa/Blantyre"]

    claim_blurb = _(
        "If you are based in Malawi or Ghana you can purchase a number from %(link)s and connect it in a few simple "
        "steps."
    ) % {"link": '<a target="_blank" href="https://www.click-mobile.com/">Click Mobile</a>'}

    claim_view = ClaimView

    config_ui = ConfigUI(
        blurb=_(
            "To finish configuring this channel, you need to configure Click Mobile to send new messages to the URL below."
        ),
        endpoints=[
            ConfigUI.Endpoint(courier="receive", label=_("Receive URL")),
        ],
    )
