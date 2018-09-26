from datetime import timedelta

import pytz

from django.conf import settings
from django.core.exceptions import ValidationError
from django.urls import reverse
from django.utils import timezone

from temba.campaigns.tasks import check_campaigns_task
from temba.contacts.models import Contact, ContactField, ContactGroup, ImportTask
from temba.flows.models import ActionSet, Flow, FlowRevision, FlowRun, FlowStart, RuleSet
from temba.msgs.models import Msg
from temba.orgs.models import Language, Org, get_current_export_version
from temba.tests import ESMockWithScroll, TembaTest, also_in_flowserver
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

        self.create_secondary_org()
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

        flow_json = self.get_flow_json("call_me_maybe")["definition"]
        flow = Flow.create_instance(
            dict(
                name="Call Me Maybe",
                org=self.org,
                is_system=True,
                created_by=self.admin,
                modified_by=self.admin,
                saved_by=self.admin,
                version_number=3,
            )
        )

        FlowRevision.create_instance(
            dict(
                flow=flow,
                definition=flow_json,
                spec_version=3,
                revision=1,
                created_by=self.admin,
                modified_by=self.admin,
            )
        )

        event4 = CampaignEvent.create_flow_event(
            self.org, self.admin, campaign, self.planting_date, offset=2, unit="W", flow=flow, delivery_hour="5"
        )

        self.assertEqual(flow.version_number, 3)
        self.assertEqual(campaign.get_sorted_events(), [event2, event1, event3, event4])
        flow.refresh_from_db()
        self.assertNotEqual(flow.version_number, 3)
        self.assertEqual(flow.version_number, get_current_export_version())

    @also_in_flowserver
    def test_message_event(self, in_flowserver):
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

        # update the planting date for our contact
        self.farmer1.set_field(self.user, "planting_date", "1/10/2020 10:00")

        fire = EventFire.objects.get()

        # change our fire date to sometime in the past so it gets triggered
        fire.scheduled = timezone.now() - timedelta(hours=1)
        fire.save(update_fields=("scheduled",))

        # schedule our events to fire
        check_campaigns_task()

        run = FlowRun.objects.get()
        msg = run.get_messages().get()

        self.assertEqual(msg.text, "Hi ROB JASPER don't forget to plant on 01-10-2020 10:00")

        if in_flowserver:
            session_json = run.session.output
            self.assertEqual(session_json["trigger"]["type"], "campaign")
            self.assertEqual(
                session_json["trigger"]["event"],
                {"uuid": str(event.uuid), "campaign": {"uuid": str(campaign.uuid), "name": "Planting Reminders"}},
            )

        # deleting a message campaign event should clean up the flow/runs/starts created by it
        event.release()

        self.assertEqual(FlowRun.objects.count(), 0)
        self.assertEqual(FlowStart.objects.count(), 0)
        self.assertEqual(Flow.objects.filter(is_system=True, is_active=True).count(), 0)

    def test_trim_event_fires(self):
        campaign = Campaign.create(self.org, self.admin, "Planting Reminders", self.farmers)

        # create a reminder for our first planting event
        event = CampaignEvent.create_flow_event(
            self.org, self.admin, campaign, relative_to=self.planting_date, offset=3, unit="D", flow=self.reminder_flow
        )

        trimDate = timezone.now() - timedelta(days=settings.EVENT_FIRE_TRIM_DAYS + 1)

        # manually create two event fires
        EventFire.objects.create(event=event, contact=self.farmer1, scheduled=trimDate, fired=trimDate)
        e2 = EventFire.objects.create(
            event=event, contact=self.farmer1, scheduled=timezone.now(), fired=timezone.now()
        )

        # trim our events
        trim_event_fires_task()

        # should now have only one event, e2
        e = EventFire.objects.get()
        self.assertEqual(e.id, e2.id)

    def test_events_batch_fire(self):
        campaign = Campaign.create(self.org, self.admin, "Planting Reminders", self.farmers)

        # create a reminder for our first planting event
        event = CampaignEvent.create_flow_event(
            self.org, self.admin, campaign, relative_to=self.planting_date, offset=3, unit="D", flow=self.reminder_flow
        )

        self.assertEqual(0, EventFire.objects.all().count())
        self.farmer1.set_field(self.user, "planting_date", "10-05-2020 12:30:10")
        self.farmer2.set_field(self.user, "planting_date", "15-05-2020 12:30:10")

        # now we have event fires accordingly
        self.assertEqual(2, EventFire.objects.all().count())

        self.assertEqual(0, FlowStart.objects.all().count())

        first_event_fire = EventFire.objects.all().first()
        self.assertFalse(EventFire.objects.get(id=first_event_fire.id).fired)

        # no flow start if we start just one contact
        EventFire.batch_fire([first_event_fire], self.reminder_flow)

        self.assertEqual(0, FlowStart.objects.all().count())
        self.assertTrue(EventFire.objects.get(id=first_event_fire.id).fired)

        # should have a flowstart object is we start many event fires
        EventFire.batch_fire(list(EventFire.objects.all()), self.reminder_flow)
        self.assertEqual(1, FlowStart.objects.all().count())
        self.assertEqual(0, EventFire.objects.filter(fired=None).count())

        # fires should delete when we release a contact
        self.assertEqual(1, EventFire.objects.filter(contact=self.farmer1).count())
        self.farmer1.release(self.admin)
        self.assertEqual(0, EventFire.objects.filter(contact=self.farmer1).count())

        # but our campaign and group remains intact
        Campaign.objects.get(id=campaign.id)
        ContactGroup.user_groups.get(id=self.farmers.id)

        # deleting this event shouldn't delete the runs or starts
        event.release()

        self.assertEqual(FlowRun.objects.count(), 1)
        self.assertEqual(FlowStart.objects.all().count(), 1)

    def test_message_event_editing(self):
        # update the planting date for our contacts
        self.farmer1.set_field(self.user, "planting_date", "1/10/2020")

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
        self.org.save()

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
        self.org.save()

        response = self.client.get(url)
        self.assertIn("base", response.context["form"].fields)
        self.assertIn("spa", response.context["form"].fields)
        self.assertIn("ace", response.context["form"].fields)

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
        )
        response = self.client.post(
            reverse("campaigns.campaignevent_create") + "?campaign=%d" % campaign.pk, post_data
        )

        # should be redirected back to our campaign read page
        self.assertRedirect(response, reverse("campaigns.campaign_read", args=[campaign.pk]))

        # should have one event, which created a corresponding flow
        event = CampaignEvent.objects.get()
        flow = event.flow
        self.assertTrue(flow.is_system)

        entry = ActionSet.objects.filter(uuid=flow.entry_uuid)[0]
        msg = entry.get_actions()[0].msg
        self.assertEqual(flow.base_language, "base")
        self.assertEqual(msg, {"base": "This is my message", "spa": "hola", "ace": ""})
        self.assertFalse(RuleSet.objects.filter(flow=flow))

        self.assertEqual(-1, event.offset)
        self.assertEqual(13, event.delivery_hour)
        self.assertEqual("W", event.unit)
        self.assertEqual("M", event.event_type)

        self.assertEqual(event.get_message(contact=self.farmer1), "This is my message")
        self.assertEqual(event.get_message(contact=self.farmer2), "hola")
        self.assertEqual(event.get_message(), "This is my message")

        url = reverse("campaigns.campaignevent_update", args=[event.id])
        response = self.client.get(url)
        self.assertEqual("This is my message", response.context["form"].fields["base"].initial)
        self.assertEqual("hola", response.context["form"].fields["spa"].initial)
        self.assertEqual("", response.context["form"].fields["ace"].initial)

        # 'Created On' system field must be selectable in the form
        contact_fields = [field.key for field in response.context["form"].fields["relative_to"].queryset]
        self.assertEqual(contact_fields, ["created_on", "planting_date"])

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
        post_data = dict(
            relative_to=self.planting_date.pk,
            event_type="M",
            base="Required",
            spa="This is my spanish @contact.planting_date",
            ace="",
            direction="B",
            offset=1,
            unit="W",
            flow_to_start="",
            delivery_hour=13,
        )
        response = self.client.post(url, post_data)
        self.assertEqual(302, response.status_code)
        flow.refresh_from_db()

        # we should retain 'base' as our base language
        self.assertEqual("base", flow.base_language)

        # now we can remove our primary language
        self.org.primary_language = None
        self.org.save()

        # and still get the same settings, (it should use the base of the flow instead of just base here)
        response = self.client.get(url)
        self.assertIn("base", response.context["form"].fields)
        self.assertEqual("This is my spanish @contact.planting_date", response.context["form"].fields["spa"].initial)
        self.assertEqual("", response.context["form"].fields["ace"].initial)

        # our single message flow should have a dependency on planting_date
        event.flow.refresh_from_db()
        self.assertEqual(1, event.flow.field_dependencies.all().count())

        # delete the event
        self.client.post(reverse("campaigns.campaignevent_delete", args=[event.pk]), dict())
        self.assertFalse(CampaignEvent.objects.filter(id=event.id).exists())

        # our single message flow should be released and take its dependencies with it
        self.assertEqual(0, event.flow.field_dependencies.all().count())

    def test_views(self):
        # update the planting date for our contacts
        self.farmer1.set_field(self.user, "planting_date", "1/10/2020")

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
        campaign = Campaign.objects.get()
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
        self.assertEqual(contact_fields, ["created_on", "planting_date"])

        post_data = dict(
            relative_to=self.planting_date.pk,
            delivery_hour=-1,
            base="",
            direction="A",
            offset=2,
            unit="D",
            event_type="M",
            flow_to_start=self.reminder_flow.pk,
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
        )
        response = self.client.post(
            reverse("campaigns.campaignevent_create") + "?campaign=%d" % campaign.pk, post_data
        )

        # should be redirected back to our campaign read page
        self.assertRedirect(response, reverse("campaigns.campaign_read", args=[campaign.pk]))

        # should now have a campaign event
        event = CampaignEvent.objects.get()
        self.assertEqual(self.reminder_flow, event.flow)
        self.assertEqual(self.planting_date, event.relative_to)
        self.assertEqual(2, event.offset)

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
        )
        response = self.client.post(reverse("campaigns.campaignevent_update", args=[event.pk]), post_data)

        # should be redirected back to our campaign event read page
        self.assertRedirect(response, reverse("campaigns.campaignevent_read", args=[event.pk]))

        # should now have update the campaign event
        event = CampaignEvent.objects.get()
        self.assertEqual(self.reminder_flow, event.flow)
        self.assertEqual(self.planting_date, event.relative_to)
        self.assertEqual(1, event.offset)

        # should also event fires rescheduled for our contacts
        fire = EventFire.objects.get()
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
        )
        self.client.post(reverse("campaigns.campaignevent_create") + "?campaign=%d" % campaign.pk, post_data)

        # trying to archive our flow should fail since it belongs to a campaign
        post_data = dict(action="archive", objects=[self.reminder_flow.pk])
        response = self.client.post(reverse("flows.flow_list"), post_data)
        self.reminder_flow.refresh_from_db()
        self.assertFalse(self.reminder_flow.is_archived)
        self.assertEqual(
            "Reminder Flow is used inside a campaign. To archive it, first remove it from your campaigns.",
            response.get("Temba-Toast"),
        )

        post_data = dict(action="archive", objects=[self.reminder_flow.pk, self.reminder2_flow.pk])
        response = self.client.post(reverse("flows.flow_list"), post_data)
        self.assertEqual(
            "Planting Reminder and Reminder Flow are used inside a campaign. To archive them, first remove them from your campaigns.",
            response.get("Temba-Toast"),
        )

        for e in CampaignEvent.objects.filter(flow=self.reminder2_flow.pk):
            e.release()

        # archive the campaign
        post_data = dict(action="archive", objects=campaign.pk)
        self.client.post(reverse("campaigns.campaign_list"), post_data)
        response = self.client.get(reverse("campaigns.campaign_list"))
        self.assertNotContains(response, "Planting Reminders")

        # should have no event fires
        self.assertFalse(EventFire.objects.all())

        # restore the campaign
        post_data = dict(action="restore", objects=campaign.pk)
        self.client.post(reverse("campaigns.campaign_archived"), post_data)

        # EventFire should be back
        self.assertTrue(EventFire.objects.all())

        # set a planting date on our other farmer
        self.farmer2.set_field(self.user, "planting_date", "1/6/2022")

        # should have two fire events now
        fires = EventFire.objects.all()
        self.assertEqual(2, len(fires))

        fire = fires[0]
        self.assertEqual(2, fire.scheduled.day)
        self.assertEqual(10, fire.scheduled.month)
        self.assertEqual(2020, fire.scheduled.year)
        self.assertEqual(event, fire.event)

        fire = fires[1]
        self.assertEqual(2, fire.scheduled.day)
        self.assertEqual(6, fire.scheduled.month)
        self.assertEqual(2022, fire.scheduled.year)
        self.assertEqual(event, fire.event)

        # setting a planting date on our outside contact has no effect
        self.nonfarmer.set_field(self.user, "planting_date", "1/7/2025")
        self.assertEqual(2, EventFire.objects.all().count())

        # remove one of the farmers from the group
        response = self.client.post(
            reverse("contacts.contact_read", args=[self.farmer1.uuid]),
            dict(contact=self.farmer1.pk, group=self.farmers.pk),
        )
        self.assertEqual(200, response.status_code)

        # should only be one event now (on farmer 2)
        fire = EventFire.objects.get()
        self.assertEqual(2, fire.scheduled.day)
        self.assertEqual(6, fire.scheduled.month)
        self.assertEqual(2022, fire.scheduled.year)
        self.assertEqual(event, fire.event)

        # but if we add him back in, should be updated
        post_data = dict(name=self.farmer1.name, groups=[self.farmers.id], __urn__tel=self.farmer1.get_urn("tel").path)

        planting_date_field = ContactField.get_by_key(self.org, "planting_date")
        self.client.post(reverse("contacts.contact_update", args=[self.farmer1.id]), post_data)
        response = self.client.post(
            reverse("contacts.contact_update_fields", args=[self.farmer1.id]),
            dict(contact_field=planting_date_field.id, field_value="4/8/2020"),
        )
        self.assertRedirect(response, reverse("contacts.contact_read", args=[self.farmer1.uuid]))

        fires = EventFire.objects.all()
        self.assertEqual(2, len(fires))

        fire = fires[0]
        self.assertEqual(5, fire.scheduled.day)
        self.assertEqual(8, fire.scheduled.month)
        self.assertEqual(2020, fire.scheduled.year)
        self.assertEqual(event, fire.event)
        self.assertEqual(str(fire), "%s - %s" % (fire.event, fire.contact))

        event = CampaignEvent.objects.filter(is_active=True).first()

        # get the detail page of the event
        response = self.client.get(reverse("campaigns.campaignevent_read", args=[event.pk]))
        self.assertEqual(200, response.status_code)
        self.assertEqual(response.context["scheduled_event_fires_count"], 0)
        self.assertEqual(len(response.context["scheduled_event_fires"]), 2)

        # delete the event
        self.client.post(reverse("campaigns.campaignevent_delete", args=[event.pk]), dict())
        self.assertFalse(CampaignEvent.objects.all().exists())
        response = self.client.get(reverse("campaigns.campaign_read", args=[campaign.pk]))
        self.assertNotContains(response, "Color Flow")

    def test_eventfire_get_relative_to_value(self):
        campaign = Campaign.create(self.org, self.admin, "Planting Reminders", self.farmers)
        field_created_on = self.org.contactfields.get(key="created_on")

        # create a reminder for our first planting event
        event = CampaignEvent.create_flow_event(
            self.org, self.admin, campaign, relative_to=self.planting_date, offset=3, unit="D", flow=self.reminder_flow
        )
        self.farmer1.set_field(self.admin, "planting_date", timezone.now())

        trimDate = timezone.now() - timedelta(days=settings.EVENT_FIRE_TRIM_DAYS + 1)
        ev = EventFire.objects.create(event=event, contact=self.farmer1, scheduled=trimDate, fired=trimDate)
        self.assertIsNotNone(ev.get_relative_to_value())

        event2 = CampaignEvent.create_flow_event(
            self.org, self.admin, campaign, relative_to=field_created_on, offset=3, unit="D", flow=self.reminder_flow
        )

        trimDate = timezone.now() - timedelta(days=settings.EVENT_FIRE_TRIM_DAYS + 1)
        ev2 = EventFire.objects.create(event=event2, contact=self.farmer1, scheduled=trimDate, fired=trimDate)
        self.assertIsNotNone(ev2.get_relative_to_value())

    def test_campaignevent_calculate_scheduled_fire(self):
        planting_date = timezone.now()
        field_created_on = self.org.contactfields.get(key="created_on")

        self.farmer1.set_field(self.admin, "planting_date", planting_date)

        campaign = Campaign.create(self.org, self.admin, "Planting Reminders", self.farmers)

        # create a reminder for our first planting event
        event = CampaignEvent.create_flow_event(
            self.org, self.admin, campaign, relative_to=self.planting_date, offset=3, unit="D", flow=self.reminder_flow
        )

        expected_result = (
            (planting_date + timedelta(days=3)).replace(second=0, microsecond=0).astimezone(self.org.timezone)
        )
        self.assertEqual(event.calculate_scheduled_fire(self.farmer1), expected_result)

        # create a reminder for our first planting event
        event = CampaignEvent.create_flow_event(
            self.org, self.admin, campaign, relative_to=field_created_on, offset=5, unit="D", flow=self.reminder_flow
        )

        expected_result = (
            (self.farmer1.created_on + timedelta(days=5))
            .replace(second=0, microsecond=0)
            .astimezone(self.org.timezone)
        )
        self.assertEqual(event.calculate_scheduled_fire(self.farmer1), expected_result)

    def test_deleting_reimport_contact_groups(self):
        campaign = Campaign.create(self.org, self.admin, "Planting Reminders", self.farmers)

        # create a reminder for our first planting event
        planting_reminder = CampaignEvent.create_flow_event(
            self.org, self.admin, campaign, relative_to=self.planting_date, offset=3, unit="D", flow=self.reminder_flow
        )

        self.assertEqual(0, EventFire.objects.all().count())
        self.farmer1.set_field(self.user, "planting_date", "10-05-2020 12:30:10")
        self.farmer2.set_field(self.user, "planting_date", "15-05-2020 12:30:10")

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
            org_id=self.org.id, timezone=str(self.org.timezone), extra_fields=extra_fields, original_filename=filename
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
        self.farmers = ContactGroup.user_groups.get(name="Farmers")
        self.login(self.admin)
        post_data = dict(name="Planting Reminders", group=self.farmers.pk)
        self.client.post(reverse("campaigns.campaign_update", args=[campaign.pk]), post_data)

        # should have two fresh new fires
        self.assertEqual(2, EventFire.objects.all().count())

        # check their new planting dates
        scheduled = EventFire.objects.get(contact=self.farmer1, event=planting_reminder).scheduled
        self.assertEqual("13-8-2020", "%s-%s-%s" % (scheduled.day, scheduled.month, scheduled.year))

        # farmer two fire
        scheduled = EventFire.objects.get(contact=self.farmer2, event=planting_reminder).scheduled
        self.assertEqual("18-8-2020", "%s-%s-%s" % (scheduled.day, scheduled.month, scheduled.year))

        # give our non farmer a planting date
        self.nonfarmer.set_field(self.user, "planting_date", "20-05-2020 12:30:10")

        # now update to the non-farmer group
        self.nonfarmers = self.create_group("Not Farmers", [self.nonfarmer])
        post_data = dict(name="Planting Reminders", group=self.nonfarmers.pk)
        self.client.post(reverse("campaigns.campaign_update", args=[campaign.pk]), post_data)

        # only one fire for the non-farmer the previous two should be deleted by the group change
        self.assertEqual(1, EventFire.objects.all().count())
        self.assertEqual(1, EventFire.objects.filter(contact=self.nonfarmer).count())

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
        self.farmer1.set_field(self.user, "planting_date", "03-11-2029 12:30:00")
        EventFire.update_campaign_events(campaign)

        # try changing our field type to something non-date, should throw
        with self.assertRaises(ValueError):
            ContactField.get_or_create(self.org, self.admin, "planting_date", value_type=Value.TYPE_TEXT)

        # we should be scheduled to go off on the 5th at 12:30:10 Eastern
        fire = EventFire.objects.get()
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
        self.farmer1.set_field(self.user, "planting_date", "10-03-2029 02:30:00")
        EventFire.update_campaign_events(campaign)

        fire = EventFire.objects.get()
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
        event.release()

        # should be able to change our field type now
        ContactField.get_or_create(self.org, self.admin, "planting_date", value_type=Value.TYPE_TEXT)

    def test_scheduling(self):
        campaign = Campaign.create(self.org, self.admin, "Planting Reminders", self.farmers)
        self.assertEqual("Planting Reminders", str(campaign))

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

        self.assertEqual("Planting Date == 0 -> Reminder Flow", str(planting_reminder))

        # schedule our reminders
        EventFire.update_campaign_events(campaign)

        # we should haven't any event fires created, since neither of our farmers have a planting date
        self.assertEqual(0, EventFire.objects.all().count())

        # ok, set a planting date on one of our contacts
        self.farmer1.set_field(self.user, "planting_date", "05-10-2020 12:30:10")

        # update our campaign events
        EventFire.update_campaign_events(campaign)

        # should have one event now
        fire = EventFire.objects.get()
        self.assertEqual(5, fire.scheduled.day)
        self.assertEqual(10, fire.scheduled.month)
        self.assertEqual(2020, fire.scheduled.year)

        # account for timezone difference, our org is in UTC+2
        self.assertEqual(17 - 2, fire.scheduled.hour)

        self.assertEqual(self.farmer1, fire.contact)
        self.assertEqual(planting_reminder, fire.event)

        self.assertIsNone(fire.fired)

        # change the date of our date
        self.farmer1.set_field(self.user, "planting_date", "06-10-2020 12:30:10")

        EventFire.update_campaign_events_for_contact(campaign, self.farmer1)
        fire = EventFire.objects.get()
        self.assertEqual(6, fire.scheduled.day)
        self.assertEqual(10, fire.scheduled.month)
        self.assertEqual(2020, fire.scheduled.year)
        self.assertEqual(self.farmer1, fire.contact)
        self.assertEqual(planting_reminder, fire.event)

        # set it to something invalid
        self.farmer1.set_field(self.user, "planting_date", "what?")
        EventFire.update_campaign_events_for_contact(campaign, self.farmer1)
        self.assertFalse(EventFire.objects.all())

        # now something valid again
        self.farmer1.set_field(self.user, "planting_date", "07-10-2020 12:30:10")

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

        # should have two events now, ordered by date
        events = EventFire.objects.all()

        self.assertEqual(planting_reminder, events[0].event)
        self.assertEqual(7, events[0].scheduled.day)

        self.assertEqual(planting_reminder2, events[1].event)
        self.assertEqual(8, events[1].scheduled.day)

        # mark one of the events as inactive
        planting_reminder2.is_active = False
        planting_reminder2.save()

        # update the campaign
        EventFire.update_campaign_events(campaign)

        # back to only one event
        event = EventFire.objects.get()
        self.assertEqual(planting_reminder, event.event)
        self.assertEqual(7, event.scheduled.day)

        # update our date
        self.farmer1.set_field(self.user, "planting_date", "09-10-2020 12:30")

        # should have updated
        event = EventFire.objects.get()
        self.assertEqual(planting_reminder, event.event)
        self.assertEqual(9, event.scheduled.day)

        # let's remove our contact field
        ContactField.hide_field(self.org, self.user, "planting_date")

        # shouldn't have anything scheduled
        self.assertFalse(EventFire.objects.all())

        # add it back in
        ContactField.get_or_create(self.org, self.admin, "planting_date", "planting Date")

        # should be back!
        event = EventFire.objects.get()
        self.assertEqual(planting_reminder, event.event)
        self.assertEqual(9, event.scheduled.day)

        # change our fire date to sometime in the past so it gets triggered
        event.scheduled = timezone.now() - timedelta(hours=1)
        event.save()

        # schedule our events to fire
        check_campaigns_task()

        # should have one flow run now
        run = FlowRun.objects.get()
        self.assertEqual(event.contact, run.contact)

        # deleting this event shouldn't delete the runs
        event.release()

        self.assertEqual(FlowRun.objects.count(), 1)

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

    def test_with_dynamic_group(self):
        # create a campaign on a dynamic group
        self.create_field("gender", "Gender")

        with ESMockWithScroll():
            women = self.create_group("Women", query='gender="F"')

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
        anna = self.create_contact("Anna", "+250788333333")
        anna.set_field(self.admin, "planting_date", "09-10-2020 12:30")

        # no contacts in our dynamic group yet, so no event fires
        self.assertEqual(EventFire.objects.filter(event=event).count(), 0)

        # update contact so that they become part of the dynamic group
        anna.set_field(self.admin, "gender", "f")
        self.assertEqual(set(women.contacts.all()), {anna})

        # and who should now have an event fire for our campaign event
        self.assertEqual(EventFire.objects.filter(event=event, contact=anna).count(), 1)

        # change dynamic group query so anna is removed
        with ESMockWithScroll():
            women.update_query("gender=FEMALE")

        self.assertEqual(set(women.contacts.all()), set())

        # check that her event fire is now removed
        self.assertEqual(EventFire.objects.filter(event=event, contact=anna).count(), 0)

        # but if query is reverted, her event fire should be recreated
        mock_es_data = [
            {
                "_type": "_doc",
                "_index": "dummy_index",
                "_source": {"id": anna.id, "modified_on": anna.modified_on.isoformat()},
            }
        ]
        with ESMockWithScroll(data=mock_es_data):
            women.update_query("gender=F")
        self.assertEqual(set(women.contacts.all()), {anna})

        # check that her event fire is now removed
        self.assertEqual(EventFire.objects.filter(event=event, contact=anna).count(), 1)

    def test_model_as_json(self):
        field_created_on = self.org.contactfields.get(key="created_on")
        campaign = Campaign.create(self.org, self.admin, "Planting Reminders", self.farmers)

        # create a reminder for our first planting event
        planting_reminder = CampaignEvent.create_flow_event(
            self.org, self.admin, campaign, relative_to=self.planting_date, offset=3, unit="D", flow=self.reminder_flow
        )

        self.assertEqual(
            campaign.as_json(),
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
            campaign2.as_json(),
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
            campaign3.as_json(),
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
            country=self.country,
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
            country=self.country,
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
