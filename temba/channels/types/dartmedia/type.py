from django.utils.translation import gettext_lazy as _

from temba.channels.types.dartmedia.views import ClaimView
from temba.contacts.models import URN

from ...models import ChannelType, ConfigUI


class DartMediaType(ChannelType):
    """
    An DartMedia channel (http://dartmedia.biz/)
    """

    code = "DA"
    category = ChannelType.Category.PHONE

    courier_url = r"^da/(?P<uuid>[a-z0-9\-]+)/(?P<action>delivered|received|receive)$"

    name = "DartMedia"

    claim_blurb = _("Easily add a two way number you have configured with %(link)s in Indonesia.") % {
        "link": '<a target="_blank" href="http://dartmedia.biz/">Dart Media</a>'
    }
    claim_view = ClaimView

    schemes = [URN.TEL_SCHEME, URN.EXTERNAL_SCHEME]
    max_length = 160

    show_public_addresses = True

    configuration_blurb = _(
        "To finish configuring your Dart Media connection you'll need to provide them with the following details."
    )
    config_ui = ConfigUI(
        endpoints=[
            ConfigUI.Endpoint(
                courier="receive",
                label=_("Received URL"),
                help=_(
                    "This endpoint should be called by Dart Media when new messages are received to your number. "
                    "You can set the receive URL on your Dart Media account by contacting your sales agent."
                ),
            ),
            ConfigUI.Endpoint(
                courier="delivered",
                label=_("Delivered URL"),
                help=_(
                    "This endpoint should be called by Dart Media when a message has been to the final recipient. "
                    "(delivery reports) You can set the delivery callback URL on your Dart Media account by "
                    "contacting your sales agent."
                ),
            ),
        ]
    )

    available_timezones = ["Asia/Jakarta"]

    def is_recommended_to(self, org, user):
        return self.is_available_to(org, user)[0]
