from smartmin.models import SmartModel
from temba_expressions.utils import tokenize

from django.conf import settings
from django.db import models
from django.db.models import Q
from django.utils import timezone
from django.utils.translation import ugettext_lazy as _

from temba.channels.models import Channel, ChannelEvent
from temba.contacts.models import Contact, ContactGroup
from temba.flows.models import Flow, FlowRun, FlowStart
from temba.ivr.models import IVRCall
from temba.msgs.models import Msg
from temba.orgs.models import Org


class Trigger(SmartModel):
    """
    A Trigger is used to start a user in a flow based on an event. For example, triggers might fire
    for missed calls, inboud sms messages starting with a keyword, or on a repeating schedule.
    """

    TYPE_CATCH_ALL = "C"
    TYPE_KEYWORD = "K"
    TYPE_MISSED_CALL = "M"
    TYPE_NEW_CONVERSATION = "N"
    TYPE_REFERRAL = "R"
    TYPE_SCHEDULE = "S"
    TYPE_USSD_PULL = "U"
    TYPE_INBOUND_CALL = "V"

    TRIGGER_TYPES = (
        (TYPE_KEYWORD, _("Keyword Trigger")),
        (TYPE_SCHEDULE, _("Schedule Trigger")),
        (TYPE_INBOUND_CALL, _("Inbound Call Trigger")),
        (TYPE_MISSED_CALL, _("Missed Call Trigger")),
        (TYPE_CATCH_ALL, _("Catch All Trigger")),
        (TYPE_NEW_CONVERSATION, _("New Conversation Trigger")),
        (TYPE_USSD_PULL, _("USSD Pull Session Trigger")),
        (TYPE_REFERRAL, _("Referral Trigger")),
    )

    KEYWORD_MAX_LEN = 16

    MATCH_FIRST_WORD = "F"
    MATCH_ONLY_WORD = "O"

    MATCH_TYPES = (
        (MATCH_FIRST_WORD, _("Message starts with the keyword")),
        (MATCH_ONLY_WORD, _("Message contains only the keyword")),
    )

    EXPORT_TYPE = "trigger_type"
    EXPORT_KEYWORD = "keyword"
    EXPORT_FLOW = "flow"
    EXPORT_GROUPS = "groups"
    EXPORT_CHANNEL = "channel"

    org = models.ForeignKey(
        Org, on_delete=models.PROTECT, verbose_name=_("Org"), help_text=_("The organization this trigger belongs to")
    )

    keyword = models.CharField(
        verbose_name=_("Keyword"),
        max_length=KEYWORD_MAX_LEN,
        null=True,
        blank=True,
        help_text=_("Word to match in the message text"),
    )

    referrer_id = models.CharField(
        verbose_name=_("Referrer Id"),
        max_length=255,
        null=True,
        blank=True,
        help_text=_("The referrer id that triggers us"),
    )

    flow = models.ForeignKey(
        Flow,
        on_delete=models.PROTECT,
        verbose_name=_("Flow"),
        help_text=_("Which flow will be started"),
        related_name="triggers",
    )

    last_triggered = models.DateTimeField(
        verbose_name=_("Last Triggered"), default=None, null=True, help_text=_("The last time this trigger was fired")
    )

    trigger_count = models.IntegerField(
        verbose_name=_("Trigger Count"), default=0, help_text=_("How many times this trigger has fired")
    )

    is_archived = models.BooleanField(
        verbose_name=_("Is Archived"), default=False, help_text=_("Whether this trigger is archived")
    )

    groups = models.ManyToManyField(
        ContactGroup, verbose_name=_("Groups"), help_text=_("The groups to broadcast the flow to")
    )

    contacts = models.ManyToManyField(
        Contact, verbose_name=_("Contacts"), help_text=_("Individual contacts to broadcast the flow to")
    )

    schedule = models.OneToOneField(
        "schedules.Schedule",
        on_delete=models.PROTECT,
        verbose_name=_("Schedule"),
        null=True,
        blank=True,
        related_name="trigger",
        help_text=_("Our recurring schedule"),
    )

    trigger_type = models.CharField(
        max_length=1,
        choices=TRIGGER_TYPES,
        default=TYPE_KEYWORD,
        verbose_name=_("Trigger Type"),
        help_text=_("The type of this trigger"),
    )

    match_type = models.CharField(
        max_length=1,
        choices=MATCH_TYPES,
        default=MATCH_FIRST_WORD,
        null=True,
        verbose_name=_("Trigger When"),
        help_text=_("How to match a message with a keyword"),
    )

    channel = models.ForeignKey(
        Channel,
        on_delete=models.PROTECT,
        verbose_name=_("Channel"),
        null=True,
        related_name="triggers",
        help_text=_("The associated channel"),
    )

    @classmethod
    def create(cls, org, user, trigger_type, flow, channel=None, **kwargs):
        trigger = cls.objects.create(
            org=org, trigger_type=trigger_type, flow=flow, channel=channel, created_by=user, modified_by=user, **kwargs
        )

        # archive any conflicts
        trigger.archive_conflicts(user)

        if trigger.channel:
            if settings.IS_PROD:
                trigger.channel.get_type().activate_trigger(trigger)

        return trigger

    def __str__(self):
        if self.trigger_type == Trigger.TYPE_KEYWORD:
            return self.keyword
        return self.get_trigger_type_display()  # pragma: needs cover

    def trigger_scopes(self):
        """
        Returns keys that represents the scopes that this trigger can operate against (and might conflict with other triggers with)
        """
        groups = ["**"] if not self.groups else [str(g.id) for g in self.groups.all().order_by("id")]
        return [
            "%s_%s_%s_%s" % (self.trigger_type, str(self.channel_id), group, str(self.keyword)) for group in groups
        ]

    def archive(self, user):
        self.modified_by = user
        self.is_archived = True
        self.save()

        if settings.IS_PROD and self.channel:
            self.channel.get_type().deactivate_trigger(self)

    def restore(self, user):
        self.modified_by = user
        self.is_archived = False
        self.save()

        # archive any conflicts
        self.archive_conflicts(user)

        if settings.IS_PROD and self.channel:
            self.channel.get_type().activate_trigger(self)

    def archive_conflicts(self, user):
        """
        Archives any triggers that conflict with this one
        """
        now = timezone.now()

        if not self.trigger_type == Trigger.TYPE_SCHEDULE:
            matches = Trigger.objects.filter(
                org=self.org, is_active=True, is_archived=False, trigger_type=self.trigger_type
            )

            # if this trigger has a keyword, only archive others with the same keyword
            if self.keyword:
                matches = matches.filter(keyword=self.keyword)

            # if this trigger has a group, only archive others with the same group
            if self.groups.all():  # pragma: needs cover
                matches = matches.filter(groups__in=self.groups.all())
            else:
                matches = matches.filter(groups=None)

            # if this trigger has a referrer_id, only archive others with the same referrer_id
            if self.referrer_id is not None:
                matches = matches.filter(referrer_id__iexact=self.referrer_id)

            # if this trigger has a channel, only archive others with the same channel
            if self.channel:
                matches = matches.filter(channel=self.channel)

            # archive any conflicting triggers
            matches.exclude(id=self.id).update(is_archived=True, modified_on=now, modified_by=user)

    @classmethod
    def archive_triggers_for_contact(cls, contact, user):
        contact_triggers = list(contact.trigger_set.all())

        for trigger in contact_triggers:
            trigger.contacts.remove(contact)

            if not trigger.groups.exists() and not trigger.contacts.exists() and not trigger.is_archived:
                trigger.archive(user)

    @classmethod
    def import_triggers(cls, org, user, trigger_defs, same_site=False):
        """
        Import triggers from a list of exported triggers
        """

        for trigger_def in trigger_defs:

            # resolve our groups
            groups = []
            for group_spec in trigger_def[Trigger.EXPORT_GROUPS]:

                group = None

                if same_site:  # pragma: needs cover
                    group = ContactGroup.user_groups.filter(org=org, uuid=group_spec["uuid"]).first()

                if not group:
                    group = ContactGroup.get_user_group(org, group_spec["name"])

                if not group:
                    group = ContactGroup.create_static(org, user, group_spec["name"])  # pragma: needs cover

                if not group.is_active:  # pragma: needs cover
                    group.is_active = True
                    group.save()

                groups.append(group)

            flow = Flow.objects.get(org=org, uuid=trigger_def[Trigger.EXPORT_FLOW]["uuid"], is_active=True)

            # see if that trigger already exists
            trigger = Trigger.objects.filter(org=org, trigger_type=trigger_def[Trigger.EXPORT_TYPE])

            if trigger_def[Trigger.EXPORT_KEYWORD]:
                trigger = trigger.filter(keyword__iexact=trigger_def[Trigger.EXPORT_KEYWORD])

            if groups:
                trigger = trigger.filter(groups__in=groups)

            trigger = trigger.first()
            if trigger:
                trigger.is_archived = False
                trigger.flow = flow
                trigger.save()
            else:

                # if we have a channel resolve it
                channel = trigger_def.get(Trigger.EXPORT_CHANNEL, None)  # older exports won't have a channel
                if channel:
                    channel = Channel.objects.filter(uuid=channel, org=org).first()

                trigger = Trigger.objects.create(
                    org=org,
                    trigger_type=trigger_def[Trigger.EXPORT_TYPE],
                    keyword=trigger_def[Trigger.EXPORT_KEYWORD],
                    flow=flow,
                    created_by=user,
                    modified_by=user,
                    channel=channel,
                )

                for group in groups:
                    trigger.groups.add(group)

    @classmethod
    def get_triggers_of_type(cls, org, trigger_type):
        return Trigger.objects.filter(org=org, trigger_type=trigger_type, is_active=True, is_archived=False)

    @classmethod
    def catch_triggers(cls, entity, trigger_type, channel, referrer_id=None, extra=None):  # pragma: no cover
        if isinstance(entity, Msg):
            contact = entity.contact
            start_msg = entity
        elif isinstance(entity, ChannelEvent) or isinstance(entity, IVRCall):
            contact = entity.contact
            start_msg = Msg(org=entity.org, contact=contact, channel=entity.channel, created_on=timezone.now(), id=0)
        elif isinstance(entity, Contact):
            contact = entity
            start_msg = Msg(org=entity.org, contact=contact, channel=channel, created_on=timezone.now(), id=0)
        else:  # pragma: needs cover
            raise ValueError("Entity must be of type msg, call or contact")

        triggers = Trigger.get_triggers_of_type(entity.org, trigger_type)

        if trigger_type in [Trigger.TYPE_NEW_CONVERSATION, Trigger.TYPE_REFERRAL]:
            triggers = triggers.filter(models.Q(channel=channel) | models.Q(channel=None))

        if referrer_id is not None:
            triggers = triggers.filter(models.Q(referrer_id__iexact=referrer_id) | models.Q(referrer_id=""))

            # if we catch more than one trigger with a referrer_id, ignore the catchall
            if len(triggers) > 1:
                triggers = triggers.exclude(referrer_id="")
        elif trigger_type == Trigger.TYPE_REFERRAL:
            triggers = triggers.filter(referrer_id="")

        # is there a match for a group specific trigger?
        group_ids = contact.user_groups.values_list("pk", flat=True)
        group_triggers = triggers.filter(groups__in=group_ids).order_by("groups__name")

        # if we match with a group restriction, that takes precedence
        if group_triggers:
            triggers = group_triggers

        # otherwise, restrict to triggers that don't filter by group
        else:
            triggers = triggers.filter(groups=None)

        # only fire the first matching trigger
        if triggers:
            contact.ensure_unstopped()
            triggers[0].flow.start([], [contact], start_msg=start_msg, restart_participants=True, extra=extra)

        return bool(triggers)

    @classmethod
    def find_and_handle(cls, msg):
        words = tokenize(msg.text)

        # skip if message doesn't have any words
        if not words:
            return False

        # skip if message contact is currently active in a flow
        active_run_qs = FlowRun.objects.filter(
            is_active=True, contact=msg.contact, flow__is_active=True, flow__is_archived=False
        )
        active_run = active_run_qs.order_by("-created_on", "-pk").first()

        if active_run and active_run.flow.ignore_triggers and not active_run.is_completed():
            return False

        # find a matching keyword trigger with an active flow
        trigger = Trigger.objects.filter(
            org=msg.org,
            is_archived=False,
            is_active=True,
            trigger_type=cls.TYPE_KEYWORD,
            flow__is_archived=False,
            flow__is_active=True,
        )

        # if message text is only one word, then we can match 'only-word' triggers too
        match_types = (cls.MATCH_FIRST_WORD, cls.MATCH_ONLY_WORD) if len(words) == 1 else (cls.MATCH_FIRST_WORD,)
        trigger = trigger.filter(keyword__iexact=words[0], match_type__in=match_types)

        # trigger needs to match the contact's groups or be non-group specific
        trigger = trigger.filter(Q(groups__in=msg.contact.user_groups.all()) | Q(groups=None))

        trigger = trigger.prefetch_related("groups", "groups__contacts").order_by("groups__name").first()

        # if no trigger for contact groups find there is a no group trigger
        if not trigger:
            return False

        contact = msg.contact
        contact.ensure_unstopped()

        # if we have an associated flow, start this contact in it
        trigger.flow.start([], [contact], start_msg=msg, restart_participants=True)

        return True

    @classmethod
    def find_flow_for_inbound_call(cls, contact):

        groups_ids = contact.user_groups.values_list("pk", flat=True)

        # Check first if we have a trigger for the contact groups
        matching = (
            Trigger.objects.filter(
                is_archived=False,
                is_active=True,
                org=contact.org,
                trigger_type=Trigger.TYPE_INBOUND_CALL,
                flow__is_archived=False,
                flow__is_active=True,
                groups__in=groups_ids,
            )
            .order_by("groups__name")
            .prefetch_related("groups", "groups__contacts")
        )

        # If no trigger for contact groups find there is a no group trigger
        if not matching:
            matching = Trigger.objects.filter(
                is_archived=False,
                is_active=True,
                org=contact.org,
                trigger_type=Trigger.TYPE_INBOUND_CALL,
                flow__is_archived=False,
                flow__is_active=True,
                groups=None,
            ).prefetch_related("groups", "groups__contacts")

        if not matching:
            return None

        trigger = matching[0]
        return trigger.flow

    @classmethod
    def apply_action_archive(cls, user, triggers):
        for trigger in triggers:
            trigger.archive(user)

        return [each_trigger.pk for each_trigger in triggers]

    @classmethod
    def apply_action_restore(cls, user, triggers):
        restore_priority = triggers.order_by("-modified_on")
        trigger_scopes = set()

        # work through all the restored triggers in order of most recent used
        for trigger in restore_priority:
            trigger_scope = set(trigger.trigger_scopes())

            # if we haven't already restored a trigger with this scope
            if not trigger_scopes.intersection(trigger_scope):
                trigger.restore(user)
                trigger_scopes = trigger_scopes | trigger_scope

        return [t.pk for t in triggers]

    def fire(self):
        """
        Fires this trigger in response to a schedule
        """

        # do nothing if this trigger is no longer active
        if self.is_archived or not self.is_active:
            return

        groups = list(self.groups.all())
        contacts = list(self.contacts.all())

        # do nothing if there are no groups or contacts
        if not groups and not contacts:
            return

        start = FlowStart.create(self.flow, self.created_by, groups=groups, contacts=contacts)
        start.async_start()

    def as_export_def(self):
        """
        The definition of this trigger for export.
        """
        return {
            Trigger.EXPORT_TYPE: self.trigger_type,
            Trigger.EXPORT_KEYWORD: self.keyword,
            Trigger.EXPORT_FLOW: self.flow.as_export_ref(),
            Trigger.EXPORT_GROUPS: [group.as_export_ref() for group in self.groups.all()],
            Trigger.EXPORT_CHANNEL: self.channel.uuid if self.channel else None,
        }

    def release(self):
        """
        Releases this Trigger
        """

        self.delete()

        if self.schedule:
            self.schedule.delete()
