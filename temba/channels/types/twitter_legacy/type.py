from temba.contacts.models import URN

from ...models import ChannelType


class TwitterLegacyType(ChannelType):
    """
    An legacy style Twitter channel which would have used Mage to receive messages
    """

    code = "TT"
    category = ChannelType.Category.SOCIAL_MEDIA

    name = "Twitter Legacy"

    schemes = [URN.TWITTER_SCHEME, URN.TWITTERID_SCHEME]

    def is_available_to(self, org, user):
        return False, False
