from datetime import datetime, timezone as tzone

from django.urls import reverse

from temba.tests import mock_mailroom
from temba.tickets.models import Topic

from . import APITest


class TicketActionsEndpointTest(APITest):
    @mock_mailroom
    def test_endpoint(self, mr_mocks):
        endpoint_url = reverse("api.v2.ticket_actions") + ".json"

        self.assertGetNotAllowed(endpoint_url)
        self.assertPostNotPermitted(endpoint_url, [None, self.user])
        self.assertDeleteNotAllowed(endpoint_url)

        # create some tickets
        joe = self.create_contact("Joe Blow", phone="+250788123123")
        frank = self.create_contact("Frank", urns=["facebook:123456"])
        sales = Topic.create(self.org, self.admin, "Sales")
        ticket1 = self.create_ticket(joe, closed_on=datetime(2021, 1, 1, 12, 30, 45, 123456, tzone.utc))
        ticket2 = self.create_ticket(joe)
        self.create_ticket(frank)

        # on another org
        ticket4 = self.create_ticket(self.create_contact("Jim", urns=["twitter:jimmy"], org=self.org2))

        # try actioning more tickets than this endpoint is allowed to operate on at one time
        self.assertPost(
            endpoint_url,
            self.agent,
            {"tickets": [str(x) for x in range(101)], "action": "close"},
            errors={"tickets": "Ensure this field has no more than 100 elements."},
        )

        # try actioning a ticket which is not in this org
        self.assertPost(
            endpoint_url,
            self.agent,
            {"tickets": [str(ticket1.uuid), str(ticket4.uuid)], "action": "close"},
            errors={"tickets": f"No such object: {ticket4.uuid}"},
        )

        # try to close tickets without specifying any tickets
        self.assertPost(
            endpoint_url,
            self.agent,
            {"action": "close"},
            errors={"tickets": "This field is required."},
        )

        # try to assign ticket without specifying assignee
        self.assertPost(
            endpoint_url,
            self.agent,
            {"tickets": [str(ticket1.uuid)], "action": "assign"},
            errors={"non_field_errors": 'For action "assign" you must specify the assignee'},
        )

        # try to add a note without specifying note
        self.assertPost(
            endpoint_url,
            self.agent,
            {"tickets": [str(ticket1.uuid)], "action": "add_note"},
            errors={"non_field_errors": 'For action "add_note" you must specify the note'},
        )

        # try to change topic without specifying topic
        self.assertPost(
            endpoint_url,
            self.agent,
            {"tickets": [str(ticket1.uuid)], "action": "change_topic"},
            errors={"non_field_errors": 'For action "change_topic" you must specify the topic'},
        )

        # assign valid tickets to a user
        self.assertPost(
            endpoint_url,
            self.agent,
            {"tickets": [str(ticket1.uuid), str(ticket2.uuid)], "action": "assign", "assignee": "agent@textit.com"},
            status=204,
        )

        ticket1.refresh_from_db()
        ticket2.refresh_from_db()
        self.assertEqual(self.agent, ticket1.assignee)
        self.assertEqual(self.agent, ticket2.assignee)

        # unassign tickets
        self.assertPost(
            endpoint_url,
            self.agent,
            {"tickets": [str(ticket1.uuid)], "action": "assign", "assignee": None},
            status=204,
        )

        ticket1.refresh_from_db()
        self.assertIsNone(ticket1.assignee)

        # add a note to tickets
        self.assertPost(
            endpoint_url,
            self.agent,
            {"tickets": [str(ticket1.uuid), str(ticket2.uuid)], "action": "add_note", "note": "Looks important"},
            status=204,
        )

        self.assertEqual("Looks important", ticket1.events.last().note)
        self.assertEqual("Looks important", ticket2.events.last().note)

        # change topic of tickets
        self.assertPost(
            endpoint_url,
            self.agent,
            {"tickets": [str(ticket1.uuid), str(ticket2.uuid)], "action": "change_topic", "topic": str(sales.uuid)},
            status=204,
        )

        ticket1.refresh_from_db()
        ticket2.refresh_from_db()
        self.assertEqual(sales, ticket1.topic)
        self.assertEqual(sales, ticket2.topic)

        # close tickets
        self.assertPost(
            endpoint_url,
            self.agent,
            {"tickets": [str(ticket1.uuid), str(ticket2.uuid)], "action": "close"},
            status=204,
        )

        ticket1.refresh_from_db()
        ticket2.refresh_from_db()
        self.assertEqual("C", ticket1.status)
        self.assertEqual("C", ticket2.status)

        # and finally reopen them
        self.assertPost(
            endpoint_url,
            self.agent,
            {"tickets": [str(ticket1.uuid), str(ticket2.uuid)], "action": "reopen"},
            status=204,
        )

        ticket1.refresh_from_db()
        ticket2.refresh_from_db()
        self.assertEqual("O", ticket1.status)
        self.assertEqual("O", ticket2.status)
