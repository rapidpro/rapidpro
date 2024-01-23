from datetime import date, datetime, timedelta, timezone as tzone
from unittest.mock import patch

from openpyxl import load_workbook

from django.conf import settings
from django.test.utils import override_settings
from django.urls import reverse
from django.utils import timezone

from temba.contacts.models import Contact, ContactField, ContactGroup, ContactURN
from temba.orgs.models import Export
from temba.tests import CRUDLTestMixin, TembaTest, matchers, mock_mailroom
from temba.utils.dates import datetime_to_timestamp
from temba.utils.uuid import uuid4

from .models import (
    Team,
    Ticket,
    TicketCount,
    TicketDailyCount,
    TicketDailyTiming,
    TicketEvent,
    TicketExport,
    Topic,
    export_ticket_stats,
)
from .tasks import squash_ticket_counts


class TicketTest(TembaTest):
    def test_model(self):
        topic = Topic.create(self.org, self.admin, "Sales")
        contact = self.create_contact("Bob", urns=["twitter:bobby"])

        ticket = Ticket.objects.create(
            org=self.org,
            contact=contact,
            topic=self.org.default_ticket_topic,
            body="Where are my cookies?",
            status="O",
        )

        self.assertEqual(f"Ticket[uuid={ticket.uuid}, topic=General]", str(ticket))

        # test bulk assignment
        with patch("temba.mailroom.client.MailroomClient.ticket_assign") as mock_assign:
            Ticket.bulk_assign(self.org, self.admin, [ticket], self.agent)

        mock_assign.assert_called_once_with(self.org.id, self.admin.id, [ticket.id], self.agent.id)
        mock_assign.reset_mock()

        # test bulk un-assignment
        with patch("temba.mailroom.client.MailroomClient.ticket_assign") as mock_assign:
            Ticket.bulk_assign(self.org, self.admin, [ticket], None)

        mock_assign.assert_called_once_with(self.org.id, self.admin.id, [ticket.id], None)
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
        general = self.org.default_ticket_topic
        cats = Topic.create(self.org, self.admin, "Cats")

        contact1 = self.create_contact("Bob", urns=["twitter:bobby"])
        contact2 = self.create_contact("Jim", urns=["twitter:jimmy"])

        org2_general = self.org2.default_ticket_topic
        org2_contact = self.create_contact("Bob", urns=["twitter:bobby"], org=self.org2)

        t1 = self.create_ticket(contact1, "Test 1", topic=general)
        t2 = self.create_ticket(contact2, "Test 2", topic=general)
        t3 = self.create_ticket(contact1, "Test 3", topic=general)
        t4 = self.create_ticket(contact2, "Test 4", topic=cats)
        t5 = self.create_ticket(contact1, "Test 5", topic=cats)
        t6 = self.create_ticket(org2_contact, "Test 6", topic=org2_general)

        def assert_counts(
            org, *, assignee_open: dict, assignee_closed: dict, topic_open: dict, topic_closed: dict, contacts: dict
        ):
            assignees = [None] + list(Ticket.get_allowed_assignees(org))

            self.assertEqual(assignee_open, TicketCount.get_by_assignees(org, assignees, Ticket.STATUS_OPEN))
            self.assertEqual(assignee_closed, TicketCount.get_by_assignees(org, assignees, Ticket.STATUS_CLOSED))

            self.assertEqual(sum(assignee_open.values()), TicketCount.get_all(org, Ticket.STATUS_OPEN))
            self.assertEqual(sum(assignee_closed.values()), TicketCount.get_all(org, Ticket.STATUS_CLOSED))

            self.assertEqual(topic_open, TicketCount.get_by_topics(org, list(org.topics.all()), Ticket.STATUS_OPEN))
            self.assertEqual(topic_closed, TicketCount.get_by_topics(org, list(org.topics.all()), Ticket.STATUS_CLOSED))

            self.assertEqual(contacts, {c: Contact.objects.get(id=c.id).ticket_count for c in contacts})

        # t1:O/None/General t2:O/None/General t3:O/None/General t4:O/None/Cats t5:O/None/Cats t6:O/None/General
        assert_counts(
            self.org,
            assignee_open={None: 5, self.agent: 0, self.editor: 0, self.admin: 0},
            assignee_closed={None: 0, self.agent: 0, self.editor: 0, self.admin: 0},
            topic_open={general: 3, cats: 2},
            topic_closed={general: 0, cats: 0},
            contacts={contact1: 3, contact2: 2},
        )
        assert_counts(
            self.org2,
            assignee_open={None: 1, self.admin2: 0},
            assignee_closed={None: 0, self.admin2: 0},
            topic_open={org2_general: 1},
            topic_closed={org2_general: 0},
            contacts={org2_contact: 1},
        )

        Ticket.bulk_assign(self.org, self.admin, [t1, t2], assignee=self.agent)
        Ticket.bulk_assign(self.org, self.admin, [t3], assignee=self.editor)
        Ticket.bulk_assign(self.org2, self.admin2, [t6], assignee=self.admin2)

        # t1:O/Agent/General t2:O/Agent/General t3:O/Editor/General t4:O/None/Cats t5:O/None/Cats t6:O/Admin2/General
        assert_counts(
            self.org,
            assignee_open={None: 2, self.agent: 2, self.editor: 1, self.admin: 0},
            assignee_closed={None: 0, self.agent: 0, self.editor: 0, self.admin: 0},
            topic_open={general: 3, cats: 2},
            topic_closed={general: 0, cats: 0},
            contacts={contact1: 3, contact2: 2},
        )
        assert_counts(
            self.org2,
            assignee_open={None: 0, self.admin2: 1},
            assignee_closed={None: 0, self.admin2: 0},
            topic_open={org2_general: 1},
            topic_closed={org2_general: 0},
            contacts={org2_contact: 1},
        )

        Ticket.bulk_close(self.org, self.admin, [t1, t4])
        Ticket.bulk_close(self.org2, self.admin2, [t6])

        # t1:C/Agent/General t2:O/Agent/General t3:O/Editor/General t4:C/None/Cats t5:O/None/Cats t6:C/Admin2/General
        assert_counts(
            self.org,
            assignee_open={None: 1, self.agent: 1, self.editor: 1, self.admin: 0},
            assignee_closed={None: 1, self.agent: 1, self.editor: 0, self.admin: 0},
            topic_open={general: 2, cats: 1},
            topic_closed={general: 1, cats: 1},
            contacts={contact1: 2, contact2: 1},
        )
        assert_counts(
            self.org2,
            assignee_open={None: 0, self.admin2: 0},
            assignee_closed={None: 0, self.admin2: 1},
            topic_open={org2_general: 0},
            topic_closed={org2_general: 1},
            contacts={org2_contact: 0},
        )

        Ticket.bulk_assign(self.org, self.admin, [t1, t5], assignee=self.admin)

        # t1:C/Admin/General t2:O/Agent/General t3:O/Editor/General t4:C/None/Cats t5:O/Admin/Cats t6:C/Admin2/General
        assert_counts(
            self.org,
            assignee_open={None: 0, self.agent: 1, self.editor: 1, self.admin: 1},
            assignee_closed={None: 1, self.agent: 0, self.editor: 0, self.admin: 1},
            topic_open={general: 2, cats: 1},
            topic_closed={general: 1, cats: 1},
            contacts={contact1: 2, contact2: 1},
        )

        Ticket.bulk_reopen(self.org, self.admin, [t4])
        Ticket.bulk_change_topic(self.org, self.admin, [t1], cats)

        # t1:C/Admin/General t2:O/Agent/General t3:O/Editor/General t4:O/None/Cats t5:O/Admin/Cats t6:C/Admin2/General
        assert_counts(
            self.org,
            assignee_open={None: 1, self.agent: 1, self.editor: 1, self.admin: 1},
            assignee_closed={None: 0, self.agent: 0, self.editor: 0, self.admin: 1},
            topic_open={general: 2, cats: 2},
            topic_closed={general: 0, cats: 1},
            contacts={contact1: 2, contact2: 2},
        )

        squash_ticket_counts()  # shouldn't change counts

        assert_counts(
            self.org,
            assignee_open={None: 1, self.agent: 1, self.editor: 1, self.admin: 1},
            assignee_closed={None: 0, self.agent: 0, self.editor: 0, self.admin: 1},
            topic_open={general: 2, cats: 2},
            topic_closed={general: 0, cats: 1},
            contacts={contact1: 2, contact2: 2},
        )

        TicketEvent.objects.all().delete()
        t1.delete()
        t2.delete()
        t6.delete()

        # t3:O/Editor/General t4:O/None/Cats t5:O/Admin/Cats
        assert_counts(
            self.org,
            assignee_open={None: 1, self.agent: 0, self.editor: 1, self.admin: 1},
            assignee_closed={None: 0, self.agent: 0, self.editor: 0, self.admin: 0},
            topic_open={general: 1, cats: 2},
            topic_closed={general: 0, cats: 0},
            contacts={contact1: 2, contact2: 1},
        )
        assert_counts(
            self.org2,
            assignee_open={None: 0, self.admin2: 0},
            assignee_closed={None: 0, self.admin2: 0},
            topic_open={org2_general: 0},
            topic_closed={org2_general: 0},
            contacts={org2_contact: 0},
        )


class TopicCRUDLTest(TembaTest, CRUDLTestMixin):
    def setUp(self):
        super().setUp()

    def test_update(self):
        system_topic = Topic.objects.filter(org=self.org, is_system=True).first()
        user_topic = Topic.objects.create(org=self.org, name="Hot Topic", created_by=self.admin, modified_by=self.admin)

        # can't edit a system topic
        update_url = reverse("tickets.topic_update", args=[system_topic.uuid])
        self.assertUpdateSubmit(
            update_url,
            {"name": "My Topic"},
            form_errors={"name": "Cannot edit system topic"},
            object_unchanged=system_topic,
        )

        # names must be unique
        update_url = reverse("tickets.topic_update", args=[user_topic.uuid])
        self.assertUpdateSubmit(
            update_url,
            {"name": "General"},
            form_errors={"name": "Topic already exists, please try another name"},
            object_unchanged=user_topic,
        )

        # check permissions
        self.assertUpdateFetch(update_url, allow_viewers=False, allow_editors=True, form_fields=["name"])

        # edit successfully
        self.assertUpdateSubmit(update_url, {"name": "Boring Tickets"}, success_status=302)

        user_topic.refresh_from_db()
        self.assertEqual(user_topic.name, "Boring Tickets")


class TicketCRUDLTest(TembaTest, CRUDLTestMixin):
    def setUp(self):
        super().setUp()

        self.contact = self.create_contact("Bob", urns=["twitter:bobby"])

    def test_list(self):
        list_url = reverse("tickets.ticket_list")
        ticket = self.create_ticket(self.contact, "Test 1", assignee=self.admin)

        # just a placeholder view for frontend components
        self.assertListFetch(list_url, allow_viewers=True, allow_editors=True, allow_agents=True, context_objects=[])

        # can hit this page with a uuid
        # TODO: work out reverse for deep link
        # deep_link = reverse(
        #    "tickets.ticket_list", kwargs={"folder": "all", "status": "open", "uuid": str(ticket.uuid)}
        # )

        deep_link = f"{list_url}all/open/{str(ticket.uuid)}/"
        self.assertContentMenu(deep_link, self.admin, ["Edit", "Add Note", "Start Flow"])
        response = self.assertListFetch(
            deep_link, allow_viewers=True, allow_editors=True, allow_agents=True, context_objects=[]
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

        # bad topic should give a 404
        bad_topic_link = f"{list_url}{uuid4()}/open/{str(ticket.uuid)}/"
        response = self.client.get(bad_topic_link)
        self.assertEqual(404, response.status_code)

        response = self.client.get(
            list_url,
            content_type="application/json",
            HTTP_TEMBA_REFERER_PATH=f"/tickets/mine/open/{ticket.uuid}",
        )

        self.assertEqual(("tickets", "mine", "open", str(ticket.uuid)), response.context["temba_referer"])

        # contacts in a flow get interrupt menu option instead
        flow = self.get_flow("color")
        self.contact.current_flow = flow
        self.contact.save()
        deep_link = f"{list_url}all/open/{str(ticket.uuid)}/"
        self.assertContentMenu(deep_link, self.admin, ["Edit", "Add Note", "Interrupt"])

        # closed our tickets don't get extra menu options
        ticket.status = Ticket.STATUS_CLOSED
        ticket.save()
        deep_link = f"{list_url}all/closed/{str(ticket.uuid)}/"
        self.assertContentMenu(deep_link, self.admin, [])

    def test_update(self):
        ticket = self.create_ticket(self.contact, "Test 1", assignee=self.admin)

        update_url = reverse("tickets.ticket_update", args=[ticket.uuid])

        self.assertUpdateFetch(
            update_url, allow_viewers=False, allow_editors=True, allow_agents=True, form_fields=["topic", "body"]
        )

        user_topic = Topic.objects.create(org=self.org, name="Hot Topic", created_by=self.admin, modified_by=self.admin)

        # edit successfully
        self.assertUpdateSubmit(update_url, {"topic": user_topic.id, "body": "This is silly"}, success_status=302)

        ticket.refresh_from_db()
        self.assertEqual(user_topic, ticket.topic)
        self.assertEqual("This is silly", ticket.body)

    def test_menu(self):
        menu_url = reverse("tickets.ticket_menu")

        self.create_ticket(self.contact, "Test 1", assignee=self.admin)
        self.create_ticket(self.contact, "Test 2", assignee=self.admin)
        self.create_ticket(self.contact, "Test 3", assignee=None)
        self.create_ticket(self.contact, "Test 4", closed_on=timezone.now())

        self.assertListFetch(menu_url, allow_viewers=True, allow_editors=True, allow_agents=True)
        self.assertMenu(menu_url, 5, ["My Tickets", "Unassigned", "All", "General"], allow_viewers=True)

    @mock_mailroom
    def test_folder(self, mr_mocks):
        self.login(self.admin)

        user_topic = Topic.objects.create(org=self.org, name="Hot Topic", created_by=self.admin, modified_by=self.admin)

        contact1 = self.create_contact("Joe", phone="123", last_seen_on=timezone.now())
        contact2 = self.create_contact("Frank", phone="124", last_seen_on=timezone.now())
        contact3 = self.create_contact("Anne", phone="125", last_seen_on=timezone.now())
        self.create_contact("Mary No tickets", phone="126", last_seen_on=timezone.now())
        self.create_contact("Mr Other Org", phone="126", last_seen_on=timezone.now(), org=self.org2)
        topic = Topic.objects.filter(org=self.org).first()

        open_url = reverse("tickets.ticket_folder", kwargs={"folder": "all", "status": "open"})
        closed_url = reverse("tickets.ticket_folder", kwargs={"folder": "all", "status": "closed"})
        mine_url = reverse("tickets.ticket_folder", kwargs={"folder": "mine", "status": "open"})
        unassigned_url = reverse("tickets.ticket_folder", kwargs={"folder": "unassigned", "status": "open"})
        system_topic_url = reverse("tickets.ticket_folder", kwargs={"folder": topic.uuid, "status": "open"})
        user_topic_url = reverse("tickets.ticket_folder", kwargs={"folder": user_topic.uuid, "status": "open"})
        bad_topic_url = reverse("tickets.ticket_folder", kwargs={"folder": uuid4(), "status": "open"})

        def assert_tickets(resp, tickets: list):
            actual_tickets = [t["ticket"]["uuid"] for t in resp.json()["results"]]
            expected_tickets = [str(t.uuid) for t in tickets]
            self.assertEqual(expected_tickets, actual_tickets)

        # general topic gets export
        self.assertContentMenu(system_topic_url, self.admin, ["Export"])

        # user topic gets edit too
        self.assertContentMenu(user_topic_url, self.admin, ["Edit", "-", "Export"])

        # no tickets yet so no contacts returned
        response = self.client.get(open_url)
        assert_tickets(response, [])

        # contact 1 has two open tickets
        c1_t1 = self.create_ticket(contact1, "Question 1")
        # assign it
        c1_t1.assign(self.admin, assignee=self.admin)
        c1_t2 = self.create_ticket(contact1, "Question 2")

        # give contact1 and old style broadcast message that doesn't have created_by set
        self.create_incoming_msg(contact1, "I have an issue")
        c1_msg1 = self.create_broadcast(self.admin, "We can help", contacts=[contact1]).msgs.first()
        c1_msg1.created_by = None
        c1_msg1.save(update_fields=("created_by",))

        # contact 2 has an open ticket and a closed ticket
        c2_t1 = self.create_ticket(contact2, "Question 3")
        c2_t2 = self.create_ticket(contact2, "Question 4", closed_on=timezone.now())

        self.create_incoming_msg(contact2, "Anyone there?")
        self.create_incoming_msg(contact2, "Hello?")

        # contact 3 has two closed tickets
        c3_t1 = self.create_ticket(contact3, "Question 5", closed_on=timezone.now())
        c3_t2 = self.create_ticket(contact3, "Question 6", closed_on=timezone.now())

        self.create_outgoing_msg(contact3, "Yes", created_by=self.agent)

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
                        "type": "T",
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
                        "type": "T",
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
                        "type": "T",
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

        # three tickets for our general topic
        response = self.client.get(system_topic_url)
        assert_tickets(response, [c2_t1, c1_t2, c1_t1])

        # bad topic should be a 404
        response = self.client.get(bad_topic_url)
        self.assertEqual(response.status_code, 404)

        # fetching closed folder returns all closed tickets
        response = self.client.get(closed_url)
        assert_tickets(response, [c3_t2, c3_t1, c2_t2])
        self.assertEqual(
            {
                "uuid": str(contact3.uuid),
                "name": "Anne",
                "last_seen_on": matchers.ISODate(),
                "last_msg": {
                    "text": "Yes",
                    "direction": "O",
                    "type": "T",
                    "created_on": matchers.ISODate(),
                    "sender": {"id": self.agent.id, "email": "agent@nyaruka.com"},
                    "attachments": [],
                },
                "ticket": {
                    "uuid": str(c3_t2.uuid),
                    "assignee": None,
                    "topic": {"uuid": matchers.UUID4String(), "name": "General"},
                    "body": "Question 6",
                    "last_activity_on": matchers.ISODate(),
                    "closed_on": matchers.ISODate(),
                },
            },
            response.json()["results"][0],
        )

        # deep linking to a single ticket returns just that ticket
        response = self.client.get(f"{open_url}{str(c1_t1.uuid)}")
        assert_tickets(response, [c1_t1])

        # make sure when paging we get a next url
        with patch("temba.tickets.views.TicketCRUDL.Folder.paginate_by", 1):
            response = self.client.get(open_url + "?_format=json")
            self.assertIsNotNone(response.json()["next"])

    @mock_mailroom
    def test_note(self, mr_mocks):
        ticket = self.create_ticket(self.contact, "Ticket 1")

        update_url = reverse("tickets.ticket_note", args=[ticket.uuid])

        self.assertUpdateFetch(
            update_url, allow_viewers=False, allow_editors=True, allow_agents=True, form_fields=["note"]
        )

        self.assertUpdateSubmit(
            update_url, {"note": ""}, form_errors={"note": "This field is required."}, object_unchanged=ticket
        )

        self.assertUpdateSubmit(update_url, {"note": "I have a bad feeling about this."}, success_status=200)

        self.assertEqual(1, ticket.events.filter(event_type=TicketEvent.TYPE_NOTE_ADDED).count())

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

    def test_export_when_export_already_in_progress(self):
        self.clear_storage()
        self.login(self.admin)
        export_url = reverse("tickets.ticket_export")

        # create a dummy export task so that we won't be able to export
        blocking_export = TicketExport.create(
            self.org, self.admin, start_date=date.today() - timedelta(days=7), end_date=date.today(), with_fields=()
        )
        response = self.client.post(export_url, {"start_date": "2022-06-28", "end_date": "2022-09-28"})
        self.assertModalResponse(response, redirect="/ticket/")

        response = self.client.get("/ticket/")
        self.assertContains(response, "already an export in progress")

        # ok mark that export as finished and try again
        blocking_export.status = Export.STATUS_COMPLETE
        blocking_export.save(update_fields=("status",))

        response = self.client.post(export_url, {"start_date": "2022-06-28", "end_date": "2022-09-28"})
        self.assertModalResponse(response, redirect="/ticket/")
        self.assertEqual(2, Export.objects.count())

    def test_export_empty(self):
        self.login(self.admin)

        # check results of sheet in workbook (no Contact ID column)
        export = self._request_export(start_date=date.today() - timedelta(days=7), end_date=date.today())
        self.assertExcelSheet(
            export[0],
            [
                [
                    "UUID",
                    "Opened On",
                    "Closed On",
                    "Topic",
                    "Assigned To",
                    "Contact UUID",
                    "Contact Name",
                    "URN Scheme",
                    "URN Value",
                ]
            ],
            tz=self.org.timezone,
        )

        with self.anonymous(self.org):
            # anon org doesn't see URN value column
            export = self._request_export(start_date=date.today() - timedelta(days=7), end_date=date.today())
            self.assertExcelSheet(
                export[0],
                [
                    [
                        "UUID",
                        "Opened On",
                        "Closed On",
                        "Topic",
                        "Assigned To",
                        "Contact UUID",
                        "Contact Name",
                        "URN Scheme",
                        "Anon Value",
                    ]
                ],
                tz=self.org.timezone,
            )

        self.clear_storage()

    def test_export(self):
        export_url = reverse("tickets.ticket_export")

        self.login(self.admin)

        gender = self.create_field("gender", "Gender")
        age = self.create_field("age", "Age", value_type=ContactField.TYPE_NUMBER)

        # messages can't be older than org
        self.org.created_on = datetime(2016, 1, 2, 10, tzinfo=tzone.utc)
        self.org.save(update_fields=("created_on",))

        topic = Topic.create(self.org, self.admin, "AFC Richmond")
        assignee = self.admin
        today = timezone.now().astimezone(self.org.timezone).date()

        # create a contact with no urns
        nate = self.create_contact("Nathan Shelley", fields={"gender": "Male"})

        # create a contact with one set of urns
        jamie = self.create_contact("Jamie Tartt", fields={"gender": "Male", "age": 25})
        ContactURN.create(self.org, jamie, "twitter:jamietarttshark")

        # create a contact with multiple urns that have different max priority
        roy = self.create_contact("Roy Kent", fields={"gender": "Male", "age": 41})
        ContactURN.create(self.org, roy, "tel:+1234567890")
        ContactURN.create(self.org, roy, "twitter:roykent")

        # create a contact with multiple urns that have the same max priority
        sam = self.create_contact("Sam Obisanya", fields={"gender": "Male", "age": 22})
        ContactURN.create(self.org, sam, "twitter:nigerianprince", priority=50)
        ContactURN.create(self.org, sam, "tel:+9876543210", priority=50)

        testers = self.create_group("Testers", contacts=[nate, roy])

        # create an open ticket for nate, opened 30 days ago
        ticket1 = self.create_ticket(
            nate,
            body="Y'ello",
            topic=topic,
            assignee=assignee,
            opened_on=timezone.now() - timedelta(days=30),
        )
        # create an open ticket for jamie, opened 25 days ago
        ticket2 = self.create_ticket(
            jamie, body="Hi", topic=topic, assignee=assignee, opened_on=timezone.now() - timedelta(days=25)
        )

        # create a closed ticket for roy, opened yesterday
        ticket3 = self.create_ticket(
            roy,
            body="Hello",
            topic=topic,
            assignee=assignee,
            opened_on=timezone.now() - timedelta(days=1),
            closed_on=timezone.now(),
        )
        # create a closed ticket for sam, opened today
        ticket4 = self.create_ticket(
            sam,
            body="Yo",
            topic=topic,
            assignee=assignee,
            opened_on=timezone.now(),
            closed_on=timezone.now(),
        )

        # create a ticket on another org for rebecca
        self.create_ticket(self.create_contact("Rebecca", urns=["twitter:rwaddingham"], org=self.org2), "Stuff")

        # try to submit without specifying dates (UI doesn't actually allow this)
        response = self.client.post(export_url, {})
        self.assertFormError(response, "form", "start_date", "This field is required.")
        self.assertFormError(response, "form", "end_date", "This field is required.")

        # try to submit with start date in future
        response = self.client.post(export_url, {"start_date": "2200-01-01", "end_date": "2022-09-28"})
        self.assertFormError(response, "form", None, "Start date can't be in the future.")

        # try to submit with start date > end date
        response = self.client.post(export_url, {"start_date": "2022-09-01", "end_date": "2022-03-01"})
        self.assertFormError(response, "form", None, "End date can't be before start date.")

        # check requesting export for last 90 days
        with self.mockReadOnly(assert_models={Ticket, ContactURN, ContactGroup, ContactField}):
            with self.assertNumQueries(29):
                export = self._request_export(start_date=today - timedelta(days=90), end_date=today)

        expected_headers = [
            "UUID",
            "Opened On",
            "Closed On",
            "Topic",
            "Assigned To",
            "Contact UUID",
            "Contact Name",
            "URN Scheme",
            "URN Value",
        ]

        self.assertExcelSheet(
            export[0],
            rows=[
                expected_headers,
                [
                    ticket1.uuid,
                    ticket1.opened_on,
                    "",
                    ticket1.topic.name,
                    ticket1.assignee.email,
                    ticket1.contact.uuid,
                    "Nathan Shelley",
                    "",
                    "",
                ],
                [
                    ticket2.uuid,
                    ticket2.opened_on,
                    "",
                    ticket2.topic.name,
                    ticket2.assignee.email,
                    ticket2.contact.uuid,
                    "Jamie Tartt",
                    "twitter",
                    "jamietarttshark",
                ],
                [
                    ticket3.uuid,
                    ticket3.opened_on,
                    ticket3.closed_on,
                    ticket3.topic.name,
                    ticket3.assignee.email,
                    ticket3.contact.uuid,
                    "Roy Kent",
                    "tel",
                    "+1234567890",
                ],
                [
                    ticket4.uuid,
                    ticket4.opened_on,
                    ticket4.closed_on,
                    ticket4.topic.name,
                    ticket4.assignee.email,
                    ticket4.contact.uuid,
                    "Sam Obisanya",
                    "twitter",
                    "nigerianprince",
                ],
            ],
            tz=self.org.timezone,
        )

        # check requesting export for last 7 days
        with self.mockReadOnly(assert_models={Ticket, ContactURN, ContactGroup, ContactField}):
            export = self._request_export(start_date=today - timedelta(days=7), end_date=today)

        self.assertExcelSheet(
            export[0],
            rows=[
                expected_headers,
                [
                    ticket3.uuid,
                    ticket3.opened_on,
                    ticket3.closed_on,
                    ticket3.topic.name,
                    ticket3.assignee.email,
                    ticket3.contact.uuid,
                    "Roy Kent",
                    "tel",
                    "+1234567890",
                ],
                [
                    ticket4.uuid,
                    ticket4.opened_on,
                    ticket4.closed_on,
                    ticket4.topic.name,
                    ticket4.assignee.email,
                    ticket4.contact.uuid,
                    "Sam Obisanya",
                    "twitter",
                    "nigerianprince",
                ],
            ],
            tz=self.org.timezone,
        )

        # check requesting with contact fields and groups
        with self.mockReadOnly(assert_models={Ticket, ContactURN, ContactGroup, ContactField}):
            export = self._request_export(
                start_date=today - timedelta(days=7), end_date=today, with_fields=(age, gender), with_groups=(testers,)
            )

        self.assertExcelSheet(
            export[0],
            rows=[
                expected_headers + ["Field:Age", "Field:Gender", "Group:Testers"],
                [
                    ticket3.uuid,
                    ticket3.opened_on,
                    ticket3.closed_on,
                    ticket3.topic.name,
                    ticket3.assignee.email,
                    ticket3.contact.uuid,
                    "Roy Kent",
                    "tel",
                    "+1234567890",
                    "41",
                    "Male",
                    True,
                ],
                [
                    ticket4.uuid,
                    ticket4.opened_on,
                    ticket4.closed_on,
                    ticket4.topic.name,
                    ticket4.assignee.email,
                    ticket4.contact.uuid,
                    "Sam Obisanya",
                    "twitter",
                    "nigerianprince",
                    "22",
                    "Male",
                    False,
                ],
            ],
            tz=self.org.timezone,
        )

        with self.anonymous(self.org):
            with self.mockReadOnly(assert_models={Ticket, ContactURN, ContactGroup, ContactField}):
                export = self._request_export(start_date=today - timedelta(days=90), end_date=today)
            self.assertExcelSheet(
                export[0],
                [
                    [
                        "UUID",
                        "Opened On",
                        "Closed On",
                        "Topic",
                        "Assigned To",
                        "Contact UUID",
                        "Contact Name",
                        "URN Scheme",
                        "Anon Value",
                    ],
                    [
                        ticket1.uuid,
                        ticket1.opened_on,
                        "",
                        ticket1.topic.name,
                        ticket1.assignee.email,
                        ticket1.contact.uuid,
                        "Nathan Shelley",
                        "",
                        ticket1.contact.anon_display,
                    ],
                    [
                        ticket2.uuid,
                        ticket2.opened_on,
                        "",
                        ticket2.topic.name,
                        ticket2.assignee.email,
                        ticket2.contact.uuid,
                        "Jamie Tartt",
                        "twitter",
                        ticket2.contact.anon_display,
                    ],
                    [
                        ticket3.uuid,
                        ticket3.opened_on,
                        ticket3.closed_on,
                        ticket3.topic.name,
                        ticket3.assignee.email,
                        ticket3.contact.uuid,
                        "Roy Kent",
                        "tel",
                        ticket3.contact.anon_display,
                    ],
                    [
                        ticket4.uuid,
                        ticket4.opened_on,
                        ticket4.closed_on,
                        ticket4.topic.name,
                        ticket4.assignee.email,
                        ticket4.contact.uuid,
                        "Sam Obisanya",
                        "twitter",
                        ticket4.contact.anon_display,
                    ],
                ],
                tz=self.org.timezone,
            )

        self.clear_storage()

    def test_export_with_too_many_fields_and_groups(self):
        export_url = reverse("tickets.ticket_export")
        today = timezone.now().astimezone(self.org.timezone).date()
        too_many_fields = [self.create_field(f"Field {i}", f"field{i}") for i in range(11)]
        too_many_groups = [self.create_group(f"Group {i}", contacts=[]) for i in range(11)]

        self.login(self.admin)
        response = self.client.post(
            export_url,
            {
                "start_date": today - timedelta(days=7),
                "end_date": today,
                "with_fields": [cf.id for cf in too_many_fields],
                "with_groups": [cg.id for cg in too_many_groups],
            },
        )
        self.assertFormError(response, "form", "with_fields", "You can only include up to 10 fields.")
        self.assertFormError(response, "form", "with_groups", "You can only include up to 10 groups.")

    def _request_export(self, start_date: date, end_date: date, with_fields=(), with_groups=()):
        export_url = reverse("tickets.ticket_export")
        self.client.post(
            export_url,
            {
                "start_date": start_date.isoformat(),
                "end_date": end_date.isoformat(),
                "with_fields": [cf.id for cf in with_fields],
                "with_groups": [cf.id for cf in with_groups],
            },
        )
        task = Export.objects.all().order_by("-id").first()
        filename = f"{settings.MEDIA_ROOT}/test_orgs/{self.org.id}/ticket_exports/{task.uuid}.xlsx"
        workbook = load_workbook(filename=filename)
        return workbook.worksheets


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

    def test_release(self):
        topic1 = Topic.create(self.org, self.admin, "Sales")
        flow = self.create_flow("Test")
        flow.topic_dependencies.add(topic1)

        topic1.release(self.admin)

        self.assertFalse(topic1.is_active)
        self.assertTrue(topic1.name.startswith("deleted-"))

        flow.refresh_from_db()
        self.assertTrue(flow.has_issues)

        # can't release default topic
        with self.assertRaises(AssertionError):
            self.org.default_ticket_topic.release(self.admin)


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
