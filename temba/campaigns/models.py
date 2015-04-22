from operator import attrgetter
from uuid import uuid4
from django.db import models
from django.db.models import Model
from django.utils import timezone
from smartmin.models import SmartModel
from datetime import timedelta

from temba.contacts.models import ContactGroup, ContactField, Contact
from temba.orgs.models import Org
from temba.flows.models import Flow
from dateutil.parser import parse
from django.utils.translation import ugettext_lazy as _

class Campaign(SmartModel):
    name = models.CharField(max_length=255,
                            help_text="The name of this campaign")
    group = models.ForeignKey(ContactGroup,
                              help_text="The group this campaign operates on")
    is_archived = models.BooleanField(default=False,
                                      help_text="Whether this campaign is archived or not")
    org = models.ForeignKey(Org,
                            help_text="The organization this campaign exists for")

    @classmethod
    def get_unique_name(cls, base_name, org, ignore=None):
        name = base_name[:255].strip()

        count = 2
        while True:
            campaigns = Campaign.objects.filter(name=name, org=org, is_active=True)
            if ignore:
                campaigns = campaigns.exclude(pk=ignore.pk)
            if campaigns.first() is None:
                break
            name = '%s %d' % (base_name[:255].strip(), count)
            count += 1

        return name

    @classmethod
    def import_campaigns(cls, exported_json, org, user, site=None):
        """
        Import campaigns from our export file
        """
        from temba.orgs.models import EARLIEST_IMPORT_VERSION
        if exported_json.get('version', 0) < EARLIEST_IMPORT_VERSION:
            raise ValueError(_("Unknown version (%s)" % exported_json.get('version', 0)))

        if 'campaigns' in exported_json:
            for campaign_spec in exported_json['campaigns']:
                name = campaign_spec['name']
                campaign = None
                group = None

                # first check if we have the objects by id
                if site and site == exported_json.get('site', None):
                    group = ContactGroup.user_groups.filter(id=campaign_spec['group']['id'], org=org, is_active=True).first()
                    if group:
                        group.name = campaign_spec['group']['name']
                        group.save()

                    campaign = Campaign.objects.filter(org=org, id=campaign_spec['id']).first()
                    if campaign:
                        campaign.name = Campaign.get_unique_name(name, org, ignore=campaign)
                        campaign.save()

                # fall back to lookups by name
                if not group:
                    group = ContactGroup.user_groups.filter(name=campaign_spec['group']['name'], org=org).first()

                if not campaign:
                    campaign = Campaign.objects.filter(org=org, name=name).first()

                # all else fails, create the objects from scratch
                if not group:
                    group = ContactGroup.create(org, user, campaign_spec['group']['name'])

                if not campaign:
                    campaign = Campaign.objects.create(name=Campaign.get_unique_name(name, org), org=org, group=group, created_by=user, modified_by=user)
                else:
                    campaign.group = group
                    campaign.save()


                # we want to nuke old single message flows
                for event in campaign.events.all():
                    if event.flow.flow_type == Flow.MESSAGE:
                        event.flow.delete()

                # and all of the events, we'll recreate these
                campaign.events.all().delete()

                # fill our campaign with events
                for event_spec in campaign_spec['events']:
                    relative_to = ContactField.get_or_create(org,
                                                             key=event_spec['relative_to']['key'],
                                                             label=event_spec['relative_to']['label'])

                    # create our message flow for message events
                    if event_spec['event_type'] == MESSAGE_EVENT:
                        flow = Flow.create_single_message(org, user, event_spec['message'])
                    else:
                        flow = Flow.objects.filter(org=org, id=event_spec['flow']['id']).first()

                    if flow:
                        event = campaign.events.create(created_by=user,
                                                       modified_by=user,
                                                       offset=event_spec['offset'],
                                                       unit=event_spec['unit'],
                                                       event_type=event_spec['event_type'],
                                                       delivery_hour=event_spec['delivery_hour'],
                                                       message=event_spec['message'],
                                                       flow=flow,
                                                       relative_to=relative_to)
                        event.update_flow_name()

                # update our scheduled events for this campaign
                EventFire.update_campaign_events(campaign)

    @classmethod
    def apply_action_archive(cls, campaigns):
        campaigns.update(is_archived=True)

        # update the events for each campaign
        for campaign in campaigns:
            EventFire.update_campaign_events(campaign)

        return [each_campaign.pk for each_campaign in campaigns]

    @classmethod
    def apply_action_restore(cls, campaigns):
        campaigns.update(is_archived=False)

        # update the events for each campaign
        for campaign in campaigns:
            EventFire.update_campaign_events(campaign)

        return [each_campaign.pk for each_campaign in campaigns]

    def get_events(self):
        return self.events.filter(is_active=True).order_by('relative_to', 'offset')

    def as_json(self):
        """
        A json representation of this event, suitable for export. Note this only returns the ids and names
        of the dependent flows. You will want to export these flows seperately using get_all_flows()
        """
        definition = dict(name=self.name, id=self.pk, group=dict(id=self.group.id, name=self.group.name))
        events = []

        for event in self.events.all().order_by('flow__id'):
            events.append(dict(id=event.pk, offset=event.offset,
                               unit=event.unit,
                               event_type=event.event_type,
                               delivery_hour=event.delivery_hour,
                               message=event.message,
                               flow=dict(id=event.flow.pk, name=event.flow.name),
                               relative_to=dict(label=event.relative_to.label, key=event.relative_to.key, id=event.relative_to.pk)))
        definition['events'] = events
        return definition

    def get_all_flows(self):
        """
        Unique set of flows, including single message flows
        """
        return [event.flow for event in self.events.filter(is_active=True).order_by('flow__id').distinct('flow')]

    def get_flows(self):
        """
        A unique set of user-facing flows this campaign uses
        """
        return [event.flow for event in self.events.filter(is_active=True).exclude(flow__flow_type=Flow.MESSAGE).order_by('flow__id').distinct('flow')]

    def get_sorted_events(self):
        events = [e for e in self.events.filter(is_active=True)]

        # now sort by real offset
        return sorted(events, key=lambda e: e.relative_to.pk * 100000 + e.minute_offset())

    def __unicode__(self):
        return self.name


FLOW_EVENT = 'F'
MESSAGE_EVENT = 'M'
EVENT_TYPES = ((FLOW_EVENT, "Flow Event"),
               (MESSAGE_EVENT, "Message Event"))

MINUTES = 'M'
HOURS = 'H'
DAYS = 'D'
WEEKS = 'W'

UNIT_CHOICES = ((MINUTES, "Minutes"),
                (HOURS, "Hours"),
                (DAYS, "Days"),
                (WEEKS, "Weeks"))


class CampaignEvent(SmartModel):

    campaign = models.ForeignKey(Campaign, related_name='events',
                                 help_text="The campaign this event is part of")
    offset = models.IntegerField(default=0,
                                 help_text="The offset in days from our date (positive is after, negative is before)")
    unit = models.CharField(max_length=1, choices=UNIT_CHOICES, default=DAYS,
                            help_text="The unit for the offset for this event")
    relative_to = models.ForeignKey(ContactField, related_name='campaigns',
                                    help_text="The field our offset is relative to")

    flow = models.ForeignKey(Flow, help_text="The flow that will be triggered")

    event_type = models.CharField(max_length=1, choices=EVENT_TYPES, default=FLOW_EVENT,
                                  help_text='The type of this event')

    # when sending single message events, we store the message here (as well as on the flow) for convenience
    message = models.TextField(help_text="The message to send out", null=True, blank=True)

    delivery_hour = models.IntegerField(default=-1, help_text="The hour to send the message or flow at.")


    @classmethod
    def get_hour_choices(cls):
        hours = [(-1, 'during the same hour',), (0, 'at Midnight')]
        period = 'a.m.'
        for i in range(1, 24):
            hour = i
            if i >= 12:
                period = 'p.m.'
                if i > 12:
                    hour -= 12
            hours.append((i, 'at %s:00 %s' % (hour, period)))
        return hours

    def update_flow_name(self):
        """
        Updates our flow name to include our Event id, keeps flow names from colliding. No-op for non-message events.
        """
        if self.event_type != MESSAGE_EVENT:
            return

        self.flow.name = "Single Message (%d)" % self.pk
        self.flow.save(update_fields=['name'])

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

        if self.unit == HOURS:
            offset = self.offset * 60
        elif self.unit == DAYS:
            offset = self.offset * 60 * 24
        elif self.unit == WEEKS:
            offset = self.offset * 60 * 24 * 7

        # if there is a specified hour, use that
        if self.delivery_hour != -1:
            offset += self.delivery_hour * 60

        return offset

    def calculate_scheduled_fire(self, contact):
        if not self.relative_to.is_active: # pragma: no cover
            return None

        # try to parse it to a datetime
        try:
            now = timezone.now()
            date_value = EventFire.parse_relative_to_date(contact, self.relative_to.key)
            if date_value:
                if self.unit == MINUTES:
                    delta = timedelta(minutes=self.offset)
                elif self.unit == HOURS:
                    delta = timedelta(hours=self.offset)
                elif self.unit == DAYS:
                    delta = timedelta(days=self.offset)
                elif self.unit == WEEKS:
                    delta = timedelta(weeks=self.offset)

                scheduled = date_value + delta

                if self.delivery_hour != -1:
                    scheduled = scheduled.replace(hour=self.delivery_hour)

                if scheduled > now:
                    return scheduled

        except Exception as e:
            pass

        return None

    def __unicode__(self):
        return "%s == %d -> %s" % (self.relative_to, self.offset, self.flow)

class EventFire(Model):
    event = models.ForeignKey('campaigns.CampaignEvent', related_name="event_fires",
                              help_text="The event that will be fired")
    contact = models.ForeignKey(Contact, related_name="fire_events",
                                help_text="The contact that is scheduled to have an event run")
    scheduled = models.DateTimeField(help_text="When this event is scheduled to run")
    fired = models.DateTimeField(null=True, blank=True,
                                 help_text="When this event actually fired, null if not yet fired")

    def is_firing_soon(self):
        return self.scheduled < timezone.now()

    @classmethod
    def parse_relative_to_date(cls, contact, key):
        relative_date = contact.org.parse_date(contact.get_field_display(key))

        # if we got a date, floor to the minute
        if relative_date:
            relative_date = relative_date.replace(second=0, microsecond=0)

        return relative_date

    def get_relative_to_value(self):
        return EventFire.parse_relative_to_date(self.contact, self.event.relative_to.key)

    def fire(self):
        """
        Actually fires this event for the passed in contact and flow
        """
        self.fired = timezone.now()
        self.event.flow.start([], [self.contact], restart_participants=True)
        self.save()

    @classmethod
    def update_campaign_events(cls, campaign):
        """
        Updates all the scheduled events for each user for the passed in campaign.
        Should be called anytime a campaign changes.
        """
        for contact in campaign.group.contacts.exclude(is_test=True):
            cls.update_campaign_events_for_contact(campaign, contact)

    @classmethod
    def update_events_for_event(cls, event):
        # unschedule any fires
        EventFire.objects.filter(event=event, fired=None).delete()

        # add new ones
        if event.is_active:
            for contact in event.campaign.group.contacts.exclude(is_test=True):
                # calculate our scheduled date
                scheduled = event.calculate_scheduled_fire(contact)

                # and if we have a date, then schedule it
                if scheduled:
                    EventFire.objects.create(event=event, contact=contact, scheduled=scheduled)

    @classmethod
    def update_field_events(cls, contact_field):
        """
        Cancel any events for the passed in contact field
        """
        if not contact_field.is_active:
            # remove any scheduled fires for the passed in field
            EventFire.objects.filter(event__relative_to=contact_field, fired=None).delete()
        else:
            # cancel existing events, we are going to recreate them all
            EventFire.objects.filter(event__relative_to=contact_field, fired=None).delete()

            for event in CampaignEvent.objects.filter(relative_to=contact_field,
                                                      campaign__is_active=True, campaign__is_archived=False, is_active=True):

                # for each contact
                for contact in event.campaign.group.contacts.filter(is_active=True):
                    # calculate our scheduled date
                    scheduled = event.calculate_scheduled_fire(contact)

                    # and if we have a date, then schedule it
                    if scheduled:
                        EventFire.objects.create(event=event, contact=contact, scheduled=scheduled)

    @classmethod
    def update_events_for_contact(cls, contact):
        """
        Updates all the events for a contact, across all campaigns.
        Should be called anytime a contact field or contact group membership changes.
        """
        # remove all pending fires for this contact
        EventFire.objects.filter(contact=contact, fired=None).delete()

        # get all the groups this user is in
        groups = [g.id for g in contact.user_groups.all()]

        # for each campaign that might effect us
        for campaign in Campaign.objects.filter(group__in=groups, org=contact.org,
                                                is_active=True, is_archived=False).distinct():

            # update all the events for the campaign
            EventFire.update_campaign_events_for_contact(campaign, contact)

    @classmethod
    def update_events_for_contact_field(cls, contact, key):
        """
        Updates all the events for a contact, across all campaigns.
        Should be called anytime a contact field or contact group membership changes.
        """
        # get all the groups this user is in
        groups = [_.id for _ in contact.user_groups.all()]

        # get all events which are in one of these groups and on this field
        for event in CampaignEvent.objects.filter(campaign__group__in=groups, relative_to__key=key,
                                                  campaign__is_archived=False, is_active=True):

            # remove any unfired events, they will get recreated below
            EventFire.objects.filter(event=event, contact=contact, fired=None).delete()

            # calculate our scheduled date
            scheduled = event.calculate_scheduled_fire(contact)

            # and if we have a date, then schedule it
            if scheduled and not contact.is_test:
                EventFire.objects.create(event=event, contact=contact, scheduled=scheduled)

    @classmethod
    def update_campaign_events_for_contact(cls, campaign, contact):
        """
        Updates all the events for the passed in contact and campaign.
        Should be called anytime a contact field or contact group membership changes.
        """
        # remove any unfired events, they will get recreated below
        EventFire.objects.filter(event__campaign=campaign, contact=contact, fired=None).delete()

        # for each event
        for event in campaign.get_events():
            # calculate our scheduled date
            scheduled = event.calculate_scheduled_fire(contact)

            # and if we have a date, then schedule it
            if scheduled and not contact.is_test:
                EventFire.objects.create(event=event, contact=contact, scheduled=scheduled)

    def __unicode__(self):
        return "%s - %s" % (self.event, self.contact)

    class Meta:
        ordering = ('scheduled',)
