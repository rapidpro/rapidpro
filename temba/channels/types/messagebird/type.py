from django.utils.translation import gettext_lazy as _

from temba.contacts.models import URN

from ...models import ChannelType
from .views import SUPPORTED_TIMEZONES, ClaimView


class MessageBirdType(ChannelType):
    """
    An MessageBird channel
    """

    code = "MBD"
    category = ChannelType.Category.PHONE

    courier_url = r"^mbd/(?P<uuid>[a-z0-9\-]+)/(?P<action>receive|status)$"

    name = "MessageBird"

    claim_blurb = _("Connect your approved %(link)s channel") % {
        "link": '<a target="_blank" href="https://www.messagebird.com/">Messagebird</a>'
    }
    claim_view = ClaimView

    beta_only = True

    schemes = [URN.TEL_SCHEME]

    available_timezones = SUPPORTED_TIMEZONES
    configuration_blurb = _(
        "To use your Messagebirld channel you'll have to configure the Messagebird to send raw  "
        "receivedSMS messages to the url below either with a flow or by registering the webhook with them"
        "Shortcodes don't work with flows and require a webhook."
        "Configure the status url under Developer Settings to receive status updates for your messages."
    )

    configuration_urls = (
        dict(
            label=_("Receive URL"),
            url="https://{{ channel.callback_domain }}{% url 'courier.mbd' channel.uuid 'receive'%}",
            description=_("Webhook address for inbmound messages to this address."),
            label=_("Status URL"),
            url="https://{{ channel.callback_domain }}{% url 'courier.mbd' channel.uuid 'status'%}",
            description=_("Webhook address for message status calls to this address."),
        ),
    )
