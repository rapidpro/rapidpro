from django.utils.translation import gettext_lazy as _

from temba.channels.types.thinq.views import ClaimView
from temba.contacts.models import URN
from temba.utils.timezones import timezone_to_country_code

from ...models import ChannelType, ConfigUI


class ThinQType(ChannelType):
    """
    A ThinQ channel (https://thinq.com/)
    """

    CONFIG_ACCOUNT_ID = "account_id"
    CONFIG_API_TOKEN_USER = "api_token_user"
    CONFIG_API_TOKEN = "api_token"

    code = "TQ"
    name = "ThinQ"
    category = ChannelType.Category.PHONE

    courier_url = r"^tq/(?P<uuid>[a-z0-9\-]+)/(?P<action>receive|status)$"
    schemes = [URN.TEL_SCHEME]

    claim_blurb = _(
        "If you have a number with %(link)s you can connect it in a few easy steps to automate your SMS numbers."
    ) % {"link": '<a target="_blank" href="https://thinq.com">ThinQ</a>'}
    claim_view = ClaimView

    config_ui = ConfigUI(
        blurb=_(
            "To finish configuring this channel, you'll need to set the following callback URLs on the ThinQ "
            "website on the SMS -> SMS Configuration page."
        ),
        endpoints=[
            ConfigUI.Endpoint(
                courier="receive",
                label=_("Inbound SMS Configuration"),
                help=_(
                    """Set your Inbound SMS Configuration URL to the above, making sure you select "URL" for Attachment Type."""
                ),
            ),
            ConfigUI.Endpoint(
                courier="status",
                label=_("Outbound SMS Configuration"),
                help=_(
                    """Set your Delivery Confirmation URL to the above, making sure you select "Form-Data" as the Delivery Notification Format."""
                ),
            ),
        ],
        show_public_ips=True,
    )

    def is_available_to(self, org, user):
        region_aware_visible, region_ignore_visible = super().is_available_to(org, user)
        countrycode = timezone_to_country_code(org.timezone)
        region_aware_visible = countrycode in ["US"]
        return region_aware_visible, region_ignore_visible

    def is_recommended_to(self, org, user):
        return False
