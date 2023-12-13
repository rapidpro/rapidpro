import uuid

from django.db import models
from django.utils import timezone

from temba.channels.models import Channel
from temba.orgs.models import Org


class Template(models.Model):
    """
    Templates represent messages that can be used in flows and have template variables substituted into them. These
    are currently only used for WhatsApp channels.
    """

    # the uuid for this template
    uuid = models.UUIDField(default=uuid.uuid4)

    # the name of this template
    name = models.CharField(max_length=512)

    # the organization this template is used in
    org = models.ForeignKey(Org, on_delete=models.PROTECT, related_name="templates")

    # when this template was last modified
    modified_on = models.DateTimeField(default=timezone.now)

    # when this template was created
    created_on = models.DateTimeField(default=timezone.now)

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

    class Meta:
        unique_together = ("org", "name")


class TemplateTranslation(models.Model):
    """
    TemplateTranslation represents a translation for a template and channel pair.
    """

    STATUS_APPROVED = "A"
    STATUS_PENDING = "P"
    STATUS_REJECTED = "R"
    STATUS_UNSUPPORTED_COMPONENTS = "X"

    STATUS_CHOICES = (
        (STATUS_APPROVED, "approved"),
        (STATUS_PENDING, "pending"),
        (STATUS_REJECTED, "rejected"),
        (STATUS_UNSUPPORTED_COMPONENTS, "unsupported_components"),
    )

    template = models.ForeignKey(Template, on_delete=models.PROTECT, related_name="translations")
    channel = models.ForeignKey(Channel, on_delete=models.PROTECT, related_name="template_translations")

    namespace = models.CharField(max_length=36, default="")
    locale = models.CharField(null=True, max_length=6)  # e.g. eng-US
    content = models.TextField(null=True)
    components = models.JSONField(default=list)
    params = models.JSONField(default=dict)
    variable_count = models.IntegerField()
    status = models.CharField(max_length=1, choices=STATUS_CHOICES, default=STATUS_PENDING, null=False)

    external_id = models.CharField(null=True, max_length=64)
    external_locale = models.CharField(null=True, max_length=6)  # e.g. en_US

    is_active = models.BooleanField(default=True)

    @classmethod
    def trim(cls, channel, existing):
        """
        Trims what channel templates exist for this channel based on the set of templates passed in
        """
        ids = [tc.id for tc in existing]

        # mark any that weren't included as inactive
        TemplateTranslation.objects.filter(channel=channel).exclude(id__in=ids).update(is_active=False)

        # Make sure the seen one are active
        TemplateTranslation.objects.filter(channel=channel, id__in=ids, is_active=False).update(is_active=True)

    @classmethod
    def get_or_create(
        cls,
        channel,
        name,
        *,
        locale,
        content,
        variable_count,
        status,
        external_id,
        external_locale,
        namespace,
        components,
        params,
    ):
        existing = TemplateTranslation.objects.filter(channel=channel, external_id=external_id).first()

        if not existing:
            template = Template.objects.filter(org=channel.org, name=name).first()
            if not template:
                template = Template.objects.create(
                    org=channel.org, name=name, created_on=timezone.now(), modified_on=timezone.now()
                )
            else:
                template.modified_on = timezone.now()
                template.save(update_fields=["modified_on"])

            existing = TemplateTranslation.objects.create(
                template=template,
                channel=channel,
                namespace=namespace,
                locale=locale,
                content=content,
                variable_count=variable_count,
                components=components,
                params=params,
                status=status,
                external_id=external_id,
                external_locale=external_locale,
            )

        else:
            if (
                existing.status != status
                or existing.content != content
                or existing.locale != locale
                or existing.components != components
                or existing.params != params
            ):
                existing.namespace = namespace
                existing.locale = locale
                existing.status = status
                existing.content = content
                existing.variable_count = variable_count
                existing.is_active = True
                existing.components = components
                existing.params = params
                existing.external_locale = external_locale
                existing.save(
                    update_fields=[
                        "namespace",
                        "locale",
                        "status",
                        "content",
                        "is_active",
                        "variable_count",
                        "components",
                        "params",
                        "external_locale",
                    ]
                )

                existing.template.modified_on = timezone.now()
                existing.template.save(update_fields=["modified_on"])

        return existing

    def __str__(self):
        return f"{self.template.name} ({self.locale}) {self.status}: {self.content}"

    class Meta:
        indexes = [models.Index(name="templatetranslations_by_ext", fields=("channel", "external_id"))]
