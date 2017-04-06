from __future__ import absolute_import, unicode_literals

import six

from django.db import models
from django.conf import settings
from django.utils import timezone
from django.contrib.auth.models import User
from temba.channels.models import ChannelSession
from temba.contacts.models import Contact, URN, ContactURN
from temba.triggers.models import Trigger


class USSDQuerySet(models.QuerySet):
    def get(self, *args, **kwargs):
        kwargs.update(dict(session_type=USSDSession.USSD))
        return super(USSDQuerySet, self).get(*args, **kwargs)

    def create(self, **kwargs):
        if kwargs.get('channel'):
            user = kwargs.get('channel').created_by
        else:  # testing purposes (eg. simulator)
            user = User.objects.get(username=settings.ANONYMOUS_USER_NAME)

        kwargs.update(dict(session_type=USSDSession.USSD, created_by=user, modified_by=user))
        return super(USSDQuerySet, self).create(**kwargs)

    def get_initiated_push_session(self, contact):
        return self.filter(direction=USSDSession.USSD_PUSH, status=USSDSession.INITIATED, contact=contact).first()

    def get_session_with_status_only(self, session_id):
        return self.only('status').filter(id=session_id).first()


class USSDSession(ChannelSession):
    USSD_PULL = INCOMING = 'I'
    USSD_PUSH = OUTGOING = 'O'

    objects = USSDQuerySet.as_manager()

    class Meta:
        proxy = True

    @property
    def should_end(self):
        return self.status == self.ENDING

    def mark_ending(self):  # session to be ended
        if self.status != self.ENDING:
            self.status = self.ENDING
            self.save(update_fields=['status'])

    def close(self):  # session has successfully ended
        if self.status == self.ENDING:
            self.status = self.COMPLETED
        else:
            self.status = self.INTERRUPTED

        self.ended_on = timezone.now()
        self.save(update_fields=['status', 'ended_on'])

    def start_session_async(self, flow, date, message_id):
        from temba.msgs.models import Msg, USSD
        message = Msg.objects.create(
            channel=self.channel, contact=self.contact, contact_urn=self.contact_urn,
            sent_on=date, session=self, msg_type=USSD, external_id=message_id,
            created_on=timezone.now(), modified_on=timezone.now(), org=self.channel.org,
            direction=self.INCOMING)
        flow.start([], [self.contact], start_msg=message, restart_participants=True, session=self)

    def handle_session_async(self, urn, content, date, message_id):
        from temba.msgs.models import Msg, USSD
        Msg.create_incoming(
            channel=self.channel, org=self.org, urn=urn, text=content or '', date=date, session=self,
            msg_type=USSD, external_id=message_id)

    def handle_ussd_session_sync(self):  # pragma: needs cover
        # TODO: implement for InfoBip and other sync APIs
        pass

    @classmethod
    def handle_incoming(cls, channel, urn, date, external_id, contact=None, message_id=None, status=None,
                        content=None, starcode=None, org=None, async=True):

        trigger = None
        contact_urn = None

        # handle contact with channel
        urn = URN.from_tel(urn)

        if not contact:
            contact = Contact.get_or_create(channel.org, channel.created_by, urns=[urn], channel=channel)
            contact_urn = contact.urn_objects[urn]
        elif urn:
            contact_urn = ContactURN.get_or_create(org, contact, urn, channel=channel)

        contact.set_preferred_channel(channel)

        if contact_urn:
            contact_urn.update_affinity(channel)

        # setup session
        defaults = dict(channel=channel, contact=contact, contact_urn=contact_urn,
                        org=channel.org if channel else contact.org)

        if status == cls.TRIGGERED:
            trigger = Trigger.find_trigger_for_ussd_session(contact, starcode)
            if not trigger:
                return False
            defaults.update(dict(started_on=date, direction=cls.USSD_PULL, status=status))

        elif status == cls.INTERRUPTED:
            defaults.update(dict(ended_on=date, status=status))

        else:
            defaults.update(dict(status=cls.IN_PROGRESS))

        # check if there's an initiated PUSH session
        session = cls.objects.get_initiated_push_session(contact)

        if not session:
            session, created = cls.objects.update_or_create(external_id=external_id, defaults=defaults)
        else:
            defaults.update(dict(external_id=external_id))
            for key, value in six.iteritems(defaults):
                setattr(session, key, value)
            session.save()
            created = None

        # start session
        if created and async and trigger:
            session.start_session_async(trigger.flow, date, message_id)

        # resume session, deal with incoming content and all the other states
        else:
            session.handle_session_async(urn, content, date, message_id)

        return session
