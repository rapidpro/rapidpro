from django.utils.translation import ugettext_lazy as _

from temba.channels.types.globe.views import ClaimView
from temba.contacts.models import URN

from ...models import ChannelType


class GlobeType(ChannelType):
    """
    A Globe Labs channel
    """

    code = "GL"
    category = ChannelType.Category.PHONE

    courier_url = r"^gl/(?P<uuid>[a-z0-9\-]+)/(?P<action>receive)$"

    name = "Globe Labs"

    claim_blurb = _(
        "If you are based in the Phillipines, you can integrate {{ brand.name }} with Globe Labs to send and "
        "receive messages on your shortcode."
    )
    claim_view = ClaimView

    schemes = [URN.TEL_SCHEME]
    max_length = 160
    attachment_support = False

    configuration_blurb = _(
        "To finish configuring your Globe Labs connection you'll need to set the following notify URI for SMS on your "
        "application configuration page."
    )

    configuration_urls = (
        dict(
            label=_("Notify URI"),
            url="https://{{ channel.callback_domain }}{% url 'courier.gl' channel.uuid 'receive' %}",
        ),
    )

    def is_available_to(self, user):
        org = user.get_org()
        return org.timezone and str(org.timezone) in ["Asia/Manila"]

    def is_recommended_to(self, user):
        return self.is_available_to(user)
