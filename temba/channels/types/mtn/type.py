from django.utils.translation import gettext_lazy as _

from temba.channels.types.mtn.views import ClaimView
from temba.contacts.models import URN

from ...models import ChannelType


class MtnType(ChannelType):
    """
    An MTN Developer Portal channel (https://developers.mtn.com/)
    """

    code = "MTN"
    category = ChannelType.Category.PHONE

    beta_only = True

    courier_url = r"^mtn/(?P<uuid>[a-z0-9\-]+)/(?P<action>status|receive)$"

    name = "MTN Developer Portal"

    claim_blurb = _("Easily add a two way number you have configured with %(link)s using their APIs.") % {
        "link": '<a href="https://developers.mtn.com/">MTN Developer Portal</a>'
    }

    claim_view = ClaimView

    schemes = [URN.TEL_SCHEME]
    max_length = 160

    configuration_urls = (
        dict(
            label=_("MO messages Callback URL"),
            url="https://{{ channel.callback_domain }}{% url 'courier.mtn' channel.uuid 'receive' %}",
            description=_(
                "This endpoint should be called by MTN Developer Portal when new messages are received to your number."
            ),
        ),
        dict(
            label=_("Delivery status reports URL"),
            url="https://{{ channel.callback_domain }}{% url 'courier.mtn' channel.uuid 'status' %}",
            description=_(
                "This endpoint should be called by  MTN Developer Portal when the message status changes. (delivery reports)"
            ),
        ),
    )
