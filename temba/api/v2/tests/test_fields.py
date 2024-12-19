from django.test import override_settings
from django.urls import reverse

from temba.campaigns.models import Campaign, CampaignEvent
from temba.contacts.models import ContactField

from . import APITest


class FieldsEndpointTest(APITest):
    @override_settings(ORG_LIMIT_DEFAULTS={"fields": 10})
    def test_endpoint(self):
        endpoint_url = reverse("api.v2.fields") + ".json"

        self.assertGetNotPermitted(endpoint_url, [None])
        self.assertPostNotPermitted(endpoint_url, [None, self.user])
        self.assertDeleteNotAllowed(endpoint_url)

        nick_name = self.create_field("nick_name", "Nick Name", agent_access=ContactField.ACCESS_EDIT)
        registered = self.create_field("registered", "Registered On", value_type=ContactField.TYPE_DATETIME)
        self.create_field("not_ours", "Something Else", org=self.org2)

        # add our date field to some campaign events
        campaign = Campaign.create(self.org, self.admin, "Reminders", self.create_group("Farmers"))
        CampaignEvent.create_flow_event(
            self.org, self.admin, campaign, registered, offset=1, unit="W", flow=self.create_flow("Event 1")
        )
        CampaignEvent.create_flow_event(
            self.org, self.admin, campaign, registered, offset=2, unit="W", flow=self.create_flow("Event 2")
        )

        # and some regular flows
        self.create_flow("Flow 1").field_dependencies.add(registered)
        self.create_flow("Flow 2").field_dependencies.add(registered)
        self.create_flow("Flow 3").field_dependencies.add(registered)

        # and a group
        self.create_group("Farmers").query_fields.add(registered)

        deleted = self.create_field("deleted", "Deleted")
        deleted.release(self.admin)

        # no filtering
        self.assertGet(
            endpoint_url,
            [self.user, self.editor],
            results=[
                {
                    "key": "registered",
                    "name": "Registered On",
                    "type": "datetime",
                    "featured": False,
                    "priority": 0,
                    "usages": {"campaign_events": 2, "flows": 3, "groups": 1},
                    "agent_access": "view",
                    "label": "Registered On",
                    "value_type": "datetime",
                },
                {
                    "key": "nick_name",
                    "name": "Nick Name",
                    "type": "text",
                    "featured": False,
                    "priority": 0,
                    "usages": {"campaign_events": 0, "flows": 0, "groups": 0},
                    "agent_access": "edit",
                    "label": "Nick Name",
                    "value_type": "text",
                },
            ],
            num_queries=self.BASE_SESSION_QUERIES + 1,
        )

        # filter by key
        self.assertGet(endpoint_url + "?key=nick_name", [self.editor], results=[nick_name])

        # try to create empty field
        self.assertPost(endpoint_url, self.admin, {}, errors={"non_field_errors": "Field 'name' is required."})

        # try to create field without type
        self.assertPost(
            endpoint_url, self.admin, {"name": "goats"}, errors={"non_field_errors": "Field 'type' is required."}
        )

        # try again with some invalid values
        self.assertPost(
            endpoint_url,
            self.admin,
            {"name": "!@#$%", "type": "video"},
            errors={"name": "Can only contain letters, numbers and hypens.", "type": '"video" is not a valid choice.'},
        )

        # try again with some invalid values using deprecated field names
        self.assertPost(
            endpoint_url,
            self.admin,
            {"label": "!@#$%", "value_type": "video"},
            errors={
                "label": "Can only contain letters, numbers and hypens.",
                "value_type": '"video" is not a valid choice.',
            },
        )

        # try again with a label that would generate an invalid key
        self.assertPost(
            endpoint_url,
            self.admin,
            {"name": "HAS", "type": "text"},
            errors={"name": 'Generated key "has" is invalid or a reserved name.'},
        )

        # try again with a label that's already taken
        self.assertPost(
            endpoint_url,
            self.admin,
            {"label": "nick name", "value_type": "text"},
            errors={"label": "This field must be unique."},
        )

        # create a new field
        self.assertPost(endpoint_url, self.editor, {"name": "Age", "type": "number"}, status=201)

        age = ContactField.objects.get(
            org=self.org, name="Age", value_type="N", is_proxy=False, is_system=False, is_active=True
        )

        # update a field by its key
        self.assertPost(endpoint_url + "?key=age", self.admin, {"name": "Real Age", "type": "datetime"})
        age.refresh_from_db()
        self.assertEqual(age.name, "Real Age")
        self.assertEqual(age.value_type, "D")

        # try to update with key of deleted field
        self.assertPost(endpoint_url + "?key=deleted", self.admin, {"name": "Something", "type": "text"}, status=404)

        # try to update with non-existent key
        self.assertPost(endpoint_url + "?key=not_ours", self.admin, {"name": "Something", "type": "text"}, status=404)

        # try to change type of date field used by campaign event
        self.assertPost(
            endpoint_url + "?key=registered",
            self.admin,
            {"name": "Registered", "type": "text"},
            errors={"type": "Can't change type of date field being used by campaign events."},
        )

        CampaignEvent.objects.all().delete()
        ContactField.objects.filter(is_system=False).delete()

        for i in range(10):
            self.create_field("field%d" % i, "Field%d" % i)

        self.assertPost(
            endpoint_url,
            self.admin,
            {"label": "Age", "value_type": "numeric"},
            errors={None: "Cannot create object because workspace has reached limit of 10."},
            status=409,
        )
