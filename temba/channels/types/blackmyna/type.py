from django.utils.translation import ugettext_lazy as _

from temba.channels.views import AuthenticatedExternalClaimView
from temba.contacts.models import URN

from ...models import ChannelType


class BlackmynaType(ChannelType):
    """
    An Blackmyna channel (https://blackmyna.com)
    """

    code = "BM"
    category = ChannelType.Category.PHONE

    courier_url = r"^bm/(?P<uuid>[a-z0-9\-]+)/(?P<action>status|receive)$"

    name = "Blackmyna"

    claim_blurb = _("Easily add a two way number you have configured with %(link)s using their APIs.") % {
        "link": '<a href="http://blackmyna.com">Blackmyna</a>'
    }
    claim_view = AuthenticatedExternalClaimView

    schemes = [URN.TEL_SCHEME]
    max_length = 1600
    attachment_support = False

    configuration_blurb = _(
        "To finish configuring your Blackmyna connection you'll need to notify Blackmyna of the following URLs."
    )

    configuration_urls = (
        dict(
            label=_("Inbound URL"),
            url="https://{{ channel.callback_domain }}{% url 'courier.bm' channel.uuid 'receive' %}",
            description=_(
                "This endpoint should be called by Blackmyna when new messages are received to your number."
            ),
        ),
        dict(
            label=_("DLR URL"),
            url="https://{{ channel.callback_domain }}{% url 'courier.bm' channel.uuid 'status' %}",
            description=_(
                "This endpoint should be called by Blackmyna when the message status changes. (delivery reports)"
            ),
        ),
    )

    def is_available_to(self, user):
        org = user.get_org()
        return org.timezone and str(org.timezone) in ["Asia/Kathmandu"]

    def is_recommended_to(self, user):
        return self.is_available_to(user)
