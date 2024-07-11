import requests

from django.urls import reverse
from django.utils.translation import gettext_lazy as _

from temba.contacts.models import URN

from ...models import ChannelType
from .views import ClaimView


class TelegramType(ChannelType):
    """
    A Telegram bot channel
    """

    code = "TG"
    name = "Telegram"
    category = ChannelType.Category.SOCIAL_MEDIA

    courier_url = r"^tg/(?P<uuid>[a-z0-9\-]+)/receive$"
    schemes = [URN.TELEGRAM_SCHEME]
    redact_response_keys = {"first_name", "last_name", "username"}

    claim_blurb = _(
        "Add a %(link)s bot to send and receive messages to Telegram users for free. Your users will need an Android, "
        "Windows or iOS device and a Telegram account to send and receive messages."
    ) % {"link": '<a target="_blank" href="https://telegram.org">Telegram</a>'}
    claim_view = ClaimView

    def is_recommended_to(self, org, user):
        return True  # because it's super simpler to setup, free, and works everywhere

    def activate(self, channel):
        config = channel.config

        response = requests.post(
            f"https://api.telegram.org/bot{config['auth_token']}/setWebhook",
            data={"url": "https://" + channel.callback_domain + reverse("courier.tg", args=[channel.uuid])},
        )
        response.raise_for_status()

    def deactivate(self, channel):
        config = channel.config

        response = requests.post(f"https://api.telegram.org/bot{config['auth_token']}/deleteWebhook")
        response.raise_for_status()

    def get_error_ref_url(self, channel, code: str) -> str:
        return "https://core.telegram.org/api/errors"
