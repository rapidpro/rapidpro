from django.utils.translation import ugettext_lazy as _

from temba.contacts.models import LINE_SCHEME

from ...models import ChannelType
from .views import ClaimView


class LineType(ChannelType):
    """
    A LINE channel (https://line.me/)
    """

    code = "LN"
    category = ChannelType.Category.SOCIAL_MEDIA

    courier_url = r"^ln/(?P<uuid>[a-z0-9\-]+)/receive$"

    name = "LINE"
    icon = "icon-line"

    claim_blurb = _(
        """Add a <a href="https://line.me">LINE</a> bot to send and receive messages to LINE users
                for free. Your users will need an Android, Windows or iOS device and a LINE account to send
                and receive messages."""
    )
    claim_view = ClaimView

    schemes = [LINE_SCHEME]
    max_length = 1600
    attachment_support = False
    free_sending = True

    show_public_addresses = True
