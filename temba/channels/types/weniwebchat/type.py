

from django.utils.translation import ugettext_lazy as _

from temba.contacts.models import URN

from ...models import ChannelType
from .views import ClaimView

CONFIG_BASE_URL = "base_url"


class WeniWebChatType(ChannelType):
    """
    A Weni Web Chat channel
    """

    code = "WWC"
    category = ChannelType.Category.API

    courier_url = r"^wwc/(?P<uuid>[a-z0-9\-]+)/receive"

    name = "Weni Web Chat"
    icon = "icon-weniwebchat"
    show_config_page = False

    claim_blurb = _(
        "With Weni Web Chat, you can integrate your Rapidpro organization as a chat on your website."
    )
    claim_view = ClaimView

    schemes = [URN.WENIWEBCHAT_SCHEME]
    max_length = 320
    attachment_support = True
    free_sending = True
