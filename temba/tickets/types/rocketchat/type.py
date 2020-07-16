from django.utils.translation import ugettext_lazy as _

from temba.tickets.models import TicketerType
from temba.tickets.types.rocketchat.views import ConnectView


class RocketChatType(TicketerType):
    """
    Type for using RocketChat as a ticketer
    """

    CONFIG_DOMAIN = "domain"
    CONFIG_APP_ID = "app_id"
    CONFIG_SECRET = "secret"

    name = "RocketChat"
    slug = "rocketchat"
    # icon = ""

    connect_view = ConnectView
    connect_blurb = _(
        '<a href="https://rocket.chat/">RocketChat</a> is the ultimate free open source solution for team '
        "communications. Its omnichannel feature allows you to integrate RocketChat as a ticket service."
    )
