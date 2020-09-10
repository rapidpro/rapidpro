from ...models import ChannelType

from .views import ClaimView
from django.utils.translation import ugettext_lazy as _


class RocketChatType(ChannelType):
    """
    A Rocket.Chat app channel
    """

    code = "RC"
    category = ChannelType.Category.SOCIAL_MEDIA

    courier_url = r"^rc/(?P<uuid>[a-z0-9\-]+)/receive$"

    name = "Rocket.Chat"
    icon = "icon-rocket-chat"

    claim_blurb = _("Some nice text here")
    claim_view = ClaimView