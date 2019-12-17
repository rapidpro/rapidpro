from twilio.base.exceptions import TwilioRestException

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
        Check the <a href="https://www.twilio.com/docs/sms/whatsapp/api#configuring-inbound-message-webhooks">guide on https://www.twilio.com/docs/sms/whatsapp/api#configuring-inbound-message-webhooks for more info</a>
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

    def deactivate(self, channel):
        config = channel.config
        client = channel.org.get_twilio_client()
        number_update_args = dict()

        if not channel.is_delegate_sender():
            number_update_args["sms_application_sid"] = ""

        if channel.supports_ivr():
            number_update_args["voice_application_sid"] = ""

        try:
            try:
                number_sid = channel.bod or channel.config.get("number_sid")
                client.api.incoming_phone_numbers.get(number_sid).update(**number_update_args)
            except Exception:
                if client:
                    matching = client.api.incoming_phone_numbers.stream(phone_number=channel.address)
                    first_match = next(matching, None)
                    if first_match:
                        client.api.incoming_phone_numbers.get(first_match.sid).update(**number_update_args)

            if "application_sid" in config:
                try:
                    client.api.applications.get(sid=config["application_sid"]).delete()
                except TwilioRestException:  # pragma: no cover
                    pass

        except TwilioRestException as e:
            # we swallow 20003 which means our twilio key is no longer valid
            if e.code != 20003:
                raise e
