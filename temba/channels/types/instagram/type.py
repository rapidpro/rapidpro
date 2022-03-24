import requests

from django.urls import re_path
from django.utils.translation import gettext_lazy as _

from temba.contacts.models import URN

from ...models import Channel, ChannelType
from .views import ClaimView, RefreshToken


class InstagramType(ChannelType):
    """
    A Instagram channel
    """

    extra_links = [
        dict(
            name=_("Reconnect Instagram Business Account"),
            link="channels.types.instagram.refresh_token",
        )
    ]

    code = "IG"
    category = ChannelType.Category.SOCIAL_MEDIA

    courier_url = r"^ig/receive"

    name = "Instagram"
    icon = "icon-instagram"

    beta_only = True

    show_config_page = False

    claim_blurb = _("Add an %(link)s bot to send and receive messages on behalf of a business Instagram account.") % {
        "link": '<a href="http://instagram.com">Instagram</a>',
    }
    claim_view = ClaimView

    schemes = [URN.INSTAGRAM_SCHEME]
    max_length = 2000
    attachment_support = True
    free_sending = True

    def get_urls(self):
        return [
            self.get_claim_url(),
            re_path(
                r"^(?P<uuid>[a-z0-9\-]+)/refresh_token$",
                RefreshToken.as_view(),
                name="refresh_token",
            ),
        ]

    def deactivate(self, channel):
        config = channel.config
        requests.delete(
            f"https://graph.facebook.com/v12.0/{channel.address}/subscribed_apps",
            params={"access_token": config[Channel.CONFIG_AUTH_TOKEN]},
        )
