from ...models import TicketerType
from .views import ConnectView


class ZendeskType(TicketerType):
    """
    Type for using Zendesk as a ticketer
    """

    name = "Zendesk"
    slug = "zendesk"

    CONFIG_SUBDOMAIN = "subdomain"
    CONFIG_USERNAME = "username"
    CONFIG_API_TOKEN = "api_token"

    connect_view = ConnectView
    connect_blurb = """<a href="https://www.zendesk.com/">Zendesk</a> is one of the most popular customer service systems around. You can use it to manage all the tickets created on your account."""

    form_blurb = """Enter your credentials below to connect your Zendesk account. You can create a new API Token by visiting the API page in your Zendesk settings."""
