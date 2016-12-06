from __future__ import absolute_import, unicode_literals

from django.conf import settings
from django.contrib.auth.models import User
from django.db import models
from temba.channels.models import ChannelSession
from temba.contacts.models import Contact, URN
from temba.triggers.models import Trigger


class USSDManager(models.Manager):
    def get_queryset(self):
        return super(USSDManager, self).get_queryset().filter(session_type=USSDSession.USSD)

    def get_initiated_push_session(self, contact):
        return self.get_queryset().filter(direction=USSDSession.USSD_PUSH, contact=contact).first()


class USSDSession(ChannelSession):
    USSD_PULL = INCOMING = 'I'
    USSD_PUSH = OUTGOING = 'O'

    objects = USSDManager()

    class Meta:
        proxy = True

    def save(self, force_insert=False, force_update=False, using=None, update_fields=None):
        if not self.pk:
            self.session_type = USSDSession.USSD
            user = User.objects.get(username=settings.ANONYMOUS_USER_NAME)
            self.created_by = user
            self.modified_by = user
        super(USSDSession, self).save(force_insert, force_update, using, update_fields)

    def start_session_async(self):
        self.flow.start([], [self.contact], start_msg=None, restart_participants=True, session=self)

    def handle_session_async(self, urn, content, date, message_id):
        from temba.msgs.models import Msg

        channel = self.channel if not self.contact.is_test else None

        message = Msg.create_incoming(channel=channel, urn=urn, text=content or '', date=date)
        message.external_id = message_id
        message.save()

    def handle_ussd_session_sync(self):
        # TODO: implement for InfoBip and other sync APIs
        pass

    @classmethod
    def handle_incoming(cls, channel, urn, status, date, message_id, external_id,
                        flow=None, content=None, starcode=None, org=None, async=True):

        if not external_id:
            return False

        trigger = None

        # handle contact with channel
        urn = URN.from_tel(urn)
        contact = Contact.get_or_create(channel.org, channel.created_by, urns=[urn], channel=channel)
        contact_urn = contact.urn_objects[urn]

        contact.set_preferred_channel(channel)
        contact_urn.update_affinity(channel)

        # setup session
        defaults = dict(channel=channel, contact=contact, contact_urn=contact_urn,
                        org=channel.org)
        import ipdb; ipdb.set_trace()
        if status == cls.TRIGGERED:
            trigger = Trigger.find_trigger_for_ussd_session(contact, starcode)
            if not trigger:
                return False
            defaults.update(dict(started_on=date, flow=trigger.flow, direction=cls.USSD_PULL, status=status))

        elif status == cls.INTERRUPTED:
            defaults.update(dict(ended_on=date, status=status))

        else:
            defaults.update(dict(status=USSDSession.IN_PROGRESS))

        # check if there's an initiated Push session
        session = cls.objects.get_initiated_push_session(contact)

        if not session:
            session, created = cls.objects.update_or_create(external_id=external_id, defaults=defaults)
        else:
            session.status = USSDSession.IN_PROGRESS
            session.external_id = external_id
            session.save(update_fields=['status', 'external_id'])
            created = None

        # start session
        if created and async and trigger:
            session.start_session_async()

        # resume session, deal with incoming content
        else:
            session.handle_session_async(urn, content, date, message_id)

        return session
