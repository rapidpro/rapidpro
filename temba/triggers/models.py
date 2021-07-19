from smartmin.models import SmartModel

from django.db import models
from django.db.models import Q
from django.utils import timezone
from django.utils.translation import ugettext_lazy as _

from temba.channels.models import Channel
from temba.contacts.models import Contact, ContactGroup
from temba.flows.models import Flow
from temba.orgs.models import Org


class TriggerType:
    """
    Base class for trigger types
    """

    code = None  # single char code used for database model
    slug = None  # used for URLs
    name = None
    title = None  # used for list page title

    # flow types allowed for this type
    allowed_flow_types = ()

    # whether the type should be included in exports
    exportable = True

    # which fields to include in exports
    export_fields = ("trigger_type", "flow", "groups", "exclude_groups")

    # which field must be non-empty when importing
    required_fields = ("trigger_type", "flow")

    # form class used for creation and updating
    form = None

    def export_def(self, trigger) -> dict:
        all_fields = {
            "trigger_type": trigger.trigger_type,
            "flow": trigger.flow.as_export_ref(),
            "groups": [group.as_export_ref() for group in trigger.groups.order_by("name")],
            "exclude_groups": [group.as_export_ref() for group in trigger.exclude_groups.order_by("name")],
            "channel": trigger.channel.uuid if trigger.channel else None,
            "keyword": trigger.keyword,
        }
        return {f: all_fields[f] for f in self.export_fields}

    def validate_import_def(self, trigger_def: dict):
        """
        Validates a trigger definition being imported
        """
        for field in self.required_fields:
            if not trigger_def.get(field):
                raise ValueError(f"Field '{field}' is required.")


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
    TYPE_CLOSED_TICKET = "T"
    TYPE_CATCH_ALL = "C"

    KEYWORD_MAX_LEN = 16

    MATCH_FIRST_WORD = "F"
    MATCH_ONLY_WORD = "O"

    MATCH_TYPES = (
        (MATCH_FIRST_WORD, _("Message starts with the keyword")),
        (MATCH_ONLY_WORD, _("Message contains only the keyword")),
    )

    org = models.ForeignKey(Org, on_delete=models.PROTECT, related_name="triggers")
    trigger_type = models.CharField(max_length=1, default=TYPE_KEYWORD)
    is_archived = models.BooleanField(default=False)

    keyword = models.CharField(
        verbose_name=_("Keyword"),
        max_length=KEYWORD_MAX_LEN,
        null=True,
        blank=True,
        help_text=_("Word to match in the message text."),
    )

    referrer_id = models.CharField(max_length=255, null=True)

    flow = models.ForeignKey(
        Flow,
        on_delete=models.PROTECT,
        verbose_name=_("Flow"),
        help_text=_("Which flow will be started."),
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
        help_text=_("How to match a message with a keyword."),
    )

    channel = models.ForeignKey(
        Channel,
        on_delete=models.PROTECT,
        verbose_name=_("Channel"),
        null=True,
        related_name="triggers",
        help_text=_("The associated channel."),
    )

    @classmethod
    def create(
        cls,
        org,
        user,
        trigger_type,
        flow,
        *,
        channel=None,
        groups=(),
        exclude_groups=(),
        contacts=(),
        keyword=None,
        schedule=None,
        **kwargs,
    ):
        assert flow.flow_type != Flow.TYPE_SURVEY, "can't create triggers for surveyor flows"
        assert trigger_type != cls.TYPE_KEYWORD or keyword, "keyword can't be empty for keyword triggers"
        assert trigger_type != cls.TYPE_SCHEDULE or schedule, "schedule must be provided for scheduled triggers"
        assert (
            trigger_type == cls.TYPE_SCHEDULE or not contacts
        ), "contacts can only be provided for scheduled triggers"

        trigger = cls.objects.create(
            org=org,
            trigger_type=trigger_type,
            flow=flow,
            channel=channel,
            keyword=keyword,
            schedule=schedule,
            created_by=user,
            modified_by=user,
            **kwargs,
        )

        for group in groups:
            trigger.groups.add(group)
        for group in exclude_groups:
            trigger.exclude_groups.add(group)
        for contact in contacts:
            trigger.contacts.add(contact)

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
        self.save(update_fields=("modified_by", "modified_on", "is_archived"))

        if self.channel:
            self.channel.get_type().deactivate_trigger(self)

    def restore(self, user):
        self.modified_by = user
        self.is_archived = False
        self.save(update_fields=("modified_by", "modified_on", "is_archived"))

        # archive any conflicts
        self.archive_conflicts(user)

        if self.channel:
            self.channel.get_type().activate_trigger(self)

    def archive_conflicts(self, user):
        """
        Archives any triggers that conflict with this one
        """

        conflicts = self.get_conflicts(
            self.org, self.trigger_type, self.channel, self.groups.all(), self.keyword, self.referrer_id
        ).exclude(id=self.id)

        conflicts.update(is_archived=True, modified_on=timezone.now(), modified_by=user)

    @classmethod
    def get_conflicts(
        cls,
        org,
        trigger_type: str,
        channel=None,
        groups=None,
        keyword: str = None,
        referrer_id: str = None,
        include_archived=False,
    ):
        """
        Gets the triggers that would conflict with the given trigger field values
        """

        if trigger_type == Trigger.TYPE_SCHEDULE:  # schedule triggers never conflict
            return cls.objects.none()

        conflicts = org.triggers.filter(is_active=True, trigger_type=trigger_type)
        if not include_archived:
            conflicts = conflicts.filter(is_archived=False)

        if channel:
            conflicts = conflicts.filter(channel=channel)
        else:
            conflicts = conflicts.filter(channel=None)

        if groups:
            conflicts = conflicts.filter(groups__in=groups)  # any overlap in groups is a conflict
        else:
            conflicts = conflicts.filter(groups=None)

        if keyword:
            conflicts = conflicts.filter(keyword__iexact=keyword)

        if referrer_id:
            conflicts = conflicts.filter(referrer_id__iexact=referrer_id)
        else:
            conflicts = conflicts.filter(Q(referrer_id=None) | Q(referrer_id=""))

        return conflicts

    @classmethod
    def validate_import_def(cls, trigger_def: dict):
        type_code = trigger_def.get("trigger_type", "")
        try:
            trigger_type = cls.get_type(code=type_code)
        except KeyError:
            raise ValueError(f"{type_code} is not a valid trigger type")

        trigger_type.validate_import_def(trigger_def)

    @classmethod
    def import_triggers(cls, org, user, trigger_defs, same_site=False):
        """
        Import triggers from a list of exported triggers
        """

        for trigger_def in trigger_defs:
            trigger_type = cls.get_type(code=trigger_def["trigger_type"])

            # old exports might include scheduled triggers without schedules
            if not trigger_type.exportable:
                continue

            # only consider fields which are valid for this type of trigger
            trigger_def = {k: v for k, v in trigger_def.items() if k in trigger_type.export_fields}

            # resolve groups, channel and flow
            groups = cls._resolve_import_groups(org, user, same_site, trigger_def["groups"])
            exclude_groups = cls._resolve_import_groups(org, user, same_site, trigger_def.get("exclude_groups", []))

            channel_uuid = trigger_def.get("channel")
            channel = org.channels.filter(uuid=channel_uuid, is_active=True).first() if channel_uuid else None

            flow_uuid = trigger_def["flow"]["uuid"]
            flow = org.flows.get(uuid=flow_uuid, is_active=True)

            # see if that trigger already exists
            conflicts = cls.get_conflicts(
                org,
                trigger_def["trigger_type"],
                groups=groups,
                keyword=trigger_def.get("keyword"),
                channel=channel,
                include_archived=True,
            )

            # if one of our conflicts is an exact match, we can keep it
            exact_match = conflicts.filter(flow=flow).order_by("-created_on").first()
            if exact_match and set(exact_match.exclude_groups.all()) != set(exclude_groups):
                exact_match = None

            if exact_match:
                # tho maybe it needs restored...
                if exact_match.is_archived:
                    exact_match.restore(user)
            else:
                cls.create(
                    org,
                    user,
                    trigger_def["trigger_type"],
                    flow,
                    channel=channel,
                    groups=groups,
                    exclude_groups=exclude_groups,
                    keyword=trigger_def.get("keyword"),
                )

    @classmethod
    def _resolve_import_groups(cls, org, user, same_site: bool, specs):
        groups = []
        for spec in specs:
            group = None

            if same_site:  # pragma: needs cover
                group = ContactGroup.user_groups.filter(org=org, uuid=spec["uuid"]).first()

            if not group:
                group = ContactGroup.get_user_group_by_name(org, spec["name"])

            if not group:
                group = ContactGroup.create_static(org, user, spec["name"])  # pragma: needs cover

            if not group.is_active:  # pragma: needs cover
                group.is_active = True
                group.save()

            groups.append(group)

        return groups

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

    def as_export_def(self) -> dict:
        """
        The definition of this trigger for export.
        """
        export_def = self.type.export_def(self)

        # for backwards compatibility keyword needs to always be present even if the trigger type doesn't use it
        if "keyword" not in export_def:
            export_def["keyword"] = None

        return export_def

    @classmethod
    def get_type(cls, *, code: str = None, slug: str = None):
        from .types import TYPES_BY_CODE, TYPES_BY_SLUG

        return TYPES_BY_CODE[code] if code else TYPES_BY_SLUG[slug]

    @property
    def type(self):
        return self.get_type(code=self.trigger_type)

    def release(self, user):
        """
        Releases this trigger
        """

        self.is_active = False
        self.modified_by = user
        self.save(update_fields=("is_active", "modified_by", "modified_on"))

        if self.schedule:
            self.schedule.release(user)

    def delete(self):
        super().delete()

        if self.schedule:
            self.schedule.delete()

    def __str__(self):
        return f'Trigger[type={self.trigger_type}, flow="{self.flow.name}"]'
