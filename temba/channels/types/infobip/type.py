from django.utils.translation import ugettext_lazy as _

from temba.channels.views import AuthenticatedExternalCallbackClaimView
from temba.contacts.models import URN

from ...models import ChannelType


class InfobipType(ChannelType):
    """
    An Infobip channel (https://www.infobip.com/)
    """

    code = "IB"
    category = ChannelType.Category.PHONE

    courier_url = r"^ib/(?P<uuid>[a-z0-9\-]+)/(?P<action>delivered|receive)$"

    name = "Infobip"

    claim_blurb = _("Easily add a two way number you have configured with %(link)s using their APIs.") % {
        "link": '<a href="http://infobip.com">Infobip</a>'
    }
    claim_view = AuthenticatedExternalCallbackClaimView

    schemes = [URN.TEL_SCHEME]
    max_length = 1600
    attachment_support = False

    configuration_blurb = _(
        "To finish configuring your Infobip connection you'll need to set the following callback URLs on the Infobip "
        "website under your account."
    )

    configuration_urls = (
        dict(
            label=_("Received URL"),
            url="https://{{ channel.callback_domain }}{% url 'courier.ib' channel.uuid 'receive' %}",
            description=_(
                "This endpoint should be called with a POST by Infobip when new messages are received to your number. "
                "You can set the receive URL on your Infobip account by contacting your sales agent."
            ),
        ),
        dict(
            label=_("Delivered URL"),
            url="https://{{ channel.callback_domain }}{% url 'courier.ib' channel.uuid 'delivered' %}",
            description=_(
                "This endpoint should be called with a POST by Infobip when a message has been to the final recipient. "
                "(delivery reports) You can set the delivery callback URL on your Infobip account by contacting your "
                "sales agent."
            ),
        ),
    )
