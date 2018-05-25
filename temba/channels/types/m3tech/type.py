from django.utils.translation import ugettext_lazy as _

from temba.channels.views import AuthenticatedExternalClaimView
from temba.contacts.models import TEL_SCHEME

from ...models import ChannelType


class M3TechType(ChannelType):
    """
    An M3 Tech channel (http://m3techservice.com)
    """

    code = "M3"
    category = ChannelType.Category.PHONE

    courier_url = r"^m3/(?P<uuid>[a-z0-9\-]+)/(?P<action>sent|delivered|failed|received|receive)$"

    name = "M3 Tech"

    claim_blurb = _(
        """Easily add a two way number you have configured with <a href="http://m3techservice.com">M3 Tech</a> using their APIs."""
    )
    claim_view = AuthenticatedExternalClaimView

    schemes = [TEL_SCHEME]
    max_length = 160
    attachment_support = False

    configuration_blurb = _(
        """
        To finish configuring your connection you'll need to notify M3Tech of the following callback URLs:
        """
    )

    configuration_urls = (
        dict(
            label=_("Received URL"),
            url="https://{{ channel.callback_domain }}{% url 'courier.m3' channel.uuid 'receive' %}",
        ),
        dict(
            label=_("Sent URL"), url="https://{{ channel.callback_domain }}{% url 'courier.m3' channel.uuid 'sent' %}"
        ),
        dict(
            label=_("Delivered URL"),
            url="https://{{ channel.callback_domain }}{% url 'courier.m3' channel.uuid 'delivered' %}",
        ),
        dict(
            label=_("Failed URL"),
            url="https://{{ channel.callback_domain }}{% url 'courier.m3' channel.uuid 'failed' %}",
        ),
    )

    def is_available_to(self, user):
        org = user.get_org()
        return org.timezone and str(org.timezone) in ["Asia/Karachi"]
