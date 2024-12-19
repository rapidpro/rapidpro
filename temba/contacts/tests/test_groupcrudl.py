from datetime import timedelta

from django.test.utils import override_settings
from django.urls import reverse
from django.utils import timezone

from temba import mailroom
from temba.campaigns.models import Campaign
from temba.contacts.models import ContactGroup
from temba.schedules.models import Schedule
from temba.tests import CRUDLTestMixin, TembaTest, mock_mailroom
from temba.triggers.models import Trigger


class ContactGroupCRUDLTest(TembaTest, CRUDLTestMixin):
    def setUp(self):
        super().setUp()

        self.joe = self.create_contact("Joe Blow", phone="123")
        self.frank = self.create_contact("Frank Smith", urns=["tel:1234", "twitter:hola"])

        self.joe_and_frank = self.create_group("Customers", [self.joe, self.frank])

        self.other_org_group = self.create_group("Customers", contacts=[], org=self.org2)

    @override_settings(ORG_LIMIT_DEFAULTS={"groups": 10})
    @mock_mailroom
    def test_create(self, mr_mocks):
        url = reverse("contacts.contactgroup_create")

        # can't create group as viewer
        self.login(self.user)
        response = self.client.post(url, {"name": "Spammers"})
        self.assertLoginRedirect(response)

        self.login(self.admin)

        # try to create a contact group whose name is only whitespace
        response = self.client.post(url, {"name": "  "})
        self.assertFormError(response.context["form"], "name", "This field is required.")

        # try to create a contact group whose name contains a disallowed character
        response = self.client.post(url, {"name": '"People"'})
        self.assertFormError(response.context["form"], "name", 'Cannot contain the character: "')

        # try to create a contact group whose name is too long
        response = self.client.post(url, {"name": "X" * 65})
        self.assertFormError(
            response.context["form"], "name", "Ensure this value has at most 64 characters (it has 65)."
        )

        # try to create with name that's already taken
        response = self.client.post(url, {"name": "Customers"})
        self.assertFormError(response.context["form"], "name", "Already used by another group.")

        # create with valid name (that will be trimmed)
        response = self.client.post(url, {"name": "first  "})
        self.assertNoFormErrors(response)
        ContactGroup.objects.get(org=self.org, name="first")

        # create a group with preselected contacts
        self.client.post(url, {"name": "Everybody", "preselected_contacts": f"{self.joe.id},{self.frank.id}"})
        group = ContactGroup.objects.get(org=self.org, name="Everybody")
        self.assertEqual(set(group.contacts.all()), {self.joe, self.frank})

        # create a dynamic group using a query
        self.client.post(url, {"name": "Frank", "group_query": "tel = 1234"})

        ContactGroup.objects.get(org=self.org, name="Frank", query="tel = 1234")

        for group in ContactGroup.objects.filter(is_system=False):
            group.release(self.admin)

        for i in range(10):
            ContactGroup.create_manual(self.org2, self.admin2, "group%d" % i)

        response = self.client.post(url, {"name": "People"})
        self.assertNoFormErrors(response)
        ContactGroup.objects.get(org=self.org, name="People")

        for group in ContactGroup.objects.filter(is_system=False):
            group.release(self.admin)

        for i in range(10):
            ContactGroup.create_manual(self.org, self.admin, "group%d" % i)

        self.assertEqual(10, ContactGroup.objects.filter(is_active=True, is_system=False).count())
        response = self.client.post(url, {"name": "People"})
        self.assertFormError(
            response.context["form"],
            "name",
            "This workspace has reached its limit of 10 groups. You must delete existing ones before you can create new ones.",
        )

    def test_create_disallow_duplicates(self):
        self.login(self.admin)

        self.client.post(reverse("contacts.contactgroup_create"), dict(name="First Group"))

        # assert it was created
        ContactGroup.objects.get(name="First Group")

        # try to create another group with the same name, but a dynamic query, should fail
        response = self.client.post(
            reverse("contacts.contactgroup_create"), dict(name="First Group", group_query="firsts")
        )
        self.assertFormError(response.context["form"], "name", "Already used by another group.")

        # try to create another group with same name, not dynamic, same thing
        response = self.client.post(
            reverse("contacts.contactgroup_create"), dict(name="First Group", group_query="firsts")
        )
        self.assertFormError(response.context["form"], "name", "Already used by another group.")

    @mock_mailroom
    def test_update(self, mr_mocks):
        manual = self.create_group("Customers", [self.joe, self.frank])
        smart = self.create_group("Dynamic", query="tel is 1234")
        open_tickets = self.org.groups.get(name="Open Tickets")

        update_url = reverse("contacts.contactgroup_update", args=[manual.id])

        self.assertRequestDisallowed(update_url, [None, self.user, self.agent, self.admin2])

        self.assertUpdateFetch(update_url, [self.editor, self.admin], form_fields=("name",))

        # try to update name to only whitespace
        self.assertUpdateSubmit(
            update_url,
            self.admin,
            {"name": "    "},
            form_errors={"name": "This field is required."},
            object_unchanged=manual,
        )

        # try to update name to contain a disallowed character
        self.assertUpdateSubmit(
            update_url,
            self.admin,
            {"name": '"People"'},
            form_errors={"name": 'Cannot contain the character: "'},
            object_unchanged=manual,
        )

        # update with valid name (that will be trimmed)
        self.assertUpdateSubmit(update_url, self.admin, {"name": "new name   "})

        manual.refresh_from_db()
        self.assertEqual(manual.name, "new name")

        # now try a smart group
        update_url = reverse("contacts.contactgroup_update", args=[smart.id])

        # mark our group as ready
        smart.status = ContactGroup.STATUS_READY
        smart.save(update_fields=("status",))

        self.assertUpdateFetch(update_url, [self.editor, self.admin], form_fields=("name", "query"))

        # simulate submitting an unparseable query
        mr_mocks.exception(mailroom.QueryValidationException("error at !", "syntax"))

        self.assertUpdateSubmit(
            update_url,
            self.admin,
            {"name": "Frank", "query": "(!))!)"},
            form_errors={"query": "Invalid query syntax."},
            object_unchanged=smart,
        )

        # or a query that depends on id
        self.assertUpdateSubmit(
            update_url,
            self.admin,
            {"name": "Frank", "query": "id = 123"},
            form_errors={"query": 'You cannot create a smart group based on "id" or "group".'},
            object_unchanged=smart,
        )

        # update with valid query
        self.assertUpdateSubmit(update_url, self.admin, {"name": "Frank", "query": 'twitter = "hola"'})

        smart.refresh_from_db()
        self.assertEqual(smart.query, 'twitter = "hola"')

        # mark our dynamic group as evaluating
        smart.status = ContactGroup.STATUS_EVALUATING
        smart.save(update_fields=("status",))

        # and check we can't change the query while that is the case
        self.assertUpdateSubmit(
            update_url,
            self.admin,
            {"name": "Frank", "query": 'twitter = "hello"'},
            form_errors={"query": "You cannot update the query of a group that is populating."},
            object_unchanged=smart,
        )

        # but can change the name
        self.assertUpdateSubmit(update_url, self.admin, {"name": "Frank2", "query": 'twitter = "hola"'})

        smart.refresh_from_db()
        self.assertEqual(smart.name, "Frank2")

        # try to update a system group
        response = self.requestView(reverse("contacts.contactgroup_update", args=[open_tickets.id]), self.admin)
        self.assertEqual(404, response.status_code)

    def test_usages(self):
        flow = self.get_flow("dependencies", name="Dependencies")
        group = ContactGroup.objects.get(name="Cat Facts")

        campaign1 = Campaign.create(self.org, self.admin, "Planting Reminders", group)
        campaign2 = Campaign.create(self.org, self.admin, "Deleted", group)
        campaign2.is_active = False
        campaign2.save(update_fields=("is_active",))

        trigger1 = Trigger.create(
            self.org,
            self.admin,
            Trigger.TYPE_KEYWORD,
            flow,
            keywords=["test1"],
            match_type=Trigger.MATCH_FIRST_WORD,
            groups=[group],
        )
        trigger2 = Trigger.create(
            self.org,
            self.admin,
            Trigger.TYPE_KEYWORD,
            flow,
            keywords=["test2"],
            match_type=Trigger.MATCH_FIRST_WORD,
            exclude_groups=[group],
        )

        usages_url = reverse("contacts.contactgroup_usages", args=[group.uuid])

        self.assertRequestDisallowed(usages_url, [None, self.agent, self.admin2])
        response = self.assertReadFetch(usages_url, [self.user, self.editor, self.admin], context_object=group)

        self.assertEqual(
            {"flow": [flow], "campaign": [campaign1], "trigger": [trigger1, trigger2]},
            {t: list(qs) for t, qs in response.context["dependents"].items()},
        )

    def test_delete(self):
        # create a group which isn't used by anything
        group1 = self.create_group("Group 1", contacts=[])

        # create a group which is used only by a flow (soft dependency)
        group2 = self.create_group("Group 2", contacts=[])
        flow1 = self.create_flow("Flow 1")
        flow1.group_dependencies.add(group2)

        # create a group which is used by a flow (soft) and a scheduled trigger (soft)
        group3 = self.create_group("Group 3", contacts=[])
        flow2 = self.create_flow("Flow 2")
        flow2.group_dependencies.add(group3)
        schedule1 = Schedule.create(self.org, timezone.now() + timedelta(days=3), Schedule.REPEAT_DAILY)
        trigger1 = Trigger.create(
            self.org,
            self.admin,
            trigger_type=Trigger.TYPE_SCHEDULE,
            flow=flow2,
            keywords=["trigger1"],
            match_type=Trigger.MATCH_FIRST_WORD,
            groups=[group3.id],
            schedule=schedule1,
        )
        self.assertEqual(1, group3.triggers.count())
        self.assertEqual(trigger1, group3.triggers.get(is_active=True, keywords=trigger1.keywords))

        # create a group which is used by a flow (soft), a trigger (soft), and a campaign (hard dependency)
        group4 = self.create_group("Group 4", contacts=[])
        flow3 = self.create_flow("Flow 3")
        flow3.group_dependencies.add(group4)
        trigger2 = Trigger.create(
            self.org,
            self.admin,
            Trigger.TYPE_KEYWORD,
            flow3,
            keywords=["trigger2"],
            match_type=Trigger.MATCH_FIRST_WORD,
            groups=[group4],
        )
        campaign1 = Campaign.create(self.org, self.admin, "Planting Reminders", group4)

        delete_group1_url = reverse("contacts.contactgroup_delete", args=[group1.uuid])
        delete_group2_url = reverse("contacts.contactgroup_delete", args=[group2.uuid])
        delete_group3_url = reverse("contacts.contactgroup_delete", args=[group3.uuid])
        delete_group4_url = reverse("contacts.contactgroup_delete", args=[group4.uuid])

        self.assertRequestDisallowed(delete_group1_url, [None, self.user, self.agent, self.admin2])

        # a group with no dependents can be deleted
        response = self.assertDeleteFetch(delete_group1_url, [self.editor, self.admin])

        self.assertEqual({}, response.context["soft_dependents"])
        self.assertEqual({}, response.context["hard_dependents"])
        self.assertContains(response, "You are about to delete")
        self.assertContains(response, "There is no way to undo this. Are you sure?")

        self.assertDeleteSubmit(delete_group1_url, self.admin, object_deactivated=group1, success_status=200)

        # a group with only soft dependents can be deleted but we give warnings
        response = self.assertDeleteFetch(delete_group2_url, [self.editor])

        self.assertEqual({"flow"}, set(response.context["soft_dependents"].keys()))
        self.assertEqual({}, response.context["hard_dependents"])
        self.assertContains(response, "is used by the following items but can still be deleted:")
        self.assertContains(response, flow1.name)
        self.assertContains(response, "There is no way to undo this. Are you sure?")

        self.assertDeleteSubmit(delete_group2_url, self.admin, object_deactivated=group2, success_status=200)

        # check that the flow is now marked as having issues
        flow1.refresh_from_db()
        self.assertTrue(flow1.has_issues)
        self.assertNotIn(group2, flow1.field_dependencies.all())

        # a group with only soft dependents can be deleted but we give warnings
        response = self.assertDeleteFetch(delete_group3_url, [self.admin])

        self.assertEqual({"flow", "trigger"}, set(response.context["soft_dependents"].keys()))
        self.assertEqual({}, response.context["hard_dependents"])
        self.assertContains(response, "is used by the following items but can still be deleted:")
        self.assertContains(response, flow2.name)
        self.assertContains(response, f"Schedule → {flow2.name}")
        self.assertContains(response, "There is no way to undo this. Are you sure?")

        self.assertDeleteSubmit(delete_group3_url, self.admin, object_deactivated=group3, success_status=200)

        # check that the flow is now marked as having issues
        flow2.refresh_from_db()
        self.assertTrue(flow2.has_issues)
        self.assertNotIn(group3, flow2.field_dependencies.all())

        # check that the trigger is released
        trigger1.refresh_from_db()
        self.assertFalse(trigger1.is_active)

        # a group with hard dependents can't be deleted
        response = self.assertDeleteFetch(delete_group4_url, [self.admin])

        self.assertEqual({"flow", "trigger"}, set(response.context["soft_dependents"].keys()))
        self.assertEqual({"campaign"}, set(response.context["hard_dependents"].keys()))
        self.assertContains(response, "can't be deleted as it is still used by the following items:")
        self.assertContains(response, campaign1.name)
        self.assertNotContains(response, "Delete")

        # check that the flow is not deleted
        flow3.refresh_from_db()
        self.assertTrue(flow3.is_active)

        # check that the trigger is not released
        trigger2.refresh_from_db()
        self.assertTrue(trigger2.is_active)

        # check that the campaign is not deleted
        campaign1.refresh_from_db()
        self.assertTrue(campaign1.is_active)
