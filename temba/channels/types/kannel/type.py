from django.utils.translation import ugettext_lazy as _

from temba.channels.types.kannel.views import ClaimView
from temba.contacts.models import TEL_SCHEME

from ...models import ChannelType


class KannelType(ChannelType):
    """
    An Kannel channel (http://www.kannel.org/)
    """

    code = "KN"
    category = ChannelType.Category.PHONE

    courier_url = r"^kn/(?P<uuid>[a-z0-9\-]+)/(?P<action>status|receive)$"

    name = "Kannel"
    icon = "icon-channel-kannel"

    claim_blurb = _(
        """Connect your <a href="http://www.kannel.org/" target="_blank">Kannel</a> instance, we'll walk you through
                       the steps necessary to get your SMSC connection working in a few minutes."""
    )
    claim_view = ClaimView

    schemes = [TEL_SCHEME]
    max_length = 1600

    attachment_support = False
