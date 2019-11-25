from django.utils.translation import ugettext_lazy as _

from temba.channels.types.thinq.views import ClaimView
from temba.contacts.models import TEL_SCHEME
from temba.utils.timezones import timezone_to_country_code

from ...models import ChannelType


class ThinQType(ChannelType):
    """
    A ThinQ channel (https://thinq.com/)
    """

    code = "TQ"
    category = ChannelType.Category.PHONE

    courier_url = r"^tq/(?P<uuid>[a-z0-9\-]+)/(?P<action>receive|status)$"

    name = "ThinQ"
    icon = "icon-thinq"

    claim_blurb = _(
        """If you have a number with <a href="https://thinq.com">ThinQ</a> you can connect it in a few easy steps to
        automate your SMS numbers."""
    )
    claim_view = ClaimView

    show_public_addresses = True

    schemes = [TEL_SCHEME]
    max_length = 160
    attachment_support = False

    configuration_blurb = _(
        """
        To finish configuring your ThinQ connection you'll need to set the following callback URLs
        on the ThinQ website on the SMS -> SMS Configuration page.
        """
    )

    CONFIG_ACCOUNT_ID = "account_id"
    CONFIG_API_TOKEN_USER = "api_token_user"
    CONFIG_API_TOKEN = "api_token"

    configuration_urls = (
        dict(
            label=_("Inbound SMS Configuration"),
            url="https://{{ channel.callback_domain }}{% url 'courier.tq' channel.uuid 'receive' %}",
            description=_(
                """
                Set your Inbound SMS Configuration URL to the above, making sure you select "URL" for Attachment Type.
                """
            ),
        ),
        dict(
            label=_("Outbound SMS Configuration"),
            url="https://{{ channel.callback_domain }}{% url 'courier.tq' channel.uuid 'status' %}",
            description=_(
                """
                Set your Delivery Confirmation URL to the above, making sure you select "Form-Data" as the Delivery
                Notification Format.
                """
            ),
        ),
    )

    def is_available_to(self, user):
        org = user.get_org()
        countrycode = timezone_to_country_code(org.timezone)
        return countrycode in ["US"]

    def is_recommended_to(self, user):
        return False
