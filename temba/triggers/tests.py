# -*- coding: utf-8 -*-

from __future__ import unicode_literals

import time

from datetime import timedelta
from django.core.urlresolvers import reverse
from django.utils import timezone
from temba.orgs.models import Language
from temba.contacts.models import TEL_SCHEME
from temba.flows.models import Flow, ActionSet, FlowRun
from temba.schedules.models import Schedule
from temba.msgs.models import Msg, INCOMING, Call
from temba.channels.models import SEND, CALL, ANSWER, RECEIVE
from temba.tests import TembaTest
from .models import Trigger
from temba.triggers.views import DefaultTriggerForm, RegisterTriggerForm


class TriggerTest(TembaTest):

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
        post_data = dict(keyword='keyword with spaces', flow=flow.pk)
        response = self.client.post(reverse("triggers.trigger_keyword"), data=post_data)
        self.assertEquals(1, len(response.context['form'].errors))

        # try a keyword with special characters
        post_data = dict(keyword='keyw!o^rd__', flow=flow.pk)
        response = self.client.post(reverse("triggers.trigger_keyword"), data=post_data)
        self.assertEquals(1, len(response.context['form'].errors))

        # unicode keyword (Arabic)
        post_data = dict(keyword='١٠٠', flow=flow.pk)
        self.client.post(reverse("triggers.trigger_keyword"), data=post_data)
        trigger = Trigger.objects.get(keyword=u'١٠٠')
        self.assertEquals(flow.pk, trigger.flow.pk)

        # unicode keyword (Hindi)
        post_data = dict(keyword='मिलाए', flow=flow.pk)
        self.client.post(reverse("triggers.trigger_keyword"), data=post_data)
        trigger = Trigger.objects.get(keyword=u'मिलाए')
        self.assertEquals(flow.pk, trigger.flow.pk)

        # a valid keyword
        post_data = dict(keyword='startkeyword', flow=flow.pk)
        self.client.post(reverse("triggers.trigger_keyword"), data=post_data)
        trigger = Trigger.objects.get(keyword='startkeyword')
        self.assertEquals(flow.pk, trigger.flow.pk)

        # try a duplicate keyword
        post_data = dict(keyword='startkeyword', flow=flow.pk)
        response = self.client.post(reverse("triggers.trigger_keyword"), data=post_data)
        self.assertEquals(1, len(response.context['form'].errors))

        # see our trigger on the list page
        response = self.client.get(reverse('triggers.trigger_list'))
        self.assertContains(response, 'startkeyword')

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

        post_data = dict(keyword='startkeyword', flow=flow.pk)
        response = self.client.post(reverse("triggers.trigger_keyword"), data=post_data)
        self.assertEquals(Trigger.objects.filter(keyword="startkeyword").count(), 2)
        self.assertEquals(1, Trigger.objects.filter(keyword="startkeyword", is_archived=False).count())
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
        self.assertEquals(1, Trigger.objects.filter(keyword="startkeyword", is_archived=False).count())
        self.assertFalse(other_trigger.pk == Trigger.objects.filter(keyword="startkeyword", is_archived=False)[0].pk)


        self.contact = self.create_contact('Eric', '+250788382382')
        self.contact2 = self.create_contact('Nic', '+250788383383')
        group1 = self.create_group("first", [self.contact2])
        group2 = self.create_group("second", [self.contact])
        group3 = self.create_group("third", [self.contact, self.contact2])

        self.assertEquals(Trigger.objects.filter(keyword="startkeyword").count(), 2)
        self.assertEquals(Trigger.objects.filter(keyword="startkeyword", is_archived=False).count(), 1)

        # update trigger with 2 groups
        post_data = dict(keyword='startkeyword', flow=flow.pk, groups=[group1.pk, group2.pk])
        response = self.client.post(reverse("triggers.trigger_keyword"), data=post_data)
        self.assertEquals(Trigger.objects.filter(keyword="startkeyword").count(), 3)
        self.assertEquals(Trigger.objects.filter(keyword="startkeyword", is_archived=False).count(), 2)

        # get error when groups overlap
        post_data = dict(keyword='startkeyword', flow=flow.pk)
        post_data['groups'] = [group2.pk, group3.pk]
        response = self.client.post(reverse("triggers.trigger_keyword"), data=post_data)
        self.assertEquals(1, len(response.context['form'].errors))
        self.assertEquals(Trigger.objects.filter(keyword="startkeyword").count(), 3)
        self.assertEquals(Trigger.objects.filter(keyword="startkeyword", is_archived=False).count(), 2)

        # allow new creation when groups do not overlap
        post_data = dict(keyword='startkeyword', flow=flow.pk)
        post_data['groups'] = [group3.pk]
        response = self.client.post(reverse("triggers.trigger_keyword"), data=post_data)
        self.assertEquals(Trigger.objects.filter(keyword="startkeyword").count(), 4)
        self.assertEquals(Trigger.objects.filter(keyword="startkeyword", is_archived=False).count(), 3)

    def test_inbound_call_trigger(self):
        self.login(self.admin)

        # shouldn't see an option for inbound call triggers without a answer channel
        response = self.client.get(reverse('triggers.trigger_create'))
        self.assertNotContains(response, 'Start a flow after receiving a call')

        # make our channel support ivr
        self.channel.role += CALL+ANSWER
        self.channel.save()

        response = self.client.get(reverse('triggers.trigger_create'))
        self.assertContains(response, 'Start a flow after receiving a call')

        # flow is required
        response = self.client.post(reverse('triggers.trigger_inbound_call'), dict())
        self.assertEquals(response.context['form'].errors.keys(), ['flow'])

        # flow must be an ivr flow
        message_flow = self.create_flow()
        post_data = dict(flow=message_flow.pk)
        response = self.client.post(reverse('triggers.trigger_inbound_call'), post_data)
        self.assertEquals(response.context['form'].errors.keys(), ['flow'])

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
        self.assertEquals(guitarist_flow.pk, Trigger.find_flow_for_inbound_call(trey).pk)

        # now lets check that group specific call triggers work
        mike = self.create_contact('Mike', '+17075551213')
        bassists = self.create_group('Bassists', [mike])

        # flow specific to our group
        bassist_flow = self.create_flow()
        bassist_flow.flow_type = Flow.VOICE
        bassist_flow.save()

        post_data = dict(flow=bassist_flow.pk, groups=[bassists.pk])
        response = self.client.post(reverse('triggers.trigger_inbound_call'), post_data)
        self.assertEquals(2, Trigger.objects.filter(trigger_type=Trigger.TYPE_INBOUND_CALL).count())

        self.assertEquals(bassist_flow.pk, Trigger.find_flow_for_inbound_call(mike).pk)
        self.assertEquals(guitarist_flow.pk, Trigger.find_flow_for_inbound_call(trey).pk)

        # release our channel
        self.channel.release()

        # we no longer have voice flows or inbound call triggers that arent archived
        self.assertEquals(0, Flow.objects.filter(flow_type=Flow.VOICE, is_archived=False).count())
        self.assertEquals(0, Trigger.objects.filter(trigger_type=Trigger.TYPE_INBOUND_CALL, is_archived=False).count())

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
        post_data['omnibox'] = "g-%d,c-%d" % (linkin_park.pk, stromae.pk)
        post_data['repeat_period'] = 'D'
        post_data['start'] = 'later'
        post_data['start_datetime_value'] = "%d" % tommorrow_stamp

        response = self.client.post(reverse("triggers.trigger_schedule"), post_data)
        self.assertEquals(response.context['form'].errors.keys(), ['flow'])
        self.assertFalse(Trigger.objects.all())
        self.assertFalse(Schedule.objects.all())

        post_data = dict()
        post_data['flow'] = flow.pk
        post_data['omnibox'] = "g-%d,c-%d" % (linkin_park.pk, stromae.pk)
        post_data['start'] = 'never'
        post_data['repeat_period'] = 'O'

        response = self.client.post(reverse("triggers.trigger_schedule"), post_data)
        self.assertEquals(1, Trigger.objects.all().count())

        trigger = Trigger.objects.all().order_by('-pk')[0]
        self.assertTrue(trigger.schedule)
        self.assertEquals(trigger.schedule.status, 'U')
        self.assertEquals(trigger.groups.all()[0].pk, linkin_park.pk)
        self.assertEquals(trigger.contacts.all()[0].pk, stromae.pk)

        post_data = dict()
        post_data['flow'] = flow.pk
        post_data['omnibox'] = "g-%d,c-%d" % (linkin_park.pk, stromae.pk)
        post_data['start'] = 'stop'
        post_data['repeat_period'] = 'O'

        response = self.client.post(reverse("triggers.trigger_schedule"), post_data)
        self.assertEquals(2, Trigger.objects.all().count())

        trigger = Trigger.objects.all().order_by('-pk')[0]
        self.assertTrue(trigger.schedule)
        self.assertEquals(trigger.schedule.status, 'U')
        self.assertEquals(trigger.groups.all()[0].pk, linkin_park.pk)
        self.assertEquals(trigger.contacts.all()[0].pk, stromae.pk)

        post_data = dict()
        post_data['flow'] = flow.pk
        post_data['omnibox'] = "g-%d,c-%d" % (linkin_park.pk, stromae.pk)
        post_data['repeat_period'] = 'O'
        post_data['start'] = 'now'
        post_data['start_datetime_value'] = "%d" % now_stamp

        response = self.client.post(reverse("triggers.trigger_schedule"), post_data)
        self.assertEquals(3, Trigger.objects.all().count())

        trigger = Trigger.objects.all().order_by('-pk')[0]
        self.assertTrue(trigger.schedule)
        self.assertFalse(trigger.schedule.next_fire)
        self.assertEquals(trigger.schedule.repeat_period, 'O')
        self.assertEquals(trigger.schedule.repeat_days, 0)
        self.assertEquals(trigger.groups.all()[0].pk, linkin_park.pk)
        self.assertEquals(trigger.contacts.all()[0].pk, stromae.pk)

        post_data = dict()
        post_data['flow'] = flow.pk
        post_data['omnibox'] = "g-%d,c-%d" % (linkin_park.pk, stromae.pk)
        post_data['repeat_period'] = 'D'
        post_data['start'] = 'later'
        post_data['start_datetime_value'] = "%d" % tommorrow_stamp

        response = self.client.post(reverse("triggers.trigger_schedule"), post_data)
        self.assertEquals(4, Trigger.objects.all().count())

        trigger = Trigger.objects.all().order_by('-pk')[0]
        self.assertTrue(trigger.schedule)
        self.assertEquals(trigger.schedule.repeat_period, 'D')
        self.assertEquals(trigger.groups.all()[0].pk, linkin_park.pk)
        self.assertEquals(trigger.contacts.all()[0].pk, stromae.pk)

        update_url = reverse('triggers.trigger_update', args=[trigger.pk])

        post_data = dict()
        post_data['omnibox'] = "g-%d,c-%d" % (linkin_park.pk, stromae.pk)
        post_data['repeat_period'] = 'O'
        post_data['start'] = 'now'
        post_data['start_datetime_value'] = "%d" % now_stamp

        response = self.client.post(update_url, post_data)
        self.assertEquals(response.context['form'].errors.keys(), ['flow'])

        post_data = dict()
        post_data['flow'] = flow.pk
        post_data['omnibox'] = "g-%d" % linkin_park.pk
        post_data['repeat_period'] = 'O'
        post_data['start'] = 'now'
        post_data['start_datetime_value'] = "%d" % now_stamp

        response = self.client.post(update_url, post_data)

        trigger = Trigger.objects.get(pk=trigger.pk)
        self.assertTrue(trigger.schedule)
        self.assertEquals(trigger.schedule.repeat_period, 'O')
        self.assertFalse(trigger.schedule.next_fire)
        self.assertEquals(trigger.groups.all()[0].pk, linkin_park.pk)
        self.assertFalse(trigger.contacts.all())

        post_data = dict()
        post_data['flow'] = flow.pk
        post_data['omnibox'] = "g-%d,c-%d" % (linkin_park.pk, stromae.pk)
        post_data['start'] = 'never'
        post_data['repeat_period'] = 'O'

        response = self.client.post(update_url, post_data)

        trigger = Trigger.objects.get(pk=trigger.pk)
        self.assertTrue(trigger.schedule)
        self.assertEquals(trigger.schedule.status, 'U')
        self.assertEquals(trigger.groups.all()[0].pk, linkin_park.pk)
        self.assertEquals(trigger.contacts.all()[0].pk, stromae.pk)

        post_data = dict()
        post_data['flow'] = flow.pk
        post_data['omnibox'] = "g-%d,c-%d" % (linkin_park.pk, stromae.pk)
        post_data['start'] = 'stop'
        post_data['repeat_period'] = 'O'

        response = self.client.post(update_url, post_data)

        trigger = Trigger.objects.get(pk=trigger.pk)
        self.assertTrue(trigger.schedule)
        self.assertEquals(trigger.schedule.status, 'U')
        self.assertEquals(trigger.groups.all()[0].pk, linkin_park.pk)
        self.assertEquals(trigger.contacts.all()[0].pk, stromae.pk)

        post_data = dict()
        post_data['flow'] = flow.pk
        post_data['omnibox'] = "g-%d,c-%d" % (linkin_park.pk, stromae.pk)
        post_data['repeat_period'] = 'D'
        post_data['start'] = 'later'
        post_data['start_datetime_value'] = "%d" % tommorrow_stamp

        response = self.client.post(update_url, post_data)

        trigger = Trigger.objects.get(pk=trigger.pk)

        self.assertTrue(trigger.schedule)
        self.assertEquals(trigger.schedule.repeat_period, 'D')
        self.assertEquals(trigger.groups.all()[0].pk, linkin_park.pk)
        self.assertEquals(trigger.contacts.all()[0].pk, stromae.pk)

    def test_join_group_trigger(self):

        self.login(self.admin)

        group = self.create_group(name='Chat', contacts=[])

        # create a trigger that sets up a group join flow
        post_data = dict(keyword='join', action_join_group=group.pk, response='Thanks for joining')
        self.client.post(reverse("triggers.trigger_register"), data=post_data)

        # did our group join flow get created?
        flow = Flow.objects.get(flow_type=Flow.FLOW)

        # check that our trigger exists and shows our group
        trigger = Trigger.objects.get(keyword='join', flow=flow)
        self.assertEquals('Join Chat', trigger.flow.name)

        # now let's try it out
        contact = self.create_contact('Ben', '+250788382382')
        msg = self.create_msg(direction=INCOMING, contact=contact, text="join")
        self.assertIsNone(msg.msg_type)

        self.assertTrue(Trigger.find_and_handle(msg))

        self.assertEqual(msg.msg_type, 'F')
        self.assertEqual(Trigger.objects.get(pk=trigger.pk).trigger_count, 1)

        # we should be in the group now
        self.assertEqual(set(contact.user_groups.all()), {group})

        # and have one incoming and one outgoing message
        self.assertEquals(2, contact.msgs.count())

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
        self.assertEquals(200, response.status_code)

        # confirm our objects
        flow = Flow.objects.filter(flow_type=Flow.FLOW).order_by('-pk').first()
        trigger = Trigger.objects.get(keyword='join_lang', flow=flow)
        self.assertEquals('Join Lang Group', trigger.flow.name)

    def test_trigger_form(self):

        for form in (DefaultTriggerForm, RegisterTriggerForm):

            trigger_form = form(self.admin)
            pick = self.get_flow('pick_a_number')
            favorites = self.get_flow('favorites')
            self.assertEquals(2, trigger_form.fields['flow'].choices.queryset.all().count())

            # now change to a single message type
            pick.flow_type = Flow.MESSAGE
            pick.save()

            # our flow should no longer be an option
            trigger_form = form(self.admin)
            choices = trigger_form.fields['flow'].choices
            self.assertEquals(1, choices.queryset.all().count())
            self.assertIsNone(choices.queryset.filter(pk=pick.pk).first())

            pick.delete()
            favorites.delete()


    def test_unicode_trigger(self):
        self.login(self.admin)
        group = self.create_group(name='Chat', contacts=[])

        # create a trigger that sets up a group join flow
        post_data = dict(action_join_group=group.pk, keyword=u'١٠٠')
        self.client.post(reverse("triggers.trigger_register"), data=post_data)

        # did our group join flow get created?
        flow = Flow.objects.get(flow_type=Flow.FLOW)

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
        self.assertEquals("Join Chat", trigger.flow.name)

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

        Call.create_call(self.channel, contact.get_urn(TEL_SCHEME).path, timezone.now(), 0, Call.TYPE_IN_MISSED)
        self.assertEquals(1, Call.objects.all().count())
        self.assertEquals(0, flow.runs.all().count())

        trigger_url = reverse("triggers.trigger_missed_call")

        response = self.client.get(trigger_url)
        self.assertEquals(response.status_code, 200)

        post_data = dict(flow=flow.pk)

        response = self.client.post(trigger_url, post_data)
        trigger =  Trigger.objects.all().order_by('-pk')[0]

        self.assertEquals(trigger.trigger_type, Trigger.TYPE_MISSED_CALL)
        self.assertEquals(trigger.flow.pk, flow.pk)

        missed_call_trigger = Trigger.get_triggers_of_type(self.org, Trigger.TYPE_MISSED_CALL).first()

        self.assertEquals(missed_call_trigger.pk, trigger.pk)

        Call.create_call(self.channel, contact.get_urn(TEL_SCHEME).path, timezone.now(), 0, Call.TYPE_IN_MISSED)
        self.assertEquals(2, Call.objects.all().count())
        self.assertEquals(1, flow.runs.all().count())
        self.assertEquals(flow.runs.all()[0].contact.pk, contact.pk)

        other_flow = Flow.copy(flow, self.admin)
        post_data = dict(flow=other_flow.pk)

        response = self.client.post(reverse("triggers.trigger_update", args=[trigger.pk]), post_data)
        trigger = Trigger.objects.get(pk=trigger.pk)
        self.assertEquals(trigger.flow.pk, other_flow.pk)

        # create ten missed call triggers
        for i in range(10):
            response = self.client.get(trigger_url)
            self.assertEquals(response.status_code, 200)

            post_data = dict(flow=flow.pk)

            response = self.client.post(trigger_url, post_data)
            self.assertEquals(i+2, Trigger.objects.all().count())
            self.assertEquals(1, Trigger.objects.filter(is_archived=False, trigger_type=Trigger.TYPE_MISSED_CALL).count())

        # even unarchiving we only have one acive trigger at a time
        triggers = Trigger.objects.filter(trigger_type=Trigger.TYPE_MISSED_CALL, is_archived=True)
        active_trigger = Trigger.objects.get(trigger_type=Trigger.TYPE_MISSED_CALL, is_archived=False)

        post_data = dict()
        post_data['action'] = 'restore'
        post_data['objects'] = [_.pk for _ in triggers]

        response = self.client.post(reverse("triggers.trigger_archived"), post_data)
        self.assertEquals(1, Trigger.objects.filter(is_archived=False, trigger_type=Trigger.TYPE_MISSED_CALL).count())
        self.assertFalse(active_trigger.pk == Trigger.objects.filter(is_archived=False, trigger_type=Trigger.TYPE_MISSED_CALL)[0].pk)

    def test_catch_all_trigger(self):
        self.login(self.admin)
        catch_all_trigger = Trigger.get_triggers_of_type(self.org, Trigger.TYPE_CATCH_ALL).first()
        flow = self.create_flow()

        contact = self.create_contact("Ali", "250788739305")

        # make our first message echo back the original message
        action_set = ActionSet.objects.get(uuid=flow.entry_uuid)
        actions = action_set.as_json()['actions']
        actions[0]['msg']['base'] = 'Echo: @step.value'
        action_set.set_actions_dict(actions)
        action_set.save()

        self.assertFalse(catch_all_trigger)

        Msg.create_incoming(self.channel, (TEL_SCHEME, contact.get_urn().path), "Hi")
        self.assertEquals(1, Msg.objects.all().count())
        self.assertEquals(0, flow.runs.all().count())

        trigger_url = reverse("triggers.trigger_catchall")

        response = self.client.get(trigger_url)
        self.assertEquals(response.status_code, 200)

        post_data = dict(flow=flow.pk)

        response = self.client.post(trigger_url, post_data)
        trigger = Trigger.objects.all().order_by('-pk')[0]

        self.assertEquals(trigger.trigger_type, Trigger.TYPE_CATCH_ALL)
        self.assertEquals(trigger.flow.pk, flow.pk)

        catch_all_trigger = Trigger.get_triggers_of_type(self.org, Trigger.TYPE_CATCH_ALL).first()

        self.assertEquals(catch_all_trigger.pk, trigger.pk)

        incoming = Msg.create_incoming(self.channel, (TEL_SCHEME, contact.get_urn().path), "Hi")
        self.assertEquals(1, flow.runs.all().count())
        self.assertEquals(flow.runs.all()[0].contact.pk, contact.pk)
        reply = Msg.objects.get(response_to=incoming)
        self.assertEquals('Echo: Hi', reply.text)

        other_flow = Flow.copy(flow, self.admin)
        post_data = dict(flow=other_flow.pk)

        response = self.client.post(reverse("triggers.trigger_update", args=[trigger.pk]), post_data)
        trigger = Trigger.objects.get(pk=trigger.pk)
        self.assertEquals(trigger.flow.pk, other_flow.pk)

        # create a bunch of catch all triggers
        for i in range(3):
            response = self.client.get(trigger_url)
            self.assertEquals(response.status_code, 200)

            post_data = dict(flow=flow.pk)
            response = self.client.post(trigger_url, post_data)
            self.assertEquals(i+2, Trigger.objects.all().count())
            self.assertEquals(1, Trigger.objects.filter(is_archived=False, trigger_type=Trigger.TYPE_CATCH_ALL).count())

        # even unarchiving we only have one acive trigger at a time
        triggers = Trigger.objects.filter(trigger_type=Trigger.TYPE_CATCH_ALL, is_archived=True)
        active_trigger = Trigger.objects.get(trigger_type=Trigger.TYPE_CATCH_ALL, is_archived=False)

        post_data = dict()
        post_data['action'] = 'restore'
        post_data['objects'] = [_.pk for _ in triggers]

        response = self.client.post(reverse("triggers.trigger_archived"), post_data)
        self.assertEquals(1, Trigger.objects.filter(is_archived=False, trigger_type=Trigger.TYPE_CATCH_ALL).count())
        self.assertFalse(active_trigger.pk == Trigger.objects.filter(is_archived=False, trigger_type=Trigger.TYPE_CATCH_ALL)[0].pk)

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
        self.assertEquals(response.status_code, 200)

        # test trigger for Flow of flow_type of FLOW
        flow = self.create_flow()

        # a valid keyword
        post_data = dict(keyword='kiki', flow=flow.pk)
        self.client.post(reverse("triggers.trigger_keyword"), data=post_data)
        trigger = Trigger.objects.get(keyword='kiki')
        self.assertEquals(flow.pk, trigger.flow.pk)

        update_url = reverse('triggers.trigger_update', args=[trigger.pk])

        response = self.client.get(update_url)
        self.assertEquals(response.status_code, 200)
        self.assertEquals(len(response.context['form'].fields), 4)

        group = self.create_group("first", [])

        post_data = dict()
        post_data['keyword'] = 'koko'
        post_data['flow'] = flow.pk
        post_data['groups'] = [group.pk]

        response = self.client.post(update_url, post_data, follow=True)

        updated_trigger = Trigger.objects.get(pk=trigger.pk)
        self.assertEquals(updated_trigger.keyword, 'koko')
        self.assertEquals(updated_trigger.flow.pk, flow.pk)
        self.assertTrue(group in updated_trigger.groups.all())

    def test_trigger_handle(self):

        self.contact = self.create_contact('Eric', '+250788382382')
        self.contact2 = self.create_contact('Nic', '+250788383383')

        incoming = self.create_msg(direction=INCOMING, contact=self.contact, text="")

        self.assertFalse(Trigger.find_and_handle(incoming))

        incoming = self.create_msg(direction=INCOMING, contact=self.contact, text="some text")

        self.assertFalse(Trigger.find_and_handle(incoming))

        flow = self.create_flow()

        Trigger.objects.create(org=self.org, keyword='when', flow=flow,
                               created_by=self.admin, modified_by=self.admin)

        incoming = self.create_msg(direction=INCOMING, contact=self.contact, text="when is it?")

        self.assertTrue(Trigger.find_and_handle(incoming))

        group = self.create_group("first", [self.contact2])

        trigger = Trigger.objects.create(org=self.org, keyword='where', flow=flow, 
                                         created_by=self.admin, modified_by=self.admin)

        trigger.groups.add(group)

        incoming = self.create_msg(direction=INCOMING, contact=self.contact, text="where do you go?")

        self.assertFalse(Trigger.find_and_handle(incoming))

        incoming2 = self.create_msg(direction=INCOMING, contact=self.contact2, text="where do I find it?")

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
        trigger1 = Trigger.objects.create(org=self.org, keyword=keyword, flow=flow1,
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

