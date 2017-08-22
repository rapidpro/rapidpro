from __future__ import absolute_import, unicode_literals

import json
from uuid import uuid4

import six

from django.db import models
from django.utils import timezone
from temba.channels.models import ChannelSession
from temba.contacts.models import Contact, URN, ContactURN
from temba.flows.models import RuleSet, Rule, TrueTest, EqTest
from temba.triggers.models import Trigger
from temba.utils import get_anonymous_user


class USSDQuerySet(models.QuerySet):
    def get(self, *args, **kwargs):
        kwargs.update(dict(session_type=USSDSession.USSD))
        return super(USSDQuerySet, self).get(*args, **kwargs)

    def create(self, **kwargs):
        if kwargs.get('channel'):
            user = kwargs.get('channel').created_by
        else:  # testing purposes (eg. simulator)
            user = get_anonymous_user()

        kwargs.update(dict(session_type=USSDSession.USSD, created_by=user, modified_by=user))
        return super(USSDQuerySet, self).create(**kwargs)

    def get_initiated_push_session(self, contact):
        return self.filter(direction=USSDSession.USSD_PUSH, status=USSDSession.INITIATED, contact=contact).first()

    def get_interrupted_pull_session(self, contact):
        return self.filter(direction=USSDSession.USSD_PULL, status=USSDSession.INTERRUPTED, contact=contact).last()

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

    def resume(self):
        if self.status == self.INTERRUPTED:
            self.status = self.IN_PROGRESS
            self.ended_on = None
            self.save(update_fields=['status', 'ended_on'])

    def create_incoming_message(self, content, date, message_id):
        from temba.msgs.models import Msg, USSD

        return Msg.objects.create(
            channel=self.channel, contact=self.contact, contact_urn=self.contact_urn,
            sent_on=date, session=self, msg_type=USSD, external_id=message_id,
            created_on=timezone.now(), modified_on=timezone.now(), org=self.channel.org,
            direction=self.INCOMING, text=content or '')

    def start_session_async(self, flow, date, message_id):
        message = self.create_incoming_message(None, date, message_id)
        flow.start([], [self.contact], start_msg=message, restart_participants=True, session=self)

    def resume_session_async(self, content, date, message_id):
        from temba.flows.models import Flow

        message = None

        last_run = self.runs.last()
        flow = last_run.flow

        steps = last_run.steps.all()

        # pick the step which was interrupted
        last_step = steps.filter(rule_value='interrupted_status').order_by('-pk').first()

        # get the resuming RuleSet
        resuming_ruleset = last_step.get_step()

        # get the entry RuleSet
        entry_ruleset = RuleSet.objects.filter(uuid=flow.entry_uuid).first()

        # create a RuleSet to ask the user to Resume/Continue or Restart the session
        resume_or_restart_ruleset = RuleSet(flow=flow, uuid=str(uuid4()), x=0, y=0, ruleset_type=RuleSet.TYPE_WAIT_USSD_MENU)
        resume_or_restart_ruleset.set_rules_dict(
            [Rule(str(uuid4()), dict(base="Resume"), resuming_ruleset.uuid, 'R', EqTest(test="1"),
                  dict(base="Resume flow")).as_json(),
             Rule(str(uuid4()), dict(base="Restart"), entry_ruleset.uuid, 'R', EqTest(test="2"),
                  dict(base="Restart from main menu")).as_json(),
                Rule(str(uuid4()), dict(base="All Responses"), entry_ruleset.uuid, 'R', TrueTest()).as_json()])
        config = {
            "ussd_message": {"base": "Welcome back. Please select an option:"}
        }
        resume_or_restart_ruleset.config = json.dumps(config)
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

        # check if there's an interrupted PULL session
        if not session:
            session = cls.objects.get_interrupted_pull_session(contact)
            if session:
                resume_session = True
                defaults.update(dict(status=cls.INTERRUPTED))

        created = False
        if not session:
            try:
                session = cls.objects.select_for_update().exclude(status__in=USSDSession.DONE)\
                                                         .get(external_id=external_id)
                for k, v in six.iteritems(defaults):
                    setattr(session, k, v() if callable(v) else v)
                session.save()
            except cls.DoesNotExist:
                defaults['external_id'] = external_id
                session = cls.objects.create(**defaults)
                created = True
        else:
            defaults.update(dict(external_id=external_id))
            for key, value in six.iteritems(defaults):
                setattr(session, key, value)
            session.save()

        # start session
        if created and async and trigger:
            session.start_session_async(trigger.flow, date, message_id)

        # resume session, deal with incoming content and all the other states
        else:
            session.handle_session_async(urn, content, date, message_id)

        return session
