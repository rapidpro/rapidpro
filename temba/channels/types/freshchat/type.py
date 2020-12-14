from django.utils.translation import ugettext_lazy as _

from temba.contacts.models import URN

from ...models import ChannelType
from .views import ClaimView


class FreshChatType(ChannelType):
    """
    An FreshChat channel
    """

    code = "FC"
    category = ChannelType.Category.API

    courier_url = r"^fc/(?P<uuid>[a-z0-9\-]+)/receive$"

    name = "FreshChat"
    icon = "icon-freshchat"

    claim_blurb = _("Connect your approved %(link)s channel") % {
        "link": '<a href="https://www.freshworks.com/live-chat-software/">FreshChat</a>'
    }
    claim_view = ClaimView

    schemes = [URN.FRESHCHAT_SCHEME]
    attachment_support = True
    free_sending = True

    configuration_blurb = _(
        "To use your FreshChat channel you'll have to configure the FreshChat server to direct "
        "messages to the url below."
    )

    configuration_urls = (
        dict(
            label=_("Receive URL"),
            url="https://{{ channel.callback_domain }}{% url 'courier.fc' channel.uuid %}",
            description=_("POST FreshChat trigger to this address."),
        ),
    )
