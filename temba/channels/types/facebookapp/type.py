import requests

from django.conf.urls import url
from django.utils.translation import ugettext_lazy as _

from temba.contacts.models import FACEBOOK_SCHEME

from ...models import Channel, ChannelType
from .views import ClaimView, RefreshToken


class FacebookAppType(ChannelType):
    """
    A Facebook channel
    """

    extra_links = [dict(link=_("Reconnect Facebook Page"), name="channels.types.facebookapp.refresh_token")]

    code = "FBA"
    category = ChannelType.Category.SOCIAL_MEDIA

    courier_url = r"^fba/receive"

    name = "Facebook"
    icon = "icon-facebook-official"

    show_config_page = False

    claim_blurb = _(
        """Add a <a href="http://facebook.com">Facebook</a> bot to send and receive messages on behalf
    of one of your Facebook pages for free. You will need to connect your page by logging into your Facebook and checking the Facebook page to connect"""
    )
    claim_view = ClaimView

    schemes = [FACEBOOK_SCHEME]
    max_length = 2000
    attachment_support = True
    free_sending = True

    def get_urls(self):
        return [
            self.get_claim_url(),
            url(r"^(?P<uuid>[a-z0-9\-]+)/refresh_token$", RefreshToken.as_view(), name="refresh_token"),
        ]

    def is_available_to(self, user):
        return False

    def deactivate(self, channel):
        config = channel.config
        requests.delete(
            f"https://graph.facebook.com/v7.0/{channel.address}/subscribed_apps",
            params={"access_token": config[Channel.CONFIG_AUTH_TOKEN]},
        )
