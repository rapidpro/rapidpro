from django.db import models
from django.utils.translation import ugettext_lazy as _

from temba.channels.models import ChannelConnection


class IVRManager(models.Manager):
    def create(self, *args, **kwargs):
        return super().create(*args, connection_type=IVRCall.IVR, **kwargs)

    def get_queryset(self):
        return super().get_queryset().filter(connection_type__in=[IVRCall.IVR, IVRCall.VOICE])


class IVRCall(ChannelConnection):
    IVR_EXPIRES_CHOICES = (
        (1, _("After 1 minute")),
        (2, _("After 2 minutes")),
        (3, _("After 3 minutes")),
        (4, _("After 4 minutes")),
        (5, _("After 5 minutes")),
        (10, _("After 10 minutes")),
        (15, _("After 15 minutes")),
    )

    IVR_RETRY_CHOICES = ((30, _("After 30 minutes")), (60, _("After 1 hour")), (1440, _("After 1 day")))

    objects = IVRManager()

    class Meta:
        proxy = True
