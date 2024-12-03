from django.test import override_settings
from django.urls import reverse

from temba.api.v2.serializers import format_datetime
from temba.tests import matchers
from temba.tickets.models import Topic

from . import APITest


class TopicsEndpointTest(APITest):
    def test_endpoint(self):
        endpoint_url = reverse("api.v2.topics") + ".json"

        self.assertGetNotPermitted(endpoint_url, [None])
        self.assertPostNotPermitted(endpoint_url, [None, self.agent, self.user])
        self.assertDeleteNotAllowed(endpoint_url)

        # create some topics
        support = Topic.create(self.org, self.admin, "Support")
        sales = Topic.create(self.org, self.admin, "Sales")
        other_org = Topic.create(self.org2, self.admin, "Bugs")

        contact = self.create_contact("Ann", phone="+1234567890")
        self.create_ticket(contact, topic=support)

        # no filtering
        self.assertGet(
            endpoint_url,
            [self.user, self.editor],
            results=[
                {
                    "uuid": str(sales.uuid),
                    "name": "Sales",
                    "counts": {"open": 0, "closed": 0},
                    "system": False,
                    "created_on": format_datetime(sales.created_on),
                },
                {
                    "uuid": str(support.uuid),
                    "name": "Support",
                    "counts": {"open": 1, "closed": 0},
                    "system": False,
                    "created_on": format_datetime(support.created_on),
                },
                {
                    "uuid": str(self.org.default_ticket_topic.uuid),
                    "name": "General",
                    "counts": {"open": 0, "closed": 0},
                    "system": True,
                    "created_on": format_datetime(self.org.default_ticket_topic.created_on),
                },
            ],
            num_queries=self.BASE_SESSION_QUERIES + 3,
        )

        # try to create empty topic
        response = self.assertPost(endpoint_url, self.editor, {}, errors={"name": "This field is required."})

        # create new topic
        response = self.assertPost(endpoint_url, self.editor, {"name": "Food"}, status=201)

        food = Topic.objects.get(name="Food")
        self.assertEqual(
            response.json(),
            {
                "uuid": str(food.uuid),
                "name": "Food",
                "counts": {"open": 0, "closed": 0},
                "system": False,
                "created_on": matchers.ISODate(),
            },
        )

        # try to create another topic with same name
        self.assertPost(endpoint_url, self.editor, {"name": "Food"}, errors={"name": "This field must be unique."})

        # it's fine if a topic in another org has that name
        self.assertPost(endpoint_url, self.editor, {"name": "Bugs"}, status=201)

        # try to create a topic with invalid name
        self.assertPost(endpoint_url, self.editor, {"name": '"Hi"'}, errors={"name": 'Cannot contain the character: "'})

        # try to create a topic with name that's too long
        self.assertPost(
            endpoint_url,
            self.editor,
            {"name": "x" * 65},
            errors={"name": "Ensure this field has no more than 64 characters."},
        )

        # update topic by UUID
        self.assertPost(endpoint_url + f"?uuid={support.uuid}", self.admin, {"name": "Support Tickets"})

        support.refresh_from_db()
        self.assertEqual(support.name, "Support Tickets")

        # can't update default topic for an org
        self.assertPost(
            endpoint_url + f"?uuid={self.org.default_ticket_topic.uuid}",
            self.admin,
            {"name": "Won't work"},
            errors={None: "Cannot modify system object."},
            status=403,
        )

        # can't update topic from other org
        self.assertPost(endpoint_url + f"?uuid={other_org.uuid}", self.admin, {"name": "Won't work"}, status=404)

        # can't update topic to same name as existing topic
        self.assertPost(
            endpoint_url + f"?uuid={support.uuid}",
            self.admin,
            {"name": "General"},
            errors={"name": "This field must be unique."},
        )

        # try creating a new topic after reaching the limit
        current_count = self.org.topics.filter(is_system=False, is_active=True).count()
        with override_settings(ORG_LIMIT_DEFAULTS={"topics": current_count}):
            response = self.assertPost(
                endpoint_url,
                self.admin,
                {"name": "Interesting"},
                errors={None: "Cannot create object because workspace has reached limit of 4."},
                status=409,
            )
