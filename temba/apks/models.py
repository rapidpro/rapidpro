from gettext import gettext as _

from markdown import markdown

from django.db import models
from django.utils import timezone
from django.utils.safestring import mark_safe


class Apk(models.Model):
    DOWNLOAD_EXPIRES = 60 * 60 * 24  # Up to 24 hours

    TYPE_RELAYER = "R"
    TYPE_MESSAGE_PACK = "M"

    TYPE_CHOICES = (
        (TYPE_RELAYER, _("Relayer Application APK")),
        (TYPE_MESSAGE_PACK, _("Message Pack Application APK")),
    )

    apk_type = models.CharField(choices=TYPE_CHOICES, max_length=1)

    apk_file = models.FileField(upload_to="apks")

    version = models.TextField(null=False, help_text="Our version, ex: 1.9.8")

    pack = models.IntegerField(
        null=True, blank=True, help_text="Our pack number if this is a message pack (otherwise blank)"
    )

    description = models.TextField(
        null=True, blank=True, default="", help_text="Changelog for this version, markdown supported"
    )

    created_on = models.DateTimeField(default=timezone.now)

    def markdown_description(self):
        return mark_safe(markdown(self.description))

    class Meta:
        unique_together = ("apk_type", "version", "pack")
