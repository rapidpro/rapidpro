from temba.contacts.models import URN

from ...models import ChannelType


class ChikkaType(ChannelType):
    """
    No longer exists, see https://en.wikipedia.org/wiki/Chikka
    """

    code = "CK"
    category = ChannelType.Category.PHONE

    name = "Chikka"
    schemes = [URN.TEL_SCHEME]

    def is_available_to(self, org, user):
        return False, False
