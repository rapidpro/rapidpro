from gettext import gettext as _

from django.db import models
from django.utils import timezone


class Apk(models.Model):
    DOWNLOAD_EXPIRES = 60 * 60 * 24  # Up to 24 hours

    TYPE_RELAYER = "R"
    TYPE_MESSAGE_PACK = "M"

    TYPE_CHOICES = (
        (TYPE_RELAYER, _("Relayer Application APK")),
        (TYPE_MESSAGE_PACK, _("Message Pack Application APK")),
    )

    apk_type = models.CharField(choices=TYPE_CHOICES, max_length=1)

    name = models.CharField(
        verbose_name=_("Name"),
        max_length=64,
        blank=True,
        null=True,
        help_text=_("Descriptive label for this application APK"),
    )

    apk_file = models.FileField(upload_to="apks")

    created_on = models.DateTimeField(default=timezone.now)

    description = models.TextField(null=True, blank=True, default="")
