from django.utils.translation import ugettext_lazy as _

from temba.channels.types.dartmedia.views import ClaimView
from temba.contacts.models import URN

from ...models import ChannelType


class Hub9Type(ChannelType):
    """
    An DartMedia channel (http://dartmedia.biz/)
    """

    code = "H9"
    category = ChannelType.Category.PHONE

    courier_url = r"^h9/(?P<uuid>[a-z0-9\-]+)/(?P<action>sent|delivered|failed|receive|received)$"

    name = "Hub9"

    claim_blurb = _("Easily add a two way number you have configured with Hub9 in Indonesia.")
    claim_view = ClaimView

    schemes = [URN.TEL_SCHEME, URN.EXTERNAL_SCHEME]
    max_length = 1600
    attachment_support = False

    show_public_addresses = True

    configuration_blurb = _(
        "To finish configuring your Hub9 connection you'll need to provide them with the following details."
    )

    configuration_urls = (
        dict(
            label=_("Received URL"),
            url="https://{{ channel.callback_domain }}{% url 'courier.h9' channel.uuid 'receive' %}",
            description=_(
                "This endpoint should be called by Hub9 when new messages are received to your number. "
                "You can set the receive URL on your Hub9 account by contacting your sales agent."
            ),
        ),
        dict(
            label=_("Delivered URL"),
            url="https://{{ channel.callback_domain }}{% url 'courier.h9' channel.uuid 'delivered' %}",
            description=_(
                "This endpoint should be called by Hub9 when a message has been to the final recipient. "
                "(delivery reports) You can set the delivery callback URL on your Hub9 account by contacting your "
                "sales agent."
            ),
        ),
    )

    def is_available_to(self, user):
        org = user.get_org()
        return org.timezone and str(org.timezone) in ["Asia/Jakarta"]

    def is_recommended_to(self, user):
        return self.is_available_to(user)
