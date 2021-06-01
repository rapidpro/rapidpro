from typing import NamedTuple

from smartmin.models import SmartModel

from django.db import models
from django.utils import timezone
from django.utils.translation import ugettext_lazy as _

from temba.channels.models import Channel
from temba.contacts.models import Contact, ContactGroup
from temba.flows.models import Flow
from temba.orgs.models import Org


class Folder(NamedTuple):
    label: str
    title: str
    types: tuple


class Trigger(SmartModel):
    """
    A Trigger is used to start a user in a flow based on an event. For example, triggers might fire for missed calls,
    inbound messages starting with a keyword, or on a repeating schedule.
    """

    TYPE_KEYWORD = "K"
    TYPE_SCHEDULE = "S"
    TYPE_INBOUND_CALL = "V"
    TYPE_MISSED_CALL = "M"
    TYPE_NEW_CONVERSATION = "N"
    TYPE_REFERRAL = "R"
    TYPE_CATCH_ALL = "C"

    TRIGGER_TYPES = (
        (TYPE_KEYWORD, "Keyword"),
        (TYPE_SCHEDULE, "Schedule"),
        (TYPE_INBOUND_CALL, "Inbound Call"),
        (TYPE_MISSED_CALL, "Missed Call"),
        (TYPE_NEW_CONVERSATION, "New Conversation"),
        (TYPE_REFERRAL, "Referral"),
        (TYPE_CATCH_ALL, "Catch All"),
    )

    ALLOWED_FLOW_TYPES = {
        TYPE_KEYWORD: (Flow.TYPE_MESSAGE, Flow.TYPE_VOICE),
        TYPE_SCHEDULE: (Flow.TYPE_MESSAGE, Flow.TYPE_VOICE, Flow.TYPE_BACKGROUND),
        TYPE_INBOUND_CALL: (Flow.TYPE_VOICE,),
        TYPE_MISSED_CALL: (Flow.TYPE_MESSAGE, Flow.TYPE_VOICE),
        TYPE_NEW_CONVERSATION: (Flow.TYPE_MESSAGE,),
        TYPE_REFERRAL: (Flow.TYPE_MESSAGE,),
        TYPE_CATCH_ALL: (Flow.TYPE_MESSAGE, Flow.TYPE_VOICE),
    }

    FOLDER_KEYWORDS = "keywords"
    FOLDER_SCHEDULED = "scheduled"
    FOLDER_CALLS = "calls"
    FOLDER_SOCIAL_MEDIA = "social"
    FOLDER_CATCHALL = "catchall"
    FOLDERS = {
        FOLDER_KEYWORDS: Folder(_("Keywords"), _("Keyword Triggers"), (TYPE_KEYWORD,)),
        FOLDER_SCHEDULED: Folder(_("Scheduled"), _("Scheduled Triggers"), (TYPE_SCHEDULE,)),
        FOLDER_CALLS: Folder(_("Calls"), _("Call Triggers"), (TYPE_INBOUND_CALL, TYPE_MISSED_CALL)),
        FOLDER_SOCIAL_MEDIA: Folder(
            _("Social Media"),
            _("Social Media Triggers"),
            (TYPE_NEW_CONVERSATION, TYPE_REFERRAL),
        ),
        FOLDER_CATCHALL: Folder(_("Catch All"), _("Catch All Triggers"), (TYPE_CATCH_ALL,)),
    }

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

    org = models.ForeignKey(Org, on_delete=models.PROTECT, related_name="triggers")

    trigger_type = models.CharField(max_length=1, choices=TRIGGER_TYPES, default=TYPE_KEYWORD)

    is_archived = models.BooleanField(default=False)

    keyword = models.CharField(
        verbose_name=_("Keyword"),
        max_length=KEYWORD_MAX_LEN,
        null=True,
        blank=True,
        help_text=_("Word to match in the message text"),
    )

    referrer_id = models.CharField(max_length=255, null=True)

    flow = models.ForeignKey(
        Flow,
        on_delete=models.PROTECT,
        verbose_name=_("Flow"),
        help_text=_("Which flow will be started"),
        related_name="triggers",
    )

    # who trigger applies to
    groups = models.ManyToManyField(ContactGroup, related_name="triggers_included")
    exclude_groups = models.ManyToManyField(ContactGroup, related_name="triggers_excluded")
    contacts = models.ManyToManyField(Contact, related_name="triggers")  # scheduled triggers only

    schedule = models.OneToOneField("schedules.Schedule", on_delete=models.PROTECT, null=True, related_name="trigger")

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
    def create(cls, org, user, trigger_type, flow, channel=None, include_groups=(), **kwargs):
        trigger = cls.objects.create(
            org=org, trigger_type=trigger_type, flow=flow, channel=channel, created_by=user, modified_by=user, **kwargs
        )

        for group in include_groups:
            trigger.groups.add(group)

        # archive any conflicts
        trigger.archive_conflicts(user)

        if trigger.channel:
            trigger.channel.get_type().activate_trigger(trigger)

        return trigger

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

        if self.channel:
            self.channel.get_type().deactivate_trigger(self)

    def restore(self, user):
        self.modified_by = user
        self.is_archived = False
        self.save()

        # archive any conflicts
        self.archive_conflicts(user)

        if self.channel:
            self.channel.get_type().activate_trigger(self)

    def archive_conflicts(self, user):
        """
        Archives any triggers that conflict with this one
        """

        # schedule triggers can be duplicated
        if self.trigger_type == Trigger.TYPE_SCHEDULE:
            return

        matches = self.org.triggers.filter(is_active=True, is_archived=False, trigger_type=self.trigger_type)

        # if this trigger has a keyword, only archive others with the same keyword
        if self.keyword:
            matches = matches.filter(keyword=self.keyword)

        # if this trigger has groups, only archive others with the same group
        if self.groups.all():
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
        matches.exclude(id=self.id).update(is_archived=True, modified_on=timezone.now(), modified_by=user)

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
                    group = ContactGroup.get_user_group_by_name(org, group_spec["name"])

                if not group:
                    group = ContactGroup.create_static(org, user, group_spec["name"])  # pragma: needs cover

                if not group.is_active:  # pragma: needs cover
                    group.is_active = True
                    group.save()

                groups.append(group)

            flow = Flow.objects.get(org=org, uuid=trigger_def[Trigger.EXPORT_FLOW]["uuid"], is_active=True)

            # see if that trigger already exists
            existing_triggers = Trigger.objects.filter(org=org, trigger_type=trigger_def[Trigger.EXPORT_TYPE])

            if trigger_def[Trigger.EXPORT_KEYWORD]:
                existing_triggers = existing_triggers.filter(keyword__iexact=trigger_def[Trigger.EXPORT_KEYWORD])

            if groups:
                existing_triggers = existing_triggers.filter(groups__in=groups)

            exact_flow_trigger = existing_triggers.filter(flow=flow).order_by("-created_on").first()
            for tr in existing_triggers:
                if not tr.is_archived and tr != exact_flow_trigger:
                    tr.archive(user)

            if exact_flow_trigger:
                if exact_flow_trigger.is_archived:
                    exact_flow_trigger.restore(user)
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
    def apply_action_archive(cls, user, triggers):
        for trigger in triggers:
            trigger.archive(user)

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

    @classmethod
    def get_folder(cls, org, key: str):
        return cls.filter_folder(org.triggers.filter(is_active=True, is_archived=False), key)

    @classmethod
    def filter_folder(cls, qs, key: str):
        assert key in cls.FOLDERS, f"{key} is not a valid trigger folder"

        return qs.filter(trigger_type__in=cls.FOLDERS[key].types)

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
        Releases this trigger
        """

        self.delete()

        if self.schedule:
            self.schedule.delete()

    def __str__(self):
        return f'Trigger[type={self.trigger_type}, flow="{self.flow.name}"]'
