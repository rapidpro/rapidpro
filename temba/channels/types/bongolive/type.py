from django.utils.translation import ugettext_lazy as _

from temba.contacts.models import TEL_SCHEME

from ...models import ChannelType
from .views import ClaimView


class BongoLiveType(ChannelType):
    """
    An Bongo Live channel type (https://www.bongolive.co.tz)
    """

    code = "BL"
    name = "Bongo Live"
    available_timezones = ["Africa/Dar_es_Salaam"]
    category = ChannelType.Category.PHONE

    courier_url = r"^bl/(?P<uuid>[a-z0-9\-]+)/(?P<action>receive|status)$"

    schemes = [TEL_SCHEME]
    max_length = 160
    attachment_support = False

    claim_view = ClaimView
    claim_blurb = _(
        """
        If you have an <a href="https://www.bongolive.co.tz/">Bongo Live</a> number,
        you can quickly connect it using their APIs.
        """
    )

    configuration_blurb = _(
        """
        To finish connecting your channel, you need to have Bongo Live configure the URLs below for your shortcode.
        """
    )

    configuration_urls = (
        dict(
            label=_("Receive URL"),
            url="https://{{channel.callback_domain}}/c/bl/{{channel.uuid}}/receive",
            description=_("This URL should be called by Bongo Live when new messages are received."),
        ),
        dict(
            label=_("DLR callback URL"),
            url="https://{{channel.callback_domain}}/c/bl/{{channel.uuid}}/status",
            description=_(
                "This URL should be called by Bongo Live when the status of an outgoing message is updated."
            ),
        ),
    )
