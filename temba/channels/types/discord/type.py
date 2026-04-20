from django.utils.translation import gettext_lazy as _

from temba.contacts.models import URN

from ...models import ChannelType
from .views import ClaimView


class DiscordType(ChannelType):
    """
    A Discord bot channel, powered by the standalone Rapidpro-Discord-Proxy
    """

    code = "DS"
    name = "Discord"
    category = ChannelType.Category.SOCIAL_MEDIA

    courier_url = r"^ds/(?P<uuid>[a-z0-9\-]+)/receive$"
    schemes = [URN.DISCORD_SCHEME]
    redact_response_keys = {"first_name", "last_name", "username"}

    claim_blurb = _(
        "Add a %(link)s bot to send messages to Discord users for free. "
        "Your users will need a Discord account and a compatible device to send/recieve messages. "
        "This channel type is only available if your instance has been "
        "configured with the Discord proxy application, available %(proxy_link)s."
        % {
            "link": '<a target="_blank" href="https://discord.com/">Discord</a>',
            "proxy_link": '<a target="_blank" href="https://github.com/releaseplatform/RapidPro-Discord-Proxy">here</a>',
        }
    )
    claim_view = ClaimView
