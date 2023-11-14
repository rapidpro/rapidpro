from django.utils.translation import gettext_lazy as _

from temba.channels.types.twilio.type import TwilioType
from temba.channels.types.twilio.views import UpdateForm
from temba.contacts.models import URN

from ...models import ChannelType, ConfigUI
from .views import ClaimView


class TwilioWhatsappType(ChannelType):
    """
    An Twilio channel
    """

    SESSION_ACCOUNT_SID = TwilioType.SESSION_ACCOUNT_SID
    SESSION_AUTH_TOKEN = TwilioType.SESSION_AUTH_TOKEN

    code = "TWA"
    name = "Twilio WhatsApp"
    category = ChannelType.Category.SOCIAL_MEDIA

    unique_addresses = True

    courier_url = r"^twa/(?P<uuid>[a-z0-9\-]+)/(?P<action>receive|status)$"
    schemes = [URN.WHATSAPP_SCHEME]
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

    claim_blurb = _("If you have a %(link)s number, you can connect it to communicate with your WhatsApp contacts.") % {
        "link": '<a target="_blank" href="https://www.twilio.com/whatsapp/">Twilio WhatsApp</a>'
    }

    claim_view = ClaimView
    update_form = UpdateForm

    config_ui = ConfigUI(
        blurb=_(
            "To finish configuring this channel, you'll need to add the following URL in your Twilio "
            "Inbound Settings. Check the Twilio WhatsApp documentation for more information."
        ),
        endpoints=[
            ConfigUI.Endpoint(
                courier="receive",
                label=_("Request URL"),
                help=_("This endpoint should be called by Twilio when new messages are received by your number."),
            ),
        ],
    )

    def get_error_ref_url(self, channel, code: str) -> str:
        return f"https://www.twilio.com/docs/api/errors/{code}"

    def check_credentials(self, config: dict) -> bool:
        return TwilioType().check_credentials(config)
