from django.urls import re_path
from django.utils.translation import gettext_lazy as _

from temba.channels.models import Channel, ChannelType
from temba.contacts.models import URN
from temba.utils.timezones import timezone_to_country_code

from .views import ClaimView, SearchView, UpdateForm

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

    Callback status information (https://developer.nexmo.com/api/voice#status-values):

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

    code = "NX"
    category = ChannelType.Category.PHONE

    courier_url = r"^nx/(?P<uuid>[a-z0-9\-]+)/(?P<action>status|receive)$"

    name = "Vonage"
    icon = "icon-vonage"

    claim_blurb = _("Easily add a two way number you have configured with %(link)s using their APIs.") % {
        "link": '<a href="https://www.vonage.com/">Vonage</a>'
    }
    claim_view = ClaimView
    update_form = UpdateForm

    schemes = [URN.TEL_SCHEME]
    max_length = 1600
    max_tps = 1

    ivr_protocol = ChannelType.IVRProtocol.IVR_PROTOCOL_NCCO

    configuration_blurb = _(
        "Your Vonage configuration URLs are as follows. These should have been set up automatically when claiming your "
        "number, but if not you can set them from your Vonage dashboard."
    )

    configuration_urls = (
        dict(
            label=_("Callback URL for Inbound Messages"),
            url="https://{{ channel.callback_domain }}{% url 'courier.nx' channel.uuid 'receive' %}",
            description=_("The callback URL is called by Vonage when you receive new incoming messages."),
        ),
        dict(
            label=_("Callback URL for Delivery Receipt"),
            url="https://{{ channel.callback_domain }}{% url 'courier.nx' channel.uuid 'status' %}",
            description=_(
                "The delivery URL is called by Vonage when a message is successfully delivered to a recipient."
            ),
        ),
        dict(
            label=_("Callback URL for Incoming Call"),
            url="https://{{ channel.callback_domain }}{% url 'mailroom.ivr_handler' channel.uuid 'incoming' %}",
            description=_("The callback URL is called by Vonage when you receive an incoming call."),
        ),
    )

    def is_recommended_to(self, user):
        org = user.get_org()
        country_code = timezone_to_country_code(org.timezone)
        return country_code in RECOMMENDED_COUNTRIES

    def deactivate(self, channel):
        app_id = channel.config.get(Channel.CONFIG_VONAGE_APP_ID)
        if app_id:
            client = channel.org.get_vonage_client()
            client.delete_application(app_id)

    def get_urls(self):
        return [self.get_claim_url(), re_path(r"^search$", SearchView.as_view(), name="search")]
