from django.db import models
from django.utils.translation import gettext_lazy as _

from temba.channels.models import ChannelConnection


class IVRManager(models.Manager):
    def create(self, *args, **kwargs):
        return super().create(*args, connection_type=IVRCall.TYPE_VOICE, **kwargs)

    def get_queryset(self):
        return super().get_queryset().filter(connection_type=IVRCall.TYPE_VOICE)


class IVRCall(ChannelConnection):
    RETRY_CHOICES = ((-1, _("Never")), (30, _("After 30 minutes")), (60, _("After 1 hour")), (1440, _("After 1 day")))

    objects = IVRManager()

    class Meta:
        proxy = True
