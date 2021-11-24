from unittest.mock import patch

from django.test.utils import override_settings
from django.urls import reverse
from django.utils import timezone

from temba.contacts.models import Contact
from temba.tests import CRUDLTestMixin, TembaTest, matchers, mock_mailroom
from temba.utils.dates import datetime_to_timestamp

from .models import Ticket, TicketCount, Ticketer, TicketEvent, Topic
from .tasks import squash_ticketcounts
from .types import reload_ticketer_types
from .types.internal import InternalType
from .types.mailgun import MailgunType
from .types.zendesk import ZendeskType


class TicketTest(TembaTest):
    def test_model(self):
        ticketer = Ticketer.create(self.org, self.user, MailgunType.slug, "Email (bob@acme.com)", {})
        topic = Topic.get_or_create(self.org, self.admin, "Sales")
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
                        "sender": {"id": self.admin.id, "email": "Administrator@nyaruka.com"},
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
                        "sender": {"id": self.admin.id, "email": "Administrator@nyaruka.com"},
                        "attachments": [],
                    },
                    "ticket": {
                        "uuid": str(joes_open_tickets[1].uuid),
                        "assignee": {
                            "id": self.admin.id,
                            "first_name": "",
                            "last_name": "",
                            "email": "Administrator@nyaruka.com",
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
                (self.admin.id, "Administrator"),
                (self.agent.id, "Agent"),
                (self.editor.id, "Editor"),
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
    def test_is_valid_name(self):
        self.assertTrue(Topic.is_valid_name("Sales"))
        self.assertTrue(Topic.is_valid_name("Support"))
        self.assertFalse(Topic.is_valid_name(""))
        self.assertFalse(Topic.is_valid_name("   "))
        self.assertFalse(Topic.is_valid_name("  x  "))
        self.assertFalse(Topic.is_valid_name("!Sales"))
        self.assertFalse(Topic.is_valid_name("x" * 65))  # too long

    def test_model(self):
        topic1 = Topic.get_or_create(self.org, self.admin, "Sales")
        topic2 = Topic.get_or_create(self.org, self.admin, "Support")

        self.assertEqual(topic1, Topic.get_or_create(self.org, self.admin, "Sales"))
        self.assertEqual(topic2, Topic.get_or_create(self.org, self.admin, "SUPPORT"))

        self.assertEqual(f"Topic[uuid={topic1.uuid}, topic=Sales]", str(topic1))
