import telegram

from django.urls import reverse
from django.utils.translation import ugettext_lazy as _

from temba.contacts.models import DISCORD_SCHEME

from ...models import ChannelType
from .views import ClaimView


class DiscordType(ChannelType):
    """
    A Telegram bot channel
    """

    code = "DS"
    category = ChannelType.Category.SOCIAL_MEDIA

    courier_url = r"^ds/(?P<uuid>[a-z0-9\-]+)/receive$"

    name = "Discord"
    icon = "icon-exernal"
    show_config_page = False

    #TODO
    claim_blurb = _(
        """A channel type that allows you to use the Discord proxy TODO link"""
    )
    claim_view = ClaimView

    schemes = [DISCORD_SCHEME]
    max_length = 1600
    attachment_support = False # Later this will be True
    free_sending = True

    # TODO
    redact_response_keys = {"first_name", "last_name", "username"}

    def activate(self, channel):
        pass
        # config = channel.config
        # bot = telegram.Bot(config["auth_token"])
        # bot.set_webhook("https://" + channel.callback_domain + reverse("courier.tg", args=[channel.uuid]))

    def deactivate(self, channel):
        pass
        # config = channel.config
        # bot = telegram.Bot(config["auth_token"])
        # bot.delete_webhook()
