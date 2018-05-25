from django.utils.translation import ugettext_lazy as _

from temba.channels.views import AuthenticatedExternalCallbackClaimView
from temba.contacts.models import TEL_SCHEME

from ...models import ChannelType


class HighConnectionType(ChannelType):
    """
    An High Connection channel (http://www.highconnexion.com/en/)
    """

    code = "HX"
    category = ChannelType.Category.PHONE

    courier_url = r"^hx/(?P<uuid>[a-z0-9\-]+)/(?P<action>status|receive)$"

    name = "High Connection"
    slug = "high_connection"

    claim_blurb = _(
        """If you are based in France, you can purchase a number from High Connexion
                  <a href="http://www.highconnexion.com/en/">High Connection</a> and connect it in a few simple steps."""
    )
    claim_view = AuthenticatedExternalCallbackClaimView

    schemes = [TEL_SCHEME]
    max_length = 1500
    attachment_support = False

    configuration_blurb = _(
        """
        To finish configuring your connection you'll need to notify HighConnection of the following URL for incoming (MO) messages
        """
    )

    configuration_urls = (
        dict(
            label=_("Receive URL"),
            url="https://{{ channel.callback_domain }}{% url 'courier.hx' channel.uuid 'receive' %}",
        ),
    )

    def is_available_to(self, user):
        org = user.get_org()
        return org.timezone and str(org.timezone) in ["Europe/Paris"]
