from django.utils.translation import gettext_lazy as _

from temba.channels.types.chikka.views import ClaimView
from temba.contacts.models import URN

from ...models import ChannelType


class ChikkaType(ChannelType):
    """
    An Chikka channel (http://www.jasminsms.com/)
    """

    code = "CK"
    category = ChannelType.Category.PHONE

    courier_url = r"^ck/(?P<uuid>[a-z0-9\-]+)/receive$"

    name = "Chikka"

    claim_blurb = _(
        "If you are based in the Phillipines, you can integrate with Chikka to send and receive "
        "messages on your short code."
    )
    claim_view = ClaimView

    schemes = [URN.TEL_SCHEME]
    max_length = 160

    configuration_blurb = _(
        "To finish configuring your Chikka connection you need to set the following URLs in your "
        "Chikka account API settings."
    )

    configuration_urls = (
        dict(
            label=_("Notification Receiver URL"),
            url="https://{{ channel.callback_domain }}{% url 'courier.ck' channel.uuid %}",
        ),
        dict(
            label=_("Message Receiver URL"),
            url="https://{{ channel.callback_domain }}{% url 'courier.ck' channel.uuid %}",
        ),
    )

    available_timezones = ["Asia/Manila"]
