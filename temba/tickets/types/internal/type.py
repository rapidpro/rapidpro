from django.utils.translation import ugettext_lazy as _

from ...models import TicketerType
from .views import ConnectView


class InternalType(TicketerType):
    """
    Type for using RapidPro itself as the ticketer.
    """

    name = "Internal"
    slug = "internal"
    icon = "icon-channel-external"

    connect_view = ConnectView
    connect_blurb = _("Enabling this will allow you to handle tickets within this application.")

    def is_available_to(self, user):
        return user.is_beta() and not user.get_org().ticketers.filter(ticketer_type=self.slug).exists()
