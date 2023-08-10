from django.utils.translation import gettext_lazy as _

from temba.channels.types.twilio.type import TwilioType
from temba.channels.types.twilio.views import UpdateForm
from temba.contacts.models import URN

from ...models import ChannelType
from .views import ClaimView


class TwilioWhatsappType(ChannelType):
    """
    An Twilio channel
    """

    SESSION_ACCOUNT_SID = TwilioType.SESSION_ACCOUNT_SID
    SESSION_AUTH_TOKEN = TwilioType.SESSION_AUTH_TOKEN

    code = "TWA"
    category = ChannelType.Category.SOCIAL_MEDIA

    courier_url = r"^twa/(?P<uuid>[a-z0-9\-]+)/(?P<action>receive|status)$"

    name = "Twilio WhatsApp"

    claim_blurb = _("If you have a %(link)s number, you can connect it to communicate with your WhatsApp contacts.") % {
        "link": '<a target="_blank" href="https://www.twilio.com/whatsapp/">Twilio WhatsApp</a>'
    }

    claim_view = ClaimView
    update_form = UpdateForm

    schemes = [URN.WHATSAPP_SCHEME]
    max_length = 1600

    configuration_blurb = _(
        "To finish configuring your Twilio WhatsApp connection you'll need to add the following URL in your Twilio "
        "Inbound Settings. Check the Twilio WhatsApp documentation for more information."
    )

    configuration_urls = (
        dict(
            label=_("Request URL"),
            url="https://{{ channel.callback_domain }}{% url 'courier.twa' channel.uuid 'receive' %}",
            description=_(
                "This endpoint should be called by Twilio when new messages are received by your Twilio WhatsApp "
                "number."
            ),
        ),
    )

    redact_request_keys = (
        "FromCity",
        "FromState",
        "FromZip",
        "ToCity",
        "ToState",
        "ToZip",
        "CalledCity",
        "CalledState",
        "CalledZip",
    )

    def get_error_ref_url(self, channel, code: str) -> str:
        return f"https://www.twilio.com/docs/api/errors/{code}"

    def check_credentials(self, config: dict) -> bool:
        return TwilioType().check_credentials(config)
