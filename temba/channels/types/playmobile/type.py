from django.utils.translation import gettext_lazy as _

from temba.channels.models import ChannelType, ConfigUI
from temba.channels.types.playmobile.views import ClaimView
from temba.contacts.models import URN


class PlayMobileType(ChannelType):
    """
    A Play Mobile channel (http://playmobile.uz/)
    """

    code = "PM"
    name = "Play Mobile"
    category = ChannelType.Category.PHONE

    courier_url = r"^pm/(?P<uuid>[a-z0-9\-]+)/(?P<action>receive)$"
    schemes = [URN.TEL_SCHEME]
    available_timezones = ["Asia/Tashkent", "Asia/Samarkand"]

    claim_blurb = _(
        "If you are based in Uzbekistan, you can purchase a short code from %(link)s and connect it in a few simple "
        "steps."
    ) % {"link": '<a href="http://playmobile.uz/">Play Mobile</a>'}
    claim_view = ClaimView

    config_ui = ConfigUI(
        blurb=_("To finish configuring this channel, you'll need to notify Play Mobile of the following URL."),
        endpoints=[
            ConfigUI.Endpoint(
                courier="receive",
                label=_("Receive URL"),
                help=_("To receive incoming messages, you need to set the receive URL for your Play Mobile account."),
            ),
        ],
    )
