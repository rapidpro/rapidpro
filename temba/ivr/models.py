from datetime import timedelta

from django.contrib.postgres.fields import ArrayField
from django.core.exceptions import ObjectDoesNotExist
from django.db import models
from django.db.models import Q
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from temba.channels.models import Channel, ChannelLog
from temba.contacts.models import Contact, ContactURN
from temba.orgs.models import Org


class Call(models.Model):
    """
    An IVR call
    """

    DIRECTION_IN = "I"
    DIRECTION_OUT = "O"
    DIRECTION_CHOICES = ((DIRECTION_IN, _("Incoming")), (DIRECTION_OUT, _("Outgoing")))

    STATUS_PENDING = "P"  # used for initial creation in database
    STATUS_QUEUED = "Q"  # used when we need to throttle requests for new calls
    STATUS_WIRED = "W"  # the call has been requested on the IVR provider
    STATUS_IN_PROGRESS = "I"  # the call has been answered
    STATUS_COMPLETED = "D"  # the call was completed successfully
    STATUS_ERRORED = "E"  # temporary failure (will be retried)
    STATUS_FAILED = "F"  # permanent failure
    STATUS_CHOICES = (
        (STATUS_PENDING, _("Pending")),
        (STATUS_QUEUED, _("Queued")),
        (STATUS_WIRED, _("Wired")),
        (STATUS_IN_PROGRESS, _("In Progress")),
        (STATUS_COMPLETED, _("Complete")),
        (STATUS_ERRORED, _("Errored")),
        (STATUS_FAILED, _("Failed")),
    )

    ERROR_PROVIDER = "P"
    ERROR_BUSY = "B"
    ERROR_NOANSWER = "N"
    ERROR_MACHINE = "M"
    ERROR_CHOICES = (
        (ERROR_PROVIDER, _("Provider")),  # an API call to the IVR provider returned an error
        (ERROR_BUSY, _("Busy")),  # the contact couldn't be called because they're busy
        (ERROR_NOANSWER, _("No Answer")),  # the contact didn't answer the call
        (ERROR_MACHINE, _("Answering Machine")),  # the call went to an answering machine
    )

    RETRY_CHOICES = ((-1, _("Never")), (30, _("After 30 minutes")), (60, _("After 1 hour")), (1440, _("After 1 day")))

    org = models.ForeignKey(Org, on_delete=models.PROTECT, related_name="calls")
    direction = models.CharField(max_length=1, choices=DIRECTION_CHOICES)
    status = models.CharField(max_length=1, choices=STATUS_CHOICES)

    channel = models.ForeignKey(Channel, on_delete=models.PROTECT, related_name="calls")
    contact = models.ForeignKey(Contact, on_delete=models.PROTECT, related_name="calls")
    contact_urn = models.ForeignKey(ContactURN, on_delete=models.PROTECT, related_name="calls")
    external_id = models.CharField(max_length=255)  # e.g. Twilio call ID

    created_on = models.DateTimeField(default=timezone.now)
    modified_on = models.DateTimeField(default=timezone.now)
    started_on = models.DateTimeField(null=True)
    ended_on = models.DateTimeField(null=True)
    duration = models.IntegerField(null=True)  # in seconds

    error_reason = models.CharField(max_length=1, null=True, choices=ERROR_CHOICES)
    error_count = models.IntegerField(default=0)
    next_attempt = models.DateTimeField(null=True)

    log_uuids = ArrayField(models.UUIDField(), null=True)

    def get_duration(self) -> timedelta:
        """
        Either gets the set duration as reported by provider, or tries to calculate it
        """
        duration = self.duration or 0

        if not duration and self.status == self.STATUS_IN_PROGRESS and self.started_on:
            duration = (timezone.now() - self.started_on).seconds

        return timedelta(seconds=duration)

    @property
    def status_display(self) -> str:
        """
        Gets the status/error_reason as display text, e.g. Wired, Errored (No Answer)
        """
        status = self.get_status_display()
        if self.status in (self.STATUS_ERRORED, self.STATUS_FAILED) and self.error_reason:
            status += f" ({self.get_error_reason_display()})"
        return status

    def get_session(self):
        """
        There is a one-to-one relationship between flow sessions and call, but as call can be null
        it can throw an exception
        """
        try:
            return self.session
        except ObjectDoesNotExist:  # pragma: no cover
            return None

    def get_logs(self) -> list:
        return ChannelLog.get_by_uuid(self.channel, self.log_uuids or [])

    def release(self):
        session = self.get_session()
        if session:
            session.delete()

        self.delete()

    class Meta:
        indexes = [
            # used to list calls in UI
            models.Index(name="calls_org_created_on", fields=["org", "-created_on"]),
            # used by mailroom to fetch calls that need to be retried
            models.Index(
                name="calls_to_retry",
                fields=["next_attempt"],
                condition=Q(status__in=("Q", "E"), next_attempt__isnull=False),
            ),
        ]
