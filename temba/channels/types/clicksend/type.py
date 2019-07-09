from django.utils.translation import ugettext_lazy as _

from temba.channels.views import AuthenticatedExternalClaimView
from temba.contacts.models import TEL_SCHEME

from ...models import ChannelType


class ClickSendType(ChannelType):
    """
    A ClickSend channel (https://www.clicksend.com/)
    """

    code = "CS"
    name = "ClickSend"
    available_timezones = [
        "America/New_York",
        "America/Detroit",
        "America/Kentucky/Louisville",
        "America/Kentucky/Monticello",
        "America/Indiana/Indianapolis",
        "America/Indiana/Vincennes",
        "America/Indiana/Winamac",
        "America/Indiana/Marengo",
        "America/Indiana/Petersburg",
        "America/Indiana/Vevay",
        "America/Chicago",
        "America/Indiana/Tell_City",
        "America/Indiana/Knox",
        "America/Menominee",
        "America/North_Dakota/Center",
        "America/North_Dakota/New_Salem",
        "America/North_Dakota/Beulah",
        "America/Denver",
        "America/Boise",
        "America/Phoenix",
        "America/Los_Angeles",
        "America/Anchorage",
        "America/Juneau",
        "America/Sitka",
        "America/Metlakatla",
        "America/Yakutat",
        "America/Nome",
        "America/Adak",
        "Pacific/Honolulu",
        "US/Alaska",
        "US/Aleutian",
        "US/Arizona",
        "US/Central",
        "US/East-Indiana",
        "US/Eastern",
        "US/Hawaii",
        "US/Indiana-Starke",
        "US/Michigan",
        "US/Mountain",
        "US/Pacific",
    ]
    category = ChannelType.Category.PHONE
    schemes = [TEL_SCHEME]
    max_length = 1224
    attachment_support = False

    claim_view = AuthenticatedExternalClaimView
    claim_view_kwargs = {
        "username_label": _("API Username"),
        "username_help": _("Your API Username"),
        "password_label": _("API Password"),
        "password_help": _("Your API Password"),
        "form_blurb": _("You can connect your ClickSend number by entering the settings below."),
    }

    claim_blurb = _(
        """
        If you have a <a href="https://www.clicksend.com/">ClickSend</a> number,
        you can quickly connect it using their APIs.
        """
    )

    configuration_blurb = _(
        """
        To finish connecting your channel, you need to set your inbound SMS URL below for your number.
        """
    )

    configuration_urls = (
        dict(
            label=_("Receive URL"),
            url="https://{{channel.callback_domain}}/c/cs/{{channel.uuid}}/receive",
            description=_(
                "This URL should be called by ClickSend when new messages are received."
                "On your ClickSend dashboard, you can set this URL by going to SMS, then Settings, "
                "then the Inbound SMS Settings menu."
                "Add a new rule, select action URL, and use the URL above, then click save."
            ),
        ),
    )
