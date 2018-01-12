from __future__ import unicode_literals, absolute_import

import six

from django.utils.translation import ugettext_lazy as _

from temba.channels.models import Channel, ChannelType
from temba.channels.types.vumi_ussd.views import ClaimView
from temba.contacts.models import TEL_SCHEME


class VumiUSSDType(ChannelType):
    """
    A Vumi USSD channel
    """

    code = 'VMU'
    category = ChannelType.Category.USSD

    name = "Vumi USSD"
    slug = 'vumi_ussd'

    claim_blurb = _("""Easily connect your <a href="http://go.vumi.org/">Vumi</a> account to take advantage of session based messaging across USSD transports.""")
    claim_view = ClaimView

    schemes = [TEL_SCHEME]
    max_length = 182

    is_ussd = True

    def is_available_to(self, user):
        org = user.get_org()
        return org.timezone and six.text_type(org.timezone) in ['Africa/Johannesburg']

    def send(self, channel, msg, text):
        # use regular Vumi channel sending
        return Channel.get_type_from_code('VM').send(channel, msg, text)
