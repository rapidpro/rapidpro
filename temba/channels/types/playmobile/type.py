from django.utils.translation import ugettext_lazy as _

from temba.channels.models import ChannelType
from temba.channels.types.playmobile.views import ClaimView
from temba.contacts.models import TEL_SCHEME


class PlayMobileType(ChannelType):
    """
    A Play Mobile channel (http://playmobile.uz/)
    """

    courier_url = r"^pm/(?P<uuid>[a-z0-9\-]+)/(?P<action>receive)$"

    code = "PM"
    category = ChannelType.Category.PHONE

    name = "Play Mobile"
    available_timezones = ["Asia/Tashkent", "Asia/Samarkand"]

    claim_blurb = _(
        """If you are based in Uzbekistan, you can purchase a short code from <a href="http://playmobile.uz/">Play Mobile</a> and connect it in a few simple steps."""
    )
    claim_view = ClaimView

    schemes = [TEL_SCHEME]
    max_length = 160

    attachment_support = False

    configuration_blurb = _(
        """
        To finish configuring your Play Mobile connection you'll need to notify Play Mobile of the following URL.
        """
    )

    configuration_urls = (
        dict(
            label=_("Receive URL"),
            url="https://{{ channel.callback_domain }}{% url 'courier.pm' channel.uuid 'receive' %}",
            description=_(
                "To receive incoming messages, you need to set the receive URL for your Play Mobile account."
            ),
        ),
    )
