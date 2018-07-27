# -*- coding: utf-8 -*-
from __future__ import absolute_import, division, print_function, unicode_literals

import six
import time
import json
from uuid import uuid4

from mock import patch
from datetime import timedelta
from django.core.urlresolvers import reverse
from django.test import override_settings
from django.utils import timezone
from temba.channels.models import Channel, ChannelEvent
from temba.contacts.models import TEL_SCHEME, Contact
from temba.flows.models import Flow, ActionSet, FlowRun
from temba.orgs.models import Language
from temba.msgs.models import Msg, INCOMING
from temba.schedules.models import Schedule
from temba.tests import TembaTest, MockResponse
from .models import Trigger
from .views import DefaultTriggerForm, RegisterTriggerForm


class TriggerTest(TembaTest):

    def test_no_trigger_redirects_to_create_page(self):
        self.login(self.admin)

        # no trigger existing
        Trigger.objects.all().delete()

        response = self.client.get(reverse('triggers.trigger_list'))
        self.assertEqual(response.status_code, 302)

        response = self.client.get(reverse('triggers.trigger_list'), follow=True)
        self.assertEqual(response.request['PATH_INFO'], reverse('triggers.trigger_create'))

    def test_keyword_trigger(self):
        self.login(self.admin)
        flow = self.create_flow()
        voice_flow = self.create_flow()
        voice_flow.flow_type = 'V'
        voice_flow.name = 'IVR Flow'
        voice_flow.save()

        # flow options should show sms and voice flows
        response = self.client.get(reverse("triggers.trigger_keyword"))
        self.assertContains(response, flow.name)
        self.assertContains(response, voice_flow.name)

        # try a keyword with spaces
        post_data = dict(keyword='keyword with spaces', flow=flow.id, match_type='F')
        response = self.client.post(reverse("triggers.trigger_keyword"), data=post_data)
        self.assertEqual(1, len(response.context['form'].errors))

        # try a keyword with special characters
        post_data = dict(keyword='keyw!o^rd__', flow=flow.id, match_type='F')
        response = self.client.post(reverse("triggers.trigger_keyword"), data=post_data)
        self.assertEqual(1, len(response.context['form'].errors))

        # unicode keyword (Arabic)
        post_data = dict(keyword='١٠٠', flow=flow.id, match_type='F')
        self.client.post(reverse("triggers.trigger_keyword"), data=post_data)
        trigger = Trigger.objects.get(keyword=u'١٠٠')
        self.assertEqual(flow.pk, trigger.flow.pk)

        # unicode keyword (Hindi)
        post_data = dict(keyword='मिलाए', flow=flow.id, match_type='F')
        self.client.post(reverse("triggers.trigger_keyword"), data=post_data)
        trigger = Trigger.objects.get(keyword=u'मिलाए')
        self.assertEqual(flow.pk, trigger.flow.pk)

        # a valid keyword
        post_data = dict(keyword='startkeyword', flow=flow.id, match_type='F')
        self.client.post(reverse("triggers.trigger_keyword"), data=post_data)
        trigger = Trigger.objects.get(keyword='startkeyword')
        self.assertEqual(flow.pk, trigger.flow.pk)

        # try a duplicate keyword
        post_data = dict(keyword='startkeyword', flow=flow.id, match_type='F')
        response = self.client.post(reverse("triggers.trigger_keyword"), data=post_data)
        self.assertEqual(1, len(response.context['form'].errors))

        # see our trigger on the list page
        response = self.client.get(reverse('triggers.trigger_list'))
        self.assertContains(response, 'startkeyword')

        response = self.client.get(reverse('triggers.trigger_list') + '?search=Key')
        self.assertContains(response, 'startkeyword')
        self.assertTrue(response.context['object_list'])

        response = self.client.get(reverse('triggers.trigger_list') + '?search=Tottenham')
        self.assertNotContains(response, 'startkeyword')
        self.assertFalse(response.context['object_list'])

        # archive it
        post_data = dict(action='archive', objects=trigger.pk)
        self.client.post(reverse('triggers.trigger_list'), post_data)
        response = self.client.get(reverse('triggers.trigger_list'))
        self.assertNotContains(response, 'startkeyword')

        # unarchive it
        response = self.client.get(reverse('triggers.trigger_archived'), post_data)
        self.assertContains(response, 'startkeyword')
        post_data = dict(action='restore', objects=trigger.pk)
        self.client.post(reverse('triggers.trigger_archived'), post_data)
        response = self.client.get(reverse('triggers.trigger_archived'), post_data)
        self.assertNotContains(response, 'startkeyword')
        response = self.client.get(reverse('triggers.trigger_list'), post_data)
        self.assertContains(response, 'startkeyword')

        # once archived we can duplicate it but with one active at a time
        trigger = Trigger.objects.get(keyword='startkeyword')
        trigger.is_archived = True
        trigger.save()

        post_data = dict(keyword='startkeyword', flow=flow.id, match_type='F')
        response = self.client.post(reverse("triggers.trigger_keyword"), data=post_data)
        self.assertEqual(Trigger.objects.filter(keyword="startkeyword").count(), 2)
        self.assertEqual(1, Trigger.objects.filter(keyword="startkeyword", is_archived=False).count())
        other_trigger = Trigger.objects.filter(keyword="startkeyword", is_archived=False)[0]
        self.assertFalse(trigger.pk == other_trigger.pk)

        # try archiving it we have one archived and the other active
        response = self.client.get(reverse('triggers.trigger_archived'), post_data)
        self.assertContains(response, 'startkeyword')
        post_data = dict(action='restore', objects=trigger.pk)
        self.client.post(reverse('triggers.trigger_archived'), post_data)
        response = self.client.get(reverse('triggers.trigger_archived'), post_data)
        self.assertContains(response, 'startkeyword')
        response = self.client.get(reverse('triggers.trigger_list'), post_data)
        self.assertContains(response, 'startkeyword')
        self.assertEqual(1, Trigger.objects.filter(keyword="startkeyword", is_archived=False).count())
        self.assertFalse(other_trigger.pk == Trigger.objects.filter(keyword="startkeyword", is_archived=False)[0].pk)

        self.contact = self.create_contact('Eric', '+250788382382')
        self.contact2 = self.create_contact('Nic', '+250788383383')
        group1 = self.create_group("first", [self.contact2])
        group2 = self.create_group("second", [self.contact])
        group3 = self.create_group("third", [self.contact, self.contact2])

        self.assertEqual(Trigger.objects.filter(keyword="startkeyword").count(), 2)
        self.assertEqual(Trigger.objects.filter(keyword="startkeyword", is_archived=False).count(), 1)

        # update trigger with 2 groups
        post_data = dict(keyword='startkeyword', flow=flow.id, match_type='F', groups=[group1.pk, group2.pk])
        response = self.client.post(reverse("triggers.trigger_keyword"), data=post_data)
        self.assertEqual(Trigger.objects.filter(keyword="startkeyword").count(), 3)
        self.assertEqual(Trigger.objects.filter(keyword="startkeyword", is_archived=False).count(), 2)

        # get error when groups overlap
        post_data = dict(keyword='startkeyword', flow=flow.id, match_type='F')
        post_data['groups'] = [group2.pk, group3.pk]
        response = self.client.post(reverse("triggers.trigger_keyword"), data=post_data)
        self.assertEqual(1, len(response.context['form'].errors))
        self.assertEqual(Trigger.objects.filter(keyword="startkeyword").count(), 3)
        self.assertEqual(Trigger.objects.filter(keyword="startkeyword", is_archived=False).count(), 2)

        # allow new creation when groups do not overlap
        post_data = dict(keyword='startkeyword', flow=flow.id, match_type='F')
        post_data['groups'] = [group3.pk]
        response = self.client.post(reverse("triggers.trigger_keyword"), data=post_data)
        self.assertEqual(Trigger.objects.filter(keyword="startkeyword").count(), 4)
        self.assertEqual(Trigger.objects.filter(keyword="startkeyword", is_archived=False).count(), 3)

    def test_inbound_call_trigger(self):
        self.login(self.admin)

        # inbound call trigger can be made without a call channel
        response = self.client.get(reverse('triggers.trigger_create'))
        self.assertContains(response, 'Start a flow after receiving a call')

        # make our channel support ivr
        self.channel.role += Channel.ROLE_CALL + Channel.ROLE_ANSWER
        self.channel.save()

        # flow is required
        response = self.client.post(reverse('triggers.trigger_inbound_call'), dict())
        self.assertEqual(list(response.context['form'].errors.keys()), ['flow'])

        # flow must be an ivr flow
        message_flow = self.create_flow()
        post_data = dict(flow=message_flow.pk)
        response = self.client.post(reverse('triggers.trigger_inbound_call'), post_data)
        self.assertEqual(list(response.context['form'].errors.keys()), ['flow'])

        # now lets create our first valid inbound call trigger
        guitarist_flow = self.create_flow()
        guitarist_flow.flow_type = Flow.VOICE
        guitarist_flow.save()

        post_data = dict(flow=guitarist_flow.pk)
        response = self.client.post(reverse('triggers.trigger_inbound_call'), post_data)
        trigger = Trigger.objects.filter(trigger_type=Trigger.TYPE_INBOUND_CALL).first()
        self.assertIsNotNone(trigger)

        # pretend we are getting a call from somebody
        trey = self.create_contact('Trey', '+17075551212')
        self.assertEqual(guitarist_flow.pk, Trigger.find_flow_for_inbound_call(trey).pk)

        # now lets check that group specific call triggers work
        mike = self.create_contact('Mike', '+17075551213')
        bassists = self.create_group('Bassists', [mike])

        # flow specific to our group
        bassist_flow = self.create_flow()
        bassist_flow.flow_type = Flow.VOICE
        bassist_flow.save()

        post_data = dict(flow=bassist_flow.pk, groups=[bassists.pk])
        response = self.client.post(reverse('triggers.trigger_inbound_call'), post_data)
        self.assertEqual(2, Trigger.objects.filter(trigger_type=Trigger.TYPE_INBOUND_CALL).count())

        self.assertEqual(bassist_flow.pk, Trigger.find_flow_for_inbound_call(mike).pk)
        self.assertEqual(guitarist_flow.pk, Trigger.find_flow_for_inbound_call(trey).pk)

        # release our channel
        self.channel.release()

        # should still have two voice flows and triggers (they aren't archived)
        self.assertEqual(2, Flow.objects.filter(flow_type=Flow.VOICE, is_archived=False).count())
        self.assertEqual(2, Trigger.objects.filter(trigger_type=Trigger.TYPE_INBOUND_CALL, is_archived=False).count())

    def test_referral_trigger(self):
        self.login(self.admin)
        flow = self.create_flow()

        self.fb_channel = Channel.create(self.org, self.user, None, 'FB', None, '1234',
                                         config={Channel.CONFIG_AUTH_TOKEN: 'auth'},
                                         uuid='00000000-0000-0000-0000-000000001234')

        create_url = reverse('triggers.trigger_referral')

        post_data = dict()
        response = self.client.post(create_url, post_data)
        self.assertEqual(list(response.context['form'].errors.keys()), ['flow'])

        # ok, valid referrer id and flow
        post_data = dict(flow=flow.id, referrer_id='signup')
        response = self.client.post(create_url, post_data)
        self.assertNoFormErrors(response)

        # assert our trigger was created
        first_trigger = Trigger.objects.get()
        self.assertEqual(first_trigger.trigger_type, Trigger.TYPE_REFERRAL)
        self.assertEqual(first_trigger.flow, flow)
        self.assertIsNone(first_trigger.channel)

        # empty referrer_id should create the trigger
        post_data = dict(flow=flow.id, referrer_id='')
        response = self.client.post(create_url, post_data)
        self.assertNoFormErrors(response)

        # try to create the same trigger, should fail as we can only have one per referrer
        post_data = dict(flow=flow.id, referrer_id='signup')
        response = self.client.post(create_url, post_data)
        self.assertEqual(list(response.context['form'].errors.keys()), ['__all__'])

        # should work if we specify a specific channel
        post_data['channel'] = self.fb_channel.id
        response = self.client.post(create_url, post_data)
        self.assertNoFormErrors(response)

        # load it
        second_trigger = Trigger.objects.get(channel=self.fb_channel)
        self.assertEqual(second_trigger.trigger_type, Trigger.TYPE_REFERRAL)
        self.assertEqual(second_trigger.flow, flow)

        # try updating it to a null channel
        update_url = reverse('triggers.trigger_update', args=[second_trigger.id])
        del post_data['channel']
        response = self.client.post(update_url, post_data)
        self.assertEqual(list(response.context['form'].errors.keys()), ['__all__'])

        # archive our first trigger
        Trigger.apply_action_archive(self.admin, Trigger.objects.filter(channel=None))

        # should now be able to update to a null channel
        response = self.client.post(update_url, post_data)
        self.assertNoFormErrors(response)
        second_trigger.refresh_from_db()

        self.assertIsNone(second_trigger.channel)

    def test_trigger_schedule(self):
        self.login(self.admin)
        flow = self.create_flow()

        chester = self.create_contact("Chester", "+250788987654")
        shinoda = self.create_contact("Shinoda", "+250234213455")
        linkin_park = self.create_group("Linkin Park", [chester, shinoda])
        stromae = self.create_contact("Stromae", "+250788645323")

        now = timezone.now()
        now_stamp = time.mktime(now.timetuple())

        tommorrow = now + timedelta(days=1)
        tommorrow_stamp = time.mktime(tommorrow.timetuple())

        post_data = dict()
        post_data['omnibox'] = "g-%s,c-%s" % (linkin_park.uuid, stromae.uuid)
        post_data['repeat_period'] = 'D'
        post_data['start'] = 'later'
        post_data['start_datetime_value'] = "%d" % tommorrow_stamp

        response = self.client.post(reverse("triggers.trigger_schedule"), post_data)
        self.assertEqual(list(response.context['form'].errors.keys()), ['flow'])
        self.assertFalse(Trigger.objects.all())
        self.assertFalse(Schedule.objects.all())

        # survey flows should not be an option
        flow.flow_type = Flow.SURVEY
        flow.save()
        response = self.client.get(reverse("triggers.trigger_schedule"))
        self.assertEqual(0, response.context['form'].fields['flow'].queryset.all().count())

        # back to normal flow type
        flow.flow_type = Flow.FLOW
        flow.save()
        self.assertEqual(1, response.context['form'].fields['flow'].queryset.all().count())

        post_data = dict()
        post_data['flow'] = flow.pk
        post_data['omnibox'] = "g-%s,c-%s" % (linkin_park.uuid, stromae.uuid)
        post_data['start'] = 'never'
        post_data['repeat_period'] = 'O'

        response = self.client.post(reverse("triggers.trigger_schedule"), post_data)
        self.assertEqual(1, Trigger.objects.all().count())

        trigger = Trigger.objects.all().order_by('-pk')[0]
        self.assertTrue(trigger.schedule)
        self.assertEqual(trigger.schedule.status, 'U')
        self.assertEqual(trigger.groups.all()[0].pk, linkin_park.pk)
        self.assertEqual(trigger.contacts.all()[0].pk, stromae.pk)

        post_data = dict()
        post_data['flow'] = flow.pk
        post_data['omnibox'] = "g-%s,c-%s" % (linkin_park.uuid, stromae.uuid)
        post_data['start'] = 'stop'
        post_data['repeat_period'] = 'O'

        response = self.client.post(reverse("triggers.trigger_schedule"), post_data)
        self.assertEqual(2, Trigger.objects.all().count())

        trigger = Trigger.objects.all().order_by('-pk')[0]
        self.assertTrue(trigger.schedule)
        self.assertEqual(trigger.schedule.status, 'U')
        self.assertEqual(trigger.groups.all()[0].pk, linkin_park.pk)
        self.assertEqual(trigger.contacts.all()[0].pk, stromae.pk)

        post_data = dict()
        post_data['flow'] = flow.pk
        post_data['omnibox'] = "g-%s,c-%s" % (linkin_park.uuid, stromae.uuid)
        post_data['repeat_period'] = 'O'
        post_data['start'] = 'now'
        post_data['start_datetime_value'] = "%d" % now_stamp

        response = self.client.post(reverse("triggers.trigger_schedule"), post_data)
        self.assertEqual(3, Trigger.objects.all().count())

        trigger = Trigger.objects.all().order_by('-pk')[0]
        self.assertTrue(trigger.schedule)
        self.assertFalse(trigger.schedule.next_fire)
        self.assertEqual(trigger.schedule.repeat_period, 'O')
        self.assertEqual(trigger.schedule.repeat_days, 0)
        self.assertEqual(trigger.groups.all()[0].pk, linkin_park.pk)
        self.assertEqual(trigger.contacts.all()[0].pk, stromae.pk)

        post_data = dict()
        post_data['flow'] = flow.pk
        post_data['omnibox'] = "g-%s,c-%s" % (linkin_park.uuid, stromae.uuid)
        post_data['repeat_period'] = 'D'
        post_data['start'] = 'later'
        post_data['start_datetime_value'] = "%d" % tommorrow_stamp

        response = self.client.post(reverse("triggers.trigger_schedule"), post_data)
        self.assertEqual(4, Trigger.objects.all().count())

        trigger = Trigger.objects.all().order_by('-pk')[0]
        self.assertTrue(trigger.schedule)
        self.assertEqual(trigger.schedule.repeat_period, 'D')
        self.assertEqual(trigger.groups.all()[0].pk, linkin_park.pk)
        self.assertEqual(trigger.contacts.all()[0].pk, stromae.pk)

        update_url = reverse('triggers.trigger_update', args=[trigger.pk])

        post_data = dict()
        post_data['omnibox'] = "g-%s,c-%s" % (linkin_park.uuid, stromae.uuid)
        post_data['repeat_period'] = 'O'
        post_data['start'] = 'now'
        post_data['start_datetime_value'] = "%d" % now_stamp

        response = self.client.post(update_url, post_data)
        self.assertEqual(list(response.context['form'].errors.keys()), ['flow'])

        post_data = dict()
        post_data['flow'] = flow.pk
        post_data['omnibox'] = "g-%s" % linkin_park.uuid
        post_data['repeat_period'] = 'O'
        post_data['start'] = 'now'
        post_data['start_datetime_value'] = "%d" % now_stamp

        response = self.client.post(update_url, post_data)

        trigger = Trigger.objects.get(pk=trigger.pk)
        self.assertTrue(trigger.schedule)
        self.assertEqual(trigger.schedule.repeat_period, 'O')
        self.assertFalse(trigger.schedule.next_fire)
        self.assertEqual(trigger.groups.all()[0].pk, linkin_park.pk)
        self.assertFalse(trigger.contacts.all())

        post_data = dict()
        post_data['flow'] = flow.pk
        post_data['omnibox'] = "g-%s,c-%s" % (linkin_park.uuid, stromae.uuid)
        post_data['start'] = 'never'
        post_data['repeat_period'] = 'O'

        response = self.client.post(update_url, post_data)

        trigger = Trigger.objects.get(pk=trigger.pk)
        self.assertTrue(trigger.schedule)
        self.assertEqual(trigger.schedule.status, 'U')
        self.assertEqual(trigger.groups.all()[0].pk, linkin_park.pk)
        self.assertEqual(trigger.contacts.all()[0].pk, stromae.pk)

        post_data = dict()
        post_data['flow'] = flow.pk
        post_data['omnibox'] = "g-%s,c-%s" % (linkin_park.uuid, stromae.uuid)
        post_data['start'] = 'stop'
        post_data['repeat_period'] = 'O'

        response = self.client.post(update_url, post_data)

        trigger = Trigger.objects.get(pk=trigger.pk)
        self.assertTrue(trigger.schedule)
        self.assertEqual(trigger.schedule.status, 'U')
        self.assertEqual(trigger.groups.all()[0].pk, linkin_park.pk)
        self.assertEqual(trigger.contacts.all()[0].pk, stromae.pk)

        post_data = dict()
        post_data['flow'] = flow.pk
        post_data['omnibox'] = "g-%s,c-%s" % (linkin_park.uuid, stromae.uuid)
        post_data['repeat_period'] = 'D'
        post_data['start'] = 'later'
        post_data['start_datetime_value'] = "%d" % tommorrow_stamp

        response = self.client.post(update_url, post_data)

        trigger = Trigger.objects.get(pk=trigger.pk)

        self.assertTrue(trigger.schedule)
        self.assertEqual(trigger.schedule.repeat_period, 'D')
        self.assertEqual(trigger.groups.all()[0].pk, linkin_park.pk)
        self.assertEqual(trigger.contacts.all()[0].pk, stromae.pk)

    def test_join_group_trigger(self):
        self.login(self.admin)
        group = self.create_group(name='Chat', contacts=[])

        favorites = self.get_flow('favorites')

        # create a trigger that sets up a group join flow
        post_data = dict(keyword='join', action_join_group=group.pk, response='Thanks for joining', flow=favorites.pk)
        self.client.post(reverse("triggers.trigger_register"), data=post_data)

        # did our group join flow get created?
        flow = Flow.objects.get(flow_type=Flow.FLOW, name='Join Chat')

        # check that our trigger exists and shows our group
        trigger = Trigger.objects.get(keyword='join', flow=flow)
        self.assertEqual(trigger.flow.name, 'Join Chat')

        # the org has no language, so it should be a 'base' flow
        self.assertEqual(flow.base_language, 'base')

        # now let's try it out
        contact = self.create_contact('macklemore', '+250788382382')
        msg = self.create_msg(direction=INCOMING, contact=contact, text="join ben haggerty")
        self.assertIsNone(msg.msg_type)

        self.assertTrue(Trigger.find_and_handle(msg))

        self.assertEqual(msg.msg_type, 'F')

        contact.refresh_from_db()
        self.assertEqual('Ben Haggerty', contact.name)

        # we should be in the group now
        self.assertEqual({group}, set(contact.user_groups.all()))

        # and have one incoming and one outgoing message plus an outgoing from our favorites flow
        self.assertEqual(3, contact.msgs.count())

        # deleting our contact group should leave our triggers and flows since the group can be recreated
        self.client.post(reverse("contacts.contactgroup_delete", args=[group.pk]))
        self.assertTrue(Trigger.objects.get(pk=trigger.pk).is_active)

        # try creating a join group on an org with a language
        language = Language.create(self.org, self.admin, "Klingon", 'kli')
        self.org.primary_language = language
        self.org.save()

        # now create another group trigger
        group = self.create_group(name='Lang Group', contacts=[])
        post_data = dict(keyword='join_lang', action_join_group=group.pk, response='Thanks for joining')
        response = self.client.post(reverse("triggers.trigger_register"), data=post_data)
        self.assertEqual(response.status_code, 200)

        # confirm our objects
        flow = Flow.objects.filter(flow_type=Flow.FLOW).order_by('-pk').first()
        trigger = Trigger.objects.get(keyword='join_lang', flow=flow)
        self.assertEqual(trigger.flow.name, 'Join Lang Group')

        # the flow should be created with the primary language for the org
        self.assertEqual(flow.base_language, 'kli')

    def test_trigger_form(self):

        for form in (DefaultTriggerForm, RegisterTriggerForm):

            trigger_form = form(self.admin)
            pick = self.get_flow('pick_a_number')
            favorites = self.get_flow('favorites')
            self.assertEqual(2, trigger_form.fields['flow'].choices.queryset.all().count())

            # now change to a single message type
            pick.flow_type = Flow.MESSAGE
            pick.save()

            # our flow should no longer be an option
            trigger_form = form(self.admin)
            choices = trigger_form.fields['flow'].choices
            self.assertEqual(1, choices.queryset.all().count())
            self.assertIsNone(choices.queryset.filter(pk=pick.pk).first())

            pick.delete()
            favorites.delete()

    def test_unicode_trigger(self):
        self.login(self.admin)
        group = self.create_group(name='Chat', contacts=[])

        # no keyword must show validation error
        post_data = dict(action_join_group=group.pk, keyword='@#$')
        response = self.client.post(reverse("triggers.trigger_register"), data=post_data)
        self.assertEqual(1, len(response.context['form'].errors))

        # create a trigger that sets up a group join flow
        post_data = dict(action_join_group=group.pk, keyword=u'١٠٠')
        self.client.post(reverse("triggers.trigger_register"), data=post_data)

        # did our group join flow get created?
        Flow.objects.get(flow_type=Flow.FLOW)

        # now let's try it out
        contact = self.create_contact('Ben', '+250788382382')
        msg = self.create_msg(direction=INCOMING, contact=contact, text=u'١٠٠ join group')
        self.assertIsNone(msg.msg_type)

        self.assertTrue(Trigger.find_and_handle(msg))

        # we should be in the group now
        self.assertEqual(msg.msg_type, 'F')
        self.assertEqual(set(contact.user_groups.all()), {group})

    def test_join_group_no_response(self):

        self.login(self.admin)
        group = self.create_group(name='Chat', contacts=[])

        # create a trigger that sets up a group join flow
        post_data = dict(action_join_group=group.pk, keyword='join')
        self.client.post(reverse("triggers.trigger_register"), data=post_data)

        # did our group join flow get created?
        flow = Flow.objects.get(flow_type=Flow.FLOW)

        # check that our trigger exists and shows our group
        trigger = Trigger.objects.get(keyword='join', flow=flow)
        self.assertEqual("Join Chat", trigger.flow.name)

        # now let's try it out
        contact = self.create_contact('Ben', '+250788382382')
        msg = self.create_msg(direction=INCOMING, contact=contact, text="join")
        self.assertIsNone(msg.msg_type)

        self.assertTrue(Trigger.find_and_handle(msg))

        # we should be in the group now
        self.assertEqual(msg.msg_type, 'F')
        self.assertEqual(set(contact.user_groups.all()), {group})

    def test_missed_call_trigger(self):
        self.login(self.admin)
        missed_call_trigger = Trigger.get_triggers_of_type(self.org, Trigger.TYPE_MISSED_CALL).first()
        flow = self.create_flow()
        contact = self.create_contact("Ali", "250788739305")

        self.assertFalse(missed_call_trigger)

        ChannelEvent.create(self.channel, six.text_type(contact.get_urn(TEL_SCHEME)), ChannelEvent.TYPE_CALL_IN_MISSED, timezone.now(), {})
        self.assertEqual(ChannelEvent.objects.all().count(), 1)
        self.assertEqual(flow.runs.all().count(), 0)

        trigger_url = reverse("triggers.trigger_missed_call")

        response = self.client.get(trigger_url)
        self.assertEqual(response.status_code, 200)

        post_data = dict(flow=flow.pk)

        response = self.client.post(trigger_url, post_data)
        trigger = Trigger.objects.all().order_by('-pk')[0]

        self.assertEqual(trigger.trigger_type, Trigger.TYPE_MISSED_CALL)
        self.assertEqual(trigger.flow.pk, flow.pk)

        missed_call_trigger = Trigger.get_triggers_of_type(self.org, Trigger.TYPE_MISSED_CALL).first()

        self.assertEqual(missed_call_trigger.pk, trigger.pk)

        ChannelEvent.create(self.channel, six.text_type(contact.get_urn(TEL_SCHEME)), ChannelEvent.TYPE_CALL_IN_MISSED, timezone.now(), {})
        self.assertEqual(ChannelEvent.objects.all().count(), 2)
        self.assertEqual(flow.runs.all().count(), 1)
        self.assertEqual(flow.runs.all()[0].contact.pk, contact.pk)

        other_flow = Flow.copy(flow, self.admin)
        post_data = dict(flow=other_flow.pk)

        response = self.client.post(reverse("triggers.trigger_update", args=[trigger.pk]), post_data)
        trigger = Trigger.objects.get(pk=trigger.pk)
        self.assertEqual(trigger.flow.pk, other_flow.pk)

        # create ten missed call triggers
        for i in range(10):
            response = self.client.get(trigger_url)
            self.assertEqual(response.status_code, 200)

            post_data = dict(flow=flow.pk)

            response = self.client.post(trigger_url, post_data)
            self.assertEqual(i + 2, Trigger.objects.all().count())
            self.assertEqual(1, Trigger.objects.filter(is_archived=False, trigger_type=Trigger.TYPE_MISSED_CALL).count())

        # even unarchiving we only have one acive trigger at a time
        triggers = Trigger.objects.filter(trigger_type=Trigger.TYPE_MISSED_CALL, is_archived=True)
        active_trigger = Trigger.objects.get(trigger_type=Trigger.TYPE_MISSED_CALL, is_archived=False)

        post_data = dict()
        post_data['action'] = 'restore'
        post_data['objects'] = [t.pk for t in triggers]

        response = self.client.post(reverse("triggers.trigger_archived"), post_data)
        self.assertEqual(1, Trigger.objects.filter(is_archived=False, trigger_type=Trigger.TYPE_MISSED_CALL).count())
        self.assertFalse(active_trigger.pk == Trigger.objects.filter(is_archived=False, trigger_type=Trigger.TYPE_MISSED_CALL)[0].pk)

    def test_new_conversation_trigger_viber(self):
        self.login(self.admin)
        flow = self.create_flow()
        flow2 = self.create_flow()

        # see if we list new conversation triggers on the trigger page
        create_trigger_url = reverse('triggers.trigger_create', args=[])
        response = self.client.get(create_trigger_url)
        self.assertNotContains(response, "conversation is started")

        # add a viber public channel
        viber_channel = Channel.create(self.org, self.user, None, 'VP', None, '1001',
                                       uuid='00000000-0000-0000-0000-000000001234',
                                       config={Channel.CONFIG_AUTH_TOKEN: "auth_token"})

        # should now be able to create one
        response = self.client.get(create_trigger_url)
        self.assertContains(response, "conversation is started")

        response = self.client.get(reverse('triggers.trigger_new_conversation', args=[]))
        self.assertEqual(response.context['form'].fields['channel'].queryset.count(), 1)
        self.assertTrue(viber_channel in response.context['form'].fields['channel'].queryset.all())

        # create a facebook channel
        fb_channel = Channel.create(self.org, self.user, None, 'FB', address='1001',
                                    config={'page_name': "Temba", 'auth_token': 'fb_token'})

        response = self.client.get(reverse('triggers.trigger_new_conversation', args=[]))
        self.assertEqual(response.context['form'].fields['channel'].queryset.count(), 2)
        self.assertTrue(viber_channel in response.context['form'].fields['channel'].queryset.all())
        self.assertTrue(fb_channel in response.context['form'].fields['channel'].queryset.all())

        response = self.client.post(reverse('triggers.trigger_new_conversation', args=[]),
                                    data=dict(channel=viber_channel.id, flow=flow.id))
        self.assertEqual(response.status_code, 200)

        trigger = Trigger.objects.get(trigger_type=Trigger.TYPE_NEW_CONVERSATION, is_active=True, is_archived=False)
        self.assertEqual(trigger.channel, viber_channel)
        self.assertEqual(trigger.flow, flow)

        # try to create another one, fails as we already have a trigger for that channel
        response = self.client.post(reverse('triggers.trigger_new_conversation', args=[]),
                                    data=dict(channel=viber_channel.id, flow=flow2.id))
        self.assertEqual(response.status_code, 200)
        self.assertFormError(response, 'form', 'channel', 'Trigger with this Channel already exists.')

        # try to change the existing trigger
        response = self.client.post(reverse('triggers.trigger_update', args=[trigger.id]),
                                    data=dict(id=trigger.id, flow=flow2.id, channel=viber_channel.id),
                                    follow=True)
        self.assertEqual(response.status_code, 200)

        trigger.refresh_from_db()
        self.assertEqual(flow2, trigger.flow)
        self.assertEqual(viber_channel, trigger.channel)

    @override_settings(IS_PROD=True)
    @patch('requests.post')
    def test_new_conversation_trigger(self, mock_post):
        self.login(self.admin)
        flow = self.create_flow()
        flow2 = self.create_flow()

        # see if we list new conversation triggers on the trigger page
        create_trigger_url = reverse('triggers.trigger_create', args=[])
        response = self.client.get(create_trigger_url)
        self.assertNotContains(response, "conversation is started")

        # create a facebook channel
        fb_channel = Channel.create(self.org, self.user, None, 'FB', address='1001',
                                    config={'page_name': "Temba", 'auth_token': 'fb_token'})

        # should now be able to create one
        response = self.client.get(create_trigger_url)
        self.assertContains(response, "conversation is started")

        # go create it
        mock_post.return_value = MockResponse(200, '{"message": "Success"}')

        response = self.client.post(reverse('triggers.trigger_new_conversation', args=[]),
                                    data=dict(channel=fb_channel.id, flow=flow.id))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(mock_post.call_count, 1)

        # check that it is right
        trigger = Trigger.objects.get(trigger_type=Trigger.TYPE_NEW_CONVERSATION, is_active=True, is_archived=False)
        self.assertEqual(trigger.channel, fb_channel)
        self.assertEqual(trigger.flow, flow)

        # try to create another one, fails as we already have a trigger for that channel
        response = self.client.post(reverse('triggers.trigger_new_conversation', args=[]), data=dict(channel=fb_channel.id, flow=flow2.id))
        self.assertEqual(response.status_code, 200)
        self.assertFormError(response, 'form', 'channel', 'Trigger with this Channel already exists.')

        # ok, trigger a facebook event
        data = json.loads("""{
        "object": "page",
          "entry": [
            {
              "id": "620308107999975",
              "time": 1467841778778,
              "messaging": [
                {
                  "sender":{
                    "id":"1001"
                  },
                  "recipient":{
                    "id":"%s"
                  },
                  "timestamp":1458692752478,
                  "postback":{
                    "payload":"get_started"
                  }
                }
              ]
            }
          ]
        }
        """ % fb_channel.address)

        with patch('requests.get') as mock_get:
            mock_get.return_value = MockResponse(200, '{"first_name": "Ben","last_name": "Haggerty"}')

            callback_url = reverse('handlers.facebook_handler', args=[fb_channel.uuid])
            response = self.client.post(callback_url, json.dumps(data), content_type="application/json")
            self.assertEqual(response.status_code, 200)

            # should have a new flow run for Ben
            contact = Contact.from_urn(self.org, 'facebook:1001')
            self.assertTrue(contact.name, "Ben Haggerty")

            # and a new channel event for the conversation
            self.assertTrue(ChannelEvent.objects.filter(channel=fb_channel, contact=contact,
                                                        event_type=ChannelEvent.TYPE_NEW_CONVERSATION))

            run = FlowRun.objects.get(contact=contact)
            self.assertEqual(run.flow, flow)

        # archive our trigger, should unregister our callback
        with patch('requests.post') as mock_post:
            mock_post.return_value = MockResponse(200, '{"message": "Success"}')

            Trigger.apply_action_archive(self.admin, Trigger.objects.filter(pk=trigger.pk))
            self.assertEqual(response.status_code, 200)
            self.assertEqual(mock_post.call_count, 1)

            trigger.refresh_from_db()
            self.assertTrue(trigger.is_archived)

    def test_catch_all_trigger(self):
        self.login(self.admin)
        catch_all_trigger = Trigger.get_triggers_of_type(self.org, Trigger.TYPE_CATCH_ALL).first()
        flow = self.get_flow('color')

        contact = self.create_contact("Ali", "250788739305")

        # make our first message echo back the original message
        action_set = ActionSet.objects.get(uuid=flow.entry_uuid)
        actions = action_set.as_json()['actions']
        actions[0]['msg']['base'] = 'Echo: @step.value'
        action_set.actions = actions
        action_set.save()

        self.assertFalse(catch_all_trigger)

        Msg.create_incoming(self.channel, six.text_type(contact.get_urn()), "Hi")
        self.assertEqual(1, Msg.objects.all().count())
        self.assertEqual(0, flow.runs.all().count())

        trigger_url = reverse("triggers.trigger_catchall")

        response = self.client.get(trigger_url)
        self.assertEqual(response.status_code, 200)

        post_data = dict(flow=flow.pk)

        response = self.client.post(trigger_url, post_data)
        trigger = Trigger.objects.all().order_by('-pk')[0]

        self.assertEqual(trigger.trigger_type, Trigger.TYPE_CATCH_ALL)
        self.assertEqual(trigger.flow.pk, flow.pk)

        catch_all_trigger = Trigger.get_triggers_of_type(self.org, Trigger.TYPE_CATCH_ALL).first()

        self.assertEqual(catch_all_trigger.pk, trigger.pk)

        incoming = Msg.create_incoming(self.channel, six.text_type(contact.get_urn()), "Hi")
        self.assertEqual(1, flow.runs.all().count())
        self.assertEqual(flow.runs.all()[0].contact.pk, contact.pk)
        reply = Msg.objects.get(response_to=incoming)
        self.assertEqual('Echo: Hi', reply.text)

        other_flow = Flow.copy(flow, self.admin)
        post_data = dict(flow=other_flow.pk)

        self.client.post(reverse("triggers.trigger_update", args=[trigger.pk]), post_data)
        trigger = Trigger.objects.get(pk=trigger.pk)
        self.assertEqual(trigger.flow.pk, other_flow.pk)

        # try to create another catch all trigger
        response = self.client.post(trigger_url, post_data)

        # shouldn't have succeeded as we already have a catch-all trigger
        self.assertTrue(len(response.context['form'].errors))

        # archive the previous one
        trigger.is_archived = True
        trigger.save()
        old_catch_all = trigger

        # try again
        self.client.post(trigger_url, post_data)

        # this time we are a go
        new_catch_all = Trigger.objects.get(is_archived=False, trigger_type=Trigger.TYPE_CATCH_ALL)

        # now add a new trigger based on a group
        group = self.create_group("Trigger Group", [])
        post_data['groups'] = [group.pk]
        response = self.client.post(trigger_url, post_data)

        # should now have two catch all triggers
        self.assertEqual(2, Trigger.objects.filter(is_archived=False, trigger_type=Trigger.TYPE_CATCH_ALL).count())

        group_catch_all = Trigger.objects.get(is_archived=False, trigger_type=Trigger.TYPE_CATCH_ALL, groups=group)

        # try to add another catchall trigger with a few different groups
        group2 = self.create_group("Trigger Group 2", [])
        post_data['groups'] = [group.pk, group2.pk]
        response = self.client.post(trigger_url, post_data)

        # should have failed
        self.assertTrue(len(response.context['form'].errors))

        post_data = dict()
        post_data['action'] = 'restore'
        post_data['objects'] = [old_catch_all.pk]

        response = self.client.post(reverse("triggers.trigger_archived"), post_data)
        old_catch_all.refresh_from_db()
        new_catch_all.refresh_from_db()

        # our new triggers should have been auto-archived, our old one is now active
        self.assertEqual(2, Trigger.objects.filter(is_archived=False, trigger_type=Trigger.TYPE_CATCH_ALL).count())
        self.assertTrue(new_catch_all.is_archived)
        self.assertFalse(old_catch_all.is_archived)

        # ok, archive our old one too, leaving only our group specific trigger
        old_catch_all.is_archived = True
        old_catch_all.save()

        # try a message again, this shouldn't cause anything since the contact isn't part of our group
        FlowRun.objects.all().delete()
        Msg.objects.all().delete()

        incoming = Msg.create_incoming(self.channel, six.text_type(contact.get_urn()), "Hi")
        self.assertEqual(0, FlowRun.objects.all().count())
        self.assertFalse(Msg.objects.filter(response_to=incoming))

        # now add the contact to the group
        group.contacts.add(contact)

        # this time should trigger the flow
        incoming = Msg.create_incoming(self.channel, six.text_type(contact.get_urn()), "Hi")
        self.assertEqual(1, FlowRun.objects.all().count())
        self.assertEqual(other_flow.runs.all()[0].contact.pk, contact.pk)
        reply = Msg.objects.get(response_to=incoming)
        self.assertEqual('Echo: Hi', reply.text)

        # delete the group
        group.release()

        # trigger should no longer be active
        group_catch_all.refresh_from_db()
        self.assertFalse(group_catch_all.is_active)

    def test_update(self):
        self.login(self.admin)
        group = self.create_group(name='Chat', contacts=[])

        # create a trigger that sets up a group join flow
        post_data = dict(action_join_group=group.pk, keyword='join')
        self.client.post(reverse("triggers.trigger_register"), data=post_data)

        # did our group join flow get created?
        flow = Flow.objects.get(flow_type=Flow.FLOW)

        # check that our trigger exists and shows our group
        trigger = Trigger.objects.get(keyword='join', flow=flow)
        update_url = reverse('triggers.trigger_update', args=[trigger.pk])

        response = self.client.get(update_url)
        self.assertEqual(response.status_code, 200)

        # test trigger for Flow of flow_type of FLOW
        flow = self.create_flow()

        # a valid keyword
        post_data = dict(keyword='kiki', flow=flow.id, match_type='F')
        self.client.post(reverse("triggers.trigger_keyword"), data=post_data)
        trigger = Trigger.objects.get(keyword='kiki')
        self.assertEqual(flow.pk, trigger.flow.pk)

        update_url = reverse('triggers.trigger_update', args=[trigger.pk])

        response = self.client.get(update_url)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.context['form'].fields), 5)

        group = self.create_group("first", [])

        # show validation error if keyword is None or not defined
        post_data = dict(flow=flow.id, match_type='O', groups=[group.id])
        response = self.client.post(update_url, post_data, follow=True)
        self.assertEqual(1, len(response.context['form'].errors))

        post_data = dict(keyword='koko', flow=flow.id, match_type='O', groups=[group.id])
        self.client.post(update_url, post_data, follow=True)

        trigger.refresh_from_db()
        self.assertEqual(trigger.keyword, 'koko')
        self.assertEqual(trigger.match_type, Trigger.MATCH_ONLY_WORD)
        self.assertEqual(trigger.flow, flow)
        self.assertTrue(group in trigger.groups.all())

    def test_trigger_handle(self):
        self.contact = self.create_contact('Eric', '+250788382382')
        self.contact2 = self.create_contact('Nic', '+250788383383')

        # create an incoming message with no text
        incoming = self.create_msg(direction=INCOMING, contact=self.contact, text="")

        # check not handled
        self.assertFalse(Trigger.find_and_handle(incoming))

        incoming = self.create_msg(direction=INCOMING, contact=self.contact, text="some text")

        # check not handled (no trigger or flow)
        self.assertFalse(Trigger.find_and_handle(incoming))

        # setup a flow and keyword trigger
        flow = self.create_flow()
        trigger = Trigger.objects.create(org=self.org, keyword='when', flow=flow,
                                         created_by=self.admin, modified_by=self.admin)

        incoming = self.create_msg(direction=INCOMING, contact=self.contact, text="when is it?")

        # check message was handled
        self.assertTrue(Trigger.find_and_handle(incoming))

        # should also have a flow run
        run = FlowRun.objects.get()
        self.assertTrue(run.responded)

        # change match type to 'only'
        trigger.match_type = Trigger.MATCH_ONLY_WORD
        trigger.save()

        # check message is not handled
        incoming = self.create_msg(direction=INCOMING, contact=self.contact, text="when and where?")
        self.assertFalse(Trigger.find_and_handle(incoming))

        incoming = self.create_msg(direction=INCOMING, contact=self.contact, text="  WHEN! ")
        self.assertTrue(Trigger.find_and_handle(incoming))

        incoming = self.create_msg(direction=INCOMING, contact=self.contact, text="\WHEN")
        self.assertTrue(Trigger.find_and_handle(incoming))

        # change match type back to 'first'
        trigger.match_type = Trigger.MATCH_FIRST_WORD
        trigger.save()

        # test that trigger unstops contact if needed
        self.contact.stop(self.admin)

        self.contact.refresh_from_db()
        self.assertTrue(self.contact.is_stopped)

        incoming = self.create_msg(direction=INCOMING, contact=self.contact, text="when is it?")
        self.assertTrue(Trigger.find_and_handle(incoming))

        self.contact.refresh_from_db()
        self.assertFalse(self.contact.is_stopped)
        self.assertEqual(FlowRun.objects.all().count(), 4)

        # create trigger for specific contact group
        group = self.create_group("first", [self.contact2])
        trigger = Trigger.objects.create(org=self.org, keyword='where', flow=flow,
                                         created_by=self.admin, modified_by=self.admin)
        trigger.groups.add(group)

        incoming = self.create_msg(direction=INCOMING, contact=self.contact, text="where do you go?")

        # check not handled (contact not in the group)
        self.assertFalse(Trigger.find_and_handle(incoming))

        incoming2 = self.create_msg(direction=INCOMING, contact=self.contact2, text="where do I find it?")

        # check was handled (this contact is in the group)
        self.assertTrue(Trigger.find_and_handle(incoming2))

    def test_trigger_handle_priority(self):

        self.contact = self.create_contact('Eric', '+250788382382')
        self.contact2 = self.create_contact('Nic', '+250788383383')
        self.contact3 = self.create_contact('Rowan', "+250788654321")
        self.contact4 = self.create_contact('Norbert', "+250788765432")

        group1 = self.create_group("first", [self.contact2, self.contact3])
        group2 = self.create_group("Klab", [self.contact2, self.contact4])

        flow1 = self.create_flow()
        flow2 = self.create_flow()
        flow3 = self.create_flow()

        keyword = 'unique'

        # no group trigger
        Trigger.objects.create(org=self.org, keyword=keyword, flow=flow1,
                               created_by=self.admin, modified_by=self.admin)

        # group1 trigger
        trigger2 = Trigger.objects.create(org=self.org, keyword=keyword, flow=flow2,
                                          created_by=self.admin, modified_by=self.admin)

        trigger2.groups.add(group1)

        # group2 trigger
        trigger3 = Trigger.objects.create(org=self.org, keyword=keyword, flow=flow3,
                                          created_by=self.admin, modified_by=self.admin)

        trigger3.groups.add(group2)

        incoming1 = self.create_msg(direction=INCOMING, contact=self.contact, text="unique is the keyword send")

        # incoming1 should be handled and in flow1
        self.assertTrue(Trigger.find_and_handle(incoming1))
        self.assertTrue(FlowRun.objects.filter(contact=self.contact)[0].flow.pk, flow1.pk)

        incoming2 = self.create_msg(direction=INCOMING, contact=self.contact2, text="unique is the keyword send")

        # incoming2 should be handled and in flow3
        self.assertTrue(Trigger.find_and_handle(incoming2))
        self.assertTrue(FlowRun.objects.filter(contact=self.contact2)[0].flow.pk, flow3.pk)

        incoming3 = self.create_msg(direction=INCOMING, contact=self.contact3, text="unique is the keyword send")

        # incoming2 should be handled and in flow2
        self.assertTrue(Trigger.find_and_handle(incoming3))
        self.assertTrue(FlowRun.objects.filter(contact=self.contact3)[0].flow.pk, flow2.pk)

        incoming4 = self.create_msg(direction=INCOMING, contact=self.contact4, text="other is the keyword send")

        # incoming4 should not be handled
        self.assertFalse(Trigger.find_and_handle(incoming4))

    def test_export_import(self):
        # tweak our current channel to be twitter so we can create a channel-based trigger
        Channel.objects.filter(id=self.channel.id).update(channel_type='TT')
        flow = self.create_flow()

        group = self.create_group("Trigger Group", [])

        # create a trigger on this flow for the follow actions but only on some groups
        trigger = Trigger.objects.create(org=self.org, flow=flow, trigger_type=Trigger.TYPE_FOLLOW, channel=self.channel,
                                         created_by=self.admin, modified_by=self.admin)
        trigger.groups.add(group)

        components = self.org.resolve_dependencies([flow], [], include_triggers=True)

        # export everything
        export = self.org.export_definitions('http://rapidpro.io', components)

        # remove our trigger
        Trigger.objects.all().delete()

        # and reimport them.. trigger should be recreated
        self.org.import_app(export, self.admin)

        trigger = Trigger.objects.get()
        self.assertEqual(trigger.trigger_type, Trigger.TYPE_FOLLOW)
        self.assertEqual(trigger.flow, flow)
        self.assertEqual(trigger.channel, self.channel)
        self.assertEqual(list(trigger.groups.all()), [group])

    @patch('temba.orgs.models.Org.get_ussd_channels')
    def test_ussd_trigger(self, get_ussd_channels):
        self.login(self.admin)

        flow = self.get_flow('ussd_example')

        # check if we have ussd section
        get_ussd_channels.return_value = True
        response = self.client.get(reverse('triggers.trigger_create'))

        self.assertTrue(get_ussd_channels.called)
        self.assertContains(response, 'USSD mobile initiated flow')

        channel = Channel.add_config_external_channel(self.org, self.user,
                                                      "HU", 1234, 'JNU',
                                                      dict(account_key="11111",
                                                           access_token=str(uuid4()),
                                                           transport_name="ussd_transport",
                                                           conversation_key="22222"),
                                                      role=Channel.ROLE_USSD)

        # flow options should show ussd flow example
        response = self.client.get(reverse("triggers.trigger_ussd"))
        self.assertContains(response, flow.name)

        # try a ussd code with letters instead of numbers
        post_data = dict(channel=channel.pk, keyword='*keyword#', flow=flow.pk)
        response = self.client.post(reverse("triggers.trigger_ussd"), data=post_data)
        self.assertEqual(1, len(response.context['form'].errors))
        self.assertIn("keyword", response.context['form'].errors)
        self.assertEqual(response.context['form'].errors['keyword'], [u'USSD code must contain only *,# and numbers'])

        # try a proper ussd code
        post_data = dict(channel=channel.pk, keyword='*111#', flow=flow.pk)
        response = self.client.post(reverse("triggers.trigger_ussd"), data=post_data)
        self.assertEqual(0, len(response.context['form'].errors))
        trigger = Trigger.objects.get(keyword='*111#')
        self.assertEqual(flow.pk, trigger.flow.pk)

        # try a duplicate ussd code
        post_data = dict(channel=channel.pk, keyword='*111#', flow=flow.pk)
        response = self.client.post(reverse("triggers.trigger_ussd"), data=post_data)
        self.assertEqual(1, len(response.context['form'].errors))
        self.assertEqual(response.context['form'].errors['__all__'],
                         [u'An active trigger already exists, triggers must be unique for each group'])

        # different code on same channel should work
        post_data = dict(channel=channel.pk, keyword='*112#', flow=flow.pk)
        response = self.client.post(reverse("triggers.trigger_ussd"), data=post_data)
        self.assertNoFormErrors(response)
        trigger = Trigger.objects.get(keyword='*112#')

        # try a second ussd code with the same channel
        # TODO: fix this with multichannel triggers
        # post_data = dict(channel=channel.pk, keyword='*112#', flow=flow.pk)
        # response = self.client.post(reverse("triggers.trigger_ussd"), data=post_data)
        # self.assertEqual(0, len(response.context['form'].errors))
        # self.assertEqual(2, Trigger.objects.count())
        # trigger = Trigger.objects.get(keyword='*112#')
        # self.assertEqual(flow.pk, trigger.flow.pk)
