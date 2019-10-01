from django.utils.translation import ugettext_lazy as _

from ...models import NLPProviderType
from .views import BothubView


class BothubType(NLPProviderType):
    """
    The BotHub Nlp Type Provider
    """

    name = "Bothub"

    slug = "bh"
    code = "BH"
    category = NLPProviderType.Category.BOTHUB

    icon = "icon-channel-external"

    show_config_page = True

    claim_blurb = _(
        "Collborative tool available in multiple languages that allow dataset translations and selection of different algorithms"
    )
    claim_view = BothubView

    configuration_blurb = _("Add Some text")
