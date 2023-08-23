from django.utils.translation import gettext_lazy as _

from temba.contacts.models import URN

from ...models import ChannelType, ConfigUI
from .views import ClaimView


class MtargetType(ChannelType):
    """
    An Mtarget channel type (https://www.mtarget.fr/)
    """

    code = "MT"
    category = ChannelType.Category.PHONE

    courier_url = r"^mt/(?P<uuid>[a-z0-9\-]+)/(?P<action>status|receive)$"

    name = "Mtarget"

    available_timezones = ["Africa/Kigali", "Africa/Yaoundé", "Africa/Porto-Novo", "Africa/Kinshasa", "Europe/Paris"]
    recommended_timezones = ["Africa/Kigali", "Africa/Yaoundé", "Africa/Porto-Novo", "Africa/Kinshasa", "Europe/Paris"]

    schemes = [URN.TEL_SCHEME]
    max_length = 765

    claim_view = ClaimView
    claim_blurb = _("If you have an %(link)s account, you can quickly connect it using their APIs.") % {
        "link": '<a target="_blank" href="https://www.mtarget.fr/">Mtarget</a>'
    }

    configuration_blurb = _(
        "To finish connecting your channel, you need to have Mtarget configure the URLs below for your Service ID."
    )
    config_ui = ConfigUI(
        endpoints=[
            ConfigUI.Endpoint(courier="receive", label=_("Receive URL")),
            ConfigUI.Endpoint(courier="status", label=_("Status URL")),
        ]
    )
