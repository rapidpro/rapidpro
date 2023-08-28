from django.utils.translation import gettext_lazy as _

from temba.channels.types.dmark.views import ClaimView
from temba.contacts.models import URN

from ...models import ChannelType, ConfigUI


class DMarkType(ChannelType):
    """
    A DMark Channel Type http://smsapi1.dmarkmobile.com/
    """

    code = "DK"
    name = "DMark"
    category = ChannelType.Category.PHONE

    courier_url = r"^dk/(?P<uuid>[a-z0-9\-]+)/(?P<action>receive|status)$"
    schemes = [URN.TEL_SCHEME]
    available_timezones = ["Africa/Kampala", "Africa/Kinshasa"]

    claim_blurb = _(
        "If you are based in Uganda or DRC you can purchase a short code from %(link)s and connect it in a few simple "
        "steps."
    ) % {"link": '<a target="_blank" href="http://dmarkmobile.com/">DMark Mobile</a>'}
    claim_view = ClaimView

    config_ui = ConfigUI(
        blurb=_("To finish configuring this channel, you need to set DMark to send MO messages to the URL below."),
        endpoints=[
            ConfigUI.Endpoint(courier="receive", label=_("Receive URL")),
        ],
    )
