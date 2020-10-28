from django.utils.translation import ugettext_lazy as _

from temba.channels.types.shaqodoon.views import ClaimView
from temba.contacts.models import URN

from ...models import ChannelType


class ShaqodoonType(ChannelType):
    """
    An Shaqodoon channel
    """

    code = "SQ"
    category = ChannelType.Category.PHONE

    courier_url = r"^sq/(?P<uuid>[a-z0-9\-]+)/(?P<action>sent|delivered|failed|received|receive)$"

    name = "Shaqodoon"

    claim_blurb = _(
        "If you are based in Somalia, you can integrate with Shaqodoon to send and receive messages on your shortcode."
    )
    claim_view = ClaimView

    schemes = [URN.TEL_SCHEME]
    max_length = 1600
    attachment_support = False

    configuration_blurb = _(
        "To finish configuring your Shaqodoon connection you'll need to provide Shaqodoon with the following delivery "
        "URL for incoming messages to {{ channel.address }}."
    )

    configuration_urls = (
        dict(label="", url="https://{{ channel.callback_domain }}{% url 'courier.sq' channel.uuid 'receive' %}"),
    )

    def is_available_to(self, user):
        org = user.get_org()
        return org.timezone and str(org.timezone) in ["Africa/Mogadishu"]

    def is_recommended_to(self, user):
        return self.is_available_to(user)
