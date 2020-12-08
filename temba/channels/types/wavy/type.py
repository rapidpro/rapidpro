from django.utils.translation import ugettext_lazy as _

from temba.contacts.models import URN

from ...models import ChannelType
from .views import ClaimView


class WavyType(ChannelType):
    """
    An Movile/Wavy channel type (https://wavy.global/en/)
    """

    code = "WV"
    name = "Movile/Wavy"
    available_timezones = [
        "America/Noronha",
        "America/Belem",
        "America/Fortaleza",
        "America/Recife",
        "America/Araguaina",
        "America/Maceio",
        "America/Bahia",
        "America/Sao_Paulo",
        "America/Campo_Grande",
        "America/Cuiaba",
        "America/Santarem",
        "America/Porto_Velho",
        "America/Boa_Vista",
        "America/Manaus",
        "America/Eirunepe",
        "America/Rio_Branco",
    ]
    category = ChannelType.Category.PHONE

    courier_url = r"^wv/(?P<uuid>[a-z0-9\-]+)/(?P<action>sent|delivered|receive)$"

    schemes = [URN.TEL_SCHEME]
    max_length = 160
    attachment_support = False

    claim_view = ClaimView
    claim_blurb = _("If you have an %(link)s number, you can quickly connect it using their APIs.") % {
        "link": '<a href="https://wavy.global/en/">Movile/Wavy</a>'
    }

    configuration_blurb = _(
        "To finish connecting your channel, you need to have Movile/Wavy configure the URL below for your number."
    )

    configuration_urls = (
        dict(
            label=_("Receive URL"),
            url="https://{{ channel.callback_domain }}{% url 'courier.wv' channel.uuid 'receive' %}",
            description=_("This URL should be called by Movile/Wavy when new messages are received."),
        ),
        dict(
            label=_("Sent URL"),
            url="https://{{ channel.callback_domain }}{% url 'courier.wv' channel.uuid 'sent' %}",
            description=_(
                "To receive the acknowledgement of sent messages, you need to set the Sent URL for your Movile/Wavy "
                "account."
            ),
        ),
        dict(
            label=_("Delivered URL"),
            url="https://{{ channel.callback_domain }}{% url 'courier.wv' channel.uuid 'delivered' %}",
            description=_(
                "To receive delivery of delivered messages, you need to set the Delivered URL for your Movile/Wavy "
                "account."
            ),
        ),
    )
