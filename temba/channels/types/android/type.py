from temba.contacts.models import TEL_SCHEME

from ...models import ChannelType
from ...views import UpdateAndroidForm
from .views import ClaimView


class AndroidType(ChannelType):
    """
    An Android relayer app channel type
    """

    code = "A"

    name = "Android"
    icon = "icon-channel-android"

    claim_view = ClaimView

    schemes = [TEL_SCHEME]
    max_length = -1
    attachment_support = False
    free_sending = False
    show_config_page = False

    update_form = UpdateAndroidForm
