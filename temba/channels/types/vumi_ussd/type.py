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

    configuration_blurb = _(
        """
        To finish configuring your Vumi connection you'll need to set the following parameters on your Vumi conversation:
        """
    )

    configuration_urls = (
        dict(
            label=_("API Token"),
            url="{{ channel.config.access_token }}",
            description=_('This token is used to authenticate with your Vumi account, set it by editing the "Content" page on your conversation.'),
        ),
        dict(
            label=_("Push Message URL"),
            url="https://{{ channel.callback_domain }}{% url 'courier.vm' channel.uuid 'receive' %}",
            description=_("This endpoint will be called by Vumi when new messages are received to your number."),
        ),
        dict(
            label=_("Push Event URL"),
            url="https://{{ channel.callback_domain }}{% url 'courier.vm' channel.uuid 'event' %}",
            description=_("This endpoint will be called by Vumi when sent messages are sent or delivered."),
        ),
    )

    def is_available_to(self, user):
        org = user.get_org()
        return org.timezone and six.text_type(org.timezone) in ['Africa/Johannesburg']

    def send(self, channel, msg, text):
        # use regular Vumi channel sending
        return Channel.get_type_from_code('VM').send(channel, msg, text)
