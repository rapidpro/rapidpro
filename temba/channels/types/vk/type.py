from django.utils.translation import ugettext_lazy as _

from temba.contacts.models import VK_SCHEME

from ...models import ChannelType
from .views import ClaimView


class VKType(ChannelType):
    """
    A VK channel
    """

    code = "VK"
    category = ChannelType.Category.SOCIAL_MEDIA

    courier_url = r"^vk/(?P<uuid>[a-z0-9\-]+)/receive"

    name = "VK"
    icon = "icon-vk"

    claim_blurb = _(
        """Add a VK bot to send and receive messages on behalf of a VK community
        for free. You will need to create an access token for your community first.
        """
    )
    claim_view = ClaimView

    schemes = [VK_SCHEME]
    max_length = 320
    attachment_support = True
    free_sending = True
