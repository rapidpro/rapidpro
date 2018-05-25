from django.utils.translation import ugettext_lazy as _

from temba.channels.types.chikka.views import ClaimView
from temba.contacts.models import TEL_SCHEME

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
        """If you are based in the Phillipines, you can integrate with Chikka to send
                       and receive messages on your shortcode."""
    )
    claim_view = ClaimView

    schemes = [TEL_SCHEME]
    max_length = 160
    attachment_support = False

    configuration_blurb = _(
        """
        To finish configuring your Chikka connection you need to set the following URLs in your Chikka account API settings.
        """
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

    def is_available_to(self, user):
        org = user.get_org()
        return org.timezone and str(org.timezone) in ["Asia/Manila"]
