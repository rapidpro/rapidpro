import telegram

from django.urls import reverse
from django.utils.translation import ugettext_lazy as _

from temba.contacts.models import TELEGRAM_SCHEME

from ...models import ChannelType
from .views import ClaimView


class TelegramType(ChannelType):
    """
    A Telegram bot channel
    """

    code = "TG"
    category = ChannelType.Category.SOCIAL_MEDIA

    courier_url = r"^tg/(?P<uuid>[a-z0-9\-]+)/receive$"

    name = "Telegram"
    icon = "icon-telegram"
    show_config_page = False

    claim_blurb = _(
        """Add a <a href="https://telegram.org">Telegram</a> bot to send and receive messages to Telegram
    users for free. Your users will need an Android, Windows or iOS device and a Telegram account to send and receive
    messages."""
    )
    claim_view = ClaimView

    schemes = [TELEGRAM_SCHEME]
    max_length = 1600
    attachment_support = True
    free_sending = True

    redact_response_keys = {"first_name", "last_name", "username"}

    def activate(self, channel):
        config = channel.config
        bot = telegram.Bot(config["auth_token"])
        bot.set_webhook("https://" + channel.callback_domain + reverse("courier.tg", args=[channel.uuid]))

    def deactivate(self, channel):
        config = channel.config
        bot = telegram.Bot(config["auth_token"])
        bot.delete_webhook()
