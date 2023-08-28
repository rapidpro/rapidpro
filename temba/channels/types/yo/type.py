from django.utils.translation import gettext_lazy as _

from temba.channels.types.yo.views import ClaimView
from temba.contacts.models import URN

from ...models import ChannelType, ConfigUI


class YoType(ChannelType):
    """
    An Yo! channel (http://www.yo.co.ug/)
    """

    code = "YO"
    name = "YO!"
    category = ChannelType.Category.PHONE

    courier_url = r"^yo/(?P<uuid>[a-z0-9\-]+)/(?P<action>receive)$"
    schemes = [URN.TEL_SCHEME]
    available_timezones = ["Africa/Kampala"]

    claim_view = ClaimView
    claim_blurb = _(
        "If you are based in Uganda, you can integrate with %(link)s to send and receive messages on your short code."
    ) % {"link": '<a target="_blank" href="http://www.yo.co.ug/">Yo!</a>'}

    config_ui = ConfigUI(
        blurb=_("To finish configuring this channel, you'll need to notify Yo! of the following inbound SMS URL."),
        endpoints=[
            ConfigUI.Endpoint(
                courier="receive",
                label=_("Inbound SMS URL"),
                help=_(
                    "This URL should be called with a GET by Yo! when new incoming "
                    "messages are received on your short code."
                ),
            ),
        ],
    )

    def is_recommended_to(self, org, user):
        return self.is_available_to(org, user)[0]
