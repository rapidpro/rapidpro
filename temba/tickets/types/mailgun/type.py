from django.conf import settings

from ...models import TicketerType


class MailgunType(TicketerType):
    """
    Type for using mailgun as an email-based ticketer
    """

    CONFIG_DOMAIN = "domain"
    CONFIG_API_KEY = "api_key"
    CONFIG_TO_ADDRESS = "to_address"

    name = "Email"
    slug = "mailgun"
    icon = "icon-envelop"

    def is_available(self):
        return bool(settings.MAILGUN_API_KEY)
