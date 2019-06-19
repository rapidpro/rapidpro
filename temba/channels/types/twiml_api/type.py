from django.utils.translation import ugettext_lazy as _

from temba.channels.types.twiml_api.views import ClaimView
from temba.contacts.models import TEL_SCHEME

from ...models import ChannelType


class TwimlAPIType(ChannelType):
    """
    An Twiml API channel

    Callback status information (https://www.twilio.com/docs/voice/twiml#callstatus-values):

        queued: The call is ready and waiting in line before going out.
        ringing: The call is currently ringing.
        in-progress: The call was answered and is currently in progress.
        completed: The call was answered and has ended normally.
        busy: The caller received a busy signal.
        failed: The call could not be completed as dialed, most likely because the phone number was non-existent.
        no-answer: The call ended without being answered.
        canceled: The call was canceled via the REST API while queued or ringing.

    """

    code = "TW"
    category = ChannelType.Category.PHONE

    name = "TwiML Rest API"
    slug = "twiml_api"
    icon = "icon-channel-twilio"

    courier_url = r"^tw/(?P<uuid>[a-z0-9\-]+)/(?P<action>receive|status)$"

    schemes = [TEL_SCHEME]
    max_length = 1600

    attachment_support = True

    ivr_protocol = ChannelType.IVRProtocol.IVR_PROTOCOL_TWIML

    claim_view = ClaimView
    claim_blurb = _(
        """
        Connect to a service that speaks TwiML. You can use this to connect to TwiML compatible services outside of Twilio.
        """
    )

    configuration_blurb = _(
        """
        To finish configuring your TwiML REST API channel you'll need to add the following URL in your TwiML REST API instance.
        """
    )

    configuration_urls = (
        dict(
            label=_("TwiML REST API Host"),
            url="{{ channel.config.send_url }}",
            description=_("The endpoint which will receive Twilio API requests for this channel"),
        ),
        dict(
            label="",
            url="https://{{ channel.callback_domain }}{% url 'courier.tw' channel.uuid 'receive' %}",
            description=_("Incoming messages for this channel will be sent to this endpoint."),
        ),
    )
