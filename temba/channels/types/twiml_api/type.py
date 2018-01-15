from __future__ import unicode_literals, absolute_import

from django.utils.translation import ugettext_lazy as _

from temba.channels.types.twiml_api.views import ClaimView
from temba.contacts.models import TEL_SCHEME
from ...models import Channel, ChannelType


class TwimlAPIType(ChannelType):
    """
    An Twiml API channel
    """

    code = 'TW'
    category = ChannelType.Category.PHONE

    name = "TwiML Rest API"
    slug = "twiml_api"
    icon = "icon-channel-twilio"

    claim_blurb = _("""Connect to a service that speaks TwiML. You can use this to connect to TwiML compatible services outside of Twilio.""")
    claim_view = ClaimView

    schemes = [TEL_SCHEME]
    max_length = 1600

    attachment_support = True

    ivr_protocol = ChannelType.IVRProtocol.IVR_PROTOCOL_TWIML

    def send(self, channel, msg, text):
        # use regular Twilio channel sending
        return Channel.get_type_from_code('T').send(channel, msg, text)
