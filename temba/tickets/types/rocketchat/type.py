import re

from django.utils.translation import ugettext_lazy as _

from temba.tickets.models import TicketerType
from temba.tickets.types.rocketchat.views import ConnectView


RE_HOST = re.compile(r"(?:(?P<scheme>https?)://)?(?P<domain>[^ \"'/]+)")
CALLBACK_URL_TEMPLATE = "{host}/mr/tickets/types/rocketchat/{uuid}/event"


class RocketChatType(TicketerType):
    """
    Type for using RocketChat as a ticketer
    """

    CONFIG_BASE_URL = "base_url"
    CONFIG_SECRET = "secret"

    name = "RocketChat"
    slug = "rocketchat"
    # icon = ""

    connect_view = ConnectView
    connect_blurb = _(
        '<a href="https://rocket.chat/">RocketChat</a> is the ultimate free open source solution for team '
        "communications. Its omnichannel feature allows you to integrate RocketChat as a ticket service."
    )

    @staticmethod
    def callback_url(ticketer, domain=None):
        if not domain:
            domain = RE_HOST.search(ticketer.org.get_brand_domain()).group()
        scheme, domain = RE_HOST.search(domain).groups()
        if not domain:
            raise ValueError("Cannot to identify the hostname.")
        return CALLBACK_URL_TEMPLATE.format(host=f"{scheme or 'https'}://{domain}", uuid=ticketer.uuid)
