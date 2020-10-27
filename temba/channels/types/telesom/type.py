from django.utils.translation import ugettext_lazy as _

from temba.channels.types.telesom.views import ClaimView
from temba.contacts.models import URN

from ...models import ChannelType


class TelesomType(ChannelType):
    """
    An Telesom channel
    """

    code = "TS"
    category = ChannelType.Category.PHONE

    courier_url = r"^ts/(?P<uuid>[a-z0-9\-]+)/(?P<action>receive)$"

    name = "Telesom"

    claim_blurb = _(
        "If you are based in Somalia, you can integrate with Telesom to send and receive messages on your shortcode."
    )
    claim_view = ClaimView

    schemes = [URN.TEL_SCHEME]
    max_length = 160
    attachment_support = False

    configuration_blurb = _(
        "To finish configuring your Telesom connection you'll need to provide Telesom with the following delivery URL "
        "for incoming messages to {{ channel.address }}."
    )

    configuration_urls = (
        dict(label="", url="https://{{ channel.callback_domain }}{% url 'courier.ts' channel.uuid 'receive' %}"),
    )

    def is_available_to(self, user):
        org = user.get_org()
        return org.timezone and str(org.timezone) in ["Africa/Mogadishu"]

    def is_recommended_to(self, user):
        return self.is_available_to(user)
