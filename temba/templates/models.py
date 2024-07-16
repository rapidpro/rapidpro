from django.db import models
from django.db.models import Count
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from temba.channels.models import Channel
from temba.orgs.models import DependencyMixin, Org
from temba.utils.languages import alpha2_to_alpha3
from temba.utils.models import TembaModel, update_if_changed


class TemplateType:
    slug: str
    variable_regex: str

    def update_local(self, channel, raw: dict):  # pragma: no cover
        pass

    def _extract_variables(self, text: str) -> list:
        return list(sorted({m for m in self.variable_regex.findall(text)}))

    def _parse_language(self, lang: str) -> str:
        """
        Converts a WhatsApp language code which can be alpha2 ('en') or alpha2_country ('en_US') or alpha3 ('fil')
        to our locale format ('eng' or 'eng-US').
        """
        language, country = lang.split("_") if "_" in lang else [lang, None]
        if len(language) == 2:
            language = alpha2_to_alpha3(language)

        return f"{language}-{country}" if country else language


class Template(TembaModel, DependencyMixin):
    """
    Templates represent messages that can be used in flows and have template variables substituted into them. These
    are currently only used for WhatsApp channels.
    """

    org = models.ForeignKey(Org, on_delete=models.PROTECT, related_name="templates")
    name = models.CharField(max_length=512)  # overridden to be longer
    base_translation = models.OneToOneField(
        "templates.TemplateTranslation", on_delete=models.PROTECT, related_name="base_template", null=True
    )

    @classmethod
    def get_or_create(cls, org, name: str):
        obj, created = cls.objects.get_or_create(
            org=org, name=name, defaults={"created_by": org.created_by, "modified_by": org.created_by}
        )
        return obj

    def is_approved(self):
        """
        Returns whether this template has at least one translation and all are approved
        """
        translations = self.translations.all()
        if len(translations) == 0:
            return False

        for tr in translations:
            if tr.status != TemplateTranslation.STATUS_APPROVED:
                return False

        return True

    @classmethod
    def annotate_usage(cls, queryset):
        qs = super().annotate_usage(queryset)

        return qs.annotate(
            locale_count=Count("translations__locale", distinct=True),
            channel_count=Count("translations__channel", distinct=True),
        )

    def update_base(self, exclude=None):
        """
        Tries to set a new base translation for this template from its available translations.
        """
        candidates = self.translations.exclude(id=exclude.id) if exclude else self.translations.all()

        # try to find one in the org's primary language
        new_base = candidates.filter(locale__startswith=self.org.flow_languages[0]).first()
        if not new_base:
            # if not fallback to oldest
            new_base = candidates.order_by("id").first()

        if self.base_translation != new_base:
            self.base_translation = new_base
            self.modified_on = timezone.now()
            self.save(update_fields=("base_translation", "modified_on"))

            self.rebase()

    def rebase(self):
        if self.base_translation:
            self.base_translation.refresh_from_db()

        # update translations compatibility with the base variable list
        for trans in self.translations.all():
            trans.is_compatible = trans.variables == self.variables
            trans.save(update_fields=("is_compatible",))

    @property
    def variables(self):
        return self.base_translation.variables if self.base_translation else []

    class Meta:
        unique_together = ("org", "name")


class TemplateTranslation(models.Model):
    """
    TemplateTranslation represents a translation for a template and channel pair.
    """

    STATUS_PENDING = "P"
    STATUS_APPROVED = "A"
    STATUS_REJECTED = "R"
    STATUS_PAUSED = "U"
    STATUS_DISABLED = "D"
    STATUS_IN_APPEAL = "I"
    STATUS_CHOICES = (
        (STATUS_PENDING, _("Pending")),
        (STATUS_APPROVED, _("Approved")),
        (STATUS_REJECTED, _("Rejected")),
        (STATUS_PAUSED, _("Paused")),
        (STATUS_DISABLED, _("Disabled")),
        (STATUS_IN_APPEAL, _("In Appeal")),
    )

    template = models.ForeignKey(Template, on_delete=models.PROTECT, related_name="translations")
    channel = models.ForeignKey(Channel, on_delete=models.PROTECT, related_name="template_translations")
    locale = models.CharField(max_length=6)  # e.g. eng-US

    components = models.JSONField(default=list)
    variables = models.JSONField(default=list)
    status = models.CharField(max_length=1, choices=STATUS_CHOICES, default=STATUS_PENDING)
    namespace = models.CharField(max_length=36, default="")
    external_id = models.CharField(null=True, max_length=64)
    external_locale = models.CharField(null=True, max_length=6)  # e.g. en_US
    is_supported = models.BooleanField(default=True)  # whether all components are supported
    is_compatible = models.BooleanField(default=True)  # whether parameters match those of template base translation

    @classmethod
    def update_local(cls, channel, raw_templates: list):
        """
        Updates the local translations against the fetched raw templates from the given channel
        """

        seen_ids = []
        templates = set()

        for raw_template in raw_templates:
            translation = channel.template_type.update_local(channel, raw_template)
            if translation:
                seen_ids.append(translation.id)
                templates.add(translation.template)

        # delete any template translations we didn't see
        for gone in channel.template_translations.exclude(id__in=seen_ids):
            gone.delete()

        # if any templates don't have a base translation update them
        for template in templates:
            if not template.base_translation:
                template.update_base()

    @classmethod
    def get_or_create(
        cls,
        channel,
        name,
        locale,
        *,
        status,
        external_id,
        external_locale,
        namespace: str,
        components: list,
        variables: list,
        is_supported: bool,
    ):
        # get the template with this name
        template = Template.get_or_create(channel.org, name)

        # look for an existing translation for this channel / locale pair
        existing = template.translations.filter(channel=channel, locale=locale).first()

        if existing:
            # update existing translation if necessary
            changed = update_if_changed(
                existing,
                components=components,
                variables=variables,
                status=status,
                namespace=namespace,
                external_id=external_id,
                external_locale=external_locale,
                is_supported=is_supported,
                is_compatible=existing.is_base() or template.variables == variables,
            )
        else:
            # create new translation
            existing = cls.objects.create(
                template=template,
                channel=channel,
                locale=locale,
                components=components,
                variables=variables,
                status=status,
                namespace=namespace,
                external_id=external_id,
                external_locale=external_locale,
                is_supported=is_supported,
                is_compatible=template.variables == variables,
            )
            changed = True

        # mark template as modified if we made translation changes
        if changed:
            existing.template.modified_on = timezone.now()
            existing.template.save(update_fields=("modified_on",))

            if existing.is_base():
                existing.template.rebase()

        return existing

    def is_base(self):
        return self.template.base_translation == self

    def delete(self):
        """
        Overriden so that if this translation is the base translation for a template, we find a new base for that template
        """

        try:
            self.base_template.update_base(exclude=self)
        except Template.DoesNotExist:
            pass

        super().delete()

    def __repr__(self):  # pragma: no cover
        return f'<TemplateTranslation: id={self.id} channel="{self.channel.name}" locale="{self.locale}">'

    class Meta:
        constraints = [
            # used to prevent adding duplicate translations for the same channel and locale
            models.UniqueConstraint(name="templatetranslations_unique", fields=("template", "channel", "locale"))
        ]
