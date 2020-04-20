from ...models import TicketingServiceType
from .views import ConnectView


class EmailType(TicketingServiceType):
    """
    Type for using email as a ticketing service
    """

    CONFIG_EMAIL_ADDRESS = "email"
    CONFIG_VERIFICATION_TOKEN = "verification_token"

    name = "Email"
    slug = "email"

    connect_view = ConnectView
    connect_blurb = """
        Connecting an email address will forward all new tickets and their responses to that email address. You will be
        able to respond to them directly and your replies will be sent to the contact.
        """

    form_blurb = """
        You will need to verify your email address by entering the token sent to you.
        """
