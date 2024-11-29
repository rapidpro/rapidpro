from django.test.utils import override_settings
from django.urls import reverse

from temba.campaigns.models import Campaign, CampaignEvent
from temba.contacts.models import ContactField
from temba.tests import CRUDLTestMixin, TembaTest, mock_mailroom


class ContactFieldCRUDLTest(TembaTest, CRUDLTestMixin):
    def setUp(self):
        super().setUp()

        self.age = self.create_field("age", "Age", value_type="N", show_in_table=True)
        self.gender = self.create_field("gender", "Gender", value_type="T")
        self.state = self.create_field("state", "State", value_type="S")

        self.deleted = self.create_field("foo", "Foo")
        self.deleted.is_active = False
        self.deleted.save(update_fields=("is_active",))

        self.other_org_field = self.create_field("other", "Other", org=self.org2)

    def test_create(self):
        create_url = reverse("contacts.contactfield_create")

        self.assertRequestDisallowed(create_url, [None, self.user, self.agent])

        # for a deploy that doesn't have locations feature, don't show location field types
        with override_settings(FEATURES={}):
            response = self.assertCreateFetch(
                create_url,
                [self.editor, self.admin],
                form_fields=["name", "value_type", "show_in_table", "agent_access"],
            )
            self.assertEqual(
                [("T", "Text"), ("N", "Number"), ("D", "Date & Time")],
                response.context["form"].fields["value_type"].choices,
            )

        response = self.assertCreateFetch(
            create_url,
            [self.editor, self.admin],
            form_fields=["name", "value_type", "show_in_table", "agent_access"],
        )
        self.assertEqual(
            [("T", "Text"), ("N", "Number"), ("D", "Date & Time"), ("S", "State"), ("I", "District"), ("W", "Ward")],
            response.context["form"].fields["value_type"].choices,
        )

        # try to submit with empty name
        self.assertCreateSubmit(
            create_url,
            self.admin,
            {"name": "", "value_type": "T", "show_in_table": True, "agent_access": "E"},
            form_errors={"name": "This field is required."},
        )

        # try to submit with invalid name
        self.assertCreateSubmit(
            create_url,
            self.admin,
            {"name": "???", "value_type": "T", "show_in_table": True, "agent_access": "E"},
            form_errors={"name": "Can only contain letters, numbers and hypens."},
        )

        # try to submit with something that would be an invalid key
        self.assertCreateSubmit(
            create_url,
            self.admin,
            {"name": "HAS", "value_type": "T", "show_in_table": True, "agent_access": "E"},
            form_errors={"name": "Can't be a reserved word."},
        )

        # try to submit with name of existing field
        self.assertCreateSubmit(
            create_url,
            self.admin,
            {"name": "AGE", "value_type": "N", "show_in_table": True, "agent_access": "E"},
            form_errors={"name": "Must be unique."},
        )

        # submit with valid data
        self.assertCreateSubmit(
            create_url,
            self.admin,
            {"name": "Goats", "value_type": "N", "show_in_table": True, "agent_access": "E"},
            new_obj_query=ContactField.user_fields.filter(
                org=self.org, name="Goats", value_type="N", show_in_table=True, agent_access="E"
            ),
            success_status=200,
        )

        # it's also ok to create a field with the same name as a deleted field
        ContactField.user_fields.get(key="age").release(self.admin)

        self.assertCreateSubmit(
            create_url,
            self.admin,
            {"name": "Age", "value_type": "N", "show_in_table": True, "agent_access": "N"},
            new_obj_query=ContactField.user_fields.filter(
                org=self.org, name="Age", value_type="N", show_in_table=True, agent_access="N", is_active=True
            ),
            success_status=200,
        )

        # simulate an org which has reached the limit for fields
        with override_settings(ORG_LIMIT_DEFAULTS={"fields": 2}):
            self.assertCreateSubmit(
                create_url,
                self.admin,
                {"name": "Sheep", "value_type": "T", "show_in_table": True, "agent_access": "E"},
                form_errors={
                    "__all__": "This workspace has reached its limit of 2 fields. You must delete existing ones before you can create new ones."
                },
            )

    def test_update(self):
        update_url = reverse("contacts.contactfield_update", args=[self.age.key])

        self.assertRequestDisallowed(update_url, [None, self.user, self.agent, self.admin2])

        # for a deploy that doesn't have locations feature, don't show location field types
        with override_settings(FEATURES={}):
            response = self.assertUpdateFetch(
                update_url,
                [self.editor, self.admin],
                form_fields={"name": "Age", "value_type": "N", "show_in_table": True, "agent_access": "V"},
            )
            self.assertEqual(3, len(response.context["form"].fields["value_type"].choices))

        response = self.assertUpdateFetch(
            update_url,
            [self.editor, self.admin],
            form_fields={"name": "Age", "value_type": "N", "show_in_table": True, "agent_access": "V"},
        )
        self.assertEqual(6, len(response.context["form"].fields["value_type"].choices))

        # try submit without change
        self.assertUpdateSubmit(
            update_url,
            self.admin,
            {"name": "Age", "value_type": "N", "show_in_table": True, "agent_access": "V"},
            success_status=200,
        )

        # try to submit with empty name
        self.assertUpdateSubmit(
            update_url,
            self.admin,
            {"name": "", "value_type": "N", "show_in_table": True, "agent_access": "V"},
            form_errors={"name": "This field is required."},
            object_unchanged=self.age,
        )

        # try to submit with invalid name
        self.assertUpdateSubmit(
            update_url,
            self.admin,
            {"name": "???", "value_type": "N", "show_in_table": True, "agent_access": "V"},
            form_errors={"name": "Can only contain letters, numbers and hypens."},
            object_unchanged=self.age,
        )

        # try to submit with a name that is used by another field
        self.assertUpdateSubmit(
            update_url,
            self.admin,
            {"name": "GENDER", "value_type": "N", "show_in_table": True, "agent_access": "V"},
            form_errors={"name": "Must be unique."},
            object_unchanged=self.age,
        )

        # submit with different name, type and agent access
        self.assertUpdateSubmit(
            update_url,
            self.admin,
            {"name": "Age In Years", "value_type": "T", "show_in_table": False, "agent_access": "E"},
            success_status=200,
        )

        self.age.refresh_from_db()
        self.assertEqual("Age In Years", self.age.name)
        self.assertEqual("T", self.age.value_type)
        self.assertFalse(self.age.show_in_table)
        self.assertEqual("E", self.age.agent_access)

        # simulate an org which has reached the limit for fields - should still be able to update a field
        with override_settings(ORG_LIMIT_DEFAULTS={"fields": 2}):
            self.assertUpdateSubmit(
                update_url,
                self.admin,
                {"name": "Age 2", "value_type": "T", "show_in_table": True, "agent_access": "E"},
                success_status=200,
            )

        self.age.refresh_from_db()
        self.assertEqual("Age 2", self.age.name)

        # create a date field used in a campaign event
        registered = self.create_field("registered", "Registered", value_type="D")
        campaign = Campaign.create(self.org, self.admin, "Reminders", self.create_group("Farmers"))
        CampaignEvent.create_flow_event(
            self.org, self.admin, campaign, registered, offset=1, unit="W", flow=self.create_flow("Test")
        )

        update_url = reverse("contacts.contactfield_update", args=[registered.key])

        self.assertUpdateFetch(
            update_url,
            [self.editor, self.admin],
            form_fields={"name": "Registered", "value_type": "D", "show_in_table": False, "agent_access": "V"},
        )

        # try to submit with different type
        self.assertUpdateSubmit(
            update_url,
            self.admin,
            {"name": "Registered", "value_type": "T", "show_in_table": False, "agent_access": "V"},
            form_errors={"value_type": "Can't change type of date field being used by campaign events."},
            object_unchanged=registered,
        )

        # submit with only a different name
        self.assertUpdateSubmit(
            update_url,
            self.admin,
            {"name": "Registered On", "value_type": "D", "show_in_table": False, "agent_access": "V"},
            success_status=200,
        )

        registered.refresh_from_db()
        self.assertEqual("Registered On", registered.name)
        self.assertEqual("D", registered.value_type)
        self.assertFalse(registered.show_in_table)

    def test_list(self):
        list_url = reverse("contacts.contactfield_list")

        self.assertRequestDisallowed(list_url, [None, self.agent])
        self.assertListFetch(
            list_url, [self.user, self.editor, self.admin], context_objects=[self.age, self.gender, self.state]
        )
        self.assertContentMenu(list_url, self.user, [])
        self.assertContentMenu(list_url, self.admin, ["New"])

    def test_create_warnings(self):
        self.login(self.admin)
        create_url = reverse("contacts.contactfield_create")
        response = self.client.get(create_url)

        self.assertEqual(3, response.context["total_count"])
        self.assertEqual(250, response.context["total_limit"])
        self.assertNotContains(response, "You have reached the limit")
        self.assertNotContains(response, "You are approaching the limit")

        with override_settings(ORG_LIMIT_DEFAULTS={"fields": 10}):
            response = self.requestView(create_url, self.admin)

            self.assertContains(response, "You are approaching the limit")

        with override_settings(ORG_LIMIT_DEFAULTS={"fields": 3}):
            response = self.requestView(create_url, self.admin)

            self.assertContains(response, "You have reached the limit")

    @mock_mailroom
    def test_usages(self, mr_mocks):
        flow = self.get_flow("dependencies", name="Dependencies")
        field = ContactField.user_fields.filter(is_active=True, org=self.org, key="favorite_cat").get()
        field.value_type = ContactField.TYPE_DATETIME
        field.save(update_fields=("value_type",))

        group = self.create_group("Farmers", query='favorite_cat != ""')
        campaign = Campaign.create(self.org, self.admin, "Planting Reminders", group)

        # create flow events
        event1 = CampaignEvent.create_flow_event(
            self.org,
            self.admin,
            campaign,
            relative_to=field,
            offset=0,
            unit="D",
            flow=flow,
            delivery_hour=17,
        )
        inactive_campaignevent = CampaignEvent.create_flow_event(
            self.org,
            self.admin,
            campaign,
            relative_to=field,
            offset=0,
            unit="D",
            flow=flow,
            delivery_hour=20,
        )
        inactive_campaignevent.is_active = False
        inactive_campaignevent.save(update_fields=("is_active",))

        usages_url = reverse("contacts.contactfield_usages", args=[field.key])

        self.assertRequestDisallowed(usages_url, [None, self.agent, self.admin2])
        response = self.assertReadFetch(usages_url, [self.user, self.editor, self.admin], context_object=field)

        self.assertEqual(
            {"flow": [flow], "group": [group], "campaign_event": [event1]},
            {t: list(qs) for t, qs in response.context["dependents"].items()},
        )

    def test_delete(self):
        # create new field 'Joined On' which is used by a campaign event (soft) and a flow (soft)
        group = self.create_group("Amazing Group", contacts=[])
        joined_on = self.create_field("joined_on", "Joined On", value_type=ContactField.TYPE_DATETIME)
        campaign = Campaign.create(self.org, self.admin, Campaign.get_unique_name(self.org, "Reminders"), group)
        flow = self.create_flow("Amazing Flow")
        flow.field_dependencies.add(joined_on)
        campaign_event = CampaignEvent.create_flow_event(
            self.org, self.admin, campaign, joined_on, offset=1, unit="W", flow=flow, delivery_hour=13
        )

        # make 'Age' appear to be used by a flow (soft) and a group (hard)
        flow.field_dependencies.add(self.age)
        group.query_fields.add(self.age)

        delete_gender_url = reverse("contacts.contactfield_delete", args=[self.gender.key])
        delete_joined_url = reverse("contacts.contactfield_delete", args=[joined_on.key])
        delete_age_url = reverse("contacts.contactfield_delete", args=[self.age.key])

        self.assertRequestDisallowed(delete_gender_url, [None, self.user, self.agent, self.admin2])

        # a field with no dependents can be deleted
        response = self.assertDeleteFetch(delete_gender_url, [self.editor, self.admin])
        self.assertEqual({}, response.context["soft_dependents"])
        self.assertEqual({}, response.context["hard_dependents"])
        self.assertContains(response, "You are about to delete")
        self.assertContains(response, "There is no way to undo this. Are you sure?")

        self.assertDeleteSubmit(delete_gender_url, self.admin, object_deactivated=self.gender, success_status=200)

        # create the same field again
        self.gender = self.create_field("gender", "Gender", value_type="T")

        # since fields are queried by key name, try and delete it again
        # to make sure we aren't deleting the previous deleted field again
        self.assertDeleteSubmit(delete_gender_url, self.admin, object_deactivated=self.gender, success_status=200)
        self.gender.refresh_from_db()
        self.assertFalse(self.gender.is_active)

        # a field with only soft dependents can also be deleted but we give warnings
        response = self.assertDeleteFetch(delete_joined_url, [self.admin])
        self.assertEqual({"flow", "campaign_event"}, set(response.context["soft_dependents"].keys()))
        self.assertEqual({}, response.context["hard_dependents"])
        self.assertContains(response, "is used by the following items but can still be deleted:")
        self.assertContains(response, "Amazing Flow")
        self.assertContains(response, "There is no way to undo this. Are you sure?")

        self.assertDeleteSubmit(delete_joined_url, self.admin, object_deactivated=joined_on, success_status=200)

        # check that flow is now marked as having issues
        flow.refresh_from_db()
        self.assertTrue(flow.has_issues)
        self.assertNotIn(joined_on, flow.field_dependencies.all())

        # and that the campaign event is gone
        campaign_event.refresh_from_db()
        self.assertFalse(campaign_event.is_active)

        # a field with hard dependents can't be deleted
        response = self.assertDeleteFetch(delete_age_url, [self.admin])
        self.assertEqual({"flow"}, set(response.context["soft_dependents"].keys()))
        self.assertEqual({"group"}, set(response.context["hard_dependents"].keys()))
        self.assertContains(response, "can't be deleted as it is still used by the following items:")
        self.assertContains(response, "Amazing Group")
        self.assertNotContains(response, "Delete")
