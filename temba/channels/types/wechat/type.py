from django.utils.translation import gettext_lazy as _

from temba.contacts.models import URN

from ...models import ChannelType, ConfigUI
from .views import ClaimView


class WeChatType(ChannelType):
    """
    A WeChat channel (https://www.wechat.com)
    """

    code = "WC"
    name = "WeChat"
    category = ChannelType.Category.SOCIAL_MEDIA

    courier_url = r"^wc/(?P<uuid>[a-z0-9\-]+)/?$"
    schemes = [URN.WECHAT_SCHEME]

    claim_blurb = _(
        "Add a %(link)s bot to send and receive messages to WeChat users for free. Your users will need an Android, "
        "Windows or iOS device and a WeChat account to send and receive messages."
    ) % {"link": '<a target="_blank" href="https://wechat.com">WeChat</a>'}
    claim_view = ClaimView

    config_ui = ConfigUI(
        blurb=_(
            "To finish configuring this channel, you'll need to enter the following webhook URL and token on "
            "WeChat Official Accounts Platform."
        ),
        endpoints=[
            ConfigUI.Endpoint(courier="", label=_("Webhook URL")),
        ],
        show_secret=True,
        show_public_ips=True,
    )
