from django.utils.translation import ugettext_lazy as _

from temba.channels.models import ChannelType
from temba.channels.types.verboice.views import ClaimView
from temba.contacts.models import URN


class VerboiceType(ChannelType):
    code = "VB"
    category = ChannelType.Category.PHONE

    name = "Verboice"

    courier_url = r"^vb/(?P<uuid>[a-z0-9\-]+)/(?P<action>status|receive)$"

    claim_blurb = _(
        "Use a %(link)s connection to leverage in-country SIP connections for building voice (IVR) flows."
    ) % {"link": '<a href="http://verboice.instedd.org">Verboice</a>'}
    claim_view = ClaimView

    max_length = 1600
    schemes = [URN.TEL_SCHEME]

    ivr_protocol = ChannelType.IVRProtocol.IVR_PROTOCOL_TWIML

    configuration_blurb = _(
        "To finish configuring your connection you'll need to set the following status callback URL for your Verboice "
        "project"
    )

    configuration_urls = (
        dict(
            label=_("Status Callback URL"),
            url="https://{{ channel.callback_domain }}{% url 'courier.vb' channel.uuid 'status' %}",
        ),
    )

    def is_available_to(self, user):
        return False
