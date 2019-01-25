from temba.channels.models import ChannelType
from temba.contacts.models import TEL_SCHEME


class JunebugUSSDType(ChannelType):
    """
    A Junebug USSD channel
    """

    code = "JNU"
    category = ChannelType.Category.USSD

    name = "Junebug USSD"
    slug = "junebug_ussd"
    icon = "icon-junebug"

    schemes = [TEL_SCHEME]
    max_length = 1600

    def is_available_to(self, user):
        return False
