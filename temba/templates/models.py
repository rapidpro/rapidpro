import regex

from django.contrib.postgres.fields import JSONField
from django.db import models

from temba.channels.models import Channel
from temba.orgs.models import Org
from temba.utils.models import TembaModel


class Template(TembaModel):
    """
    Templates represent messages that can be used in flows and have template variables substituted into them. These
    are usually used by WhatsApp channels, but can also be used more generically to create DRY messages in flows.
    """

    # the name of this template
    name = models.CharField(null=False, max_length=64)

    # the slug for this template (generated from name on creation then permanent)
    slug = models.CharField(null=False, max_length=64)

    # the message for this template keyed by language code
    message = JSONField(null=False)

    # the organization this template is used in
    org = models.ForeignKey(Org, on_delete=models.PROTECT)

    @classmethod
    def make_slug(cls, name):
        slug = regex.sub(r"([^a-z0-9]+)", " ", name.lower(), regex.V0)
        return regex.sub(r"([^a-z0-9]+)", "_", slug.strip(), regex.V0)

    class Meta:
        unique_together = ("org", "slug")


class ChannelTemplate(models.Model):
    """
    ChannelTemplate represents a template that must be synced to a specific channel. It maintains both the external
    id for the channel template as well as the current status.
    """

    STATUS_SYNCED = "S"
    STATUS_PENDING = "P"

    STATUS_CHOICES = ((STATUS_SYNCED, "Synced"), (STATUS_PENDING, "Pending"))

    # the current status of this channel template
    status = models.CharField(max_length=1, choices=STATUS_CHOICES, default=STATUS_PENDING, null=False)

    # the template this maps to
    template = models.ForeignKey(Template, on_delete=models.PROTECT)

    # the channel that synced this template
    channel = models.ForeignKey(Channel, on_delete=models.PROTECT)

    # the external id for this channel template
    external_id = models.CharField(null=True, max_length=64)
