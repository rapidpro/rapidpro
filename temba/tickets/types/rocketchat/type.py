from django.utils.translation import ugettext_lazy as _

from temba.tickets.models import TicketerType
from temba.tickets.types.rocketchat.views import ConnectView


class RocketChatType(TicketerType):
    """
    Type for using RocketChat as a ticketer
    """

    CONFIG_BASE_URL = "base_url"
    CONFIG_SECRET = "secret"
    CONFIG_ADMIN_AUTH_TOKEN = "admin_auth_token"
    CONFIG_ADMIN_USER_ID = "admin_user_id"

    name = "Rocket.Chat"
    slug = "rocketchat"
    icon = "icon-rocketchat"

    connect_view = ConnectView
    connect_blurb = _(
        "%(link)s is a free open source solution for team communications which can be connected as a ticket service"
        "through its omnichannel feature."
    ) % {"link": '<a href="https://rocket.chat/">Rocket.Chat</a>'}

    def is_available_to(self, user):
        return user.is_beta()
