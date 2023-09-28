from django.urls import re_path

from ...models import TicketerType
from .views import AdminUIView, ConfigureView, FileCallbackView, ManifestView


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

    def get_urls(self):
        """
        Returns all the URLs this ticketer exposes to Django, the URL should be relative.
        """
        return [
            re_path(r"^manifest\.json", ManifestView.as_view(), name="manifest"),
            re_path(r"^admin_ui", AdminUIView.as_view(), name="admin_ui"),
            re_path(r"^configure/(?P<uuid>[a-z0-9\-]+)/$", ConfigureView.as_view(), name="configure"),
            re_path(r"^file/(?P<path>[\w\-./]+)$", FileCallbackView.as_view(), name="file_callback"),
        ]
