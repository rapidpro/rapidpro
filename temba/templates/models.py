from django.db import models
from django.db.models import Count, Q
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
            translation_count=Count("translations", filter=Q(translations__is_active=True)),
            channel_count=Count("translations__channel", filter=Q(translations__is_active=True), distinct=True),
        )

    class Meta:
        unique_together = ("org", "name")


class TemplateTranslation(models.Model):
    """
    TemplateTranslation represents a translation for a template and channel pair.
    """

    STATUS_APPROVED = "A"
    STATUS_PENDING = "P"
    STATUS_REJECTED = "R"
    STATUS_UNSUPPORTED = "X"
    STATUS_CHOICES = (
        (STATUS_APPROVED, _("Approved")),
        (STATUS_PENDING, _("Pending")),
        (STATUS_REJECTED, _("Rejected")),
        (STATUS_UNSUPPORTED, _("Unsupported")),
    )

    template = models.ForeignKey(Template, on_delete=models.PROTECT, related_name="translations")
    channel = models.ForeignKey(Channel, on_delete=models.PROTECT, related_name="template_translations")
    locale = models.CharField(max_length=6)  # e.g. eng-US

    components = models.JSONField(default=list)
    variables = models.JSONField(default=list)
    status = models.CharField(max_length=1, choices=STATUS_CHOICES, default=STATUS_PENDING, null=False)
    namespace = models.CharField(max_length=36, default="")
    external_id = models.CharField(null=True, max_length=64)
    external_locale = models.CharField(null=True, max_length=6)  # e.g. en_US
    is_active = models.BooleanField(default=True)

    @classmethod
    def update_local(cls, channel, raw_templates: list):
        """
        Updates the local translations against the fetched raw templates from the given channel
        """

        refreshed = []
        for raw_template in raw_templates:
            translation = channel.template_type.update_local(channel, raw_template)

            if translation:
                refreshed.append(translation)

        # delete any template translations we didn't see
        channel.template_translations.exclude(id__in=[tt.id for tt in refreshed]).delete()

        # TODO remove this once inactive translations are all hard deleted
        cls.objects.filter(channel=channel, id__in=[tc.id for tc in refreshed], is_active=False).update(is_active=True)

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
            )
            changed = True

        # mark template as modified if we made translation changes
        if changed:
            existing.template.modified_on = timezone.now()
            existing.template.save(update_fields=("modified_on",))

        return existing

    class Meta:
        constraints = [
            # used to prevent adding duplicate translations for the same channel and locale
            models.UniqueConstraint(name="templatetranslations_unique", fields=("template", "channel", "locale"))
        ]
