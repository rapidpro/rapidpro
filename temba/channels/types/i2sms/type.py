from django.utils.translation import ugettext_lazy as _

from temba.channels.types.i2sms.views import ClaimView
from temba.contacts.models import TEL_SCHEME

from ...models import ChannelType


class I2SMSType(ChannelType):
    """
    An I2SMS channel (https://www.i2sms.com/)
    """

    code = "I2"
    category = ChannelType.Category.PHONE

    courier_url = r"^i2/(?P<uuid>[a-z0-9\-]+)/(?P<action>receive)$"

    name = "I2SMS"
    icon = "icon-channel-external"

    claim_blurb = _(
        """If you have a long number or shortcode with <a href="https://www.i2sms.com/">I2SMS</a> you can connect it in a few
        easy steps."""
    )
    claim_view = ClaimView

    schemes = [TEL_SCHEME]
    max_length = 160
    attachment_support = False

    configuration_blurb = _(
        """
        To finish configuring your I2SMS channel you'll need to set the message URL for the `DEFAULT` keyword as
        below.
        """
    )

    configuration_urls = (
        dict(
            label=_("Message URL"),
            url="https://{{ channel.callback_domain }}{% url 'courier.i2' channel.uuid 'receive' %}",
            description=_(
                """
                You can set your message URL by visiting the <a href="https://mx.i2sms.net/">I2SMS Dashboard</a>,
                creating a DEFAULT keyword and using this URL as your message URL. Select POST HTTP Variables
                and check the box for "No URL Output".
                """
            ),
        ),
    )
