from datetime import timedelta
from decimal import Decimal
from unittest.mock import call, patch
from uuid import UUID

from django.db.models import Value as DbValue
from django.db.models.functions import Concat, Substr
from django.urls import reverse
from django.utils import timezone

from temba import mailroom
from temba.campaigns.models import Campaign, CampaignEvent, EventFire
from temba.channels.models import ChannelEvent
from temba.contacts.models import URN, Contact, ContactField, ContactGroup, ContactURN
from temba.flows.models import Flow
from temba.locations.models import AdminBoundary
from temba.mailroom import modifiers
from temba.msgs.models import Msg, SystemLabel
from temba.orgs.models import Org
from temba.schedules.models import Schedule
from temba.tests import TembaTest, mock_mailroom
from temba.tests.engine import MockSessionWriter
from temba.tickets.models import Ticket


class ContactTest(TembaTest):
    def setUp(self):
        super().setUp()

        self.user1 = self.create_user("nash")

        self.joe = self.create_contact(name="Joe Blow", urns=["twitter:blow80", "tel:+250781111111"])
        self.frank = self.create_contact(name="Frank Smith", phone="+250782222222")
        self.billy = self.create_contact(name="Billy Nophone")
        self.voldemort = self.create_contact(phone="+250768383383")

        # create an orphaned URN
        ContactURN.objects.create(
            org=self.org, scheme="tel", path="+250788888888", identity="tel:+250788888888", priority=50
        )

        # create an deleted contact
        self.jim = self.create_contact(name="Jim")
        self.jim.release(self.user, deindex=False)

        # create contact in other org
        self.other_org_contact = self.create_contact(name="Fred", phone="+250768111222", org=self.org2)

    def test_contact_notes(self):
        note_text = "This is note"

        # create 10 notes
        for i in range(10):
            self.joe.set_note(self.user, f"{note_text} {i+1}")

        notes = self.joe.notes.all().order_by("id")

        # we should only have five notes after pruning
        self.assertEqual(5, notes.count())

        # check that the oldest notes are the ones that were pruned
        self.assertEqual("This is note 6", notes.first().text)

    @mock_mailroom
    def test_block_and_stop(self, mr_mocks):
        self.joe.block(self.admin)
        self.joe.stop(self.admin)
        self.joe.restore(self.admin)

        self.assertEqual(
            [
                call(self.org, self.admin, [self.joe], [modifiers.Status(status="blocked")]),
                call(self.org, self.admin, [self.joe], [modifiers.Status(status="stopped")]),
                call(self.org, self.admin, [self.joe], [modifiers.Status(status="active")]),
            ],
            mr_mocks.calls["contact_modify"],
        )

    @mock_mailroom
    def test_open_ticket(self, mock_contact_modify):
        mock_contact_modify.return_value = {self.joe.id: {"contact": {}, "events": []}}

        ticket = self.joe.open_ticket(
            self.admin, topic=self.org.default_ticket_topic, assignee=self.agent, note="Looks sus"
        )

        self.assertEqual(self.org.default_ticket_topic, ticket.topic)
        self.assertEqual("Looks sus", ticket.events.get(event_type="O").note)

    @mock_mailroom
    def test_interrupt(self, mr_mocks):
        # noop when contact not in a flow
        self.assertFalse(self.joe.interrupt(self.admin))

        flow = self.create_flow("Test")
        MockSessionWriter(self.joe, flow).wait().save()

        self.assertTrue(self.joe.interrupt(self.admin))

    @mock_mailroom
    def test_release(self, mr_mocks):
        # create a contact with a message
        old_contact = self.create_contact("Jose", phone="+12065552000")
        self.create_incoming_msg(old_contact, "hola mundo")
        urn = old_contact.get_urn()
        self.create_channel_event(self.channel, urn.identity, ChannelEvent.TYPE_CALL_IN_MISSED)

        self.create_ticket(old_contact)

        ivr_flow = self.get_flow("ivr")
        msg_flow = self.get_flow("favorites_v13")

        self.create_incoming_call(msg_flow, old_contact)

        # steal his urn into a new contact
        contact = self.create_contact("Joe", urns=["twitter:tweettweet"], fields={"gender": "Male", "age": 40})
        urn.contact = contact
        urn.save(update_fields=("contact",))
        group = self.create_group("Test Group", contacts=[contact])

        contact2 = self.create_contact("Billy", urns=["tel:1234567"])

        # create scheduled and regular broadcasts which send to both contacts
        schedule = Schedule.create(self.org, timezone.now(), Schedule.REPEAT_DAILY)
        bcast1 = self.create_broadcast(
            self.admin, {"eng": {"text": "Test"}}, contacts=[contact, contact2], schedule=schedule
        )
        bcast2 = self.create_broadcast(self.admin, {"eng": {"text": "Test"}}, contacts=[contact, contact2])

        flow_nodes = msg_flow.get_definition()["nodes"]
        color_prompt = flow_nodes[0]
        color_split = flow_nodes[2]
        beer_prompt = flow_nodes[3]
        beer_split = flow_nodes[5]
        name_prompt = flow_nodes[6]
        name_split = flow_nodes[7]

        (
            MockSessionWriter(contact, msg_flow)
            .visit(color_prompt)
            .send_msg("What is your favorite color?", self.channel)
            .visit(color_split)
            .wait()
            .resume(msg=self.create_incoming_msg(contact, "red"))
            .visit(beer_prompt)
            .send_msg("Good choice, I like Red too! What is your favorite beer?", self.channel)
            .visit(beer_split)
            .wait()
            .resume(msg=self.create_incoming_msg(contact, "primus"))
            .visit(name_prompt)
            .send_msg("Lastly, what is your name?", self.channel)
            .visit(name_split)
            .wait()
            .save()
        )

        campaign = Campaign.create(self.org, self.admin, "Reminders", group)
        joined = self.create_field("joined", "Joined On", value_type=ContactField.TYPE_DATETIME)
        event = CampaignEvent.create_message_event(self.org, self.admin, campaign, joined, 2, unit="D", message="Hi")
        EventFire.objects.create(event=event, contact=contact, scheduled=timezone.now() + timedelta(days=2))

        self.create_incoming_call(msg_flow, contact)

        # give contact an open and a closed ticket
        self.create_ticket(contact)
        self.create_ticket(contact, closed_on=timezone.now())

        self.assertEqual(1, group.contacts.all().count())
        self.assertEqual(1, contact.calls.all().count())
        self.assertEqual(2, contact.addressed_broadcasts.all().count())
        self.assertEqual(2, contact.urns.all().count())
        self.assertEqual(2, contact.runs.all().count())
        self.assertEqual(7, contact.msgs.all().count())
        self.assertEqual(2, len(contact.fields))
        self.assertEqual(1, contact.campaign_fires.count())

        self.assertEqual(2, Ticket.get_status_count(self.org, self.org.topics.all(), Ticket.STATUS_OPEN))
        self.assertEqual(1, Ticket.get_status_count(self.org, self.org.topics.all(), Ticket.STATUS_CLOSED))

        # first try releasing with _full_release patched so we can check the state of the contact before the task
        # to do a full release has kicked off
        with patch("temba.contacts.models.Contact._full_release"):
            contact.release(self.admin)

        self.assertEqual(2, contact.urns.all().count())
        for urn in contact.urns.all():
            UUID(urn.path, version=4)
            self.assertEqual(URN.DELETED_SCHEME, urn.scheme)

        # tickets unchanged
        self.assertEqual(2, contact.tickets.count())

        # a new contact arrives with those urns
        new_contact = self.create_contact("URN Thief", urns=["tel:+12065552000", "twitter:tweettweet"])
        self.assertEqual(2, new_contact.urns.all().count())

        self.assertEqual({contact2}, set(bcast1.contacts.all()))
        self.assertEqual({contact, contact2}, set(bcast2.contacts.all()))

        # now lets go for a full release
        contact.release(self.admin)

        contact.refresh_from_db()
        self.assertEqual(0, group.contacts.all().count())
        self.assertEqual(0, contact.calls.all().count())
        self.assertEqual(0, contact.addressed_broadcasts.all().count())
        self.assertEqual(0, contact.urns.all().count())
        self.assertEqual(0, contact.runs.all().count())
        self.assertEqual(0, contact.msgs.all().count())
        self.assertEqual(0, contact.campaign_fires.count())

        # tickets deleted (only for this contact)
        self.assertEqual(0, contact.tickets.count())
        self.assertEqual(1, Ticket.get_status_count(self.org, self.org.topics.all(), Ticket.STATUS_OPEN))
        self.assertEqual(0, Ticket.get_status_count(self.org, self.org.topics.all(), Ticket.STATUS_CLOSED))

        # contact who used to own our urn had theirs released too
        self.assertEqual(0, old_contact.calls.all().count())
        self.assertEqual(0, old_contact.msgs.all().count())

        self.assertIsNone(contact.fields)
        self.assertIsNone(contact.name)

        # nope, we aren't paranoid or anything
        Org.objects.get(id=self.org.id)
        Flow.objects.get(id=msg_flow.id)
        Flow.objects.get(id=ivr_flow.id)
        self.assertEqual(1, Ticket.objects.count())

    @mock_mailroom
    def test_status_changes_and_release(self, mr_mocks):
        flow = self.create_flow("Test")
        msg1 = self.create_incoming_msg(self.joe, "Test 1")
        msg2 = self.create_incoming_msg(self.joe, "Test 2", flow=flow)
        msg3 = self.create_incoming_msg(self.joe, "Test 3", visibility="A")
        label = self.create_label("Interesting")
        label.toggle_label([msg1, msg2, msg3], add=True)
        static_group = self.create_group("Just Joe", [self.joe])

        msg_counts = SystemLabel.get_counts(self.org)
        self.assertEqual(1, msg_counts[SystemLabel.TYPE_INBOX])
        self.assertEqual(1, msg_counts[SystemLabel.TYPE_FLOWS])
        self.assertEqual(1, msg_counts[SystemLabel.TYPE_ARCHIVED])

        self.assertEqual(
            Contact.get_status_counts(self.org),
            {
                Contact.STATUS_ACTIVE: 4,
                Contact.STATUS_BLOCKED: 0,
                Contact.STATUS_STOPPED: 0,
                Contact.STATUS_ARCHIVED: 0,
            },
        )

        self.assertEqual(set(label.msgs.all()), {msg1, msg2, msg3})
        self.assertEqual(set(static_group.contacts.all()), {self.joe})

        self.joe.stop(self.user)

        # check that joe is now stopped
        self.joe = Contact.objects.get(pk=self.joe.pk)
        self.assertEqual(Contact.STATUS_STOPPED, self.joe.status)
        self.assertTrue(self.joe.is_active)

        # and added to stopped group
        self.assertEqual(
            Contact.get_status_counts(self.org),
            {
                Contact.STATUS_ACTIVE: 3,
                Contact.STATUS_BLOCKED: 0,
                Contact.STATUS_STOPPED: 1,
                Contact.STATUS_ARCHIVED: 0,
            },
        )
        self.assertEqual(set(static_group.contacts.all()), set())

        self.joe.block(self.user)

        # check that joe is now blocked instead of stopped
        self.joe.refresh_from_db()
        self.assertEqual(Contact.STATUS_BLOCKED, self.joe.status)
        self.assertTrue(self.joe.is_active)

        # and that he's been removed from the all and failed groups, and added to the blocked group
        self.assertEqual(
            Contact.get_status_counts(self.org),
            {
                Contact.STATUS_ACTIVE: 3,
                Contact.STATUS_BLOCKED: 1,
                Contact.STATUS_STOPPED: 0,
                Contact.STATUS_ARCHIVED: 0,
            },
        )

        # and removed from all groups
        self.assertEqual(set(static_group.contacts.all()), set())

        # but his messages are unchanged
        self.assertEqual(2, Msg.objects.filter(contact=self.joe, visibility="V").count())
        msg_counts = SystemLabel.get_counts(self.org)
        self.assertEqual(1, msg_counts[SystemLabel.TYPE_INBOX])
        self.assertEqual(1, msg_counts[SystemLabel.TYPE_FLOWS])
        self.assertEqual(1, msg_counts[SystemLabel.TYPE_ARCHIVED])

        self.joe.archive(self.admin)

        # check that joe is now archived
        self.joe.refresh_from_db()
        self.assertEqual(Contact.STATUS_ARCHIVED, self.joe.status)
        self.assertTrue(self.joe.is_active)

        self.assertEqual(
            Contact.get_status_counts(self.org),
            {
                Contact.STATUS_ACTIVE: 3,
                Contact.STATUS_BLOCKED: 0,
                Contact.STATUS_STOPPED: 0,
                Contact.STATUS_ARCHIVED: 1,
            },
        )

        self.joe.restore(self.admin)

        # check that joe is now neither blocked or stopped
        self.joe = Contact.objects.get(pk=self.joe.pk)
        self.assertEqual(Contact.STATUS_ACTIVE, self.joe.status)
        self.assertTrue(self.joe.is_active)

        # and that he's been removed from the blocked group, and put back in the all and failed groups
        self.assertEqual(
            Contact.get_status_counts(self.org),
            {
                Contact.STATUS_ACTIVE: 4,
                Contact.STATUS_BLOCKED: 0,
                Contact.STATUS_STOPPED: 0,
                Contact.STATUS_ARCHIVED: 0,
            },
        )

        self.joe.release(self.user)

        # check that joe has been released (doesn't change his status)
        self.joe = Contact.objects.get(pk=self.joe.pk)
        self.assertEqual(Contact.STATUS_ACTIVE, self.joe.status)
        self.assertFalse(self.joe.is_active)

        self.assertEqual(
            Contact.get_status_counts(self.org),
            {
                Contact.STATUS_ACTIVE: 3,
                Contact.STATUS_BLOCKED: 0,
                Contact.STATUS_STOPPED: 0,
                Contact.STATUS_ARCHIVED: 0,
            },
        )

        # joe's messages should be inactive, blank and have no labels
        self.assertEqual(0, Msg.objects.filter(contact=self.joe, visibility="V").count())
        self.assertEqual(0, Msg.objects.filter(contact=self.joe).exclude(text="").count())
        self.assertEqual(0, label.msgs.count())

        msg_counts = SystemLabel.get_counts(self.org)
        self.assertEqual(0, msg_counts[SystemLabel.TYPE_INBOX])
        self.assertEqual(0, msg_counts[SystemLabel.TYPE_FLOWS])
        self.assertEqual(0, msg_counts[SystemLabel.TYPE_ARCHIVED])

        # and he shouldn't be in any groups
        self.assertEqual(set(static_group.contacts.all()), set())

        # or have any URNs
        self.assertEqual(0, ContactURN.objects.filter(contact=self.joe).count())

        # blocking and failing an inactive contact won't change groups
        self.joe.block(self.user)
        self.joe.stop(self.user)

        self.assertEqual(
            Contact.get_status_counts(self.org),
            {
                Contact.STATUS_ACTIVE: 3,
                Contact.STATUS_BLOCKED: 0,
                Contact.STATUS_STOPPED: 0,
                Contact.STATUS_ARCHIVED: 0,
            },
        )

        # we don't let users undo releasing a contact... but if we have to do it for some reason
        self.joe.is_active = True
        self.joe.save(update_fields=("is_active",))

        # check joe goes into the appropriate groups
        self.assertEqual(
            Contact.get_status_counts(self.org),
            {
                Contact.STATUS_ACTIVE: 3,
                Contact.STATUS_BLOCKED: 0,
                Contact.STATUS_STOPPED: 1,
                Contact.STATUS_ARCHIVED: 0,
            },
        )

    def test_contact_display(self):
        self.assertEqual("Joe Blow", self.joe.get_display(org=self.org, formatted=False))
        self.assertEqual("Joe Blow", self.joe.get_display())
        self.assertEqual("+250768383383", self.voldemort.get_display(org=self.org, formatted=False))
        self.assertEqual("0768 383 383", self.voldemort.get_display())
        self.assertEqual("Billy Nophone", self.billy.get_display())

        self.assertEqual("0781 111 111", self.joe.get_urn_display(scheme=URN.TEL_SCHEME))
        self.assertEqual("blow80", self.joe.get_urn_display(org=self.org, formatted=False))
        self.assertEqual("blow80", self.joe.get_urn_display())
        self.assertEqual("+250768383383", self.voldemort.get_urn_display(org=self.org, formatted=False))
        self.assertEqual(
            "+250768383383", self.voldemort.get_urn_display(org=self.org, formatted=False, international=True)
        )
        self.assertEqual("+250 768 383 383", self.voldemort.get_urn_display(org=self.org, international=True))
        self.assertEqual("0768 383 383", self.voldemort.get_urn_display())
        self.assertEqual("", self.billy.get_urn_display())

        self.assertEqual("Joe Blow", str(self.joe))
        self.assertEqual("0768 383 383", str(self.voldemort))
        self.assertEqual("Billy Nophone", str(self.billy))

        with self.anonymous(self.org):
            self.assertEqual("Joe Blow", self.joe.get_display(org=self.org, formatted=False))
            self.assertEqual("Joe Blow", self.joe.get_display())
            self.assertEqual("%010d" % self.voldemort.pk, self.voldemort.get_display())
            self.assertEqual("Billy Nophone", self.billy.get_display())

            self.assertEqual(ContactURN.ANON_MASK, self.joe.get_urn_display(org=self.org, formatted=False))
            self.assertEqual(ContactURN.ANON_MASK, self.joe.get_urn_display())
            self.assertEqual(ContactURN.ANON_MASK, self.voldemort.get_urn_display())
            self.assertEqual("", self.billy.get_urn_display())
            self.assertEqual("", self.billy.get_urn_display(scheme=URN.TEL_SCHEME))

            self.assertEqual("Joe Blow", str(self.joe))
            self.assertEqual("%010d" % self.voldemort.pk, str(self.voldemort))
            self.assertEqual("Billy Nophone", str(self.billy))

    def test_bulk_urn_cache_initialize(self):
        self.joe.refresh_from_db()
        self.billy.refresh_from_db()

        contacts = (self.joe, self.frank, self.billy)
        Contact.bulk_urn_cache_initialize(contacts)

        with self.assertNumQueries(0):
            self.assertEqual(["twitter:blow80", "tel:+250781111111"], [u.urn for u in self.joe.get_urns()])
            self.assertEqual(["twitter:blow80", "tel:+250781111111"], [u.urn for u in getattr(self.joe, "_urns_cache")])
            self.assertEqual(["tel:+250782222222"], [u.urn for u in self.frank.get_urns()])
            self.assertEqual([], [u.urn for u in self.billy.get_urns()])

    @mock_mailroom
    def test_bulk_inspect(self, mr_mocks):
        self.assertEqual({}, Contact.bulk_inspect([]))
        self.assertEqual(
            {
                self.joe: {
                    "urns": [
                        {
                            "channel": {"uuid": str(self.channel.uuid), "name": "Test Channel"},
                            "scheme": "tel",
                            "path": "+250781111111",
                            "display": "",
                        },
                        {"channel": None, "scheme": "twitter", "path": "blow80", "display": ""},
                    ]
                },
                self.billy: {"urns": []},
            },
            Contact.bulk_inspect([self.joe, self.billy]),
        )

    @mock_mailroom
    def test_omnibox(self, mr_mocks):
        omnibox_url = reverse("contacts.contact_omnibox")

        # add a group with members and an empty group
        self.create_field("gender", "Gender")
        open_tickets = self.org.groups.get(name="Open Tickets")
        joe_and_frank = self.create_group("Joe and Frank", [self.joe, self.frank])
        nobody = self.create_group("Nobody", [])

        men = self.create_group("Men", query="gender=M")
        ContactGroup.objects.filter(id=men.id).update(status=ContactGroup.STATUS_READY)

        # a group which is being re-evaluated and shouldn't appear in any omnibox results
        unready = self.create_group("Group being re-evaluated...", query="gender=M")
        unready.status = ContactGroup.STATUS_EVALUATING
        unready.save(update_fields=("status",))

        # Postgres will defer to strcoll for ordering which even for en_US.UTF-8 will return different results on OSX
        # and Ubuntu. To keep ordering consistent for this test, we don't let URNs start with +
        # (see http://postgresql.nabble.com/a-strange-order-by-behavior-td4513038.html)
        ContactURN.objects.filter(path__startswith="+").update(
            path=Substr("path", 2), identity=Concat(DbValue("tel:"), Substr("path", 2))
        )

        self.login(self.admin)

        def omnibox_request(query: str):
            response = self.client.get(omnibox_url + query)
            return response.json()["results"]

        # mock mailroom to return an error
        mr_mocks.exception(mailroom.QueryValidationException("ooh that doesn't look right", "syntax"))

        # error is swallowed and we show no results
        self.assertEqual([], omnibox_request("?search=-123`213"))

        # lookup specific contacts
        self.assertEqual(
            [
                {"id": str(self.billy.uuid), "name": "Billy Nophone", "type": "contact", "urn": ""},
                {"id": str(self.joe.uuid), "name": "Joe Blow", "type": "contact", "urn": "blow80"},
            ],
            omnibox_request(f"?c={self.joe.uuid},{self.billy.uuid}"),
        )

        # lookup specific groups
        self.assertEqual(
            [
                {"id": str(joe_and_frank.uuid), "name": "Joe and Frank", "type": "group", "count": 2},
                {"id": str(men.uuid), "name": "Men", "type": "group", "count": 0},
            ],
            omnibox_request(f"?g={joe_and_frank.uuid},{men.uuid}"),
        )

        # empty query just returns up to 25 groups A-Z
        with self.assertNumQueries(10):
            self.assertEqual(
                [
                    {"id": str(joe_and_frank.uuid), "name": "Joe and Frank", "type": "group", "count": 2},
                    {"id": str(men.uuid), "name": "Men", "type": "group", "count": 0},
                    {"id": str(nobody.uuid), "name": "Nobody", "type": "group", "count": 0},
                    {"id": str(open_tickets.uuid), "name": "Open Tickets", "type": "group", "count": 0},
                ],
                omnibox_request(""),
            )

        with self.assertNumQueries(13):
            mr_mocks.contact_search(query='name ~ "250" OR urn ~ "250"', total=2, contacts=[self.billy, self.frank])

            self.assertEqual(
                [
                    {"id": str(self.billy.uuid), "name": "Billy Nophone", "type": "contact", "urn": ""},
                    {"id": str(self.frank.uuid), "name": "Frank Smith", "type": "contact", "urn": "250782222222"},
                ],
                omnibox_request("?search=250"),
            )

        with self.assertNumQueries(14):
            mr_mocks.contact_search(query='name ~ "FRA" OR urn ~ "FRA"', total=1, contacts=[self.frank])

            self.assertEqual(
                [
                    {"id": str(joe_and_frank.uuid), "name": "Joe and Frank", "type": "group", "count": 2},
                    {"id": str(self.frank.uuid), "name": "Frank Smith", "type": "contact", "urn": "250782222222"},
                ],
                omnibox_request("?search=FRA"),
            )

        # specify type filter g (all groups)
        self.assertEqual(
            [
                {"id": str(joe_and_frank.uuid), "name": "Joe and Frank", "type": "group", "count": 2},
                {"id": str(men.uuid), "name": "Men", "type": "group", "count": 0},
                {"id": str(nobody.uuid), "name": "Nobody", "type": "group", "count": 0},
                {"id": str(open_tickets.uuid), "name": "Open Tickets", "type": "group", "count": 0},
            ],
            omnibox_request("?types=g"),
        )

        # specify type filter s (non-query groups)
        self.assertEqual(
            [
                {"id": str(joe_and_frank.uuid), "name": "Joe and Frank", "type": "group", "count": 2},
                {"id": str(nobody.uuid), "name": "Nobody", "type": "group", "count": 0},
            ],
            omnibox_request("?types=s"),
        )

        with self.anonymous(self.org):
            self.assertEqual(
                [
                    {"id": str(joe_and_frank.uuid), "name": "Joe and Frank", "type": "group", "count": 2},
                    {"id": str(men.uuid), "name": "Men", "type": "group", "count": 0},
                    {"id": str(nobody.uuid), "name": "Nobody", "type": "group", "count": 0},
                    {"id": str(open_tickets.uuid), "name": "Open Tickets", "type": "group", "count": 0},
                ],
                omnibox_request(""),
            )

            mr_mocks.contact_search(query='name ~ "Billy"', total=1, contacts=[self.billy])

            self.assertEqual(
                [
                    {"id": str(self.billy.uuid), "name": "Billy Nophone", "type": "contact"},
                ],
                omnibox_request("?search=Billy"),
            )

        # exclude blocked and stopped contacts
        self.joe.block(self.admin)
        self.frank.stop(self.admin)

        # lookup by contact uuids
        self.assertEqual(omnibox_request("?c=%s,%s" % (self.joe.uuid, self.frank.uuid)), [])

    def test_get_scheduled_messages(self):
        just_joe = self.create_group("Just Joe", [self.joe])

        self.assertEqual(0, self.joe.get_scheduled_broadcasts().count())

        broadcast = self.create_broadcast(self.admin, {"eng": {"text": "Hello"}}, contacts=[self.frank])
        self.assertEqual(0, self.joe.get_scheduled_broadcasts().count())

        broadcast.contacts.add(self.joe)

        self.assertEqual(0, self.joe.get_scheduled_broadcasts().count())

        schedule_time = timezone.now() + timedelta(days=2)
        broadcast.schedule = Schedule.create(self.org, schedule_time, Schedule.REPEAT_NEVER)
        broadcast.save(update_fields=("schedule",))

        self.assertEqual(self.joe.get_scheduled_broadcasts().count(), 1)
        self.assertIn(broadcast, self.joe.get_scheduled_broadcasts())

        broadcast.contacts.remove(self.joe)
        self.assertEqual(0, self.joe.get_scheduled_broadcasts().count())

        broadcast.groups.add(just_joe)
        self.assertEqual(self.joe.get_scheduled_broadcasts().count(), 1)
        self.assertIn(broadcast, self.joe.get_scheduled_broadcasts())

        broadcast.groups.remove(just_joe)
        self.assertEqual(0, self.joe.get_scheduled_broadcasts().count())

    @mock_mailroom
    def test_contacts_search(self, mr_mocks):
        search_url = reverse("contacts.contact_search")
        self.login(self.admin)

        mr_mocks.contact_search("Frank", cleaned='name ~ "Frank"', contacts=[self.frank])

        response = self.client.get(search_url + "?search=Frank")
        self.assertEqual(200, response.status_code)
        results = response.json()

        # check that we get a total and a sample
        self.assertEqual(1, results["total"])
        self.assertEqual(1, len(results["sample"]))
        self.assertEqual("+250 782 222 222", results["sample"][0]["primary_urn_formatted"])

        # our query should get expanded into a proper query
        self.assertEqual('name ~ "Frank"', results["query"])

        # check no primary urn
        self.frank.urns.all().delete()
        response = self.client.get(search_url + "?search=Frank")
        self.assertEqual(200, response.status_code)
        results = response.json()
        self.assertEqual("--", results["sample"][0]["primary_urn_formatted"])

        # no query, no results
        response = self.client.get(search_url)
        results = response.json()
        self.assertEqual(0, results["total"])

        mr_mocks.exception(mailroom.QueryValidationException("mismatched input at <EOF>", "syntax"))

        # bogus query
        response = self.client.get(search_url + '?search=name="notclosed')
        results = response.json()
        self.assertEqual("Invalid query syntax.", results["error"])
        self.assertEqual(0, results["total"])

        # if we query a field, it should show up in our field dict
        age = self.create_field("age", "Age", ContactField.TYPE_NUMBER)

        mr_mocks.contact_search("age>32", cleaned='age > 32"', contacts=[self.frank], fields=[age])

        response = self.client.get(search_url + "?search=age>32")
        results = response.json()
        self.assertEqual("Age", results["fields"][str(age.uuid)]["label"])

    @mock_mailroom
    def test_update_status(self, mr_mocks):
        self.login(self.admin)

        self.assertEqual(Contact.STATUS_ACTIVE, self.joe.status)

        for status, _ in Contact.STATUS_CHOICES:
            self.client.post(reverse("contacts.contact_update", args=[self.joe.id]), {"status": status})

            self.joe.refresh_from_db()
            self.assertEqual(status, self.joe.status)

    def test_update(self):
        # if new values don't differ from current values.. no modifications
        self.assertEqual([], self.joe.update(name="Joe Blow", language=""))

        # change language
        self.assertEqual([modifiers.Language(language="eng")], self.joe.update(name="Joe Blow", language="eng"))

        self.joe.language = "eng"
        self.joe.save(update_fields=("language",))

        # change name
        self.assertEqual([modifiers.Name(name="Joseph Blow")], self.joe.update(name="Joseph Blow", language="eng"))

        # change both name and language
        self.assertEqual(
            [modifiers.Name(name="Joseph Blower"), modifiers.Language(language="spa")],
            self.joe.update(name="Joseph Blower", language="spa"),
        )

    @mock_mailroom
    def test_update_static_groups(self, mr_mocks):
        # create some static groups
        spammers = self.create_group("Spammers", [])
        testers = self.create_group("Testers", [])
        customers = self.create_group("Customers", [])

        self.assertEqual(set(spammers.contacts.all()), set())
        self.assertEqual(set(testers.contacts.all()), set())
        self.assertEqual(set(customers.contacts.all()), set())

        # add to 2 static groups
        mods = self.joe.update_static_groups([spammers, testers])
        self.assertEqual(
            [
                modifiers.Groups(
                    modification="add",
                    groups=[
                        modifiers.GroupRef(uuid=spammers.uuid, name="Spammers"),
                        modifiers.GroupRef(uuid=testers.uuid, name="Testers"),
                    ],
                ),
            ],
            mods,
        )

        self.joe.modify(self.admin, mods)

        # remove from one and add to another
        mods = self.joe.update_static_groups([testers, customers])

        self.assertEqual(
            [
                modifiers.Groups(
                    modification="remove", groups=[modifiers.GroupRef(uuid=spammers.uuid, name="Spammers")]
                ),
                modifiers.Groups(
                    modification="add", groups=[modifiers.GroupRef(uuid=customers.uuid, name="Customers")]
                ),
            ],
            mods,
        )

    @mock_mailroom
    def test_bulk_modify_with_no_contacts(self, mr_mocks):
        Contact.bulk_modify(self.admin, [], [modifiers.Language(language="spa")])

        # just a NOOP
        self.assertEqual([], mr_mocks.calls["contact_modify"])

    @mock_mailroom
    def test_contact_model(self, mr_mocks):
        contact = self.create_contact(name="Boy", phone="12345")
        self.assertEqual(contact.get_display(), "Boy")

        contact3 = self.create_contact(name=None, phone="0788111222")
        self.channel.country = "RW"
        self.channel.save()

        normalized = contact3.get_urn(URN.TEL_SCHEME).ensure_number_normalization(self.channel)
        self.assertEqual(normalized.path, "+250788111222")

        contact4 = self.create_contact(name=None, phone="0788333444")
        normalized = contact4.get_urn(URN.TEL_SCHEME).ensure_number_normalization(self.channel)
        self.assertEqual(normalized.path, "+250788333444")

        contact5 = self.create_contact(name="Jimmy", phone="+250788333555")
        mods = contact5.update_urns(["twitter:jimmy_woot", "tel:0788333666"])
        contact5.modify(self.user, mods)

        # check old phone URN still existing but was detached
        self.assertIsNone(ContactURN.objects.get(identity="tel:+250788333555").contact)

        # check new URNs were created and attached
        self.assertEqual(contact5, ContactURN.objects.get(identity="tel:+250788333666").contact)
        self.assertEqual(contact5, ContactURN.objects.get(identity="twitter:jimmy_woot").contact)

        # check twitter URN takes priority if you don't specify scheme
        self.assertEqual("twitter:jimmy_woot", str(contact5.get_urn()))
        self.assertEqual("twitter:jimmy_woot", str(contact5.get_urn(schemes=[URN.TWITTER_SCHEME])))
        self.assertEqual("tel:+250788333666", str(contact5.get_urn(schemes=[URN.TEL_SCHEME])))
        self.assertIsNone(contact5.get_urn(schemes=["email"]))
        self.assertIsNone(contact5.get_urn(schemes=["facebook"]))

    def test_field_json(self):
        self.setUpLocations()

        # simple text field
        self.set_contact_field(self.joe, "dog", "Chef")
        self.joe.refresh_from_db()
        dog_uuid = str(ContactField.user_fields.get(key="dog").uuid)

        self.assertEqual(self.joe.fields, {dog_uuid: {"text": "Chef"}})

        self.set_contact_field(self.joe, "dog", "")
        self.joe.refresh_from_db()
        self.assertEqual(self.joe.fields, {})

        # numeric field value
        self.set_contact_field(self.joe, "dog", "23.00")
        self.joe.refresh_from_db()
        self.assertEqual(self.joe.fields, {dog_uuid: {"text": "23.00", "number": 23}})

        # numeric field value
        self.set_contact_field(self.joe, "dog", "37.27903")
        self.joe.refresh_from_db()
        self.assertEqual(self.joe.fields, {dog_uuid: {"text": "37.27903", "number": Decimal("37.27903")}})

        # numeric field values that could be NaN, we don't support that
        self.set_contact_field(self.joe, "dog", "NaN")
        self.joe.refresh_from_db()
        self.assertEqual(self.joe.fields, {dog_uuid: {"text": "NaN"}})

        # datetime instead
        self.set_contact_field(self.joe, "dog", "2018-03-05T02:31:00.000Z")
        self.joe.refresh_from_db()
        self.assertEqual(
            self.joe.fields, {dog_uuid: {"text": "2018-03-05T02:31:00.000Z", "datetime": "2018-03-05T04:31:00+02:00"}}
        )

        # setting another field doesn't ruin anything
        self.set_contact_field(self.joe, "cat", "Rando")
        self.joe.refresh_from_db()
        cat_uuid = str(ContactField.user_fields.get(key="cat").uuid)
        self.assertEqual(
            self.joe.fields,
            {
                dog_uuid: {"text": "2018-03-05T02:31:00.000Z", "datetime": "2018-03-05T04:31:00+02:00"},
                cat_uuid: {"text": "Rando"},
            },
        )

        # setting a fully qualified path parses to that level, regardless of field type
        self.set_contact_field(self.joe, "cat", "Rwanda > Kigali City")
        self.joe.refresh_from_db()
        self.assertEqual(
            self.joe.fields,
            {
                dog_uuid: {"text": "2018-03-05T02:31:00.000Z", "datetime": "2018-03-05T04:31:00+02:00"},
                cat_uuid: {"text": "Rwanda > Kigali City", "state": "Rwanda > Kigali City"},
            },
        )

        # clear our previous fields
        self.set_contact_field(self.joe, "dog", "")
        self.assertEqual(self.joe.fields, {cat_uuid: {"text": "Rwanda > Kigali City", "state": "Rwanda > Kigali City"}})
        self.joe.refresh_from_db()

        self.set_contact_field(self.joe, "cat", "")
        self.joe.refresh_from_db()

        # change a field to an invalid field value type
        self.set_contact_field(self.joe, "cat", "xx")
        ContactField.user_fields.filter(key="cat").update(value_type="Z")
        bad_field = ContactField.user_fields.get(key="cat")

        with self.assertRaises(KeyError):
            self.joe.get_field_serialized(bad_field)

        with self.assertRaises(KeyError):
            self.joe.get_field_value(bad_field)

    def test_field_values(self):
        self.setUpLocations()

        registration_field = self.create_field(
            "registration_date", "Registration Date", value_type=ContactField.TYPE_DATETIME
        )
        weight_field = self.create_field("weight", "Weight", value_type=ContactField.TYPE_NUMBER)
        color_field = self.create_field("color", "Color", value_type=ContactField.TYPE_TEXT)
        state_field = self.create_field("state", "State", value_type=ContactField.TYPE_STATE)

        # none value instances
        self.assertEqual(self.joe.get_field_serialized(weight_field), None)
        self.assertEqual(self.joe.get_field_display(weight_field), "")
        self.assertEqual(self.joe.get_field_serialized(registration_field), None)
        self.assertEqual(self.joe.get_field_display(registration_field), "")

        self.set_contact_field(self.joe, "registration_date", "2014-12-31T01:04:00Z")
        self.set_contact_field(self.joe, "weight", "75.888888")
        self.set_contact_field(self.joe, "color", "green")
        self.set_contact_field(self.joe, "state", "kigali city")

        self.assertEqual(self.joe.get_field_serialized(registration_field), "2014-12-31T03:04:00+02:00")

        self.assertEqual(self.joe.get_field_serialized(weight_field), "75.888888")
        self.assertEqual(self.joe.get_field_display(weight_field), "75.888888")

        self.set_contact_field(self.joe, "weight", "0")
        self.assertEqual(self.joe.get_field_serialized(weight_field), "0")
        self.assertEqual(self.joe.get_field_display(weight_field), "0")

        # passing something non-numeric to a decimal field
        self.set_contact_field(self.joe, "weight", "xxx")
        self.assertEqual(self.joe.get_field_serialized(weight_field), None)
        self.assertEqual(self.joe.get_field_display(weight_field), "")

        self.assertEqual(self.joe.get_field_serialized(state_field), "Rwanda > Kigali City")
        self.assertEqual(self.joe.get_field_display(state_field), "Kigali City")

        self.assertEqual(self.joe.get_field_serialized(color_field), "green")
        self.assertEqual(self.joe.get_field_display(color_field), "green")

        # can fetch proxy fields too
        created_on = self.org.fields.get(key="created_on")
        last_seen_on = self.org.fields.get(key="last_seen_on")

        self.assertEqual(self.joe.get_field_display(created_on), self.org.format_datetime(self.joe.created_on))
        self.assertEqual(self.joe.get_field_display(last_seen_on), "")

    def test_set_location_fields(self):
        self.setUpLocations()

        district_field = self.create_field("district", "District", value_type=ContactField.TYPE_DISTRICT)
        not_state_field = self.create_field("not_state", "Not State", value_type=ContactField.TYPE_TEXT)

        # add duplicate district in different states
        east_province = AdminBoundary.create(osm_id="R005", name="East Province", level=1, parent=self.country)
        AdminBoundary.create(osm_id="R004", name="Remera", level=2, parent=east_province)
        kigali = AdminBoundary.objects.get(name="Kigali City")
        AdminBoundary.create(osm_id="R003", name="Remera", level=2, parent=kigali)

        joe = Contact.objects.get(pk=self.joe.pk)
        self.set_contact_field(joe, "district", "Remera")

        # empty because it is ambiguous
        self.assertFalse(joe.get_field_value(district_field))

        state_field = self.create_field("state", "State", value_type=ContactField.TYPE_STATE)

        self.set_contact_field(joe, "state", "Kigali city")
        self.assertEqual("Kigali City", joe.get_field_display(state_field))
        self.assertEqual("Rwanda > Kigali City", joe.get_field_serialized(state_field))

        # test that we don't normalize non-location fields
        self.set_contact_field(joe, "not_state", "kigali city")
        self.assertEqual("kigali city", joe.get_field_display(not_state_field))
        self.assertEqual("kigali city", joe.get_field_serialized(not_state_field))

        self.set_contact_field(joe, "district", "Remera")
        self.assertEqual("Remera", joe.get_field_display(district_field))
        self.assertEqual("Rwanda > Kigali City > Remera", joe.get_field_serialized(district_field))

    def test_set_location_ward_fields(self):
        self.setUpLocations()

        state = AdminBoundary.create(osm_id="3710302", name="Kano", level=1, parent=self.country)
        district = AdminBoundary.create(osm_id="3710307", name="Bichi", level=2, parent=state)
        AdminBoundary.create(osm_id="3710377", name="Bichi", level=3, parent=district)

        self.create_field("state", "State", value_type=ContactField.TYPE_STATE)
        self.create_field("district", "District", value_type=ContactField.TYPE_DISTRICT)
        ward = self.create_field("ward", "Ward", value_type=ContactField.TYPE_WARD)

        jemila = self.create_contact(
            name="Jemila Alley",
            urns=["tel:123", "twitter:fulani_p"],
            fields={"state": "kano", "district": "bichi", "ward": "bichi"},
        )
        self.assertEqual(jemila.get_field_serialized(ward), "Rwanda > Kano > Bichi > Bichi")
