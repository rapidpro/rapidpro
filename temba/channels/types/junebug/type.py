from django.utils.translation import ugettext_lazy as _

from temba.channels.models import ChannelType
from temba.channels.types.junebug.views import ClaimView
from temba.contacts.models import URN


class JunebugType(ChannelType):
    """
    A Junebug channel
    """

    code = "JN"
    category = ChannelType.Category.PHONE

    courier_url = r"^jn/(?P<uuid>[a-z0-9\-]+)/(?P<action>inbound)$"

    name = "Junebug"
    icon = "icon-junebug"

    claim_blurb = _("Connect your %(link)s instance that you have already set up and configured.") % {
        "link": '<a href="https://junebug.praekelt.org/">Junebug</a>'
    }
    claim_view = ClaimView

    schemes = [URN.TEL_SCHEME]
    max_length = 1600

    configuration_blurb = _(
        "As a last step you'll need to configure Junebug to call the following URL for MO (incoming) messages."
    )

    configuration_urls = (
        dict(
            label=_("Push Message URL"),
            url="https://{{ channel.callback_domain }}{% url 'courier.jn' channel.uuid 'inbound' %}",
            description=_(
                "This endpoint will be called by Junebug when new messages are received to your number, it must be "
                "configured to be called as a POST."
            ),
        ),
    )
