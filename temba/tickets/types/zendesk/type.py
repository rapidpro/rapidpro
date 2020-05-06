from django.utils.translation import ugettext_lazy as _

from ...models import TicketerType
from .views import ConnectView


class ZendeskType(TicketerType):
    """
    Type for using Zendesk as a ticketer
    """

    CONFIG_SUBDOMAIN = "subdomain"
    CONFIG_OAUTH_TOKEN = "oauth_token"

    name = "Zendesk"
    slug = "zendesk"
    icon = "icon-zendesk"

    connect_view = ConnectView
    connect_blurb = _(
        """<a href="https://www.zendesk.com/">Zendesk</a> is one of the most popular customer service systems around. You can use it to manage all the tickets created on your account."""
    )

    form_blurb = _(
        """Enter your Zendesk subdomain. You will be redirected to Zendesk where you need to grant access to this application."""
    )
