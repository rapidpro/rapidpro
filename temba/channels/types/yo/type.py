from django.utils.translation import ugettext_lazy as _

from temba.channels.types.yo.views import ClaimView
from temba.contacts.models import URN

from ...models import ChannelType


class YoType(ChannelType):
    """
    An Yo! channel (http://www.yo.co.ug/)
    """

    code = "YO"
    category = ChannelType.Category.PHONE

    courier_url = r"^yo/(?P<uuid>[a-z0-9\-]+)/(?P<action>receive)$"

    name = "YO!"
    slug = "yo"

    schemes = [URN.TEL_SCHEME]
    max_length = 1600
    attachment_support = False

    claim_view = ClaimView
    claim_blurb = _(
        "If you are based in Uganda, you can integrate with %(link)s to send and receive messages on your shortcode."
    ) % {"link": '<a href="http://www.yo.co.ug/">Yo!</a>'}

    configuration_blurb = _(
        "To finish configuring your Yo! connection you'll need to notify Yo! of the following inbound SMS URL."
    )

    configuration_urls = (
        dict(
            label=_("Inbound SMS URL"),
            url="https://{{ channel.callback_domain }}{% url 'courier.yo' channel.uuid 'receive' %}",
            description=_(
                "This URL should be called with a GET by Yo! when new incoming messages are received on your shortcode."
            ),
        ),
    )

    def is_available_to(self, user):
        org = user.get_org()
        return org.timezone and str(org.timezone) in ["Africa/Kampala"]

    def is_recommended_to(self, user):
        return self.is_available_to(user)
