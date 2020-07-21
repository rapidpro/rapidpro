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

    name = "ClickMobile"
    icon = "icon-channel-external"

    courier_url = r"^cm/(?P<uuid>[a-z0-9\-]+)/(?P<action>receive)$"

    claim_blurb = _(
        """If you are based in Malawi or Ghana you can purchase a number
    from <a href="https://www.click-mobile.com/">ClickMobile</a> and connect it
    in a few simple steps."""
    )
    claim_view = ClaimView

    schemes = [TEL_SCHEME]
    max_length = 459
    attachment_support = False

    configuration_blurb = _(
        """
        To finish configuring your ClickMobile channel you need to set ClickMobile to send MO messages to the URL below.
        """
    )

    configuration_urls = (
        dict(
            label=_("Receive URL"),
            url="https://{{ channel.callback_domain }}{% url 'courier.cm' channel.uuid 'receive' %}",
        ),
    )
