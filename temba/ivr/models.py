from __future__ import absolute_import, unicode_literals

from django.db import models
from django.conf import settings
from django.core.urlresolvers import reverse
from temba.channels.models import ChannelSession


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
    def create_outgoing(cls, channel, contact, contact_urn, flow, user):
        return IVRCall.objects.create(channel=channel, contact=contact, contact_urn=contact_urn, flow=flow,
                                      direction=IVRCall.OUTGOING, org=channel.org,
                                      created_by=user, modified_by=user)

    @classmethod
    def create_incoming(cls, channel, contact, contact_urn, flow, user):
        return IVRCall.objects.create(channel=channel, contact=contact, contact_urn=contact_urn, flow=flow,
                                      direction=IVRCall.INCOMING, org=channel.org, created_by=user,
                                      modified_by=user)

    @classmethod
    def hangup_test_call(cls, flow):
        # if we have an active call, hang it up
        test_call = IVRCall.objects.filter(contact__is_test=True, flow=flow)
        if test_call:
            test_call = test_call[0]
            if not test_call.is_done():
                test_call.hangup()

    def hangup(self):
        if not self.is_done():
            client = self.channel.get_ivr_client()
            if client and self.external_id:
                client.calls.hangup(self.external_id)

    def do_start_call(self, qs=None):
        client = self.channel.get_ivr_client()
        from temba.ivr.clients import IVRException
        from temba.flows.models import ActionLog, FlowRun
        if client:
            try:
                url = "https://%s%s" % (settings.TEMBA_HOST, reverse('ivr.ivrcall_handle', args=[self.pk]))
                if qs:  # pragma: no cover
                    url = "%s?%s" % (url, qs)

                tel = None

                # if we are working with a test contact, look for user settings
                if self.contact.is_test:
                    user_settings = self.created_by.get_settings()
                    if user_settings.tel:
                        tel = user_settings.tel
                        run = FlowRun.objects.filter(session=self)
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
                    run = FlowRun.objects.filter(session=self)
                    ActionLog.create(run[0], "Call ended. %s" % e.message)

            except Exception as e:  # pragma: no cover
                import traceback
                traceback.print_exc()
                self.status = self.FAILED
                self.save()

                if self.contact.is_test:
                    run = FlowRun.objects.filter(session=self)
                    ActionLog.create(run[0], "Call ended.")

    def start_call(self):
        from temba.ivr.tasks import start_call_task
        start_call_task.delay(self.pk)
