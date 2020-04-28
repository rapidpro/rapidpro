from ...models import TicketServiceType
from .views import ConnectView


class MailgunType(TicketServiceType):
    """
    Type for using mailgun as an email-based ticket service
    """

    CONFIG_DOMAIN = "domain"
    CONFIG_API_KEY = "api_key"
    CONFIG_TO_ADDRESS = "to_address"

    name = "Mailgun"
    slug = "mailgun"

    connect_view = ConnectView
    connect_blurb = """Connecting a mailgun managed email address will forward all new tickets and their responses to that email address. You will be able to respond to them directly and your replies will be sent to the contact."""

    form_blurb = """You will need to verify your email address by entering the token sent to you."""
