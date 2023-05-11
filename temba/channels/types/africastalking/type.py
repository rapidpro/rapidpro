from django.utils.translation import gettext_lazy as _

from temba.contacts.models import URN

from ...models import ChannelType
from .views import ClaimView


class AfricasTalkingType(ChannelType):
    """
    An Africa's Talking channel (https://africastalking.com/)
    """

    code = "AT"
    category = ChannelType.Category.PHONE

    courier_url = r"^at/(?P<uuid>[a-z0-9\-]+)/(?P<action>receive|delivery|callback|status)$"

    name = "Africa's Talking"

    claim_blurb = _("You can purchase a short code from %(link)s and connect it in a few simple steps.") % {
        "link": """<a target="_blank" href="http://africastalking.com">Africa's Talking</a>"""
    }
    claim_view = ClaimView

    schemes = [URN.TEL_SCHEME]
    max_length = 160

    configuration_blurb = _(
        "To finish configuring your Africa's Talking connection you'll need to set the following callback URLs on the "
        "Africa's Talking website under your account."
    )

    configuration_urls = (
        dict(
            label=_("Callback URL"),
            url="https://{{ channel.callback_domain }}{% url 'courier.at' channel.uuid 'receive' %}",
            description=_(
                "You can set the callback URL on your Africa's Talking account by visiting the SMS Dashboard page, "
                "then clicking on Callback URL."
            ),
        ),
        dict(
            label=_("Delivery URL"),
            url="https://{{ channel.callback_domain }}{% url 'courier.at' channel.uuid 'status' %}",
            description=_(
                "You can set the delivery URL on your Africa's Talking account by visiting the SMS Dashboard page, "
                "then clicking on Delivery Reports."
            ),
        ),
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
