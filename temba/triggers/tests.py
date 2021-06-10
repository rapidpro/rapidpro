from datetime import timedelta
from unittest.mock import patch

from django.urls import reverse
from django.utils import timezone

from temba.channels.models import Channel
from temba.contacts.models import ContactGroup
from temba.contacts.search.omnibox import omnibox_serialize
from temba.flows.models import Flow
from temba.orgs.models import Language
from temba.schedules.models import Schedule
from temba.tests import CRUDLTestMixin, TembaTest
from temba.utils.dates import datetime_to_str

from .models import Trigger


class TriggerTest(TembaTest):
    def test_model(self):
        flow = self.create_flow()
        trigger = Trigger.create(self.org, self.admin, Trigger.TYPE_KEYWORD, flow, keyword="join")

        self.assertEqual('Trigger[type=K, flow="Test Flow"]', str(trigger))

    def test_archive_conflicts(self):
        flow = self.create_flow()
        group1 = self.create_group("Group 1", contacts=[])
        group2 = self.create_group("Group 1", contacts=[])
        channel1 = self.create_channel("FB", "FB Channel 1", "12345")
        channel2 = self.create_channel("FB", "FB Channel 2", "23456")

        def assert_conflict_resolution(archived, not_archived):
            archived.refresh_from_db()
            not_archived.refresh_from_db()

            self.assertTrue(archived.is_archived)
            self.assertFalse(not_archived.is_archived)

        # keyword triggers conflict if keyword and groups match
        trigger1 = Trigger.create(self.org, self.admin, Trigger.TYPE_KEYWORD, flow, keyword="join")
        trigger2 = Trigger.create(self.org, self.admin, Trigger.TYPE_KEYWORD, flow, keyword="start")
        Trigger.create(self.org, self.admin, Trigger.TYPE_KEYWORD, flow, keyword="join")

        assert_conflict_resolution(archived=trigger1, not_archived=trigger2)

        trigger1 = Trigger.create(self.org, self.admin, Trigger.TYPE_KEYWORD, flow, groups=(group1,), keyword="join")
        trigger2 = Trigger.create(self.org, self.admin, Trigger.TYPE_KEYWORD, flow, groups=(group2,), keyword="join")
        Trigger.create(self.org, self.admin, Trigger.TYPE_KEYWORD, flow, groups=(group1,), keyword="join")

        assert_conflict_resolution(archived=trigger1, not_archived=trigger2)

        # incoming call triggers conflict if groups match
        trigger1 = Trigger.create(self.org, self.admin, Trigger.TYPE_INBOUND_CALL, flow, groups=(group1,))
        trigger2 = Trigger.create(self.org, self.admin, Trigger.TYPE_INBOUND_CALL, flow, groups=(group2,))
        Trigger.create(self.org, self.admin, Trigger.TYPE_INBOUND_CALL, flow, groups=(group1,))

        assert_conflict_resolution(archived=trigger1, not_archived=trigger2)

        # missed call triggers always conflict
        trigger1 = Trigger.create(self.org, self.admin, Trigger.TYPE_MISSED_CALL, flow)
        trigger2 = Trigger.create(self.org, self.admin, Trigger.TYPE_MISSED_CALL, flow)

        assert_conflict_resolution(archived=trigger1, not_archived=trigger2)

        # new conversation triggers conflict if channels match
        trigger1 = Trigger.create(self.org, self.admin, Trigger.TYPE_REFERRAL, flow, channel=channel1)
        trigger2 = Trigger.create(self.org, self.admin, Trigger.TYPE_REFERRAL, flow, channel=channel2)
        Trigger.create(self.org, self.admin, Trigger.TYPE_REFERRAL, flow, channel=channel1)

        assert_conflict_resolution(archived=trigger1, not_archived=trigger2)

        # referral triggers conflict if referral ids match
        trigger1 = Trigger.create(self.org, self.admin, Trigger.TYPE_REFERRAL, flow, referrer_id="12345")
        trigger2 = Trigger.create(self.org, self.admin, Trigger.TYPE_REFERRAL, flow, referrer_id="23456")
        Trigger.create(self.org, self.admin, Trigger.TYPE_REFERRAL, flow, referrer_id="12345")

        assert_conflict_resolution(archived=trigger1, not_archived=trigger2)

    def test_export_import(self):
        # tweak our current channel to be twitter so we can create a channel-based trigger
        Channel.objects.filter(id=self.channel.id).update(channel_type="TT")
        flow = self.create_flow()

        doctors = self.create_group("Doctors", contacts=[])
        farmers = self.create_group("Farmers", contacts=[])
        testers = self.create_group("Testers", contacts=[])

        # create a trigger on this flow for the new conversation actions but only on some groups
        Trigger.create(
            self.org,
            self.admin,
            Trigger.TYPE_NEW_CONVERSATION,
            flow,
            groups=[doctors, farmers],
            exclude_groups=[testers],
            channel=self.channel,
        )

        # export as a dependency of our flow
        components = self.org.resolve_dependencies([flow], [], include_triggers=True)
        export = self.org.export_definitions("http://rapidpro.io", components)

        # remove our trigger
        Trigger.objects.all().delete()

        # and reimport them.. trigger should be recreated
        self.org.import_app(export, self.admin)

        trigger = Trigger.objects.get()
        self.assertEqual(Trigger.TYPE_NEW_CONVERSATION, trigger.trigger_type)
        self.assertEqual(flow, trigger.flow)
        self.assertEqual(self.channel, trigger.channel)
        self.assertEqual({doctors, farmers}, set(trigger.groups.all()))
        self.assertEqual({testers}, set(trigger.exclude_groups.all()))

        # reimporting again over the top of that shouldn't change the trigger or create any others
        self.org.import_app(export, self.admin)

        trigger = Trigger.objects.get()
        self.assertEqual(Trigger.TYPE_NEW_CONVERSATION, trigger.trigger_type)
        self.assertEqual(flow, trigger.flow)
        self.assertEqual(self.channel, trigger.channel)
        self.assertEqual({doctors, farmers}, set(trigger.groups.all()))
        self.assertEqual({testers}, set(trigger.exclude_groups.all()))

        trigger.archive(self.admin)

        # reimporting again over the top of an archived exact match should restore it
        self.org.import_app(export, self.admin)

        trigger = Trigger.objects.get()
        self.assertFalse(trigger.is_archived)

        trigger.flow = self.create_flow("Another Flow")
        trigger.save(update_fields=("flow",))

        # reimporting again now that our trigger points to a different flow, should archive it and create a new one
        self.org.import_app(export, self.admin)

        trigger.refresh_from_db()
        self.assertTrue(trigger.is_archived)

        new_trigger = Trigger.objects.exclude(id=trigger.id).get()
        self.assertEqual(Trigger.TYPE_NEW_CONVERSATION, new_trigger.trigger_type)
        self.assertEqual(flow, new_trigger.flow)

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


class TriggerCRUDLTest(TembaTest, CRUDLTestMixin):
    def test_create(self):
        create_url = reverse("triggers.trigger_create")
        create_new_convo_url = reverse("triggers.trigger_create_new_conversation")
        create_inbound_call_url = reverse("triggers.trigger_create_inbound_call")
        create_missed_call_url = reverse("triggers.trigger_create_missed_call")

        self.assertLoginRedirect(self.client.get(create_url))

        self.login(self.user)
        self.assertLoginRedirect(self.client.get(create_url))

        self.login(self.admin)
        response = self.client.get(create_url)

        # call triggers can be made without a call channel
        self.assertContains(response, create_inbound_call_url)
        self.assertContains(response, create_missed_call_url)

        # but a new conversation trigger can't be created with a suitable channel
        self.assertNotContains(response, create_new_convo_url)

        # create a facebook channel
        self.create_channel("FB", "Facebook Channel", "1234567")

        response = self.client.get(create_url)
        self.assertContains(response, create_new_convo_url)

    def test_create_keyword(self):
        create_url = reverse("triggers.trigger_create_keyword")
        flow1 = self.create_flow("Flow 1", flow_type=Flow.TYPE_MESSAGE)
        flow2 = self.create_flow("Flow 2", flow_type=Flow.TYPE_VOICE)

        # flows that shouldn't appear as options
        self.create_flow(flow_type=Flow.TYPE_BACKGROUND)
        self.create_flow(is_system=True)

        group1 = self.create_group("Group 1", contacts=[])
        group2 = self.create_group("Group 2", contacts=[])

        response = self.assertCreateFetch(
            create_url,
            allow_viewers=False,
            allow_editors=True,
            form_fields=["keyword", "match_type", "flow", "groups", "exclude_groups"],
        )

        # flow options should show messaging and voice flows
        self.assertEqual([flow1, flow2], list(response.context["form"].fields["flow"].queryset))

        # group options are any group
        self.assertEqual([group1, group2], list(response.context["form"].fields["groups"].queryset))
        self.assertEqual([group1, group2], list(response.context["form"].fields["exclude_groups"].queryset))

        # try a keyword with spaces
        self.assertCreateSubmit(
            create_url,
            {"keyword": "with spaces", "flow": flow1.id, "match_type": "F"},
            form_errors={"keyword": "Must be a single word containing only letters and numbers."},
        )

        # try a keyword with special characters
        self.assertCreateSubmit(
            create_url,
            {"keyword": "keyw!o^rd__", "flow": flow1.id, "match_type": "F"},
            form_errors={"keyword": "Must be a single word containing only letters and numbers."},
        )

        # try with group as both inclusion and exclusion
        self.assertCreateSubmit(
            create_url,
            {
                "keyword": "start",
                "flow": flow1.id,
                "match_type": "F",
                "groups": [group1.id, group2.id],
                "exclude_groups": [group1.id],
            },
            form_errors={"__all__": "Can't include and exclude the same group."},
        )

        # create a trigger with no groups
        self.assertCreateSubmit(
            create_url,
            {"keyword": "start", "flow": flow1.id, "match_type": "F"},
            new_obj_query=Trigger.objects.filter(keyword="start", flow=flow1),
            success_status=200,
        )

        # creating triggers with non-ASCII keywords
        self.assertCreateSubmit(
            create_url,
            {"keyword": "١٠٠", "flow": flow1.id, "match_type": "F"},
            new_obj_query=Trigger.objects.filter(keyword="١٠٠", flow=flow1),
            success_status=200,
        )
        self.assertCreateSubmit(
            create_url,
            {"keyword": "मिलाए", "flow": flow1.id, "match_type": "F"},
            new_obj_query=Trigger.objects.filter(keyword="मिलाए", flow=flow1),
            success_status=200,
        )

        # try a duplicate keyword
        self.assertCreateSubmit(
            create_url,
            {"keyword": "start", "flow": flow2.id, "match_type": "F"},
            form_errors={"__all__": "There already exists a trigger of this type with these options."},
        )

        # works if we specify a group
        self.assertCreateSubmit(
            create_url,
            {"keyword": "start", "flow": flow2.id, "match_type": "F", "groups": group1.id},
            new_obj_query=Trigger.objects.filter(keyword="start", flow=flow2, groups=group1),
            success_status=200,
        )

        # groups between triggers can't overlap
        self.assertCreateSubmit(
            create_url,
            {"keyword": "start", "flow": flow2.id, "match_type": "F", "groups": [group1.id, group2.id]},
            form_errors={"__all__": "There already exists a trigger of this type with these options."},
        )

    def test_create_register(self):
        create_url = reverse("triggers.trigger_create_register")
        group1 = self.create_group(name="Chat", contacts=[])
        group2 = self.create_group(name="Testers", contacts=[])
        flow1 = self.create_flow("Flow 1")

        response = self.assertCreateFetch(
            create_url,
            allow_viewers=False,
            allow_editors=True,
            form_fields=["keyword", "action_join_group", "response", "flow", "groups", "exclude_groups"],
        )

        # group options are any group
        self.assertEqual([group1, group2], list(response.context["form"].fields["action_join_group"].queryset))

        self.assertCreateSubmit(
            create_url,
            {"keyword": "join", "action_join_group": group1.id, "response": "Thanks for joining", "flow": flow1.id},
            new_obj_query=Trigger.objects.filter(keyword="join", flow__name="Join Chat"),
            success_status=200,
        )

        # did our group join flow get created?
        flow = Flow.objects.get(flow_type=Flow.TYPE_MESSAGE, name="Join Chat")
        flow_def = flow.get_definition()

        self.assertEqual(1, len(flow_def["nodes"]))
        self.assertEqual(
            ["add_contact_groups", "set_contact_name", "send_msg", "enter_flow"],
            [a["type"] for a in flow_def["nodes"][0]["actions"]],
        )

        # check that our trigger exists and shows our group
        trigger = Trigger.objects.get(keyword="join", flow=flow)
        self.assertEqual(trigger.flow.name, "Join Chat")

        # the org has no language, so it should be a 'base' flow
        self.assertEqual(flow.base_language, "base")

        # try creating a join group on an org with a language
        language = Language.create(self.org, self.admin, "Spanish", "spa")
        self.org.primary_language = language
        self.org.save(update_fields=("primary_language",))

        self.assertCreateSubmit(
            create_url,
            {"keyword": "join2", "action_join_group": group2.id, "response": "Thanks for joining", "flow": flow1.id},
            new_obj_query=Trigger.objects.filter(keyword="join2", flow__name="Join Testers"),
            success_status=200,
        )

        flow = Flow.objects.get(flow_type=Flow.TYPE_MESSAGE, name="Join Testers")
        self.assertEqual(flow.base_language, "spa")

    def test_create_register_no_response_or_flow(self):
        create_url = reverse("triggers.trigger_create_register")
        group = self.create_group(name="Chat", contacts=[])

        # create a trigger that sets up a group join flow without a response or secondary flow
        self.assertCreateSubmit(
            create_url,
            {"action_join_group": group.id, "keyword": "join"},
            new_obj_query=Trigger.objects.filter(keyword="join", flow__name="Join Chat"),
            success_status=200,
        )

        # did our group join flow get created?
        flow = Flow.objects.get(flow_type=Flow.TYPE_MESSAGE)
        flow_def = flow.get_definition()

        self.assertEqual(1, len(flow_def["nodes"]))
        self.assertEqual(
            ["add_contact_groups", "set_contact_name"], [a["type"] for a in flow_def["nodes"][0]["actions"]]
        )

    def test_create_and_update_schedule(self):
        create_url = reverse("triggers.trigger_create_schedule")

        self.login(self.admin)

        flow1 = self.create_flow("Flow 1", flow_type=Flow.TYPE_MESSAGE)
        flow2 = self.create_flow("Flow 2", flow_type=Flow.TYPE_BACKGROUND)
        flow3 = self.create_flow("Flow 3", flow_type=Flow.TYPE_VOICE)

        # flows that shouldn't appear as options
        self.create_flow("Flow 4", flow_type=Flow.TYPE_SURVEY)
        self.create_flow("Flow 5", is_system=True)

        chester = self.create_contact("Chester", phone="+250788987654")
        shinoda = self.create_contact("Shinoda", phone="+250234213455")
        linkin_park = self.create_group("Linkin Park", [chester, shinoda])
        stromae = self.create_contact("Stromae", phone="+250788645323")

        response = self.assertCreateFetch(
            create_url,
            allow_viewers=False,
            allow_editors=True,
            form_fields=["flow", "omnibox", "repeat_period", "repeat_days_of_week", "start_datetime"],
        )

        # check we allow messaging, voice and background flows
        self.assertEqual([flow1, flow2, flow3], list(response.context["form"].fields["flow"].queryset))

        now = timezone.now()
        tommorrow = now + timedelta(days=1)

        # try to create trigger without a flow or omnibox
        self.assertCreateSubmit(
            create_url,
            {
                "repeat_period": "D",
                "start": "later",
                "start_datetime": datetime_to_str(tommorrow, "%Y-%m-%d %H:%M", self.org.timezone),
            },
            form_errors={"flow": "This field is required.", "omnibox": "This field is required."},
        )

        self.assertEqual(0, Trigger.objects.count())
        self.assertEqual(0, Schedule.objects.count())

        omnibox_selection = omnibox_serialize(self.org, [linkin_park], [stromae], True)

        # now actually create some scheduled triggers
        self.assertCreateSubmit(
            create_url,
            {
                "flow": flow1.id,
                "omnibox": omnibox_selection,
                "repeat_period": "D",
                "start": "later",
                "start_datetime": datetime_to_str(tommorrow, "%Y-%m-%d %H:%M", self.org.timezone),
            },
            new_obj_query=Trigger.objects.filter(trigger_type=Trigger.TYPE_SCHEDULE, flow=flow1),
            success_status=200,
        )

        self.client.post(
            create_url,
            {
                "flow": flow2.id,
                "omnibox": omnibox_selection,
                "repeat_period": "D",
                "start": "later",
                "start_datetime": datetime_to_str(tommorrow, "%Y-%m-%d %H:%M", self.org.timezone),
            },
            new_obj_query=Trigger.objects.filter(trigger_type=Trigger.TYPE_SCHEDULE, flow=flow2),
            success_status=200,
        )

        trigger = Trigger.objects.order_by("id").last()

        self.assertIsNotNone(trigger.schedule)
        self.assertEqual("D", trigger.schedule.repeat_period)
        self.assertEqual({linkin_park}, set(trigger.groups.all()))
        self.assertEqual({stromae}, set(trigger.contacts.all()))

        update_url = reverse("triggers.trigger_update", args=[trigger.id])

        # try to update a trigger without a flow or omnibox
        self.assertUpdateSubmit(
            update_url,
            {
                "repeat_period": "O",
                "start": "later",
                "start_datetime": datetime_to_str(now, "%Y-%m-%d %H:%M", self.org.timezone),
            },
            form_errors={"flow": "This field is required.", "omnibox": "This field is required."},
            object_unchanged=trigger,
        )

        # provide flow this time, update contact
        self.assertUpdateSubmit(
            update_url,
            {
                "flow": flow1.id,
                "omnibox": omnibox_serialize(self.org, [linkin_park], [shinoda], True),
                "repeat_period": "D",
                "start": "later",
                "start_datetime": datetime_to_str(now, "%Y-%m-%d %H:%M", self.org.timezone),
            },
        )

        trigger.refresh_from_db()

        self.assertIsNotNone(trigger.schedule)
        self.assertEqual("D", trigger.schedule.repeat_period)
        self.assertIsNotNone(trigger.schedule.next_fire)
        self.assertEqual({linkin_park}, set(trigger.groups.all()))
        self.assertEqual({shinoda}, set(trigger.contacts.all()))

        # can't submit weekly repeat without specifying the days to repeat on
        self.assertUpdateSubmit(
            update_url,
            {
                "flow": flow1.id,
                "omnibox": omnibox_selection,
                "repeat_period": "W",
                "start": "later",
                "start_datetime": datetime_to_str(now, "%Y-%m-%d %H:%M", self.org.timezone),
            },
            form_errors={"__all__": "Must specify at least one day of the week"},
            object_unchanged=trigger,
        )

        # or submit with invalid days
        self.assertUpdateSubmit(
            update_url,
            {
                "flow": flow1.id,
                "omnibox": omnibox_selection,
                "repeat_period": "W",
                "repeat_days_of_week": "X",
                "start": "later",
                "start_datetime": datetime_to_str(now, "%Y-%m-%d %H:%M", self.org.timezone),
            },
            form_errors={"repeat_days_of_week": "Select a valid choice. X is not one of the available choices."},
            object_unchanged=trigger,
        )

    def test_create_inbound_call(self):
        # make our channel support ivr
        self.channel.role += Channel.ROLE_CALL + Channel.ROLE_ANSWER
        self.channel.save()

        flow1 = self.create_flow("Flow 1", flow_type=Flow.TYPE_VOICE)
        flow2 = self.create_flow("Flow 2", flow_type=Flow.TYPE_VOICE)
        group1 = self.create_group("Group 1", contacts=[])
        group2 = self.create_group("Group 2", contacts=[])

        # flows that shouldn't appear as options
        self.create_flow("Flow 3", flow_type=Flow.TYPE_MESSAGE)
        self.create_flow("Flow 4", flow_type=Flow.TYPE_BACKGROUND)
        self.create_flow("Flow 5", is_system=True)
        self.create_flow("Flow 6", org=self.org2)

        create_url = reverse("triggers.trigger_create_inbound_call")

        response = self.assertCreateFetch(
            create_url, allow_viewers=False, allow_editors=True, form_fields=["flow", "groups", "exclude_groups"]
        )

        # flow options should only be voice flows
        self.assertEqual([flow1, flow2], list(response.context["form"].fields["flow"].queryset))

        self.assertCreateSubmit(
            create_url,
            {"flow": flow1.id, "groups": group1.id},
            new_obj_query=Trigger.objects.filter(flow=flow1, trigger_type=Trigger.TYPE_INBOUND_CALL),
            success_status=200,
        )

        # can't create another inbound call trigger for same group
        self.assertCreateSubmit(
            create_url,
            {"flow": flow2.id, "groups": group1.id},
            form_errors={"__all__": "There already exists a trigger of this type with these options."},
        )

        # but can for different group
        self.assertCreateSubmit(
            create_url,
            {"flow": flow2.id, "groups": group2.id},
            new_obj_query=Trigger.objects.filter(flow=flow2, trigger_type=Trigger.TYPE_INBOUND_CALL),
            success_status=200,
        )

    def test_create_missed_call(self):
        # make our channel support ivr
        self.channel.role += Channel.ROLE_CALL + Channel.ROLE_ANSWER
        self.channel.save()

        flow1 = self.create_flow("Flow 1", flow_type=Flow.TYPE_VOICE)
        flow2 = self.create_flow("Flow 2", flow_type=Flow.TYPE_VOICE)
        flow3 = self.create_flow("Flow 3", flow_type=Flow.TYPE_MESSAGE)

        # flows that shouldn't appear as options
        self.create_flow("Flow 4", flow_type=Flow.TYPE_BACKGROUND)
        self.create_flow("Flow 5", is_system=True)
        self.create_flow("Flow 6", org=self.org2)

        create_url = reverse("triggers.trigger_create_missed_call")

        response = self.assertCreateFetch(
            create_url, allow_viewers=False, allow_editors=True, form_fields=["flow", "groups", "exclude_groups"]
        )

        # flow options should be messaging and voice flows
        self.assertEqual([flow1, flow2, flow3], list(response.context["form"].fields["flow"].queryset))

        self.assertCreateSubmit(
            create_url,
            {"flow": flow1.id},
            new_obj_query=Trigger.objects.filter(flow=flow1, trigger_type=Trigger.TYPE_MISSED_CALL),
            success_status=200,
        )

        # we can't create another...
        self.assertCreateSubmit(
            create_url,
            {"flow": flow2.id},
            form_errors={"__all__": "There already exists a trigger of this type with these options."},
        )

    @patch("temba.channels.types.facebook.FacebookType.activate_trigger")
    @patch("temba.channels.types.viber_public.ViberPublicType.activate_trigger")
    def test_create_new_conversation(self, mock_vp_activate, mock_fb_activate):
        create_url = reverse("triggers.trigger_create_new_conversation")
        flow1 = self.create_flow("Flow 1", flow_type=Flow.TYPE_MESSAGE)
        flow2 = self.create_flow("Flow 2", flow_type=Flow.TYPE_MESSAGE)

        # flows that shouldn't appear as options
        self.create_flow("Flow 3", flow_type=Flow.TYPE_VOICE)
        self.create_flow("Flow 4", flow_type=Flow.TYPE_BACKGROUND)
        self.create_flow("Flow 5", is_system=True)

        channel1 = self.create_channel("FB", "Facebook Channel", "1234567")
        channel2 = self.create_channel("VP", "Viber Channel", "1234567")
        self.create_channel("A", "Android Channel", "+1234")

        response = self.assertCreateFetch(
            create_url,
            allow_viewers=False,
            allow_editors=True,
            form_fields=["channel", "flow", "groups", "exclude_groups"],
        )

        # flow options should show messaging and voice flows
        self.assertEqual([flow1, flow2], list(response.context["form"].fields["flow"].queryset))

        # channel options should only be channels that support conversations
        self.assertEqual([channel1, channel2], list(response.context["form"].fields["channel"].queryset))

        # go create it
        self.assertCreateSubmit(
            create_url,
            {"channel": channel1.id, "flow": flow1.id},
            new_obj_query=Trigger.objects.filter(
                trigger_type=Trigger.TYPE_NEW_CONVERSATION, is_active=True, is_archived=False, channel=channel1
            ),
            success_status=200,
        )
        self.assertEqual(mock_fb_activate.call_count, 1)

        # try to create another one, fails as we already have a trigger for that channel
        self.assertCreateSubmit(
            create_url,
            {"channel": channel1.id, "flow": flow1.id},
            form_errors={"__all__": "There already exists a trigger of this type with these options."},
        )

        # but can create a different trigger for a different channel
        self.assertCreateSubmit(
            create_url,
            {"channel": channel2.id, "flow": flow1.id},
            new_obj_query=Trigger.objects.filter(
                trigger_type=Trigger.TYPE_NEW_CONVERSATION, is_active=True, is_archived=False, channel=channel2
            ),
            success_status=200,
        )
        self.assertEqual(mock_vp_activate.call_count, 1)

    @patch("temba.channels.types.facebook.FacebookType.activate_trigger")
    def test_create_referral(self, mock_fb_activate):
        create_url = reverse("triggers.trigger_create_referral")
        flow1 = self.create_flow("Flow 1", flow_type=Flow.TYPE_MESSAGE)
        flow2 = self.create_flow("Flow 2", flow_type=Flow.TYPE_MESSAGE)

        # flows that shouldn't appear as options
        self.create_flow("Flow 3", flow_type=Flow.TYPE_VOICE)
        self.create_flow("Flow 4", flow_type=Flow.TYPE_BACKGROUND)
        self.create_flow("Flow 5", is_system=True)

        channel1 = self.create_channel("FB", "Facebook 1", "1234567")
        channel2 = self.create_channel("FB", "Facebook 2", "2345678")
        self.create_channel("A", "Android Channel", "+1234")

        response = self.assertCreateFetch(
            create_url,
            allow_viewers=False,
            allow_editors=True,
            form_fields=["channel", "referrer_id", "flow", "groups", "exclude_groups"],
        )

        # flow options should show messaging and voice flows
        self.assertEqual([flow1, flow2], list(response.context["form"].fields["flow"].queryset))

        # channel options should only be channels that support referrals
        self.assertEqual([channel1, channel2], list(response.context["form"].fields["channel"].queryset))

        # go create it
        self.assertCreateSubmit(
            create_url,
            {"channel": channel1.id, "flow": flow1.id, "referrer_id": "234567"},
            new_obj_query=Trigger.objects.filter(
                trigger_type=Trigger.TYPE_REFERRAL, channel=channel1, referrer_id="234567"
            ),
            success_status=200,
        )
        self.assertEqual(mock_fb_activate.call_count, 1)

        # try to create another one, fails as we already have a trigger for that channel and referrer
        self.assertCreateSubmit(
            create_url,
            {"channel": channel1.id, "flow": flow1.id, "referrer_id": "234567"},
            form_errors={"__all__": "There already exists a trigger of this type with these options."},
        )

        # but can create a different trigger for a different referrer
        self.assertCreateSubmit(
            create_url,
            {"channel": channel1.id, "flow": flow1.id, "referrer_id": "345678"},
            new_obj_query=Trigger.objects.filter(
                trigger_type=Trigger.TYPE_REFERRAL, channel=channel1, referrer_id="345678"
            ),
            success_status=200,
        )

        # or blank referrer
        self.assertCreateSubmit(
            create_url,
            {"channel": channel2.id, "flow": flow1.id, "referrer_id": ""},
            new_obj_query=Trigger.objects.filter(trigger_type=Trigger.TYPE_REFERRAL, channel=channel2, referrer_id=""),
            success_status=200,
        )

        # or channel
        self.assertCreateSubmit(
            create_url,
            {"channel": channel2.id, "flow": flow1.id, "referrer_id": "234567"},
            new_obj_query=Trigger.objects.filter(
                trigger_type=Trigger.TYPE_REFERRAL, channel=channel2, referrer_id="234567"
            ),
            success_status=200,
        )

    def test_create_catchall(self):
        create_url = reverse("triggers.trigger_create_catchall")
        flow1 = self.create_flow("Flow 1", flow_type=Flow.TYPE_MESSAGE)
        flow2 = self.create_flow("Flow 2", flow_type=Flow.TYPE_VOICE)

        # flows that shouldn't appear as options
        self.create_flow(flow_type=Flow.TYPE_BACKGROUND)
        self.create_flow(is_system=True)

        group1 = self.create_group("Group 1", contacts=[])
        group2 = self.create_group("Group 2", contacts=[])

        response = self.assertCreateFetch(
            create_url,
            allow_viewers=False,
            allow_editors=True,
            form_fields=["flow", "groups", "exclude_groups"],
        )

        # flow options should show messaging and voice flows
        self.assertEqual([flow1, flow2], list(response.context["form"].fields["flow"].queryset))

        # group options are any group
        self.assertEqual([group1, group2], list(response.context["form"].fields["groups"].queryset))

        # create a trigger with no groups
        self.assertCreateSubmit(
            create_url,
            {"flow": flow1.id},
            new_obj_query=Trigger.objects.filter(trigger_type=Trigger.TYPE_CATCH_ALL, flow=flow1),
            success_status=200,
        )

        # try a duplicate catch all with no groups
        self.assertCreateSubmit(
            create_url,
            {"flow": flow2.id},
            form_errors={"__all__": "There already exists a trigger of this type with these options."},
        )

        # works if we specify a group
        self.assertCreateSubmit(
            create_url,
            {"flow": flow2.id, "groups": group1.id},
            new_obj_query=Trigger.objects.filter(trigger_type=Trigger.TYPE_CATCH_ALL, flow=flow2),
            success_status=200,
        )

        # groups between triggers can't overlap
        self.assertCreateSubmit(
            create_url,
            {"flow": flow2.id, "groups": [group1.id, group2.id]},
            form_errors={"__all__": "There already exists a trigger of this type with these options."},
        )

    def test_create_closed_ticket(self):
        flow1 = self.create_flow("Flow 1", flow_type=Flow.TYPE_MESSAGE)
        flow2 = self.create_flow("Flow 2", flow_type=Flow.TYPE_VOICE)
        flow3 = self.create_flow("Flow 3", flow_type=Flow.TYPE_BACKGROUND)

        # flows that shouldn't appear as options
        self.create_flow("Flow 4", is_system=True)
        self.create_flow("Flow 5", org=self.org2)

        create_url = reverse("triggers.trigger_create_closed_ticket")

        response = self.assertCreateFetch(
            create_url, allow_viewers=False, allow_editors=True, form_fields=["flow", "groups", "exclude_groups"]
        )

        # flow options should be messaging, voice and background flows
        self.assertEqual([flow1, flow2, flow3], list(response.context["form"].fields["flow"].queryset))

        self.assertCreateSubmit(
            create_url,
            {"flow": flow1.id},
            new_obj_query=Trigger.objects.filter(flow=flow1, trigger_type=Trigger.TYPE_CLOSED_TICKET),
            success_status=200,
        )

        # we can't create another...
        self.assertCreateSubmit(
            create_url,
            {"flow": flow2.id},
            form_errors={"__all__": "There already exists a trigger of this type with these options."},
        )

    def test_update_keyword(self):
        flow = self.create_flow()
        group1 = self.create_group("Chat", contacts=[])
        group2 = self.create_group("Testers", contacts=[])
        group3 = self.create_group("Doctors", contacts=[])
        trigger = Trigger.create(self.org, self.admin, Trigger.TYPE_KEYWORD, flow, keyword="join")
        trigger.groups.add(group1)

        update_url = reverse("triggers.trigger_update", args=[trigger.id])

        self.assertUpdateFetch(
            update_url,
            allow_viewers=False,
            allow_editors=True,
            form_fields=["keyword", "match_type", "flow", "groups", "exclude_groups"],
        )

        # submit with valid keyword and extra group
        self.assertUpdateSubmit(
            update_url,
            {
                "keyword": "kiki",
                "flow": flow.id,
                "match_type": "O",
                "groups": [group1.id, group2.id],
                "exclude_groups": [group3.id],
            },
        )

        trigger.refresh_from_db()
        self.assertEqual("kiki", trigger.keyword)
        self.assertEqual(flow, trigger.flow)
        self.assertEqual(Trigger.MATCH_ONLY_WORD, trigger.match_type)
        self.assertEqual({group1, group2}, set(trigger.groups.all()))
        self.assertEqual({group3}, set(trigger.exclude_groups.all()))

        # error if keyword is not defined
        self.assertUpdateSubmit(
            update_url,
            {"keyword": "", "flow": flow.id, "match_type": "F"},
            form_errors={"keyword": "Must be a single word containing only letters and numbers."},
            object_unchanged=trigger,
        )

    def test_list(self):
        flow1 = self.create_flow("Report")
        flow2 = self.create_flow("Survey")
        trigger1 = Trigger.create(self.org, self.admin, Trigger.TYPE_KEYWORD, flow1, keyword="test")
        trigger2 = Trigger.create(self.org, self.admin, Trigger.TYPE_KEYWORD, flow2, keyword="abc")
        trigger3 = Trigger.create(self.org, self.admin, Trigger.TYPE_KEYWORD, flow1, keyword="start")

        Trigger.create(self.org, self.admin, Trigger.TYPE_KEYWORD, flow1, keyword="archived", is_archived=True)
        Trigger.create(self.org, self.admin, Trigger.TYPE_KEYWORD, flow1, keyword="inactive", is_active=False)
        Trigger.create(self.org2, self.admin, Trigger.TYPE_KEYWORD, self.create_flow(org=self.org2), keyword="other")

        list_url = reverse("triggers.trigger_list")

        response = self.assertListFetch(
            list_url, allow_viewers=True, allow_editors=True, context_objects=[trigger2, trigger3, trigger1]
        )
        self.assertEqual(("archive",), response.context["actions"])

        # can search by keyword
        self.assertListFetch(
            list_url + "?search=Sta", allow_viewers=True, allow_editors=True, context_objects=[trigger3]
        )

        # or flow name
        self.assertListFetch(
            list_url + "?search=VEY", allow_viewers=True, allow_editors=True, context_objects=[trigger2]
        )

        # can archive it
        self.client.post(list_url, {"action": "archive", "objects": trigger3.id})

        trigger3.refresh_from_db()
        self.assertTrue(trigger3.is_archived)

        # no longer appears in list
        self.assertListFetch(list_url, allow_viewers=True, allow_editors=True, context_objects=[trigger2, trigger1])

    def test_list_redirect_when_no_triggers(self):
        Trigger.objects.all().delete()

        self.login(self.admin)
        response = self.client.get(reverse("triggers.trigger_list"))
        self.assertEqual(response.status_code, 302)
        self.assertRedirect(response, reverse("triggers.trigger_create"))

    def test_archived(self):
        flow = self.create_flow()
        trigger1 = Trigger.create(self.org, self.admin, Trigger.TYPE_KEYWORD, flow, keyword="start", is_archived=True)
        trigger2 = Trigger.create(self.org, self.admin, Trigger.TYPE_KEYWORD, flow, keyword="join", is_archived=True)

        # triggers that shouldn't appear
        Trigger.create(self.org, self.admin, Trigger.TYPE_KEYWORD, flow, keyword="active", is_archived=False)
        Trigger.create(self.org, self.admin, Trigger.TYPE_KEYWORD, flow, keyword="inactive", is_active=False)
        Trigger.create(self.org2, self.admin, Trigger.TYPE_KEYWORD, self.create_flow(org=self.org2), keyword="other")

        archived_url = reverse("triggers.trigger_archived")

        response = self.assertListFetch(
            archived_url, allow_viewers=True, allow_editors=True, context_objects=[trigger2, trigger1]
        )
        self.assertEqual(("restore",), response.context["actions"])

        # can restore it
        self.client.post(reverse("triggers.trigger_archived"), {"action": "restore", "objects": trigger1.id})

        response = self.client.get(reverse("triggers.trigger_archived"))

        self.assertNotContains(response, "startkeyword")

        response = self.client.get(reverse("triggers.trigger_list"))

        # should be back in the main trigger list
        self.assertContains(response, "start")

        # once archived we can duplicate it but with one active at a time
        trigger = Trigger.objects.get(keyword="start")
        trigger.is_archived = True
        trigger.save(update_fields=("is_archived",))

        post_data = dict(keyword="start", flow=flow.id, match_type="F")
        response = self.client.post(reverse("triggers.trigger_create_keyword"), data=post_data)
        self.assertEqual(Trigger.objects.filter(keyword="start").count(), 2)
        self.assertEqual(1, Trigger.objects.filter(keyword="start", is_archived=False).count())
        other_trigger = Trigger.objects.filter(keyword="start", is_archived=False)[0]
        self.assertFalse(trigger.pk == other_trigger.pk)

        # try archiving it we have one archived and the other active
        response = self.client.get(reverse("triggers.trigger_archived"), post_data)
        self.assertContains(response, "start")
        post_data = dict(action="restore", objects=trigger.pk)
        self.client.post(reverse("triggers.trigger_archived"), post_data)
        response = self.client.get(reverse("triggers.trigger_archived"), post_data)
        self.assertContains(response, "start")
        response = self.client.get(reverse("triggers.trigger_list"), post_data)
        self.assertContains(response, "start")
        self.assertEqual(1, Trigger.objects.filter(keyword="start", is_archived=False).count())
        self.assertNotEqual(other_trigger, Trigger.objects.filter(keyword="start", is_archived=False)[0])

        self.contact = self.create_contact("Eric", phone="+250788382382")
        self.contact2 = self.create_contact("Nic", phone="+250788383383")
        group1 = self.create_group("first", [self.contact2])
        group2 = self.create_group("second", [self.contact])
        group3 = self.create_group("third", [self.contact, self.contact2])

        self.assertEqual(Trigger.objects.filter(keyword="start").count(), 2)
        self.assertEqual(Trigger.objects.filter(keyword="start", is_archived=False).count(), 1)

        # update trigger with 2 groups
        post_data = dict(keyword="start", flow=flow.id, match_type="F", groups=[group1.pk, group2.pk])
        response = self.client.post(reverse("triggers.trigger_create_keyword"), data=post_data)
        self.assertEqual(Trigger.objects.filter(keyword="start").count(), 3)
        self.assertEqual(Trigger.objects.filter(keyword="start", is_archived=False).count(), 2)

        # get error when groups overlap
        post_data = dict(keyword="start", flow=flow.id, match_type="F")
        post_data["groups"] = [group2.pk, group3.pk]
        response = self.client.post(reverse("triggers.trigger_create_keyword"), data=post_data)
        self.assertEqual(1, len(response.context["form"].errors))
        self.assertEqual(Trigger.objects.filter(keyword="start").count(), 3)
        self.assertEqual(Trigger.objects.filter(keyword="start", is_archived=False).count(), 2)

        # allow new creation when groups do not overlap
        post_data = dict(keyword="start", flow=flow.id, match_type="F")
        post_data["groups"] = [group3.pk]
        self.client.post(reverse("triggers.trigger_create_keyword"), data=post_data)
        self.assertEqual(Trigger.objects.filter(keyword="start").count(), 4)
        self.assertEqual(Trigger.objects.filter(keyword="start", is_archived=False).count(), 3)

    def test_type_lists(self):
        flow1 = self.create_flow("Flow 1")
        flow2 = self.create_flow("Flow 2")
        trigger1 = Trigger.create(self.org, self.admin, Trigger.TYPE_KEYWORD, flow1, keyword="test")
        trigger2 = Trigger.create(self.org, self.admin, Trigger.TYPE_KEYWORD, flow2, keyword="abc")
        trigger3 = Trigger.create(self.org, self.admin, Trigger.TYPE_REFERRAL, flow1, referrer_id="234")
        trigger4 = Trigger.create(self.org, self.admin, Trigger.TYPE_REFERRAL, flow2, referrer_id="456")
        trigger5 = Trigger.create(self.org, self.admin, Trigger.TYPE_CATCH_ALL, flow1)
        Trigger.create(self.org2, self.admin, Trigger.TYPE_KEYWORD, self.create_flow(org=self.org2), keyword="other")

        keywords_url = reverse("triggers.trigger_type", kwargs={"folder": "keywords"})
        socials_url = reverse("triggers.trigger_type", kwargs={"folder": "social"})
        catchall_url = reverse("triggers.trigger_type", kwargs={"folder": "catchall"})

        response = self.assertListFetch(
            keywords_url, allow_viewers=True, allow_editors=True, context_objects=[trigger2, trigger1]
        )
        self.assertEqual(("archive",), response.context["actions"])

        # can search by keyword
        self.assertListFetch(
            keywords_url + "?search=TES", allow_viewers=True, allow_editors=True, context_objects=[trigger1]
        )

        self.assertListFetch(socials_url, allow_viewers=True, allow_editors=True, context_objects=[trigger3, trigger4])
        self.assertListFetch(catchall_url, allow_viewers=True, allow_editors=True, context_objects=[trigger5])
