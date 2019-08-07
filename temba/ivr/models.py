import logging
from datetime import timedelta

from django.core.exceptions import ObjectDoesNotExist
from django.db import models
from django.utils import timezone
from django.utils.translation import ugettext_lazy as _

from temba.channels.models import ChannelConnection, ChannelLog

logger = logging.getLogger(__name__)


class IVRManager(models.Manager):
    def create(self, *args, **kwargs):
        return super().create(*args, connection_type=IVRCall.IVR, **kwargs)

    def get_queryset(self):
        return super().get_queryset().filter(connection_type__in=[IVRCall.IVR, IVRCall.VOICE])


class IVRCall(ChannelConnection):
    RETRY_BACKOFF_MINUTES = 60
    MAX_RETRY_ATTEMPTS = 3
    IGNORE_PENDING_CALLS_OLDER_THAN_DAYS = 7  # calls with modified_on older than 7 days are going to be ignored
    DEFAULT_MAX_IVR_EXPIRATION_WINDOW_DAYS = 7

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

    def get_duration(self):
        """
        Either gets the set duration as reported by provider, or tries to calculate
        it from the approximate time it was started
        """
        duration = self.duration
        if not duration and self.status == self.IN_PROGRESS and self.started_on:
            duration = (timezone.now() - self.started_on).seconds

        if not duration:
            duration = 0

        return timedelta(seconds=duration)

    def has_logs(self):
        """
        Returns whether this IVRCall has any channel logs
        """
        return self.channel and self.channel.is_active and ChannelLog.objects.filter(connection=self).count() > 0

    def has_flow_session(self):
        """
        Checks whether this channel session has an associated flow session.
        See https://docs.djangoproject.com/en/2.1/topics/db/examples/one_to_one/
        """
        try:
            self.session
            return True
        except ObjectDoesNotExist:
            return False
