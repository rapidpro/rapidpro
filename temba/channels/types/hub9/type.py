from django.utils.translation import gettext_lazy as _

from temba.channels.types.dartmedia.views import ClaimView
from temba.contacts.models import URN

from ...models import ChannelType, ConfigUI


class Hub9Type(ChannelType):
    """
    An DartMedia channel (http://dartmedia.biz/)
    """

    code = "H9"
    name = "Hub9"
    category = ChannelType.Category.PHONE

    courier_url = r"^h9/(?P<uuid>[a-z0-9\-]+)/(?P<action>sent|delivered|failed|receive|received)$"
    schemes = [URN.TEL_SCHEME, URN.EXTERNAL_SCHEME]
    available_timezones = ["Asia/Jakarta"]

    claim_blurb = _("Easily add a two way number you have configured with Hub9 in Indonesia.")
    claim_view = ClaimView

    config_ui = ConfigUI(
        blurb=_("To finish configuring this channel, you'll need to provide them with the following details."),
        endpoints=[
            ConfigUI.Endpoint(
                courier="receive",
                label=_("Received URL"),
                help=_(
                    "This endpoint should be called by Hub9 when new messages are received to your number. "
                    "You can set the receive URL on your Hub9 account by contacting your sales agent."
                ),
            ),
            ConfigUI.Endpoint(
                courier="delivered",
                label=_("Delivered URL"),
                help=_(
                    "This endpoint should be called by Hub9 when a message has been to the final recipient. "
                    "(delivery reports) You can set the delivery callback URL on your Hub9 account by contacting your "
                    "sales agent."
                ),
            ),
        ],
        show_public_ips=True,
    )

    def is_recommended_to(self, org, user):
        return self.is_available_to(org, user)[0]
