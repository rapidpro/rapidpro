from temba.contacts.models import TEL_SCHEME

from ...models import ChannelType
from ...views import UpdateAndroidForm


class AndroidType(ChannelType):
    """
    An Android relayer app channel type
    """

    code = "A"
    slug = "android"

    name = "Android"
    icon = "icon-channel-android"

    schemes = [TEL_SCHEME]
    max_length = -1
    attachment_support = False
    free_sending = False
    show_config_page = False

    update_form = UpdateAndroidForm
