from django.urls import re_path
from django.utils.translation import gettext_lazy as _

from temba.channels.models import ChannelType, ConfigUI
from temba.contacts.models import URN
from temba.utils.timezones import timezone_to_country_code

from .client import VonageClient
from .views import ClaimView, Connect, SearchView, UpdateForm

RECOMMENDED_COUNTRIES = {
    "US",
    "CA",
    "GB",
    "AU",
    "AT",
    "FI",
    "DE",
    "HK",
    "HU",
    "LT",
    "NL",
    "NO",
    "PL",
    "SE",
    "CH",
    "BE",
    "ES",
    "ZA",
}


class VonageType(ChannelType):
    """
    A Vonage (formerly Nexmo) channel

    Callback status information (https://developer.vonage.com/en/api/voice#status-values):

        started: Platform has started the call.
        ringing: The user's handset is ringing.
        answered: The user has answered your call.
        machine: Platform detected an answering machine.
        human: Platform detected human answering the call.
        completed: Platform has terminated this call.
        timeout: Your user did not answer your call within ringing_timer seconds.
        failed: The call failed to complete
        rejected: The call was rejected
        cancelled: The call was not answered
        busy: The number being dialled was on another call
    """

    SESSION_API_KEY = "VONAGE_API_KEY"
    SESSION_API_SECRET = "VONAGE_API_SECRET"

    CONFIG_API_KEY = "nexmo_api_key"
    CONFIG_API_SECRET = "nexmo_api_secret"
    CONFIG_APP_ID = "nexmo_app_id"
    CONFIG_APP_PRIVATE_KEY = "nexmo_app_private_key"

    code = "NX"
    name = "Vonage"
    category = ChannelType.Category.PHONE

    unique_addresses = True

    courier_url = r"^nx/(?P<uuid>[a-z0-9\-]+)/(?P<action>status|receive)$"
    schemes = [URN.TEL_SCHEME]

    claim_blurb = _("Easily add a two way number you have configured with %(link)s using their APIs.") % {
        "link": '<a target="_blank" href="https://www.vonage.com/">Vonage</a>'
    }
    claim_view = ClaimView
    update_form = UpdateForm

    config_ui = ConfigUI(
        blurb=_(
            "Your Vonage configuration URLs are as follows. These should have been set up automatically when claiming your "
            "number, but if not you can set them from your Vonage dashboard."
        ),
        endpoints=[
            ConfigUI.Endpoint(
                courier="receive",
                label=_("Callback URL for Inbound Messages"),
                help=_("The callback URL is called by Vonage when you receive new incoming messages."),
            ),
            ConfigUI.Endpoint(
                courier="status",
                label=_("Callback URL for Delivery Receipt"),
                help=_("The delivery URL is called by Vonage when a message is successfully delivered to a recipient."),
            ),
            ConfigUI.Endpoint(
                mailroom="incoming",
                label=_("Callback URL for Incoming Call"),
                help=_("The callback URL is called by Vonage when you receive an incoming call."),
            ),
        ],
    )

    def is_recommended_to(self, org, user):
        return timezone_to_country_code(org.timezone) in RECOMMENDED_COUNTRIES

    def deactivate(self, channel):
        app_id = channel.config.get(self.CONFIG_APP_ID)
        api_key = channel.config.get(self.CONFIG_API_KEY)
        api_secret = channel.config.get(self.CONFIG_API_SECRET)
        if api_key and api_secret and app_id:
            client = VonageClient(api_key=api_key, api_secret=api_secret)
            client.delete_application(app_id)

    def get_urls(self):
        return [
            self.get_claim_url(),
            re_path(r"^search$", SearchView.as_view(channel_type=self), name="search"),
            re_path(r"^connect$", Connect.as_view(channel_type=self), name="connect"),
        ]

    def get_error_ref_url(self, channel, code: str) -> str:
        if code.startswith("send:"):
            return "https://developer.vonage.com/messaging/sms/guides/troubleshooting-sms"
        elif code.startswith("dlr:"):
            return "https://developer.vonage.com/messaging/sms/guides/delivery-receipts"
        return None
