from unittest.mock import patch

from django.contrib.auth.models import Group
from django.test.utils import override_settings
from django.urls import reverse
from django.utils import timezone

from temba.tests import CRUDLTestMixin, TembaTest, matchers, mock_mailroom

from .models import Ticket, Ticketer
from .types import reload_ticketer_types
from .types.internal import InternalType
from .types.mailgun import MailgunType
from .types.zendesk import ZendeskType


class TicketTest(TembaTest):
    def test_model(self):
        ticketer = Ticketer.create(self.org, self.user, MailgunType.slug, "Email (bob@acme.com)", {})

        contact = self.create_contact("Bob", urns=["twitter:bobby"])

        ticket = Ticket.objects.create(
            org=self.org,
            ticketer=ticketer,
            contact=contact,
            subject="Need help",
            body="Where are my cookies?",
            status="O",
        )

        self.assertEqual(f"Ticket[uuid={ticket.uuid}, subject=Need help]", str(ticket))

        with patch("temba.mailroom.client.MailroomClient.ticket_close") as mock_close:
            Ticket.bulk_close(self.org, [ticket])

        mock_close.assert_called_once_with(self.org.id, [ticket.id])

        with patch("temba.mailroom.client.MailroomClient.ticket_reopen") as mock_reopen:
            Ticket.bulk_reopen(self.org, [ticket])

        mock_reopen.assert_called_once_with(self.org.id, [ticket.id])


class TicketCRUDLTest(TembaTest, CRUDLTestMixin):
    def setUp(self):
        super().setUp()

        self.mailgun = Ticketer.create(self.org, self.user, MailgunType.slug, "Email (bob@acme.com)", {})
        self.zendesk = Ticketer.create(self.org, self.user, ZendeskType.slug, "Zendesk (acme)", {})
        self.internal = Ticketer.create(self.org, self.user, InternalType.slug, "Internal", {})
        self.contact = self.create_contact("Bob", urns=["twitter:bobby"])

    def create_ticket(self, subject, body, status, ticketer=None, org=None):
        return Ticket.objects.create(
            org=org or self.org,
            ticketer=ticketer or self.mailgun,
            contact=self.contact,
            subject=subject,
            body=body,
            status=status,
        )

    def test_folder(self):
        self.login(self.user)

        ticketer = Ticketer.create(self.org, self.user, "internal", "Internal", {})
        contact1 = self.create_contact("Joe", phone="123", last_seen_on=timezone.now())
        contact2 = self.create_contact("Frank", phone="124", last_seen_on=timezone.now())
        contact3 = self.create_contact("Anne", phone="125", last_seen_on=timezone.now())
        self.create_contact("Mary No tickets", phone="126", last_seen_on=timezone.now())
        self.create_contact("Mr Other Org", phone="126", last_seen_on=timezone.now(), org=self.org2)

        open_url = reverse("tickets.ticket_folder", kwargs={"folder": "open"})
        closed_url = reverse("tickets.ticket_folder", kwargs={"folder": "closed"})

        # no tickets yet so no contacts returned
        response = self.client.get(open_url)
        self.assertEqual(0, len(response.context["object_list"]))

        # contact 1 has two open tickets
        c1_t1 = Ticket.objects.create(
            org=self.org, ticketer=ticketer, contact=contact1, subject="Question 1", status="O"
        )
        c1_t2 = Ticket.objects.create(
            org=self.org, ticketer=ticketer, contact=contact1, subject="Question 2", status="O"
        )

        self.create_incoming_msg(contact1, "I have an issue")
        self.create_broadcast(self.admin, "We can help", contacts=[contact1]).msgs.first()

        # contact 2 has an open ticket and a closed ticket
        c2_t1 = Ticket.objects.create(
            org=self.org, ticketer=ticketer, contact=contact2, subject="Question 3", status="O"
        )
        c2_t2 = Ticket.objects.create(
            org=self.org, ticketer=ticketer, contact=contact2, subject="Question 4", status="C"
        )

        self.create_incoming_msg(contact2, "Anyone there?")
        self.create_incoming_msg(contact2, "Hello?")

        # contact 3 has two closed tickets
        c3_t1 = Ticket.objects.create(
            org=self.org, ticketer=ticketer, contact=contact3, subject="Question 5", status="C"
        )
        c3_t2 = Ticket.objects.create(
            org=self.org, ticketer=ticketer, contact=contact3, subject="Question 6", status="C"
        )

        # fetching open folder returns all open tickets
        response = self.client.get(open_url)
        self.assertEqual([c2_t1, c1_t2, c1_t1], list(response.context["object_list"]))

        # fetching closed folder returns all closed tickets
        response = self.client.get(closed_url)
        self.assertEqual([c3_t2, c3_t1, c2_t2], list(response.context["object_list"]))

        # can request page as JSON
        response = self.client.get(open_url + "?_format=json")

        joes_open_tickets = contact1.tickets.filter(status="O")

        expected_json = {
            "results": [
                {
                    "uuid": str(contact2.uuid),
                    "name": "Frank",
                    "last_seen_on": matchers.ISODate(),
                    "last_msg": {
                        "text": "Hello?",
                        "direction": "I",
                        "type": "I",
                        "created_on": matchers.ISODate(),
                        "sender": None,
                    },
                    "ticket": {
                        "uuid": str(contact2.tickets.filter(status="O").first().uuid),
                        "subject": "Question 3",
                        "closed_on": None,
                    },
                },
                {
                    "uuid": str(contact1.uuid),
                    "name": "Joe",
                    "last_seen_on": matchers.ISODate(),
                    "last_msg": {
                        "text": "We can help",
                        "direction": "O",
                        "type": "I",
                        "created_on": matchers.ISODate(),
                        "sender": {"id": self.admin.id, "email": "Administrator@nyaruka.com"},
                    },
                    "ticket": {
                        "uuid": str(joes_open_tickets[0].uuid),
                        "subject": "Question 2",
                        "closed_on": None,
                    },
                },
                {
                    "uuid": str(contact1.uuid),
                    "name": "Joe",
                    "last_seen_on": matchers.ISODate(),
                    "last_msg": {
                        "text": "We can help",
                        "direction": "O",
                        "type": "I",
                        "created_on": matchers.ISODate(),
                        "sender": {"id": self.admin.id, "email": "Administrator@nyaruka.com"},
                    },
                    "ticket": {
                        "uuid": str(joes_open_tickets[1].uuid),
                        "subject": "Question 1",
                        "closed_on": None,
                    },
                },
            ]
        }

        self.assertEqual(expected_json, response.json())

        # make sure when paging we get a next url
        with patch("temba.tickets.views.TicketCRUDL.Folder.paginate_by", 1):
            response = self.client.get(open_url + "?_format=json")
            self.assertIsNotNone(response.json()["next"])

    @mock_mailroom
    def test_open_redirect(self, mr_mocks):
        open_url = reverse("tickets.ticket_open")

        self.mailgun.delete()
        self.zendesk.delete()

        # visiting the open tickets shouldn't redirect since we aren't beta
        response = self.requestView(open_url, self.admin)
        self.assertIsNone(response.get("Location", None))

        beta = Group.objects.filter(name="Beta").first()
        self.admin.groups.add(beta)

        response = self.requestView(open_url, self.admin)
        self.assertEqual(reverse("tickets.ticket_list"), response.get("Location", None))

    @mock_mailroom
    def test_open(self, mr_mocks):
        open_url = reverse("tickets.ticket_open")

        ticket1 = self.create_ticket("Ticket 1", "Where are my cookies?", "O")
        ticket2 = self.create_ticket("Ticket 2", "Where are my shoes?", "O", ticketer=self.zendesk)
        self.create_ticket("Ticket 3", "Old ticket", "C")
        self.create_ticket("Ticket 4", "Where are my trousers?", "O", org=self.org2)

        response = self.assertListFetch(
            open_url, allow_viewers=True, allow_editors=True, context_objects=[ticket2, ticket1]
        )

        self.assertEqual(("close",), response.context["actions"])
        self.assertContains(response, reverse("tickets.ticket_filter", args=[self.mailgun.uuid]))
        self.assertContains(response, reverse("tickets.ticket_filter", args=[self.zendesk.uuid]))

        # can close tickets with an action POST
        response = self.requestView(open_url, self.admin, post_data={"action": "close", "objects": [ticket2.id]})
        self.assertEqual(200, response.status_code)

        ticket1.refresh_from_db()
        ticket2.refresh_from_db()

        self.assertEqual("O", ticket1.status)
        self.assertEqual("C", ticket2.status)

        # unless you're only a user
        response = self.requestView(open_url, self.user, post_data={"action": "close", "objects": [ticket1.id]})
        self.assertEqual(403, response.status_code)

        # return generic error as a toast if mailroom blows up (actual mailroom error will be logged to sentry)
        mr_mocks.error("boom!")

        response = self.requestView(open_url, self.admin, post_data={"action": "close", "objects": [ticket1.id]})
        self.assertEqual(200, response.status_code)
        self.assertEqual("An error occurred while making your changes. Please try again.", response["Temba-Toast"])

    @mock_mailroom
    def test_closed(self, mr_mocks):
        closed_url = reverse("tickets.ticket_closed")

        # still see closed tickets for deleted ticketers
        self.zendesk.release()

        ticket1 = self.create_ticket("Ticket 1", "Where are my cookies?", "C")
        ticket2 = self.create_ticket("Ticket 2", "Where are my shoes?", "C", ticketer=self.zendesk)
        self.create_ticket("Ticket 3", "New ticket", "O")
        self.create_ticket("Ticket 4", "Where are my trousers?", "O", org=self.org2)

        response = self.assertListFetch(
            closed_url, allow_viewers=True, allow_editors=True, context_objects=[ticket2, ticket1]
        )
        self.assertEqual(("reopen",), response.context["actions"])
        self.assertContains(response, reverse("tickets.ticket_filter", args=[self.mailgun.uuid]))

        # can't link to deleted ticketer
        self.assertNotContains(response, reverse("tickets.ticket_filter", args=[self.zendesk.uuid]))

        # can reopen tickets with an action POST
        response = self.requestView(closed_url, self.admin, post_data={"action": "reopen", "objects": [ticket1.id]})
        self.assertEqual(200, response.status_code)

        ticket1.refresh_from_db()
        ticket2.refresh_from_db()

        self.assertEqual("O", ticket1.status)
        self.assertEqual("C", ticket2.status)

        # unless you're only a user
        response = self.requestView(closed_url, self.user, post_data={"action": "reopen", "objects": [ticket2.id]})
        self.assertEqual(403, response.status_code)

    def test_filter(self):
        filter_url = reverse("tickets.ticket_filter", args=[self.mailgun.uuid])

        ticket1 = self.create_ticket("Ticket 1", "Where are my cookies?", "O")
        ticket2 = self.create_ticket("Ticket 2", "Where are my shoes?", "C")
        self.create_ticket("Ticket 3", "New ticket", "O", ticketer=self.zendesk)

        response = self.assertReadFetch(filter_url, allow_viewers=True, allow_editors=True)
        self.assertEqual(self.mailgun, response.context["ticketer"])
        self.assertEqual([ticket2, ticket1], list(response.context["object_list"]))
        self.assertEqual(("close", "reopen"), response.context["actions"])

        # normal users don't see HTTP logs for ticketers
        logs_url = reverse("request_logs.httplog_ticketer", args=[self.mailgun.uuid])
        self.assertNotContains(response, logs_url)

        support = Group.objects.get(name="Customer Support")
        support.user_set.add(self.admin)

        # customer support users do
        response = self.requestView(filter_url, self.admin)
        self.assertContains(response, logs_url)


class TicketerTest(TembaTest):
    def test_release(self):
        ticketer = Ticketer.create(self.org, self.user, MailgunType.slug, "Email (bob@acme.com)", {})

        contact = self.create_contact("Bob", urns=["twitter:bobby"])

        ticket = Ticket.objects.create(
            org=self.org,
            ticketer=ticketer,
            contact=contact,
            subject="Need help",
            body="Where are my cookies?",
            status="O",
        )

        with patch("temba.mailroom.client.MailroomClient.ticket_close") as mock_close:
            # release it
            ticketer.release()
            ticketer.refresh_from_db()
            self.assertFalse(ticketer.is_active)

        # will have asked mailroom to close the ticket
        mock_close.assert_called_once_with(self.org.id, [ticket.id])

        # reactivate
        ticketer.is_active = True
        ticketer.save()

        # add a dependency and try again
        flow = self.create_flow()
        flow.ticketer_dependencies.add(ticketer)

        with self.assertRaises(AssertionError):
            ticketer.release()

        ticketer.refresh_from_db()
        self.assertTrue(ticketer.is_active)


class TicketerCRUDLTest(TembaTest, CRUDLTestMixin):
    def test_org_home(self):
        ticketer = Ticketer.create(self.org, self.user, MailgunType.slug, "Email (bob@acme.com)", {})

        self.login(self.admin)
        response = self.client.get(reverse("orgs.org_home"))

        self.assertContains(response, "Email (bob@acme.com)")
        self.assertContains(response, reverse("tickets.ticket_filter", args=[ticketer.uuid]))

    def test_connect(self):
        connect_url = reverse("tickets.ticketer_connect")

        with override_settings(TICKETER_TYPES=[]):
            reload_ticketer_types()

            response = self.assertListFetch(connect_url, allow_viewers=False, allow_editors=False)

            self.assertEqual([], response.context["ticketer_types"])
            self.assertContains(response, "No ticketing services are available.")

        with override_settings(TICKETER_TYPES=["temba.tickets.types.mailgun.MailgunType"], MAILGUN_API_KEY="123"):
            reload_ticketer_types()

            response = self.assertListFetch(connect_url, allow_viewers=False, allow_editors=False)

            self.assertNotContains(response, "No ticketing services are available.")
            self.assertContains(response, reverse("tickets.types.mailgun.connect"))

        # put them all back...
        reload_ticketer_types()

    def test_delete(self):
        ticketer = Ticketer.create(self.org, self.user, MailgunType.slug, "Email (bob@acme.com)", {})

        delete_url = reverse("tickets.ticketer_delete", args=[ticketer.uuid])

        # try to delete it
        response = self.client.post(delete_url)
        self.assertRedirect(response, "/users/login/")

        self.login(self.admin)

        with patch("temba.mailroom.client.MailroomClient.ticket_close"):
            self.client.post(delete_url)

        ticketer.refresh_from_db()
        self.assertFalse(ticketer.is_active)

        # reactivate
        ticketer.is_active = True
        ticketer.save()

        # add a dependency and try again
        flow = self.create_flow()
        flow.ticketer_dependencies.add(ticketer)

        with self.assertRaises(AssertionError):
            self.client.post(delete_url)

        ticketer.refresh_from_db()
        self.assertTrue(ticketer.is_active)
