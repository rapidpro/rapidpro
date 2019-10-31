from temba.contacts.models import TWITTER_SCHEME, TWITTERID_SCHEME

from ...models import ChannelType


class TwitterLegacyType(ChannelType):
    """
    An legacy style Twitter channel which would have used Mage to receive messages
    """

    code = "TT"
    category = ChannelType.Category.SOCIAL_MEDIA

    name = "Twitter Legacy"
    icon = "icon-twitter"

    schemes = [TWITTER_SCHEME, TWITTERID_SCHEME]
    max_length = 10000
    show_config_page = False
    free_sending = True
    quick_reply_text_size = 36

    def is_available_to(self, user):
        return False
