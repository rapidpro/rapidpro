# -*- coding: utf-8 -*-
from __future__ import absolute_import, division, print_function, unicode_literals

import json
import six
import pytz

from datetime import timedelta
from django.core.exceptions import ValidationError
from django.core.urlresolvers import reverse
from django.utils import timezone
from temba.campaigns.tasks import check_campaigns_task
from temba.contacts.models import ContactField, ImportTask, Contact, ContactGroup
from temba.flows.models import FlowRun, Flow, RuleSet, ActionSet, FlowRevision, FlowStart
from temba.msgs.models import Msg
from temba.orgs.models import Language, get_current_export_version
from temba.tests import TembaTest
from .models import Campaign, CampaignEvent, EventFire


class CampaignTest(TembaTest):

    def setUp(self):
        super(CampaignTest, self).setUp()

        self.farmer1 = self.create_contact("Rob Jasper", "+250788111111")
        self.farmer2 = self.create_contact("Mike Gordon", "+250788222222", language='spa')

        self.nonfarmer = self.create_contact("Trey Anastasio", "+250788333333")
        self.farmers = self.create_group("Farmers", [self.farmer1, self.farmer2])

        self.reminder_flow = self.create_flow(name="Reminder Flow")
        self.reminder2_flow = self.create_flow(name="Planting Reminder")

        # create a voice flow to make sure they work too, not a proper voice flow but
        # sufficient for assuring these flow types show up where they should
        self.voice_flow = self.create_flow(name="IVR flow", flow_type='V')

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

        flow_json = self.get_flow_json('call_me_maybe')['definition']
        flow = Flow.create_instance(dict(name='Call Me Maybe', org=self.org, flow_type=Flow.MESSAGE,
                                         created_by=self.admin, modified_by=self.admin,
                                         saved_by=self.admin, version_number=3))

        FlowRevision.create_instance(dict(flow=flow, definition=flow_json,
                                          spec_version=3, revision=1,
                                          created_by=self.admin, modified_by=self.admin))

        event4 = CampaignEvent.create_flow_event(self.org, self.admin, campaign, self.planting_date,
                                                 offset=2, unit='W', flow=flow, delivery_hour='5')

        self.assertEqual(flow.version_number, 3)
        self.assertEqual(campaign.get_sorted_events(), [event2, event1, event3, event4])
        flow.refresh_from_db()
        self.assertNotEqual(flow.version_number, 3)
        self.assertEqual(flow.version_number, get_current_export_version())

    def test_events_batch_fire(self):
        campaign = Campaign.create(self.org, self.admin, "Planting Reminders", self.farmers)

        # create a reminder for our first planting event
        CampaignEvent.create_flow_event(self.org, self.admin, campaign, relative_to=self.planting_date,
                                        offset=3, unit='D', flow=self.reminder_flow)

        self.assertEqual(0, EventFire.objects.all().count())
        self.farmer1.set_field(self.user, 'planting_date', "10-05-2020 12:30:10")
        self.farmer2.set_field(self.user, 'planting_date', "15-05-2020 12:30:10")

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

    def test_message_event(self):
        # update the planting date for our contacts
        self.farmer1.set_field(self.user, 'planting_date', '1/10/2020')

        # ok log in as an org
        self.login(self.admin)

        # create a campaign
        campaign = Campaign.create(self.org, self.admin, "Planting Reminders", self.farmers)

        # go create an event that based on a message
        url = '%s?campaign=%d' % (reverse('campaigns.campaignevent_create'), campaign.id)
        response = self.client.get(url)
        self.assertIn('base', response.context['form'].fields)

        # should be no language list
        self.assertNotContains(response, 'show_language')

        # set our primary language to Achinese
        ace = Language.objects.create(org=self.org, name='Achinese', iso_code='ace',
                                      created_by=self.admin, modified_by=self.admin)

        self.org.primary_language = ace
        self.org.save()

        # now we should have ace as our primary
        response = self.client.get(url)
        self.assertNotIn('base', response.context['form'].fields)
        self.assertIn('ace', response.context['form'].fields)

        # add second language
        spa = Language.objects.create(org=self.org, name='Spanish', iso_code='spa',
                                      created_by=self.admin, modified_by=self.admin)

        response = self.client.get(url)
        self.assertNotIn('base', response.context['form'].fields)
        self.assertIn('ace', response.context['form'].fields)
        self.assertIn('spa', response.context['form'].fields)

        # and our language list should be there
        self.assertContains(response, 'show_language')

        self.org.primary_language = None
        self.org.save()

        response = self.client.get(url)
        self.assertIn('base', response.context['form'].fields)
        self.assertIn('spa', response.context['form'].fields)
        self.assertIn('ace', response.context['form'].fields)

        post_data = dict(relative_to=self.planting_date.pk, event_type='M', base="This is my message", spa="hola",
                         direction='B', offset=1, unit='W', flow_to_start='', delivery_hour=13)
        response = self.client.post(reverse('campaigns.campaignevent_create') + "?campaign=%d" % campaign.pk, post_data)

        # should be redirected back to our campaign read page
        self.assertRedirect(response, reverse('campaigns.campaign_read', args=[campaign.pk]))

        # should have one event, which created a corresponding flow
        event = CampaignEvent.objects.get()
        flow = event.flow
        self.assertEqual(Flow.MESSAGE, flow.flow_type)

        entry = ActionSet.objects.filter(uuid=flow.entry_uuid)[0]
        msg = entry.get_actions()[0].msg
        self.assertEqual(flow.base_language, 'base')
        self.assertEqual(msg, {'base': "This is my message", 'spa': "hola", 'ace': ""})
        self.assertFalse(RuleSet.objects.filter(flow=flow))

        self.assertEqual(-1, event.offset)
        self.assertEqual(13, event.delivery_hour)
        self.assertEqual('W', event.unit)
        self.assertEqual('M', event.event_type)

        self.assertEqual(event.get_message(contact=self.farmer1), "This is my message")
        self.assertEqual(event.get_message(contact=self.farmer2), "hola")
        self.assertEqual(event.get_message(), "This is my message")

        url = reverse('campaigns.campaignevent_update', args=[event.id])
        response = self.client.get(url)
        self.assertEqual('This is my message', response.context['form'].fields['base'].initial)
        self.assertEqual('hola', response.context['form'].fields['spa'].initial)
        self.assertEqual('', response.context['form'].fields['ace'].initial)

        # promote spanish to our primary language
        self.org.primary_language = spa
        self.org.save()

        # the base language needs to stay present since it's the true backdown
        response = self.client.get(url)
        self.assertIn('base', response.context['form'].fields)
        self.assertEqual('This is my message', response.context['form'].fields['base'].initial)
        self.assertEqual('hola', response.context['form'].fields['spa'].initial)
        self.assertEqual('', response.context['form'].fields['ace'].initial)

        # now we save our new settings
        post_data = dict(relative_to=self.planting_date.pk, event_type='M', base='Required', spa="This is my spanish @contact.planting_date", ace='',
                         direction='B', offset=1, unit='W', flow_to_start='', delivery_hour=13)
        response = self.client.post(url, post_data)
        self.assertEqual(302, response.status_code)
        flow.refresh_from_db()

        # we should retain 'base' as our base language
        self.assertEqual('base', flow.base_language)

        # now we can remove our primary language
        self.org.primary_language = None
        self.org.save()

        # and still get the same settings, (it should use the base of the flow instead of just base here)
        response = self.client.get(url)
        self.assertIn('base', response.context['form'].fields)
        self.assertEqual('This is my spanish @contact.planting_date', response.context['form'].fields['spa'].initial)
        self.assertEqual('', response.context['form'].fields['ace'].initial)

        # our single message flow should have a dependency on planting_date
        event.flow.refresh_from_db()
        self.assertEqual(1, event.flow.field_dependencies.all().count())

        # delete the event
        self.client.post(reverse('campaigns.campaignevent_delete', args=[event.pk]), dict())
        self.assertFalse(CampaignEvent.objects.get(id=event.id).is_active)

        # our single message flow should be released and take its dependencies with it
        event.flow.refresh_from_db()
        self.assertFalse(event.flow.is_active)
        self.assertEqual(0, event.flow.field_dependencies.all().count())

    def test_views(self):
        # update the planting date for our contacts
        self.farmer1.set_field(self.user, 'planting_date', '1/10/2020')

        # get the resulting time (including minutes)
        planting_date = self.farmer1.get_field('planting_date').datetime_value

        # don't log in, try to create a new campaign
        response = self.client.get(reverse('campaigns.campaign_create'))
        self.assertRedirect(response, reverse('users.user_login'))

        # ok log in as an org
        self.login(self.admin)

        # go to to the creation page
        response = self.client.get(reverse('campaigns.campaign_create'))
        self.assertEqual(200, response.status_code)

        # groups shouldn't include the group that isn't ready
        self.assertEqual(set(response.context['form'].fields['group'].queryset), {self.farmers})

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
        self.assertEqual(200, response.status_code)

        post_data = dict(relative_to=self.planting_date.pk, delivery_hour=-1, base='', direction='A', offset=2, unit='D', event_type='M', flow_to_start=self.reminder_flow.pk)
        response = self.client.post(reverse('campaigns.campaignevent_create') + "?campaign=%d" % campaign.pk, post_data)

        self.assertTrue(response.context['form'].errors)
        self.assertIn('A message is required', six.text_type(response.context['form'].errors['__all__']))

        post_data = dict(relative_to=self.planting_date.pk, delivery_hour=-1, base='allo!' * 500, direction='A',
                         offset=2, unit='D', event_type='M', flow_to_start=self.reminder_flow.pk)

        response = self.client.post(reverse('campaigns.campaignevent_create') + "?campaign=%d" % campaign.pk, post_data)

        self.assertTrue(response.context['form'].errors)
        self.assertTrue("Translation for &#39;Default&#39; exceeds the %d character limit." % Msg.MAX_TEXT_LEN in six.text_type(response.context['form'].errors['__all__']))

        post_data = dict(relative_to=self.planting_date.pk, delivery_hour=-1, base='', direction='A', offset=2, unit='D', event_type='F')
        response = self.client.post(reverse('campaigns.campaignevent_create') + "?campaign=%d" % campaign.pk, post_data)

        self.assertTrue(response.context['form'].errors)
        self.assertIn('Please select a flow', response.context['form'].errors['flow_to_start'])

        post_data = dict(relative_to=self.planting_date.pk, delivery_hour=-1, base='', direction='A', offset=2, unit='D', event_type='F', flow_to_start=self.reminder_flow.pk)
        response = self.client.post(reverse('campaigns.campaignevent_create') + "?campaign=%d" % campaign.pk, post_data)

        # should be redirected back to our campaign read page
        self.assertRedirect(response, reverse('campaigns.campaign_read', args=[campaign.pk]))

        # should now have a campaign event
        event = CampaignEvent.objects.get()
        self.assertEqual(self.reminder_flow, event.flow)
        self.assertEqual(self.planting_date, event.relative_to)
        self.assertEqual(2, event.offset)

        # read the campaign read page
        response = self.client.get(reverse('campaigns.campaign_read', args=[campaign.pk]))
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

        post_data = dict(relative_to=self.planting_date.pk, delivery_hour=15, base='', direction='A', offset=1, unit='D', event_type='F', flow_to_start=self.reminder_flow.pk)
        response = self.client.post(reverse('campaigns.campaignevent_update', args=[event.pk]), post_data)

        # should be redirected back to our campaign event read page
        self.assertRedirect(response, reverse('campaigns.campaignevent_read', args=[event.pk]))

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

        post_data = dict(relative_to=self.planting_date.pk, delivery_hour=15, base='', direction='A', offset=2,
                         unit='D', event_type='F', flow_to_start=self.reminder2_flow.pk)
        self.client.post(reverse('campaigns.campaignevent_create') + "?campaign=%d" % campaign.pk, post_data)

        # trying to archive our flow should fail since it belongs to a campaign
        post_data = dict(action='archive', objects=[self.reminder_flow.pk])
        response = self.client.post(reverse('flows.flow_list'), post_data)
        self.reminder_flow.refresh_from_db()
        self.assertFalse(self.reminder_flow.is_archived)
        self.assertEqual('Reminder Flow is used inside a campaign. To archive it, first remove it from your campaigns.', response.get('Temba-Toast'))

        post_data = dict(action='archive', objects=[self.reminder_flow.pk, self.reminder2_flow.pk])
        response = self.client.post(reverse('flows.flow_list'), post_data)
        self.assertEqual('Planting Reminder and Reminder Flow are used inside a campaign. To archive them, first remove them from your campaigns.', response.get('Temba-Toast'))
        CampaignEvent.objects.filter(flow=self.reminder2_flow.pk).delete()

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
        self.nonfarmer.set_field(self.user, 'planting_date', '1/7/2025')
        self.assertEqual(2, EventFire.objects.all().count())

        # remove one of the farmers from the group
        response = self.client.post(reverse('contacts.contact_read', args=[self.farmer1.uuid]),
                                    dict(contact=self.farmer1.pk, group=self.farmers.pk))
        self.assertEqual(200, response.status_code)

        # should only be one event now (on farmer 2)
        fire = EventFire.objects.get()
        self.assertEqual(2, fire.scheduled.day)
        self.assertEqual(6, fire.scheduled.month)
        self.assertEqual(2022, fire.scheduled.year)
        self.assertEqual(event, fire.event)

        # but if we add him back in, should be updated
        post_data = dict(name=self.farmer1.name,
                         groups=[self.farmers.id],
                         __urn__tel=self.farmer1.get_urn('tel').path)

        self.client.post(reverse('contacts.contact_update', args=[self.farmer1.id]), post_data)
        planting_date = ContactField.objects.filter(key='planting_date').first()
        response = self.client.post(reverse('contacts.contact_update_fields', args=[self.farmer1.id]),
                                    dict(contact_field=planting_date.id, field_value='4/8/2020'))
        self.assertRedirect(response, reverse('contacts.contact_read', args=[self.farmer1.uuid]))

        fires = EventFire.objects.all()
        self.assertEqual(2, len(fires))

        fire = fires[0]
        self.assertEqual(5, fire.scheduled.day)
        self.assertEqual(8, fire.scheduled.month)
        self.assertEqual(2020, fire.scheduled.year)
        self.assertEqual(event, fire.event)
        self.assertEqual(str(fire), "%s - %s" % (fire.event, fire.contact))

        event = CampaignEvent.objects.get()

        # get the detail page of the event
        response = self.client.get(reverse('campaigns.campaignevent_read', args=[event.pk]))
        self.assertEqual(200, response.status_code)
        self.assertEqual(response.context['scheduled_event_fires_count'], 0)
        self.assertEqual(len(response.context['scheduled_event_fires']), 2)

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

        self.assertEqual(0, EventFire.objects.all().count())
        self.farmer1.set_field(self.user, 'planting_date', "10-05-2020 12:30:10")
        self.farmer2.set_field(self.user, 'planting_date', "15-05-2020 12:30:10")

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
        filename = 'farmers.csv'
        extra_fields = [dict(key='planting_date', header='planting_date', label='Planting Date', type='D')]
        import_params = dict(org_id=self.org.id, timezone=six.text_type(self.org.timezone), extra_fields=extra_fields, original_filename=filename)

        task = ImportTask.objects.create(
            created_by=self.admin, modified_by=self.admin,
            csv_file='test_imports/' + filename,
            model_class="Contact", import_params=json.dumps(import_params), import_log="", task_id="A")
        Contact.import_csv(task, log=None)

        # check that we have new planting dates
        self.farmer1 = Contact.objects.get(pk=self.farmer1.pk)
        self.farmer2 = Contact.objects.get(pk=self.farmer2.pk)

        planting = self.farmer1.get_field('planting_date').datetime_value
        self.assertEqual("10-8-2020", "%s-%s-%s" % (planting.day, planting.month, planting.year))

        planting = self.farmer2.get_field('planting_date').datetime_value
        self.assertEqual("15-8-2020", "%s-%s-%s" % (planting.day, planting.month, planting.year))

        # now update the campaign
        self.farmers = ContactGroup.user_groups.get(name='Farmers')
        self.login(self.admin)
        post_data = dict(name="Planting Reminders", group=self.farmers.pk)
        self.client.post(reverse('campaigns.campaign_update', args=[campaign.pk]), post_data)

        # should have two fresh new fires
        self.assertEqual(2, EventFire.objects.all().count())

        # check their new planting dates
        scheduled = EventFire.objects.get(contact=self.farmer1, event=planting_reminder).scheduled
        self.assertEqual("13-8-2020", "%s-%s-%s" % (scheduled.day, scheduled.month, scheduled.year))

        # farmer two fire
        scheduled = EventFire.objects.get(contact=self.farmer2, event=planting_reminder).scheduled
        self.assertEqual("18-8-2020", "%s-%s-%s" % (scheduled.day, scheduled.month, scheduled.year))

        # give our non farmer a planting date
        self.nonfarmer.set_field(self.user, 'planting_date', "20-05-2020 12:30:10")

        # now update to the non-farmer group
        self.nonfarmers = self.create_group("Not Farmers", [self.nonfarmer])
        post_data = dict(name="Planting Reminders", group=self.nonfarmers.pk)
        self.client.post(reverse('campaigns.campaign_update', args=[campaign.pk]), post_data)

        # only one fire for the non-farmer the previous two should be deleted by the group change
        self.assertEqual(1, EventFire.objects.all().count())
        self.assertEqual(1, EventFire.objects.filter(contact=self.nonfarmer).count())

    def test_dst_scheduling(self):
        # set our timezone to something that honors DST
        eastern = pytz.timezone('US/Eastern')
        self.org.timezone = eastern
        self.org.save()

        # create our campaign and event
        campaign = Campaign.create(self.org, self.admin, "Planting Reminders", self.farmers)
        CampaignEvent.create_flow_event(self.org, self.admin, campaign, relative_to=self.planting_date,
                                        offset=2, unit='D', flow=self.reminder_flow)

        # set the time to something pre-dst (fall back on November 4th at 2am to 1am)
        self.farmer1.set_field(self.user, 'planting_date', "03-11-2029 12:30:00")
        EventFire.update_campaign_events(campaign)

        # we should be scheduled to go off on the 5th at 12:30:10 Eastern
        fire = EventFire.objects.get()
        self.assertEqual(5, fire.scheduled.day)
        self.assertEqual(11, fire.scheduled.month)
        self.assertEqual(2029, fire.scheduled.year)
        self.assertEqual(12, fire.scheduled.astimezone(eastern).hour)

        # assert our offsets are different (we crossed DST)
        self.assertNotEqual(fire.scheduled.utcoffset(), self.farmer1.get_field('planting_date').datetime_value.utcoffset())

        # the number of hours between these two events should be 49 (two days 1 hour)
        delta = fire.scheduled - self.farmer1.get_field('planting_date').datetime_value
        self.assertEqual(delta.days, 2)
        self.assertEqual(delta.seconds, 3600)

        # spring forward case, this will go across a DST jump forward scenario
        self.farmer1.set_field(self.user, 'planting_date', "10-03-2029 02:30:00")
        EventFire.update_campaign_events(campaign)

        fire = EventFire.objects.get()
        self.assertEqual(12, fire.scheduled.day)
        self.assertEqual(3, fire.scheduled.month)
        self.assertEqual(2029, fire.scheduled.year)
        self.assertEqual(2, fire.scheduled.astimezone(eastern).hour)

        # assert our offsets changed (we crossed DST)
        self.assertNotEqual(fire.scheduled.utcoffset(), self.farmer1.get_field('planting_date').datetime_value.utcoffset())

        # delta should be 47 hours exactly
        delta = fire.scheduled - self.farmer1.get_field('planting_date').datetime_value
        self.assertEqual(delta.days, 1)
        self.assertEqual(delta.seconds, 82800)

    def test_scheduling(self):
        campaign = Campaign.create(self.org, self.admin, "Planting Reminders", self.farmers)
        self.assertEqual("Planting Reminders", six.text_type(campaign))

        # create a reminder for our first planting event
        planting_reminder = CampaignEvent.create_flow_event(self.org, self.admin, campaign, relative_to=self.planting_date,
                                                            offset=0, unit='D', flow=self.reminder_flow, delivery_hour=17)

        self.assertEqual("Planting Date == 0 -> Reminder Flow", six.text_type(planting_reminder))

        # schedule our reminders
        EventFire.update_campaign_events(campaign)

        # we should haven't any event fires created, since neither of our farmers have a planting date
        self.assertEqual(0, EventFire.objects.all().count())

        # ok, set a planting date on one of our contacts
        self.farmer1.set_field(self.user, 'planting_date', "05-10-2020 12:30:10")

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
        self.farmer1.set_field(self.user, 'planting_date', "06-10-2020 12:30:10")

        EventFire.update_campaign_events_for_contact(campaign, self.farmer1)
        fire = EventFire.objects.get()
        self.assertEqual(6, fire.scheduled.day)
        self.assertEqual(10, fire.scheduled.month)
        self.assertEqual(2020, fire.scheduled.year)
        self.assertEqual(self.farmer1, fire.contact)
        self.assertEqual(planting_reminder, fire.event)

        # set it to something invalid
        self.farmer1.set_field(self.user, 'planting_date', "what?")
        EventFire.update_campaign_events_for_contact(campaign, self.farmer1)
        self.assertFalse(EventFire.objects.all())

        # now something valid again
        self.farmer1.set_field(self.user, 'planting_date', "07-10-2020 12:30:10")

        EventFire.update_campaign_events_for_contact(campaign, self.farmer1)
        fire = EventFire.objects.get()
        self.assertEqual(7, fire.scheduled.day)
        self.assertEqual(10, fire.scheduled.month)
        self.assertEqual(2020, fire.scheduled.year)
        self.assertEqual(self.farmer1, fire.contact)
        self.assertEqual(planting_reminder, fire.event)

        # create another reminder
        planting_reminder2 = CampaignEvent.create_flow_event(self.org, self.admin, campaign, relative_to=self.planting_date,
                                                             offset=1, unit='D', flow=self.reminder2_flow)

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
        self.farmer1.set_field(self.user, 'planting_date', '09-10-2020 12:30')

        # should have updated
        event = EventFire.objects.get()
        self.assertEqual(planting_reminder, event.event)
        self.assertEqual(9, event.scheduled.day)

        # let's remove our contact field
        ContactField.hide_field(self.org, self.user, 'planting_date')

        # shouldn't have anything scheduled
        self.assertFalse(EventFire.objects.all())

        # add it back in
        ContactField.get_or_create(self.org, self.admin, 'planting_date', "planting Date")

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

    def test_translations(self):
        campaign = Campaign.create(self.org, self.admin, "Planting Reminders", self.farmers)

        event1 = CampaignEvent.create_message_event(self.org, self.admin, campaign,
                                                    relative_to=self.planting_date,
                                                    offset=0, unit='D', message={'eng': "hello"},
                                                    base_language='eng')

        with self.assertRaises(ValidationError):
            event1.message = {'ddddd': "x"}
            event1.full_clean()

        with self.assertRaises(ValidationError):
            event1.message = {'eng': "x" * 8001}
            event1.full_clean()

        with self.assertRaises(ValidationError):
            event1.message = {}
            event1.full_clean()

    def test_unarchiving_campaigns(self):
        # create a campaign
        campaign = Campaign.create(self.org, self.user, "Planting Reminders", self.farmers)

        flow = self.create_flow()

        CampaignEvent.create_flow_event(self.org, self.admin, campaign, self.planting_date,
                                        offset=1, unit='W', flow=flow, delivery_hour='13')
        CampaignEvent.create_flow_event(self.org, self.admin, campaign, self.planting_date,
                                        offset=1, unit='W', flow=self.reminder_flow, delivery_hour='9')

        CampaignEvent.create_message_event(self.org, self.admin, campaign, self.planting_date,
                                           1, CampaignEvent.UNIT_DAYS, "Don't forget to brush your teeth")

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
        self.create_field('gender', "Gender")
        women = self.create_group("Women", query='gender="F"')
        campaign = Campaign.create(self.org, self.admin, "Planting Reminders for Women", women)
        event = CampaignEvent.create_message_event(self.org, self.admin, campaign,
                                                   relative_to=self.planting_date,
                                                   offset=0, unit='D', message={'eng': "hello"},
                                                   base_language='eng')

        # create a contact not in the group, but with a field value
        anna = self.create_contact("Anna", "+250788333333")
        anna.set_field(self.admin, 'planting_date', "09-10-2020 12:30")

        # no contacts in our dynamic group yet, so no event fires
        self.assertEqual(EventFire.objects.filter(event=event).count(), 0)

        # update contact so that they become part of the dynamic group
        anna.set_field(self.admin, 'gender', "f")
        self.assertEqual(set(women.contacts.all()), {anna})

        # and who should now have an event fire for our campaign event
        self.assertEqual(EventFire.objects.filter(event=event, contact=anna).count(), 1)

        # change dynamic group query so anna is removed
        women.update_query('gender=FEMALE')
        self.assertEqual(set(women.contacts.all()), set())

        # check that her event fire is now removed
        self.assertEqual(EventFire.objects.filter(event=event, contact=anna).count(), 0)

        # but if query is reverted, her event fire should be recreated
        women.update_query('gender=F')
        self.assertEqual(set(women.contacts.all()), {anna})

        # check that her event fire is now removed
        self.assertEqual(EventFire.objects.filter(event=event, contact=anna).count(), 1)
