from django.utils.translation import gettext_lazy as _

from temba.contacts.models import URN

from ...models import ChannelType
from .views import ClaimView


class DiscordType(ChannelType):
    """
    A Discord bot channel, powered by the standalone Rapidpro-Discord-Proxy
    """

    code = "DS"
    category = ChannelType.Category.SOCIAL_MEDIA

    courier_url = r"^ds/(?P<uuid>[a-z0-9\-]+)/receive$"

    name = "Discord"
    icon = "icon-discord"
    show_config_page = False

    claim_blurb = _(
        "Add a %(link)s bot to send messages to Discord users for free. "
        "Your users will need a Discord account and a compatible device to send/recieve messages. "
        "This channel type is only available if your instance has been "
        "configured with the Discord proxy application, available %(proxy_link)s."
        % {
            "link": '<a href="https://discord.com/">Discord</a>',
            "proxy_link": '<a href="https://github.com/releaseplatform/RapidPro-Discord-Proxy">here</a>',
        }
    )
    claim_view = ClaimView

    schemes = [URN.DISCORD_SCHEME]
    max_length = 1600
    attachment_support = True
    free_sending = True

    redact_response_keys = {"first_name", "last_name", "username"}

    def activate(self, channel):
        pass

    def deactivate(self, channel):
        pass
