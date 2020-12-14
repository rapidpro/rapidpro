from django.utils.translation import ugettext_lazy as _

from temba.contacts.models import URN

from ...models import ChannelType
from .views import ClaimView


class JioChatType(ChannelType):
    """
    A JioChat channel (https://www.jiochat.com)
    """

    code = "JC"
    category = ChannelType.Category.SOCIAL_MEDIA

    courier_url = r"^jc/(?P<uuid>[a-z0-9\-]+)(/rcv/msg/message|/rcv/event/menu|/rcv/event/follow)?/?$"

    name = "JioChat"
    icon = "icon-jiochat"

    claim_blurb = _(
        "Add a %(link)s bot to send and receive messages to JioChat users for free. Your users will need an Android, "
        "Windows or iOS device and a JioChat account to send and receive messages."
    ) % {"link": '<a href="https://jiochat.me">JioChat</a>'}
    claim_view = ClaimView

    schemes = [URN.JIOCHAT_SCHEME]
    max_length = 1600
    attachment_support = False
    free_sending = True

    configuration_blurb = _(
        "To finish configuring your JioChat connection, you'll need to enter the following webhook URL and token on "
        "JioChat Developer Center configuration."
    )

    configuration_urls = (
        dict(label=_("Webhook URL"), url="https://{{ channel.callback_domain }}{% url 'courier.jc' channel.uuid %}"),
        dict(label=_("Token"), url="{{ channel.config.secret }}"),
    )
