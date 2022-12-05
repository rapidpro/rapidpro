from django.utils.translation import gettext_lazy as _

from temba.contacts.models import URN

from ...models import ChannelType
from .views import ClaimView


class BandwidthType(ChannelType):
    """
    An Bandwidth channel type (https://www.bandwidth.com/)
    """

    code = "BW"
    name = "Bandwidth"
    category = ChannelType.Category.PHONE

    beta_only = True

    courier_url = r"^bw/(?P<uuid>[a-z0-9\-]+)/(?P<action>receive|status)$"

    schemes = [URN.TEL_SCHEME]
    max_length = 2048

    claim_view = ClaimView
    claim_blurb = _("If you have an %(link)s number, you can quickly connect it using their APIs.") % {
        "link": '<a href="https://www.bandwidth.com/">Bandwidth</a>'
    }

    configuration_blurb = _(
        "To finish configuring your Bandwidth connection you need to set the following URLs in your "
        "Bandwidth account settings."
    )

    configuration_urls = (
        dict(
            label=_("Inbound Message Webhook URL"),
            url="https://{{ channel.callback_domain }}{% url 'courier.bw' channel.uuid 'receive' %}",
        ),
        dict(
            label=_("Outbound Message Webhook URL"),
            url="https://{{ channel.callback_domain }}{% url 'courier.bw' channel.uuid 'status' %}",
        ),
    )
