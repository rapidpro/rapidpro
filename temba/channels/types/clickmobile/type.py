from django.utils.translation import ugettext_lazy as _

from temba.channels.types.clickmobile.views import ClaimView
from temba.contacts.models import TEL_SCHEME

from ...models import ChannelType


class ClickMobileType(ChannelType):
    """
    A ClickMobile Channel Type https://www.click-mobile.com/
    """

    code = "CM"
    category = ChannelType.Category.PHONE

    name = "Click Mobile"
    icon = "icon-channel-external"

    courier_url = r"^cm/(?P<uuid>[a-z0-9\-]+)/(?P<action>receive)$"

    claim_blurb = _(
        """If you are based in Malawi or Ghana you can purchase a number from %(link)s and connect it in a few simple steps."""
    ) % {"link": '<a href="https://www.click-mobile.com/">Click Mobile</a>'}

    claim_view = ClaimView

    schemes = [TEL_SCHEME]
    max_length = 459
    attachment_support = False

    configuration_blurb = _(
        """To finish configuring your channel you need to configure Click Mobile to send new messages to the URL below."""
    )

    configuration_urls = (
        dict(
            label=_("Receive URL"),
            url="https://{{ channel.callback_domain }}{% url 'courier.cm' channel.uuid 'receive' %}",
        ),
    )

    def is_available_to(self, user):
        org = user.get_org()
        return org.timezone and str(org.timezone) in ["Africa/Accra", "Africa/Blantyre"]
