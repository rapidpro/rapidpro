from django.utils.translation import gettext_lazy as _

from temba.channels.types.shaqodoon.views import ClaimView
from temba.contacts.models import URN

from ...models import ChannelType, ConfigUI


class ShaqodoonType(ChannelType):
    """
    An Shaqodoon channel
    """

    code = "SQ"
    name = "Shaqodoon"
    category = ChannelType.Category.PHONE

    courier_url = r"^sq/(?P<uuid>[a-z0-9\-]+)/(?P<action>sent|delivered|failed|received|receive)$"
    schemes = [URN.TEL_SCHEME]
    available_timezones = ["Africa/Mogadishu"]

    claim_blurb = _(
        "If you are based in Somalia, you can integrate with Shaqodoon to send and receive messages on your short code."
    )
    claim_view = ClaimView

    config_ui = ConfigUI(
        blurb=_(
            "To finish configuring this channel, you'll need to provide Shaqodoon with the following delivery URL."
        ),
        endpoints=[
            ConfigUI.Endpoint(courier="receive", label=_("Receive URL")),
        ],
    )

    def is_recommended_to(self, org, user):
        return self.is_available_to(org, user)[0]
