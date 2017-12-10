from __future__ import unicode_literals, absolute_import

from django.utils.translation import ugettext_lazy as _

from temba.channels.models import ChannelType, Channel
from temba.channels.types.junebug_ussd.views import ClaimView
from temba.contacts.models import TEL_SCHEME


class JunebugUSSDType(ChannelType):
    """
    A Junebug USSD channel
    """

    code = 'JNU'
    category = ChannelType.Category.USSD

    name = "Junebug USSD"
    slug = "junebug_ussd"
    icon = "icon-junebug"

    claim_blurb = _("""Connect your <a href="https://junebug.praekelt.org/" target="_blank">Junebug</a> instance that you have already set up and configured.""")
    claim_view = ClaimView

    schemes = [TEL_SCHEME]
    max_length = 1600

    def send(self, channel, msg, text):
        # use regular Junebug channel sending
        return Channel.get_type_from_code('JN').send(channel, msg, text)
