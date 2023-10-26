from django.utils.translation import gettext_lazy as _

from temba.channels.types.globe.views import ClaimView
from temba.contacts.models import URN

from ...models import ChannelType, ConfigUI


class GlobeType(ChannelType):
    """
    A Globe Labs channel
    """

    code = "GL"
    name = "Globe Labs"
    category = ChannelType.Category.PHONE

    courier_url = r"^gl/(?P<uuid>[a-z0-9\-]+)/(?P<action>receive)$"
    schemes = [URN.TEL_SCHEME]
    available_timezones = ["Asia/Manila"]

    claim_blurb = _(
        "If you are based in the Phillipines, you can integrate {{ branding.name }} with Globe Labs to send and "
        "receive messages on your short code."
    )
    claim_view = ClaimView

    config_ui = ConfigUI(
        blurb=_(
            "To finish configuring this channel, you'll need to set the following notify URI for SMS on your "
            "application configuration page."
        ),
        endpoints=[
            ConfigUI.Endpoint(courier="receive", label=_("Notify URI")),
        ],
    )

    def is_recommended_to(self, org, user):
        return self.is_available_to(org, user)[0]
