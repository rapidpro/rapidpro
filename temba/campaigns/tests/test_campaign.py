import json
from datetime import timedelta
from zoneinfo import ZoneInfo

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.files.storage import default_storage
from django.urls import reverse
from django.utils import timezone

from temba.campaigns.models import Campaign, CampaignEvent, EventFire
from temba.campaigns.tasks import trim_event_fires
from temba.contacts.models import ContactField
from temba.flows.models import Flow
from temba.msgs.models import Msg
from temba.orgs.models import DefinitionExport, Org
from temba.tests import TembaTest, matchers, mock_mailroom


class CampaignTest(TembaTest):
    def setUp(self):
        super().setUp()

        self.farmer1 = self.create_contact("Rob Jasper", phone="+250788111111")
        self.farmer2 = self.create_contact("Mike Gordon", phone="+250788222222", language="spa")

        self.nonfarmer = self.create_contact("Trey Anastasio", phone="+250788333333")
        self.farmers = self.create_group("Farmers", [self.farmer1, self.farmer2])

        self.reminder_flow = self.create_flow(name="Reminder Flow")
        self.reminder2_flow = self.create_flow(name="Planting Reminder")

        self.background_flow = self.create_flow(name="Background Flow", flow_type=Flow.TYPE_BACKGROUND)

        # create a voice flow to make sure they work too, not a proper voice flow but
        # sufficient for assuring these flow types show up where they should
        self.voice_flow = self.create_flow(name="IVR flow", flow_type="V")

        # create a contact field for our planting date
        self.planting_date = self.create_field("planting_date", "Planting Date", value_type=ContactField.TYPE_DATETIME)

    @mock_mailroom
    def test_model(self, mr_mocks):
        contact = self.create_contact("Joe", phone="+1234567890")
        campaign = Campaign.create(self.org, self.admin, Campaign.get_unique_name(self.org, "Reminders"), self.farmers)
        flow = self.create_flow("Test Flow")

        event1 = CampaignEvent.create_flow_event(
            self.org, self.admin, campaign, self.planting_date, offset=1, unit="W", flow=flow, delivery_hour=13
        )
        event2 = CampaignEvent.create_message_event(
            self.org, self.admin, campaign, self.planting_date, offset=3, unit="D", message="Hello", delivery_hour=9
        )

        self.assertEqual("Reminders", campaign.name)
        self.assertEqual("Reminders", str(campaign))
        self.assertEqual(f'<Event: id={event1.id} relative_to=planting_date offset=1 flow="Test Flow">', repr(event1))
        self.assertEqual([event1, event2], list(campaign.get_events()))
        self.assertEqual(None, event1.get_message(contact))
        self.assertEqual("Hello", event2.get_message(contact))

        campaign.schedule_events_async()

        # should have queued a scheduling task to mailroom for each event
        self.assertEqual(
            [
                {
                    "org_id": self.org.id,
                    "type": "schedule_campaign_event",
                    "queued_on": matchers.Datetime(),
                    "task": {"campaign_event_id": event1.id, "org_id": self.org.id},
                },
                {
                    "org_id": self.org.id,
                    "type": "schedule_campaign_event",
                    "queued_on": matchers.Datetime(),
                    "task": {"campaign_event_id": event2.id, "org_id": self.org.id},
                },
            ],
            mr_mocks.queued_batch_tasks,
        )

        campaign.recreate_events()

        # existing events should be deactivated
        event1.refresh_from_db()
        event2.refresh_from_db()
        self.assertFalse(event1.is_active)
        self.assertFalse(event2.is_active)

        # and clones created
        new_event1, new_event2 = campaign.events.filter(is_active=True).order_by("id")

        self.assertEqual(self.planting_date, new_event1.relative_to)
        self.assertEqual("W", new_event1.unit)
        self.assertEqual(1, new_event1.offset)
        self.assertEqual(flow, new_event1.flow)
        self.assertEqual(13, new_event1.delivery_hour)
        self.assertEqual("D", new_event2.unit)
        self.assertEqual(3, new_event2.offset)
        self.assertEqual({"eng": "Hello"}, new_event2.message)

    def test_get_offset_display(self):
        campaign = Campaign.create(self.org, self.admin, Campaign.get_unique_name(self.org, "Reminders"), self.farmers)
        flow = self.create_flow("Test")
        event = CampaignEvent.create_flow_event(
            self.org, self.admin, campaign, self.planting_date, offset=0, unit="W", flow=flow
        )

        def assert_display(offset: int, unit: str, expected: str):
            event.offset = offset
            event.unit = unit
            self.assertEqual(expected, event.offset_display)

        assert_display(-2, "M", "2 minutes before")
        assert_display(-1, "M", "1 minute before")
        assert_display(0, "M", "on")
        assert_display(1, "M", "1 minute after")
        assert_display(2, "M", "2 minutes after")
        assert_display(-2, "H", "2 hours before")
        assert_display(-1, "H", "1 hour before")
        assert_display(0, "H", "on")
        assert_display(1, "H", "1 hour after")
        assert_display(2, "H", "2 hours after")
        assert_display(-2, "D", "2 days before")
        assert_display(-1, "D", "1 day before")
        assert_display(0, "D", "on")
        assert_display(1, "D", "1 day after")
        assert_display(2, "D", "2 days after")
        assert_display(-2, "W", "2 weeks before")
        assert_display(-1, "W", "1 week before")
        assert_display(0, "W", "on")
        assert_display(1, "W", "1 week after")
        assert_display(2, "W", "2 weeks after")

    def test_get_unique_name(self):
        self.assertEqual("Reminders", Campaign.get_unique_name(self.org, "Reminders"))

        # ensure checking against existing campaigns is case-insensitive
        reminders = Campaign.create(self.org, self.admin, "REMINDERS", self.farmers)

        self.assertEqual("Reminders 2", Campaign.get_unique_name(self.org, "Reminders"))
        self.assertEqual("Reminders", Campaign.get_unique_name(self.org, "Reminders", ignore=reminders))
        self.assertEqual("Reminders", Campaign.get_unique_name(self.org2, "Reminders"))  # different org

        Campaign.create(self.org, self.admin, "Reminders 2", self.farmers)

        self.assertEqual("Reminders 3", Campaign.get_unique_name(self.org, "Reminders"))

        # ensure we don't exceed the name length limit
        Campaign.create(self.org, self.admin, "X" * 64, self.farmers)

        self.assertEqual(f"{'X' * 62} 2", Campaign.get_unique_name(self.org, "X" * 64))

    def test_get_sorted_events(self):
        # create a campaign
        campaign = Campaign.create(self.org, self.user, "Planting Reminders", self.farmers)

        flow = self.create_flow("Test 1")

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

        event4 = CampaignEvent.create_flow_event(
            self.org,
            self.admin,
            campaign,
            self.planting_date,
            offset=2,
            unit="W",
            flow=self.create_flow("Test 2"),
            delivery_hour="5",
        )

        self.assertEqual(campaign.get_sorted_events(), [event2, event1, event3, event4])

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
            message={"eng": "Hi @(upper(contact.name)) don't forget to plant on @(format_date(contact.planting_date))"},
            base_language="eng",
        )

        self.assertEqual(
            {
                "uuid": str(event.flow.uuid),
                "name": event.flow.name,
                "spec_version": Flow.CURRENT_SPEC_VERSION,
                "revision": 1,
                "language": "eng",
                "type": "messaging_background",
                "expire_after_minutes": 0,
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
            event.flow.get_definition(),
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

        trim_date = timezone.now() - (settings.RETENTION_PERIODS["eventfire"] + timedelta(days=1))

        # manually create two event fires
        EventFire.objects.create(event=event, contact=self.farmer1, scheduled=trim_date, fired=trim_date)
        e2 = EventFire.objects.create(event=event, contact=self.farmer1, scheduled=timezone.now(), fired=timezone.now())

        # create an unfired fire and release its event
        EventFire.objects.create(event=second_event, contact=self.farmer1, scheduled=trim_date)
        second_event.release(self.admin)

        # trim our events, one fired and one inactive onfired
        trim_event_fires()

        # should now have only one event, e2
        e = EventFire.objects.get()
        self.assertEqual(e.id, e2.id)

    @mock_mailroom
    def test_views(self, mr_mocks):
        open_tickets = self.org.groups.get(name="Open Tickets")

        current_year = timezone.now().year

        # update the planting date for our contacts
        self.set_contact_field(self.farmer1, "planting_date", f"1/10/{current_year-2}")

        # don't log in, try to create a new campaign
        response = self.client.get(reverse("campaigns.campaign_create"))
        self.assertLoginRedirect(response)

        # ok log in as an org
        self.login(self.admin)

        # go to to the creation page
        response = self.client.get(reverse("campaigns.campaign_create"))
        self.assertEqual(200, response.status_code)

        # groups shouldn't include the group that isn't ready
        self.assertEqual({open_tickets, self.farmers}, set(response.context["form"].fields["group"].queryset))

        response = self.client.post(
            reverse("campaigns.campaign_create"), {"name": "Planting Reminders", "group": self.farmers.id}
        )

        # should redirect to read page for this campaign
        campaign = Campaign.objects.filter(is_active=True).first()
        self.assertRedirect(response, reverse("campaigns.campaign_read", args=[campaign.uuid]))

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
        self.client.post(reverse("campaigns.campaign_list"), {"action": "archive", "objects": campaign.id})
        response = self.client.get(reverse("campaigns.campaign_list"))
        self.assertNotContains(response, "Planting Reminders")

        # restore the campaign
        response = self.client.get(reverse("campaigns.campaign_archived"))
        self.assertContains(response, "Planting Reminders")
        self.client.post(reverse("campaigns.campaign_archived"), {"action": "restore", "objects": campaign.id})
        response = self.client.get(reverse("campaigns.campaign_archived"))
        self.assertNotContains(response, "Planting Reminders")
        response = self.client.get(reverse("campaigns.campaign_list"))
        self.assertContains(response, "Planting Reminders")

        # test viewers cannot use action archive or restore
        self.client.logout()

        # login as a viewer
        self.login(self.user)

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
        response = self.client.post(reverse("campaigns.campaignevent_create") + "?campaign=%d" % campaign.pk, post_data)

        self.assertTrue(response.context["form"].errors)
        self.assertIn("A message is required", str(response.context["form"].errors["__all__"]))

        post_data = dict(
            relative_to=self.planting_date.pk,
            delivery_hour=-1,
            eng="allo!" * 500,
            direction="A",
            offset=2,
            unit="D",
            event_type="M",
            flow_to_start=self.reminder_flow.pk,
            flow_start_mode="I",
        )

        response = self.client.post(reverse("campaigns.campaignevent_create") + "?campaign=%d" % campaign.pk, post_data)

        self.assertFormError(
            response.context["form"], None, f"Translation for 'English' exceeds the {Msg.MAX_TEXT_LEN} character limit."
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
        response = self.client.post(reverse("campaigns.campaignevent_create") + "?campaign=%d" % campaign.pk, post_data)

        self.assertFormError(response.context["form"], "flow_to_start", "This field is required.")

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
        response = self.client.post(reverse("campaigns.campaignevent_create") + "?campaign=%d" % campaign.pk, post_data)

        event = CampaignEvent.objects.filter(is_active=True).get()
        # should be redirected back to our campaign read page
        self.assertRedirect(response, reverse("campaigns.campaign_read", args=[campaign.uuid]))

        self.assertEqual(self.reminder_flow, event.flow)
        self.assertEqual(self.planting_date, event.relative_to)
        self.assertEqual(2, event.offset)
        self.assertEqual("I", event.start_mode)

        # read the campaign read page
        response = self.client.get(reverse("campaigns.campaign_read", args=[campaign.uuid]))
        self.assertContains(response, "Reminder Flow")
        self.assertContains(response, "1")

        # should have queued a scheduling task to mailroom
        self.assertEqual(
            [
                {
                    "org_id": self.org.id,
                    "type": "schedule_campaign_event",
                    "queued_on": matchers.Datetime(),
                    "task": {"campaign_event_id": event.id, "org_id": self.org.id},
                }
            ],
            mr_mocks.queued_batch_tasks,
        )

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

        # should have queued another scheduling task to mailroom
        self.assertEqual(2, len(mr_mocks.queued_batch_tasks))
        self.assertEqual("schedule_campaign_event", mr_mocks.queued_batch_tasks[-1]["type"])

        # should be redirected back to our campaign event read page
        event = CampaignEvent.objects.filter(is_active=True).get()
        self.assertRedirect(response, reverse("campaigns.campaignevent_read", args=[event.campaign.uuid, event.pk]))

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
        response = self.client.get(
            reverse("campaigns.campaignevent_read", args=[previous_event.campaign.uuid, previous_event.pk])
        )
        self.assertRedirect(response, reverse("campaigns.campaign_read", args=[previous_event.campaign.uuid]))

        # attempting to update our old event gives a 404
        response = self.client.post(reverse("campaigns.campaignevent_update", args=[previous_event.pk]), post_data)
        self.assertEqual(404, response.status_code)

        # should now have update the campaign event
        self.assertEqual(self.reminder_flow, event.flow)
        self.assertEqual(self.planting_date, event.relative_to)
        self.assertEqual(1, event.offset)
        self.assertEqual("S", event.start_mode)

        # should have queued another scheduling task to mailroom
        self.assertEqual(3, len(mr_mocks.queued_batch_tasks))
        self.assertEqual("schedule_campaign_event", mr_mocks.queued_batch_tasks[-1]["type"])

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

        # should have queued another scheduling task to mailroom
        self.assertEqual(4, len(mr_mocks.queued_batch_tasks))

        # trying to archive our flow should fail since it belongs to a campaign
        post_data = dict(action="archive", objects=[self.reminder_flow.pk])
        response = self.client.post(reverse("flows.flow_list"), post_data)
        self.reminder_flow.refresh_from_db()
        self.assertFalse(self.reminder_flow.is_archived)
        # TODO: Convert to temba-toast
        # self.assertEqual(
        # "The following flows are still used by campaigns so could not be archived: Reminder Flow",
        # response.get("Temba-Toast"),
        # )

        post_data = dict(action="archive", objects=[self.reminder_flow.pk, self.reminder2_flow.pk])
        response = self.client.post(reverse("flows.flow_list"), post_data)
        # self.assertEqual(
        # "The following flows are still used by campaigns so could not be archived: Planting Reminder, Reminder Flow",
        # response.get("Temba-Toast"),
        # )

        for e in CampaignEvent.objects.filter(flow=self.reminder2_flow.pk):
            e.release(self.admin)

        # archive the campaign
        post_data = dict(action="archive", objects=campaign.pk)
        self.client.post(reverse("campaigns.campaign_list"), post_data)
        response = self.client.get(reverse("campaigns.campaign_list"))
        self.assertNotContains(response, "Planting Reminders")

        # should not have queued another scheduling task to mailroom since campaign is now archived
        self.assertEqual(4, len(mr_mocks.queued_batch_tasks))

        # shouldn't have any active event fires
        self.assertFalse(EventFire.objects.filter(event__is_active=True).exists())

        # restore the campaign
        post_data = dict(action="restore", objects=campaign.pk)
        self.client.post(reverse("campaigns.campaign_archived"), post_data)

        # should have queued another scheduling task to mailroom
        self.assertEqual(5, len(mr_mocks.queued_batch_tasks))

        # set a planting date on our other farmer
        self.set_contact_field(self.farmer2, "planting_date", f"1/6/{current_year+1}")

        # should have an event fire now
        fires = EventFire.objects.filter(event__is_active=True)
        self.assertEqual(1, len(fires))

        # setting a planting date on our outside contact has no effect
        self.set_contact_field(self.nonfarmer, "planting_date", f"1/7/{current_year+3}")
        self.assertEqual(1, EventFire.objects.filter(event__is_active=True).count())

        self.set_contact_field(self.farmer1, "planting_date", f"4/8/{current_year-2}")

        event = CampaignEvent.objects.filter(is_active=True).first()

        # get the detail page of the event
        response = self.client.get(reverse("campaigns.campaignevent_read", args=[event.campaign.uuid, event.id]))
        self.assertEqual(200, response.status_code)
        self.assertEqual(response.context["scheduled_event_fires_count"], 0)
        self.assertEqual(len(response.context["scheduled_event_fires"]), 1)

        # delete the event
        self.client.post(reverse("campaigns.campaignevent_delete", args=[event.id]), dict())
        self.assertFalse(CampaignEvent.objects.filter(is_active=True).exists())
        response = self.client.get(reverse("campaigns.campaign_read", args=[campaign.uuid]))
        self.assertNotContains(response, "Color Flow")

        post_data = dict(
            relative_to=self.planting_date.pk,
            delivery_hour=-1,
            base="",
            direction="A",
            offset=2,
            unit="D",
            event_type="F",
            flow_start_mode="I",
            flow_to_start=self.background_flow.pk,
        )
        response = self.client.post(reverse("campaigns.campaignevent_create") + "?campaign=%d" % campaign.pk, post_data)

        # events created with background flows are always passive start mode
        event = CampaignEvent.objects.filter(is_active=True).get()
        self.assertEqual(CampaignEvent.MODE_PASSIVE, event.start_mode)

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

    def test_view_campaign_archive(self):
        self.login(self.admin)

        post_data = dict(name="Planting Reminders", group=self.farmers.pk)
        self.client.post(reverse("campaigns.campaign_create"), post_data)

        campaign = Campaign.objects.filter(is_active=True).first()

        # archive the campaign
        response = self.client.post(reverse("campaigns.campaign_archive", args=[campaign.pk]))

        self.assertRedirect(response, f"/campaign/read/{campaign.uuid}/")

        campaign.refresh_from_db()
        self.assertTrue(campaign.is_archived)

    def test_view_campaign_activate(self):
        self.login(self.admin)

        post_data = dict(name="Planting Reminders", group=self.farmers.pk)
        self.client.post(reverse("campaigns.campaign_create"), post_data)

        campaign = Campaign.objects.filter(is_active=True).first()

        # activate the campaign
        response = self.client.post(reverse("campaigns.campaign_activate", args=[campaign.pk]))

        self.assertRedirect(response, f"/campaign/read/{campaign.uuid}/")

        campaign.refresh_from_db()
        self.assertFalse(campaign.is_archived)

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
                "kin",
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
            eng="This is my message",
            kin="muraho",
            direction="B",
            offset=1,
            unit="W",
            flow_to_start="",
            delivery_hour=13,
            message_start_mode="I",
        )

        response = self.client.post(reverse("campaigns.campaignevent_create") + "?campaign=%d" % campaign.pk, post_data)

        self.assertRedirect(response, reverse("campaigns.campaign_read", args=[campaign.uuid]))

        # archive the campaign
        campaign.is_archived = True
        campaign.save()

        response = self.client.post(reverse("campaigns.campaignevent_create") + "?campaign=%d" % campaign.pk, post_data)

        # we should get 404 for the archived campaign
        self.assertEqual(response.status_code, 404)

        # deactivate the campaign
        campaign.is_archived = False
        campaign.is_active = False
        campaign.save()

        response = self.client.post(reverse("campaigns.campaignevent_create") + "?campaign=%d" % campaign.pk, post_data)

        # we should get 404 for the inactive campaign
        self.assertEqual(response.status_code, 404)

    def test_eventfire_get_relative_to_value(self):
        campaign = Campaign.create(self.org, self.admin, "Planting Reminders", self.farmers)
        created_on = self.org.fields.get(key="created_on")
        last_seen_on = self.org.fields.get(key="last_seen_on")

        # create a reminder for our first planting event
        event = CampaignEvent.create_flow_event(
            self.org, self.admin, campaign, relative_to=self.planting_date, offset=3, unit="D", flow=self.reminder_flow
        )
        self.set_contact_field(self.farmer1, "planting_date", self.org.format_datetime(timezone.now()))

        trim_date = timezone.now() - (settings.RETENTION_PERIODS["eventfire"] + timedelta(days=1))
        ev = EventFire.objects.create(event=event, contact=self.farmer1, scheduled=trim_date, fired=trim_date)
        self.assertIsNotNone(ev.get_relative_to_value())

        # create event relative to created_on
        event2 = CampaignEvent.create_flow_event(
            self.org, self.admin, campaign, relative_to=created_on, offset=3, unit="D", flow=self.reminder_flow
        )

        trim_date = timezone.now() - (settings.RETENTION_PERIODS["eventfire"] + timedelta(days=1))
        ev2 = EventFire.objects.create(event=event2, contact=self.farmer1, scheduled=trim_date, fired=trim_date)
        self.assertIsNotNone(ev2.get_relative_to_value())

        # create event relative to last_seen_on
        event3 = CampaignEvent.create_flow_event(
            self.org, self.admin, campaign, relative_to=last_seen_on, offset=3, unit="D", flow=self.reminder_flow
        )

        trim_date = timezone.now() - (settings.RETENTION_PERIODS["eventfire"] + timedelta(days=1))
        ev3 = EventFire.objects.create(event=event3, contact=self.farmer1, scheduled=trim_date, fired=trim_date)
        self.assertIsNone(ev3.get_relative_to_value())

        # give contact a last seen on value
        self.farmer1.last_seen_on = timezone.now()
        self.farmer1.save(update_fields=("last_seen_on",))

        ev4 = EventFire.objects.create(event=event3, contact=self.farmer1, scheduled=trim_date, fired=trim_date)
        self.assertIsNotNone(ev4.get_relative_to_value())

    def test_import(self):
        self.import_file("test_flows/the_clinic.json")
        self.assertEqual(1, Campaign.objects.count())

        campaign = Campaign.objects.get()
        self.assertEqual("Appointment Schedule", campaign.name)
        self.assertEqual(6, campaign.events.count())

        events = list(campaign.events.order_by("id"))
        self.assertEqual(CampaignEvent.TYPE_FLOW, events[0].event_type)
        self.assertEqual(CampaignEvent.TYPE_FLOW, events[1].event_type)
        self.assertEqual(CampaignEvent.TYPE_FLOW, events[2].event_type)
        self.assertEqual(CampaignEvent.TYPE_FLOW, events[3].event_type)
        self.assertEqual(CampaignEvent.TYPE_MESSAGE, events[4].event_type)
        self.assertEqual(CampaignEvent.TYPE_MESSAGE, events[5].event_type)

        # message flow should be migrated to latest engine spec
        self.assertEqual({"und": "This is a second campaign message"}, events[5].message)
        self.assertEqual("und", events[5].flow.base_language)
        self.assertEqual(Flow.CURRENT_SPEC_VERSION, events[5].flow.version_number)

    def test_import_created_on_event(self):
        campaign = Campaign.create(self.org, self.admin, "New contact reminders", self.farmers)
        created_on = self.org.fields.get(key="created_on")

        CampaignEvent.create_flow_event(
            self.org, self.admin, campaign, relative_to=created_on, offset=3, unit="D", flow=self.reminder_flow
        )

        self.login(self.admin)

        export = DefinitionExport.create(self.org, self.admin, flows=[], campaigns=[campaign])
        export.perform()

        with default_storage.open(f"orgs/{self.org.id}/definition_exports/{export.uuid}.json") as export_file:
            exported = json.loads(export_file.read())

        self.org.import_app(exported, self.admin)

    def test_update_to_non_date(self):
        # create our campaign and event
        campaign = Campaign.create(self.org, self.admin, "Planting Reminders", self.farmers)
        event = CampaignEvent.create_flow_event(
            self.org, self.admin, campaign, relative_to=self.planting_date, offset=2, unit="D", flow=self.reminder_flow
        )

        # try changing our field type to something non-date, should throw
        with self.assertRaises(ValueError):
            ContactField.get_or_create(self.org, self.admin, "planting_date", value_type=ContactField.TYPE_TEXT)

        # release our campaign event
        event.release(self.admin)

        # should be able to change our field type now
        ContactField.get_or_create(self.org, self.admin, "planting_date", value_type=ContactField.TYPE_TEXT)

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

        flow = self.create_flow("Test")

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

        flow.archive(self.admin)
        campaign.is_archived = True
        campaign.save()

        self.assertTrue(campaign.is_archived)
        self.assertTrue(Flow.objects.filter(is_archived=True))

        # unarchive
        Campaign.apply_action_restore(self.admin, Campaign.objects.filter(pk=campaign.pk))
        campaign.refresh_from_db()
        self.assertFalse(campaign.is_archived)
        self.assertFalse(Flow.objects.filter(is_archived=True))

    def test_model_as_export_def(self):
        field_created_on = self.org.fields.get(key="created_on")
        campaign = Campaign.create(self.org, self.admin, "Planting Reminders", self.farmers)

        # create a reminder for our first planting event
        planting_reminder = CampaignEvent.create_flow_event(
            self.org, self.admin, campaign, relative_to=self.planting_date, offset=3, unit="D", flow=self.reminder_flow
        )

        self.assertEqual(
            campaign.as_export_def(),
            {
                "name": "Planting Reminders",
                "uuid": str(campaign.uuid),
                "group": {"uuid": str(self.farmers.uuid), "name": "Farmers"},
                "events": [
                    {
                        "uuid": str(planting_reminder.uuid),
                        "offset": 3,
                        "unit": "D",
                        "event_type": "F",
                        "start_mode": "I",
                        "delivery_hour": -1,
                        "message": None,
                        "relative_to": {"label": "Planting Date", "key": "planting_date"},
                        "flow": {"uuid": str(self.reminder_flow.uuid), "name": "Reminder Flow"},
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
                "uuid": str(campaign2.uuid),
                "group": {"uuid": str(self.farmers.uuid), "name": "Farmers"},
                "events": [
                    {
                        "uuid": str(planting_reminder2.uuid),
                        "offset": 2,
                        "unit": "D",
                        "event_type": "F",
                        "start_mode": "I",
                        "delivery_hour": -1,
                        "message": None,
                        "relative_to": {"key": "created_on", "label": "Created On"},
                        "flow": {"uuid": str(self.reminder_flow.uuid), "name": "Reminder Flow"},
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
                "uuid": str(campaign3.uuid),
                "group": {"uuid": str(self.farmers.uuid), "name": "Farmers"},
                "events": [
                    {
                        "uuid": str(planting_reminder3.uuid),
                        "offset": 2,
                        "unit": "D",
                        "event_type": "M",
                        "start_mode": "I",
                        "delivery_hour": -1,
                        "message": {"eng": "o' a framer?"},
                        "relative_to": {"key": "created_on", "label": "Created On"},
                        "base_language": "eng",
                    }
                ],
            },
        )

    def test_create_flow_event(self):
        gender = self.create_field("gender", "Gender", value_type="T")
        created_on = self.org.fields.get(key="created_on")
        campaign = Campaign.create(self.org, self.admin, "Planting Reminders", self.farmers)

        new_org = Org.objects.create(
            name="Temba New", timezone=ZoneInfo("Africa/Kigali"), created_by=self.user, modified_by=self.user
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

        # can't create event relative to non-date field
        with self.assertRaises(ValueError):
            CampaignEvent.create_flow_event(
                self.org,
                self.admin,
                campaign,
                offset=3,
                unit="D",
                flow=self.reminder_flow,
                relative_to=gender,
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
            self.org, self.admin, campaign, offset=3, unit="D", flow=self.reminder_flow, relative_to=created_on
        )

        self.assertEqual(campaign_event.campaign_id, campaign.id)
        self.assertEqual(campaign_event.offset, 3)
        self.assertEqual(campaign_event.unit, "D")
        self.assertEqual(campaign_event.relative_to_id, created_on.id)
        self.assertEqual(campaign_event.flow_id, self.reminder_flow.id)
        self.assertEqual(campaign_event.event_type, "F")
        self.assertEqual(campaign_event.message, None)
        self.assertEqual(campaign_event.delivery_hour, -1)

    def test_create_message_event(self):
        gender = self.create_field("gender", "Gender", value_type="T")
        created_on = self.org.fields.get(key="created_on")
        campaign = Campaign.create(self.org, self.admin, "Planting Reminders", self.farmers)

        new_org = Org.objects.create(
            name="Temba New", timezone=ZoneInfo("Africa/Kigali"), created_by=self.user, modified_by=self.user
        )

        with self.assertRaises(AssertionError):
            CampaignEvent.create_message_event(
                new_org,
                self.admin,
                campaign,
                offset=3,
                unit="D",
                message="oy, pancake man, come back",
                relative_to=self.planting_date,
            )

        # can't create event relative to non-date field
        with self.assertRaises(ValueError):
            CampaignEvent.create_message_event(
                self.org,
                self.admin,
                campaign,
                offset=3,
                unit="D",
                message="oy, pancake man, come back",
                relative_to=gender,
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
        self.assertEqual(campaign_event.message, {"eng": "oy, pancake man, come back"})
        self.assertEqual(campaign_event.delivery_hour, -1)

        campaign_event = CampaignEvent.create_message_event(
            self.org,
            self.admin,
            campaign,
            offset=3,
            unit="D",
            message="oy, pancake man, come back",
            relative_to=created_on,
        )

        self.assertEqual(campaign_event.campaign_id, campaign.id)
        self.assertEqual(campaign_event.offset, 3)
        self.assertEqual(campaign_event.unit, "D")
        self.assertEqual(campaign_event.relative_to_id, created_on.id)
        self.assertIsNotNone(campaign_event.flow_id)
        self.assertEqual(campaign_event.event_type, "M")
        self.assertEqual(campaign_event.message, {"eng": "oy, pancake man, come back"})
        self.assertEqual(campaign_event.delivery_hour, -1)
        self.assertEqual(campaign_event.flow.flow_type, Flow.TYPE_BACKGROUND)
