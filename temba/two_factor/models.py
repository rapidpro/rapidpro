from base64 import b32encode
from os import urandom

from smartmin.models import SmartModel

from django.db import models
from django.utils.translation import ugettext_lazy as _

from temba.orgs.models import UserSettings


def generate_token():  # pragma: no cover
    return b32encode(urandom(5)).decode("utf-8").lower()


class BackupToken(SmartModel):
    settings = models.ForeignKey(
        UserSettings, verbose_name=_("Settings"), related_name="backups", on_delete=models.CASCADE
    )
    token = models.CharField(verbose_name=_("Token"), max_length=18, unique=True, default=generate_token)
    used = models.BooleanField(verbose_name=_("Used"), default=False)

    def __str__(self):
        return f"{self.token}"
