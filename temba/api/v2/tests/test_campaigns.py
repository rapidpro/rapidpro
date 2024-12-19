from django.urls import reverse

from temba.api.v2.serializers import format_datetime
from temba.campaigns.models import Campaign
from temba.contacts.models import ContactGroup

from . import APITest


class CampaignsEndpointTest(APITest):
    def test_endpoint(self):
        endpoint_url = reverse("api.v2.campaigns") + ".json"

        self.assertGetNotPermitted(endpoint_url, [None, self.agent])
        self.assertPostNotPermitted(endpoint_url, [None, self.user, self.agent])
        self.assertDeleteNotAllowed(endpoint_url)

        joe = self.create_contact("Joe Blow", phone="+250788123123")
        frank = self.create_contact("Frank", urns=["facebook:123456"])
        reporters = self.create_group("Reporters", [joe, frank])
        other_group = self.create_group("Others", [])
        campaign1 = Campaign.create(self.org, self.admin, "Reminders #1", reporters)
        campaign2 = Campaign.create(self.org, self.admin, "Reminders #2", reporters)

        # create campaign for other org
        spammers = ContactGroup.get_or_create(self.org2, self.admin2, "Spammers")
        spam = Campaign.create(self.org2, self.admin2, "Spam", spammers)

        # no filtering
        response = self.assertGet(
            endpoint_url,
            [self.user, self.editor, self.admin],
            results=[
                {
                    "uuid": str(campaign2.uuid),
                    "name": "Reminders #2",
                    "archived": False,
                    "group": {"uuid": reporters.uuid, "name": "Reporters"},
                    "created_on": format_datetime(campaign2.created_on),
                },
                {
                    "uuid": str(campaign1.uuid),
                    "name": "Reminders #1",
                    "archived": False,
                    "group": {"uuid": reporters.uuid, "name": "Reporters"},
                    "created_on": format_datetime(campaign1.created_on),
                },
            ],
            num_queries=self.BASE_SESSION_QUERIES + 2,
        )

        # filter by UUID
        self.assertGet(endpoint_url + f"?uuid={campaign1.uuid}", [self.editor], results=[campaign1])

        # try to create empty campaign
        self.assertPost(
            endpoint_url,
            self.editor,
            {},
            errors={"name": "This field is required.", "group": "This field is required."},
        )

        # create new campaign
        response = self.assertPost(
            endpoint_url, self.editor, {"name": "Reminders #3", "group": reporters.uuid}, status=201
        )

        campaign3 = Campaign.objects.get(name="Reminders #3")
        self.assertEqual(
            response.json(),
            {
                "uuid": str(campaign3.uuid),
                "name": "Reminders #3",
                "archived": False,
                "group": {"uuid": reporters.uuid, "name": "Reporters"},
                "created_on": format_datetime(campaign3.created_on),
            },
        )

        # try to create another campaign with same name
        self.assertPost(
            endpoint_url,
            self.editor,
            {"name": "Reminders #3", "group": reporters.uuid},
            errors={"name": "This field must be unique."},
        )

        # it's fine if a campaign in another org has that name
        self.assertPost(endpoint_url, self.editor, {"name": "Spam", "group": reporters.uuid}, status=201)

        # try to create a campaign with name that's too long
        self.assertPost(
            endpoint_url,
            self.editor,
            {"name": "x" * 65, "group": reporters.uuid},
            errors={"name": "Ensure this field has no more than 64 characters."},
        )

        # update campaign by UUID
        self.assertPost(
            endpoint_url + f"?uuid={campaign3.uuid}", self.editor, {"name": "Reminders III", "group": other_group.uuid}
        )

        campaign3.refresh_from_db()
        self.assertEqual(campaign3.name, "Reminders III")
        self.assertEqual(campaign3.group, other_group)

        # can't update campaign in other org
        self.assertPost(
            endpoint_url + f"?uuid={spam.uuid}", self.editor, {"name": "Won't work", "group": spammers.uuid}, status=404
        )

        # can't update deleted campaign
        campaign1.is_active = False
        campaign1.save(update_fields=("is_active",))

        self.assertPost(
            endpoint_url + f"?uuid={campaign1.uuid}",
            self.editor,
            {"name": "Won't work", "group": spammers.uuid},
            status=404,
        )

        # can't update inactive or archived campaign
        campaign1.is_active = True
        campaign1.is_archived = True
        campaign1.save(update_fields=("is_active", "is_archived"))

        self.assertPost(
            endpoint_url + f"?uuid={campaign1.uuid}",
            self.editor,
            {"name": "Won't work", "group": spammers.uuid},
            status=404,
        )
