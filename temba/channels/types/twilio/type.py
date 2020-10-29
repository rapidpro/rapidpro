from twilio.base.exceptions import TwilioRestException

from django.utils.translation import ugettext_lazy as _

from temba.channels.types.twilio.views import ClaimView
from temba.channels.views import TWILIO_SUPPORTED_COUNTRIES_CONFIG
from temba.contacts.models import URN
from temba.utils.timezones import timezone_to_country_code

from ...models import ChannelType


class TwilioType(ChannelType):
    """
    An Twilio channel
    """

    code = "T"
    category = ChannelType.Category.PHONE
    show_config_page = False

    courier_url = r"^t/(?P<uuid>[a-z0-9\-]+)/(?P<action>receive|status)$"

    name = "Twilio"
    icon = "icon-channel-twilio"
    claim_blurb = _("Easily add a two way number you have configured with %(link)s using their APIs.") % {
        "link": '<a href="https://www.twilio.com/">Twilio</a>'
    }
    claim_view = ClaimView

    schemes = [URN.TEL_SCHEME]
    max_length = 1600

    ivr_protocol = ChannelType.IVRProtocol.IVR_PROTOCOL_TWIML

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

    def is_recommended_to(self, user):
        org = user.get_org()
        countrycode = timezone_to_country_code(org.timezone)
        return countrycode in TWILIO_SUPPORTED_COUNTRIES_CONFIG

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
