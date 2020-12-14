from datetime import timedelta
from unittest.mock import patch

from django.test import override_settings
from django.urls import reverse
from django.utils import timezone

from temba.channels.models import Channel
from temba.contacts.models import ContactGroup
from temba.contacts.search.omnibox import omnibox_serialize
from temba.flows.models import Flow
from temba.orgs.models import Language
from temba.schedules.models import Schedule
from temba.tests import MockResponse, TembaTest
from temba.utils.dates import datetime_to_str

from .models import Trigger
from .views import DefaultTriggerForm, RegisterTriggerForm


class TriggerTest(TembaTest):
    def test_no_trigger_redirects_to_create_page(self):
        self.login(self.admin)

        # no trigger existing
        Trigger.objects.all().delete()

        response = self.client.get(reverse("triggers.trigger_list"))
        self.assertEqual(response.status_code, 302)

        response = self.client.get(reverse("triggers.trigger_list"), follow=True)
        self.assertEqual(response.request["PATH_INFO"], reverse("triggers.trigger_create"))

    def test_keyword_trigger(self):
        self.login(self.admin)

        flow = self.create_flow()
        voice_flow = self.get_flow("ivr")

        # flow options should show sms and voice flows
        response = self.client.get(reverse("triggers.trigger_keyword"))

        self.assertContains(response, flow.name)
        self.assertContains(response, voice_flow.name)

        # try a keyword with spaces
        response = self.client.post(
            reverse("triggers.trigger_keyword"), {"keyword": "keyword with spaces", "flow": flow.id, "match_type": "F"}
        )

        self.assertEqual(len(response.context["form"].errors), 1)

        # try a keyword with special characters
        response = self.client.post(
            reverse("triggers.trigger_keyword"), {"keyword": "keyw!o^rd__", "flow": flow.id, "match_type": "F"}
        )

        self.assertEqual(len(response.context["form"].errors), 1)

        self.client.post(reverse("triggers.trigger_keyword"), {"keyword": "١٠٠", "flow": flow.id, "match_type": "F"})

        trigger = Trigger.objects.get(keyword="١٠٠")
        self.assertEqual(flow, trigger.flow)
        self.assertEqual('Trigger[type=K, flow="Color Flow"]', str(trigger))

        # non-latin keyword (Hindi)
        self.client.post(reverse("triggers.trigger_keyword"), {"keyword": "मिलाए", "flow": flow.id, "match_type": "F"})

        trigger = Trigger.objects.get(keyword="मिलाए")
        self.assertEqual(flow, trigger.flow)

        # a valid keyword
        self.client.post(
            reverse("triggers.trigger_keyword"), {"keyword": "startkeyword", "flow": flow.id, "match_type": "F"}
        )

        trigger = Trigger.objects.get(keyword="startkeyword")
        self.assertEqual(flow, trigger.flow)

        # try a duplicate keyword
        response = self.client.post(
            reverse("triggers.trigger_keyword"), {"keyword": "startkeyword", "flow": flow.id, "match_type": "F"}
        )

        self.assertEqual(len(response.context["form"].errors), 1)

        # see our trigger on the list page
        response = self.client.get(reverse("triggers.trigger_list"))

        self.assertContains(response, "startkeyword")

        # can search by keyword
        response = self.client.get(reverse("triggers.trigger_list") + "?search=Key")

        self.assertContains(response, "startkeyword")
        self.assertTrue(response.context["object_list"])

        response = self.client.get(reverse("triggers.trigger_list") + "?search=Tottenham")

        self.assertNotContains(response, "startkeyword")
        self.assertFalse(response.context["object_list"])

        # can archive it
        self.client.post(reverse("triggers.trigger_list"), {"action": "archive", "objects": trigger.id})
        response = self.client.get(reverse("triggers.trigger_list"))

        self.assertNotContains(response, "startkeyword")

        # and it now appears on the archive page
        response = self.client.get(reverse("triggers.trigger_archived"))
        self.assertContains(response, "startkeyword")

        # can restore it
        self.client.post(reverse("triggers.trigger_archived"), {"action": "restore", "objects": trigger.id})
        response = self.client.get(reverse("triggers.trigger_archived"))

        self.assertNotContains(response, "startkeyword")

        response = self.client.get(reverse("triggers.trigger_list"))

        # should be back in the main trigger list
        self.assertContains(response, "startkeyword")

        # once archived we can duplicate it but with one active at a time
        trigger = Trigger.objects.get(keyword="startkeyword")
        trigger.is_archived = True
        trigger.save(update_fields=("is_archived",))

        post_data = dict(keyword="startkeyword", flow=flow.id, match_type="F")
        response = self.client.post(reverse("triggers.trigger_keyword"), data=post_data)
        self.assertEqual(Trigger.objects.filter(keyword="startkeyword").count(), 2)
        self.assertEqual(1, Trigger.objects.filter(keyword="startkeyword", is_archived=False).count())
        other_trigger = Trigger.objects.filter(keyword="startkeyword", is_archived=False)[0]
        self.assertFalse(trigger.pk == other_trigger.pk)

        # try archiving it we have one archived and the other active
        response = self.client.get(reverse("triggers.trigger_archived"), post_data)
        self.assertContains(response, "startkeyword")
        post_data = dict(action="restore", objects=trigger.pk)
        self.client.post(reverse("triggers.trigger_archived"), post_data)
        response = self.client.get(reverse("triggers.trigger_archived"), post_data)
        self.assertContains(response, "startkeyword")
        response = self.client.get(reverse("triggers.trigger_list"), post_data)
        self.assertContains(response, "startkeyword")
        self.assertEqual(1, Trigger.objects.filter(keyword="startkeyword", is_archived=False).count())
        self.assertNotEqual(other_trigger, Trigger.objects.filter(keyword="startkeyword", is_archived=False)[0])

        self.contact = self.create_contact("Eric", phone="+250788382382")
        self.contact2 = self.create_contact("Nic", phone="+250788383383")
        group1 = self.create_group("first", [self.contact2])
        group2 = self.create_group("second", [self.contact])
        group3 = self.create_group("third", [self.contact, self.contact2])

        self.assertEqual(Trigger.objects.filter(keyword="startkeyword").count(), 2)
        self.assertEqual(Trigger.objects.filter(keyword="startkeyword", is_archived=False).count(), 1)

        # update trigger with 2 groups
        post_data = dict(keyword="startkeyword", flow=flow.id, match_type="F", groups=[group1.pk, group2.pk])
        response = self.client.post(reverse("triggers.trigger_keyword"), data=post_data)
        self.assertEqual(Trigger.objects.filter(keyword="startkeyword").count(), 3)
        self.assertEqual(Trigger.objects.filter(keyword="startkeyword", is_archived=False).count(), 2)

        # get error when groups overlap
        post_data = dict(keyword="startkeyword", flow=flow.id, match_type="F")
        post_data["groups"] = [group2.pk, group3.pk]
        response = self.client.post(reverse("triggers.trigger_keyword"), data=post_data)
        self.assertEqual(1, len(response.context["form"].errors))
        self.assertEqual(Trigger.objects.filter(keyword="startkeyword").count(), 3)
        self.assertEqual(Trigger.objects.filter(keyword="startkeyword", is_archived=False).count(), 2)

        # allow new creation when groups do not overlap
        post_data = dict(keyword="startkeyword", flow=flow.id, match_type="F")
        post_data["groups"] = [group3.pk]
        self.client.post(reverse("triggers.trigger_keyword"), data=post_data)
        self.assertEqual(Trigger.objects.filter(keyword="startkeyword").count(), 4)
        self.assertEqual(Trigger.objects.filter(keyword="startkeyword", is_archived=False).count(), 3)

    def test_inbound_call_trigger(self):
        self.login(self.admin)

        # inbound call trigger can be made without a call channel
        response = self.client.get(reverse("triggers.trigger_create"))
        self.assertContains(response, "Start a flow after receiving a call")

        # make our channel support ivr
        self.channel.role += Channel.ROLE_CALL + Channel.ROLE_ANSWER
        self.channel.save()

        # flow is required
        response = self.client.post(reverse("triggers.trigger_inbound_call"), dict())
        self.assertEqual(list(response.context["form"].errors.keys()), ["flow"])

        # flow must be an ivr flow
        message_flow = self.create_flow()
        post_data = dict(flow=message_flow.pk)
        response = self.client.post(reverse("triggers.trigger_inbound_call"), post_data)
        self.assertEqual(list(response.context["form"].errors.keys()), ["flow"])

        # now lets create our first valid inbound call trigger
        guitarist_flow = self.create_flow()
        guitarist_flow.flow_type = Flow.TYPE_VOICE
        guitarist_flow.save()

        post_data = dict(flow=guitarist_flow.pk)
        response = self.client.post(reverse("triggers.trigger_inbound_call"), post_data)
        trigger = Trigger.objects.filter(trigger_type=Trigger.TYPE_INBOUND_CALL).first()
        self.assertIsNotNone(trigger)

        # now lets check that group specific call triggers work
        mike = self.create_contact("Mike", phone="+17075551213")
        bassists = self.create_group("Bassists", [mike])

        # flow specific to our group
        bassist_flow = self.create_flow()
        bassist_flow.flow_type = Flow.TYPE_VOICE
        bassist_flow.save()

        post_data = dict(flow=bassist_flow.pk, groups=[bassists.pk])
        self.client.post(reverse("triggers.trigger_inbound_call"), post_data)
        self.assertEqual(2, Trigger.objects.filter(trigger_type=Trigger.TYPE_INBOUND_CALL).count())

        # release our channel
        self.channel.release()

        # should still have two voice flows and triggers (they aren't archived)
        self.assertEqual(2, Flow.objects.filter(flow_type=Flow.TYPE_VOICE, is_archived=False).count())
        self.assertEqual(2, Trigger.objects.filter(trigger_type=Trigger.TYPE_INBOUND_CALL, is_archived=False).count())

    def test_referral_trigger(self):
        self.login(self.admin)
        flow = self.create_flow()

        self.fb_channel = Channel.create(
            self.org,
            self.user,
            None,
            "FB",
            None,
            "1234",
            config={Channel.CONFIG_AUTH_TOKEN: "auth"},
            uuid="00000000-0000-0000-0000-000000001234",
        )

        create_url = reverse("triggers.trigger_referral")

        post_data = dict()
        response = self.client.post(create_url, post_data)
        self.assertEqual(list(response.context["form"].errors.keys()), ["flow"])

        # ok, valid referrer id and flow
        post_data = dict(flow=flow.id, referrer_id="signup")
        response = self.client.post(create_url, post_data)
        self.assertNoFormErrors(response)

        # assert our trigger was created
        first_trigger = Trigger.objects.get()
        self.assertEqual(first_trigger.trigger_type, Trigger.TYPE_REFERRAL)
        self.assertEqual(first_trigger.flow, flow)
        self.assertIsNone(first_trigger.channel)

        # empty referrer_id should create the trigger
        post_data = dict(flow=flow.id, referrer_id="")
        response = self.client.post(create_url, post_data)
        self.assertNoFormErrors(response)

        # try to create the same trigger, should fail as we can only have one per referrer
        post_data = dict(flow=flow.id, referrer_id="signup")
        response = self.client.post(create_url, post_data)
        self.assertEqual(list(response.context["form"].errors.keys()), ["__all__"])

        # should work if we specify a specific channel
        post_data["channel"] = self.fb_channel.id
        response = self.client.post(create_url, post_data)
        self.assertNoFormErrors(response)

        # load it
        second_trigger = Trigger.objects.get(channel=self.fb_channel)
        self.assertEqual(second_trigger.trigger_type, Trigger.TYPE_REFERRAL)
        self.assertEqual(second_trigger.flow, flow)

        # try updating it to a null channel
        update_url = reverse("triggers.trigger_update", args=[second_trigger.id])
        del post_data["channel"]
        response = self.client.post(update_url, post_data)
        self.assertEqual(list(response.context["form"].errors.keys()), ["__all__"])

        # archive our first trigger
        Trigger.apply_action_archive(self.admin, Trigger.objects.filter(channel=None))

        # should now be able to update to a null channel
        response = self.client.post(update_url, post_data)
        self.assertNoFormErrors(response)
        second_trigger.refresh_from_db()

        self.assertIsNone(second_trigger.channel)

    @patch("temba.flows.models.FlowStart.async_start")
    def test_trigger_schedule(self, mock_async_start):
        self.login(self.admin)
        flow = self.create_flow()

        chester = self.create_contact("Chester", phone="+250788987654")
        shinoda = self.create_contact("Shinoda", phone="+250234213455")
        linkin_park = self.create_group("Linkin Park", [chester, shinoda])
        stromae = self.create_contact("Stromae", phone="+250788645323")

        now = timezone.now()

        tommorrow = now + timedelta(days=1)

        omnibox_selection = omnibox_serialize(flow.org, [linkin_park], [stromae], True)

        # try to create trigger without a flow or omnibox
        response = self.client.post(
            reverse("triggers.trigger_schedule"),
            {
                "omnibox": omnibox_selection,
                "repeat_period": "D",
                "start": "later",
                "start_datetime": datetime_to_str(tommorrow, "%Y-%m-%d %H:%M", self.org.timezone),
            },
        )

        self.assertEqual(list(response.context["form"].errors.keys()), ["flow"])
        self.assertFalse(Trigger.objects.all())
        self.assertFalse(Schedule.objects.all())

        # survey flows should not be an option
        flow.flow_type = Flow.TYPE_SURVEY
        flow.save(update_fields=("flow_type",))

        response = self.client.get(reverse("triggers.trigger_schedule"))

        # check no flows listed
        self.assertEqual(response.context["form"].fields["flow"].queryset.all().count(), 0)

        # revert flow to messaging flow type
        flow.flow_type = Flow.TYPE_MESSAGE
        flow.save(update_fields=("flow_type",))

        self.assertEqual(response.context["form"].fields["flow"].queryset.all().count(), 1)

        # this time provide a flow but leave out omnibox..
        response = self.client.post(
            reverse("triggers.trigger_schedule"),
            {
                "flow": flow.id,
                "repeat_period": "D",
                "start": "later",
                "start_datetime": datetime_to_str(tommorrow, "%Y-%m-%d %H:%M", self.org.timezone),
            },
        )
        self.assertEqual(list(response.context["form"].errors.keys()), ["omnibox"])
        self.assertFalse(Trigger.objects.all())
        self.assertFalse(Schedule.objects.all())

        # ok, really create it
        self.client.post(
            reverse("triggers.trigger_schedule"),
            {
                "flow": flow.id,
                "omnibox": omnibox_selection,
                "repeat_period": "D",
                "start": "later",
                "start_datetime": datetime_to_str(tommorrow, "%Y-%m-%d %H:%M", self.org.timezone),
            },
        )

        self.assertEqual(Trigger.objects.count(), 1)

        self.client.post(
            reverse("triggers.trigger_schedule"),
            {
                "flow": flow.id,
                "omnibox": omnibox_selection,
                "repeat_period": "D",
                "start": "later",
                "start_datetime": datetime_to_str(tommorrow, "%Y-%m-%d %H:%M", self.org.timezone),
            },
        )

        self.assertEqual(2, Trigger.objects.all().count())

        trigger = Trigger.objects.order_by("id").last()

        self.assertTrue(trigger.schedule)
        self.assertEqual(trigger.schedule.repeat_period, "D")
        self.assertEqual(set(trigger.groups.all()), {linkin_park})
        self.assertEqual(set(trigger.contacts.all()), {stromae})

        update_url = reverse("triggers.trigger_update", args=[trigger.pk])

        # try to update a trigger without a flow
        response = self.client.post(
            update_url,
            {
                "omnibox": omnibox_selection,
                "repeat_period": "O",
                "start": "later",
                "start_datetime": datetime_to_str(now, "%Y-%m-%d %H:%M", self.org.timezone),
            },
        )

        self.assertEqual(list(response.context["form"].errors.keys()), ["flow"])

        # provide flow this time, update contact
        self.client.post(
            update_url,
            {
                "flow": flow.id,
                "omnibox": omnibox_serialize(flow.org, [linkin_park], [shinoda], True),
                "repeat_period": "D",
                "start": "later",
                "start_datetime": datetime_to_str(now, "%Y-%m-%d %H:%M", self.org.timezone),
            },
        )

        trigger.refresh_from_db()

        self.assertTrue(trigger.schedule)
        self.assertEqual(trigger.schedule.repeat_period, "D")
        self.assertTrue(trigger.schedule.next_fire)
        self.assertEqual(set(trigger.groups.all()), {linkin_park})
        self.assertEqual(set(trigger.contacts.all()), {shinoda})

        # can't submit weekly repeat without specifying the days to repeat on
        response = self.client.post(
            update_url,
            {
                "flow": flow.id,
                "omnibox": omnibox_selection,
                "repeat_period": "W",
                "start": "later",
                "start_datetime": datetime_to_str(now, "%Y-%m-%d %H:%M", self.org.timezone),
            },
        )

        self.assertFormError(response, "form", "__all__", "Must specify at least one day of the week")

        # or submit with invalid days
        response = self.client.post(
            update_url,
            {
                "flow": flow.id,
                "omnibox": omnibox_selection,
                "repeat_period": "W",
                "repeat_days_of_week": "X",
                "start": "later",
                "start_datetime": datetime_to_str(now, "%Y-%m-%d %H:%M", self.org.timezone),
            },
        )

        self.assertFormError(
            response, "form", "repeat_days_of_week", "Select a valid choice. X is not one of the available choices."
        )

    def test_join_group_trigger(self):
        self.login(self.admin)
        group = self.create_group(name="Chat", contacts=[])

        favorites = self.get_flow("favorites")

        # create a trigger that sets up a group join flow
        self.client.post(
            reverse("triggers.trigger_register"),
            {"keyword": "join", "action_join_group": group.id, "response": "Thanks for joining", "flow": favorites.id},
        )

        # did our group join flow get created?
        flow = Flow.objects.get(flow_type=Flow.TYPE_MESSAGE, name="Join Chat")
        flow_def = flow.get_definition()

        self.assertEqual(len(flow_def["nodes"]), 1)
        self.assertEqual(len(flow_def["nodes"][0]["actions"]), 4)
        self.assertEqual(flow_def["nodes"][0]["actions"][0]["type"], "add_contact_groups")
        self.assertEqual(flow_def["nodes"][0]["actions"][1]["type"], "set_contact_name")
        self.assertEqual(flow_def["nodes"][0]["actions"][2]["type"], "send_msg")
        self.assertEqual(flow_def["nodes"][0]["actions"][3]["type"], "enter_flow")

        # check that our trigger exists and shows our group
        trigger = Trigger.objects.get(keyword="join", flow=flow)
        self.assertEqual(trigger.flow.name, "Join Chat")

        # the org has no language, so it should be a 'base' flow
        self.assertEqual(flow.base_language, "base")

        # deleting our contact group should leave our triggers and flows since the group can be recreated
        self.client.post(reverse("contacts.contactgroup_delete", args=[group.id]))
        self.assertTrue(Trigger.objects.get(pk=trigger.id).is_active)

        # try creating a join group on an org with a language
        language = Language.create(self.org, self.admin, "Klingon", "kli")
        self.org.primary_language = language
        self.org.save(update_fields=("primary_language",))

        # now create another group trigger
        group = self.create_group(name="Lang Group", contacts=[])
        response = self.client.post(
            reverse("triggers.trigger_register"),
            {"keyword": "join_lang", "action_join_group": group.id, "response": "Thanks for joining"},
        )

        self.assertEqual(response.status_code, 200)

        # confirm our objects
        flow = Flow.objects.filter(flow_type=Flow.TYPE_MESSAGE).order_by("id").last()
        trigger = Trigger.objects.get(keyword="join_lang", flow=flow)

        self.assertEqual(trigger.flow.name, "Join Lang Group")

        # the flow should be created with the primary language for the org
        self.assertEqual(flow.base_language, "kli")

    def test_join_group_nonlatin(self):
        self.login(self.admin)
        group = self.create_group(name="Chat", contacts=[])

        # no keyword must show validation error
        response = self.client.post(
            reverse("triggers.trigger_register"), {"action_join_group": group.id, "keyword": "@#$"}
        )
        self.assertEqual(len(response.context["form"].errors), 1)

        # create a trigger that sets up a group join flow
        self.client.post(reverse("triggers.trigger_register"), {"action_join_group": group.id, "keyword": "١٠٠"})

        # did our group join flow get created?
        Flow.objects.get(flow_type=Flow.TYPE_MESSAGE)

    def test_join_group_no_response_or_flow(self):
        self.login(self.admin)

        group = self.create_group(name="Chat", contacts=[])

        # create a trigger that sets up a group join flow without a response or secondary flow
        self.client.post(reverse("triggers.trigger_register"), {"action_join_group": group.id, "keyword": "join"})

        # did our group join flow get created?
        flow = Flow.objects.get(flow_type=Flow.TYPE_MESSAGE)
        flow_def = flow.get_definition()

        self.assertEqual(len(flow_def["nodes"]), 1)
        self.assertEqual(len(flow_def["nodes"][0]["actions"]), 2)
        self.assertEqual(flow_def["nodes"][0]["actions"][0]["type"], "add_contact_groups")
        self.assertEqual(flow_def["nodes"][0]["actions"][1]["type"], "set_contact_name")

        # check that our trigger exists and shows our group
        trigger = Trigger.objects.get(keyword="join", flow=flow)
        self.assertEqual("Join Chat", trigger.flow.name)

    def test_trigger_form(self):

        for form in (DefaultTriggerForm, RegisterTriggerForm):

            trigger_form = form(self.admin)
            pick = self.get_flow("pick_a_number")
            favorites = self.get_flow("favorites")
            self.assertEqual(2, trigger_form.fields["flow"].choices.queryset.all().count())

            # now change to a system flow
            pick.is_system = True
            pick.save()

            # our flow should no longer be an option
            trigger_form = form(self.admin)
            choices = trigger_form.fields["flow"].choices
            self.assertEqual(1, choices.queryset.all().count())
            self.assertIsNone(choices.queryset.filter(pk=pick.pk).first())

            pick.release()
            favorites.release()

    def test_missed_call_trigger(self):
        self.login(self.admin)
        flow = self.create_flow()

        trigger_url = reverse("triggers.trigger_missed_call")

        response = self.client.get(trigger_url)
        self.assertEqual(response.status_code, 200)

        response = self.client.post(trigger_url, {"flow": flow.id})
        self.assertEqual(response.status_code, 200)

        trigger = Trigger.objects.order_by("id").last()

        self.assertEqual(trigger.trigger_type, Trigger.TYPE_MISSED_CALL)
        self.assertEqual(trigger.flow, flow)

        other_flow = Flow.copy(flow, self.admin)

        response = self.client.post(reverse("triggers.trigger_update", args=[trigger.id]), {"flow": other_flow.id})
        self.assertEqual(response.status_code, 302)

        trigger.refresh_from_db()
        self.assertEqual(trigger.flow, other_flow)

        # create ten missed call triggers
        for i in range(10):
            response = self.client.get(trigger_url)
            self.assertEqual(response.status_code, 200)

            self.client.post(trigger_url, {"flow": flow.id})

            self.assertEqual(Trigger.objects.all().count(), i + 2)
            self.assertEqual(
                Trigger.objects.filter(is_archived=False, trigger_type=Trigger.TYPE_MISSED_CALL).count(), 1
            )

        # even unarchiving we only have one active trigger at a time
        triggers = Trigger.objects.filter(trigger_type=Trigger.TYPE_MISSED_CALL, is_archived=True)
        active_trigger = Trigger.objects.get(trigger_type=Trigger.TYPE_MISSED_CALL, is_archived=False)

        response = self.client.post(
            reverse("triggers.trigger_archived"), {"action": "restore", "objects": [t.id for t in triggers]}
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(Trigger.objects.filter(is_archived=False, trigger_type=Trigger.TYPE_MISSED_CALL).count(), 1)
        self.assertNotEqual(
            active_trigger, Trigger.objects.filter(is_archived=False, trigger_type=Trigger.TYPE_MISSED_CALL)[0]
        )

    def test_new_conversation_trigger_viber(self):
        self.login(self.admin)
        flow = self.create_flow()
        flow2 = self.create_flow()

        # see if we list new conversation triggers on the trigger page
        create_trigger_url = reverse("triggers.trigger_create", args=[])
        response = self.client.get(create_trigger_url)
        self.assertNotContains(response, "conversation is started")

        # add a viber public channel
        viber_channel = Channel.create(
            self.org,
            self.user,
            None,
            "VP",
            None,
            "1001",
            uuid="00000000-0000-0000-0000-000000001234",
            config={Channel.CONFIG_AUTH_TOKEN: "auth_token"},
        )

        # should now be able to create one
        response = self.client.get(create_trigger_url)
        self.assertContains(response, "conversation is started")

        response = self.client.get(reverse("triggers.trigger_new_conversation", args=[]))
        self.assertEqual(response.context["form"].fields["channel"].queryset.count(), 1)
        self.assertTrue(viber_channel in response.context["form"].fields["channel"].queryset.all())

        # create a facebook channel
        fb_channel = Channel.create(
            self.org, self.user, None, "FB", address="1001", config={"page_name": "Temba", "auth_token": "fb_token"}
        )

        response = self.client.get(reverse("triggers.trigger_new_conversation", args=[]))
        self.assertEqual(response.context["form"].fields["channel"].queryset.count(), 2)
        self.assertTrue(viber_channel in response.context["form"].fields["channel"].queryset.all())
        self.assertTrue(fb_channel in response.context["form"].fields["channel"].queryset.all())

        response = self.client.post(
            reverse("triggers.trigger_new_conversation", args=[]), data=dict(channel=viber_channel.id, flow=flow.id)
        )
        self.assertEqual(response.status_code, 200)

        trigger = Trigger.objects.get(trigger_type=Trigger.TYPE_NEW_CONVERSATION, is_active=True, is_archived=False)
        self.assertEqual(trigger.channel, viber_channel)
        self.assertEqual(trigger.flow, flow)

        # try to create another one, fails as we already have a trigger for that channel
        response = self.client.post(
            reverse("triggers.trigger_new_conversation", args=[]), data=dict(channel=viber_channel.id, flow=flow2.id)
        )
        self.assertEqual(response.status_code, 200)
        self.assertFormError(response, "form", "channel", "Trigger with this Channel already exists.")

        # try to change the existing trigger
        response = self.client.post(
            reverse("triggers.trigger_update", args=[trigger.id]),
            data=dict(id=trigger.id, flow=flow2.id, channel=viber_channel.id),
            follow=True,
        )
        self.assertEqual(response.status_code, 200)

        trigger.refresh_from_db()
        self.assertEqual(flow2, trigger.flow)
        self.assertEqual(viber_channel, trigger.channel)

    @override_settings(IS_PROD=True)
    def test_new_conversation_trigger(self):
        self.login(self.admin)

        flow = self.create_flow()
        flow2 = self.create_flow()

        # see if we list new conversation triggers on the trigger page
        create_trigger_url = reverse("triggers.trigger_create", args=[])
        response = self.client.get(create_trigger_url)
        self.assertNotContains(response, "conversation is started")

        # create a facebook channel
        fb_channel = Channel.create(
            self.org, self.user, None, "FB", address="1001", config={"page_name": "Temba", "auth_token": "fb_token"}
        )

        # should now be able to create one
        response = self.client.get(create_trigger_url)
        self.assertContains(response, "conversation is started")

        # go create it
        with patch("requests.post") as mock_post:
            mock_post.return_value = MockResponse(200, '{"message": "Success"}')

            response = self.client.post(
                reverse("triggers.trigger_new_conversation", args=[]), data=dict(channel=fb_channel.id, flow=flow.id)
            )
            self.assertEqual(response.status_code, 200)
            self.assertEqual(mock_post.call_count, 1)

            # check that it is right
            trigger = Trigger.objects.get(
                trigger_type=Trigger.TYPE_NEW_CONVERSATION, is_active=True, is_archived=False
            )
            self.assertEqual(trigger.channel, fb_channel)
            self.assertEqual(trigger.flow, flow)

            # try to create another one, fails as we already have a trigger for that channel
            response = self.client.post(
                reverse("triggers.trigger_new_conversation", args=[]), data=dict(channel=fb_channel.id, flow=flow2.id)
            )
            self.assertEqual(response.status_code, 200)
            self.assertFormError(response, "form", "channel", "Trigger with this Channel already exists.")

        # archive our trigger, should unregister our callback
        with patch("requests.post") as mock_post:
            mock_post.return_value = MockResponse(200, '{"message": "Success"}')

            Trigger.apply_action_archive(self.admin, Trigger.objects.filter(pk=trigger.pk))
            self.assertEqual(response.status_code, 200)
            self.assertEqual(mock_post.call_count, 1)

            trigger.refresh_from_db()
            self.assertTrue(trigger.is_archived)

    def test_catch_all_trigger(self):
        self.login(self.admin)

        flow = self.get_flow("color")
        trigger_url = reverse("triggers.trigger_catchall")

        response = self.client.get(trigger_url)

        self.assertEqual(response.status_code, 200)

        self.client.post(trigger_url, {"flow": flow.id})

        trigger = Trigger.objects.order_by("id").last()

        self.assertEqual(trigger.trigger_type, Trigger.TYPE_CATCH_ALL)
        self.assertEqual(trigger.flow, flow)

        # update trigger to point to different flow
        other_flow = Flow.copy(flow, self.admin)

        self.client.post(reverse("triggers.trigger_update", args=[trigger.pk]), {"flow": other_flow.id})

        trigger.refresh_from_db()

        self.assertEqual(trigger.flow, other_flow)

        # try to create another catch all trigger
        response = self.client.post(trigger_url, {"flow": other_flow.id})

        # shouldn't have succeeded as we already have a catch-all trigger
        self.assertTrue(len(response.context["form"].errors))

        # archive the previous one
        old_catch_all = trigger
        trigger.is_archived = True
        trigger.save(update_fields=("is_archived",))

        # try again
        self.client.post(trigger_url, {"flow": other_flow.id})

        # this time we are a go
        new_catch_all = Trigger.objects.get(is_archived=False, trigger_type=Trigger.TYPE_CATCH_ALL)

        # now add a new trigger based on a group
        group = self.create_group("Trigger Group", [])

        self.client.post(trigger_url, {"flow": other_flow.id, "groups": group.id})

        # should now have two catch all triggers
        self.assertEqual(2, Trigger.objects.filter(is_archived=False, trigger_type=Trigger.TYPE_CATCH_ALL).count())

        group_catch_all = Trigger.objects.get(is_archived=False, trigger_type=Trigger.TYPE_CATCH_ALL, groups=group)

        # try to add another catchall trigger with a few different groups
        group2 = self.create_group("Trigger Group 2", [])

        response = self.client.post(trigger_url, {"flow": other_flow.id, "groups": [group.id, group2.id]})

        # should have failed
        self.assertTrue(len(response.context["form"].errors))

        self.client.post(reverse("triggers.trigger_archived"), {"action": "restore", "objects": [old_catch_all.id]})

        old_catch_all.refresh_from_db()
        new_catch_all.refresh_from_db()

        # our new triggers should have been auto-archived, our old one is now active
        self.assertEqual(Trigger.objects.filter(is_archived=False, trigger_type=Trigger.TYPE_CATCH_ALL).count(), 2)
        self.assertTrue(new_catch_all.is_archived)
        self.assertFalse(old_catch_all.is_archived)

        # ok, archive our old one too, leaving only our group specific trigger
        old_catch_all.is_archived = True
        old_catch_all.save(update_fields=("is_archived",))

        # delete a group attached to a trigger
        group.release()

        # trigger should no longer be active
        group_catch_all.refresh_from_db()

        self.assertFalse(group_catch_all.is_active)

    def test_update(self):
        self.login(self.admin)

        group = self.create_group(name="Chat", contacts=[])

        # create a trigger that sets up a group join flow
        post_data = dict(action_join_group=group.pk, keyword="join")
        self.client.post(reverse("triggers.trigger_register"), data=post_data)

        # did our group join flow get created?
        flow = Flow.objects.get(flow_type=Flow.TYPE_MESSAGE)

        # check that our trigger exists and shows our group
        trigger = Trigger.objects.get(keyword="join", flow=flow)
        update_url = reverse("triggers.trigger_update", args=[trigger.pk])

        response = self.client.get(update_url)
        self.assertEqual(response.status_code, 200)

        # test trigger for Flow of flow_type of FLOW
        flow = self.create_flow()

        # a valid keyword
        post_data = dict(keyword="kiki", flow=flow.id, match_type="F")
        self.client.post(reverse("triggers.trigger_keyword"), data=post_data)
        trigger = Trigger.objects.get(keyword="kiki")
        self.assertEqual(flow.pk, trigger.flow.pk)

        update_url = reverse("triggers.trigger_update", args=[trigger.pk])

        response = self.client.get(update_url)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.context["form"].fields), 5)

        group = self.create_group("first", [])

        # show validation error if keyword is None or not defined
        post_data = dict(flow=flow.id, match_type="O", groups=[group.id])
        response = self.client.post(update_url, post_data, follow=True)
        self.assertEqual(1, len(response.context["form"].errors))

        post_data = dict(keyword="koko", flow=flow.id, match_type="O", groups=[group.id])
        self.client.post(update_url, post_data, follow=True)

        trigger.refresh_from_db()
        self.assertEqual(trigger.keyword, "koko")
        self.assertEqual(trigger.match_type, Trigger.MATCH_ONLY_WORD)
        self.assertEqual(trigger.flow, flow)
        self.assertIn(group, trigger.groups.all())

    def test_export_import(self):
        # tweak our current channel to be twitter so we can create a channel-based trigger
        Channel.objects.filter(id=self.channel.id).update(channel_type="TT")
        flow = self.create_flow()

        group = self.create_group("Trigger Group", [])

        # create a trigger on this flow for the new conversation actions but only on some groups
        trigger = Trigger.objects.create(
            org=self.org,
            flow=flow,
            trigger_type=Trigger.TYPE_NEW_CONVERSATION,
            channel=self.channel,
            created_by=self.admin,
            modified_by=self.admin,
        )
        trigger.groups.add(group)

        components = self.org.resolve_dependencies([flow], [], include_triggers=True)

        # export everything
        export = self.org.export_definitions("http://rapidpro.io", components)

        # remove our trigger
        Trigger.objects.all().delete()

        # and reimport them.. trigger should be recreated
        self.org.import_app(export, self.admin)

        trigger = Trigger.objects.get()
        self.assertEqual(trigger.trigger_type, Trigger.TYPE_NEW_CONVERSATION)
        self.assertEqual(trigger.flow, flow)
        self.assertEqual(trigger.channel, self.channel)
        self.assertEqual(list(trigger.groups.all()), [group])

    def test_release(self):
        flow = self.create_flow()
        group = self.create_group("Trigger Group", [])

        trigger = Trigger.objects.create(
            org=self.org,
            flow=flow,
            trigger_type=Trigger.TYPE_SCHEDULE,
            created_by=self.admin,
            modified_by=self.admin,
            schedule=Schedule.create_schedule(self.org, self.admin, timezone.now(), Schedule.REPEAT_MONTHLY),
        )
        trigger.groups.add(group)

        trigger.release()

        # schedule should also have been deleted but obviously not group or flow
        self.assertEqual(Trigger.objects.count(), 0)
        self.assertEqual(Schedule.objects.count(), 0)
        self.assertEqual(ContactGroup.user_groups.count(), 1)
        self.assertEqual(Flow.objects.count(), 1)
