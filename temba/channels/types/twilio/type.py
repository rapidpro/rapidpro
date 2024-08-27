from twilio.base.exceptions import TwilioRestException
from twilio.rest import Client as TwilioClient

from django.urls import re_path
from django.utils.translation import gettext_lazy as _

from temba.contacts.models import URN
from temba.utils.timezones import timezone_to_country_code

from ...models import Channel, ChannelType
from .views import SUPPORTED_COUNTRIES, ClaimView, Connect, SearchView, UpdateForm


class TwilioType(ChannelType):
    """
    An Twilio channel
    """

    SESSION_ACCOUNT_SID = "TWILIO_ACCOUNT_SID"
    SESSION_AUTH_TOKEN = "TWILIO_AUTH_TOKEN"

    code = "T"
    name = "Twilio"
    category = ChannelType.Category.PHONE

    unique_addresses = True

    courier_url = r"^t/(?P<uuid>[a-z0-9\-]+)/(?P<action>receive|status)$"
    schemes = [URN.TEL_SCHEME]
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

    claim_blurb = _("Easily add a two way number you have configured with %(link)s using their APIs.") % {
        "link": '<a target="_blank" href="https://www.twilio.com/">Twilio</a>'
    }
    claim_view = ClaimView
    update_form = UpdateForm

    def is_recommended_to(self, org, user):
        return timezone_to_country_code(org.timezone) in SUPPORTED_COUNTRIES

    def deactivate(self, channel):
        config = channel.config
        client = TwilioClient(config[Channel.CONFIG_ACCOUNT_SID], config[Channel.CONFIG_AUTH_TOKEN])
        number_update_args = {"sms_application_sid": ""}

        if channel.supports_ivr():
            number_update_args["voice_application_sid"] = ""

        try:
            try:
                number_sid = channel.config["number_sid"]
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

    def get_urls(self):
        return [
            self.get_claim_url(),
            re_path(r"^search/$", SearchView.as_view(channel_type=self), name="search"),
            re_path(r"^connect/$", Connect.as_view(channel_type=self), name="connect"),
        ]

    def get_error_ref_url(self, channel, code: str) -> str:
        return f"https://www.twilio.com/docs/api/errors/{code}"

    def check_credentials(self, config: dict) -> bool:
        account_sid = config.get("account_sid", None)
        account_token = config.get("auth_token", None)

        try:
            client = TwilioClient(account_sid, account_token)
            # get the actual primary auth tokens from twilio and use them
            client.api.account.fetch()
        except Exception:  # pragma: needs cover
            return False
        return True
