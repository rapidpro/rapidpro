from __future__ import unicode_literals, absolute_import

from django.utils.translation import ugettext_lazy as _

from temba.channels.types.verboice.views import ClaimView
from temba.contacts.models import TEL_SCHEME
from temba.channels.models import ChannelType


class VerboiceType(ChannelType):
    code = 'VB'
    category = ChannelType.Category.PHONE

    name = "Verboice"

    claim_blurb = _('Use a <a href="http://verboice.instedd.org">Verboice</a> connection to leverage in-country SIP connections for building voice (IVR) flows.')
    claim_view = ClaimView

    max_length = 1600
    schemes = [TEL_SCHEME]

    ivr_protocol = ChannelType.IVRProtocol.IVR_PROTOCOL_TWIML

    configuration_blurb = _(
        """
        To finish configuring your connection you'll need to set the following status callback URL for your Verboice project
        """
    )

    configuration_urls = (
        dict(
            label=_("Status Callback URL"),
            url="https://{{ channel.callback_domain }}{% url 'courier.vb' channel.uuid 'status' %}",
        ),
    )

    def is_available_to(self, user):
        return False

    def send(self, channel, msg, text):  # pragma: no cover
        raise Exception("Sending Verboice messages is not supported.")
