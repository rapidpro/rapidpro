from datetime import datetime, timedelta
from unittest.mock import patch

import pytz

from django.conf import settings
from django.core.exceptions import ValidationError
from django.urls import reverse
from django.utils import timezone

from temba.contacts.models import Contact, ContactField, ContactGroup, ImportTask
from temba.flows.models import Flow, FlowRevision
from temba.msgs.models import Msg
from temba.orgs.models import Language, Org
from temba.tests import TembaTest, matchers, mock_mailroom
from temba.utils import json
from temba.values.constants import Value

from .models import Campaign, CampaignEvent, EventFire
from .tasks import trim_event_fires_task


class CampaignTest(TembaTest):
    def setUp(self):
        super().setUp()

        self.farmer1 = self.create_contact("Rob Jasper", "+250788111111")
        self.farmer2 = self.create_contact("Mike Gordon", "+250788222222", language="spa")

        self.nonfarmer = self.create_contact("Trey Anastasio", "+250788333333")
        self.farmers = self.create_group("Farmers", [self.farmer1, self.farmer2])

        self.reminder_flow = self.create_flow(name="Reminder Flow")
        self.reminder2_flow = self.create_flow(name="Planting Reminder")

        # create a voice flow to make sure they work too, not a proper voice flow but
        # sufficient for assuring these flow types show up where they should
        self.voice_flow = self.create_flow(name="IVR flow", flow_type="V")

        # create a contact field for our planting date
        self.planting_date = ContactField.get_or_create(
            self.org, self.admin, "planting_date", "Planting Date", value_type=Value.TYPE_DATETIME
        )

    def test_get_unique_name(self):
        campaign1 = Campaign.create(
            self.org, self.admin, Campaign.get_unique_name(self.org, "Reminders"), self.farmers
        )
        self.assertEqual(campaign1.name, "Reminders")

        campaign2 = Campaign.create(
            self.org, self.admin, Campaign.get_unique_name(self.org, "Reminders"), self.farmers
        )
        self.assertEqual(campaign2.name, "Reminders 2")

        campaign3 = Campaign.create(
            self.org, self.admin, Campaign.get_unique_name(self.org, "Reminders"), self.farmers
        )
        self.assertEqual(campaign3.name, "Reminders 3")

        self.assertEqual(Campaign.get_unique_name(self.org2, "Reminders"), "Reminders")  # different org

    def test_get_sorted_events(self):
        # create a campaign
        campaign = Campaign.create(self.org, self.user, "Planting Reminders", self.farmers)

        flow = self.create_flow()

        event1 = CampaignEvent.create_flow_event(
            self.org, self.admin, campaign, self.planting_date, offset=1, unit="W", flow=flow, delivery_hour="13"
        )
        event2 = CampaignEvent.create_flow_event(
            self.org, self.admin, campaign, self.planting_date, offset=1, unit="W", flow=flow, delivery_hour="9"
        )
        event3 = CampaignEvent.create_flow_event(
            self.org, self.admin, campaign, self.planting_date, offset=2, unit="W", flow=flow, delivery_hour="1"
        )

        self.assertEqual(campaign.get_sorted_events(), [event2, event1, event3])

        flow_json = self.get_flow_json("favorites")
        flow = Flow.objects.create(
            name="Call Me Maybe",
            org=self.org,
            is_system=True,
            created_by=self.admin,
            modified_by=self.admin,
            saved_by=self.admin,
            version_number=3,
            flow_type="V",
        )

        FlowRevision.objects.create(
            flow=flow, definition=flow_json, spec_version=3, revision=1, created_by=self.admin, modified_by=self.admin
        )

        event4 = CampaignEvent.create_flow_event(
            self.org, self.admin, campaign, self.planting_date, offset=2, unit="W", flow=flow, delivery_hour="5"
        )

        self.assertEqual(flow.version_number, 3)
        self.assertEqual(campaign.get_sorted_events(), [event2, event1, event3, event4])

        flow.refresh_from_db()

        self.assertNotEqual(flow.version_number, 3)
        self.assertEqual(flow.version_number, Flow.FINAL_LEGACY_VERSION)

    def test_message_event(self):
        # create a campaign with a message event 1 day after planting date
        campaign = Campaign.create(self.org, self.admin, "Planting Reminders", self.farmers)
        event = CampaignEvent.create_message_event(
            self.org,
            self.admin,
            campaign,
            relative_to=self.planting_date,
            offset=1,
            unit="D",
            message={
                "eng": "Hi @(upper(contact.name)) don't forget to plant on @(format_date(contact.planting_date))"
            },
            base_language="eng",
        )

        self.assertEqual(
            event.flow.as_json(),
            {
                "uuid": str(event.flow.uuid),
                "name": event.flow.name,
                "spec_version": "13.0.0",
                "revision": 1,
                "language": "eng",
                "type": "messaging",
                "expire_after_minutes": 720,
                "localization": {},
                "nodes": [
                    {
                        "uuid": matchers.UUID4String(),
                        "actions": [
                            {
                                "uuid": matchers.UUID4String(),
                                "type": "send_msg",
                                "text": "Hi @(upper(contact.name)) don't forget to plant on @(format_date(contact.planting_date))",
                            }
                        ],
                        "exits": [{"uuid": matchers.UUID4String()}],
                    }
                ],
            },
        )

    def test_trim_event_fires(self):
        campaign = Campaign.create(self.org, self.admin, "Planting Reminders", self.farmers)

        # create a reminder for our first planting event
        event = CampaignEvent.create_flow_event(
            self.org, self.admin, campaign, relative_to=self.planting_date, offset=3, unit="D", flow=self.reminder_flow
        )

        # create a reminder for our first planting event
        second_event = CampaignEvent.create_flow_event(
            self.org, self.admin, campaign, relative_to=self.planting_date, offset=5, unit="D", flow=self.reminder_flow
        )

        trim_date = timezone.now() - timedelta(days=settings.EVENT_FIRE_TRIM_DAYS + 1)

        # manually create two event fires
        EventFire.objects.create(event=event, contact=self.farmer1, scheduled=trim_date, fired=trim_date)
        e2 = EventFire.objects.create(
            event=event, contact=self.farmer1, scheduled=timezone.now(), fired=timezone.now()
        )

        # create an unfired fire and release its event
        EventFire.objects.create(event=second_event, contact=self.farmer1, scheduled=trim_date)
        second_event.release()

        # trim our events, one fired and one inactive onfired
        trim_event_fires_task()

        # should now have only one event, e2
        e = EventFire.objects.get()
        self.assertEqual(e.id, e2.id)

    def test_event_fire_creation(self):

        self.login(self.admin)

        # update the planting date for our contacts
        self.set_contact_field(self.farmer1, "planting_date", "1/10/2020", legacy_handle=True)

        # create a campaign with an event
        campaign = Campaign.create(self.org, self.admin, "Planting Reminders", self.farmers)
        field = ContactField.get_by_key(self.org, "planting_date")
        event = CampaignEvent.create_message_event(self.org, self.admin, campaign, field, 15, "D", "Event Message")

        # should create one event fire
        EventFire.do_create_eventfires_for_event(event)
        self.assertEqual(EventFire.objects.filter(event=event).count(), 1)

        # but shouldn't create extras if we call it again
        EventFire.do_create_eventfires_for_event(event)
        self.assertEqual(EventFire.objects.filter(event=event).count(), 1)

    def test_message_event_editing(self):
        # update the planting date for our contacts
        self.set_contact_field(self.farmer1, "planting_date", "1/10/2020")

        # ok log in as an org
        self.login(self.admin)

        # create a campaign
        campaign = Campaign.create(self.org, self.admin, "Planting Reminders", self.farmers)

        # go create an event that based on a message
        url = "%s?campaign=%d" % (reverse("campaigns.campaignevent_create"), campaign.id)
        response = self.client.get(url)
        self.assertIn("base", response.context["form"].fields)

        # should be no language list
        self.assertNotContains(response, "show_language")

        # set our primary language to Achinese
        ace = Language.objects.create(
            org=self.org, name="Achinese", iso_code="ace", created_by=self.admin, modified_by=self.admin
        )

        self.org.primary_language = ace
        self.org.save(update_fields=("primary_language",))

        # now we should have ace as our primary
        response = self.client.get(url)

        self.assertNotIn("base", response.context["form"].fields)
        self.assertIn("ace", response.context["form"].fields)

        # add second language
        spa = Language.objects.create(
            org=self.org, name="Spanish", iso_code="spa", created_by=self.admin, modified_by=self.admin
        )

        response = self.client.get(url)

        self.assertNotIn("base", response.context["form"].fields)
        self.assertIn("ace", response.context["form"].fields)
        self.assertIn("spa", response.context["form"].fields)

        # and our language list should be there
        self.assertContains(response, "show_language")

        self.org.primary_language = None
        self.org.save(update_fields=("primary_language",))

        response = self.client.get(url)

        self.assertIn("base", response.context["form"].fields)
        self.assertIn("spa", response.context["form"].fields)
        self.assertIn("ace", response.context["form"].fields)

        response = self.client.post(
            f"{reverse('campaigns.campaignevent_create')}?campaign={campaign.id}",
            {
                "relative_to": self.planting_date.id,
                "event_type": "M",
                "base": "This is my message",
                "spa": "hola",
                "direction": "B",
                "offset": 1,
                "unit": "W",
                "flow_to_start": "",
                "delivery_hour": 13,
                "message_start_mode": "I",
            },
        )

        # should be redirected back to our campaign read page
        self.assertRedirect(response, reverse("campaigns.campaign_read", args=[campaign.id]))

        # should have one event, which created a corresponding flow
        event = CampaignEvent.objects.filter(is_active=True).first()

        self.assertEqual(event.offset, -1)
        self.assertEqual(event.delivery_hour, 13)
        self.assertEqual(event.unit, "W")
        self.assertEqual(event.event_type, "M")
        self.assertEqual(event.start_mode, "I")

        self.assertEqual(event.get_message(contact=self.farmer1), "This is my message")
        self.assertEqual(event.get_message(contact=self.farmer2), "hola")
        self.assertEqual(event.get_message(), "This is my message")

        self.assertTrue(event.flow.is_system)
        self.assertTrue(event.flow.base_language, "base")

        flow_json = event.flow.as_json()
        action_uuid = flow_json["nodes"][0]["actions"][0]["uuid"]

        self.assertEqual(
            flow_json,
            {
                "uuid": str(event.flow.uuid),
                "name": f"Single Message ({event.id})",
                "spec_version": "13.0.0",
                "revision": 1,
                "expire_after_minutes": 720,
                "language": "base",
                "type": "messaging",
                "localization": {"spa": {action_uuid: {"text": ["hola"]}}},
                "nodes": [
                    {
                        "uuid": matchers.UUID4String(),
                        "actions": [{"uuid": action_uuid, "type": "send_msg", "text": "This is my message"}],
                        "exits": [{"uuid": matchers.UUID4String()}],
                    }
                ],
            },
        )

        url = reverse("campaigns.campaignevent_update", args=[event.id])
        response = self.client.get(url)

        self.assertEqual("This is my message", response.context["form"].fields["base"].initial)
        self.assertEqual("hola", response.context["form"].fields["spa"].initial)
        self.assertEqual("", response.context["form"].fields["ace"].initial)

        # 'Created On' system field must be selectable in the form
        contact_fields = [field.key for field in response.context["form"].fields["relative_to"].queryset]
        self.assertEqual(contact_fields, ["created_on", "last_seen_on", "planting_date"])

        # promote spanish to our primary language
        self.org.primary_language = spa
        self.org.save()

        # the base language needs to stay present since it's the true backdown
        response = self.client.get(url)
        self.assertIn("base", response.context["form"].fields)
        self.assertEqual("This is my message", response.context["form"].fields["base"].initial)
        self.assertEqual("hola", response.context["form"].fields["spa"].initial)
        self.assertEqual("", response.context["form"].fields["ace"].initial)

        # now we save our new settings
        response = self.client.post(
            url,
            {
                "relative_to": self.planting_date.id,
                "event_type": "M",
                "base": "Required",
                "spa": "This is my spanish @fields.planting_date",
                "ace": "",
                "direction": "B",
                "offset": 1,
                "unit": "W",
                "flow_to_start": "",
                "delivery_hour": 13,
            },
        )

        self.assertEqual(response.status_code, 302)
        event.flow.refresh_from_db()

        # we should retain 'base' as our base language
        self.assertEqual("base", event.flow.base_language)

        # now we can remove our primary language
        self.org.primary_language = None
        self.org.save(update_fields=("primary_language",))

        # and still get the same settings, (it should use the base of the flow instead of just base here)
        event = CampaignEvent.objects.all().order_by("id").last()
        url = reverse("campaigns.campaignevent_update", args=[event.id])
        response = self.client.get(url)

        self.assertIn("base", response.context["form"].fields)
        self.assertEqual(response.context["form"].fields["spa"].initial, "This is my spanish @fields.planting_date")
        self.assertEqual(response.context["form"].fields["ace"].initial, "")

        # our single message flow should have a dependency on planting_date
        event.flow.refresh_from_db()
        self.assertEqual(event.flow.field_dependencies.count(), 1)

        # delete the event
        self.client.post(reverse("campaigns.campaignevent_delete", args=[event.id]), dict())
        self.assertFalse(CampaignEvent.objects.filter(id=event.id).first().is_active)

        # our single message flow should be released and take its dependencies with it
        self.assertEqual(event.flow.field_dependencies.count(), 0)

    @mock_mailroom
    def test_views(self, mr_mocks):
        # update the planting date for our contacts
        self.set_contact_field(self.farmer1, "planting_date", "1/10/2020", legacy_handle=True)

        # get the resulting time (including minutes)
        planting_date = self.farmer1.get_field_value(self.planting_date)

        # don't log in, try to create a new campaign
        response = self.client.get(reverse("campaigns.campaign_create"))
        self.assertRedirect(response, reverse("users.user_login"))

        # ok log in as an org
        self.login(self.admin)

        # go to to the creation page
        response = self.client.get(reverse("campaigns.campaign_create"))
        self.assertEqual(200, response.status_code)

        # groups shouldn't include the group that isn't ready
        self.assertEqual(set(response.context["form"].fields["group"].queryset), {self.farmers})

        post_data = dict(name="Planting Reminders", group=self.farmers.pk)
        response = self.client.post(reverse("campaigns.campaign_create"), post_data)

        # should redirect to read page for this campaign
        campaign = Campaign.objects.filter(is_active=True).first()
        self.assertRedirect(response, reverse("campaigns.campaign_read", args=[campaign.pk]))

        # go to the list page, should be there as well
        response = self.client.get(reverse("campaigns.campaign_list"))
        self.assertContains(response, "Planting Reminders")

        # try searching for the campaign by group name
        response = self.client.get(reverse("campaigns.campaign_list") + "?search=farmers")
        self.assertContains(response, "Planting Reminders")

        # test no match
        response = self.client.get(reverse("campaigns.campaign_list") + "?search=factory")
        self.assertNotContains(response, "Planting Reminders")

        # archive a campaign
        post_data = dict(action="archive", objects=campaign.pk)
        self.client.post(reverse("campaigns.campaign_list"), post_data)
        response = self.client.get(reverse("campaigns.campaign_list"))
        self.assertNotContains(response, "Planting Reminders")

        # restore the campaign
        response = self.client.get(reverse("campaigns.campaign_archived"))
        self.assertContains(response, "Planting Reminders")
        post_data = dict(action="restore", objects=campaign.pk)
        self.client.post(reverse("campaigns.campaign_archived"), post_data)
        response = self.client.get(reverse("campaigns.campaign_archived"))
        self.assertNotContains(response, "Planting Reminders")
        response = self.client.get(reverse("campaigns.campaign_list"))
        self.assertContains(response, "Planting Reminders")

        # test viewers cannot use action archive or restore
        self.client.logout()

        # create a viewer
        self.viewer = self.create_user("Viewer")
        self.org.viewers.add(self.viewer)
        self.viewer.set_org(self.org)

        self.login(self.viewer)

        # go to the list page, should be there as well
        response = self.client.get(reverse("campaigns.campaign_list"))
        self.assertContains(response, "Planting Reminders")

        # cannot archive a campaign
        post_data = dict(action="archive", objects=campaign.pk)
        self.client.post(reverse("campaigns.campaign_list"), post_data)
        response = self.client.get(reverse("campaigns.campaign_list"))
        self.assertContains(response, "Planting Reminders")
        response = self.client.get(reverse("campaigns.campaign_archived"))
        self.assertNotContains(response, "Planting Reminders")

        self.client.logout()
        self.login(self.admin)

        # see if we can create a new event, should see both sms and voice flows
        response = self.client.get(reverse("campaigns.campaignevent_create") + "?campaign=%d" % campaign.pk)
        self.assertEqual(200, response.status_code)
        self.assertContains(response, self.reminder_flow.name)
        self.assertContains(response, self.voice_flow.name)

        # 'Created On' system field must be selectable in the form
        contact_fields = [field.key for field in response.context["form"].fields["relative_to"].queryset]
        self.assertEqual(contact_fields, ["created_on", "last_seen_on", "planting_date"])

        post_data = dict(
            relative_to=self.planting_date.pk,
            delivery_hour=-1,
            base="",
            direction="A",
            offset=2,
            unit="D",
            event_type="M",
            flow_to_start=self.reminder_flow.pk,
            flow_start_mode="I",
        )
        response = self.client.post(
            reverse("campaigns.campaignevent_create") + "?campaign=%d" % campaign.pk, post_data
        )

        self.assertTrue(response.context["form"].errors)
        self.assertIn("A message is required", str(response.context["form"].errors["__all__"]))

        post_data = dict(
            relative_to=self.planting_date.pk,
            delivery_hour=-1,
            base="allo!" * 500,
            direction="A",
            offset=2,
            unit="D",
            event_type="M",
            flow_to_start=self.reminder_flow.pk,
            flow_start_mode="I",
        )

        response = self.client.post(
            reverse("campaigns.campaignevent_create") + "?campaign=%d" % campaign.pk, post_data
        )

        self.assertTrue(response.context["form"].errors)
        self.assertTrue(
            "Translation for &#39;Default&#39; exceeds the %d character limit." % Msg.MAX_TEXT_LEN
            in str(response.context["form"].errors["__all__"])
        )

        post_data = dict(
            relative_to=self.planting_date.pk,
            delivery_hour=-1,
            base="",
            direction="A",
            offset=2,
            unit="D",
            event_type="F",
            flow_start_mode="I",
        )
        response = self.client.post(
            reverse("campaigns.campaignevent_create") + "?campaign=%d" % campaign.pk, post_data
        )

        self.assertTrue(response.context["form"].errors)
        self.assertIn("Please select a flow", response.context["form"].errors["flow_to_start"])

        post_data = dict(
            relative_to=self.planting_date.pk,
            delivery_hour=-1,
            base="",
            direction="A",
            offset=2,
            unit="D",
            event_type="F",
            flow_to_start=self.reminder_flow.pk,
            flow_start_mode="I",
        )
        response = self.client.post(
            reverse("campaigns.campaignevent_create") + "?campaign=%d" % campaign.pk, post_data
        )

        event = CampaignEvent.objects.filter(is_active=True).get()
        # should be redirected back to our campaign read page
        self.assertRedirect(response, reverse("campaigns.campaign_read", args=[campaign.pk]))

        self.assertEqual(self.reminder_flow, event.flow)
        self.assertEqual(self.planting_date, event.relative_to)
        self.assertEqual(2, event.offset)
        self.assertEqual("I", event.start_mode)

        # read the campaign read page
        response = self.client.get(reverse("campaigns.campaign_read", args=[campaign.pk]))
        self.assertContains(response, "Reminder Flow")
        self.assertContains(response, "1")

        # convert our planting date to UTC and calculate with our offset
        utc_planting_date = planting_date.astimezone(pytz.utc)
        scheduled_date = utc_planting_date + timedelta(days=2)

        # should also have event fires scheduled for our contacts
        fire = EventFire.objects.get()
        self.assertEqual(scheduled_date.hour, fire.scheduled.hour)
        self.assertEqual(scheduled_date.minute, fire.scheduled.minute)
        self.assertEqual(scheduled_date.day, fire.scheduled.day)
        self.assertEqual(scheduled_date.month, fire.scheduled.month)
        self.assertEqual(scheduled_date.year, fire.scheduled.year)
        self.assertEqual(event, fire.event)

        post_data = dict(
            relative_to=self.planting_date.pk,
            delivery_hour=15,
            base="",
            direction="A",
            offset=1,
            unit="D",
            event_type="F",
            flow_to_start=self.reminder_flow.pk,
            flow_start_mode="I",
        )
        response = self.client.post(reverse("campaigns.campaignevent_update", args=[event.pk]), post_data)

        # should be redirected back to our campaign event read page
        event = CampaignEvent.objects.filter(is_active=True).get()
        self.assertRedirect(response, reverse("campaigns.campaignevent_read", args=[event.pk]))

        # should now have update the campaign event
        self.assertEqual(self.reminder_flow, event.flow)
        self.assertEqual(self.planting_date, event.relative_to)
        self.assertEqual(1, event.offset)
        self.assertEqual("I", event.start_mode)

        # flow event always set exec mode to 'F' no matter what
        post_data = dict(
            relative_to=self.planting_date.pk,
            delivery_hour=15,
            base="",
            direction="A",
            offset=1,
            unit="D",
            event_type="F",
            flow_to_start=self.reminder_flow.pk,
            flow_start_mode="S",
        )
        response = self.client.post(reverse("campaigns.campaignevent_update", args=[event.pk]), post_data)

        # should be redirected to our new event
        previous_event = event
        event = CampaignEvent.objects.filter(is_active=True).get()

        # reading our old event should redirect to the campaign page
        response = self.client.get(reverse("campaigns.campaignevent_read", args=[previous_event.pk]))
        self.assertRedirect(response, reverse("campaigns.campaign_read", args=[previous_event.campaign.pk]))

        # attempting to update our old event gives a 404
        response = self.client.post(reverse("campaigns.campaignevent_update", args=[previous_event.pk]), post_data)
        self.assertEqual(404, response.status_code)

        # should now have update the campaign event
        self.assertEqual(self.reminder_flow, event.flow)
        self.assertEqual(self.planting_date, event.relative_to)
        self.assertEqual(1, event.offset)
        self.assertEqual("S", event.start_mode)

        # should also event fires rescheduled for our contacts
        fire = EventFire.objects.filter(event__is_active=True).get()
        self.assertEqual(13, fire.scheduled.hour)
        self.assertEqual(0, fire.scheduled.minute)
        self.assertEqual(0, fire.scheduled.second)
        self.assertEqual(0, fire.scheduled.microsecond)
        self.assertEqual(2, fire.scheduled.day)
        self.assertEqual(10, fire.scheduled.month)
        self.assertEqual(2020, fire.scheduled.year)
        self.assertEqual(event, fire.event)

        post_data = dict(
            relative_to=self.planting_date.pk,
            delivery_hour=15,
            base="",
            direction="A",
            offset=2,
            unit="D",
            event_type="F",
            flow_to_start=self.reminder2_flow.pk,
            flow_start_mode="I",
        )
        self.client.post(reverse("campaigns.campaignevent_create") + "?campaign=%d" % campaign.pk, post_data)

        # trying to archive our flow should fail since it belongs to a campaign
        post_data = dict(action="archive", objects=[self.reminder_flow.pk])
        response = self.client.post(reverse("flows.flow_list"), post_data)
        self.reminder_flow.refresh_from_db()
        self.assertFalse(self.reminder_flow.is_archived)
        self.assertEqual(
            "The following flows are still used by campaigns so could not be archived: Reminder Flow",
            response.get("Temba-Toast"),
        )

        post_data = dict(action="archive", objects=[self.reminder_flow.pk, self.reminder2_flow.pk])
        response = self.client.post(reverse("flows.flow_list"), post_data)
        self.assertEqual(
            "The following flows are still used by campaigns so could not be archived: Planting Reminder, Reminder Flow",
            response.get("Temba-Toast"),
        )

        for e in CampaignEvent.objects.filter(flow=self.reminder2_flow.pk):
            e.release()

        # archive the campaign
        post_data = dict(action="archive", objects=campaign.pk)
        self.client.post(reverse("campaigns.campaign_list"), post_data)
        response = self.client.get(reverse("campaigns.campaign_list"))
        self.assertNotContains(response, "Planting Reminders")

        # shouldn't have any active event fires
        self.assertFalse(EventFire.objects.filter(event__is_active=True).exists())

        # restore the campaign
        post_data = dict(action="restore", objects=campaign.pk)
        self.client.post(reverse("campaigns.campaign_archived"), post_data)

        # EventFire should be back
        self.assertTrue(EventFire.objects.all().exists())

        # set a planting date on our other farmer
        self.set_contact_field(self.farmer2, "planting_date", "1/6/2022", legacy_handle=True)

        # should have two fire events now
        fires = EventFire.objects.filter(event__is_active=True)
        self.assertEqual(2, len(fires))

        fire = fires[0]
        self.assertEqual(2, fire.scheduled.day)
        self.assertEqual(10, fire.scheduled.month)
        self.assertEqual(2020, fire.scheduled.year)

        fire = fires[1]
        self.assertEqual(2, fire.scheduled.day)
        self.assertEqual(6, fire.scheduled.month)
        self.assertEqual(2022, fire.scheduled.year)

        # setting a planting date on our outside contact has no effect
        self.set_contact_field(self.nonfarmer, "planting_date", "1/7/2025", legacy_handle=True)
        self.assertEqual(2, EventFire.objects.filter(event__is_active=True).count())

        planting_date_field = ContactField.get_by_key(self.org, "planting_date")

        self.client.post(reverse("contacts.contact_update", args=[self.farmer1.id]), post_data)

        response = self.client.post(
            reverse("contacts.contact_update_fields", args=[self.farmer1.id]),
            dict(contact_field=planting_date_field.id, field_value="4/8/2020"),
        )
        self.assertRedirect(response, reverse("contacts.contact_read", args=[self.farmer1.uuid]))

        event = CampaignEvent.objects.filter(is_active=True).first()

        # get the detail page of the event
        response = self.client.get(reverse("campaigns.campaignevent_read", args=[event.id]))
        self.assertEqual(200, response.status_code)
        self.assertEqual(response.context["scheduled_event_fires_count"], 0)
        self.assertEqual(len(response.context["scheduled_event_fires"]), 2)

        # delete the event
        self.client.post(reverse("campaigns.campaignevent_delete", args=[event.id]), dict())
        self.assertFalse(CampaignEvent.objects.filter(is_active=True).exists())
        response = self.client.get(reverse("campaigns.campaign_read", args=[campaign.id]))
        self.assertNotContains(response, "Color Flow")

    def test_view_campaign_cant_modify_inactive_or_archive(self):
        self.login(self.admin)

        campaign = Campaign.create(self.org, self.admin, "Planting Reminders", self.farmers)

        response = self.client.get(reverse("campaigns.campaign_update", args=[campaign.id]))

        # sanity check, form is available in the response
        self.assertContains(response, "Planting Reminders")
        self.assertListEqual(list(response.context["form"].fields.keys()), ["name", "group", "loc"])

        # archive the campaign
        campaign.is_archived = True
        campaign.save()

        response = self.client.get(reverse("campaigns.campaign_update", args=[campaign.id]))

        # we should get 404 for the archived campaign
        self.assertEqual(response.status_code, 404)

        # deactivate the campaign
        campaign.is_archived = False
        campaign.is_active = False
        campaign.save()

        response = self.client.get(reverse("campaigns.campaign_update", args=[campaign.pk]))

        # we should get 404 for the inactive campaign
        self.assertEqual(response.status_code, 404)

    def test_view_campaign_read_with_customer_support(self):
        self.customer_support.is_staff = True
        self.customer_support.save()

        self.login(self.customer_support)

        campaign = Campaign.create(self.org, self.admin, "Perform the rain dance", self.farmers)

        response = self.client.get(reverse("campaigns.campaign_read", args=[campaign.pk]))

        gear_links = response.context["view"].get_gear_links()
        self.assertListEqual([gl["title"] for gl in gear_links], ["Service"])
        self.assertEqual(
            gear_links[-1]["href"],
            f"/org/service/?organization={campaign.org_id}&redirect_url=/campaign/read/{campaign.id}/",
        )

    def test_view_campaign_read_archived(self):
        self.login(self.admin)

        campaign = Campaign.create(self.org, self.admin, "Perform the rain dance", self.farmers)

        response = self.client.get(reverse("campaigns.campaign_read", args=[campaign.pk]))

        # page title and main content title should NOT contain (Archived)
        self.assertContains(response, "Perform the rain dance", count=2)
        self.assertContains(response, "(Archived)", count=0)

        gear_links = response.context["view"].get_gear_links()
        self.assertListEqual([gl["title"] for gl in gear_links], ["Add Event", "Export", "Edit", "Archive"])

        # archive the campaign
        campaign.is_archived = True
        campaign.save()

        response = self.client.get(reverse("campaigns.campaign_read", args=[campaign.pk]))

        # page title and main content title should contain (Archived)
        self.assertContains(response, "Perform the rain dance", count=2)
        self.assertContains(response, "(Archived)", count=2)

        gear_links = response.context["view"].get_gear_links()
        self.assertListEqual([gl["title"] for gl in gear_links], ["Activate", "Export"])

    def test_view_campaign_archive(self):
        self.login(self.admin)

        post_data = dict(name="Planting Reminders", group=self.farmers.pk)
        self.client.post(reverse("campaigns.campaign_create"), post_data)

        campaign = Campaign.objects.filter(is_active=True).first()

        # archive the campaign
        response = self.client.post(reverse("campaigns.campaign_archive", args=[campaign.pk]))

        self.assertRedirect(response, f"/campaign/read/{campaign.pk}/")

        campaign.refresh_from_db()
        self.assertTrue(campaign.is_archived)

    def test_view_campaign_activate(self):
        self.login(self.admin)

        post_data = dict(name="Planting Reminders", group=self.farmers.pk)
        self.client.post(reverse("campaigns.campaign_create"), post_data)

        campaign = Campaign.objects.filter(is_active=True).first()

        # activate the campaign
        response = self.client.post(reverse("campaigns.campaign_activate", args=[campaign.pk]))

        self.assertRedirect(response, f"/campaign/read/{campaign.pk}/")

        campaign.refresh_from_db()
        self.assertFalse(campaign.is_archived)

    def test_view_campaignevent_read_on_archived_campaign(self):
        self.login(self.admin)

        campaign = Campaign.create(self.org, self.admin, "Perform the rain dance", self.farmers)

        # create a reminder for our first planting event
        event = CampaignEvent.create_flow_event(
            self.org, self.admin, campaign, relative_to=self.planting_date, offset=3, unit="D", flow=self.reminder_flow
        )

        response = self.client.get(reverse("campaigns.campaignevent_read", args=[event.pk]))

        # page title and main content title should NOT contain (Archived)
        self.assertContains(response, "Perform the rain dance", count=2)
        self.assertContains(response, "(Archived)", count=0)

        gear_links = response.context["view"].get_gear_links()
        self.assertListEqual([gl["title"] for gl in gear_links], ["Edit", "Delete"])

        # archive the campaign
        campaign.is_archived = True
        campaign.save()

        response = self.client.get(reverse("campaigns.campaignevent_read", args=[event.pk]))

        # page title and main content title should contain (Archived)
        self.assertContains(response, "Perform the rain dance", count=2)
        self.assertContains(response, "(Archived)", count=1)

        gear_links = response.context["view"].get_gear_links()
        self.assertListEqual([gl["title"] for gl in gear_links], ["Delete"])

    def test_view_campaignevent_update_on_archived_campaign(self):
        self.login(self.admin)

        campaign = Campaign.create(self.org, self.admin, "Planting Reminders", self.farmers)

        # create a reminder for our first planting event
        event = CampaignEvent.create_flow_event(
            self.org, self.admin, campaign, relative_to=self.planting_date, offset=3, unit="D", flow=self.reminder_flow
        )

        response = self.client.get(reverse("campaigns.campaignevent_update", args=[event.pk]))

        # sanity check, form is available in the response
        self.assertContains(response, "Planting Reminder")

        self.assertEqual(
            set(response.context["form"].fields.keys()),
            {
                "offset",
                "unit",
                "relative_to",
                "event_type",
                "delivery_hour",
                "direction",
                "flow_to_start",
                "flow_start_mode",
                "message_start_mode",
                "eng",
                "loc",
            },
        )

        # archive the campaign
        campaign.is_archived = True
        campaign.save()

        response = self.client.get(reverse("campaigns.campaignevent_update", args=[campaign.pk]))

        # we should get 404 for the archived campaign
        self.assertEqual(response.status_code, 404)

        # deactivate the campaign
        campaign.is_archived = False
        campaign.is_active = False
        campaign.save()

        response = self.client.get(reverse("campaigns.campaign_update", args=[campaign.pk]))

        # we should get 404 for the inactive campaign
        self.assertEqual(response.status_code, 404)

    def test_view_campaignevent_create_on_archived_campaign(self):
        self.login(self.admin)

        campaign = Campaign.create(self.org, self.admin, "Planting Reminders", self.farmers)

        post_data = dict(
            relative_to=self.planting_date.pk,
            event_type="M",
            base="This is my message",
            spa="hola",
            direction="B",
            offset=1,
            unit="W",
            flow_to_start="",
            delivery_hour=13,
            message_start_mode="I",
        )

        response = self.client.post(
            reverse("campaigns.campaignevent_create") + "?campaign=%d" % campaign.pk, post_data
        )

        self.assertRedirect(response, reverse("campaigns.campaign_read", args=[campaign.pk]))

        # archive the campaign
        campaign.is_archived = True
        campaign.save()

        response = self.client.post(
            reverse("campaigns.campaignevent_create") + "?campaign=%d" % campaign.pk, post_data
        )

        # we should get 404 for the archived campaign
        self.assertEqual(response.status_code, 404)

        # deactivate the campaign
        campaign.is_archived = False
        campaign.is_active = False
        campaign.save()

        response = self.client.post(
            reverse("campaigns.campaignevent_create") + "?campaign=%d" % campaign.pk, post_data
        )

        # we should get 404 for the inactive campaign
        self.assertEqual(response.status_code, 404)

    def test_eventfire_get_relative_to_value(self):
        campaign = Campaign.create(self.org, self.admin, "Planting Reminders", self.farmers)
        created_on = self.org.contactfields.get(key="created_on")
        last_seen_on = self.org.contactfields.get(key="last_seen_on")

        # create a reminder for our first planting event
        event = CampaignEvent.create_flow_event(
            self.org, self.admin, campaign, relative_to=self.planting_date, offset=3, unit="D", flow=self.reminder_flow
        )
        self.set_contact_field(self.farmer1, "planting_date", self.org.format_datetime(timezone.now()))

        trim_date = timezone.now() - timedelta(days=settings.EVENT_FIRE_TRIM_DAYS + 1)
        ev = EventFire.objects.create(event=event, contact=self.farmer1, scheduled=trim_date, fired=trim_date)
        self.assertIsNotNone(ev.get_relative_to_value())

        # create event relative to created_on
        event2 = CampaignEvent.create_flow_event(
            self.org, self.admin, campaign, relative_to=created_on, offset=3, unit="D", flow=self.reminder_flow
        )

        trim_date = timezone.now() - timedelta(days=settings.EVENT_FIRE_TRIM_DAYS + 1)
        ev2 = EventFire.objects.create(event=event2, contact=self.farmer1, scheduled=trim_date, fired=trim_date)
        self.assertIsNotNone(ev2.get_relative_to_value())

        # recalculate for created on
        EventFire.update_campaign_events(campaign)
        self.assertEqual(2, EventFire.objects.filter(event__relative_to=created_on, fired=None).count())

        # create event relative to last_seen_on
        event3 = CampaignEvent.create_flow_event(
            self.org, self.admin, campaign, relative_to=last_seen_on, offset=3, unit="D", flow=self.reminder_flow
        )

        trim_date = timezone.now() - timedelta(days=settings.EVENT_FIRE_TRIM_DAYS + 1)
        ev3 = EventFire.objects.create(event=event3, contact=self.farmer1, scheduled=trim_date, fired=trim_date)
        self.assertIsNone(ev3.get_relative_to_value())

        # give contact a last seen on value
        self.farmer1.last_seen_on = timezone.now()
        self.farmer1.save(update_fields=("last_seen_on",), handle_update=False)

        ev4 = EventFire.objects.create(event=event3, contact=self.farmer1, scheduled=trim_date, fired=trim_date)
        self.assertIsNotNone(ev4.get_relative_to_value())

        # recalculate for created on
        EventFire.update_campaign_events(campaign)
        self.assertEqual(1, EventFire.objects.filter(event__relative_to=last_seen_on, fired=None).count())

    def test_campaignevent_calculate_scheduled_fire(self):
        planting_date = timezone.now()
        created_on = self.org.contactfields.get(key="created_on")
        last_seen_on = self.org.contactfields.get(key="last_seen_on")

        self.set_contact_field(self.farmer1, "planting_date", self.org.format_datetime(planting_date))

        campaign = Campaign.create(self.org, self.admin, "Planting Reminders", self.farmers)

        # create a reminder for our first planting event
        event = CampaignEvent.create_flow_event(
            self.org, self.admin, campaign, relative_to=self.planting_date, offset=3, unit="D", flow=self.reminder_flow
        )

        expected_result = (
            (planting_date + timedelta(days=3)).replace(second=0, microsecond=0).astimezone(self.org.timezone)
        )
        self.assertEqual(event.calculate_scheduled_fire(self.farmer1), expected_result)

        # create a reminder for our first planting event based on created_on
        event = CampaignEvent.create_flow_event(
            self.org, self.admin, campaign, relative_to=created_on, offset=5, unit="D", flow=self.reminder_flow
        )

        expected_result = (
            (self.farmer1.created_on + timedelta(days=5))
            .replace(second=0, microsecond=0)
            .astimezone(self.org.timezone)
        )
        self.assertEqual(event.calculate_scheduled_fire(self.farmer1), expected_result)

        # create a reminder based on last_seen_on
        event = CampaignEvent.create_flow_event(
            self.org, self.admin, campaign, relative_to=last_seen_on, offset=5, unit="D", flow=self.reminder_flow
        )
        self.assertIsNone(event.calculate_scheduled_fire(self.farmer1))

        # give contact a last seen on value
        now = timezone.now()
        self.farmer1.last_seen_on = now
        self.farmer1.save(update_fields=("last_seen_on",), handle_update=False)

        expected_result = (now + timedelta(days=5)).replace(second=0, microsecond=0).astimezone(self.org.timezone)
        self.assertEqual(event.calculate_scheduled_fire(self.farmer1), expected_result)

    def test_import_created_on_event(self):
        campaign = Campaign.create(self.org, self.admin, "New contact reminders", self.farmers)
        created_on = ContactField.system_fields.get(org=self.org, key="created_on")

        CampaignEvent.create_flow_event(
            self.org, self.admin, campaign, relative_to=created_on, offset=3, unit="D", flow=self.reminder_flow
        )

        self.login(self.admin)

        response = self.client.post(
            reverse("orgs.org_export"), {"flows": [self.reminder_flow.id], "campaigns": [campaign.id]}
        )
        exported = response.json()

        self.org.import_app(exported, self.admin)

    def test_deleting_reimport_contact_groups(self):
        with patch.object(timezone, "now", return_value=datetime(2020, 5, 1, 0, 0, 0, 0, pytz.UTC)):
            campaign = Campaign.create(self.org, self.admin, "Planting Reminders", self.farmers)

            # create a reminder for our first planting event
            planting_reminder = CampaignEvent.create_flow_event(
                self.org,
                self.admin,
                campaign,
                relative_to=self.planting_date,
                offset=3,
                unit="D",
                flow=self.reminder_flow,
            )

            self.assertEqual(0, EventFire.objects.all().count())
            self.set_contact_field(self.farmer1, "planting_date", "10-05-2020 12:30:10", legacy_handle=True)
            self.set_contact_field(self.farmer2, "planting_date", "15-05-2020 12:30:10", legacy_handle=True)

            # now we have event fires accordingly
            self.assertEqual(2, EventFire.objects.all().count())

            # farmer one fire
            scheduled = EventFire.objects.get(contact=self.farmer1, event=planting_reminder).scheduled
            self.assertEqual("13-5-2020", "%s-%s-%s" % (scheduled.day, scheduled.month, scheduled.year))

            # farmer two fire
            scheduled = EventFire.objects.get(contact=self.farmer2, event=planting_reminder).scheduled
            self.assertEqual("18-5-2020", "%s-%s-%s" % (scheduled.day, scheduled.month, scheduled.year))

            # delete our farmers group
            self.farmers.release()

            # this should have removed all the event fires for that group
            self.assertEqual(0, EventFire.objects.filter(event=planting_reminder).count())

            # and our group is no longer active
            self.assertFalse(campaign.group.is_active)

            # now import the group again
            filename = "farmers.csv"
            extra_fields = [dict(key="planting_date", header="field: planting_date", label="Planting Date", type="D")]
            import_params = dict(
                org_id=self.org.id,
                timezone=str(self.org.timezone),
                extra_fields=extra_fields,
                original_filename=filename,
            )

            task = ImportTask.objects.create(
                created_by=self.admin,
                modified_by=self.admin,
                csv_file="test_imports/" + filename,
                model_class="Contact",
                import_params=json.dumps(import_params),
                import_log="",
                task_id="A",
            )
            Contact.import_csv(task, log=None)

            # check that we have new planting dates
            self.farmer1 = Contact.objects.get(pk=self.farmer1.pk)
            self.farmer2 = Contact.objects.get(pk=self.farmer2.pk)

            planting = self.farmer1.get_field_value(self.planting_date)
            self.assertEqual("10-8-2020", "%s-%s-%s" % (planting.day, planting.month, planting.year))

            planting = self.farmer2.get_field_value(self.planting_date)
            self.assertEqual("15-8-2020", "%s-%s-%s" % (planting.day, planting.month, planting.year))

            # now update the campaign
            new_farmers = ContactGroup.user_groups.filter(name="Farmers", is_active=True).first()
            new_campaign = Campaign.create(self.org, self.admin, "Planting Reminders", new_farmers)
            new_planting_reminder = CampaignEvent.create_flow_event(
                self.org,
                self.admin,
                new_campaign,
                relative_to=self.planting_date,
                offset=3,
                unit="D",
                flow=self.reminder_flow,
            )

            self.login(self.admin)
            post_data = dict(name="Planting Reminders", group=new_farmers.pk)

            self.client.post(reverse("campaigns.campaign_update", args=[new_campaign.pk]), post_data)

            self.set_contact_field(self.farmer1, "planting_date", "13-08-2020 12:30:10", legacy_handle=True)
            self.set_contact_field(self.farmer2, "planting_date", "18-08-2020 12:30:10", legacy_handle=True)

            # should have two fresh new fires
            self.assertEqual(2, EventFire.objects.all().count())

            # check their new planting dates
            scheduled = EventFire.objects.get(contact=self.farmer1, event=new_planting_reminder).scheduled
            self.assertEqual("16-8-2020", "%s-%s-%s" % (scheduled.day, scheduled.month, scheduled.year))

            # farmer two fire
            scheduled = EventFire.objects.get(contact=self.farmer2, event=new_planting_reminder).scheduled
            self.assertEqual("21-8-2020", "%s-%s-%s" % (scheduled.day, scheduled.month, scheduled.year))

            # give our non farmer a planting date
            self.set_contact_field(self.nonfarmer, "planting_date", "20-05-2020 12:30:10", legacy_handle=True)

            # now update to the non-farmer group
            self.nonfarmers = self.create_group("Not Farmers", [self.nonfarmer])
            post_data = dict(name="Planting Reminders", group=self.nonfarmers.pk)
            self.client.post(reverse("campaigns.campaign_update", args=[new_campaign.pk]), post_data)

            # only one fire for the non-farmer the previous two should be deleted by the group change
            self.assertEqual(1, EventFire.objects.filter(event__is_active=True).count())
            self.assertEqual(2, EventFire.objects.filter(event__is_active=False).count())
            self.assertEqual(1, EventFire.objects.filter(event__is_active=True, contact=self.nonfarmer).count())

    def test_dst_scheduling(self):
        # set our timezone to something that honors DST
        eastern = pytz.timezone("US/Eastern")
        self.org.timezone = eastern
        self.org.save()

        # create our campaign and event
        campaign = Campaign.create(self.org, self.admin, "Planting Reminders", self.farmers)
        event = CampaignEvent.create_flow_event(
            self.org, self.admin, campaign, relative_to=self.planting_date, offset=2, unit="D", flow=self.reminder_flow
        )

        # set the time to something pre-dst (fall back on November 4th at 2am to 1am)
        self.set_contact_field(self.farmer1, "planting_date", "03-11-2029 12:30:00", legacy_handle=True)
        EventFire.update_campaign_events(campaign)

        # try changing our field type to something non-date, should throw
        with self.assertRaises(ValueError):
            ContactField.get_or_create(self.org, self.admin, "planting_date", value_type=Value.TYPE_TEXT)

        # we should be scheduled to go off on the 5th at 12:30:10 Eastern
        fire = EventFire.objects.filter(event__is_active=True).first()
        self.assertEqual(5, fire.scheduled.day)
        self.assertEqual(11, fire.scheduled.month)
        self.assertEqual(2029, fire.scheduled.year)
        self.assertEqual(12, fire.scheduled.astimezone(eastern).hour)

        # assert our offsets are different (we crossed DST)
        self.assertNotEqual(fire.scheduled.utcoffset(), self.farmer1.get_field_value(self.planting_date).utcoffset())

        # the number of hours between these two events should be 49 (two days 1 hour)
        delta = fire.scheduled - self.farmer1.get_field_value(self.planting_date)
        self.assertEqual(delta.days, 2)
        self.assertEqual(delta.seconds, 3600)

        # spring forward case, this will go across a DST jump forward scenario
        self.set_contact_field(self.farmer1, "planting_date", "10-03-2029 02:30:00", legacy_handle=True)
        EventFire.update_campaign_events(campaign)

        fire = EventFire.objects.filter(event__is_active=True).first()
        self.assertEqual(12, fire.scheduled.day)
        self.assertEqual(3, fire.scheduled.month)
        self.assertEqual(2029, fire.scheduled.year)
        self.assertEqual(2, fire.scheduled.astimezone(eastern).hour)

        # assert our offsets changed (we crossed DST)
        self.assertNotEqual(fire.scheduled.utcoffset(), self.farmer1.get_field_value(self.planting_date).utcoffset())

        # delta should be 47 hours exactly
        delta = fire.scheduled - self.farmer1.get_field_value(self.planting_date)
        self.assertEqual(delta.days, 1)
        self.assertEqual(delta.seconds, 82800)

        # release our campaign event
        event = campaign.get_events().first()
        event.release()

        # should be able to change our field type now
        ContactField.get_or_create(self.org, self.admin, "planting_date", value_type=Value.TYPE_TEXT)

    def test_scheduling(self):
        campaign = Campaign.create(self.org, self.admin, "Planting Reminders", self.farmers)

        self.assertEqual(str(campaign), f'Campaign[uuid={campaign.uuid}, name="Planting Reminders"]')

        # create a reminder for our first planting event
        planting_reminder = CampaignEvent.create_flow_event(
            self.org,
            self.admin,
            campaign,
            relative_to=self.planting_date,
            offset=0,
            unit="D",
            flow=self.reminder_flow,
            delivery_hour=17,
        )

        self.assertEqual(str(planting_reminder), 'Event[relative_to=planting_date, offset=0, flow="Reminder Flow"]')

        # schedule our reminders
        EventFire.update_campaign_events(campaign)

        # we should haven't any event fires created, since neither of our farmers have a planting date
        self.assertEqual(0, EventFire.objects.all().count())

        # ok, set a planting date on one of our contacts
        self.set_contact_field(self.farmer1, "planting_date", "05-10-2020 12:30:10", legacy_handle=True)

        # update our campaign events
        EventFire.update_campaign_events(campaign)

        # should have one event now
        fire = EventFire.objects.get(event__is_active=True)
        self.assertEqual(5, fire.scheduled.day)
        self.assertEqual(10, fire.scheduled.month)
        self.assertEqual(2020, fire.scheduled.year)

        # account for timezone difference, our org is in UTC+2
        self.assertEqual(17 - 2, fire.scheduled.hour)

        self.assertEqual(self.farmer1, fire.contact)

        planting_reminder = campaign.get_events().first()
        self.assertEqual(planting_reminder, fire.event)

        self.assertIsNone(fire.fired)

        # change the date of our date
        self.set_contact_field(self.farmer1, "planting_date", "06-10-2020 12:30:10", legacy_handle=True)

        EventFire.update_campaign_events_for_contact(campaign, self.farmer1)
        fire = EventFire.objects.get()
        self.assertEqual(6, fire.scheduled.day)
        self.assertEqual(10, fire.scheduled.month)
        self.assertEqual(2020, fire.scheduled.year)
        self.assertEqual(self.farmer1, fire.contact)
        self.assertEqual(planting_reminder, fire.event)

        # set it to something invalid
        self.set_contact_field(self.farmer1, "planting_date", "what?", legacy_handle=True)
        EventFire.update_campaign_events_for_contact(campaign, self.farmer1)
        self.assertFalse(EventFire.objects.all())

        # now something valid again
        self.set_contact_field(self.farmer1, "planting_date", "07-10-2020 12:30:10", legacy_handle=True)

        EventFire.update_campaign_events_for_contact(campaign, self.farmer1)
        fire = EventFire.objects.get()
        self.assertEqual(7, fire.scheduled.day)
        self.assertEqual(10, fire.scheduled.month)
        self.assertEqual(2020, fire.scheduled.year)
        self.assertEqual(self.farmer1, fire.contact)
        self.assertEqual(planting_reminder, fire.event)

        # create another reminder
        planting_reminder2 = CampaignEvent.create_flow_event(
            self.org,
            self.admin,
            campaign,
            relative_to=self.planting_date,
            offset=1,
            unit="D",
            flow=self.reminder2_flow,
        )

        self.assertEqual(1, planting_reminder2.abs_offset())

        # update the campaign
        EventFire.update_campaign_events(campaign)

        # since planting reminder had events, it'll get cloned
        planting_reminder = campaign.get_events().first()

        # should have two events now, ordered by date
        events = EventFire.objects.filter(event__is_active=True)

        self.assertEqual(planting_reminder, events[0].event)
        self.assertEqual(7, events[0].scheduled.day)

        self.assertEqual(planting_reminder2, events[1].event)
        self.assertEqual(8, events[1].scheduled.day)

        # mark one of the events as inactive
        planting_reminder2.is_active = False
        planting_reminder2.save()

        # update the campaign
        EventFire.update_campaign_events(campaign)

        # since planting reminder had events, it'll get cloned
        planting_reminder = campaign.get_events().first()

        # back to only one event
        fire = EventFire.objects.get(event__is_active=True)
        self.assertEqual(planting_reminder, fire.event)
        self.assertEqual(7, fire.scheduled.day)

        # update our date
        self.set_contact_field(self.farmer1, "planting_date", "09-10-2020 12:30", legacy_handle=True)

        # should have updated
        fire = EventFire.objects.get(event__is_active=True)
        self.assertEqual(planting_reminder, fire.event)
        self.assertEqual(9, fire.scheduled.day)

    def test_translations(self):
        campaign = Campaign.create(self.org, self.admin, "Planting Reminders", self.farmers)

        event1 = CampaignEvent.create_message_event(
            self.org,
            self.admin,
            campaign,
            relative_to=self.planting_date,
            offset=0,
            unit="D",
            message={"eng": "hello"},
            base_language="eng",
        )

        with self.assertRaises(ValidationError):
            event1.message = {"ddddd": "x"}
            event1.full_clean()

        with self.assertRaises(ValidationError):
            event1.message = {"eng": "x" * 8001}
            event1.full_clean()

        with self.assertRaises(ValidationError):
            event1.message = {}
            event1.full_clean()

    def test_unarchiving_campaigns(self):
        # create a campaign
        campaign = Campaign.create(self.org, self.user, "Planting Reminders", self.farmers)

        flow = self.create_flow()

        CampaignEvent.create_flow_event(
            self.org, self.admin, campaign, self.planting_date, offset=1, unit="W", flow=flow, delivery_hour="13"
        )
        CampaignEvent.create_flow_event(
            self.org,
            self.admin,
            campaign,
            self.planting_date,
            offset=1,
            unit="W",
            flow=self.reminder_flow,
            delivery_hour="9",
        )

        CampaignEvent.create_message_event(
            self.org,
            self.admin,
            campaign,
            self.planting_date,
            1,
            CampaignEvent.UNIT_DAYS,
            "Don't forget to brush your teeth",
        )

        flow.archive()
        campaign.is_archived = True
        campaign.save()

        self.assertTrue(campaign.is_archived)
        self.assertTrue(Flow.objects.filter(is_archived=True))

        # unarchive
        Campaign.apply_action_restore(self.admin, Campaign.objects.filter(pk=campaign.pk))
        campaign.refresh_from_db()
        self.assertFalse(campaign.is_archived)
        self.assertFalse(Flow.objects.filter(is_archived=True))

    @mock_mailroom
    def test_with_dynamic_group(self, mr_mocks):
        # create a campaign on a dynamic group
        self.create_field("gender", "Gender")

        women = self.create_group("Women", query='gender="F"')
        ContactGroup.user_groups.filter(id=women.id).update(status=ContactGroup.STATUS_READY)

        campaign = Campaign.create(self.org, self.admin, "Planting Reminders for Women", women)
        event = CampaignEvent.create_message_event(
            self.org,
            self.admin,
            campaign,
            relative_to=self.planting_date,
            offset=0,
            unit="D",
            message={"eng": "hello"},
            base_language="eng",
        )

        # create a contact not in the group, but with a field value
        anna = self.create_contact("Anna", urn="tel:+250788333333", fields={"planting_date": "09-10-2020 12:30"})

        # no contacts in our dynamic group yet, so no event fires
        self.assertEqual(EventFire.objects.filter(event=event).count(), 0)

        # update contact so that they become part of the dynamic group
        self.set_contact_field(anna, "gender", "f", legacy_handle=True)
        self.assertEqual(set(women.contacts.all()), {anna})

        # and who should now have an event fire for our campaign event
        self.assertEqual(EventFire.objects.filter(event=event, contact=anna).count(), 1)

        # change dynamic group query so anna is removed
        women.update_query(query='gender="FEMALE"')
        ContactGroup.user_groups.filter(id=women.id).update(status=ContactGroup.STATUS_READY)
        anna.handle_update(fields=["gender"])

        self.assertEqual(set(women.contacts.all()), set())

        # check that her event fire is now removed
        self.assertEqual(EventFire.objects.filter(event=event, contact=anna).count(), 0)

        # but if query is reverted, her event fire should be recreated
        women.update_query("gender=F")
        ContactGroup.user_groups.filter(id=women.id).update(status=ContactGroup.STATUS_READY)
        anna.handle_update(fields=["gender"])

        self.assertEqual(set(women.contacts.all()), {anna})

        # check that her event fire is now removed
        self.assertEqual(EventFire.objects.filter(event=event, contact=anna).count(), 1)

    def test_model_as_export_def(self):
        field_created_on = self.org.contactfields.get(key="created_on")
        campaign = Campaign.create(self.org, self.admin, "Planting Reminders", self.farmers)

        # create a reminder for our first planting event
        planting_reminder = CampaignEvent.create_flow_event(
            self.org, self.admin, campaign, relative_to=self.planting_date, offset=3, unit="D", flow=self.reminder_flow
        )

        self.assertEqual(
            campaign.as_export_def(),
            {
                "name": "Planting Reminders",
                "uuid": campaign.uuid,
                "group": {"uuid": self.farmers.uuid, "name": "Farmers"},
                "events": [
                    {
                        "uuid": planting_reminder.uuid,
                        "offset": 3,
                        "unit": "D",
                        "event_type": "F",
                        "start_mode": "I",
                        "delivery_hour": -1,
                        "message": None,
                        "relative_to": {"label": "Planting Date", "key": "planting_date"},
                        "flow": {"uuid": self.reminder_flow.uuid, "name": "Reminder Flow"},
                    }
                ],
            },
        )

        campaign2 = Campaign.create(self.org, self.admin, "Planting Reminders 2", self.farmers)
        planting_reminder2 = CampaignEvent.create_flow_event(
            self.org, self.admin, campaign2, relative_to=field_created_on, offset=2, unit="D", flow=self.reminder_flow
        )

        self.assertEqual(
            campaign2.as_export_def(),
            {
                "name": "Planting Reminders 2",
                "uuid": campaign2.uuid,
                "group": {"uuid": self.farmers.uuid, "name": "Farmers"},
                "events": [
                    {
                        "uuid": planting_reminder2.uuid,
                        "offset": 2,
                        "unit": "D",
                        "event_type": "F",
                        "start_mode": "I",
                        "delivery_hour": -1,
                        "message": None,
                        "relative_to": {"key": "created_on", "label": "Created On"},
                        "flow": {"uuid": self.reminder_flow.uuid, "name": "Reminder Flow"},
                    }
                ],
            },
        )

        campaign3 = Campaign.create(self.org, self.admin, "Planting Reminders 2", self.farmers)
        planting_reminder3 = CampaignEvent.create_message_event(
            self.org, self.admin, campaign3, relative_to=field_created_on, offset=2, unit="D", message="o' a framer?"
        )

        self.assertEqual(
            campaign3.as_export_def(),
            {
                "name": "Planting Reminders 2",
                "uuid": campaign3.uuid,
                "group": {"uuid": self.farmers.uuid, "name": "Farmers"},
                "events": [
                    {
                        "uuid": planting_reminder3.uuid,
                        "offset": 2,
                        "unit": "D",
                        "event_type": "M",
                        "start_mode": "I",
                        "delivery_hour": -1,
                        "message": {"base": "o' a framer?"},
                        "relative_to": {"key": "created_on", "label": "Created On"},
                        "base_language": "base",
                    }
                ],
            },
        )

    def test_campaign_create_flow_event(self):
        field_created_on = self.org.contactfields.get(key="created_on")
        field_language = self.org.contactfields.get(key="language")
        campaign = Campaign.create(self.org, self.admin, "Planting Reminders", self.farmers)

        new_org = Org.objects.create(
            name="Temba New",
            timezone=pytz.timezone("Africa/Kigali"),
            brand=settings.DEFAULT_BRAND,
            created_by=self.user,
            modified_by=self.user,
        )

        self.assertRaises(
            ValueError,
            CampaignEvent.create_flow_event,
            new_org,
            self.admin,
            campaign,
            offset=3,
            unit="D",
            flow=self.reminder_flow,
            relative_to=self.planting_date,
        )

        self.assertRaises(
            ValueError,
            CampaignEvent.create_flow_event,
            self.org,
            self.admin,
            campaign,
            offset=3,
            unit="D",
            flow=self.reminder_flow,
            relative_to=field_language,
        )

        campaign_event = CampaignEvent.create_flow_event(
            self.org, self.admin, campaign, offset=3, unit="D", flow=self.reminder_flow, relative_to=self.planting_date
        )

        self.assertEqual(campaign_event.campaign_id, campaign.id)
        self.assertEqual(campaign_event.offset, 3)
        self.assertEqual(campaign_event.unit, "D")
        self.assertEqual(campaign_event.relative_to_id, self.planting_date.id)
        self.assertEqual(campaign_event.flow_id, self.reminder_flow.id)
        self.assertEqual(campaign_event.event_type, "F")
        self.assertEqual(campaign_event.message, None)
        self.assertEqual(campaign_event.delivery_hour, -1)

        campaign_event = CampaignEvent.create_flow_event(
            self.org, self.admin, campaign, offset=3, unit="D", flow=self.reminder_flow, relative_to=field_created_on
        )

        self.assertEqual(campaign_event.campaign_id, campaign.id)
        self.assertEqual(campaign_event.offset, 3)
        self.assertEqual(campaign_event.unit, "D")
        self.assertEqual(campaign_event.relative_to_id, field_created_on.id)
        self.assertEqual(campaign_event.flow_id, self.reminder_flow.id)
        self.assertEqual(campaign_event.event_type, "F")
        self.assertEqual(campaign_event.message, None)
        self.assertEqual(campaign_event.delivery_hour, -1)

    def test_campaign_create_message_event(self):
        field_created_on = self.org.contactfields.get(key="created_on")
        field_language = self.org.contactfields.get(key="language")
        campaign = Campaign.create(self.org, self.admin, "Planting Reminders", self.farmers)

        new_org = Org.objects.create(
            name="Temba New",
            timezone=pytz.timezone("Africa/Kigali"),
            brand=settings.DEFAULT_BRAND,
            created_by=self.user,
            modified_by=self.user,
        )

        self.assertRaises(
            ValueError,
            CampaignEvent.create_message_event,
            new_org,
            self.admin,
            campaign,
            offset=3,
            unit="D",
            message="oy, pancake man, come back",
            relative_to=self.planting_date,
        )

        self.assertRaises(
            ValueError,
            CampaignEvent.create_message_event,
            self.org,
            self.admin,
            campaign,
            offset=3,
            unit="D",
            message="oy, pancake man, come back",
            relative_to=field_language,
        )

        campaign_event = CampaignEvent.create_message_event(
            self.org,
            self.admin,
            campaign,
            offset=3,
            unit="D",
            message="oy, pancake man, come back",
            relative_to=self.planting_date,
        )

        self.assertEqual(campaign_event.campaign_id, campaign.id)
        self.assertEqual(campaign_event.offset, 3)
        self.assertEqual(campaign_event.unit, "D")
        self.assertEqual(campaign_event.relative_to_id, self.planting_date.id)
        self.assertIsNotNone(campaign_event.flow_id)
        self.assertEqual(campaign_event.event_type, "M")
        self.assertEqual(campaign_event.message, {"base": "oy, pancake man, come back"})
        self.assertEqual(campaign_event.delivery_hour, -1)

        campaign_event = CampaignEvent.create_message_event(
            self.org,
            self.admin,
            campaign,
            offset=3,
            unit="D",
            message="oy, pancake man, come back",
            relative_to=field_created_on,
        )

        self.assertEqual(campaign_event.campaign_id, campaign.id)
        self.assertEqual(campaign_event.offset, 3)
        self.assertEqual(campaign_event.unit, "D")
        self.assertEqual(campaign_event.relative_to_id, field_created_on.id)
        self.assertIsNotNone(campaign_event.flow_id)
        self.assertEqual(campaign_event.event_type, "M")
        self.assertEqual(campaign_event.message, {"base": "oy, pancake man, come back"})
        self.assertEqual(campaign_event.delivery_hour, -1)


class CampaignCRUDLTest(TembaTest):
    def setUp(self):
        super().setUp()

        self.campaign1 = self.create_campaign(self.org)
        self.other_org_campaign = self.create_campaign(self.org2)

    def create_campaign(self, org):
        user = org.get_user()
        group = self.create_group("Reporters", contacts=[], org=org)
        registered = self.create_field("registered", "Registered", value_type="D", org=org)
        flow = self.create_flow(org=org)
        campaign = Campaign.create(org, user, "Welcomes", group)
        CampaignEvent.create_flow_event(
            org, user, campaign, registered, offset=1, unit="W", flow=flow, delivery_hour="13"
        )
        return campaign

    def test_read(self):
        read_url = reverse("campaigns.campaign_read", args=[self.campaign1.id])

        # can't view campaign if not logged in
        response = self.client.get(read_url)
        self.assertLoginRedirect(response)

        self.login(self.admin)

        response = self.client.get(read_url)
        self.assertContains(response, "Welcomes")
        self.assertContains(response, "Registered")

        # can't view campaign from other org
        response = self.client.get(reverse("campaigns.campaign_read", args=[self.other_org_campaign.id]))
        self.assertLoginRedirect(response)

    def test_archive_and_activate(self):
        archive_url = reverse("campaigns.campaign_archive", args=[self.campaign1.id])

        # can't archive campaign if not logged in
        response = self.client.post(archive_url)
        self.assertLoginRedirect(response)

        self.login(self.admin)

        response = self.client.post(archive_url)
        self.assertEqual(302, response.status_code)

        self.campaign1.refresh_from_db()
        self.assertTrue(self.campaign1.is_archived)

        # activate that archve
        response = self.client.post(reverse("campaigns.campaign_activate", args=[self.campaign1.id]))
        self.assertEqual(302, response.status_code)

        self.campaign1.refresh_from_db()
        self.assertFalse(self.campaign1.is_archived)

        # can't archive campaign from other org
        response = self.client.post(reverse("campaigns.campaign_archive", args=[self.other_org_campaign.id]))
        self.assertEqual(404, response.status_code)

        # check object is unchanged
        self.other_org_campaign.refresh_from_db()
        self.assertFalse(self.other_org_campaign.is_archived)


class CampaignEventCRUDLTest(TembaTest):
    def setUp(self):
        super().setUp()

        self.campaign1 = self.create_campaign(self.org)
        self.other_org_campaign = self.create_campaign(self.org2)

    def create_campaign(self, org):
        user = org.get_user()
        group = self.create_group("Reporters", contacts=[], org=org)
        registered = self.create_field("registered", "Registered", value_type="D", org=org)
        flow = self.create_flow(org=org)
        campaign = Campaign.create(org, user, "Welcomes", group)
        CampaignEvent.create_flow_event(
            org, user, campaign, registered, offset=1, unit="W", flow=flow, delivery_hour="13"
        )
        CampaignEvent.create_flow_event(
            org, user, campaign, registered, offset=2, unit="W", flow=flow, delivery_hour="13"
        )
        return campaign

    def test_read(self):
        event1 = self.campaign1.events.order_by("id").first()
        other_org_event1 = self.other_org_campaign.events.order_by("id").first()

        read_url = reverse("campaigns.campaignevent_read", args=[event1.id])

        # can't view event if not logged in
        response = self.client.get(read_url)
        self.assertLoginRedirect(response)

        self.login(self.admin)

        response = self.client.get(read_url)
        self.assertContains(response, "Welcomes")
        self.assertContains(response, "1 Week After")
        self.assertContains(response, "Registered")

        # can't view event from other org
        response = self.client.get(reverse("campaigns.campaignevent_read", args=[other_org_event1.id]))
        self.assertLoginRedirect(response)

    def test_update(self):
        event1, event2 = self.campaign1.events.order_by("id")
        other_org_event1 = self.other_org_campaign.events.order_by("id").first()

        update_url = reverse("campaigns.campaignevent_update", args=[event1.id])

        # can't view update form if not logged in
        response = self.client.get(update_url)
        self.assertLoginRedirect(response)

        self.login(self.admin)

        response = self.client.get(update_url)
        self.assertEqual(
            [
                "event_type",
                "relative_to",
                "offset",
                "unit",
                "delivery_hour",
                "direction",
                "flow_to_start",
                "flow_start_mode",
                "message_start_mode",
                "eng",
                "loc",
            ],
            list(response.context["form"].fields.keys()),
        )

        # can't view update form for event from other org
        response = self.client.get(reverse("campaigns.campaignevent_update", args=[other_org_event1.id]))
        self.assertLoginRedirect(response)

        accepted = self.create_field("accepted", "Accepted", value_type="D")

        # update the first event
        response = self.client.post(
            update_url,
            {
                "relative_to": accepted.id,
                "event_type": "M",
                "eng": "Hi there",
                "direction": "B",
                "offset": 2,
                "unit": "D",
                "flow_to_start": "",
                "delivery_hour": 11,
            },
        )
        self.assertEqual(302, response.status_code)

        # original event will be unchanged.. except to be inactive
        event1.refresh_from_db()
        self.assertEqual("F", event1.event_type)
        self.assertFalse(event1.is_active)

        # but will have a new replacement event
        new_event1 = self.campaign1.events.filter(id__gt=event2.id).get()

        self.assertEqual(accepted, new_event1.relative_to)
        self.assertEqual("M", new_event1.event_type)
        self.assertEqual(-2, new_event1.offset)
        self.assertEqual("D", new_event1.unit)

        # can't update event in other org
        response = self.client.post(
            update_url,
            {
                "relative_to": other_org_event1.relative_to,
                "event_type": "M",
                "eng": "Hi there",
                "direction": "B",
                "offset": 2,
                "unit": "D",
                "flow_to_start": "",
                "delivery_hour": 11,
            },
        )
        self.assertEqual(404, response.status_code)

        # check event is unchanged
        other_org_event1.refresh_from_db()
        self.assertEqual("F", other_org_event1.event_type)
        self.assertTrue(other_org_event1.is_active)
