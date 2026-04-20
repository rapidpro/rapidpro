from django.utils.translation import gettext_lazy as _

from temba.contacts.models import URN

from ...models import ChannelType
from .views import ClaimView, UpdateForm


class AndroidType(ChannelType):
    """
    An Android relayer app channel type
    """

    code = "A"
    name = "Android"
    category = ChannelType.Category.PHONE

    schemes = [URN.TEL_SCHEME]

    claim_blurb = _(
        "Works in any country and uses the cell phone plan you already have. You just need an Android phone to get started."
    )
    claim_view = ClaimView

    update_form = UpdateForm

    def is_recommended_to(self, org, user):
        return False
