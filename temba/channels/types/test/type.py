from django.utils.translation import gettext_lazy as _

from temba.contacts.models import URN

from ...models import ChannelType
from .views import ClaimView


class TestType(ChannelType):
    """
    A dummy channel type for load testing purposes
    """

    code = "TST"
    name = "Test"
    category = ChannelType.Category.API
    schemes = [URN.EXTERNAL_SCHEME]

    claim_blurb = _("Only staff users can see this option. Used for load testing. Uses ext URNs.")
    claim_view = ClaimView

    def is_available_to(self, org, user):
        return user.is_staff, user.is_staff
