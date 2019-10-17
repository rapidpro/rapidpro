from django.utils.translation import ugettext_lazy as _

from temba.channels.types.africastalking.views import ClaimView
from temba.contacts.models import TEL_SCHEME

from ...models import ChannelType


class AfricasTalkingType(ChannelType):
    """
    An Africa's Talking channel (https://africastalking.com/)
    """

    code = "AT"
    category = ChannelType.Category.PHONE

    courier_url = r"^at/(?P<uuid>[a-z0-9\-]+)/(?P<action>receive|delivery|callback|status)$"

    name = "Africa's Talking"
    icon = "icon-channel-external"

    claim_blurb = _(
        """If you are based in Kenya, Malawi, Nigeria, Rwanda or Uganda you can purchase a short
    code from <a href="http://africastalking.com">Africa's Talking</a> and connect it
    in a few simple steps."""
    )
    claim_view = ClaimView

    schemes = [TEL_SCHEME]
    max_length = 160
    attachment_support = False

    configuration_blurb = _(
        """
        To finish configuring your Africa's Talking connection you'll need to set the following callback URLs
        on the Africa's Talking website under your account.
        """
    )

    configuration_urls = (
        dict(
            label=_("Callback URL"),
            url="https://{{ channel.callback_domain }}{% url 'courier.at' channel.uuid 'receive' %}",
            description=_(
                """
                You can set the callback URL on your Africa's Talking account by visiting the SMS Dashboard page,
                then clicking on Callback URL.
                """
            ),
        ),
        dict(
            label=_("Delivery URL"),
            url="https://{{ channel.callback_domain }}{% url 'courier.at' channel.uuid 'status' %}",
            description=_(
                """
                You can set the delivery URL on your Africa's Talking account by visiting the SMS Dashboard page,
                then clicking on Delivery Reports.
                """
            ),
        ),
    )

    def is_available_to(self, user):
        org = user.get_org()
        return org.timezone and str(org.timezone) in [
            "Africa/Nairobi",
            "Africa/Kampala",
            "Africa/Lilongwe",
            "Africa/Kigali",
            "Africa/Lagos",
        ]

    def is_recommended_to(self, user):
        return self.is_available_to(user)
