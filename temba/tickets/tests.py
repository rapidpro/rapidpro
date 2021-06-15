from datetime import datetime
from unittest.mock import patch

import pytz

from django.contrib.auth.models import Group
from django.test.utils import override_settings
from django.urls import reverse
from django.utils import timezone

from temba.tests import CRUDLTestMixin, MigrationTest, TembaTest, matchers, mock_mailroom

from .models import Ticket, Ticketer, TicketEvent
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

        ticket.assign(self.admin, assignee=self.editor, note="Please deal with this")
        ticket.add_note(self.admin, note="This is important")

        self.assertEqual(self.editor, ticket.assignee)

        events = list(ticket.events.order_by("id"))
        self.assertEqual(TicketEvent.TYPE_ASSIGNED, events[0].event_type)
        self.assertEqual("Please deal with this", events[0].note)
        self.assertEqual(self.admin, events[0].created_by)
        self.assertEqual(TicketEvent.TYPE_NOTE, events[1].event_type)
        self.assertEqual("This is important", events[1].note)
        self.assertEqual(self.admin, events[1].created_by)

        with patch("temba.mailroom.client.MailroomClient.ticket_close") as mock_close:
            Ticket.bulk_close(self.org, self.admin, [ticket])

        mock_close.assert_called_once_with(self.org.id, self.admin.id, [ticket.id])

        with patch("temba.mailroom.client.MailroomClient.ticket_reopen") as mock_reopen:
            Ticket.bulk_reopen(self.org, self.admin, [ticket])

        mock_reopen.assert_called_once_with(self.org.id, self.admin.id, [ticket.id])


class TicketCRUDLTest(TembaTest, CRUDLTestMixin):
    def setUp(self):
        super().setUp()

        self.mailgun = Ticketer.create(self.org, self.user, MailgunType.slug, "Email (bob@acme.com)", {})
        self.zendesk = Ticketer.create(self.org, self.user, ZendeskType.slug, "Zendesk (acme)", {})
        self.internal = Ticketer.create(self.org, self.user, InternalType.slug, "Internal", {})
        self.other_org_internal = Ticketer.create(self.org2, self.admin2, InternalType.slug, "Internal", {})
        self.contact = self.create_contact("Bob", urns=["twitter:bobby"])

    def test_list(self):
        list_url = reverse("tickets.ticket_list")

        # just a placeholder view for frontend components
        self.assertListFetch(list_url, allow_viewers=True, allow_editors=True, allow_agents=True, context_objects=[])

    def test_folder(self):
        self.login(self.user)

        contact1 = self.create_contact("Joe", phone="123", last_seen_on=timezone.now())
        contact2 = self.create_contact("Frank", phone="124", last_seen_on=timezone.now())
        contact3 = self.create_contact("Anne", phone="125", last_seen_on=timezone.now())
        self.create_contact("Mary No tickets", phone="126", last_seen_on=timezone.now())
        self.create_contact("Mr Other Org", phone="126", last_seen_on=timezone.now(), org=self.org2)

        open_url = reverse("tickets.ticket_folder", kwargs={"folder": "open"})
        closed_url = reverse("tickets.ticket_folder", kwargs={"folder": "closed"})

        def assert_tickets(resp, tickets: list):
            actual_tickets = [t["ticket"]["uuid"] for t in resp.json()["results"]]
            expected_tickets = [str(t.uuid) for t in tickets]
            self.assertEqual(expected_tickets, actual_tickets)

        # no tickets yet so no contacts returned
        response = self.client.get(open_url)
        assert_tickets(response, [])

        # contact 1 has two open tickets
        c1_t1 = self.create_ticket(self.mailgun, contact1, "Question 1")
        c1_t2 = self.create_ticket(self.mailgun, contact1, "Question 2")

        self.create_incoming_msg(contact1, "I have an issue")
        self.create_broadcast(self.admin, "We can help", contacts=[contact1]).msgs.first()

        # contact 2 has an open ticket and a closed ticket
        c2_t1 = self.create_ticket(self.mailgun, contact2, "Question 3")
        c2_t2 = self.create_ticket(self.mailgun, contact2, "Question 4", closed_on=timezone.now())

        self.create_incoming_msg(contact2, "Anyone there?")
        self.create_incoming_msg(contact2, "Hello?")

        # contact 3 has two closed tickets
        c3_t1 = self.create_ticket(self.mailgun, contact3, "Question 5", closed_on=timezone.now())
        c3_t2 = self.create_ticket(self.mailgun, contact3, "Question 6", closed_on=timezone.now())

        # fetching open folder returns all open tickets
        response = self.client.get(open_url)
        assert_tickets(response, [c2_t1, c1_t2, c1_t1])

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

        # fetching closed folder returns all closed tickets
        response = self.client.get(closed_url)
        assert_tickets(response, [c3_t2, c3_t1, c2_t2])

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

        ticket1 = self.create_ticket(self.mailgun, self.contact, "Ticket 1")
        ticket2 = self.create_ticket(self.zendesk, self.contact, "Ticket 2")
        self.create_ticket(self.mailgun, self.contact, "Ticket 3", closed_on=timezone.now())
        self.create_ticket(self.other_org_internal, self.contact, "Ticket 4")

        response = self.assertListFetch(
            open_url, allow_viewers=True, allow_editors=True, allow_agents=True, context_objects=[ticket2, ticket1]
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
        self.zendesk.release(self.admin)

        ticket1 = self.create_ticket(self.mailgun, self.contact, "Ticket 1", closed_on=timezone.now())
        ticket2 = self.create_ticket(self.zendesk, self.contact, "Ticket 2", closed_on=timezone.now())
        self.create_ticket(self.mailgun, self.contact, "Ticket 3")
        self.create_ticket(self.other_org_internal, self.contact, "Ticket 4", closed_on=timezone.now())

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

        ticket1 = self.create_ticket(self.mailgun, self.contact, "Ticket 1")
        ticket2 = self.create_ticket(self.mailgun, self.contact, "Ticket 2", closed_on=timezone.now())
        self.create_ticket(self.zendesk, self.contact, "Ticket 3")

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

    def test_note(self):
        ticket = self.create_ticket(self.mailgun, self.contact, "Ticket 1")

        update_url = reverse("tickets.ticket_note", args=[ticket.uuid])

        self.assertUpdateFetch(
            update_url, allow_viewers=False, allow_editors=True, allow_agents=True, form_fields=["text"]
        )

        self.assertUpdateSubmit(
            update_url, {"text": ""}, form_errors={"text": "This field is required."}, object_unchanged=ticket
        )

        self.assertUpdateSubmit(update_url, {"text": "I have a bad feeling about this."}, success_status=200)

        self.assertEqual(1, ticket.events.filter(event_type=TicketEvent.TYPE_NOTE).count())


class TicketerTest(TembaTest):
    @patch("temba.mailroom.client.MailroomClient.ticket_close")
    def test_release(self, mock_ticket_close):
        ticketer = Ticketer.create(self.org, self.user, MailgunType.slug, "Email (bob@acme.com)", {})

        contact = self.create_contact("Bob", urns=["twitter:bobby"])

        ticket = self.create_ticket(ticketer, contact, "Need help", body="Where are my cookies?")

        # release it
        ticketer.release(self.user)
        ticketer.refresh_from_db()
        self.assertFalse(ticketer.is_active)
        self.assertEqual(self.user, ticketer.modified_by)

        # will have asked mailroom to close the ticket
        mock_ticket_close.assert_called_once_with(self.org.id, self.user.id, [ticket.id])

        # reactivate
        ticketer.is_active = True
        ticketer.save()

        # add a dependency and try again
        flow = self.create_flow()
        flow.ticketer_dependencies.add(ticketer)

        self.assertFalse(flow.has_issues)

        ticketer.release(self.editor)
        ticketer.refresh_from_db()

        self.assertFalse(ticketer.is_active)
        self.assertEqual(self.editor, ticketer.modified_by)
        self.assertNotIn(ticketer, flow.ticketer_dependencies.all())

        flow.refresh_from_db()
        self.assertTrue(flow.has_issues)


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

            response = self.assertListFetch(connect_url, allow_viewers=False, allow_editors=False, allow_agents=False)

            self.assertEqual([], response.context["ticketer_types"])
            self.assertContains(response, "No ticketing services are available.")

        with override_settings(TICKETER_TYPES=["temba.tickets.types.mailgun.MailgunType"], MAILGUN_API_KEY="123"):
            reload_ticketer_types()

            response = self.assertListFetch(connect_url, allow_viewers=False, allow_editors=False, allow_agents=False)

            self.assertNotContains(response, "No ticketing services are available.")
            self.assertContains(response, reverse("tickets.types.mailgun.connect"))

        # put them all back...
        reload_ticketer_types()

    @patch("temba.mailroom.client.MailroomClient.ticket_close")
    def test_delete(self, mock_ticket_close):
        ticketer = Ticketer.create(self.org, self.user, MailgunType.slug, "Email (bob@acme.com)", {})

        delete_url = reverse("tickets.ticketer_delete", args=[ticketer.uuid])

        # fetch delete modal
        response = self.assertDeleteFetch(delete_url)
        self.assertContains(response, "You are about to delete")

        # submit to delete it
        response = self.assertDeleteSubmit(delete_url, object_deactivated=ticketer, success_status=200)
        self.assertEqual("/org/home/", response["Temba-Success"])

        # reactivate
        ticketer.is_active = True
        ticketer.save()

        # add a dependency and try again
        flow = self.create_flow()
        flow.ticketer_dependencies.add(ticketer)
        self.assertFalse(flow.has_issues)

        response = self.assertDeleteFetch(delete_url)
        self.assertContains(response, "is used by the following flows which may not work as expected")

        self.assertDeleteSubmit(delete_url, object_deactivated=ticketer, success_status=200)

        flow.refresh_from_db()
        self.assertTrue(flow.has_issues)
        self.assertNotIn(ticketer, flow.ticketer_dependencies.all())


class BackfillTicketEventsTest(MigrationTest):
    app = "tickets"
    migrate_from = "0006_auto_20210609_1913"
    migrate_to = "0007_backfill_ticket_events"

    def setUpBeforeMigration(self, apps):
        ticketer = Ticketer.create(self.org, self.user, MailgunType.slug, "Email (bob@acme.com)", {})
        contact = self.create_contact("Joe", phone="123456")

        def create_ticket(opened_on, closed_on):
            return Ticket.objects.create(
                org=self.org,
                ticketer=ticketer,
                contact=contact,
                subject="Test",
                status=Ticket.STATUS_CLOSED if closed_on else Ticket.STATUS_OPEN,
                opened_on=opened_on,
                closed_on=closed_on,
            )

        # ticket with no closed_on
        self.ticket1 = create_ticket(opened_on=datetime(2021, 1, 1, 12, 0, 30, 123456, pytz.UTC), closed_on=None)

        # ticket with a closed_on
        self.ticket2 = create_ticket(
            opened_on=datetime(2021, 2, 2, 12, 0, 30, 123456, pytz.UTC),
            closed_on=datetime(2021, 3, 3, 12, 0, 30, 123456, pytz.UTC),
        )

        # ticket that already has events
        self.ticket3 = create_ticket(
            opened_on=datetime(2021, 4, 4, 12, 0, 30, 123456, pytz.UTC),
            closed_on=datetime(2021, 5, 5, 12, 0, 30, 123456, pytz.UTC),
        )
        self.ticket3.events.create(
            org=self.org,
            event_type=TicketEvent.TYPE_OPENED,
            created_on=datetime(2021, 4, 4, 12, 0, 30, 123456, pytz.UTC),
        )
        self.ticket3.events.create(
            org=self.org,
            event_type=TicketEvent.TYPE_CLOSED,
            created_on=datetime(2021, 5, 5, 12, 0, 30, 123456, pytz.UTC),
        )

    def test_migration(self):
        ticket1_open = self.ticket1.events.get(event_type=TicketEvent.TYPE_OPENED)

        self.assertEqual(self.org, ticket1_open.org)
        self.assertEqual(datetime(2021, 1, 1, 12, 0, 30, 123456, pytz.UTC), ticket1_open.created_on)
        self.assertIsNone(self.ticket1.events.filter(event_type=TicketEvent.TYPE_CLOSED).first())

        ticket2_open = self.ticket2.events.get(event_type=TicketEvent.TYPE_OPENED)
        ticket2_close = self.ticket2.events.get(event_type=TicketEvent.TYPE_CLOSED)

        self.assertEqual(datetime(2021, 2, 2, 12, 0, 30, 123456, pytz.UTC), ticket2_open.created_on)
        self.assertEqual(datetime(2021, 3, 3, 12, 0, 30, 123456, pytz.UTC), ticket2_close.created_on)

        # check we didn't create additional events for ticket 3
        self.assertEqual(1, self.ticket3.events.filter(event_type=TicketEvent.TYPE_OPENED).count())
        self.assertEqual(1, self.ticket3.events.filter(event_type=TicketEvent.TYPE_CLOSED).count())
