from datetime import timedelta

from django.db import models
from django.db.models import Model
from django.utils import timezone
from django.utils.translation import ugettext_lazy as _

from temba.contacts.models import Contact, ContactField, ContactGroup
from temba.flows.models import Flow
from temba.msgs.models import Msg
from temba.orgs.models import Org
from temba.utils import json, on_transaction_commit
from temba.utils.models import TembaModel, TranslatableField
from temba.values.constants import Value


class Campaign(TembaModel):
    MAX_NAME_LEN = 255

    EXPORT_UUID = "uuid"
    EXPORT_NAME = "name"
    EXPORT_GROUP = "group"
    EXPORT_EVENTS = "events"

    org = models.ForeignKey(Org, related_name="campaigns", on_delete=models.PROTECT)

    name = models.CharField(max_length=MAX_NAME_LEN, help_text=_("The name of this campaign"))

    group = models.ForeignKey(
        ContactGroup,
        on_delete=models.PROTECT,
        help_text=_("The group this campaign operates on"),
        related_name="campaigns",
    )

    is_archived = models.BooleanField(default=False)

    @classmethod
    def create(cls, org, user, name, group):
        return cls.objects.create(org=org, name=name, group=group, created_by=user, modified_by=user)

    @classmethod
    def get_unique_name(cls, org, base_name, ignore=None):
        """
        Generates a unique campaign name based on the given base name
        """
        name = base_name[:255].strip()

        count = 2
        while True:
            campaigns = Campaign.objects.filter(name=name, org=org, is_active=True)
            if ignore:  # pragma: needs cover
                campaigns = campaigns.exclude(pk=ignore.pk)

            if not campaigns.exists():
                break

            name = "%s %d" % (base_name[:255].strip(), count)
            count += 1

        return name

    @classmethod
    def import_campaigns(cls, org, user, campaign_defs, same_site=False):
        """
        Import campaigns from a list of exported campaigns
        """

        for campaign_def in campaign_defs:
            name = campaign_def[Campaign.EXPORT_NAME]
            campaign = None
            group = None

            # first check if we have the objects by UUID
            if same_site:
                group = ContactGroup.user_groups.filter(
                    uuid=campaign_def[Campaign.EXPORT_GROUP]["uuid"], org=org
                ).first()
                if group:  # pragma: needs cover
                    group.name = campaign_def[Campaign.EXPORT_GROUP]["name"]
                    group.save()

                campaign = Campaign.objects.filter(org=org, uuid=campaign_def[Campaign.EXPORT_UUID]).first()
                if campaign:  # pragma: needs cover
                    campaign.name = Campaign.get_unique_name(org, name, ignore=campaign)
                    campaign.save()

            # fall back to lookups by name
            if not group:
                group = ContactGroup.get_user_group_by_name(org, campaign_def[Campaign.EXPORT_GROUP]["name"])

            if not campaign:
                campaign = Campaign.objects.filter(org=org, name=name).first()

            # all else fails, create the objects from scratch
            if not group:
                group = ContactGroup.create_static(org, user, campaign_def[Campaign.EXPORT_GROUP]["name"])

            if not campaign:
                campaign_name = Campaign.get_unique_name(org, name)
                campaign = Campaign.create(org, user, campaign_name, group)
            else:
                campaign.group = group
                campaign.save()

            # deactivate all of our events, we'll recreate these
            for event in campaign.events.all():
                event.release()

            # fill our campaign with events
            for event_spec in campaign_def[Campaign.EXPORT_EVENTS]:
                field_key = event_spec["relative_to"]["key"]

                if field_key == "created_on":
                    relative_to = ContactField.system_fields.filter(org=org, key=field_key).first()
                else:
                    relative_to = ContactField.get_or_create(
                        org, user, key=field_key, label=event_spec["relative_to"]["label"], value_type="D"
                    )

                start_mode = event_spec.get("start_mode", CampaignEvent.MODE_INTERRUPT)

                # create our message flow for message events
                if event_spec["event_type"] == CampaignEvent.TYPE_MESSAGE:

                    message = event_spec["message"]
                    base_language = event_spec.get("base_language")

                    if not isinstance(message, dict):
                        try:
                            message = json.loads(message)
                        except ValueError:
                            # if it's not a language dict, turn it into one
                            message = dict(base=message)
                            base_language = "base"

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

            # update our scheduled events for this campaign
            EventFire.update_campaign_events(campaign)

    @classmethod
    def restore_flows(cls, campaign):
        events = (
            campaign.events.filter(is_active=True, event_type=CampaignEvent.TYPE_FLOW)
            .exclude(flow=None)
            .select_related("flow")
        )
        for event in events:
            event.flow.restore()

    @classmethod
    def apply_action_archive(cls, user, campaigns):
        campaigns.update(is_archived=True, modified_by=user, modified_on=timezone.now())

        # update the events for each campaign
        for campaign in campaigns:
            EventFire.update_campaign_events(campaign)

    @classmethod
    def apply_action_restore(cls, user, campaigns):
        campaigns.update(is_archived=False, modified_by=user, modified_on=timezone.now())

        # update the events for each campaign
        for campaign in campaigns:
            Campaign.restore_flows(campaign)
            EventFire.update_campaign_events(campaign)

    def get_events(self):
        return self.events.filter(is_active=True).order_by("relative_to", "offset")

    def as_export_def(self):
        """
        The definition of this campaign for export. Note this only includes references to the dependent
        flows which will be exported separately.
        """
        events = []

        for event in self.events.filter(is_active=True).order_by("flow__uuid"):
            event_definition = dict(
                uuid=event.uuid,
                offset=event.offset,
                unit=event.unit,
                event_type=event.event_type,
                delivery_hour=event.delivery_hour,
                message=event.message,
                relative_to=dict(label=event.relative_to.label, key=event.relative_to.key),  # TODO should be key/name
                start_mode=event.start_mode,
            )

            # only include the flow definition for standalone flows
            if event.event_type == CampaignEvent.TYPE_FLOW:
                event_definition["flow"] = event.flow.as_export_ref()

            # include the flow base language for message flows
            elif event.event_type == CampaignEvent.TYPE_MESSAGE:
                event_definition["base_language"] = event.flow.base_language

            events.append(event_definition)

        return {
            Campaign.EXPORT_UUID: str(self.uuid),
            Campaign.EXPORT_NAME: self.name,
            Campaign.EXPORT_GROUP: self.group.as_export_ref(),
            Campaign.EXPORT_EVENTS: events,
        }

    def get_sorted_events(self):
        """
        Returns campaign events sorted by their actual offset with event flow definitions on the current export version
        """
        events = list(self.events.filter(is_active=True))

        for evt in events:
            if evt.flow.is_system:
                evt.flow.ensure_current_version()

        return sorted(events, key=lambda e: e.relative_to.pk * 100_000 + e.minute_offset())

    def _full_release(self):
        """
        Deletes this campaign completely
        """
        for event in self.events.all():
            event._full_release()

        self.delete()

    def __str__(self):
        return f'Campaign[uuid={self.uuid}, name="{self.name}"]'


class CampaignEvent(TembaModel):
    """
    An event within a campaign that can send a message to a contact or start them in a flow
    """

    TYPE_FLOW = "F"
    TYPE_MESSAGE = "M"

    # single char flag, human readable name, API readable name
    TYPE_CONFIG = ((TYPE_FLOW, "Flow Event", "flow"), (TYPE_MESSAGE, "Message Event", "message"))

    TYPE_CHOICES = [(t[0], t[1]) for t in TYPE_CONFIG]

    UNIT_MINUTES = "M"
    UNIT_HOURS = "H"
    UNIT_DAYS = "D"
    UNIT_WEEKS = "W"

    UNIT_CONFIG = (
        (UNIT_MINUTES, _("Minutes"), "minutes"),
        (UNIT_HOURS, _("Hours"), "hours"),
        (UNIT_DAYS, _("Days"), "days"),
        (UNIT_WEEKS, _("Weeks"), "weeks"),
    )

    UNIT_CHOICES = [(u[0], u[1]) for u in UNIT_CONFIG]

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
        if campaign.org != org:
            raise ValueError("Org mismatch")

        if relative_to.value_type != Value.TYPE_DATETIME:
            raise ValueError(
                f"Contact fields for CampaignEvents must have a datetime type, got {relative_to.value_type}."
            )

        if isinstance(message, str):
            base_language = org.primary_language.iso_code if org.primary_language else "base"
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

        if relative_to.value_type != Value.TYPE_DATETIME:
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

    def single_unit_display(self):
        return self.get_unit_display()[:-1]

    def abs_offset(self):
        return abs(self.offset)

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

    def calculate_scheduled_fire_for_value(self, date_value, now):
        tz = self.campaign.org.timezone

        # nothing to base off of, nothing to fire
        if not date_value:
            return None

        # field is no longer active? return
        if not self.relative_to.is_active:  # pragma: no cover
            return None

        # convert to our timezone
        date_value = date_value.astimezone(tz)

        # if we got a date, floor to the minute
        date_value = date_value.replace(second=0, microsecond=0)

        # try to parse it to a datetime
        try:
            if self.unit == CampaignEvent.UNIT_MINUTES:  # pragma: needs cover
                delta = timedelta(minutes=self.offset)
            elif self.unit == CampaignEvent.UNIT_HOURS:  # pragma: needs cover
                delta = timedelta(hours=self.offset)
            elif self.unit == CampaignEvent.UNIT_DAYS:
                delta = timedelta(days=self.offset)
            elif self.unit == CampaignEvent.UNIT_WEEKS:
                delta = timedelta(weeks=self.offset)

            scheduled = date_value + delta

            # normalize according to our timezone (puts us in the right DST timezone if our date changed)
            if str(tz) != "UTC":
                scheduled = tz.normalize(scheduled)

            if self.delivery_hour != -1:
                scheduled = scheduled.replace(hour=self.delivery_hour, minute=0, second=0, microsecond=0)

            # if we've changed utcoffset (DST shift), tweak accordingly (this keeps us at the same hour of the day)
            elif str(tz) != "UTC" and date_value.utcoffset() != scheduled.utcoffset():
                scheduled = tz.normalize(date_value.utcoffset() - scheduled.utcoffset() + scheduled)

            # ignore anything in the past
            if scheduled > now:
                return scheduled

        except Exception:  # pragma: no cover
            pass

        return None  # pragma: no cover

    def deactivate_and_copy(self):

        self.release()

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

    def release(self):
        """
        Marks the event inactive and releases flows for single message flows
        """
        # we need to be inactive so our fires are noops
        self.is_active = False
        self.save(update_fields=("is_active",))

        # detach any associated flow starts
        self.flow_starts.all().update(campaign_event=None)

        # if flow isn't a user created flow we can delete it too
        if self.event_type == CampaignEvent.TYPE_MESSAGE:
            self.flow.release()

    def _full_release(self):
        """
        Deletes this event completely along with associated fires
        """
        self.release()

        # delete any associated fires
        self.fires.all().delete()

        # and ourselves
        self.delete()

    def calculate_scheduled_fire(self, contact):
        return self.calculate_scheduled_fire_for_value(contact.get_field_value(self.relative_to), timezone.now())

    def __str__(self):
        return f'Event[relative_to={self.relative_to.key}, offset={self.offset}, flow="{self.flow.name}"]'


class EventFire(Model):
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

    def is_firing_soon(self):
        return self.scheduled < timezone.now()

    def get_relative_to_value(self):
        value = self.contact.get_field_value(self.event.relative_to)
        return value.replace(second=0, microsecond=0) if value else None

    @classmethod
    def update_campaign_events(cls, campaign):
        """
        Updates all the scheduled events for each user for the passed in campaign.
        Should be called anytime a campaign changes.
        """
        for event in campaign.get_events():
            if EventFire.objects.filter(event=event).exists():
                event = event.deactivate_and_copy()
            EventFire.create_eventfires_for_event(event)

    @classmethod
    def create_eventfires_for_event(cls, event):
        from temba.campaigns.tasks import create_event_fires

        on_transaction_commit(lambda: create_event_fires.delay(event.pk))

    @classmethod
    def do_create_eventfires_for_event(cls, event):

        if EventFire.objects.filter(event=event).exists():
            return

        if event.is_active and not event.campaign.is_archived:

            # create fires for our event
            field = event.relative_to
            if field.field_type == ContactField.FIELD_TYPE_USER:
                field_uuid = str(field.uuid)

                contacts = event.campaign.group.contacts.filter(is_active=True, is_blocked=False).extra(
                    where=['%s::text[] <@ (extract_jsonb_keys("contacts_contact"."fields"))'], params=[[field_uuid]]
                )
            elif field.field_type == ContactField.FIELD_TYPE_SYSTEM:
                contacts = event.campaign.group.contacts.filter(is_active=True, is_blocked=False)
            else:  # pragma: no cover
                raise ValueError(f"Unhandled ContactField type {field.field_type}.")

            now = timezone.now()
            events = []

            org = event.campaign.org
            for contact in contacts:
                contact.org = org
                scheduled = event.calculate_scheduled_fire_for_value(contact.get_field_value(field), now)

                # and if we have a date, then schedule it
                if scheduled:
                    events.append(EventFire(event=event, contact=contact, scheduled=scheduled))

            # bulk create our event fires
            EventFire.objects.bulk_create(events)

    @classmethod
    def update_events_for_contact_groups(cls, contact, groups):
        """
        Updates all the events for a contact, across all campaigns.
        Should be called anytime a contact field or contact group membership changes.
        """

        # for each campaign that might affect us
        for campaign in Campaign.objects.filter(
            group__in=groups, org=contact.org, is_active=True, is_archived=False
        ).distinct():
            # update all the events for the campaign
            EventFire.update_campaign_events_for_contact(campaign, contact)

    @classmethod
    def update_events_for_contact_fields(cls, contact, keys):
        """
        Updates all the events for a contact, across all campaigns.
        Should be called anytime a contact field or contact group membership changes.
        """
        # make sure we consider immutable fields(created_on)
        keys = list(keys)
        keys.extend(list(ContactField.IMMUTABLE_FIELDS))

        events = CampaignEvent.objects.filter(
            campaign__group__in=contact.user_groups,
            campaign__org=contact.org,
            relative_to__key__in=keys,
            campaign__is_archived=False,
            is_active=True,
        ).prefetch_related("relative_to")

        for event in events:
            # remove any unfired events, they will get recreated below
            EventFire.objects.filter(event=event, contact=contact, fired=None).delete()

            # calculate our scheduled date
            scheduled = event.calculate_scheduled_fire(contact)

            # and if we have a date, then schedule it
            if scheduled:
                EventFire.objects.create(event=event, contact=contact, scheduled=scheduled)

    @classmethod
    def update_campaign_events_for_contact(cls, campaign, contact):
        """
        Updates all the events for the passed in contact and campaign.
        Should be called anytime a contact field or contact group membership changes.
        """
        # remove any unfired events, they will get recreated below
        EventFire.objects.filter(event__campaign=campaign, contact=contact, fired=None).delete()

        # if we aren't archived and still in our campaign's group
        if not campaign.is_archived and contact.user_groups.filter(id__in=[campaign.group.id]).exists():
            # then scheduled all our events
            for event in campaign.get_events():
                # calculate our scheduled date
                scheduled = event.calculate_scheduled_fire(contact)

                # and if we have a date, then schedule it
                if scheduled:
                    EventFire.objects.create(event=event, contact=contact, scheduled=scheduled)

    def __str__(self):  # pragma: no cover
        return f"EventFire[event={self.event.uuid}, contact={self.contact.uuid}, scheduled={self.scheduled}]"

    class Meta:
        ordering = ("scheduled",)
