from __future__ import unicode_literals, absolute_import

from django.utils.translation import ugettext_lazy as _

from temba.contacts.models import TEL_SCHEME
from .views import ClaimView
from ...models import ChannelType


class InfobipUSSDType(ChannelType):
    """
    An Infobip USSD channel (https://ussd.infobip.com/)
    """

    code = 'IBU'
    category = ChannelType.Category.PHONE

    name = "Infobip USSD"
    icon = 'icon-power-cord'

    claim_blurb = _("""Add a USSD channel you have configured with <a href="http://ussd.infobip.com">Infobip</a> using their APIs.""")
    claim_view = ClaimView

    schemes = [TEL_SCHEME]
    max_length = 1600
    attachment_support = False

    def send(self, channel, msg, text):  # pragma: no cover
        pass
