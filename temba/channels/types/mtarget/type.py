from django.utils.translation import ugettext_lazy as _

from temba.contacts.models import TEL_SCHEME

from ...models import ChannelType
from .views import ClaimView


class MtargetType(ChannelType):
    """
    An Mtarget channel type (https://www.mtarget.fr/)
    """

    code = "MT"
    category = ChannelType.Category.PHONE

    courier_url = r"^mt/(?P<uuid>[a-z0-9\-]+)/(?P<action>status|receive)$"

    name = "Mtarget"
    icon = "icon-mtarget"

    available_timezones = ["Africa/Kigali", "Africa/Yaoundé", "Africa/Porto-Novo", "Africa/Kinshasa", "Europe/Paris"]
    recommended_timezones = ["Africa/Kigali", "Africa/Yaoundé", "Africa/Porto-Novo", "Africa/Kinshasa", "Europe/Paris"]

    schemes = [TEL_SCHEME]
    max_length = 765
    attachment_support = False

    claim_view = ClaimView
    claim_blurb = _(
        """
        If you have an <a href="https://www.mtarget.fr/">Mtarget</a> account,
        you can quickly connect it using their APIs.
        """
    )

    configuration_blurb = _(
        """
        To finish connecting your channel, you need to have Mtarget configure the URLs below for your Service ID.
        """
    )

    configuration_urls = (
        dict(
            label=_("Receive URL"),
            url="https://{{channel.callback_domain}}{% url 'courier.mt' channel.uuid 'receive' %}",
        ),
        dict(
            label=_("Status URL"),
            url="https://{{channel.callback_domain}}{% url 'courier.mt' channel.uuid 'status' %}",
        ),
    )
