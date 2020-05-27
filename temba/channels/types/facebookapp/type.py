from django.utils.translation import ugettext_lazy as _

from temba.contacts.models import FACEBOOK_SCHEME

from ...models import ChannelType
from .views import ClaimView


class FacebookappType(ChannelType):
    """
    A Facebook channel
    """

    code = "FBA"
    category = ChannelType.Category.SOCIAL_MEDIA

    courier_url = r"^fba/receive"

    name = "Facebook"
    icon = "icon-facebook-official"

    claim_blurb = _(
        """Add a <a href="http://facebook.com">Facebook</a> bot to send and receive messages on behalf
    of one of your Facebook pages for free. You will need to connect your page by logging into your facebook and chekcing the Facebook page to connect"""
    )
    claim_view = ClaimView

    schemes = [FACEBOOK_SCHEME]
    max_length = 320
    attachment_support = True
    free_sending = True

    def is_available_to(self, user):
        return False
