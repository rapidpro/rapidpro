from django.utils.translation import ugettext_lazy as _

from ...models import NLPProviderType
from .views import WitView


class WitType(NLPProviderType):
    """
    The Wit Nlp Type Provider
    """

    name = "Wit.ai"

    slug = "wt"
    code = "WT"
    category = NLPProviderType.Category.WIT

    icon = "icon-channel-external"

    show_config_page = True

    claim_blurb = _(
        "Wit.ai makes it easy for developers to build applications and devices that you can talk or text to"
    )
    claim_view = WitView

    configuration_blurb = _("Add Some text")
