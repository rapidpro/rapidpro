# -*- coding: utf-8 -*-
from __future__ import absolute_import, division, print_function, unicode_literals

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

    schemes = [TEL_SCHEME]
    max_length = 1600

    attachment_support = True

    ivr_protocol = ChannelType.IVRProtocol.IVR_PROTOCOL_TWIML

    claim_view = ClaimView
    claim_blurb = _(
        """
        Connect to a service that speaks TwiML. You can use this to connect to TwiML compatible services outside of Twilio.
        """
    )

    configuration_blurb = _(
        """
        To finish configuring your TwiML REST API channel you'll need to add the following URL in your TwiML REST API instance.
        """
    )

    configuration_urls = (
        dict(
            label=_("TwiML REST API Host"),
            url="{{ channel.config.send_url }}",
            description=_("The endpoint which will receive Twilio API requests for this channel"),
        ),
        dict(
            label=_(""),
            url="https://{{ channel.callback_domain }}{% url 'handlers.twiml_api_handler' channel.uuid %}",
            description=_("Incoming messages for this channel will be sent to this endpoint."),
        )
    )

    def send(self, channel, msg, text):
        # use regular Twilio channel sending
        return Channel.get_type_from_code('T').send(channel, msg, text)
