from abc import ABCMeta
from enum import Enum

from django.db import models
from django.utils.translation import ugettext_lazy as _

from temba.utils.models import TembaModel, JSONAsTextField
from temba.orgs.models import Org


class NLPProviderType(metaclass=ABCMeta):
    """
    Base class for all dynamic nlp providers types
    """

    class Category(Enum):
        BOTHUB = 1
        WIT = 2

    name = None

    slug = None
    code = None
    category = None
    icon = "icon-channel-external"

    show_config_page = True

    claim_blurb = None
    claim_view = None
    claim_view_kwargs = None

    configuration_blurb = None
    configuration_urls = None


class NLPProvider(TembaModel):

    name = models.CharField(
        verbose_name=_("Name"),
        max_length=64,
        blank=True,
        null=True,
        help_text=_("Descriptive label for this NLP Provider"),
    )

    nlp_type = models.CharField(verbose_name=_("NLP Provider Type"), max_length=3)

    config = JSONAsTextField(
        verbose_name=_("Config"),
        null=True,
        default=dict,
        help_text=_(
            "Any nlp provider specific configuration, used for the various aggregators"
        ),
    )

    org = models.ForeignKey(
        Org,
        on_delete=models.PROTECT,
        verbose_name=_("Org"),
        related_name="nlp_providers",
        blank=True,
        null=True,
        help_text=_("Organization using this Provider"),
    )
