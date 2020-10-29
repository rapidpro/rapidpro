import re

from django.urls import reverse
from django.utils.translation import ugettext_lazy as _

from temba.contacts.models import ROCKETCHAT_SCHEME

from ...models import ChannelType
from .views import ClaimView

RE_HOST = re.compile(r"(?:(?P<scheme>https?)://)?(?P<domain>[^ \"'/]+)")


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

    claim_blurb = _("Add a %(link)s bot to send and receive messages to Rocket.Chat users. ") % {
        "link":"<a href="https://rocket.chat/">Rocket.Chat</a>"
        }
    claim_view = ClaimView
    schemes = [ROCKETCHAT_SCHEME]

    @staticmethod
    def callback_url(channel, domain=None):
        if not domain:
            domain = RE_HOST.search(channel.org.get_brand_domain() or "")
            domain = domain and domain.group() or ""
        search = RE_HOST.search(domain)
        if not search:
            raise ValueError("Could not identify the hostname.")
        scheme, domain = search.groups()
        return f"{scheme or 'https'}://{domain}{reverse('courier.rc', args=[channel.uuid])}"
