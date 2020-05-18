from django.utils.translation import ugettext_lazy as _
from temba.contacts.models import EXTERNAL_SCHEME
from .views import ClaimView
from ...models import ChannelType
from ...views import UpdateWebSocketForm


class WebChatType(ChannelType):
    """
    A WebChat channel
    """

    code = "WCH"
    category = ChannelType.Category.API

    courier_url = r"^wch/(?P<uuid>[a-z0-9\-]+)/(?P<action>receive|register|referrer)$"

    name = _("WebChat Channel")
    icon = "icon-cloud"
    show_config_page = True
    show_edit_page = True

    update_form = UpdateWebSocketForm

    claim_blurb = _("Use our pluggable API to create a mobile-friendly web chat widget to add to any website.")
    claim_view = ClaimView

    schemes = [EXTERNAL_SCHEME]
    max_length = 2000
    attachment_support = True

    async_activation = False
    quick_reply_text_size = 50
