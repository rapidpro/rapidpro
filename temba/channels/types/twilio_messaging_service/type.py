from django.utils.translation import gettext_lazy as _

from temba.channels.types.twilio.views import SUPPORTED_COUNTRIES
from temba.contacts.models import URN
from temba.utils.timezones import timezone_to_country_code

from ...models import ChannelType
from .views import ClaimView


class TwilioMessagingServiceType(ChannelType):
    """
    An Twilio Messaging Service channel
    """

    code = "TMS"
    category = ChannelType.Category.PHONE

    courier_url = r"^tms/(?P<uuid>[a-z0-9\-]+)/(?P<action>receive|status)$"

    name = "Twilio Messaging Service"
    slug = "twilio_messaging_service"
    icon = "icon-channel-twilio"

    claim_view = ClaimView
    claim_blurb = _(
        "You can connect a messaging service from your Twilio account to benefit from %(link)s features."
    ) % {"link": '<a href="https://www.twilio.com/copilot">Twilio Copilot</a>'}

    configuration_blurb = _(
        "To finish configuring your Twilio Messaging Service connection you'll need to add the following URL in your "
        "Messaging Service Inbound Settings."
    )

    configuration_urls = (
        dict(
            label=_("Request URL"),
            url="https://{{ channel.callback_domain }}{% url 'courier.tms' channel.uuid 'receive' %}",
            description=_(
                "This endpoint should be called by Twilio when new messages are received by your Messaging Service."
            ),
        ),
    )

    schemes = [URN.TEL_SCHEME]
    max_length = 1600

    def is_recommended_to(self, org, user):
        return timezone_to_country_code(org.timezone) in SUPPORTED_COUNTRIES

    def get_error_ref_url(self, channel, code: str) -> str:
        return f"https://www.twilio.com/docs/api/errors/{code}"
