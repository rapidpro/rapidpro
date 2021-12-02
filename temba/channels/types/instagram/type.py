import requests

from django.conf.urls import url
from django.utils.translation import ugettext_lazy as _

from temba.contacts.models import URN

from ...models import Channel, ChannelType
from .views import ClaimView, RefreshToken


class InstagramType(ChannelType):
    """
    A Instagram channel
    """

    extra_links = [dict(name=_("Reconnect Facebook Page"), link="channels.types.facebookapp.refresh_token")]

    code = "IG"
    category = ChannelType.Category.SOCIAL_MEDIA

    courier_url = r"^ig/receive"

    name = "Instagram"
    icon = "icon-facebook-official"

    show_config_page = False

    claim_blurb = _(
        "Add a %(link)s bot to send and receive messages on behalf of one of your business Instagram acounts for free. You will "
        "need to connect your page by logging into your Facebook and checking the Facebook page to connect. "
        "Your page must be linked to a business Instagram account, see how %(link1)s."
        "On the Facebook page, navigate Settings > Page roles and verify you have an admin page role on the page."
    ) % {
        "link": '<a href="http://instagram.com">Instagram</a>',
        "link1": '<a href="https://help.instagram.com/399237934150902"> here </a>',
    }
    claim_view = ClaimView

    schemes = [URN.INSTAGRAM_SCHEME]
    max_length = 2000
    attachment_support = True
    free_sending = True

    def get_urls(self):
        return [
            self.get_claim_url(),
            url(r"^(?P<uuid>[a-z0-9\-]+)/refresh_token$", RefreshToken.as_view(), name="refresh_token"),
        ]

    def deactivate(self, channel):
        config = channel.config
        requests.delete(
            f"https://graph.facebook.com/v12.0/{channel.address}/subscribed_apps",
            params={"access_token": config[Channel.CONFIG_AUTH_TOKEN]},
        )
