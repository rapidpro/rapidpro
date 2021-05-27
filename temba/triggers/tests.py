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
from temba.tests import CRUDLTestMixin, MockResponse, TembaTest
from temba.utils.dates import datetime_to_str

from .models import Trigger


class TriggerTest(TembaTest):
    def test_model(self):
        flow = self.create_flow()
        trigger = Trigger.create(self.org, self.admin, Trigger.TYPE_KEYWORD, flow, keyword="join")

        self.assertEqual('Trigger[type=K, flow="Test Flow"]', str(trigger))

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

        create_url = reverse("triggers.trigger_schedule")

        flow = self.create_flow()
        background_flow = self.get_flow("background")
        self.get_flow("media_survey")

        chester = self.create_contact("Chester", phone="+250788987654")
        shinoda = self.create_contact("Shinoda", phone="+250234213455")
        linkin_park = self.create_group("Linkin Park", [chester, shinoda])
        stromae = self.create_contact("Stromae", phone="+250788645323")

        response = self.client.get(create_url)

        # the normal flow and background flow should be options but not the surveyor flow
        self.assertEqual(list(response.context["form"].fields["flow"].queryset), [background_flow, flow])

        now = timezone.now()
        tommorrow = now + timedelta(days=1)
        omnibox_selection = omnibox_serialize(flow.org, [linkin_park], [stromae], True)

        # try to create trigger without a flow or omnibox
        response = self.client.post(
            create_url,
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

        # this time provide a flow but leave out omnibox..
        response = self.client.post(
            create_url,
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
            create_url,
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
            create_url,
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


class TriggerCRUDLTest(TembaTest, CRUDLTestMixin):
    def test_create(self):
        create_url = reverse("triggers.trigger_create")

        self.assertLoginRedirect(self.client.get(create_url))

        self.login(self.user)
        self.assertLoginRedirect(self.client.get(create_url))

        self.login(self.admin)
        response = self.client.get(create_url)

        # inbound call trigger can be made without a call channel
        self.assertContains(response, "Start a flow after receiving a call")

    def test_create_keyword(self):
        create_url = reverse("triggers.trigger_keyword")
        flow1 = self.create_flow("Flow 1", flow_type=Flow.TYPE_MESSAGE)
        flow2 = self.create_flow("Flow 2", flow_type=Flow.TYPE_VOICE)

        # flows that shouldn't appear as options
        self.create_flow(flow_type=Flow.TYPE_BACKGROUND)
        self.create_flow(is_system=True)

        response = self.assertCreateFetch(
            create_url,
            allow_viewers=False,
            allow_editors=True,
            form_fields=["keyword", "match_type", "flow", "groups"],
        )

        # flow options should show messaging and voice flows
        self.assertEqual([flow1, flow2], list(response.context["form"].fields["flow"].queryset))

        # try a keyword with spaces
        self.assertCreateSubmit(
            create_url,
            {"keyword": "with spaces", "flow": flow1.id, "match_type": "F"},
            form_errors={"keyword": "Keywords must be a single word containing only letter and numbers"},
        )

        # try a keyword with special characters
        self.assertCreateSubmit(
            create_url,
            {"keyword": "keyw!o^rd__", "flow": flow1.id, "match_type": "F"},
            form_errors={"keyword": "Keywords must be a single word containing only letter and numbers"},
        )

        # test creating triggers with non-ASCII characters
        self.assertCreateSubmit(
            create_url,
            {"keyword": "١٠٠", "flow": flow1.id, "match_type": "F"},
            new_obj_query=Trigger.objects.filter(keyword="١٠٠", flow=flow1),
        )
        self.assertCreateSubmit(
            create_url,
            {"keyword": "मिलाए", "flow": flow1.id, "match_type": "F"},
            new_obj_query=Trigger.objects.filter(keyword="मिलाए", flow=flow1),
        )

        # and with an ASCII keyword
        self.assertCreateSubmit(
            create_url,
            {"keyword": "startkeyword", "flow": flow1.id, "match_type": "F"},
            new_obj_query=Trigger.objects.filter(keyword="startkeyword", flow=flow1),
        )

        # try a duplicate keyword
        self.assertCreateSubmit(
            create_url,
            {"keyword": "startkeyword", "flow": flow1.id, "match_type": "F"},
            form_errors={"__all__": "An active trigger already exists, triggers must be unique for each group"},
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

        create_url = reverse("triggers.trigger_inbound_call")

        response = self.assertCreateFetch(
            create_url, allow_viewers=False, allow_editors=True, form_fields=["flow", "groups"]
        )

        # flow options should only be voice flows
        self.assertEqual([flow1, flow2], list(response.context["form"].fields["flow"].queryset))

        self.assertCreateSubmit(
            create_url,
            {"flow": flow1.id, "groups": group1.id},
            new_obj_query=Trigger.objects.filter(flow=flow1, trigger_type=Trigger.TYPE_INBOUND_CALL),
        )

        # can't create another inbound call trigger for same group
        self.assertCreateSubmit(
            create_url,
            {"flow": flow2.id, "groups": group1.id},
            form_errors={"__all__": "An active trigger already exists, triggers must be unique for each group"},
        )

        # but can for different group
        self.assertCreateSubmit(
            create_url,
            {"flow": flow2.id, "groups": group2.id},
            new_obj_query=Trigger.objects.filter(flow=flow2, trigger_type=Trigger.TYPE_INBOUND_CALL),
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

        create_url = reverse("triggers.trigger_missed_call")

        response = self.assertCreateFetch(create_url, allow_viewers=False, allow_editors=True, form_fields=["flow"])

        # flow options should be messaging and voice flows
        self.assertEqual([flow1, flow2, flow3], list(response.context["form"].fields["flow"].queryset))

        self.assertCreateSubmit(
            create_url,
            {"flow": flow1.id},
            new_obj_query=Trigger.objects.filter(flow=flow1, trigger_type=Trigger.TYPE_MISSED_CALL),
            success_status=200,
        )

        trigger1 = Trigger.objects.get()

        # we can create another which will archive the first as a conflict
        self.assertCreateSubmit(
            create_url,
            {"flow": flow2.id},
            new_obj_query=Trigger.objects.filter(flow=flow2, trigger_type=Trigger.TYPE_MISSED_CALL),
            success_status=200,
        )

        trigger1.refresh_from_db()
        self.assertTrue(trigger1.is_archived)

    def test_update_keyword(self):
        flow = self.create_flow()
        group1 = self.create_group("Chat", contacts=[])
        group2 = self.create_group("Testers", contacts=[])
        trigger = Trigger.create(self.org, self.admin, Trigger.TYPE_KEYWORD, flow, keyword="join")
        trigger.groups.add(group1)

        update_url = reverse("triggers.trigger_update", args=[trigger.id])

        self.assertUpdateFetch(
            update_url,
            allow_viewers=False,
            allow_editors=True,
            form_fields=["keyword", "match_type", "flow", "groups"],
        )

        # submit with valid keyword and extra group
        self.assertUpdateSubmit(
            update_url, {"keyword": "kiki", "flow": flow.id, "match_type": "O", "groups": [group1.id, group2.id]}
        )

        trigger.refresh_from_db()
        self.assertEqual("kiki", trigger.keyword)
        self.assertEqual(flow, trigger.flow)
        self.assertEqual(Trigger.MATCH_ONLY_WORD, trigger.match_type)
        self.assertEqual({group1, group2}, set(trigger.groups.all()))

        # error if keyword is not defined
        self.assertUpdateSubmit(
            update_url,
            {"keyword": "", "flow": flow.id, "match_type": "F"},
            form_errors={"keyword": "Keywords must be a single word containing only letter and numbers"},
            object_unchanged=trigger,
        )

    def test_list(self):
        flow = self.create_flow()
        trigger1 = Trigger.create(self.org, self.admin, Trigger.TYPE_KEYWORD, flow, keyword="test")
        trigger2 = Trigger.create(self.org, self.admin, Trigger.TYPE_KEYWORD, flow, keyword="abc")
        trigger3 = Trigger.create(self.org, self.admin, Trigger.TYPE_KEYWORD, flow, keyword="start")
        Trigger.create(self.org2, self.admin, Trigger.TYPE_KEYWORD, flow, keyword="other")

        list_url = reverse("triggers.trigger_list")

        response = self.assertListFetch(
            list_url, allow_viewers=True, allow_editors=True, context_objects=[trigger2, trigger3, trigger1]
        )
        self.assertEqual(("archive",), response.context["actions"])

        # can search by keyword
        self.assertListFetch(
            list_url + "?search=Sta", allow_viewers=True, allow_editors=True, context_objects=[trigger3]
        )

        # can archive it
        self.client.post(list_url, {"action": "archive", "objects": trigger3.id})

        trigger3.refresh_from_db()
        self.assertTrue(trigger3.is_archived)

        # no longer appears in list
        self.assertListFetch(list_url, allow_viewers=True, allow_editors=True, context_objects=[trigger2, trigger1])

    def test_archived(self):
        flow = self.create_flow()
        trigger = Trigger.create(
            self.org, self.admin, Trigger.TYPE_KEYWORD, flow, keyword="startkeyword", is_archived=True
        )

        archived_url = reverse("triggers.trigger_archived")

        response = self.assertListFetch(
            archived_url, allow_viewers=True, allow_editors=True, context_objects=[trigger]
        )
        self.assertEqual(("restore",), response.context["actions"])

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

    def test_list_redirect_when_no_triggers(self):
        Trigger.objects.all().delete()

        self.login(self.admin)
        response = self.client.get(reverse("triggers.trigger_list"))
        self.assertEqual(response.status_code, 302)
        self.assertRedirect(response, reverse("triggers.trigger_create"))
