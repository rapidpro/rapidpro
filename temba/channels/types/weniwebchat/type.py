

from django.utils.translation import ugettext_lazy as _

from temba.contacts.models import URN

from ...models import ChannelType
from .views import ClaimView

CONFIG_SOCKET_URL = "socket_url"


class WeniWebChatType(ChannelType):
    """
    A Weni Web Chat channel
    """

    code = "WWN"
    category = ChannelType.Category.API

    CONFIG_COMMUNITY_NAME = "community_name"

    courier_url = r"^wwn/(?P<uuid>[a-z0-9\-]+)/receive"

    name = "Weni Web Chat"
    # icon = "icon-vk"
    show_config_page = False

    claim_blurb = _(
        "Lorem ipsum dolor sit amet, consectetur adipiscing elit. Aliquam semper nulla et arcu malesuada,"
        "id porttitor mi scelerisque. In diam diam, lacinia ut massa quis, viverra volutpat urna." 
        "Sed mollis quam id tristique commodo."
    )
    claim_view = ClaimView

    schemes = [URN.WENIWEBCHAT_SCHEME]
    max_length = 320
    # attachment_support = True
    free_sending = True
