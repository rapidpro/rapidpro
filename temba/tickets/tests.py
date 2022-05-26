from datetime import date, datetime
from unittest.mock import patch

from django.test.utils import override_settings
from django.urls import reverse
from django.utils import timezone

from temba.contacts.models import Contact
from temba.tests import CRUDLTestMixin, MigrationTest, TembaTest, matchers, mock_mailroom
from temba.utils.dates import datetime_to_timestamp

from .models import (
    Team,
    Ticket,
    TicketCount,
    TicketDailyCount,
    TicketDailyTiming,
    Ticketer,
    TicketEvent,
    Topic,
    export_ticket_stats,
)
from .tasks import squash_ticketcounts
from .types import reload_ticketer_types
from .types.internal import InternalType
from .types.mailgun import MailgunType
from .types.zendesk import ZendeskType


class TicketTest(TembaTest):
    def test_model(self):
        ticketer = Ticketer.create(self.org, self.user, MailgunType.slug, "Email (bob@acme.com)", {})
        topic = Topic.create(self.org, self.admin, "Sales")
        contact = self.create_contact("Bob", urns=["twitter:bobby"])

        ticket = Ticket.objects.create(
            org=self.org,
            ticketer=ticketer,
            contact=contact,
            topic=self.org.default_ticket_topic,
            body="Where are my cookies?",
            status="O",
        )

        self.assertEqual(f"Ticket[uuid={ticket.uuid}, topic=General]", str(ticket))

        # test bulk assignment
        with patch("temba.mailroom.client.MailroomClient.ticket_assign") as mock_assign:
            Ticket.bulk_assign(self.org, self.admin, [ticket], self.agent, "over to you")

        mock_assign.assert_called_once_with(self.org.id, self.admin.id, [ticket.id], self.agent.id, "over to you")
        mock_assign.reset_mock()

        # test bulk un-assignment
        with patch("temba.mailroom.client.MailroomClient.ticket_assign") as mock_assign:
            Ticket.bulk_assign(self.org, self.admin, [ticket], None)

        mock_assign.assert_called_once_with(self.org.id, self.admin.id, [ticket.id], None, None)
        mock_assign.reset_mock()

        # test bulk adding a note
        with patch("temba.mailroom.client.MailroomClient.ticket_add_note") as mock_add_note:
            Ticket.bulk_add_note(self.org, self.admin, [ticket], "please handle")

        mock_add_note.assert_called_once_with(self.org.id, self.admin.id, [ticket.id], "please handle")

        # test bulk changing topic
        with patch("temba.mailroom.client.MailroomClient.ticket_change_topic") as mock_change_topic:
            Ticket.bulk_change_topic(self.org, self.admin, [ticket], topic)

        mock_change_topic.assert_called_once_with(self.org.id, self.admin.id, [ticket.id], topic.id)

        # test bulk closing
        with patch("temba.mailroom.client.MailroomClient.ticket_close") as mock_close:
            Ticket.bulk_close(self.org, self.admin, [ticket], force=True)

        mock_close.assert_called_once_with(self.org.id, self.admin.id, [ticket.id], force=True)

        # test bulk re-opening
        with patch("temba.mailroom.client.MailroomClient.ticket_reopen") as mock_reopen:
            Ticket.bulk_reopen(self.org, self.admin, [ticket])

        mock_reopen.assert_called_once_with(self.org.id, self.admin.id, [ticket.id])

    def test_allowed_assignees(self):
        self.assertEqual({self.admin, self.editor, self.agent}, set(Ticket.get_allowed_assignees(self.org)))
        self.assertEqual({self.admin2}, set(Ticket.get_allowed_assignees(self.org2)))

    @mock_mailroom
    def test_counts(self, mr_mocks):
        ticketer = Ticketer.create(self.org, self.admin, MailgunType.slug, "bob@acme.com", {})
        contact1 = self.create_contact("Bob", urns=["twitter:bobby"])
        contact2 = self.create_contact("Jim", urns=["twitter:jimmy"])
        org2_ticketer = Ticketer.create(self.org2, self.admin2, MailgunType.slug, "jim@acme.com", {})
        org2_contact = self.create_contact("Bob", urns=["twitter:bobby"], org=self.org2)

        t1 = self.create_ticket(ticketer, contact1, "Test 1")
        t2 = self.create_ticket(ticketer, contact2, "Test 2")
        t3 = self.create_ticket(ticketer, contact1, "Test 3")
        t4 = self.create_ticket(ticketer, contact2, "Test 4")
        t5 = self.create_ticket(ticketer, contact1, "Test 5")
        t6 = self.create_ticket(org2_ticketer, org2_contact, "Test 6")

        def assert_counts(org, *, open: dict, closed: dict, contacts: dict):
            assignees = [None] + list(Ticket.get_allowed_assignees(org))

            self.assertEqual(open, TicketCount.get_by_assignees(org, assignees, Ticket.STATUS_OPEN))
            self.assertEqual(closed, TicketCount.get_by_assignees(org, assignees, Ticket.STATUS_CLOSED))

            self.assertEqual(sum(open.values()), TicketCount.get_all(org, Ticket.STATUS_OPEN))
            self.assertEqual(sum(closed.values()), TicketCount.get_all(org, Ticket.STATUS_CLOSED))

            self.assertEqual(contacts, {c: Contact.objects.get(id=c.id).ticket_count for c in contacts})

        # t1:O/None t2:O/None t3:O/None t4:O/None t5:O/None t6:O/None
        assert_counts(
            self.org,
            open={None: 5, self.agent: 0, self.editor: 0, self.admin: 0},
            closed={None: 0, self.agent: 0, self.editor: 0, self.admin: 0},
            contacts={contact1: 3, contact2: 2},
        )
        assert_counts(
            self.org2, open={None: 1, self.admin2: 0}, closed={None: 0, self.admin2: 0}, contacts={org2_contact: 1}
        )

        Ticket.bulk_assign(self.org, self.admin, [t1, t2], assignee=self.agent)
        Ticket.bulk_assign(self.org, self.admin, [t3], assignee=self.editor)
        Ticket.bulk_assign(self.org2, self.admin2, [t6], assignee=self.admin2)

        # t1:O/Agent t2:O/Agent t3:O/Editor t4:O/None t5:O/None t6:O/Admin2
        assert_counts(
            self.org,
            open={None: 2, self.agent: 2, self.editor: 1, self.admin: 0},
            closed={None: 0, self.agent: 0, self.editor: 0, self.admin: 0},
            contacts={contact1: 3, contact2: 2},
        )
        assert_counts(
            self.org2, open={None: 0, self.admin2: 1}, closed={None: 0, self.admin2: 0}, contacts={org2_contact: 1}
        )

        Ticket.bulk_close(self.org, self.admin, [t1, t4])
        Ticket.bulk_close(self.org2, self.admin2, [t6])

        # t1:C/Agent t2:O/Agent t3:O/Editor t4:C/None t5:O/None t6:C/Admin2
        assert_counts(
            self.org,
            open={None: 1, self.agent: 1, self.editor: 1, self.admin: 0},
            closed={None: 1, self.agent: 1, self.editor: 0, self.admin: 0},
            contacts={contact1: 2, contact2: 1},
        )
        assert_counts(
            self.org2, open={None: 0, self.admin2: 0}, closed={None: 0, self.admin2: 1}, contacts={org2_contact: 0}
        )

        Ticket.bulk_assign(self.org, self.admin, [t1, t5], assignee=self.admin)

        # t1:C/Admin t2:O/Agent t3:O/Editor t4:C/None t5:O/Admin t6:C/Admin2
        assert_counts(
            self.org,
            open={None: 0, self.agent: 1, self.editor: 1, self.admin: 1},
            closed={None: 1, self.agent: 0, self.editor: 0, self.admin: 1},
            contacts={contact1: 2, contact2: 1},
        )

        Ticket.bulk_reopen(self.org, self.admin, [t4])

        # t1:C/Admin t2:O/Agent t3:O/Editor t4:O/None t5:O/Admin t6:C/Admin2
        assert_counts(
            self.org,
            open={None: 1, self.agent: 1, self.editor: 1, self.admin: 1},
            closed={None: 0, self.agent: 0, self.editor: 0, self.admin: 1},
            contacts={contact1: 2, contact2: 2},
        )

        squash_ticketcounts()  # shouldn't change counts

        assert_counts(
            self.org,
            open={None: 1, self.agent: 1, self.editor: 1, self.admin: 1},
            closed={None: 0, self.agent: 0, self.editor: 0, self.admin: 1},
            contacts={contact1: 2, contact2: 2},
        )

        TicketEvent.objects.all().delete()
        t1.delete()
        t2.delete()
        t6.delete()

        # t3:O/Editor t4:O/None t5:O/Admin
        assert_counts(
            self.org,
            open={None: 1, self.agent: 0, self.editor: 1, self.admin: 1},
            closed={None: 0, self.agent: 0, self.editor: 0, self.admin: 0},
            contacts={contact1: 2, contact2: 1},
        )
        assert_counts(
            self.org2, open={None: 0, self.admin2: 0}, closed={None: 0, self.admin2: 0}, contacts={org2_contact: 0}
        )


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
        ticket = self.create_ticket(self.internal, self.contact, "Test 1", assignee=self.admin)

        # just a placeholder view for frontend components
        self.assertListFetch(list_url, allow_viewers=False, allow_editors=True, allow_agents=True, context_objects=[])

        # can hit this page with a uuid
        # TODO: work out reverse for deep link
        # deep_link = reverse(
        #    "tickets.ticket_list", kwargs={"folder": "all", "status": "open", "uuid": str(ticket.uuid)}
        # )

        deep_link = f"{list_url}all/open/{str(ticket.uuid)}/"
        response = self.assertListFetch(
            deep_link, allow_viewers=False, allow_editors=True, allow_agents=True, context_objects=[]
        )

        # our ticket exists on the first page, so it'll get flagged to be focused
        self.assertEqual(str(ticket.uuid), response.context["nextUUID"])

        # deep link into a page that doesn't have our ticket
        deep_link = f"{list_url}all/closed/{str(ticket.uuid)}/"
        self.login(self.admin)
        response = self.client.get(deep_link)

        # now our ticket is listed as the uuid and we were redirected to all/open
        self.assertEqual("all", response.context["folder"])
        self.assertEqual("open", response.context["status"])
        self.assertEqual(str(ticket.uuid), response.context["uuid"])

        # fetch with spa flag
        response = self.client.get(
            list_url,
            content_type="application/json",
            HTTP_TEMBA_SPA="1",
            HTTP_TEMBA_REFERER_PATH=f"/tickets/mine/open/{ticket.uuid}",
        )
        self.assertEqual("spa.html", response.context["base_template"])
        self.assertEqual(("tickets", "mine", "open", str(ticket.uuid)), response.context["temba_referer"])

    def test_menu(self):
        menu_url = reverse("tickets.ticket_menu")

        self.create_ticket(self.internal, self.contact, "Test 1", assignee=self.admin)
        self.create_ticket(self.internal, self.contact, "Test 2", assignee=self.admin)
        self.create_ticket(self.internal, self.contact, "Test 3", assignee=None)
        self.create_ticket(self.internal, self.contact, "Test 4", closed_on=timezone.now())

        response = self.assertListFetch(menu_url, allow_viewers=False, allow_editors=True, allow_agents=True)

        menu = response.json()["results"]
        self.assertEqual(
            [
                {"id": "mine", "name": "My Tickets", "icon": "coffee", "count": 2},
                {"id": "unassigned", "name": "Unassigned", "icon": "mail", "count": 1},
                {"id": "all", "name": "All", "icon": "archive", "count": 3},
            ],
            menu,
        )

    @mock_mailroom
    def test_folder(self, mr_mocks):
        self.login(self.admin)

        contact1 = self.create_contact("Joe", phone="123", last_seen_on=timezone.now())
        contact2 = self.create_contact("Frank", phone="124", last_seen_on=timezone.now())
        contact3 = self.create_contact("Anne", phone="125", last_seen_on=timezone.now())
        self.create_contact("Mary No tickets", phone="126", last_seen_on=timezone.now())
        self.create_contact("Mr Other Org", phone="126", last_seen_on=timezone.now(), org=self.org2)

        open_url = reverse("tickets.ticket_folder", kwargs={"folder": "all", "status": "open"})
        closed_url = reverse("tickets.ticket_folder", kwargs={"folder": "all", "status": "closed"})
        mine_url = reverse("tickets.ticket_folder", kwargs={"folder": "mine", "status": "open"})
        unassigned_url = reverse("tickets.ticket_folder", kwargs={"folder": "unassigned", "status": "open"})

        def assert_tickets(resp, tickets: list):
            actual_tickets = [t["ticket"]["uuid"] for t in resp.json()["results"]]
            expected_tickets = [str(t.uuid) for t in tickets]
            self.assertEqual(expected_tickets, actual_tickets)

        # no tickets yet so no contacts returned
        response = self.client.get(open_url)
        assert_tickets(response, [])

        # contact 1 has two open tickets
        c1_t1 = self.create_ticket(self.mailgun, contact1, "Question 1")
        # assign it
        c1_t1.assign(self.admin, assignee=self.admin, note="I've got this")
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

        joes_open_tickets = contact1.tickets.filter(status="O").order_by("-opened_on")

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
                        "attachments": [],
                    },
                    "ticket": {
                        "uuid": str(contact2.tickets.filter(status="O").first().uuid),
                        "assignee": None,
                        "topic": {"uuid": matchers.UUID4String(), "name": "General"},
                        "body": "Question 3",
                        "last_activity_on": matchers.ISODate(),
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
                        "sender": {"id": self.admin.id, "email": "admin@nyaruka.com"},
                        "attachments": [],
                    },
                    "ticket": {
                        "uuid": str(joes_open_tickets[0].uuid),
                        "assignee": None,
                        "topic": {"uuid": matchers.UUID4String(), "name": "General"},
                        "body": "Question 2",
                        "last_activity_on": matchers.ISODate(),
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
                        "sender": {"id": self.admin.id, "email": "admin@nyaruka.com"},
                        "attachments": [],
                    },
                    "ticket": {
                        "uuid": str(joes_open_tickets[1].uuid),
                        "assignee": {
                            "id": self.admin.id,
                            "first_name": "Andy",
                            "last_name": "",
                            "email": "admin@nyaruka.com",
                        },
                        "topic": {"uuid": matchers.UUID4String(), "name": "General"},
                        "body": "Question 1",
                        "last_activity_on": matchers.ISODate(),
                        "closed_on": None,
                    },
                },
            ]
        }
        self.assertEqual(expected_json, response.json())

        # test before and after windowing
        response = self.client.get(f"{open_url}?before={datetime_to_timestamp(c2_t1.last_activity_on)}")
        self.assertEqual(2, len(response.json()["results"]))

        response = self.client.get(f"{open_url}?after={datetime_to_timestamp(c1_t2.last_activity_on)}")
        self.assertEqual(1, len(response.json()["results"]))

        # the two unassigned tickets
        response = self.client.get(unassigned_url)
        assert_tickets(response, [c2_t1, c1_t2])

        # one assigned ticket for mine
        response = self.client.get(mine_url)
        assert_tickets(response, [c1_t1])

        # fetching closed folder returns all closed tickets
        response = self.client.get(closed_url)
        assert_tickets(response, [c3_t2, c3_t1, c2_t2])

        # deep linking to a single ticket returns just that ticket
        response = self.client.get(f"{open_url}{str(c1_t1.uuid)}")
        assert_tickets(response, [c1_t1])

        # make sure when paging we get a next url
        with patch("temba.tickets.views.TicketCRUDL.Folder.paginate_by", 1):
            response = self.client.get(open_url + "?_format=json")
            self.assertIsNotNone(response.json()["next"])

    @mock_mailroom
    def test_note(self, mr_mocks):
        ticket = self.create_ticket(self.mailgun, self.contact, "Ticket 1")

        update_url = reverse("tickets.ticket_note", args=[ticket.uuid])

        self.assertUpdateFetch(
            update_url, allow_viewers=False, allow_editors=True, allow_agents=True, form_fields=["note"]
        )

        self.assertUpdateSubmit(
            update_url, {"note": ""}, form_errors={"note": "This field is required."}, object_unchanged=ticket
        )

        self.assertUpdateSubmit(update_url, {"note": "I have a bad feeling about this."}, success_status=200)

        self.assertEqual(1, ticket.events.filter(event_type=TicketEvent.TYPE_NOTE_ADDED).count())

    @mock_mailroom
    def test_assign(self, mr_mocks):
        ticket = self.create_ticket(self.mailgun, self.contact, "Some ticket")

        assign_url = reverse("tickets.ticket_assign", args=[ticket.uuid])

        response = self.assertUpdateFetch(
            assign_url, allow_viewers=False, allow_editors=True, allow_agents=True, form_fields=["note", "assignee"]
        )
        # should show unassigned as option plus other permitted users
        self.assertEqual(
            [
                ("", "Unassigned"),
                (self.admin.id, "Andy"),
                (self.agent.id, "Agnes"),
                (self.editor.id, "Ed McEdits"),
            ],
            list(response.context["form"].fields["assignee"].choices),
        )

        self.assertUpdateSubmit(
            assign_url, {"assignee": self.admin.id, "note": "You got this one"}, success_status=200
        )
        ticket.refresh_from_db()
        self.assertEqual(self.admin, ticket.assignee)

        last_event = ticket.events.order_by("id").last()
        self.assertEqual(self.admin, last_event.assignee)
        self.assertEqual("You got this one", last_event.note)

        # now fetch it again to make sure our initial value is set
        self.assertUpdateFetch(
            assign_url,
            allow_viewers=False,
            allow_editors=True,
            allow_agents=True,
            form_fields={"note": None, "assignee": self.admin.id},
        )

        # submit an assignment to the same person
        self.assertUpdateSubmit(
            assign_url, {"assignee": self.admin.id, "note": "Have you looked?"}, success_status=200
        )

        # this should create a note event instead of an assignment event
        last_event = ticket.events.all().last()
        self.assertIsNone(last_event.assignee)
        self.assertEqual("Have you looked?", last_event.note)

        # submit with no assignee to un-assign
        self.assertUpdateSubmit(assign_url, {"assignee": ""}, success_status=200)

        ticket.refresh_from_db()
        self.assertIsNone(ticket.assignee)

    def test_export_stats(self):
        export_url = reverse("tickets.ticket_export_stats")

        self.login(self.admin)

        response = self.client.get(export_url)
        self.assertEqual(200, response.status_code)
        self.assertEqual("application/ms-excel", response["Content-Type"])
        self.assertEqual(
            f"attachment; filename=ticket-stats-{timezone.now().strftime('%Y-%m-%d')}.xlsx",
            response["Content-Disposition"],
        )


class TicketerTest(TembaTest):
    @patch("temba.mailroom.client.MailroomClient.ticket_close")
    def test_release(self, mock_ticket_close):
        ticketer = Ticketer.create(self.org, self.user, MailgunType.slug, "Email (bob@acme.com)", {})

        contact = self.create_contact("Bob", urns=["twitter:bobby"])

        ticket = self.create_ticket(ticketer, contact, "Where are my cookies?")

        # release it
        ticketer.release(self.user)
        ticketer.refresh_from_db()
        self.assertFalse(ticketer.is_active)
        self.assertEqual(self.user, ticketer.modified_by)

        # will have asked mailroom to close the ticket
        mock_ticket_close.assert_called_once_with(self.org.id, self.user.id, [ticket.id], force=True)

        # reactivate
        ticketer.is_active = True
        ticketer.save()

        # add a dependency and try again
        flow = self.create_flow("Deps")
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
        self.assertContains(response, "ticketer/delete/")
        self.assertContains(response, "HTTP Log")
        self.assertContains(response, reverse("request_logs.httplog_ticketer", args=[ticketer.uuid]))

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
        flow = self.create_flow("Color Flow")
        flow.ticketer_dependencies.add(ticketer)
        self.assertFalse(flow.has_issues)

        response = self.assertDeleteFetch(delete_url)
        self.assertContains(response, "is used by the following items but can still be deleted:")
        self.assertContains(response, "Color Flow")

        self.assertDeleteSubmit(delete_url, object_deactivated=ticketer, success_status=200)

        flow.refresh_from_db()
        self.assertTrue(flow.has_issues)
        self.assertNotIn(ticketer, flow.ticketer_dependencies.all())


class TopicTest(TembaTest):
    def test_create(self):
        topic1 = Topic.create(self.org, self.admin, "Sales")

        self.assertEqual("Sales", topic1.name)
        self.assertEqual("Sales", str(topic1))
        self.assertEqual(f'<Topic: uuid={topic1.uuid} name="Sales">', repr(topic1))

        # try to create with invalid name
        with self.assertRaises(AssertionError):
            Topic.create(self.org, self.admin, '"Support"')

        # try to create with name that already exists
        with self.assertRaises(AssertionError):
            Topic.create(self.org, self.admin, "Sales")

    @override_settings(ORG_LIMIT_DEFAULTS={"topics": 3})
    def test_import(self):
        def _import(definition, preview=False):
            return Topic.import_def(self.org, self.admin, definition, preview=preview)

        # preview import as dependency ref from flow inspection
        topic1, result = _import({"uuid": "0c81be38-8481-4a20-92ca-67e9a5617e77", "name": "Sales"}, preview=True)
        self.assertIsNone(topic1)
        self.assertEqual(Topic.ImportResult.CREATED, result)
        self.assertEqual(0, Topic.objects.filter(name="Sales").count())

        # import as dependency ref from flow inspection
        topic1, result = _import({"uuid": "0c81be38-8481-4a20-92ca-67e9a5617e77", "name": "Sales"})
        self.assertNotEqual("0c81be38-8481-4a20-92ca-67e9a5617e77", str(topic1.uuid))  # UUIDs never trusted
        self.assertEqual("Sales", topic1.name)
        self.assertEqual(Topic.ImportResult.CREATED, result)

        # preview import same definition again
        topic2, result = _import({"uuid": "0c81be38-8481-4a20-92ca-67e9a5617e77", "name": "Sales"}, preview=True)
        self.assertEqual(topic1, topic2)
        self.assertEqual(Topic.ImportResult.MATCHED, result)

        # import same definition again
        topic2, result = _import({"uuid": "0c81be38-8481-4a20-92ca-67e9a5617e77", "name": "Sales"})
        self.assertEqual(topic1, topic2)
        self.assertEqual("Sales", topic2.name)
        self.assertEqual(Topic.ImportResult.MATCHED, result)

        # import different UUID but same name
        topic3, result = _import({"uuid": "89a2265b-0caf-478f-837c-187fc8c32b46", "name": "Sales"})
        self.assertEqual(topic2, topic3)
        self.assertEqual(Topic.ImportResult.MATCHED, result)

        topic4 = Topic.create(self.org, self.admin, "Support")

        # import with UUID of existing thing (i.e. importing an export from this workspace)
        topic5, result = _import({"uuid": str(topic4.uuid), "name": "Support"})
        self.assertEqual(topic4, topic5)
        self.assertEqual(Topic.ImportResult.MATCHED, result)

        # preview import with UUID of existing thing with different name
        topic6, result = _import({"uuid": str(topic4.uuid), "name": "Help"}, preview=True)
        self.assertEqual(topic5, topic6)
        self.assertEqual("Support", topic6.name)  # not actually updated
        self.assertEqual(Topic.ImportResult.UPDATED, result)

        # import with UUID of existing thing with different name
        topic6, result = _import({"uuid": str(topic4.uuid), "name": "Help"})
        self.assertEqual(topic5, topic6)
        self.assertEqual("Help", topic6.name)  # updated
        self.assertEqual(Topic.ImportResult.UPDATED, result)

        # import with UUID of existing thing and name that conflicts with another existing thing
        topic7, result = _import({"uuid": str(topic4.uuid), "name": "Sales"})
        self.assertEqual(topic6, topic7)
        self.assertEqual("Sales 2", topic7.name)  # updated with suffix to make it unique
        self.assertEqual(Topic.ImportResult.UPDATED, result)

        # import definition of default topic from other workspace
        topic8, result = _import({"uuid": "bfacf01f-50d5-4236-9faa-7673bb4a9520", "name": "General"})
        self.assertEqual(self.org.default_ticket_topic, topic8)
        self.assertEqual(Topic.ImportResult.MATCHED, result)

        # import definition of default topic from this workspace
        topic9, result = _import({"uuid": str(self.org.default_ticket_topic.uuid), "name": "General"})
        self.assertEqual(self.org.default_ticket_topic, topic9)
        self.assertEqual(Topic.ImportResult.MATCHED, result)

        # import definition of default topic from this workspace... but with different name
        topic10, result = _import({"uuid": str(self.org.default_ticket_topic.uuid), "name": "Default"})
        self.assertEqual(self.org.default_ticket_topic, topic10)
        self.assertEqual("General", topic10.name)  # unchanged
        self.assertEqual(Topic.ImportResult.MATCHED, result)

        # import definition with name that can be cleaned and then matches existing
        topic11, result = _import({"uuid": "e694bad8-9cca-4efd-9f07-cb13248ed5e8", "name": " Sales\0 "})
        self.assertEqual("Sales", topic11.name)
        self.assertEqual(Topic.ImportResult.MATCHED, result)

        # import definition with name that can be cleaned and created new
        topic12, result = _import({"uuid": "c537ad58-ab2e-4b3a-8677-2766a2d14efe", "name": ' "Testing" '})
        self.assertEqual("'Testing'", topic12.name)
        self.assertEqual(Topic.ImportResult.CREATED, result)

        # try to import with name that can't be cleaned to something valid
        topic13, result = _import({"uuid": "c537ad58-ab2e-4b3a-8677-2766a2d14efe", "name": "  "})
        self.assertIsNone(topic13)
        self.assertEqual(Topic.ImportResult.IGNORED_INVALID, result)

        # import with UUID of existing thing and invalid name which will be ignored
        topic14, result = _import({"uuid": str(topic4.uuid), "name": "  "})
        self.assertEqual(topic4, topic14)
        self.assertEqual(Topic.ImportResult.MATCHED, result)

        # try to import new now that we've reached org limit
        topic15, result = _import({"uuid": "bef5f64c-0ad5-4ee0-9c9f-b3f471ec3b0c", "name": "Yet More"})
        self.assertIsNone(topic15)
        self.assertEqual(Topic.ImportResult.IGNORED_LIMIT_REACHED, result)


class TeamTest(TembaTest):
    def test_create(self):
        team1 = Team.create(self.org, self.admin, "Sales")
        self.admin.set_team(team1)
        self.agent.set_team(team1)

        self.assertEqual("Sales", team1.name)
        self.assertEqual("Sales", str(team1))
        self.assertEqual(f'<Team: uuid={team1.uuid} name="Sales">', repr(team1))

        self.assertEqual({self.admin, self.agent}, set(team1.get_users()))

        # try to create with invalid name
        with self.assertRaises(AssertionError):
            Team.create(self.org, self.admin, '"Support"')

        # try to create with name that already exists
        with self.assertRaises(AssertionError):
            Team.create(self.org, self.admin, "Sales")

    def test_release(self):
        team1 = Team.create(self.org, self.admin, "Sales")
        self.admin.set_team(team1)
        self.agent.set_team(team1)

        team1.release(self.admin)

        self.assertFalse(team1.is_active)
        self.assertTrue(team1.name.startswith("deleted-"))

        self.assertEqual(0, team1.get_users().count())


class TicketDailyCountTest(TembaTest):
    def test_model(self):
        sales = Team.create(self.org, self.admin, "Sales")
        self.agent.set_team(sales)
        self.editor.set_team(sales)

        self._record_opening(self.org, date(2022, 4, 30))
        self._record_opening(self.org, date(2022, 5, 3))
        self._record_assignment(self.org, self.admin, date(2022, 5, 3))
        self._record_reply(self.org, self.admin, date(2022, 5, 3))

        self._record_reply(self.org, self.editor, date(2022, 5, 4))
        self._record_reply(self.org, self.agent, date(2022, 5, 4))

        self._record_reply(self.org, self.admin, date(2022, 5, 5))
        self._record_reply(self.org, self.admin, date(2022, 5, 5))
        self._record_opening(self.org, date(2022, 5, 5))
        self._record_reply(self.org, self.agent, date(2022, 5, 5))

        def assert_counts():
            # openings tracked at org scope
            self.assertEqual(3, TicketDailyCount.get_by_org(self.org, TicketDailyCount.TYPE_OPENING).total())
            self.assertEqual(
                2, TicketDailyCount.get_by_org(self.org, TicketDailyCount.TYPE_OPENING, since=date(2022, 5, 1)).total()
            )
            self.assertEqual(
                1, TicketDailyCount.get_by_org(self.org, TicketDailyCount.TYPE_OPENING, until=date(2022, 5, 1)).total()
            )
            self.assertEqual(0, TicketDailyCount.get_by_org(self.org2, TicketDailyCount.TYPE_OPENING).total())
            self.assertEqual(
                [(date(2022, 4, 30), 1), (date(2022, 5, 3), 1), (date(2022, 5, 5), 1)],
                TicketDailyCount.get_by_org(self.org, TicketDailyCount.TYPE_OPENING).day_totals(),
            )
            self.assertEqual(
                [(4, 1), (5, 2)], TicketDailyCount.get_by_org(self.org, TicketDailyCount.TYPE_OPENING).month_totals()
            )

            # assignments tracked at org+user scope
            self.assertEqual(
                1, TicketDailyCount.get_by_users(self.org, [self.admin], TicketDailyCount.TYPE_ASSIGNMENT).total()
            )
            self.assertEqual(
                0, TicketDailyCount.get_by_users(self.org, [self.agent], TicketDailyCount.TYPE_ASSIGNMENT).total()
            )
            self.assertEqual(
                {self.admin: 1, self.agent: 0},
                TicketDailyCount.get_by_users(
                    self.org, [self.admin, self.agent], TicketDailyCount.TYPE_ASSIGNMENT
                ).scope_totals(),
            )
            self.assertEqual(
                [(date(2022, 5, 3), 1)],
                TicketDailyCount.get_by_users(self.org, [self.admin], TicketDailyCount.TYPE_ASSIGNMENT).day_totals(),
            )

            # replies tracked at org scope, team scope and user-in-org scope
            self.assertEqual(6, TicketDailyCount.get_by_org(self.org, TicketDailyCount.TYPE_REPLY).total())
            self.assertEqual(0, TicketDailyCount.get_by_org(self.org2, TicketDailyCount.TYPE_REPLY).total())
            self.assertEqual(3, TicketDailyCount.get_by_teams([sales], TicketDailyCount.TYPE_REPLY).total())
            self.assertEqual(
                3, TicketDailyCount.get_by_users(self.org, [self.admin], TicketDailyCount.TYPE_REPLY).total()
            )
            self.assertEqual(
                1, TicketDailyCount.get_by_users(self.org, [self.editor], TicketDailyCount.TYPE_REPLY).total()
            )
            self.assertEqual(
                2, TicketDailyCount.get_by_users(self.org, [self.agent], TicketDailyCount.TYPE_REPLY).total()
            )

        assert_counts()
        self.assertEqual(19, TicketDailyCount.objects.count())

        TicketDailyCount.squash()

        assert_counts()
        self.assertEqual(14, TicketDailyCount.objects.count())

        workbook = export_ticket_stats(self.org, date(2022, 4, 30), date(2022, 5, 6))
        self.assertEqual(["Tickets"], workbook.sheetnames)
        self.assertExcelRow(
            workbook.active, 1, ["", "Opened", "Replies", "Reply Time (Secs)"] + ["Assigned", "Replies"] * 5
        )
        self.assertExcelRow(workbook.active, 2, [date(2022, 4, 30), 1, 0, "", 0, 0, 0, 0, 0, 0, 0, 0, 0, 0])
        self.assertExcelRow(workbook.active, 3, [date(2022, 5, 1), 0, 0, "", 0, 0, 0, 0, 0, 0, 0, 0, 0, 0])
        self.assertExcelRow(workbook.active, 4, [date(2022, 5, 2), 0, 0, "", 0, 0, 0, 0, 0, 0, 0, 0, 0, 0])
        self.assertExcelRow(workbook.active, 5, [date(2022, 5, 3), 1, 1, "", 1, 1, 0, 0, 0, 0, 0, 0, 0, 0])
        self.assertExcelRow(workbook.active, 6, [date(2022, 5, 4), 0, 2, "", 0, 0, 0, 1, 0, 1, 0, 0, 0, 0])
        self.assertExcelRow(workbook.active, 7, [date(2022, 5, 5), 1, 3, "", 0, 2, 0, 1, 0, 0, 0, 0, 0, 0])

    def _record_opening(self, org, d: date):
        TicketDailyCount.objects.create(count_type=TicketDailyCount.TYPE_OPENING, scope=f"o:{org.id}", day=d, count=1)

    def _record_assignment(self, org, user, d: date):
        TicketDailyCount.objects.create(
            count_type=TicketDailyCount.TYPE_ASSIGNMENT, scope=f"o:{org.id}:u:{user.id}", day=d, count=1
        )

    def _record_reply(self, org, user, d: date):
        TicketDailyCount.objects.create(count_type=TicketDailyCount.TYPE_REPLY, scope=f"o:{org.id}", day=d, count=1)
        if user.settings.team:
            TicketDailyCount.objects.create(
                count_type=TicketDailyCount.TYPE_REPLY, scope=f"t:{user.settings.team.id}", day=d, count=1
            )
        TicketDailyCount.objects.create(
            count_type=TicketDailyCount.TYPE_REPLY, scope=f"o:{org.id}:u:{user.id}", day=d, count=1
        )


class TicketDailyTimingTest(TembaTest):
    def test_model(self):
        self._record_first_reply(self.org, date(2022, 4, 30), 60)
        self._record_first_reply(self.org, date(2022, 5, 1), 60)
        self._record_first_reply(self.org, date(2022, 5, 1), 120)
        self._record_first_reply(self.org, date(2022, 5, 1), 180)
        self._record_first_reply(self.org, date(2022, 5, 2), 11)
        self._record_first_reply(self.org, date(2022, 5, 2), 70)
        self._record_last_close(self.org, date(2022, 5, 1), 100)
        self._record_last_close(self.org, date(2022, 5, 1), 100, undo=True)
        self._record_last_close(self.org, date(2022, 5, 1), 200)
        self._record_last_close(self.org, date(2022, 5, 1), 300)
        self._record_last_close(self.org, date(2022, 5, 2), 100)

        def assert_timings():
            self.assertEqual(6, TicketDailyTiming.get_by_org(self.org, TicketDailyTiming.TYPE_FIRST_REPLY).total())
            self.assertEqual(
                [(date(2022, 4, 30), 1), (date(2022, 5, 1), 3), (date(2022, 5, 2), 2)],
                TicketDailyTiming.get_by_org(self.org, TicketDailyTiming.TYPE_FIRST_REPLY).day_totals(),
            )
            self.assertEqual(
                [(date(2022, 4, 30), 60.0), (date(2022, 5, 1), 120.0), (date(2022, 5, 2), 40.5)],
                TicketDailyTiming.get_by_org(self.org, TicketDailyTiming.TYPE_FIRST_REPLY).day_averages(rounded=False),
            )

            self.assertEqual(3, TicketDailyTiming.get_by_org(self.org, TicketDailyTiming.TYPE_LAST_CLOSE).total())
            self.assertEqual(
                [(date(2022, 5, 1), 2), (date(2022, 5, 2), 1)],
                TicketDailyTiming.get_by_org(self.org, TicketDailyTiming.TYPE_LAST_CLOSE).day_totals(),
            )
            self.assertEqual(
                [(date(2022, 5, 1), 250.0), (date(2022, 5, 2), 100.0)],
                TicketDailyTiming.get_by_org(self.org, TicketDailyTiming.TYPE_LAST_CLOSE).day_averages(),
            )

        assert_timings()

        TicketDailyTiming.squash()

        assert_timings()

        workbook = export_ticket_stats(self.org, date(2022, 4, 30), date(2022, 5, 4))
        self.assertEqual(["Tickets"], workbook.sheetnames)
        self.assertExcelRow(
            workbook.active, 1, ["", "Opened", "Replies", "Reply Time (Secs)"] + ["Assigned", "Replies"] * 5
        )
        self.assertExcelRow(workbook.active, 2, [date(2022, 4, 30), 0, 0, 60, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0])
        self.assertExcelRow(workbook.active, 3, [date(2022, 5, 1), 0, 0, 120, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0])
        self.assertExcelRow(workbook.active, 4, [date(2022, 5, 2), 0, 0, 40, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0])
        self.assertExcelRow(workbook.active, 5, [date(2022, 5, 3), 0, 0, "", 0, 0, 0, 0, 0, 0, 0, 0, 0, 0])

    def _record_first_reply(self, org, d: date, seconds: int):
        TicketDailyTiming.objects.create(
            count_type=TicketDailyTiming.TYPE_FIRST_REPLY, scope=f"o:{org.id}", day=d, count=1, seconds=seconds
        )

    def _record_last_close(self, org, d: date, seconds: int, undo: bool = False):
        count, seconds = (-1, -seconds) if undo else (1, seconds)

        TicketDailyTiming.objects.create(
            count_type=TicketDailyTiming.TYPE_LAST_CLOSE, scope=f"o:{org.id}", day=d, count=count, seconds=seconds
        )


class BackfillTicketDailyReplyTimingsTest(MigrationTest):
    app = "tickets"
    migrate_from = "0035_ticketdailytiming_ticket_replied_on_and_more"
    migrate_to = "0036_backfill_ticket_reply_timings"

    def setUpBeforeMigration(self, apps):
        ticketer = self.org.ticketers.get()
        contact = self.create_contact("Bob", phone="+1234567890")

        # ticket opened on May 1
        self.ticket1 = self.create_ticket(
            ticketer, contact, "Help", opened_on=datetime(2022, 5, 1, 10, 30, 0, 0, tzinfo=timezone.utc)
        )

        # first reply 30 mins later, then another 30 mins after that
        self._ticket_reply(
            self.ticket1, "What is the problem?", datetime(2022, 5, 1, 11, 0, 0, 0, tzinfo=timezone.utc)
        )
        self._ticket_reply(self.ticket1, "Still there?", datetime(2022, 5, 1, 11, 30, 0, 0, tzinfo=timezone.utc))

        # another ticket opened on May 1
        self.ticket2 = self.create_ticket(
            ticketer, contact, "Help", opened_on=datetime(2022, 5, 1, 13, 0, 0, 0, tzinfo=timezone.utc)
        )

        # only reply 1 hour later
        self._ticket_reply(
            self.ticket2, "What is the problem?", datetime(2022, 5, 1, 14, 0, 0, 0, tzinfo=timezone.utc)
        )

        # another ticket opened on May 2, no replies
        self.ticket3 = self.create_ticket(
            ticketer, contact, "Help", opened_on=datetime(2022, 5, 2, 13, 0, 0, 0, tzinfo=timezone.utc)
        )

        # finally another ticket on May 2 which has a reply that is already counted
        self.ticket4 = self.create_ticket(
            ticketer, contact, "Help", opened_on=datetime(2022, 5, 2, 15, 0, 0, 0, tzinfo=timezone.utc)
        )
        self._ticket_reply(self.ticket4, "Hi?", datetime(2022, 5, 2, 15, 30, 0, 0, tzinfo=timezone.utc))

        self.ticket4.replied_on = datetime(2022, 5, 1, 15, 30, 0, 0, tzinfo=timezone.utc)
        self.ticket4.save(update_fields=("replied_on",))

        TicketDailyTiming.objects.create(
            count_type=TicketDailyTiming.TYPE_FIRST_REPLY,
            scope=f"o:{self.org.id}",
            day=date(2022, 5, 2),
            count=1,
            seconds=30 * 60,
        )

    def test_migration(self):
        self.ticket1.refresh_from_db()
        self.ticket2.refresh_from_db()
        self.ticket3.refresh_from_db()
        self.ticket4.refresh_from_db()

        self.assertEqual(datetime(2022, 5, 1, 11, 0, 0, 0, tzinfo=timezone.utc), self.ticket1.replied_on)
        self.assertEqual(datetime(2022, 5, 1, 14, 0, 0, 0, tzinfo=timezone.utc), self.ticket2.replied_on)
        self.assertIsNone(self.ticket3.replied_on)
        self.assertEqual(datetime(2022, 5, 2, 15, 30, 0, 0, tzinfo=timezone.utc), self.ticket4.replied_on)

        self.assertEqual(
            [(date(2022, 5, 1), 2), (date(2022, 5, 2), 1)],
            TicketDailyTiming.get_by_org(self.org, TicketDailyTiming.TYPE_FIRST_REPLY).day_totals(),
        )
        self.assertEqual(
            [(date(2022, 5, 1), 2700.0), (date(2022, 5, 2), 1800.0)],
            TicketDailyTiming.get_by_org(self.org, TicketDailyTiming.TYPE_FIRST_REPLY).day_averages(),
        )

    def _ticket_reply(self, ticket, text, when):
        self.create_broadcast(self.admin, text, contacts=[ticket.contact], ticket=ticket, created_on=when)
