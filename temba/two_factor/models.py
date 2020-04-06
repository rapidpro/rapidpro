from base64 import b32encode
from os import urandom

from smartmin.models import SmartModel

from django.contrib.auth.models import User
from django.db import models
from django.utils.translation import ugettext_lazy as _


def generate_token():  # pragma: no cover
    return b32encode(urandom(5)).decode("utf-8").lower()


class Profile(SmartModel):
    user = models.OneToOneField(User, verbose_name=_("User"), on_delete=models.CASCADE)
    otp_secret = models.CharField(verbose_name=_("OTP Secret"), max_length=18)
    two_factor_enabled = models.BooleanField(verbose_name=_("Two Factor Enabled"), default=False)

    def __str__(self):
        return f"{self.user.username}"


class BackupToken(SmartModel):
    profile = models.ForeignKey(Profile, verbose_name=_("Used"), related_name="backups", on_delete=models.CASCADE)
    token = models.CharField(verbose_name=_("Token"), max_length=18, default=generate_token)
    used = models.BooleanField(verbose_name=_("Used"), default=False)

    def __str__(self):
        return f"{self.token}"
