from django.utils.translation import ugettext_lazy as _

from temba.channels.types.jasmin.views import ClaimView
from temba.contacts.models import TEL_SCHEME

from ...models import ChannelType


class JasminType(ChannelType):
    """
    An Jasmin channel (http://www.jasminsms.com/)
    """

    code = "JS"
    category = ChannelType.Category.PHONE

    courier_url = r"^js/(?P<uuid>[a-z0-9\-]+)/(?P<action>status|receive)$"

    name = "Jasmin"

    claim_blurb = _(
        """Connect your <a href="http://www.jasminsms.com/">Jasmin</a> instance that you have
                       already connected to an SMSC."""
    )
    claim_view = ClaimView

    schemes = [TEL_SCHEME]
    max_length = 1600
    attachment_support = False

    configuration_blurb = _(
        """
        As a last step you'll need to configure Jasmin to call the following URL for MO (incoming) messages.
        """
    )

    configuration_urls = (
        dict(
            label=_("Push Message URL"),
            url="https://{{ channel.callback_domain }}{% url 'courier.js' channel.uuid 'receive' %}",
            description=_(
                "    This endpoint will be called by Jasmin when new messages are received to your number, it must be configured to be called as a POST"
            ),
        ),
    )
