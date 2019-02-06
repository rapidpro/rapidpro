from django.utils.translation import ugettext_lazy as _

from temba.channels.views import AuthenticatedExternalClaimView
from temba.contacts.models import TEL_SCHEME

from ...models import ChannelType


class SMSCentralType(ChannelType):
    """
    An SMSCentral channel (http://smscentral.com.np/)
    """

    code = "SC"
    category = ChannelType.Category.PHONE

    courier_url = r"^sc/(?P<uuid>[a-z0-9\-]+)/(?P<action>receive)$"

    name = "SMSCentral"
    icon = "icon-channel-external"

    claim_blurb = _(
        """Easily add a two way number you have configured with <a href="http://smscentral.com.np/">SMSCentral</a> using their APIs."""
    )
    claim_view = AuthenticatedExternalClaimView

    schemes = [TEL_SCHEME]
    max_length = 1600
    max_tps = 1

    attachment_support = False

    configuration_blurb = _(
        """
        To finish configuring your SMSCentral connection you'll need to notify SMSCentral of the following URL.
        """
    )

    configuration_urls = (
        dict(
            label=_("Inbound URL"),
            url="https://{{ channel.callback_domain }}{% url 'courier.sc' channel.uuid 'receive' %}",
            description=_(
                "This endpoint should be called by SMSCentral when new messages are received to your number."
            ),
        ),
    )

    def is_available_to(self, user):
        org = user.get_org()
        return org.timezone and str(org.timezone) in ["Asia/Kathmandu"]
