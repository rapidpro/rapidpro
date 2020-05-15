from django.test.utils import override_settings
from django.urls import reverse

from temba.tests import CRUDLTestMixin, TembaTest

from .models import Ticket, Ticketer
from .types import reload_ticketer_types
from .types.mailgun import MailgunType
from .types.zendesk import ZendeskType


class TicketTest(TembaTest):
    def test_close_and_reopen(self):
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
        modified_on = ticket.modified_on

        ticket.close()
        ticket.refresh_from_db()

        self.assertEqual(Ticket.STATUS_CLOSED, ticket.status)
        self.assertIsNotNone(ticket.closed_on)
        self.assertGreater(ticket.modified_on, modified_on)

        modified_on = ticket.modified_on
        ticket.reopen()
        ticket.refresh_from_db()

        self.assertEqual(Ticket.STATUS_OPEN, ticket.status)
        self.assertIsNone(ticket.closed_on)
        self.assertGreater(ticket.modified_on, modified_on)


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

    def test_open(self):
        open_url = reverse("tickets.ticket_open")

        ticket1 = self.create_ticket("Ticket 1", "Where are my cookies?", "O")
        ticket2 = self.create_ticket("Ticket 2", "Where are my shoes?", "O")
        self.create_ticket("Ticket 3", "Old ticket", "C")
        self.create_ticket("Ticket 4", "Where are my trousers?", "O", org=self.org2)

        self.assertListFetch(open_url, allow_viewers=True, allow_editors=True, context_objects=[ticket2, ticket1])

    def test_closed(self):
        closed_url = reverse("tickets.ticket_closed")

        ticket1 = self.create_ticket("Ticket 1", "Where are my cookies?", "C")
        ticket2 = self.create_ticket("Ticket 2", "Where are my shoes?", "C")
        self.create_ticket("Ticket 3", "New ticket", "O")
        self.create_ticket("Ticket 4", "Where are my trousers?", "O", org=self.org2)

        self.assertListFetch(closed_url, allow_viewers=True, allow_editors=True, context_objects=[ticket2, ticket1])

    def test_filter(self):
        filter_url = reverse("tickets.ticket_filter", args=[self.mailgun.uuid])

        ticket1 = self.create_ticket("Ticket 1", "Where are my cookies?", "O")
        ticket2 = self.create_ticket("Ticket 2", "Where are my shoes?", "C")
        self.create_ticket("Ticket 3", "New ticket", "O", ticketer=self.zendesk)

        response = self.assertReadFetch(filter_url, allow_viewers=True, allow_editors=True)
        self.assertEqual(self.mailgun, response.context["ticketer"])
        self.assertEqual([ticket2, ticket1], list(response.context["object_list"]))


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

        # release it
        ticketer.release()
        ticketer.refresh_from_db()
        self.assertFalse(ticketer.is_active)

        # ticket should be closed too
        ticket.refresh_from_db()
        self.assertEqual("C", ticket.status)

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
