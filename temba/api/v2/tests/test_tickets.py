from datetime import datetime, timezone as tzone

from django.urls import reverse

from temba.api.v2.serializers import format_datetime
from temba.tests import mock_mailroom

from . import APITest


class TicketsEndpointTest(APITest):
    @mock_mailroom
    def test_endpoint(self, mr_mocks):
        endpoint_url = reverse("api.v2.tickets") + ".json"

        self.assertGetNotPermitted(endpoint_url, [None])
        self.assertPostNotAllowed(endpoint_url)
        self.assertDeleteNotAllowed(endpoint_url)

        # create some tickets
        ann = self.create_contact("Ann", urns=["twitter:annie"])
        bob = self.create_contact("Bob", urns=["twitter:bobby"])
        flow = self.create_flow("Support")

        ticket1 = self.create_ticket(
            ann, opened_by=self.admin, closed_on=datetime(2021, 1, 1, 12, 30, 45, 123456, tzone.utc)
        )
        ticket2 = self.create_ticket(bob, opened_in=flow)
        ticket3 = self.create_ticket(bob, assignee=self.agent)

        # on another org
        self.create_ticket(self.create_contact("Jim", urns=["twitter:jimmy"], org=self.org2))

        # no filtering
        self.assertGet(
            endpoint_url,
            [self.user, self.editor, self.admin, self.agent],
            results=[
                {
                    "uuid": str(ticket3.uuid),
                    "assignee": {"email": "agent@textit.com", "name": "Agnes"},
                    "contact": {"uuid": str(bob.uuid), "name": "Bob"},
                    "status": "open",
                    "topic": {"uuid": str(self.org.default_ticket_topic.uuid), "name": "General"},
                    "body": None,
                    "opened_on": format_datetime(ticket3.opened_on),
                    "opened_by": None,
                    "opened_in": None,
                    "modified_on": format_datetime(ticket3.modified_on),
                    "closed_on": None,
                },
                {
                    "uuid": str(ticket2.uuid),
                    "assignee": None,
                    "contact": {"uuid": str(bob.uuid), "name": "Bob"},
                    "status": "open",
                    "topic": {"uuid": str(self.org.default_ticket_topic.uuid), "name": "General"},
                    "body": None,
                    "opened_on": format_datetime(ticket2.opened_on),
                    "opened_by": None,
                    "opened_in": {"uuid": str(flow.uuid), "name": "Support"},
                    "modified_on": format_datetime(ticket2.modified_on),
                    "closed_on": None,
                },
                {
                    "uuid": str(ticket1.uuid),
                    "assignee": None,
                    "contact": {"uuid": str(ann.uuid), "name": "Ann"},
                    "status": "closed",
                    "topic": {"uuid": str(self.org.default_ticket_topic.uuid), "name": "General"},
                    "body": None,
                    "opened_on": format_datetime(ticket1.opened_on),
                    "opened_by": {"email": "admin@textit.com", "name": "Andy"},
                    "opened_in": None,
                    "modified_on": format_datetime(ticket1.modified_on),
                    "closed_on": "2021-01-01T12:30:45.123456Z",
                },
            ],
            num_queries=self.BASE_SESSION_QUERIES + 6,
        )

        # filter by contact uuid (not there)
        self.assertGet(endpoint_url + "?contact=09d23a05-47fe-11e4-bfe9-b8f6b119e9ab", [self.admin], results=[])

        # filter by contact uuid present
        self.assertGet(endpoint_url + f"?contact={bob.uuid}", [self.admin], results=[ticket3, ticket2])

        # filter further by ticket uuid
        self.assertGet(endpoint_url + f"?uuid={ticket3.uuid}", [self.admin], results=[ticket3])
