from __future__ import unicode_literals, absolute_import

from django.utils.translation import ugettext_lazy as _

from temba.channels.types.clickatell.views import ClaimView
from temba.contacts.models import TEL_SCHEME
from ...models import ChannelType


class ClickatellType(ChannelType):
    """
    A Clickatell channel (https://clickatell.com/)
    """

    code = 'CT'
    category = ChannelType.Category.PHONE

    name = "Clickatell"
    icon = "icon-channel-clickatell"

    claim_blurb = _("""Connect your <a href="http://clickatell.com/" target="_blank">Clickatell</a> number, we'll walk you
                           through the steps necessary to get your Clickatell connection working in a few minutes.""")
    claim_view = ClaimView

    schemes = [TEL_SCHEME]
    max_length = 420
    attachment_support = False

    def send(self, channel, msg, text):
        raise Exception("Sending Clickatell messages is only possible via Courier")
