from django.utils.translation import ugettext_lazy as _

from temba.channels.types.twilio_messaging_service.views import ClaimView
from temba.channels.views import TWILIO_SUPPORTED_COUNTRIES_CONFIG
from temba.contacts.models import TEL_SCHEME
from temba.utils.timezones import timezone_to_country_code

from ...models import ChannelType


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
        """
        You can connect a messaging service from your Twilio account to benefit from <a href="https://www.twilio.com/copilot">Twilio Copilot features</a></br>
        """
    )

    configuration_blurb = _(
        """
        To finish configuring your Twilio Messaging Service connection you'll need to add the following URL in your Messaging Service Inbound Settings.
        """
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

    schemes = [TEL_SCHEME]
    max_length = 1600

    attachment_support = True

    def is_recommended_to(self, user):
        org = user.get_org()
        countrycode = timezone_to_country_code(org.timezone)
        return countrycode in TWILIO_SUPPORTED_COUNTRIES_CONFIG
