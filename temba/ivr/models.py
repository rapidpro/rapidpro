from datetime import timedelta

from django.core.urlresolvers import reverse
from django.db import models
from django.utils import timezone
from django.utils.translation import ugettext_lazy as _

from temba.channels.models import Channel, ChannelLog, ChannelSession, ChannelType
from temba.utils import on_transaction_commit


class IVRManager(models.Manager):
    def create(self, *args, **kwargs):
        return super().create(*args, session_type=IVRCall.IVR, **kwargs)

    def get_queryset(self):
        return super().get_queryset().filter(session_type=IVRCall.IVR)


class IVRCall(ChannelSession):
    RETRY_BACKOFF_MINUTES = 60
    MAX_RETRY_ATTEMPTS = 3

    objects = IVRManager()

    class Meta:
        proxy = True

    @classmethod
    def create_outgoing(cls, channel, contact, contact_urn, user):
        return IVRCall.objects.create(
            channel=channel,
            contact=contact,
            contact_urn=contact_urn,
            direction=IVRCall.OUTGOING,
            org=channel.org,
            created_by=user,
            modified_by=user,
            status=IVRCall.PENDING,
        )

    @classmethod
    def create_incoming(cls, channel, contact, contact_urn, user, external_id):
        return IVRCall.objects.create(
            channel=channel,
            contact=contact,
            contact_urn=contact_urn,
            direction=IVRCall.INCOMING,
            org=channel.org,
            created_by=user,
            modified_by=user,
            external_id=external_id,
            status=IVRCall.PENDING,
        )

    @classmethod
    def hangup_test_call(cls, flow):
        # if we have an active call, hang it up
        from temba.flows.models import FlowRun

        runs = FlowRun.objects.filter(flow=flow, contact__is_test=True).exclude(connection=None)
        for run in runs:
            test_call = IVRCall.objects.filter(id=run.connection.id).first()
            if test_call.channel.channel_type in ["T", "TW"]:
                if not test_call.is_done():
                    test_call.close()

    def close(self):
        if not self.is_done():

            # mark us as interrupted
            self.status = ChannelSession.INTERRUPTED
            self.ended_on = timezone.now()
            self.save()

            client = self.channel.get_ivr_client()
            if client and self.external_id:
                client.hangup(self)

    def do_start_call(self, qs=None):
        client = self.channel.get_ivr_client()
        domain = self.channel.callback_domain

        from temba.ivr.clients import IVRException
        from temba.flows.models import ActionLog, FlowRun

        if client:
            try:
                url = "https://%s%s" % (domain, reverse("ivr.ivrcall_handle", args=[self.pk]))
                if qs:  # pragma: no cover
                    url = "%s?%s" % (url, qs)

                tel = None

                # if we are working with a test contact, look for user settings
                if self.contact.is_test:
                    user_settings = self.created_by.get_settings()
                    if user_settings.tel:
                        tel = user_settings.tel
                        run = FlowRun.objects.filter(connection=self)
                        if run:
                            ActionLog.create(run[0], "Placing test call to %s" % user_settings.get_tel_formatted())
                if not tel:
                    tel_urn = self.contact_urn
                    tel = tel_urn.path

                client.start_call(self, to=tel, from_=self.channel.address, status_callback=url)

            except IVRException as e:
                import traceback

                traceback.print_exc()

                if self.contact.is_test:
                    run = FlowRun.objects.filter(connection=self)
                    ActionLog.create(run[0], "Call ended. %s" % str(e))

            except Exception as e:  # pragma: no cover
                import traceback

                traceback.print_exc()

                self.status = self.FAILED
                self.save()

                if self.contact.is_test:
                    run = FlowRun.objects.filter(connection=self)
                    ActionLog.create(run[0], "Call ended.")

    def start_call(self):
        from temba.ivr.tasks import start_call_task

        self.status = IVRCall.QUEUED
        self.save()

        on_transaction_commit(lambda: start_call_task.delay(self.pk))

    def schedule_call_retry(self, backoff_minutes: int):
        # retry the call if it has not been retried maximum number of times
        if self.retry_count < IVRCall.MAX_RETRY_ATTEMPTS:
            self.next_attempt = timezone.now() + timedelta(minutes=backoff_minutes)
            self.retry_count += 1
        else:
            self.next_attempt = None

    def update_status(self, status: str, duration: float, channel_type: str):
        """
        Updates our status from a provide call status string

        """
        if not status:
            raise ValueError(f"IVR Call status must be defined, got: '{status}'")

        previous_status = self.status

        from temba.flows.models import FlowRun, ActionLog

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
        if self.status in ChannelSession.DONE:
            self.ended_on = timezone.now()

            if self.contact.is_test:
                run = FlowRun.objects.filter(connection=self)
                if run:
                    ActionLog.create(run[0], _("Call ended."))

        if self.status in ChannelSession.RETRY_CALL and previous_status not in ChannelSession.RETRY_CALL:
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
            runs = FlowRun.objects.filter(connection=self, is_active=True, expires_on=None)
            for run in runs:
                run.update_expiration()

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

    def get_last_log(self):
        """
        Gets the last channel log for this message. Performs sorting in Python to ease pre-fetching.
        """
        sorted_logs = None
        if self.channel and self.channel.is_active:
            sorted_logs = sorted(ChannelLog.objects.filter(connection=self), key=lambda l: l.created_on, reverse=True)
        return sorted_logs[0] if sorted_logs else None

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
