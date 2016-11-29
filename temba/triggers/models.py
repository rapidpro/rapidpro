from __future__ import unicode_literals

import regex

from django.db import models
from django.utils import timezone
from django.utils.translation import ugettext_lazy as _
from smartmin.models import SmartModel
from temba.channels.models import Channel, ChannelEvent
from temba.contacts.models import Contact, ContactGroup
from temba.flows.models import Flow, FlowRun
from temba.ivr.models import IVRCall
from temba.msgs.models import Msg
from temba.orgs.models import Org


class Trigger(SmartModel):
    """
    A Trigger is used to start a user in a flow based on an event. For example, triggers might fire
    for missed calls, inboud sms messages starting with a keyword, or on a repeating schedule.
    """
    TYPE_KEYWORD = 'K'
    TYPE_SCHEDULE = 'S'
    TYPE_MISSED_CALL = 'M'
    TYPE_INBOUND_CALL = 'V'
    TYPE_CATCH_ALL = 'C'
    TYPE_FOLLOW = 'F'
    TYPE_NEW_CONVERSATION = 'N'

    TRIGGER_TYPES = ((TYPE_KEYWORD, _("Keyword Trigger")),
                     (TYPE_SCHEDULE, _("Schedule Trigger")),
                     (TYPE_INBOUND_CALL, _("Inbound Call Trigger")),
                     (TYPE_MISSED_CALL, _("Missed Call Trigger")),
                     (TYPE_CATCH_ALL, _("Catch All Trigger")),
                     (TYPE_FOLLOW, _("Follow Account Trigger")),
                     (TYPE_NEW_CONVERSATION, _("New Conversation Trigger")))

    org = models.ForeignKey(Org, verbose_name=_("Org"), help_text=_("The organization this trigger belongs to"))

    keyword = models.CharField(verbose_name=_("Keyword"), max_length=16, null=True, blank=True,
                               help_text=_("The first word in the message text"))

    flow = models.ForeignKey(Flow, verbose_name=_("Flow"),
                             help_text=_("Which flow will be started"), related_name="triggers")

    last_triggered = models.DateTimeField(verbose_name=_("Last Triggered"), default=None, null=True,
                                          help_text=_("The last time this trigger was fired"))

    trigger_count = models.IntegerField(verbose_name=_("Trigger Count"), default=0,
                                        help_text=_("How many times this trigger has fired"))

    is_archived = models.BooleanField(verbose_name=_("Is Archived"), default=False,
                                      help_text=_("Whether this trigger is archived"))

    groups = models.ManyToManyField(ContactGroup, verbose_name=_("Groups"),
                                    help_text=_("The groups to broadcast the flow to"))

    contacts = models.ManyToManyField(Contact, verbose_name=_("Contacts"),
                                      help_text=_("Individual contacts to broadcast the flow to"))

    schedule = models.OneToOneField('schedules.Schedule', verbose_name=_("Schedule"),
                                    null=True, blank=True, related_name='trigger',
                                    help_text=_('Our recurring schedule'))

    trigger_type = models.CharField(max_length=1, choices=TRIGGER_TYPES, default=TYPE_KEYWORD,
                                    verbose_name=_("Trigger Type"), help_text=_('The type of this trigger'))

    channel = models.OneToOneField(Channel, verbose_name=_("Channel"), null=True, help_text=_("The associated channel"))

    def __unicode__(self):
        if self.trigger_type == Trigger.TYPE_KEYWORD:
            return self.keyword
        return self.get_trigger_type_display()

    def name(self):
        return self.__unicode__()

    def as_json(self):
        """
        An exportable dict representing our trigger
        """
        return dict(trigger_type=self.trigger_type,
                    keyword=self.keyword,
                    flow=dict(uuid=self.flow.uuid, name=self.flow.name),
                    groups=[dict(uuid=group.uuid, name=group.name) for group in self.groups.all()],
                    channel=self.channel.uuid if self.channel else None)

    def trigger_scopes(self):
        """
        Returns keys that represents the scopes that this trigger can operate against (and might conflict with other triggers with)
        """
        groups = ['**'] if not self.groups else [str(g.id) for g in self.groups.all().order_by('id')]
        return ['%s_%s_%s_%s' % (self.trigger_type, str(self.channel_id), group, str(self.keyword)) for group in groups]

    def restore(self, user):
        self.modified_on = timezone.now()
        self.modified_by = user
        self.is_archived = False
        self.save()

        # archive any conflicts
        self.archive_conflicts(user)

        # if this is new conversation trigger, register for the FB callback
        if self.trigger_type == Trigger.TYPE_NEW_CONVERSATION:
            self.channel.set_fb_call_to_action_payload(Channel.GET_STARTED)

    def archive_conflicts(self, user):
        """
        Archives any triggers that conflict with this one
        """
        now = timezone.now()

        if not self.trigger_type == Trigger.TYPE_SCHEDULE:
            matches = Trigger.objects.filter(org=self.org, is_active=True, is_archived=False, trigger_type=self.trigger_type)

            # if this trigger has a keyword, only archive others with the same keyword
            if self.keyword:
                matches = matches.filter(keyword=self.keyword)

            # if this trigger has a group, only archive others with the same group
            if self.groups.all():
                matches = matches.filter(groups__in=self.groups.all())
            else:
                matches = matches.filter(groups=None)

            # if this trigger has a channel, only archive others with the same channel
            if self.channel:
                matches = matches.filter(channel=self.channel)

            # archive any conflicting triggers
            matches.exclude(id=self.id).update(is_archived=True, modified_on=now, modified_by=user)

    @classmethod
    def import_triggers(cls, exported_json, org, user, same_site=False):
        """
        Import triggers from our export file
        """
        from temba.orgs.models import EARLIEST_IMPORT_VERSION
        if exported_json.get('version', 0) < EARLIEST_IMPORT_VERSION:
            raise ValueError(_("Unknown version (%s)" % exported_json.get('version', 0)))

        # first things first, let's create our groups if necesary and map their ids accordingly
        if 'triggers' in exported_json:
            for trigger_spec in exported_json['triggers']:

                # resolve our groups
                groups = []
                for group_spec in trigger_spec['groups']:

                    group = None

                    if same_site:
                        group = ContactGroup.user_groups.filter(org=org, uuid=group_spec['uuid']).first()

                    if not group:
                        group = ContactGroup.get_user_group(org, group_spec['name'])

                    if not group:
                        group = ContactGroup.create_static(org, user, group_spec['name'])

                    if not group.is_active:
                        group.is_active = True
                        group.save()

                    groups.append(group)

                flow = Flow.objects.get(org=org, uuid=trigger_spec['flow']['uuid'], is_active=True)

                # see if that trigger already exists
                trigger = Trigger.objects.filter(org=org, trigger_type=trigger_spec['trigger_type'])

                if trigger_spec['keyword']:
                    trigger = trigger.filter(keyword__iexact=trigger_spec['keyword'])

                if groups:
                    trigger = trigger.filter(groups__in=groups)

                trigger = trigger.first()
                if trigger:
                    trigger.is_archived = False
                    trigger.flow = flow
                    trigger.save()
                else:

                    # if we have a channel resolve it
                    channel = trigger_spec.get('channel', None)  # older exports won't have a channel
                    if channel:
                        channel = Channel.objects.filter(uuid=channel, org=org).first()

                    trigger = Trigger.objects.create(org=org, trigger_type=trigger_spec['trigger_type'],
                                                     keyword=trigger_spec['keyword'], flow=flow,
                                                     created_by=user, modified_by=user,
                                                     channel=channel)

                    for group in groups:
                        trigger.groups.add(group)

    @classmethod
    def get_triggers_of_type(cls, org, trigger_type):
        return Trigger.objects.filter(org=org, trigger_type=trigger_type, is_active=True, is_archived=False)

    @classmethod
    def catch_triggers(cls, entity, trigger_type, channel):
        if isinstance(entity, Msg):
            contact = entity.contact
            start_msg = entity
        elif isinstance(entity, ChannelEvent) or isinstance(entity, IVRCall):
            contact = entity.contact
            start_msg = Msg(org=entity.org, contact=contact, channel=entity.channel, created_on=timezone.now(), id=0)
        elif isinstance(entity, Contact):
            contact = entity
            start_msg = Msg(org=entity.org, contact=contact, channel=channel, created_on=timezone.now(), id=0)
        else:
            raise ValueError("Entity must be of type msg, call or contact")

        triggers = Trigger.get_triggers_of_type(entity.org, trigger_type)

        if trigger_type in [Trigger.TYPE_FOLLOW, Trigger.TYPE_NEW_CONVERSATION]:
            triggers = triggers.filter(channel=channel)

        # is there a match for a group specific trigger?
        group_ids = contact.user_groups.values_list('pk', flat=True)
        group_triggers = triggers.filter(groups__in=group_ids).order_by('groups__name')

        # if we match with a group restriction, that takes precedence
        if group_triggers:
            triggers = group_triggers

        # otherwise, restrict to triggers that don't filter by group
        else:
            triggers = triggers.filter(groups=None)

        # only fire the first matching trigger
        if triggers:
            triggers[0].flow.start([], [contact], start_msg=start_msg, restart_participants=True)

        return bool(triggers)

    @classmethod
    def find_and_handle(cls, msg):
        # get the first word out of our message
        words = regex.split(r"[\W]+", msg.text.strip(), flags=regex.UNICODE | regex.V0)

        while words and not words[0]:
            words = words[1:]

        if not words:
            return False

        keyword = words[0].lower()

        if not keyword:
            return False

        active_run_qs = FlowRun.objects.filter(is_active=True, contact=msg.contact,
                                               flow__is_active=True, flow__is_archived=False)
        active_run = active_run_qs.prefetch_related('steps').order_by("-created_on", "-pk").first()

        if active_run and active_run.flow.ignore_triggers and not active_run.is_completed():
            return False

        groups_ids = msg.contact.user_groups.values_list('pk', flat=True)

        # Check first if we have a trigger for the contact groups
        matching = Trigger.objects.filter(is_archived=False, is_active=True, org=msg.org, keyword__iexact=keyword,
                                          flow__is_archived=False, flow__is_active=True, groups__in=groups_ids).order_by('groups__name').prefetch_related('groups', 'groups__contacts')

        # If no trigger for contact groups find there is a no group trigger
        if not matching:
            matching = Trigger.objects.filter(is_archived=False, is_active=True, org=msg.org, keyword__iexact=keyword,
                                              flow__is_archived=False, flow__is_active=True, groups=None).prefetch_related('groups', 'groups__contacts')

        if not matching:
            return False

        contact = msg.contact
        trigger = matching[0]

        if not contact.is_test:
            trigger.last_triggered = msg.created_on
            trigger.trigger_count += 1
            trigger.save()

        # if we have an associated flow, start this contact in it
        trigger.flow.start([], [contact], start_msg=msg, restart_participants=True)

        return True

    @classmethod
    def find_flow_for_inbound_call(cls, contact):

        groups_ids = contact.user_groups.values_list('pk', flat=True)

        # Check first if we have a trigger for the contact groups
        matching = Trigger.objects.filter(is_archived=False, is_active=True, org=contact.org,
                                          trigger_type=Trigger.TYPE_INBOUND_CALL, flow__is_archived=False,
                                          flow__is_active=True, groups__in=groups_ids).order_by('groups__name')\
                                  .prefetch_related('groups', 'groups__contacts')

        # If no trigger for contact groups find there is a no group trigger
        if not matching:
            matching = Trigger.objects.filter(is_archived=False, is_active=True, org=contact.org,
                                              trigger_type=Trigger.TYPE_INBOUND_CALL, flow__is_archived=False,
                                              flow__is_active=True, groups=None)\
                                      .prefetch_related('groups', 'groups__contacts')

        if not matching:
            return None

        trigger = matching[0]
        trigger.last_triggered = timezone.now()
        trigger.trigger_count += 1
        trigger.save()

        return trigger.flow

    @classmethod
    def apply_action_archive(cls, user, triggers):
        triggers.update(is_archived=True)

        # for any new convo triggers, clear out the call to action payload
        for trigger in triggers.filter(trigger_type=Trigger.TYPE_NEW_CONVERSATION):
            trigger.channel.set_fb_call_to_action_payload(None)

        return [each_trigger.pk for each_trigger in triggers]

    @classmethod
    def apply_action_restore(cls, user, triggers):
        restore_priority = triggers.order_by('-last_triggered', '-modified_on')
        trigger_scopes = set()

        # work through all the restored triggers in order of most recent used
        for trigger in restore_priority:
            trigger_scope = set(trigger.trigger_scopes())

            # if we haven't already restored a trigger with this scope
            if not trigger_scopes.intersection(trigger_scope):
                trigger.restore(user)
                trigger_scopes = trigger_scopes | trigger_scope

        return [t.pk for t in triggers]

    def release(self):
        """
        Releases this Trigger, use this instead of delete
        """
        self.is_active = False
        self.save()

    def fire(self):
        if self.is_archived or not self.is_active:
            return None

        channels = self.flow.org.channels.all()
        if not channels:
            return None

        groups = list(self.groups.all())
        contacts = list(self.contacts.all())

        if groups or contacts:
            self.last_triggered = timezone.now()
            self.trigger_count += 1
            self.save()

            return self.flow.start(groups, contacts, restart_participants=True)

        return False
