from __future__ import unicode_literals

from django.core.urlresolvers import reverse
from temba.contacts.models import ContactField
from temba.flows.models import FlowRun, Flow, RuleSet, ActionSet
from temba.tests import TembaTest
from temba.campaigns.tasks import check_campaigns_task
from .models import Campaign, CampaignEvent, EventFire
from django.utils import timezone
from datetime import timedelta


class CampaignTest(TembaTest):

    def setUp(self):
        super(CampaignTest, self).setUp()

        self.farmer1 = self.create_contact("Rob Jasper", "+250788111111")
        self.farmer2 = self.create_contact("Mike Gordon", "+250788222222")

        self.nonfarmer = self.create_contact("Trey Anastasio", "+250788333333")
        self.farmers = self.create_group("Farmers", [self.farmer1, self.farmer2])

        self.reminder_flow = self.create_flow()
        self.reminder2_flow = self.create_flow()

        # create a voice flow to make sure they work too, not a proper voice flow but
        # sufficient for assuring these flow types show up where they should
        self.voice_flow = self.create_flow()
        self.voice_flow.name = 'IVR flow'
        self.voice_flow.flow_type = 'V'
        self.voice_flow.save()

        # create a contact field for our planting date
        self.planting_date = ContactField.get_or_create(self.org, self.admin, 'planting_date', "Planting Date")

    def test_get_unique_name(self):
        campaign1 = Campaign.create(self.org, self.admin, Campaign.get_unique_name(self.org, "Reminders"), self.farmers)
        self.assertEqual(campaign1.name, "Reminders")

        campaign2 = Campaign.create(self.org, self.admin, Campaign.get_unique_name(self.org, "Reminders"), self.farmers)
        self.assertEqual(campaign2.name, "Reminders 2")

        campaign3 = Campaign.create(self.org, self.admin, Campaign.get_unique_name(self.org, "Reminders"), self.farmers)
        self.assertEqual(campaign3.name, "Reminders 3")

        self.create_secondary_org()
        self.assertEqual(Campaign.get_unique_name(self.org2, "Reminders"), "Reminders")  # different org

    def test_get_sorted_events(self):
        # create a campaign
        campaign = Campaign.create(self.org, self.user, "Planting Reminders", self.farmers)

        flow = self.create_flow()

        event1 = CampaignEvent.create_flow_event(self.org, self.admin, campaign, self.planting_date,
                                                 offset=1, unit='W', flow=flow, delivery_hour='13')
        event2 = CampaignEvent.create_flow_event(self.org, self.admin, campaign, self.planting_date,
                                                 offset=1, unit='W', flow=flow, delivery_hour='9')
        event3 = CampaignEvent.create_flow_event(self.org, self.admin, campaign, self.planting_date,
                                                 offset=2, unit='W', flow=flow, delivery_hour='1')

        self.assertEqual(campaign.get_sorted_events(), [event2, event1, event3])

    def test_message_event(self):
        # update the planting date for our contacts
        self.farmer1.set_field(self.user, 'planting_date', '1/10/2020')

        # ok log in as an org
        self.login(self.admin)

        # create a campaign
        campaign = Campaign.create(self.org, self.admin, "Planting Reminders", self.farmers)

        # go create an event that based on a message
        post_data = dict(relative_to=self.planting_date.pk, event_type='M', message="This is my message",
                         direction='B', offset=1, unit='W', flow_to_start='', delivery_hour=13)
        response = self.client.post(reverse('campaigns.campaignevent_create') + "?campaign=%d" % campaign.pk, post_data)

        # should be redirected back to our campaign read page
        self.assertRedirect(response, reverse('campaigns.campaign_read', args=[campaign.pk]))

        # should have one event, which created a corresponding flow
        event = CampaignEvent.objects.get()
        flow = event.flow
        self.assertEquals(Flow.MESSAGE, flow.flow_type)

        entry = ActionSet.objects.filter(uuid=flow.entry_uuid)[0]
        self.assertEquals("This is my message", entry.get_actions()[0].msg['base'])
        self.assertFalse(RuleSet.objects.filter(flow=flow))

        self.assertEquals(-1, event.offset)
        self.assertEquals(13, event.delivery_hour)
        self.assertEquals('W', event.unit)
        self.assertEquals('M', event.event_type)

    def test_views(self):
        # update the planting date for our contacts
        self.farmer1.set_field(self.user, 'planting_date', '1/10/2020')

        # don't log in, try to create a new campaign
        response = self.client.get(reverse('campaigns.campaign_create'))
        self.assertRedirect(response, reverse('users.user_login'))

        # ok log in as an org
        self.login(self.admin)

        # go to to the creation page
        response = self.client.get(reverse('campaigns.campaign_create'))
        self.assertEquals(200, response.status_code)

        post_data = dict(name="Planting Reminders", group=self.farmers.pk)
        response = self.client.post(reverse('campaigns.campaign_create'), post_data)

        # should redirect to read page for this campaign
        campaign = Campaign.objects.get()
        self.assertRedirect(response, reverse('campaigns.campaign_read', args=[campaign.pk]))

        # go to the list page, should be there as well
        response = self.client.get(reverse('campaigns.campaign_list'))
        self.assertContains(response, "Planting Reminders")

        # try searching for the campaign by group name
        response = self.client.get(reverse('campaigns.campaign_list') + "?search=farmers")
        self.assertContains(response, "Planting Reminders")

        # test no match
        response = self.client.get(reverse('campaigns.campaign_list') + "?search=factory")
        self.assertNotContains(response, "Planting Reminders")

        # archive a campaign
        post_data = dict(action='archive', objects=campaign.pk)
        self.client.post(reverse('campaigns.campaign_list'), post_data)
        response = self.client.get(reverse('campaigns.campaign_list'))
        self.assertNotContains(response, "Planting Reminders")

        # restore the campaign
        response = self.client.get(reverse('campaigns.campaign_archived'))
        self.assertContains(response, "Planting Reminders")
        post_data = dict(action='restore', objects=campaign.pk)
        self.client.post(reverse('campaigns.campaign_archived'), post_data)
        response = self.client.get(reverse('campaigns.campaign_archived'))
        self.assertNotContains(response, "Planting Reminders")
        response = self.client.get(reverse('campaigns.campaign_list'))
        self.assertContains(response, "Planting Reminders")

        # test viewers cannot use action archive or restore
        self.client.logout()

        # create a viewer
        self.viewer = self.create_user("Viewer")
        self.org.viewers.add(self.viewer)
        self.viewer.set_org(self.org)

        self.login(self.viewer)

        # go to the list page, should be there as well
        response = self.client.get(reverse('campaigns.campaign_list'))
        self.assertContains(response, "Planting Reminders")

        # cannot archive a campaign
        post_data = dict(action='archive', objects=campaign.pk)
        self.client.post(reverse('campaigns.campaign_list'), post_data)
        response = self.client.get(reverse('campaigns.campaign_list'))
        self.assertContains(response, "Planting Reminders")
        response = self.client.get(reverse('campaigns.campaign_archived'))
        self.assertNotContains(response, "Planting Reminders")

        self.client.logout()
        self.login(self.admin)

        # see if we can create a new event, should see both sms and voice flows
        response = self.client.get(reverse('campaigns.campaignevent_create') + "?campaign=%d" % campaign.pk)
        self.assertContains(response, self.reminder_flow.name)
        self.assertContains(response, self.voice_flow.name)
        self.assertEquals(200, response.status_code)

        post_data = dict(relative_to=self.planting_date.pk, delivery_hour=15, message='', direction='A', offset=2, unit='D', event_type='M', flow_to_start=self.reminder_flow.pk)
        response = self.client.post(reverse('campaigns.campaignevent_create') + "?campaign=%d" % campaign.pk, post_data)

        self.assertTrue(response.context['form'].errors)
        self.assertTrue('Please enter a message' in response.context['form'].errors['message'])

        post_data = dict(relative_to=self.planting_date.pk, delivery_hour=15, message='', direction='A', offset=2, unit='D', event_type='F')
        response = self.client.post(reverse('campaigns.campaignevent_create') + "?campaign=%d" % campaign.pk, post_data)

        self.assertTrue(response.context['form'].errors)
        self.assertTrue('Please select a flow' in response.context['form'].errors['flow_to_start'])

        post_data = dict(relative_to=self.planting_date.pk, delivery_hour=15, message='', direction='A', offset=2, unit='D', event_type='F', flow_to_start=self.reminder_flow.pk)
        response = self.client.post(reverse('campaigns.campaignevent_create') + "?campaign=%d" % campaign.pk, post_data)

        # should be redirected back to our campaign read page
        self.assertRedirect(response, reverse('campaigns.campaign_read', args=[campaign.pk]))

        # should now have a campaign event
        event = CampaignEvent.objects.get()
        self.assertEquals(self.reminder_flow, event.flow)
        self.assertEquals(self.planting_date, event.relative_to)
        self.assertEquals(2, event.offset)

        # read the campaign read page
        response = self.client.get(reverse('campaigns.campaign_read', args=[campaign.pk]))
        self.assertContains(response, "Color Flow")
        self.assertContains(response, "1")

        # should also have event fires scheduled for our contacts
        fire = EventFire.objects.get()
        self.assertEquals(13, fire.scheduled.hour)
        self.assertEquals(3, fire.scheduled.day)
        self.assertEquals(10, fire.scheduled.month)
        self.assertEquals(2020, fire.scheduled.year)
        self.assertEquals(event, fire.event)

        post_data = dict(relative_to=self.planting_date.pk, delivery_hour=15, message='', direction='A', offset=1, unit='D', event_type='F', flow_to_start=self.reminder_flow.pk)
        response = self.client.post(reverse('campaigns.campaignevent_update', args=[event.pk]), post_data)

        # should be redirected back to our campaign event read page
        self.assertRedirect(response, reverse('campaigns.campaignevent_read', args=[event.pk]))

        # should now have update the campaign event
        event = CampaignEvent.objects.get()
        self.assertEquals(self.reminder_flow, event.flow)
        self.assertEquals(self.planting_date, event.relative_to)
        self.assertEquals(1, event.offset)

        # should also event fires rescheduled for our contacts
        fire = EventFire.objects.get()
        self.assertEquals(13, fire.scheduled.hour)
        self.assertEquals(2, fire.scheduled.day)
        self.assertEquals(10, fire.scheduled.month)
        self.assertEquals(2020, fire.scheduled.year)
        self.assertEquals(event, fire.event)

        # archive the campaign
        post_data = dict(action='archive', objects=campaign.pk)
        self.client.post(reverse('campaigns.campaign_list'), post_data)
        response = self.client.get(reverse('campaigns.campaign_list'))
        self.assertNotContains(response, "Planting Reminders")

        # should have no event fires
        self.assertFalse(EventFire.objects.all())

        # restore the campaign
        post_data = dict(action='restore', objects=campaign.pk)
        self.client.post(reverse('campaigns.campaign_archived'), post_data)

        # EventFire should be back
        self.assertTrue(EventFire.objects.all())

        # set a planting date on our other farmer
        self.farmer2.set_field(self.user, 'planting_date', '1/6/2022')

        # should have two fire events now
        fires = EventFire.objects.all()
        self.assertEquals(2, len(fires))

        fire = fires[0]
        self.assertEquals(2, fire.scheduled.day)
        self.assertEquals(10, fire.scheduled.month)
        self.assertEquals(2020, fire.scheduled.year)
        self.assertEquals(event, fire.event)

        fire = fires[1]
        self.assertEquals(2, fire.scheduled.day)
        self.assertEquals(6, fire.scheduled.month)
        self.assertEquals(2022, fire.scheduled.year)
        self.assertEquals(event, fire.event)

        # setting a planting date on our outside contact has no effect
        self.nonfarmer.set_field(self.user, 'planting_date', '1/7/2025')
        self.assertEquals(2, EventFire.objects.all().count())

        # remove one of the farmers from the group
        response = self.client.post(reverse('contacts.contact_read', args=[self.farmer1.uuid]),
                                    dict(contact=self.farmer1.pk, group=self.farmers.pk))
        self.assertEquals(200, response.status_code)

        # should only be one event now (on farmer 2)
        fire = EventFire.objects.get()
        self.assertEquals(2, fire.scheduled.day)
        self.assertEquals(6, fire.scheduled.month)
        self.assertEquals(2022, fire.scheduled.year)
        self.assertEquals(event, fire.event)

        # but if we add him back in, should be updated
        post_data = dict(name=self.farmer1.name,
                         groups=[self.farmers.id],
                         __urn__tel=self.farmer1.get_urn('tel').path)

        self.client.post(reverse('contacts.contact_update', args=[self.farmer1.id]), post_data)
        response = self.client.post(reverse('contacts.contact_update_fields', args=[self.farmer1.id]),
                                    dict(__field__planting_date=['4/8/2020']))
        self.assertRedirect(response, reverse('contacts.contact_read', args=[self.farmer1.uuid]))

        fires = EventFire.objects.all()
        self.assertEquals(2, len(fires))

        fire = fires[0]
        self.assertEquals(5, fire.scheduled.day)
        self.assertEquals(8, fire.scheduled.month)
        self.assertEquals(2020, fire.scheduled.year)
        self.assertEquals(event, fire.event)
        self.assertEquals(str(fire), "%s - %s" % (fire.event, fire.contact))

        event = CampaignEvent.objects.get()

        # get the detail page of the event
        response = self.client.get(reverse('campaigns.campaignevent_read', args=[event.pk]))
        self.assertEquals(200, response.status_code)
        self.assertEquals(response.context['scheduled_event_fires_count'], 0)
        self.assertEquals(len(response.context['scheduled_event_fires']), 2)

        # delete an event
        self.client.post(reverse('campaigns.campaignevent_delete', args=[event.pk]), dict())
        self.assertFalse(CampaignEvent.objects.all()[0].is_active)
        response = self.client.get(reverse('campaigns.campaign_read', args=[campaign.pk]))
        self.assertNotContains(response, "Color Flow")

    def test_deleting_reimport_contact_groups(self):
        campaign = Campaign.create(self.org, self.admin, "Planting Reminders", self.farmers)

        # create a reminder for our first planting event
        planting_reminder = CampaignEvent.create_flow_event(self.org, self.admin, campaign, relative_to=self.planting_date,
                                                            offset=3, unit='D', flow=self.reminder_flow)

        self.assertEquals(0, EventFire.objects.all().count())
        self.farmer1.set_field(self.user, 'planting_date', "10-05-2020 12:30:10")
        self.farmer2.set_field(self.user, 'planting_date', "15-05-2020 12:30:10")

        # now we have event fires accordingly
        self.assertEquals(2, EventFire.objects.all().count())

        # farmer one fire
        scheduled = EventFire.objects.get(contact=self.farmer1, event=planting_reminder).scheduled
        self.assertEquals("13-5-2020", "%s-%s-%s" % (scheduled.day, scheduled.month, scheduled.year))

        # farmer two fire
        scheduled = EventFire.objects.get(contact=self.farmer2, event=planting_reminder).scheduled
        self.assertEquals("18-5-2020", "%s-%s-%s" % (scheduled.day, scheduled.month, scheduled.year))

        # delete our farmers group
        self.farmers.release()

        # this should have removed all the event fires for that group
        self.assertEquals(0, EventFire.objects.filter(event=planting_reminder).count())

        # and our group is no longer active
        self.assertFalse(campaign.group.is_active)

        # now import the group again
        filename = 'farmers.csv'
        extra_fields = [dict(key='planting_date', header='planting_date', label='Planting Date', type='D')]
        import_params = dict(org_id=self.org.id, timezone=self.org.timezone, extra_fields=extra_fields, original_filename=filename)

        from temba.contacts.models import ImportTask, Contact
        import json
        task = ImportTask.objects.create(
            created_by=self.admin, modified_by=self.admin,
            csv_file='test_imports/' + filename,
            model_class="Contact", import_params=json.dumps(import_params), import_log="", task_id="A")
        Contact.import_csv(task, log=None)

        # check that we have new planting dates
        self.farmer1 = Contact.objects.get(pk=self.farmer1.pk)
        self.farmer2 = Contact.objects.get(pk=self.farmer2.pk)

        planting = self.farmer1.get_field('planting_date').datetime_value
        self.assertEquals("10-8-2020", "%s-%s-%s" % (planting.day, planting.month, planting.year))

        planting = self.farmer2.get_field('planting_date').datetime_value
        self.assertEquals("15-8-2020", "%s-%s-%s" % (planting.day, planting.month, planting.year))

        # now update the campaign
        from temba.contacts.models import ContactGroup
        self.farmers = ContactGroup.user_groups.get(name='Farmers')
        self.login(self.admin)
        post_data = dict(name="Planting Reminders", group=self.farmers.pk)
        self.client.post(reverse('campaigns.campaign_update', args=[campaign.pk]), post_data)

        # should have two fresh new fires
        self.assertEquals(2, EventFire.objects.all().count())

        # check their new planting dates
        scheduled = EventFire.objects.get(contact=self.farmer1, event=planting_reminder).scheduled
        self.assertEquals("13-8-2020", "%s-%s-%s" % (scheduled.day, scheduled.month, scheduled.year))

        # farmer two fire
        scheduled = EventFire.objects.get(contact=self.farmer2, event=planting_reminder).scheduled
        self.assertEquals("18-8-2020", "%s-%s-%s" % (scheduled.day, scheduled.month, scheduled.year))

        # give our non farmer a planting date
        self.nonfarmer.set_field(self.user, 'planting_date', "20-05-2020 12:30:10")

        # now update to the non-farmer group
        self.nonfarmers = self.create_group("Not Farmers", [self.nonfarmer])
        post_data = dict(name="Planting Reminders", group=self.nonfarmers.pk)
        self.client.post(reverse('campaigns.campaign_update', args=[campaign.pk]), post_data)

        # only one fire for the non-farmer the previous two should be deleted by the group change
        self.assertEquals(1, EventFire.objects.all().count())
        self.assertEquals(1, EventFire.objects.filter(contact=self.nonfarmer).count())

    def test_scheduling(self):
        campaign = Campaign.create(self.org, self.admin, "Planting Reminders", self.farmers)

        self.assertEquals("Planting Reminders", unicode(campaign))

        # create a reminder for our first planting event
        planting_reminder = CampaignEvent.create_flow_event(self.org, self.admin, campaign, relative_to=self.planting_date,
                                                            offset=0, unit='D', flow=self.reminder_flow, delivery_hour=17)

        self.assertEquals("Planting Date == 0 -> Color Flow", unicode(planting_reminder))

        # schedule our reminders
        EventFire.update_campaign_events(campaign)

        # we should haven't any event fires created, since neither of our farmers have a planting date
        self.assertEquals(0, EventFire.objects.all().count())

        # ok, set a planting date on one of our contacts
        self.farmer1.set_field(self.user, 'planting_date', "05-10-2020 12:30:10")

        # update our campaign events
        EventFire.update_campaign_events(campaign)

        # should have one event now
        fire = EventFire.objects.get()
        self.assertEquals(5, fire.scheduled.day)
        self.assertEquals(10, fire.scheduled.month)
        self.assertEquals(2020, fire.scheduled.year)

        # account for timezone difference, our org is in UTC+2
        self.assertEquals(17 - 2, fire.scheduled.hour)

        self.assertEquals(self.farmer1, fire.contact)
        self.assertEquals(planting_reminder, fire.event)

        self.assertIsNone(fire.fired)

        # change the date of our date
        self.farmer1.set_field(self.user, 'planting_date', "06-10-2020 12:30:10")

        EventFire.update_campaign_events_for_contact(campaign, self.farmer1)
        fire = EventFire.objects.get()
        self.assertEquals(6, fire.scheduled.day)
        self.assertEquals(10, fire.scheduled.month)
        self.assertEquals(2020, fire.scheduled.year)
        self.assertEquals(self.farmer1, fire.contact)
        self.assertEquals(planting_reminder, fire.event)

        # set it to something invalid
        self.farmer1.set_field(self.user, 'planting_date', "what?")
        EventFire.update_campaign_events_for_contact(campaign, self.farmer1)
        self.assertFalse(EventFire.objects.all())

        # now something valid again
        self.farmer1.set_field(self.user, 'planting_date', "07-10-2020 12:30:10")

        EventFire.update_campaign_events_for_contact(campaign, self.farmer1)
        fire = EventFire.objects.get()
        self.assertEquals(7, fire.scheduled.day)
        self.assertEquals(10, fire.scheduled.month)
        self.assertEquals(2020, fire.scheduled.year)
        self.assertEquals(self.farmer1, fire.contact)
        self.assertEquals(planting_reminder, fire.event)

        # create another reminder
        planting_reminder2 = CampaignEvent.create_flow_event(self.org, self.admin, campaign, relative_to=self.planting_date,
                                                             offset=1, unit='D', flow=self.reminder2_flow)

        self.assertEquals(1, planting_reminder2.abs_offset())

        # update the campaign
        EventFire.update_campaign_events(campaign)

        # should have two events now, ordered by date
        events = EventFire.objects.all()

        self.assertEquals(planting_reminder, events[0].event)
        self.assertEquals(7, events[0].scheduled.day)

        self.assertEquals(planting_reminder2, events[1].event)
        self.assertEquals(8, events[1].scheduled.day)

        # mark one of the events as inactive
        planting_reminder2.is_active = False
        planting_reminder2.save()

        # update the campaign
        EventFire.update_campaign_events(campaign)

        # back to only one event
        event = EventFire.objects.get()
        self.assertEquals(planting_reminder, event.event)
        self.assertEquals(7, event.scheduled.day)

        # update our date
        self.farmer1.set_field(self.user, 'planting_date', '09-10-2020 12:30')

        # should have updated
        event = EventFire.objects.get()
        self.assertEquals(planting_reminder, event.event)
        self.assertEquals(9, event.scheduled.day)

        # let's remove our contact field
        ContactField.hide_field(self.org, self.user, 'planting_date')

        # shouldn't have anything scheduled
        self.assertFalse(EventFire.objects.all())

        # add it back in
        ContactField.get_or_create(self.org, self.admin, 'planting_date', "planting Date")

        # should be back!
        event = EventFire.objects.get()
        self.assertEquals(planting_reminder, event.event)
        self.assertEquals(9, event.scheduled.day)

        # change our fire date to sometimein the past so it gets triggered
        event.scheduled = timezone.now() - timedelta(hours=1)
        event.save()

        # schedule our events to fire
        check_campaigns_task()

        # should have one flow run now
        run = FlowRun.objects.get()
        self.assertEquals(event.contact, run.contact)
