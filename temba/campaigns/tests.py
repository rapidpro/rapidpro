from datetime import timedelta

import pytz

from django.conf import settings
from django.core.exceptions import ValidationError
from django.urls import reverse
from django.utils import timezone

from temba.contacts.models import ContactField
from temba.flows.models import Flow, FlowRevision
from temba.msgs.models import Msg
from temba.orgs.models import Language, Org
from temba.tests import CRUDLTestMixin, TembaTest, matchers, mock_mailroom

from .models import Campaign, CampaignEvent, EventFire
from .tasks import trim_event_fires_task


class CampaignTest(TembaTest):
    def setUp(self):
        super().setUp()

        self.farmer1 = self.create_contact("Rob Jasper", phone="+250788111111")
        self.farmer2 = self.create_contact("Mike Gordon", phone="+250788222222", language="spa")

        self.nonfarmer = self.create_contact("Trey Anastasio", phone="+250788333333")
        self.farmers = self.create_group("Farmers", [self.farmer1, self.farmer2])

        self.reminder_flow = self.create_flow(name="Reminder Flow")
        self.reminder2_flow = self.create_flow(name="Planting Reminder")

        # create a voice flow to make sure they work too, not a proper voice flow but
        # sufficient for assuring these flow types show up where they should
        self.voice_flow = self.create_flow(name="IVR flow", flow_type="V")

        # create a contact field for our planting date
        self.planting_date = ContactField.get_or_create(
            self.org, self.admin, "planting_date", "Planting Date", value_type=ContactField.TYPE_DATETIME
        )

    @mock_mailroom
    def test_model(self, mr_mocks):
        campaign = Campaign.create(self.org, self.admin, Campaign.get_unique_name(self.org, "Reminders"), self.farmers)

        flow = self.create_flow()

        event1 = CampaignEvent.create_flow_event(
            self.org, self.admin, campaign, self.planting_date, offset=1, unit="W", flow=flow, delivery_hour=13
        )
        event2 = CampaignEvent.create_message_event(
            self.org, self.admin, campaign, self.planting_date, offset=3, unit="D", message="Hello", delivery_hour=9
        )

        self.assertEqual("Reminders", campaign.name)
        self.assertEqual(f'Campaign[uuid={campaign.uuid}, name="Reminders"]', str(campaign))
        self.assertEqual(f'Event[relative_to=planting_date, offset=1, flow="Color Flow"]', str(event1))
        self.assertEqual([event1, event2], list(campaign.get_events()))

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
        self.assertEqual({"base": "Hello"}, new_event2.message)

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
        self.assertEqual(flow.version_number, Flow.CURRENT_SPEC_VERSION)

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
            {
                "uuid": str(event.flow.uuid),
                "name": event.flow.name,
                "spec_version": "13.0.0",
                "revision": 1,
                "language": "eng",
                "type": "messaging",
                "expire_after_minutes": 10080,
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

        flow_json = event.flow.get_definition()
        action_uuid = flow_json["nodes"][0]["actions"][0]["uuid"]

        self.assertEqual(
            {
                "uuid": str(event.flow.uuid),
                "name": f"Single Message ({event.id})",
                "spec_version": "13.0.0",
                "revision": 1,
                "expire_after_minutes": 10080,
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
            flow_json,
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
        self.set_contact_field(self.farmer1, "planting_date", "1/10/2020")

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
        self.set_contact_field(self.farmer2, "planting_date", "1/6/2022")

        # should have an event fire now
        fires = EventFire.objects.filter(event__is_active=True)
        self.assertEqual(1, len(fires))

        # setting a planting date on our outside contact has no effect
        self.set_contact_field(self.nonfarmer, "planting_date", "1/7/2025")
        self.assertEqual(1, EventFire.objects.filter(event__is_active=True).count())

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
        self.assertEqual(len(response.context["scheduled_event_fires"]), 1)

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
        self.assertContains(response, "Archived", count=0)

        gear_links = response.context["view"].get_gear_links()
        self.assertListEqual([gl["title"] for gl in gear_links], ["Add Event", "Export", "Edit", "Archive"])

        # archive the campaign
        campaign.is_archived = True
        campaign.save()

        response = self.client.get(reverse("campaigns.campaign_read", args=[campaign.pk]))

        # page title and main content title should contain (Archived)
        self.assertContains(response, "Perform the rain dance", count=2)
        self.assertContains(response, "Archived", count=2)

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

        # page title and main content title should NOT contain Archived
        self.assertContains(response, "Perform the rain dance", count=1)
        self.assertContains(response, "Archived", count=0)

        gear_links = response.context["view"].get_gear_links()
        self.assertListEqual([gl["title"] for gl in gear_links], ["Edit", "Delete"])

        # archive the campaign
        campaign.is_archived = True
        campaign.save()

        response = self.client.get(reverse("campaigns.campaignevent_read", args=[event.pk]))

        # page title and main content title should contain Archived
        self.assertContains(response, "Perform the rain dance", count=1)
        self.assertContains(response, "Archived", count=1)

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

        # create event relative to last_seen_on
        event3 = CampaignEvent.create_flow_event(
            self.org, self.admin, campaign, relative_to=last_seen_on, offset=3, unit="D", flow=self.reminder_flow
        )

        trim_date = timezone.now() - timedelta(days=settings.EVENT_FIRE_TRIM_DAYS + 1)
        ev3 = EventFire.objects.create(event=event3, contact=self.farmer1, scheduled=trim_date, fired=trim_date)
        self.assertIsNone(ev3.get_relative_to_value())

        # give contact a last seen on value
        self.farmer1.last_seen_on = timezone.now()
        self.farmer1.save(update_fields=("last_seen_on",))

        ev4 = EventFire.objects.create(event=event3, contact=self.farmer1, scheduled=trim_date, fired=trim_date)
        self.assertIsNotNone(ev4.get_relative_to_value())

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
        event.release()

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


class CampaignCRUDLTest(TembaTest, CRUDLTestMixin):
    def create_campaign(self, org, name, group):
        user = org.get_user()
        registered = self.create_field("registered", "Registered", value_type="D", org=org)
        flow = self.create_flow(org=org)
        campaign = Campaign.create(org, user, name, group)
        CampaignEvent.create_flow_event(
            org, user, campaign, registered, offset=1, unit="W", flow=flow, delivery_hour="13"
        )
        return campaign

    def test_read(self):
        group = self.create_group("Reporters", contacts=[])
        campaign = self.create_campaign(self.org, "Welcomes", group)

        read_url = reverse("campaigns.campaign_read", args=[campaign.id])

        response = self.assertReadFetch(read_url, allow_viewers=True, allow_editors=True, context_object=campaign)
        self.assertContains(response, "Welcomes")
        self.assertContains(response, "Registered")

    def test_archive_and_activate(self):
        group = self.create_group("Reporters", contacts=[])
        campaign = self.create_campaign(self.org, "Welcomes", group)
        other_org_group = self.create_group("Reporters", contacts=[], org=self.org2)
        other_org_campaign = self.create_campaign(self.org2, "Welcomes", other_org_group)

        archive_url = reverse("campaigns.campaign_archive", args=[campaign.id])

        # can't archive campaign if not logged in
        response = self.client.post(archive_url)
        self.assertLoginRedirect(response)

        self.login(self.admin)

        response = self.client.post(archive_url)
        self.assertEqual(302, response.status_code)

        campaign.refresh_from_db()
        self.assertTrue(campaign.is_archived)

        # activate that archve
        response = self.client.post(reverse("campaigns.campaign_activate", args=[campaign.id]))
        self.assertEqual(302, response.status_code)

        campaign.refresh_from_db()
        self.assertFalse(campaign.is_archived)

        # can't archive campaign from other org
        response = self.client.post(reverse("campaigns.campaign_archive", args=[other_org_campaign.id]))
        self.assertEqual(404, response.status_code)

        # check object is unchanged
        other_org_campaign.refresh_from_db()
        self.assertFalse(other_org_campaign.is_archived)

    @mock_mailroom
    def test_update(self, mr_mocks):
        group1 = self.create_group("Reporters", contacts=[])
        group2 = self.create_group("Testers", contacts=[])
        campaign = self.create_campaign(self.org, "Welcomes", group1)

        update_url = reverse("campaigns.campaign_update", args=[campaign.id])

        self.assertUpdateFetch(update_url, allow_viewers=False, allow_editors=True, form_fields=["name", "group"])

        # try to submit with empty name
        self.assertUpdateSubmit(
            update_url,
            {"name": "", "group": group1.id},
            form_errors={"name": "This field is required."},
            object_unchanged=campaign,
        )

        # submit with valid name
        self.assertUpdateSubmit(update_url, {"name": "Greetings", "group": group1.id}, success_status=200)

        campaign.refresh_from_db()
        self.assertEqual("Greetings", campaign.name)
        self.assertEqual(group1, campaign.group)

        # group didn't change so shouldn't have queued any tasks to reschedule events
        self.assertEqual(0, len(mr_mocks.queued_batch_tasks))

        # submit with group change
        self.assertUpdateSubmit(update_url, {"name": "Greetings", "group": group2.id}, success_status=200)

        campaign.refresh_from_db()
        self.assertEqual("Greetings", campaign.name)
        self.assertEqual(group2, campaign.group)

        # should have a task queued to reschedule the campaign's event
        self.assertEqual(1, len(mr_mocks.queued_batch_tasks))
        self.assertEqual(
            {
                "type": "schedule_campaign_event",
                "org_id": self.org.id,
                "task": {"campaign_event_id": campaign.events.filter(is_active=True).get().id, "org_id": self.org.id},
                "queued_on": matchers.Datetime(),
            },
            mr_mocks.queued_batch_tasks[0],
        )

    def test_list(self):
        group = self.create_group("Reporters", contacts=[])
        campaign1 = self.create_campaign(self.org, "Welcomes", group)
        campaign2 = self.create_campaign(self.org, "Follow Ups", group)

        other_org_group = self.create_group("Reporters", contacts=[], org=self.org2)
        self.create_campaign(self.org2, "Welcomes", other_org_group)

        update_url = reverse("campaigns.campaign_list")

        self.assertListFetch(
            update_url, allow_viewers=True, allow_editors=True, context_objects=[campaign2, campaign1]
        )


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
