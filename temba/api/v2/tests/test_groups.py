from django.test import override_settings
from django.urls import reverse

from temba.campaigns.models import Campaign
from temba.contacts.models import ContactGroup
from temba.tests import mock_mailroom
from temba.triggers.models import Trigger

from . import APITest


class GroupsEndpointTest(APITest):
    @override_settings(ORG_LIMIT_DEFAULTS={"groups": 10})
    @mock_mailroom
    def test_endpoint(self, mr_mocks):
        endpoint_url = reverse("api.v2.groups") + ".json"

        self.assertGetNotPermitted(endpoint_url, [None])
        self.assertPostNotPermitted(endpoint_url, [None, self.user, self.agent])
        self.assertDeleteNotPermitted(endpoint_url, [None, self.user, self.agent])

        frank = self.create_contact("Frank", urns=["facebook:123456"])
        self.create_field("isdeveloper", "Is developer")
        open_tickets = self.org.groups.get(name="Open Tickets")
        customers = self.create_group("Customers", [frank])
        developers = self.create_group("Developers", query='isdeveloper = "YES"')
        ContactGroup.objects.filter(id=developers.id).update(status=ContactGroup.STATUS_READY)

        dynamic = self.create_group("Big Group", query='isdeveloper = "NO"')
        ContactGroup.objects.filter(id=dynamic.id).update(status=ContactGroup.STATUS_EVALUATING)

        # an initializing group
        ContactGroup.create_manual(self.org, self.admin, "Initializing", status=ContactGroup.STATUS_INITIALIZING)

        # group belong to other org
        spammers = ContactGroup.get_or_create(self.org2, self.admin2, "Spammers")

        # no filtering
        self.assertGet(
            endpoint_url,
            [self.user, self.editor],
            results=[
                {
                    "uuid": dynamic.uuid,
                    "name": "Big Group",
                    "query": 'isdeveloper = "NO"',
                    "status": "evaluating",
                    "system": False,
                    "count": 0,
                },
                {
                    "uuid": developers.uuid,
                    "name": "Developers",
                    "query": 'isdeveloper = "YES"',
                    "status": "ready",
                    "system": False,
                    "count": 0,
                },
                {
                    "uuid": customers.uuid,
                    "name": "Customers",
                    "query": None,
                    "status": "ready",
                    "system": False,
                    "count": 1,
                },
                {
                    "uuid": open_tickets.uuid,
                    "name": "Open Tickets",
                    "query": "tickets > 0",
                    "status": "ready",
                    "system": True,
                    "count": 0,
                },
            ],
            num_queries=self.BASE_SESSION_QUERIES + 2,
        )

        # filter by UUID
        self.assertGet(endpoint_url + f"?uuid={customers.uuid}", [self.editor], results=[customers])

        # filter by name
        self.assertGet(endpoint_url + "?name=developers", [self.editor], results=[developers])

        # try to filter by both
        self.assertGet(
            endpoint_url + f"?uuid={customers.uuid}&name=developers",
            [self.editor],
            errors={None: "You may only specify one of the uuid, name parameters"},
        )

        # try to create empty group
        self.assertPost(endpoint_url, self.admin, {}, errors={"name": "This field is required."})

        # create new group
        response = self.assertPost(endpoint_url, self.admin, {"name": "Reporters"}, status=201)

        reporters = ContactGroup.objects.get(name="Reporters")
        self.assertEqual(
            response.json(),
            {
                "uuid": reporters.uuid,
                "name": "Reporters",
                "query": None,
                "status": "ready",
                "system": False,
                "count": 0,
            },
        )

        # try to create another group with same name
        self.assertPost(endpoint_url, self.admin, {"name": "reporters"}, errors={"name": "This field must be unique."})

        # it's fine if a group in another org has that name
        self.assertPost(endpoint_url, self.admin, {"name": "Spammers"}, status=201)

        # try to create a group with invalid name
        self.assertPost(
            endpoint_url, self.admin, {"name": '"People"'}, errors={"name": 'Cannot contain the character: "'}
        )

        # try to create a group with name that's too long
        self.assertPost(
            endpoint_url,
            self.admin,
            {"name": "x" * 65},
            errors={"name": "Ensure this field has no more than 64 characters."},
        )

        # update group by UUID
        self.assertPost(endpoint_url + f"?uuid={reporters.uuid}", self.admin, {"name": "U-Reporters"})

        reporters.refresh_from_db()
        self.assertEqual(reporters.name, "U-Reporters")

        # can't update a system group
        self.assertPost(
            endpoint_url + f"?uuid={open_tickets.uuid}",
            self.admin,
            {"name": "Won't work"},
            errors={None: "Cannot modify system object."},
            status=403,
        )
        self.assertTrue(self.org.groups.filter(name="Open Tickets").exists())

        # can't update a group from other org
        self.assertPost(endpoint_url + f"?uuid={spammers.uuid}", self.admin, {"name": "Won't work"}, status=404)

        # try an empty delete request
        self.assertDelete(
            endpoint_url, self.admin, errors={None: "URL must contain one of the following parameters: uuid"}
        )

        # delete a group by UUID
        self.assertDelete(endpoint_url + f"?uuid={reporters.uuid}", self.admin, status=204)

        reporters.refresh_from_db()
        self.assertFalse(reporters.is_active)

        # can't delete a system group
        self.assertDelete(
            endpoint_url + f"?uuid={open_tickets.uuid}",
            self.admin,
            errors={None: "Cannot delete system object."},
            status=403,
        )
        self.assertTrue(self.org.groups.filter(name="Open Tickets").exists())

        # can't delete a group with a trigger dependency
        trigger = Trigger.create(
            self.org,
            self.admin,
            Trigger.TYPE_KEYWORD,
            self.create_flow("Test"),
            keywords=["block_group"],
            match_type=Trigger.MATCH_FIRST_WORD,
        )
        trigger.groups.add(customers)

        self.assertDelete(
            endpoint_url + f"?uuid={customers.uuid}",
            self.admin,
            errors={None: "Group is being used by triggers which must be archived first."},
            status=400,
        )

        # or a campaign dependency
        trigger.groups.clear()
        campaign = Campaign.create(self.org, self.admin, "Reminders", customers)

        self.assertDelete(
            endpoint_url + f"?uuid={customers.uuid}",
            self.admin,
            errors={None: "Group is being used by campaigns which must be archived first."},
            status=400,
        )

        # can't delete a group in another org
        self.assertDelete(endpoint_url + f"?uuid={spammers.uuid}", self.admin, status=404)

        campaign.delete()
        for group in ContactGroup.objects.filter(is_system=False):
            group.release(self.admin)

        for i in range(10):
            ContactGroup.create_manual(self.org2, self.admin2, "group%d" % i)

        self.assertPost(endpoint_url, self.admin, {"name": "Reporters"}, status=201)

        ContactGroup.objects.filter(is_system=False, is_active=True).delete()

        for i in range(10):
            ContactGroup.create_manual(self.org, self.admin, "group%d" % i)

        self.assertPost(
            endpoint_url,
            self.admin,
            {"name": "Reporters"},
            errors={None: "Cannot create object because workspace has reached limit of 10."},
            status=409,
        )
