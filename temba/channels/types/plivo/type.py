import requests

from django.urls import re_path
from django.utils.translation import gettext_lazy as _

from temba.channels.models import ChannelType
from temba.contacts.models import URN
from temba.utils.http import http_headers

from .views import ClaimView, Connect, SearchView


class PlivoType(ChannelType):
    """
    An Plivo channel (https://www.plivo.com/)
    """

    CONFIG_AUTH_ID = "PLIVO_AUTH_ID"
    CONFIG_AUTH_TOKEN = "PLIVO_AUTH_TOKEN"
    CONFIG_APP_ID = "PLIVO_APP_ID"

    code = "PL"
    name = "Plivo"
    category = ChannelType.Category.PHONE

    unique_addresses = True

    courier_url = r"^pl/(?P<uuid>[a-z0-9\-]+)/(?P<action>status|receive)$"
    schemes = [URN.TEL_SCHEME]

    claim_blurb = _("Easily add a two way number you have configured with %(link)s using their APIs.") % {
        "link": '<a href="https://www.plivo.com/">Plivo</a>'
    }
    claim_view = ClaimView

    def deactivate(self, channel):
        config = channel.config
        requests.delete(
            "https://api.plivo.com/v1/Account/%s/Application/%s/"
            % (config[self.CONFIG_AUTH_ID], config[self.CONFIG_APP_ID]),
            auth=(config[self.CONFIG_AUTH_ID], config[self.CONFIG_AUTH_TOKEN]),
            headers=http_headers(extra={"Content-Type": "application/json"}),
        )

    def get_urls(self):
        return [
            self.get_claim_url(),
            re_path(r"^search/$", SearchView.as_view(channel_type=self), name="search"),
            re_path(r"^connect/$", Connect.as_view(channel_type=self), name="connect"),
        ]
