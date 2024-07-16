from smartmin.models import SmartModel

from django.contrib.postgres.fields import ArrayField
from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import Case, Q, When
from django.utils.translation import gettext_lazy as _

from temba.channels.models import Channel
from temba.contacts.models import Contact, ContactGroup
from temba.flows.models import Flow
from temba.orgs.models import Org


class TriggerType:
    """
    Base class for trigger types.
    """

    code = None  # single char code used for database model
    slug = None  # used for URLs
    name = None

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

    def get_instance_name(self, trigger):
        return f"{self.name} → {trigger.flow.name}"

    def export_def(self, trigger) -> dict:
        all_fields = {
            "trigger_type": trigger.trigger_type,
            "flow": trigger.flow.as_export_ref(),
            "channel": trigger.channel.as_export_ref() if trigger.channel else None,
            "groups": [group.as_export_ref() for group in trigger.groups.order_by("name")],
            "exclude_groups": [group.as_export_ref() for group in trigger.exclude_groups.order_by("name")],
            "keywords": trigger.keywords,
            "match_type": trigger.match_type,
        }
        return {f: all_fields[f] for f in self.export_fields}

    def clean_import_def(self, trigger_def: dict):
        """
        Validates a trigger definition being imported
        """
        for field in self.required_fields:
            if not trigger_def.get(field):
                raise ValidationError(_("Field '%(field)s' is required."), params={"field": field})


class ChannelTriggerType(TriggerType):
    """
    Base class for trigger types based on channel activity.
    """

    # channels with these schemes or role allowed for this type
    allowed_channel_schemes = ()
    allowed_channel_role = None

    export_fields = TriggerType.export_fields + ("channel",)


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
    TYPE_OPT_IN = "I"
    TYPE_OPT_OUT = "O"

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
    flow = models.ForeignKey(Flow, on_delete=models.PROTECT, related_name="triggers")
    priority = models.IntegerField()

    # who trigger applies to
    channel = models.ForeignKey(Channel, on_delete=models.PROTECT, null=True, related_name="triggers")
    groups = models.ManyToManyField(ContactGroup, related_name="triggers_included")
    exclude_groups = models.ManyToManyField(ContactGroup, related_name="triggers_excluded")
    contacts = models.ManyToManyField(Contact, related_name="triggers")  # scheduled triggers only

    keywords = ArrayField(models.CharField(max_length=KEYWORD_MAX_LEN), null=True)
    match_type = models.CharField(max_length=1, choices=MATCH_TYPES, null=True)
    referrer_id = models.CharField(max_length=255, null=True)
    schedule = models.OneToOneField("schedules.Schedule", on_delete=models.PROTECT, null=True, related_name="trigger")

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
        keywords=None,
        schedule=None,
        match_type=None,
        **kwargs,
    ):
        assert flow.flow_type != Flow.TYPE_SURVEY, "can't create triggers for surveyor flows"
        assert trigger_type != cls.TYPE_KEYWORD or (keywords and match_type), "keywords required for keyword triggers"
        assert trigger_type != cls.TYPE_SCHEDULE or schedule, "schedule must be provided for scheduled triggers"
        assert trigger_type == cls.TYPE_SCHEDULE or not contacts, "contacts can only be provided for scheduled triggers"

        trigger = cls.objects.create(
            org=org,
            trigger_type=trigger_type,
            flow=flow,
            channel=channel,
            keywords=keywords,
            schedule=schedule,
            match_type=match_type,
            priority=cls._priority(channel, groups, exclude_groups),
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
        for conflict in trigger._get_conflicts():
            conflict.archive(user)

        if trigger.channel:
            trigger.channel.type.activate_trigger(trigger)

        return trigger

    @classmethod
    def _priority(cls, channel, groups, exclude_groups) -> int:
        """
        Calculate priority based on specificity
        """
        priority = 0
        if channel:
            priority += 4
        if groups:
            priority += 2
        if exclude_groups:
            priority += 1
        return priority

    def archive(self, user):
        self.modified_by = user
        self.is_archived = True
        self.save(update_fields=("modified_by", "modified_on", "is_archived"))

        if self.schedule:
            self.schedule.pause()

        if self.channel:
            self.channel.type.deactivate_trigger(self)

    def restore(self, user):
        self.modified_by = user
        self.is_archived = False
        self.save(update_fields=("modified_by", "modified_on", "is_archived"))

        if self.schedule:
            self.schedule.resume()

        # archive any conflicts
        for conflict in self._get_conflicts():
            conflict.archive(user)

        if self.channel:
            self.channel.type.activate_trigger(self)

    def _get_conflicts(self):
        return Trigger.get_conflicts(
            self.org, self.trigger_type, self.channel, self.groups.all(), self.keywords, self.referrer_id
        ).exclude(id=self.id)

    @classmethod
    def get_conflicts(
        cls,
        org,
        trigger_type: str,
        channel=None,
        groups=None,
        keywords: list[str] = None,
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

        if keywords:
            conflicts = conflicts.filter(keywords__overlap=keywords)

        if referrer_id:
            conflicts = conflicts.filter(referrer_id__iexact=referrer_id)
        else:
            conflicts = conflicts.filter(Q(referrer_id=None) | Q(referrer_id=""))

        return conflicts

    @classmethod
    def clean_import_def(cls, trigger_def: dict):
        type_code = trigger_def.get("trigger_type", "")
        try:
            trigger_type = cls.get_type(code=type_code)
        except KeyError:
            raise ValidationError(_("%(type)s is not a valid trigger type"), params={"type": type_code})

        # if channel is just a UUID, convert to reference object
        if "channel" in trigger_def and isinstance(trigger_def["channel"], str):
            trigger_def["channel"] = {"uuid": trigger_def["channel"], "name": ""}

        trigger_type.clean_import_def(trigger_def)

    @classmethod
    def import_triggers(cls, org, user, trigger_defs, same_site=False):
        """
        Import triggers from a list of exported triggers
        """

        for trigger_def in trigger_defs:
            cls.import_def(org, user, trigger_def, same_site=same_site)

    @classmethod
    def import_def(cls, org, user, definition: dict, same_site: bool = False):
        trigger_type = cls.get_type(code=definition["trigger_type"])

        # only consider fields which are valid for this type of trigger
        trigger_def = {k: v for k, v in definition.items() if k in trigger_type.export_fields}

        # resolve groups, channel and flow
        groups = cls._resolve_import_groups(org, user, same_site, trigger_def["groups"])
        exclude_groups = cls._resolve_import_groups(org, user, same_site, trigger_def.get("exclude_groups", []))

        channel = None
        if "channel" in trigger_def and isinstance(trigger_def["channel"], dict):
            channel = org.channels.filter(uuid=trigger_def["channel"]["uuid"], is_active=True).first()

        flow_uuid = trigger_def["flow"]["uuid"]
        flow = org.flows.get(uuid=flow_uuid, is_active=True)

        keywords = trigger_def.get("keywords")
        match_type = None
        if trigger_type.code == Trigger.TYPE_KEYWORD:
            match_type = trigger_def.get("match_type", Trigger.MATCH_FIRST_WORD)

        # see if that trigger already exists
        conflicts = cls.get_conflicts(
            org,
            trigger_def["trigger_type"],
            groups=groups,
            keywords=keywords,
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

            return exact_match
        else:
            return cls.create(
                org,
                user,
                trigger_def["trigger_type"],
                flow,
                channel=channel,
                groups=groups,
                exclude_groups=exclude_groups,
                keywords=keywords,
                match_type=match_type,
            )

    @classmethod
    def _resolve_import_groups(cls, org, user, same_site: bool, specs):
        groups = []
        for spec in specs:
            group = None

            if same_site:  # pragma: needs cover
                group = ContactGroup.get_groups(org).filter(uuid=spec["uuid"]).first()

            if not group:
                group = ContactGroup.get_group_by_name(org, spec["name"])

            if not group:
                group = ContactGroup.create_manual(org, user, spec["name"])  # pragma: needs cover

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
        # work through all the restored triggers in order of most recent used
        for trigger in triggers.order_by("-modified_on"):
            trigger.restore(user)

    @classmethod
    def apply_action_delete(cls, user, triggers):
        for trigger in triggers:
            trigger.delete()

    def as_export_def(self) -> dict:
        """
        The definition of this trigger for export.
        """

        return self.type.export_def(self)

    @classmethod
    def get_type(cls, *, code: str = None, slug: str = None):
        from .types import TYPES_BY_CODE, TYPES_BY_SLUG

        return TYPES_BY_CODE[code] if code else TYPES_BY_SLUG[slug]

    @property
    def type(self):
        return self.get_type(code=self.trigger_type)

    @property
    def name(self):
        return self.type.get_instance_name(self)

    @classmethod
    def type_order(cls):
        """
        Creates an order by expression based on order of type declarations.
        """

        from .types import TYPES_BY_CODE

        whens = [When(trigger_type=t.code, then=i) for i, t in enumerate(TYPES_BY_CODE.values())]
        return Case(*whens, default=100).asc()

    def release(self, user):
        """
        Releases this trigger
        """

        schedule = self.schedule

        self.schedule = None
        self.is_active = False
        self.modified_by = user
        self.save(update_fields=("schedule", "is_active", "modified_by", "modified_on"))

        if schedule:
            schedule.delete()

    def __repr__(self):
        return f'<Trigger: id={self.id} type={self.trigger_type} flow="{self.flow.name}">'

    class Meta:
        verbose_name = _("Trigger")
        verbose_name_plural = _("Triggers")

        constraints = [
            # ensure that scheduled triggers have a schedule
            models.CheckConstraint(
                check=~Q(trigger_type="S") | Q(schedule__isnull=False) | Q(is_active=False),
                name="triggers_scheduled_trigger_has_schedule",
            ),
        ]
