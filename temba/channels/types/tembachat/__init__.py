from temba.contacts.models import URN

from ...models import ChannelType


class TembaChatType(ChannelType):
    """
    A Temba Chat webchat channel
    """

    code = "TWC"
    name = "Temba Chat"
    category = ChannelType.Category.SOCIAL_MEDIA

    courier_url = r"^twc/(?P<uuid>[a-z0-9\-]+)/(?P<action>receive)$"
    schemes = [URN.WEBCHAT_SCHEME]

    def get_urls(self):
        return []

    def is_available_to(self, org, user):
        return False, False

    def is_recommended_to(self, org, user):
        return False
