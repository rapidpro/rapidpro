from django.utils.translation import ugettext_lazy as _

from temba.contacts.models import DISCORD_SCHEME

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
    icon = "icon-exernal"
    show_config_page = False

    # TODO
    claim_blurb = _(
        """A channel type that allows you to use the Discord proxy TODO link"""
    )
    claim_view = ClaimView

    schemes = [DISCORD_SCHEME]
    max_length = 1600
    attachment_support = True
    free_sending = True

    # TODO
    redact_response_keys = {"first_name", "last_name", "username"}

    def activate(self, channel):
        pass

    def deactivate(self, channel):
        pass
