from django.utils.translation import ugettext_lazy as _

from temba.channels.views import AuthenticatedExternalClaimView
from temba.contacts.models import TEL_SCHEME

from ...models import ChannelType


class RedRabbitType(ChannelType):
    """
    A RedRabbit channel (http://www.redrabbitsms.com/)
    """

    code = "RR"
    category = ChannelType.Category.PHONE

    name = "Red Rabbit"

    claim_blurb = _(
        """Easily add a two way number you have configured with <a href="http://www.redrabbitsms.com/">Red Rabbit</a> using their APIs."""
    )

    claim_view = AuthenticatedExternalClaimView

    schemes = [TEL_SCHEME]
    max_length = 1600
    attachment_support = False

    def is_available_to(self, user):
        return False  # Hidden since it is MT only
