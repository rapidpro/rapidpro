from smartmin.models import SmartModel

from django.db import models
from django.db.models import Q
from django.utils import timezone
from django.utils.translation import gettext_lazy as _, ngettext

from temba import mailroom
from temba.contacts.models import Contact, ContactField, ContactGroup
from temba.flows.models import Flow
from temba.msgs.models import Msg
from temba.orgs.models import Org
from temba.utils import json, on_transaction_commit
from temba.utils.models import TembaModel, TembaUUIDMixin, TranslatableField, delete_in_batches


class Campaign(TembaModel):
    org = models.ForeignKey(Org, related_name="campaigns", on_delete=models.PROTECT)
    group = models.ForeignKey(ContactGroup, on_delete=models.PROTECT, related_name="campaigns")
    is_archived = models.BooleanField(default=False)

    @classmethod
    def create(cls, org, user, name, group):
        assert cls.is_valid_name(name), f"'{name}' is not a valid campaign name"

        return cls.objects.create(org=org, name=name, group=group, created_by=user, modified_by=user)

    def archive(self, user):
        self.is_archived = True
        self.modified_by = user
        self.modified_on = timezone.now()
        self.save(update_fields=("is_archived", "modified_by", "modified_on"))

    def recreate_events(self):
        """
        Recreates all the events in this campaign - called when something like the group changes.
        """

        for event in self.get_events():
            event.recreate()

    def schedule_events_async(self):
        """
        Schedules all the events in this campaign - called when something like the group changes.
        """

        for event in self.get_events():
            event.schedule_async()

    @classmethod
    def import_campaigns(cls, org, user, campaign_defs, same_site=False) -> list:
        """
        Import campaigns from a list of exported campaigns
        """

        imported = []

        for campaign_def in campaign_defs:
            name = cls.clean_name(campaign_def["name"])
            group_ref = campaign_def["group"]
            campaign = None
            group = None

            # if export is from this site, lookup objects by UUID
            if same_site:
                group = ContactGroup.get_or_create(org, user, group_ref["name"], uuid=group_ref["uuid"])

                campaign = Campaign.objects.filter(org=org, uuid=campaign_def["uuid"]).first()
                if campaign:  # pragma: needs cover
                    campaign.name = Campaign.get_unique_name(org, name, ignore=campaign)
                    campaign.save()

            # fall back to lookups by name
            if not group:
                group = ContactGroup.get_or_create(org, user, group_ref["name"])

            if not campaign:
                campaign = Campaign.objects.filter(org=org, name=name).first()

            if not campaign:
                campaign_name = Campaign.get_unique_name(org, name)
                campaign = Campaign.create(org, user, campaign_name, group)
            else:
                campaign.group = group
                campaign.save()

            # deactivate all of our events, we'll recreate these
            for event in campaign.events.all():
                event.release(user)

            # fill our campaign with events
            for event_spec in campaign_def["events"]:
                field_key = event_spec["relative_to"]["key"]

                if field_key in ("created_on", "last_seen_on"):
                    relative_to = org.fields.filter(key=field_key, is_system=True).first()
                else:
                    relative_to = ContactField.get_or_create(
                        org,
                        user,
                        key=field_key,
                        name=event_spec["relative_to"]["label"],
                        value_type=ContactField.TYPE_DATETIME,
                    )

                start_mode = event_spec.get("start_mode", CampaignEvent.MODE_INTERRUPT)

                # create our message flow for message events
                if event_spec["event_type"] == CampaignEvent.TYPE_MESSAGE:
                    message = event_spec["message"]
                    base_language = event_spec.get("base_language")

                    # force the message value into a dict
                    if not isinstance(message, dict):
                        try:
                            message = json.loads(message)
                        except ValueError:
                            # if it's not a language dict, turn it into one
                            message = dict(base=message)
                            base_language = "base"

                    # change base to und
                    if "base" in message:
                        message["und"] = message["base"]
                        del message["base"]
                        base_language = "und"

                    # ensure base language is valid
                    if base_language not in message:  # pragma: needs cover
                        base_language = next(iter(message))

                    event = CampaignEvent.create_message_event(
                        org,
                        user,
                        campaign,
                        relative_to,
                        event_spec["offset"],
                        event_spec["unit"],
                        message,
                        event_spec["delivery_hour"],
                        base_language=base_language,
                        start_mode=start_mode,
                    )
                    event.update_flow_name()
                else:
                    flow = Flow.objects.filter(
                        org=org, is_active=True, is_system=False, uuid=event_spec["flow"]["uuid"]
                    ).first()
                    if flow:
                        CampaignEvent.create_flow_event(
                            org,
                            user,
                            campaign,
                            relative_to,
                            event_spec["offset"],
                            event_spec["unit"],
                            flow,
                            event_spec["delivery_hour"],
                            start_mode=start_mode,
                        )

            imported.append(campaign)

        return imported

    @classmethod
    def apply_action_archive(cls, user, campaigns):
        for campaign in campaigns:
            campaign.archive(user)

    @classmethod
    def apply_action_restore(cls, user, campaigns):
        campaigns.update(is_archived=False, modified_by=user, modified_on=timezone.now())

        for campaign in campaigns:
            # for any flow events, ensure flows are restored as well
            events = (
                campaign.events.filter(is_active=True, event_type=CampaignEvent.TYPE_FLOW)
                .exclude(flow=None)
                .select_related("flow")
            )
            for event in events:
                event.flow.restore(user)

            campaign.schedule_events_async()

    def get_events(self):
        return self.events.filter(is_active=True).order_by("id")

    def as_export_def(self):
        """
        The definition of this campaign for export. Note this only includes references to the dependent
        flows which will be exported separately.
        """
        events = []

        for event in self.events.filter(is_active=True).order_by("flow__uuid"):
            event_definition = {
                "uuid": str(event.uuid),
                "offset": event.offset,
                "unit": event.unit,
                "event_type": event.event_type,
                "delivery_hour": event.delivery_hour,
                "message": event.message,
                "relative_to": dict(label=event.relative_to.name, key=event.relative_to.key),  # TODO should be key/name
                "start_mode": event.start_mode,
            }

            # only include the flow definition for standalone flows
            if event.event_type == CampaignEvent.TYPE_FLOW:
                event_definition["flow"] = event.flow.as_export_ref()

            # include the flow base language for message flows
            elif event.event_type == CampaignEvent.TYPE_MESSAGE:
                event_definition["base_language"] = event.flow.base_language

            events.append(event_definition)

        return {
            "uuid": str(self.uuid),
            "name": self.name,
            "group": self.group.as_export_ref(),
            "events": events,
        }

    def get_sorted_events(self):
        """
        Returns campaign events sorted by their actual offset with event flow definitions on the current export version
        """
        events = list(self.events.filter(is_active=True))

        return sorted(events, key=lambda e: (e.relative_to.id, e.minute_offset()))

    def delete(self):
        """
        Deletes this campaign completely
        """
        for event in self.events.all():
            event.delete()

        super().delete()

    class Meta:
        verbose_name = _("Campaign")
        verbose_name_plural = _("Campaigns")


class CampaignEvent(TembaUUIDMixin, SmartModel):
    """
    An event within a campaign that can send a message to a contact or start them in a flow
    """

    TYPE_FLOW = "F"
    TYPE_MESSAGE = "M"
    TYPE_CHOICES = ((TYPE_FLOW, "Flow Event"), (TYPE_MESSAGE, "Message Event"))

    UNIT_MINUTES = "M"
    UNIT_HOURS = "H"
    UNIT_DAYS = "D"
    UNIT_WEEKS = "W"
    UNIT_CHOICES = (
        (UNIT_MINUTES, _("Minutes")),
        (UNIT_HOURS, _("Hours")),
        (UNIT_DAYS, _("Days")),
        (UNIT_WEEKS, _("Weeks")),
    )

    MODE_INTERRUPT = "I"
    MODE_SKIP = "S"
    MODE_PASSIVE = "P"
    START_MODES_CHOICES = ((MODE_INTERRUPT, "Interrupt"), (MODE_SKIP, "Skip"), (MODE_PASSIVE, "Passive"))

    campaign = models.ForeignKey(Campaign, on_delete=models.PROTECT, related_name="events")

    event_type = models.CharField(max_length=1, choices=TYPE_CHOICES, default=TYPE_FLOW)

    # the contact specific date value this is event is based on
    relative_to = models.ForeignKey(ContactField, on_delete=models.PROTECT, related_name="campaign_events")

    # offset from that date value (positive is after, negative is before)
    offset = models.IntegerField(default=0)

    # the unit for the offset, e.g. days, weeks
    unit = models.CharField(max_length=1, choices=UNIT_CHOICES, default=UNIT_DAYS)

    # the flow that will be triggered by this event
    flow = models.ForeignKey(Flow, on_delete=models.PROTECT, related_name="campaign_events")

    # what should happen to other runs when this event is triggered
    start_mode = models.CharField(max_length=1, choices=START_MODES_CHOICES, default=MODE_INTERRUPT)

    # when sending single message events, we store the message here (as well as on the flow) for convenience
    message = TranslatableField(max_length=Msg.MAX_TEXT_LEN, null=True)

    # can also specify the hour during the day that the even should be triggered
    delivery_hour = models.IntegerField(default=-1)

    @classmethod
    def create_message_event(
        cls,
        org,
        user,
        campaign,
        relative_to,
        offset,
        unit,
        message,
        delivery_hour=-1,
        base_language=None,
        start_mode=MODE_INTERRUPT,
    ):
        assert campaign.org == org, "org mismatch"

        if relative_to.value_type != ContactField.TYPE_DATETIME:
            raise ValueError(
                f"Contact fields for CampaignEvents must have a datetime type, got {relative_to.value_type}."
            )

        if isinstance(message, str):
            base_language = org.flow_languages[0]
            message = {base_language: message}

        flow = Flow.create_single_message(org, user, message, base_language)

        return cls.objects.create(
            campaign=campaign,
            relative_to=relative_to,
            offset=offset,
            unit=unit,
            event_type=cls.TYPE_MESSAGE,
            message=message,
            flow=flow,
            delivery_hour=delivery_hour,
            start_mode=start_mode,
            created_by=user,
            modified_by=user,
        )

    @classmethod
    def create_flow_event(
        cls, org, user, campaign, relative_to, offset, unit, flow, delivery_hour=-1, start_mode=MODE_INTERRUPT
    ):
        if campaign.org != org:
            raise ValueError("Org mismatch")

        if relative_to.value_type != ContactField.TYPE_DATETIME:
            raise ValueError(
                f"Contact fields for CampaignEvents must have a datetime type, got '{relative_to.value_type}'."
            )

        return cls.objects.create(
            campaign=campaign,
            relative_to=relative_to,
            offset=offset,
            unit=unit,
            event_type=cls.TYPE_FLOW,
            flow=flow,
            start_mode=start_mode,
            delivery_hour=delivery_hour,
            created_by=user,
            modified_by=user,
        )

    @classmethod
    def get_hour_choices(cls):
        hours = [(-1, "during the same hour"), (0, "at Midnight")]
        period = "a.m."
        for i in range(1, 24):
            hour = i
            if i >= 12:
                period = "p.m."
                if i > 12:
                    hour -= 12
            hours.append((i, "at %s:00 %s" % (hour, period)))
        return hours

    @property
    def name(self):
        return f"{self.campaign.name} ({self.offset_display} {self.relative_to.name})"

    def get_message(self, contact=None):
        if not self.message:
            return None

        message = None
        if contact and contact.language and contact.language in self.message:
            message = self.message[contact.language]

        if not message:
            message = self.message[self.flow.base_language]

        return message

    def update_flow_name(self):
        """
        Updates our flow name to include our Event id, keeps flow names from colliding. No-op for non-message events.
        """
        if self.event_type != self.TYPE_MESSAGE:
            return

        self.flow.name = "Single Message (%d)" % self.id
        self.flow.save(update_fields=["name"])

    def minute_offset(self):
        """
        Returns an offset that can be used to sort events that go against the same relative_to variable.
        """
        # by default our offset is in minutes
        offset = self.offset

        if self.unit == self.UNIT_HOURS:  # pragma: needs cover
            offset = self.offset * 60
        elif self.unit == self.UNIT_DAYS:
            offset = self.offset * 60 * 24
        elif self.unit == self.UNIT_WEEKS:
            offset = self.offset * 60 * 24 * 7

        # if there is a specified hour, use that
        if self.delivery_hour != -1:
            offset += self.delivery_hour * 60

        return offset

    @property
    def offset_display(self):
        """
        Returns the offset and units as a human readable string
        """
        count = abs(self.offset)
        if self.offset < 0:
            if self.unit == "M":
                return ngettext("%d minute before", "%d minutes before", count) % count
            elif self.unit == "H":
                return ngettext("%d hour before", "%d hours before", count) % count
            elif self.unit == "D":
                return ngettext("%d day before", "%d days before", count) % count
            elif self.unit == "W":
                return ngettext("%d week before", "%d weeks before", count) % count
        elif self.offset > 0:
            if self.unit == "M":
                return ngettext("%d minute after", "%d minutes after", count) % count
            elif self.unit == "H":
                return ngettext("%d hour after", "%d hours after", count) % count
            elif self.unit == "D":
                return ngettext("%d day after", "%d days after", count) % count
            elif self.unit == "W":
                return ngettext("%d week after", "%d weeks after", count) % count
        else:
            return _("on")

    def schedule_async(self):
        on_transaction_commit(lambda: mailroom.queue_schedule_campaign_event(self))

    def recreate(self):
        """
        Cleaning up millions of event fires would be expensive so instead we treat campaign events as immutable objects
        and when a change is made that would invalidate existing event fires, we deactivate the event and recreate it.
        The event fire handling code knows to ignore event fires for deactivated event.
        """
        self.release(self.created_by)

        # clone our event into a new event
        if self.event_type == CampaignEvent.TYPE_FLOW:
            return CampaignEvent.create_flow_event(
                self.campaign.org,
                self.created_by,
                self.campaign,
                self.relative_to,
                self.offset,
                self.unit,
                self.flow,
                self.delivery_hour,
                self.start_mode,
            )

        elif self.event_type == CampaignEvent.TYPE_MESSAGE:
            return CampaignEvent.create_message_event(
                self.campaign.org,
                self.created_by,
                self.campaign,
                self.relative_to,
                self.offset,
                self.unit,
                self.message,
                self.delivery_hour,
                self.flow.base_language,
                self.start_mode,
            )

    def release(self, user):
        """
        Marks the event inactive and releases flows for single message flows
        """
        # we need to be inactive so our fires are noops
        self.is_active = False
        self.modified_by = user
        self.save(update_fields=("is_active", "modified_by", "modified_on"))

        # if flow isn't a user created flow we can delete it too
        if self.event_type == CampaignEvent.TYPE_MESSAGE:
            self.flow.release(user)

    def delete(self):
        """
        Deletes this event completely along with associated fires and starts.
        """

        delete_in_batches(self.fires.all())

        for start in self.flow_starts.all():
            start.delete()

        # and ourselves
        super().delete()

    def __repr__(self):
        return f'<Event: id={self.id} relative_to={self.relative_to.key} offset={self.offset} flow="{self.flow.name}">'

    class Meta:
        verbose_name = _("Campaign Event")
        verbose_name_plural = _("Campaign Events")


class EventFire(models.Model):
    """
    A scheduled firing of a campaign event for a particular contact
    """

    RESULT_FIRED = "F"
    RESULT_SKIPPED = "S"
    RESULTS = ((RESULT_FIRED, "Fired"), (RESULT_SKIPPED, "Skipped"))

    event = models.ForeignKey(CampaignEvent, on_delete=models.PROTECT, related_name="fires")

    contact = models.ForeignKey(Contact, on_delete=models.PROTECT, related_name="campaign_fires")

    # when the event should be fired for this contact
    scheduled = models.DateTimeField()

    # when the event was fired fir this contact or null if we haven't been fired
    fired = models.DateTimeField(null=True)

    # result of this event fire or null if we haven't been fired
    fired_result = models.CharField(max_length=1, null=True, choices=RESULTS)

    def get_relative_to_value(self):
        value = self.contact.get_field_value(self.event.relative_to)
        return value.replace(second=0, microsecond=0) if value else None

    def __str__(self):  # pragma: no cover
        return f"EventFire[event={self.event.uuid}, contact={self.contact.uuid}, scheduled={self.scheduled}]"

    class Meta:
        ordering = ("scheduled",)
        indexes = [
            models.Index(name="eventfires_unfired", fields=("scheduled",), condition=Q(fired=None)),
        ]
        constraints = [
            # used to prevent adding duplicate fires for the same event and contact
            models.UniqueConstraint(
                name="eventfires_unfired_unique", fields=("event_id", "contact_id"), condition=Q(fired=None)
            )
        ]
