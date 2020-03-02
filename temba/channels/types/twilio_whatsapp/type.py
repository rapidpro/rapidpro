from django.utils.translation import ugettext_lazy as _

from temba.channels.types.twilio_whatsapp.views import ClaimView
from temba.contacts.models import WHATSAPP_SCHEME

from ...models import ChannelType


class TwilioWhatsappType(ChannelType):
    """
    An Twilio channel
    """

    code = "TWA"
    category = ChannelType.Category.SOCIAL_MEDIA

    courier_url = r"^twa/(?P<uuid>[a-z0-9\-]+)/(?P<action>receive|status)$"

    name = "Twilio Whatsapp"
    icon = "icon-whatsapp"

    claim_blurb = _(
        """If you have a Twilio for Whatsapp number, you can connect it to communicate with your Whatsapp contacts. <a href="https://www.twilio.com/whatsapp/">Learn more about Twilio Whatsapp</a>"""
    )

    claim_view = ClaimView

    schemes = [WHATSAPP_SCHEME]
    max_length = 1600

    configuration_blurb = _(
        """
        To finish configuring your Twilio Whatsapp connection you'll need to add the following URL in your Twilio Inbound Settings.
        Check the Twilio WhatsApp documentation for more information.
        """
    )

    configuration_urls = (
        dict(
            label=_("Request URL"),
            url="https://{{ channel.callback_domain }}{% url 'courier.twa' channel.uuid 'receive' %}",
            description=_(
                "This endpoint should be called by Twilio when new messages are received by your Twilio Whatsapp number."
            ),
        ),
    )

    redact_request_keys = {
        "FromCity",
        "FromState",
        "FromZip",
        "ToCity",
        "ToState",
        "ToZip",
        "CalledCity",
        "CalledState",
        "CalledZip",
    }
