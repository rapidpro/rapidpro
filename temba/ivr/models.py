import logging
from datetime import timedelta

from django_redis import get_redis_connection

from django.core.exceptions import ObjectDoesNotExist
from django.db import models
from django.urls import reverse
from django.utils import timezone
from django.utils.translation import ugettext_lazy as _

from temba.channels.models import Channel, ChannelConnection, ChannelLog, ChannelType
from temba.utils.http import HttpEvent

logger = logging.getLogger(__name__)


class IVRManager(models.Manager):
    def create(self, *args, **kwargs):
        return super().create(*args, connection_type=IVRCall.IVR, **kwargs)

    def get_queryset(self):
        return super().get_queryset().filter(connection_type__in=[IVRCall.IVR, IVRCall.VOICE])


class IVRCall(ChannelConnection):
    RETRY_BACKOFF_MINUTES = 60
    MAX_RETRY_ATTEMPTS = 3
    MAX_ERROR_COUNT = 5
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

    @classmethod
    def create_outgoing(cls, channel, contact, contact_urn):
        return IVRCall.objects.create(
            channel=channel,
            contact=contact,
            contact_urn=contact_urn,
            direction=IVRCall.OUTGOING,
            org=channel.org,
            status=IVRCall.PENDING,
        )

    @classmethod
    def create_incoming(cls, channel, contact, contact_urn, external_id):
        return IVRCall.objects.create(
            channel=channel,
            contact=contact,
            contact_urn=contact_urn,
            direction=IVRCall.INCOMING,
            org=channel.org,
            external_id=external_id,
            status=IVRCall.PENDING,
        )

    def close(self):
        from temba.flows.models import FlowSession

        if not self.is_done():
            # mark us as interrupted
            self.status = ChannelConnection.INTERRUPTED
            self.ended_on = timezone.now()
            self.save(update_fields=("status", "ended_on"))

            self.unregister_active_event()

            if self.has_flow_session():
                self.session.end(FlowSession.STATUS_INTERRUPTED)

            client = self.channel.get_ivr_client()
            if client and self.external_id:
                client.hangup(self)

    def do_start_call(self, qs=None):
        client = self.channel.get_ivr_client()
        domain = self.channel.callback_domain

        from temba.ivr.clients import IVRException

        if client and domain:
            try:
                url = "https://%s%s" % (domain, reverse("ivr.ivrcall_handle", args=[self.pk]))
                if qs:  # pragma: no cover
                    url = "%s?%s" % (url, qs)

                tel_urn = self.contact_urn
                tel = tel_urn.path

                client.start_call(self, to=tel, from_=self.channel.address, status_callback=url)

            except IVRException as e:  # pragma: no cover
                logger.error(f"Could not start IVR call: {str(e)}", exc_info=True)

            except Exception as e:  # pragma: no cover
                logger.error(f"Could not start IVR call: {str(e)}", exc_info=True)

                ChannelLog.log_ivr_interaction(
                    self, "Call failed unexpectedly", HttpEvent(method="INTERNAL", url=None, response_body=str(e))
                )

                self.status = self.FAILED
                self.save(update_fields=("status",))

        # client or domain are not known
        else:
            ChannelLog.log_ivr_interaction(
                self,
                "Unknown client or domain",
                HttpEvent(method="INTERNAL", url=None, response_body=f"client={client} domain={domain}"),
            )

            self.status = self.FAILED
            self.save(update_fields=("status",))

    def schedule_call_retry(self, backoff_minutes: int):
        # retry the call if it has not been retried maximum number of times
        if self.retry_count < IVRCall.MAX_RETRY_ATTEMPTS:
            self.next_attempt = timezone.now() + timedelta(minutes=backoff_minutes)
            self.retry_count += 1
        else:
            self.next_attempt = None

    def schedule_failed_call_retry(self):
        if self.error_count < IVRCall.MAX_ERROR_COUNT:
            self.error_count += 1

    def update_status(self, status: str, duration: float, channel_type: str):
        """
        Updates our status from a provide call status string

        """
        if not status:
            raise ValueError(f"IVR Call status must be defined, got: '{status}'")

        previous_status = self.status

        from temba.flows.models import FlowRun

        ivr_protocol = Channel.get_type_from_code(channel_type).ivr_protocol
        if ivr_protocol == ChannelType.IVRProtocol.IVR_PROTOCOL_TWIML:
            self.status = self.derive_ivr_status_twiml(status, previous_status)
        elif ivr_protocol == ChannelType.IVRProtocol.IVR_PROTOCOL_NCCO:
            self.status = self.derive_ivr_status_nexmo(status, previous_status)
        else:  # pragma: no cover
            raise ValueError(f"Unhandled IVR protocol: {ivr_protocol}")

        # if we are in progress, mark our start time
        if self.status == self.IN_PROGRESS and previous_status != self.IN_PROGRESS:
            self.started_on = timezone.now()

        # if we are done, mark our ended time
        if self.status in ChannelConnection.DONE:
            self.ended_on = timezone.now()

            self.unregister_active_event()

            from temba.flows.models import FlowSession

            if self.has_flow_session():
                self.session.end(FlowSession.STATUS_COMPLETED)

        if self.status in ChannelConnection.RETRY_CALL and previous_status not in ChannelConnection.RETRY_CALL:
            flow = self.get_flow()
            backoff_minutes = flow.metadata.get("ivr_retry", IVRCall.RETRY_BACKOFF_MINUTES)

            self.schedule_call_retry(backoff_minutes)

        if duration is not None:
            self.duration = duration

        # if we are moving into IN_PROGRESS, make sure our runs have proper expirations
        if previous_status in (self.PENDING, self.QUEUED, self.WIRED) and self.status in (
            self.IN_PROGRESS,
            self.RINGING,
        ):
            runs = FlowRun.objects.filter(connection=self, is_active=True)
            for run in runs:
                if not run.expires_on or (
                    run.expires_on - run.modified_on > timedelta(minutes=self.IVR_EXPIRES_CHOICES[-1][0])
                ):
                    run.update_expiration()

        if self.status == ChannelConnection.FAILED:
            flow = self.get_flow()
            if flow.metadata.get("ivr_retry_failed_events"):
                self.schedule_failed_call_retry()

    @staticmethod
    def derive_ivr_status_twiml(status: str, previous_status: str) -> str:
        if status == "queued":
            new_status = IVRCall.WIRED
        elif status == "ringing":
            new_status = IVRCall.RINGING
        elif status == "no-answer":
            new_status = IVRCall.NO_ANSWER
        elif status == "in-progress":
            new_status = IVRCall.IN_PROGRESS
        elif status == "completed":
            new_status = IVRCall.COMPLETED
        elif status == "busy":
            new_status = IVRCall.BUSY
        elif status == "failed":
            new_status = IVRCall.FAILED
        elif status == "canceled":
            new_status = IVRCall.CANCELED
        else:
            raise ValueError(f"Unhandled IVR call status: {status}")

        return new_status

    @staticmethod
    def derive_ivr_status_nexmo(status: str, previous_status: str) -> str:
        if status in ("ringing", "started"):
            new_status = IVRCall.RINGING
        elif status == "answered":
            new_status = IVRCall.IN_PROGRESS
        elif status == "completed":
            # nexmo sends `completed` as a final state for all call exits, we only want to mark call as completed if
            # it was previously `in progress`
            if previous_status == IVRCall.IN_PROGRESS:
                new_status = IVRCall.COMPLETED
            else:
                new_status = previous_status
        elif status == "failed":
            new_status = IVRCall.FAILED
        elif status in ("rejected", "busy"):
            new_status = IVRCall.BUSY
        elif status in ("unanswered", "timeout", "cancelled"):
            new_status = IVRCall.NO_ANSWER
        else:
            raise ValueError(f"Unhandled IVR call status: {status}")

        return new_status

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

    def get_flow(self):
        from temba.flows.models import FlowRun

        run = (
            FlowRun.objects.filter(connection=self, is_active=True)
            .select_related("flow")
            .order_by("-created_on")
            .first()
        )
        if run:
            return run.flow
        else:  # pragma: no cover
            raise ValueError(f"Cannot find flow for IVRCall id={self.id}")

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

    def register_active_event(self):
        """
        Helper function for registering active events on a throttled channel
        """
        r = get_redis_connection()

        channel_key = Channel.redis_active_events_key(self.channel_id)

        r.incr(channel_key)

    def unregister_active_event(self):
        """
        Helper function for unregistering active events on a throttled channel
        """
        r = get_redis_connection()

        channel_key = Channel.redis_active_events_key(self.channel_id)
        # are we on a throttled channel?
        current_tracked_events = r.get(channel_key)

        if current_tracked_events:

            value = int(current_tracked_events)
            if value <= 0:  # pragma: no cover
                raise ValueError("When this happens I'll quit my job and start producing moonshine/poitin/brlja !")

            r.decr(channel_key)
