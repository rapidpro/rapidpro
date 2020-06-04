from django.conf import settings
from django.utils.translation import ugettext_lazy as _

from ...models import TicketerType
from .views import ConnectView


class MailgunType(TicketerType):
    """
    Type for using mailgun as an email-based ticketer
    """

    CONFIG_DOMAIN = "domain"
    CONFIG_API_KEY = "api_key"
    CONFIG_TO_ADDRESS = "to_address"
    CONFIG_BRAND_NAME = "brand_name"
    CONFIG_URL_BASE = "url_base"

    name = "Email"
    slug = "mailgun"
    icon = "icon-email-tickets"

    connect_view = ConnectView
    connect_blurb = _(
        "Connecting an email address will forward all new tickets and their responses to that email address. "
        "You will be able to respond to them directly and your replies will be sent to the contact."
    )

    def is_available(self):
        return bool(settings.MAILGUN_API_KEY)
