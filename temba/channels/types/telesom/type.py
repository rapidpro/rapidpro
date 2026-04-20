from django.utils.translation import gettext_lazy as _

from temba.channels.types.telesom.views import ClaimView
from temba.contacts.models import URN

from ...models import ChannelType, ConfigUI


class TelesomType(ChannelType):
    """
    An Telesom channel
    """

    code = "TS"
    name = "Telesom"
    category = ChannelType.Category.PHONE

    courier_url = r"^ts/(?P<uuid>[a-z0-9\-]+)/(?P<action>receive)$"
    schemes = [URN.TEL_SCHEME]
    available_timezones = ["Africa/Mogadishu"]

    claim_blurb = _(
        "If you are based in Somalia, you can integrate with Telesom to send and receive messages on your short code."
    )
    claim_view = ClaimView

    config_ui = ConfigUI(
        blurb=_(
            "TTo finish configuring this channel, you'll need to provide Telesom with the following delivery URL "
            "for incoming messages."
        ),
        endpoints=[
            ConfigUI.Endpoint(courier="receive", label=_("Delivery URL")),
        ],
    )

    def is_recommended_to(self, org, user):
        return self.is_available_to(org, user)[0]
