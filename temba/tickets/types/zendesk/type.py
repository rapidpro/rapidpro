from ...models import TicketingServiceType
from .views import ConnectView


class ZendeskType(TicketingServiceType):
    """
    Type for using Zendesk as a ticketing service
    """

    name = "Zendesk"
    slug = "zendesk"

    CONFIG_USERNAME = "username"
    CONFIG_API_TOKEN = "api_token"
    CONFIG_SUBDOMAIN = "subdomain"

    connect_view = ConnectView
    connect_blurb = """
        <a href="https://www.zendesk.com/">Zendesk</a> is one of the most popular ticketing systems around. You can
        use it to manage all the tickets created on your account.
        """

    form_blurb = """
        Enter your credentials below to connect your ZenDesk account. You can create a new API Token by visiting the API
        page in your ZenDesk settings.
        """
