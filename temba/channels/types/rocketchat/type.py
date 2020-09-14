from django.urls import reverse
from ...models import ChannelType

from .views import ClaimView
from django.utils.translation import ugettext_lazy as _
import re

RE_HOST = re.compile(r"(?:(?P<scheme>https?)://)?(?P<domain>[^ \"'/]+)")


class RocketChatType(ChannelType):
    """
    A Rocket.Chat app channel
    """

    CONFIG_BASE_URL = "base_url"
    CONFIG_BOT_USERNAME = "bot_username"
    CONFIG_SECRET = "secret"

    code = "RC"
    slug = "rocketchat"
    category = ChannelType.Category.SOCIAL_MEDIA

    courier_url = r"^rc/(?P<uuid>[a-z0-9\-]+)/receive$"

    name = "Rocket.Chat"
    icon = "icon-rocket-chat"

    claim_blurb = _(
        """Add a <a href="https://rocket.chat/">Rocket.Chat</a> bot to send and receive messages to Rocket.Chat users for free. """
    )
    claim_view = ClaimView

    @staticmethod
    def callback_url(channel, domain=None):
        if not domain:
            domain = RE_HOST.search(channel.org.get_brand_domain() or "")
            domain = domain and domain.group() or ""
        search = RE_HOST.search(domain)
        if not search:
            raise ValueError("Could not identify the hostname.")
        scheme, domain = search.groups()
        return f"{scheme or 'https'}://{domain}/{reverse('courier.rc', args=[channel.uuid])}"
