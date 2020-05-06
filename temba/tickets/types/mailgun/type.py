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

    name = "Email"
    slug = "mailgun"
    icon = "icon-envelop"

    connect_view = ConnectView
    connect_blurb = _(
        """Connecting a email address will forward all new tickets and their responses to that email address. You will be able to respond to them directly and your replies will be sent to the contact."""
    )

    form_blurb = _(
        """New tickets and replies will be sent to the email address that you configure below. You will need to verify it by entering the token sent to you."""
    )

    def is_available(self):
        return bool(getattr(settings, "MAILGUN_API_KEY", None))
