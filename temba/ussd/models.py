from __future__ import absolute_import, unicode_literals

import six

from django.db import models
from temba.channels.models import ChannelSession
from temba.contacts.models import Contact, URN
from temba.triggers.models import Trigger


class USSDQuerySet(models.QuerySet):
    def get(self, *args, **kwargs):
        kwargs.update(dict(session_type=USSDSession.USSD))
        return super(USSDQuerySet, self).get(*args, **kwargs)

    def create(self, **kwargs):
        user = kwargs.get('channel').created_by
        kwargs.update(dict(session_type=USSDSession.USSD, created_by=user, modified_by=user))
        return super(USSDQuerySet, self).create(**kwargs)

    def get_initiated_push_session(self, contact):
        return self.filter(direction=USSDSession.USSD_PUSH, contact=contact).first()


class USSDSession(ChannelSession):
    USSD_PULL = INCOMING = 'I'
    USSD_PUSH = OUTGOING = 'O'

    objects = USSDQuerySet.as_manager()

    class Meta:
        proxy = True

    def start_session_async(self, flow):
        flow.start([], [self.contact], start_msg=None, restart_participants=True, session=self)

    def handle_session_async(self, urn, content, date, message_id):
        from temba.msgs.models import Msg

        message = Msg.create_incoming(channel=self.channel, urn=urn, text=content or '', date=date, session=self)
        message.external_id = message_id
        message.save()

    def handle_ussd_session_sync(self):  # pragma: needs cover
        # TODO: implement for InfoBip and other sync APIs
        pass

    @classmethod
    def handle_incoming(cls, channel, urn, date, external_id, message_id=None, status=None,
                        flow=None, content=None, starcode=None, org=None, async=True):

        trigger = None

        # handle contact with channel
        urn = URN.from_tel(urn)
        contact = Contact.get_or_create(channel.org, channel.created_by, urns=[urn], channel=channel)
        contact_urn = contact.urn_objects[urn]

        contact.set_preferred_channel(channel)
        contact_urn.update_affinity(channel)

        # setup session
        defaults = dict(channel=channel, contact=contact, contact_urn=contact_urn, org=channel.org)

        if status == cls.TRIGGERED:
            trigger = Trigger.find_trigger_for_ussd_session(contact, starcode)
            if not trigger:
                return False
            defaults.update(dict(started_on=date, direction=cls.USSD_PULL, status=status))

        elif status == cls.INTERRUPTED:
            defaults.update(dict(ended_on=date, status=status))

        else:
            defaults.update(dict(status=USSDSession.IN_PROGRESS))

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
            session.start_session_async(trigger.flow)

        # resume session, deal with incoming content and all the other states
        else:
            session.handle_session_async(urn, content, date, message_id)

        return session
