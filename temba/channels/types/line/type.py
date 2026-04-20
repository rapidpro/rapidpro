from django.utils.translation import gettext_lazy as _

from temba.contacts.models import URN

from ...models import ChannelType, ConfigUI
from .views import ClaimView


class LineType(ChannelType):
    """
    A LINE channel (https://line.me/)
    """

    code = "LN"
    name = "LINE"
    category = ChannelType.Category.SOCIAL_MEDIA

    unique_addresses = True

    courier_url = r"^ln/(?P<uuid>[a-z0-9\-]+)/receive$"
    schemes = [URN.LINE_SCHEME]

    claim_blurb = _(
        "Add a %(link)s bot to send and receive messages to LINE users for free. Your users will need an Android, "
        "Windows or iOS device and a LINE account to send and receive messages."
    ) % {"link": '<a target="_blank" href="https://line.me">LINE</a>'}
    claim_view = ClaimView

    config_ui = ConfigUI(show_public_ips=True)

    def get_error_ref_url(self, channel, code: str) -> str:
        return "https://developers.line.biz/en/reference/messaging-api/#error-responses"
