from __future__ import unicode_literals, absolute_import

import six

from django.utils.translation import ugettext_lazy as _

from temba.channels.types.zenvia.views import ClaimView
from temba.contacts.models import TEL_SCHEME
from temba.channels.models import ChannelType


class ZenviaType(ChannelType):
    """
    An Zenvia channel (https://www.zenvia.com/)
    """

    code = 'ZV'
    category = ChannelType.Category.PHONE

    name = "Zenvia"

    claim_blurb = _("""If you are based in Brazil, you can purchase a short code from <a href="http://www.zenvia.com.br/">Zenvia</a> and connect it in a few simple steps.""")
    claim_view = ClaimView

    schemes = [TEL_SCHEME]
    max_length = 150

    attachment_support = False

    def is_available_to(self, user):
        org = user.get_org()
        return org.timezone and six.text_type(org.timezone) in ['America/Sao_Paulo']

    def send(self, channel, msg, text):  # pragma: no cover
        raise Exception("Sending Zenvia messages is only possible via Courier")
