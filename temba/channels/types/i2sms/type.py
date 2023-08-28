from django.utils.translation import gettext_lazy as _

from temba.channels.types.i2sms.views import ClaimView
from temba.contacts.models import URN

from ...models import ChannelType, ConfigUI


class I2SMSType(ChannelType):
    """
    An I2SMS channel (https://www.i2sms.com/)
    """

    code = "I2"
    name = "I2SMS"
    category = ChannelType.Category.PHONE

    courier_url = r"^i2/(?P<uuid>[a-z0-9\-]+)/(?P<action>receive)$"
    schemes = [URN.TEL_SCHEME]

    claim_blurb = _("If you have a long number or short code with %(link)s you can connect it in a few easy steps.") % {
        "link": '<a target="_blank" href="https://www.i2sms.com/">I2SMS</a>'
    }
    claim_view = ClaimView

    config_ui = ConfigUI(
        blurb=_(
            "To finish configuring this channel, you'll need to set the message URL for the `DEFAULT` keyword as "
            "below."
        ),
        endpoints=[
            ConfigUI.Endpoint(
                courier="receive",
                label=_("Message URL"),
                help=_(
                    """You can set your message URL by visiting the <a href="https://mx.i2sms.net/">I2SMS Dashboard</a>, """
                    """creating a DEFAULT keyword and using this URL as your message URL. """
                    """Select POST HTTP Variables and check the box for "No URL Output"."""
                ),
            ),
        ],
    )
