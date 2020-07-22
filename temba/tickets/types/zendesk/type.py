from django.conf import settings
from django.conf.urls import url
from django.utils.translation import ugettext_lazy as _

from ...models import TicketerType
from .views import AdminUIView, ConfigureView, ConnectView, ManifestView


class ZendeskType(TicketerType):
    """
    Type for using Zendesk as a ticketer
    """

    CONFIG_SUBDOMAIN = "subdomain"
    CONFIG_OAUTH_TOKEN = "oauth_token"
    CONFIG_SECRET = "secret"
    CONFIG_PUSH_ID = "push_id"
    CONFIG_PUSH_TOKEN = "push_token"

    name = "Zendesk"
    slug = "zendesk"
    icon = "icon-zendesk"

    connect_view = ConnectView
    connect_blurb = _(
        '<a href="https://www.zendesk.com/">Zendesk</a> is one of the most popular customer service systems around. '
        "You can use it to manage all the tickets created on your account."
    )

    def is_available(self):
        return bool(settings.ZENDESK_CLIENT_ID and settings.ZENDESK_CLIENT_SECRET)

    def get_urls(self):
        """
        Returns all the URLs this ticketer exposes to Django, the URL should be relative.
        """
        return [
            self.get_connect_url(),
            url(r"^manifest\.json", ManifestView.as_view(), name="manifest"),
            url(r"^admin_ui", AdminUIView.as_view(), name="admin_ui"),
            url(r"^configure/(?P<uuid>[a-z0-9\-]+)/$", ConfigureView.as_view(), name="configure"),
        ]
