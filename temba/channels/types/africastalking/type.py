from django.utils.translation import gettext_lazy as _

from temba.contacts.models import URN

from ...models import ChannelType, ConfigUI
from .views import ClaimView


class AfricasTalkingType(ChannelType):
    """
    An Africa's Talking channel (https://africastalking.com/)
    """

    code = "AT"
    name = "Africa's Talking"
    category = ChannelType.Category.PHONE

    schemes = [URN.TEL_SCHEME]
    courier_url = r"^at/(?P<uuid>[a-z0-9\-]+)/(?P<action>receive|delivery|callback|status)$"

    claim_blurb = _("You can purchase a short code from %(link)s and connect it in a few simple steps.") % {
        "link": """<a target="_blank" href="http://africastalking.com">Africa's Talking</a>"""
    }
    claim_view = ClaimView

    config_ui = ConfigUI(
        blurb=_(
            "To finish configuring this channel, you'll need to add the following callback URLs to your account on the "
            "Africa's Talking website."
        ),
        endpoints=[
            ConfigUI.Endpoint(
                courier="receive",
                label=_("Callback URL"),
                help=_(
                    "You can set the callback URL on your Africa's Talking account by visiting the SMS Dashboard page, "
                    "then clicking on Callback URL."
                ),
            ),
            ConfigUI.Endpoint(
                courier="status",
                label=_("Delivery URL"),
                help=_(
                    "You can set the delivery URL on your Africa's Talking account by visiting the SMS Dashboard page, "
                    "then clicking on Delivery Reports."
                ),
            ),
        ],
    )

    available_timezones = [
        "Africa/Abidjan",
        "Africa/Accra",
        "Africa/Addis_Ababa",
        "Africa/Blantyre",
        "Africa/Dakar",
        "Africa/Dar_es_Salaam",
        "Africa/Douala",
        "Africa/Gabarone",
        "Africa/Harare",
        "Africa/Johannesburg",
        "Africa/Kampala",
        "Africa/Kigali",
        "Africa/Lagos",
        "Africa/Lusaka",
        "Africa/Maseru",
        "Africa/Mbabane",
        "Africa/Nairobi",
        "Africa/Ouagadougou",
        "Africa/Porto-Novo",
        "Africa/Windhoek",
    ]

    def is_recommended_to(self, org, user):
        return self.is_available_to(org, user)[0]
