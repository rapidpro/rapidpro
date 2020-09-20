from unittest.mock import patch

from django.contrib.auth.models import Group
from django.test.utils import override_settings
from django.urls import reverse

from temba.tests import CRUDLTestMixin, TembaTest, mock_mailroom

from .models import Ticket, Ticketer
from .types import reload_ticketer_types
from .types.mailgun import MailgunType
from .types.zendesk import ZendeskType


class TicketTest(TembaTest):
    def test_model(self):
        ticketer = Ticketer.create(self.org, self.user, MailgunType.slug, "Email (bob@acme.com)", {})

        contact = self.create_contact("Bob", twitter="bobby")

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
        self.contact = self.create_contact("Bob", twitter="bobby")

    def create_ticket(self, subject, body, status, ticketer=None, org=None):
        return Ticket.objects.create(
            org=org or self.org,
            ticketer=ticketer or self.mailgun,
            contact=self.contact,
            subject=subject,
            body=body,
            status=status,
        )

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

        contact = self.create_contact("Bob", twitter="bobby")

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
