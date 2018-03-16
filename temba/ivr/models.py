# -*- coding: utf-8 -*-
from __future__ import absolute_import, division, print_function, unicode_literals

from datetime import timedelta

import six
from django.db import models
from django.core.urlresolvers import reverse
from django.utils import timezone
from django.utils.translation import ugettext_lazy as _
from temba.channels.models import ChannelSession, Channel, ChannelLog, ChannelType
from temba.utils import on_transaction_commit


class IVRManager(models.Manager):
    def create(self, *args, **kwargs):
        return super(IVRManager, self).create(*args, session_type=IVRCall.IVR, **kwargs)

    def get_queryset(self):
        return super(IVRManager, self).get_queryset().filter(session_type=IVRCall.IVR)


class IVRCall(ChannelSession):

    objects = IVRManager()

    class Meta:
        proxy = True

    @classmethod
    def create_outgoing(cls, channel, contact, contact_urn, user):
        return IVRCall.objects.create(channel=channel, contact=contact, contact_urn=contact_urn,
                                      direction=IVRCall.OUTGOING, org=channel.org,
                                      created_by=user, modified_by=user)

    @classmethod
    def create_incoming(cls, channel, contact, contact_urn, user, external_id):
        return IVRCall.objects.create(channel=channel, contact=contact, contact_urn=contact_urn,
                                      direction=IVRCall.INCOMING, org=channel.org, created_by=user,
                                      modified_by=user, external_id=external_id)

    @classmethod
    def hangup_test_call(cls, flow):
        # if we have an active call, hang it up
        from temba.flows.models import FlowRun
        runs = FlowRun.objects.filter(flow=flow, contact__is_test=True).exclude(connection=None)
        for run in runs:
            test_call = IVRCall.objects.filter(id=run.connection.id).first()
            if test_call.channel.channel_type in ['T', 'TW']:
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
                url = "https://%s%s" % (domain, reverse('ivr.ivrcall_handle', args=[self.pk]))
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
                self.status = self.FAILED
                self.save()
                if self.contact.is_test:
                    run = FlowRun.objects.filter(connection=self)
                    ActionLog.create(run[0], "Call ended. %s" % six.text_type(e))

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
        on_transaction_commit(lambda: start_call_task.delay(self.pk))

    def update_status(self, status, duration, channel_type):
        """
        Updates our status from a provide call status string

        """
        from temba.flows.models import FlowRun, ActionLog

        previous_status = self.status
        ivr_protocol = Channel.get_type_from_code(channel_type).ivr_protocol

        if ivr_protocol == ChannelType.IVRProtocol.IVR_PROTOCOL_TWIML:
            if status == 'queued':
                self.status = self.QUEUED
            elif status == 'ringing':
                self.status = self.RINGING
            elif status == 'no-answer':
                self.status = self.NO_ANSWER
            elif status == 'in-progress':
                if self.status != self.IN_PROGRESS:
                    self.started_on = timezone.now()
                self.status = self.IN_PROGRESS
            elif status == 'completed':
                if self.contact.is_test:
                    run = FlowRun.objects.filter(connection=self)
                    if run:
                        ActionLog.create(run[0], _("Call ended."))
                self.status = self.COMPLETED
            elif status == 'busy':
                self.status = self.BUSY
            elif status == 'failed':
                self.status = self.FAILED
            elif status == 'canceled':
                self.status = self.CANCELED

        elif ivr_protocol == ChannelType.IVRProtocol.IVR_PROTOCOL_NCCO:
            if status in ('ringing', 'started'):
                self.status = self.RINGING
            elif status == 'answered':
                self.status = self.IN_PROGRESS
            elif status == 'completed':
                self.status = self.COMPLETED
            elif status == 'failed':
                self.status = self.FAILED
            elif status in ('rejected', 'busy'):
                self.status = self.BUSY
            elif status in ('unanswered', 'timeout'):
                self.status = self.NO_ANSWER

        # if we are done, mark our ended time
        if self.status in ChannelSession.DONE:
            self.ended_on = timezone.now()

        if duration is not None:
            self.duration = duration

        # if we are moving into IN_PROGRESS, make sure our runs have proper expirations
        if previous_status in [self.QUEUED, self.PENDING] and self.status in [self.IN_PROGRESS, self.RINGING]:
            runs = FlowRun.objects.filter(connection=self, is_active=True, expires_on=None)
            for run in runs:
                run.update_expiration()

    def get_duration(self):
        """
        Either gets the set duration as reported by provider, or tries to calculate
        it from the approximate time it was started
        """
        duration = self.duration
        if not duration and self.status == 'I' and self.started_on:
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
