from django.utils.translation import ugettext_lazy as _

from temba.contacts.models import URN

from ...models import ChannelType
from .views import ClaimView


class RocketChatType(ChannelType):
    """
    A Rocket.Chat app channel
    """

    CONFIG_BASE_URL = "base_url"
    CONFIG_BOT_USERNAME = "bot_username"
    CONFIG_ADMIN_AUTH_TOKEN = "admin_auth_token"
    CONFIG_ADMIN_USER_ID = "admin_user_id"
    CONFIG_SECRET = "secret"

    code = "RC"
    slug = "rocketchat"
    category = ChannelType.Category.SOCIAL_MEDIA

    courier_url = r"^rc/(?P<uuid>[a-z0-9\-]+)/receive$"

    name = "Rocket.Chat"
    icon = "icon-rocketchat"
    show_config_page = False

    claim_blurb = _("Add a %(link)s bot to send and receive messages to Rocket.Chat users.") % {
        "link": '<a href="https://rocket.chat/">Rocket.Chat</a>'
    }
    claim_view = ClaimView
    schemes = [URN.ROCKETCHAT_SCHEME]

    def is_available_to(self, user):
        return user.is_beta()
