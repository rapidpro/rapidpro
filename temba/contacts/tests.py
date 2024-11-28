import io
import tempfile
from datetime import date, datetime, timedelta, timezone as tzone
from decimal import Decimal
from unittest.mock import call, patch
from uuid import UUID
from zoneinfo import ZoneInfo

import iso8601
from openpyxl import load_workbook

from django.core.files.storage import default_storage
from django.core.validators import ValidationError
from django.db.models import Value as DbValue
from django.db.models.functions import Concat, Substr
from django.db.utils import IntegrityError
from django.urls import reverse
from django.utils import timezone

from temba import mailroom
from temba.airtime.models import AirtimeTransfer
from temba.campaigns.models import Campaign, CampaignEvent, EventFire
from temba.channels.models import ChannelEvent
from temba.flows.models import Flow, FlowSession
from temba.ivr.models import Call
from temba.locations.models import AdminBoundary
from temba.mailroom import modifiers
from temba.msgs.models import Msg, SystemLabel
from temba.orgs.models import Export, Org
from temba.schedules.models import Schedule
from temba.tests import CRUDLTestMixin, MigrationTest, TembaTest, matchers, mock_mailroom
from temba.tests.engine import MockSessionWriter
from temba.tickets.models import Ticket, Topic
from temba.utils import json, s3
from temba.utils.dates import datetime_to_timestamp

from .models import (
    URN,
    Contact,
    ContactExport,
    ContactField,
    ContactGroup,
    ContactGroupCount,
    ContactImport,
    ContactImportBatch,
    ContactURN,
)
from .tasks import squash_group_counts
from .templatetags.contacts import msg_status_badge


class ContactGroupTest(TembaTest):
    def setUp(self):
        super().setUp()

        self.joe = self.create_contact("Joe Blow", phone="123", fields={"age": "17", "gender": "male"})
        self.frank = self.create_contact("Frank Smith", phone="1234")
        self.mary = self.create_contact("Mary Mo", phone="345", fields={"age": "21", "gender": "female"})

    def test_create_manual(self):
        group = ContactGroup.create_manual(self.org, self.admin, "group one")

        self.assertEqual(group.org, self.org)
        self.assertEqual(group.name, "group one")
        self.assertEqual(group.created_by, self.admin)
        self.assertEqual(group.status, ContactGroup.STATUS_READY)

        # can't call update_query on a manual group
        self.assertRaises(AssertionError, group.update_query, "gender=M")

        # assert failure if group name is blank
        self.assertRaises(AssertionError, ContactGroup.create_manual, self.org, self.admin, "   ")

    @mock_mailroom
    def test_create_smart(self, mr_mocks):
        age = self.org.fields.get(key="age")
        gender = self.org.fields.get(key="gender")

        # create a dynamic group using a query
        query = '(Age < 18 and gender = "male") or (Age > 18 and gender = "female")'

        group = ContactGroup.create_smart(self.org, self.admin, "Group two", query)
        group.refresh_from_db()

        self.assertEqual(query, group.query)
        self.assertEqual({age, gender}, set(group.query_fields.all()))
        self.assertEqual(ContactGroup.STATUS_INITIALIZING, group.status)

        # update group query
        mr_mocks.contact_parse_query("age > 18 and name ~ Mary", cleaned='age > 18 AND name ~ "Mary"')
        group.update_query("age > 18 and name ~ Mary")
        group.refresh_from_db()

        self.assertEqual(group.query, 'age > 18 AND name ~ "Mary"')
        self.assertEqual(set(group.query_fields.all()), {age})
        self.assertEqual(group.status, ContactGroup.STATUS_INITIALIZING)

        # try to update group query to something invalid
        mr_mocks.exception(mailroom.QueryValidationException("no valid", "syntax"))
        with self.assertRaises(ValueError):
            group.update_query("age ~ Mary")

        # can't create a dynamic group with empty query
        self.assertRaises(AssertionError, ContactGroup.create_smart, self.org, self.admin, "Empty", "")

        # can't create a dynamic group with id attribute
        self.assertRaises(ValueError, ContactGroup.create_smart, self.org, self.admin, "Bose", "id = 123")

        # dynamic group should not have remove to group button
        self.login(self.admin)
        filter_url = reverse("contacts.contact_group", args=[group.uuid])
        self.client.get(filter_url)

        # put group back into evaluation state
        group.status = ContactGroup.STATUS_EVALUATING
        group.save(update_fields=("status",))

        # dynamic groups should get their own icon
        self.assertEqual(group.get_attrs(), {"icon": "group_smart"})

        # can't update query again while it is in this state
        with self.assertRaises(AssertionError):
            group.update_query("age = 18")

    def test_get_or_create(self):
        group = ContactGroup.get_or_create(self.org, self.user, "first")
        self.assertEqual(group.name, "first")
        self.assertFalse(group.is_smart)

        # name look up is case insensitive
        self.assertEqual(ContactGroup.get_or_create(self.org, self.user, "FIRST"), group)

        # fetching by id shouldn't modify original group
        self.assertEqual(ContactGroup.get_or_create(self.org, self.user, "Kigali", uuid=group.uuid), group)

        group.refresh_from_db()
        self.assertEqual(group.name, "first")

    @mock_mailroom
    def test_get_groups(self, mr_mocks):
        manual = ContactGroup.create_manual(self.org, self.admin, "Static")
        deleted = ContactGroup.create_manual(self.org, self.admin, "Deleted")
        deleted.is_active = False
        deleted.save()

        open_tickets = self.org.groups.get(name="Open Tickets")
        females = ContactGroup.create_smart(self.org, self.admin, "Females", "gender=F")
        males = ContactGroup.create_smart(self.org, self.admin, "Males", "gender=M")
        ContactGroup.objects.filter(id=males.id).update(status=ContactGroup.STATUS_READY)

        self.assertEqual(set(ContactGroup.get_groups(self.org)), {open_tickets, manual, females, males})
        self.assertEqual(set(ContactGroup.get_groups(self.org, manual_only=True)), {manual})
        self.assertEqual(set(ContactGroup.get_groups(self.org, ready_only=True)), {open_tickets, manual, males})

    def test_get_unique_name(self):
        self.assertEqual("Testers", ContactGroup.get_unique_name(self.org, "Testers"))

        # ensure checking against existing groups is case-insensitive
        self.create_group("TESTERS", contacts=[])

        self.assertEqual("Testers 2", ContactGroup.get_unique_name(self.org, "Testers"))
        self.assertEqual("Testers", ContactGroup.get_unique_name(self.org2, "Testers"))  # different org

        self.create_group("Testers 2", contacts=[])

        self.assertEqual("Testers 3", ContactGroup.get_unique_name(self.org, "Testers"))

        # ensure we don't exceed the name length limit
        self.create_group("X" * 64, contacts=[])

        self.assertEqual(f"{'X' * 62} 2", ContactGroup.get_unique_name(self.org, "X" * 64))

    @mock_mailroom
    def test_member_count(self, mr_mocks):
        group = self.create_group("Cool kids")
        group.contacts.add(self.joe, self.frank)

        self.assertEqual(ContactGroup.objects.get(pk=group.pk).get_member_count(), 2)

        group.contacts.add(self.mary)

        self.assertEqual(ContactGroup.objects.get(pk=group.pk).get_member_count(), 3)

        group.contacts.remove(self.mary)

        self.assertEqual(ContactGroup.objects.get(pk=group.pk).get_member_count(), 2)

        # blocking a contact removes them from all user groups
        self.joe.block(self.user)

        group = ContactGroup.objects.get(pk=group.pk)
        self.assertEqual(group.get_member_count(), 1)
        self.assertEqual(set(group.contacts.all()), {self.frank})

        # releasing removes from all user groups
        self.frank.release(self.user)

        group = ContactGroup.objects.get(pk=group.pk)
        self.assertEqual(group.get_member_count(), 0)
        self.assertEqual(set(group.contacts.all()), set())

    @mock_mailroom
    def test_status_group_counts(self, mr_mocks):
        # start with no contacts
        for contact in Contact.objects.all():
            contact.release(self.admin)
            contact.delete()

        counts = Contact.get_status_counts(self.org)
        self.assertEqual(
            counts,
            {
                Contact.STATUS_ACTIVE: 0,
                Contact.STATUS_BLOCKED: 0,
                Contact.STATUS_STOPPED: 0,
                Contact.STATUS_ARCHIVED: 0,
            },
        )

        self.create_contact("Hannibal", phone="0783835001")
        face = self.create_contact("Face", phone="0783835002")
        ba = self.create_contact("B.A.", phone="0783835003")
        murdock = self.create_contact("Murdock", phone="0783835004")

        counts = Contact.get_status_counts(self.org)
        self.assertEqual(
            counts,
            {
                Contact.STATUS_ACTIVE: 4,
                Contact.STATUS_BLOCKED: 0,
                Contact.STATUS_STOPPED: 0,
                Contact.STATUS_ARCHIVED: 0,
            },
        )

        # call methods twice to check counts don't change twice
        murdock.block(self.user)
        murdock.block(self.user)
        face.block(self.user)
        ba.stop(self.user)
        ba.stop(self.user)

        counts = Contact.get_status_counts(self.org)
        self.assertEqual(
            counts,
            {
                Contact.STATUS_ACTIVE: 1,
                Contact.STATUS_BLOCKED: 2,
                Contact.STATUS_STOPPED: 1,
                Contact.STATUS_ARCHIVED: 0,
            },
        )

        murdock.release(self.user)
        murdock.release(self.user)
        face.restore(self.user)
        face.restore(self.user)
        ba.restore(self.user)
        ba.restore(self.user)

        # squash all our counts, this shouldn't affect our overall counts, but we should now only have 3
        squash_group_counts()
        self.assertEqual(ContactGroupCount.objects.all().count(), 3)

        counts = Contact.get_status_counts(self.org)
        self.assertEqual(
            counts,
            {
                Contact.STATUS_ACTIVE: 3,
                Contact.STATUS_BLOCKED: 0,
                Contact.STATUS_STOPPED: 0,
                Contact.STATUS_ARCHIVED: 0,
            },
        )

        # rebuild just our system contact group
        all_contacts = self.org.active_contacts_group
        ContactGroupCount.populate_for_group(all_contacts)

        # assert our count is correct
        self.assertEqual(all_contacts.get_member_count(), 3)
        self.assertEqual(ContactGroupCount.objects.filter(group=all_contacts).count(), 1)

    @mock_mailroom
    def test_release(self, mr_mocks):
        contact1 = self.create_contact("Bob", phone="+1234567111")
        contact2 = self.create_contact("Jim", phone="+1234567222")
        contact3 = self.create_contact("Jim", phone="+1234567333")
        group1 = self.create_group("Group One", contacts=[contact1, contact2])
        group2 = self.create_group("Group One", contacts=[contact2, contact3])

        t1 = timezone.now()

        # create a campaign based on group 1 - a hard dependency
        campaign = Campaign.create(self.org, self.admin, "Reminders", group1)
        joined = self.create_field("joined", "Joined On", value_type=ContactField.TYPE_DATETIME)
        event = CampaignEvent.create_message_event(self.org, self.admin, campaign, joined, 2, unit="D", message="Hi")
        EventFire.objects.create(event=event, contact=self.joe, scheduled=timezone.now() + timedelta(days=2))
        campaign.is_archived = True
        campaign.save()

        # create scheduled and regular broadcasts which send to both groups
        schedule = Schedule.create(self.org, timezone.now(), Schedule.REPEAT_DAILY)
        bcast1 = self.create_broadcast(self.admin, {"eng": {"text": "Hi"}}, groups=[group1, group2], schedule=schedule)
        bcast2 = self.create_broadcast(self.admin, {"eng": {"text": "Hi"}}, groups=[group1, group2])

        # group still has a hard dependency so can't be released
        with self.assertRaises(AssertionError):
            group1.release(self.admin)

        campaign.delete()

        group1.release(self.admin)
        group1.refresh_from_db()

        self.assertFalse(group1.is_active)
        self.assertTrue(group1.name.startswith("deleted-"))
        self.assertEqual(0, EventFire.objects.count())  # event fires will have been deleted
        self.assertEqual({group2}, set(bcast1.groups.all()))  # removed from scheduled broadcast
        self.assertEqual({group1, group2}, set(bcast2.groups.all()))  # regular broadcast unchanged

        self.assertEqual(set(), set(group1.contacts.all()))
        self.assertEqual({contact2, contact3}, set(group2.contacts.all()))  # unchanged

        # check that contacts who were in the group have had their modified_on times updated
        contact1.refresh_from_db()
        contact2.refresh_from_db()
        contact3.refresh_from_db()
        self.assertGreater(contact1.modified_on, t1)
        self.assertGreater(contact2.modified_on, t1)
        self.assertLess(contact3.modified_on, t1)  # unchanged


class ContactTest(TembaTest, CRUDLTestMixin):
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

    def create_campaign(self):
        # create a campaign with a future event and add joe
        self.farmers = self.create_group("Farmers", [self.joe])
        self.reminder_flow = self.create_flow("Reminder Flow")
        self.planting_date = self.create_field("planting_date", "Planting Date", value_type=ContactField.TYPE_DATETIME)
        self.campaign = Campaign.create(self.org, self.admin, "Planting Reminders", self.farmers)

        # create af flow event
        self.planting_reminder = CampaignEvent.create_flow_event(
            self.org,
            self.admin,
            self.campaign,
            relative_to=self.planting_date,
            offset=0,
            unit="D",
            flow=self.reminder_flow,
            delivery_hour=17,
        )

        # and a message event
        self.message_event = CampaignEvent.create_message_event(
            self.org,
            self.admin,
            self.campaign,
            relative_to=self.planting_date,
            offset=7,
            unit="D",
            message="Sent 7 days after planting date",
        )

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

    def test_history(self):
        url = reverse("contacts.contact_history", args=[self.joe.uuid])

        kurt = self.create_contact("Kurt", phone="123123")
        self.joe.created_on = timezone.now() - timedelta(days=1000)
        self.joe.save(update_fields=("created_on",))

        self.create_broadcast(self.user, {"eng": {"text": "A beautiful broadcast"}}, contacts=[self.joe])
        self.create_campaign()

        # add a message with some attachments
        self.create_incoming_msg(
            self.joe,
            "Message caption",
            created_on=timezone.now(),
            attachments=[
                "audio/mp3:http://blah/file.mp3",
                "video/mp4:http://blah/file.mp4",
                "geo:47.5414799,-122.6359908",
            ],
        )

        # create some messages
        for i in range(94):
            self.create_incoming_msg(
                self.joe, "Inbound message %d" % i, created_on=timezone.now() - timedelta(days=(100 - i))
            )

        # because messages are stored with timestamps from external systems, possible to have initial message
        # which is little bit older than the contact itself
        self.create_incoming_msg(
            self.joe, "Very old inbound message", created_on=self.joe.created_on - timedelta(seconds=10)
        )

        flow = self.get_flow("color_v13")
        nodes = flow.get_definition()["nodes"]
        color_prompt = nodes[0]
        color_split = nodes[4]

        (
            MockSessionWriter(self.joe, flow)
            .visit(color_prompt)
            .send_msg("What is your favorite color?", self.channel)
            .call_webhook("POST", "https://example.com/", "1234")  # pretend that flow run made a webhook request
            .visit(color_split)
            .set_result("Color", "green", "Green", "I like green")
            .wait()
            .save()
        )
        (
            MockSessionWriter(kurt, flow)
            .visit(color_prompt)
            .send_msg("What is your favorite color?", self.channel)
            .visit(color_split)
            .wait()
            .save()
        )

        # mark an outgoing message as failed
        failed = Msg.objects.filter(direction="O", contact=self.joe).last()
        failed.status = "F"
        failed.save(update_fields=("status",))

        # create an airtime transfer
        AirtimeTransfer.objects.create(
            org=self.org,
            status="S",
            contact=self.joe,
            currency="RWF",
            desired_amount=Decimal("100"),
            actual_amount=Decimal("100"),
        )

        # create an event from the past
        scheduled = timezone.now() - timedelta(days=5)
        EventFire.objects.create(event=self.planting_reminder, contact=self.joe, scheduled=scheduled, fired=scheduled)

        # two tickets for joe
        sales = Topic.create(self.org, self.admin, "Sales")
        self.create_ticket(self.joe, opened_on=timezone.now(), closed_on=timezone.now())
        ticket = self.create_ticket(self.joe, topic=sales)

        # create missed incoming and outgoing calls
        self.create_channel_event(
            self.channel, str(self.joe.get_urn(URN.TEL_SCHEME)), ChannelEvent.TYPE_CALL_OUT_MISSED, extra={}
        )
        self.create_channel_event(
            self.channel, str(self.joe.get_urn(URN.TEL_SCHEME)), ChannelEvent.TYPE_CALL_IN_MISSED, extra={}
        )

        # and a referral event
        self.create_channel_event(
            self.channel, str(self.joe.get_urn(URN.TEL_SCHEME)), ChannelEvent.TYPE_NEW_CONVERSATION, extra={}
        )

        # add a failed call
        Call.objects.create(
            contact=self.joe,
            status=Call.STATUS_ERRORED,
            error_reason=Call.ERROR_NOANSWER,
            channel=self.channel,
            org=self.org,
            contact_urn=self.joe.urns.all().first(),
            error_count=0,
        )

        # add a note to our open ticket
        ticket.events.create(
            org=self.org,
            contact=ticket.contact,
            event_type="N",
            note="I have a bad feeling about this",
            created_by=self.admin,
        )

        # create an assignment
        ticket.events.create(
            org=self.org,
            contact=ticket.contact,
            event_type="A",
            created_by=self.admin,
            assignee=self.admin,
        )

        # set an output URL on our session so we fetch from there
        s = FlowSession.objects.get(contact=self.joe)
        s3.client().put_object(
            Bucket="test-sessions", Key="c/session.json", Body=io.BytesIO(json.dumps(s.output).encode())
        )
        FlowSession.objects.filter(id=s.id).update(output_url="http://minio:9000/test-sessions/c/session.json")

        # fetch our contact history
        self.login(self.admin)
        with self.assertNumQueries(25):
            response = self.client.get(url + "?limit=100")

        # history should include all messages in the last 90 days, the channel event, the call, and the flow run
        history = response.json()["events"]
        self.assertEqual(96, len(history))

        def assertHistoryEvent(events, index, expected_type, **kwargs):
            item = events[index]
            self.assertEqual(expected_type, item["type"], f"event type mismatch for item {index}")
            self.assertTrue(iso8601.parse_date(item["created_on"]))  # check created_on exists and is ISO string

            for path, expected in kwargs.items():
                self.assertPathValue(item, path, expected, f"item {index}")

        assertHistoryEvent(history, 0, "call_started", status="E", status_display="Errored (No Answer)")
        assertHistoryEvent(history, 1, "channel_event", channel_event_type="new_conversation")
        assertHistoryEvent(history, 2, "channel_event", channel_event_type="mo_miss")
        assertHistoryEvent(history, 3, "channel_event", channel_event_type="mt_miss")
        assertHistoryEvent(history, 4, "ticket_opened", ticket__topic__name="Sales")
        assertHistoryEvent(history, 5, "ticket_closed", ticket__topic__name="General")
        assertHistoryEvent(history, 6, "ticket_opened", ticket__topic__name="General")
        assertHistoryEvent(history, 7, "airtime_transferred", actual_amount="100.00")
        assertHistoryEvent(history, 8, "msg_created", msg__text="What is your favorite color?")
        assertHistoryEvent(history, 9, "flow_entered", flow__name="Colors")
        assertHistoryEvent(history, 10, "msg_received", msg__text="Message caption")
        assertHistoryEvent(
            history, 11, "msg_created", msg__text="A beautiful broadcast", created_by__email="viewer@textit.com"
        )
        assertHistoryEvent(history, 12, "campaign_fired", campaign__name="Planting Reminders")
        assertHistoryEvent(history, -1, "msg_received", msg__text="Inbound message 11")

        # revert back to reading only from DB
        FlowSession.objects.filter(id=s.id).update(output_url=None)

        # can filter by ticket to only all ticket events from that ticket rather than some events from all tickets
        response = self.client.get(url + f"?ticket={ticket.uuid}&limit=100")
        history = response.json()["events"]
        assertHistoryEvent(history, 0, "ticket_assigned", assignee__id=self.admin.id)
        assertHistoryEvent(history, 1, "ticket_note_added", note="I have a bad feeling about this")
        assertHistoryEvent(history, 5, "channel_event", channel_event_type="mt_miss")
        assertHistoryEvent(history, 6, "ticket_opened", ticket__topic__name="Sales")
        assertHistoryEvent(history, 7, "airtime_transferred", actual_amount="100.00")

        # fetch next page
        before = datetime_to_timestamp(timezone.now() - timedelta(days=90))
        response = self.requestView(url + "?limit=100&before=%d" % before, self.admin)
        self.assertFalse(response.json()["has_older"])

        # activity should include 11 remaining messages and the event fire
        history = response.json()["events"]
        self.assertEqual(12, len(history))
        assertHistoryEvent(history, 0, "msg_received", msg__text="Inbound message 10")
        assertHistoryEvent(history, 10, "msg_received", msg__text="Inbound message 0")
        assertHistoryEvent(history, 11, "msg_received", msg__text="Very old inbound message")

        response = self.requestView(url + "?limit=100", self.admin)
        history = response.json()["events"]

        self.assertEqual(96, len(history))
        assertHistoryEvent(history, 8, "msg_created", msg__text="What is your favorite color?")

        # if a new message comes in
        self.create_incoming_msg(self.joe, "Newer message")
        response = self.requestView(url, self.admin)

        # now we'll see the message that just came in first, followed by the call event
        history = response.json()["events"]
        assertHistoryEvent(history, 0, "msg_received", msg__text="Newer message")
        assertHistoryEvent(history, 1, "call_started", status="E", status_display="Errored (No Answer)")

        recent_start = datetime_to_timestamp(timezone.now() - timedelta(days=1))
        response = self.requestView(url + "?limit=100&after=%s" % recent_start, self.admin)

        # with our recent flag on, should not see the older messages
        events = response.json()["events"]
        self.assertEqual(13, len(events))
        self.assertContains(response, "file.mp4")

        # add a new run
        (
            MockSessionWriter(self.joe, flow)
            .visit(color_prompt)
            .send_msg("What is your favorite color?", self.channel)
            .visit(color_split)
            .wait()
            .save()
        )

        response = self.requestView(url + "?limit=200", self.admin)
        history = response.json()["events"]
        self.assertEqual(100, len(history))

        # before date should not match our last activity, that only happens when we truncate
        resp_json = response.json()
        self.assertNotEqual(
            resp_json["next_before"],
            datetime_to_timestamp(iso8601.parse_date(resp_json["events"][-1]["created_on"])),
        )

        assertHistoryEvent(history, 0, "msg_created", msg__text="What is your favorite color?")
        assertHistoryEvent(history, 1, "flow_entered")
        assertHistoryEvent(history, 2, "flow_exited")
        assertHistoryEvent(history, 3, "msg_received", msg__text="Newer message")
        assertHistoryEvent(history, 4, "call_started")
        assertHistoryEvent(history, 5, "channel_event")
        assertHistoryEvent(history, 6, "channel_event")
        assertHistoryEvent(history, 7, "channel_event")
        assertHistoryEvent(history, 8, "ticket_opened")
        assertHistoryEvent(history, 9, "ticket_closed")
        assertHistoryEvent(history, 10, "ticket_opened")
        assertHistoryEvent(history, 11, "airtime_transferred")
        assertHistoryEvent(history, 12, "msg_created", msg__text="What is your favorite color?")
        assertHistoryEvent(history, 13, "flow_entered")

        # make our message event older than our planting reminder
        self.message_event.created_on = self.planting_reminder.created_on - timedelta(days=1)
        self.message_event.save()

        # but fire it immediately
        scheduled = timezone.now()
        EventFire.objects.create(event=self.message_event, contact=self.joe, scheduled=scheduled, fired=scheduled)

        # when fetched with limit of 1, it should be the only event we see
        response = self.requestView(
            url + "?limit=1&before=%d" % datetime_to_timestamp(scheduled + timedelta(minutes=5)), self.admin
        )
        assertHistoryEvent(response.json()["events"], 0, "campaign_fired", campaign_event__id=self.message_event.id)

        # now try the proper max history to test truncation
        response = self.requestView(url + "?before=%d" % datetime_to_timestamp(timezone.now()), self.admin)

        # our before should be the same as the last item
        resp_json = response.json()
        last_item_date = datetime_to_timestamp(iso8601.parse_date(resp_json["events"][-1]["created_on"]))
        self.assertEqual(resp_json["next_before"], last_item_date)

        # and our after should be 90 days earlier
        self.assertEqual(resp_json["next_after"], last_item_date - (90 * 24 * 60 * 60 * 1000 * 1000))
        self.assertEqual(50, len(resp_json["events"]))

        # and we should have a marker for older items
        self.assertTrue(resp_json["has_older"])

        # can't view history of contact in other org
        response = self.client.get(reverse("contacts.contact_history", args=[self.other_org_contact.uuid]))
        self.assertEqual(response.status_code, 404)

        # invalid UUID should return 404
        response = self.client.get(reverse("contacts.contact_history", args=["837d0842-4f6b-4751-bf21-471df75ce786"]))
        self.assertEqual(response.status_code, 404)

    def test_history_session_events(self):
        flow = self.get_flow("color_v13")
        nodes = flow.get_definition()["nodes"]
        (
            MockSessionWriter(self.joe, flow)
            .visit(nodes[0])
            .add_contact_urn("twitter", "joey")
            .set_contact_field("gender", "Gender", "M")
            .set_contact_field("age", "Age", "")
            .set_contact_language("spa")
            .set_contact_language("")
            .set_contact_name("Joe")
            .set_contact_name("")
            .set_result("Color", "red", "Red", "it's red")
            .send_email(["joe@textit.com"], "Test", "Hello there Joe")
            .error("unable to send email")
            .fail("this is a failure")
            .save()
        )

        history_url = reverse("contacts.contact_history", args=[self.joe.uuid])
        self.login(self.user)

        response = self.client.get(history_url)
        self.assertEqual(200, response.status_code)

        resp_json = response.json()
        self.assertEqual(9, len(resp_json["events"]))
        self.assertEqual(
            [
                "flow_exited",
                "contact_name_changed",
                "contact_name_changed",
                "contact_language_changed",
                "contact_language_changed",
                "contact_field_changed",
                "contact_field_changed",
                "contact_urns_changed",
                "flow_entered",
            ],
            [e["type"] for e in resp_json["events"]],
        )

    def test_msg_status_badge(self):
        msg = self.create_outgoing_msg(self.joe, "This is an outgoing message")

        # wired has a primary color check
        msg.status = Msg.STATUS_WIRED
        self.assertIn('"check"', msg_status_badge(msg))
        self.assertIn("--color-primary-dark", msg_status_badge(msg))

        # delivered has a success check
        msg.status = Msg.STATUS_DELIVERED
        self.assertIn('"check"', msg_status_badge(msg))
        self.assertIn("--success-rgb", msg_status_badge(msg))

        # errored show retrying icon
        msg.status = Msg.STATUS_ERRORED
        self.assertIn('"retry"', msg_status_badge(msg))

        # failed messages show an x
        msg.status = Msg.STATUS_FAILED
        self.assertIn('"x"', msg_status_badge(msg))

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

    def test_update_urns_field(self):
        update_url = reverse("contacts.contact_update", args=[self.joe.pk])

        # we have a field to add new urns
        response = self.requestView(update_url, self.admin)
        self.assertEqual(self.joe, response.context["object"])
        self.assertContains(response, "Add Connection")

        # no field to add new urns for anon org
        with self.anonymous(self.org):
            response = self.requestView(update_url, self.admin)
            self.assertEqual(self.joe, response.context["object"])
            self.assertNotContains(response, "Add Connection")

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


class ContactURNTest(TembaTest):
    def setUp(self):
        super().setUp()

    def test_get_display(self):
        urn = ContactURN.objects.create(
            org=self.org, scheme="tel", path="+250788383383", identity="tel:+250788383383", priority=50
        )
        self.assertEqual(urn.get_display(self.org), "0788 383 383")
        self.assertEqual(urn.get_display(self.org, formatted=False), "+250788383383")
        self.assertEqual(urn.get_display(self.org, international=True), "+250 788 383 383")
        self.assertEqual(urn.get_display(self.org, formatted=False, international=True), "+250788383383")

        # friendly tel formatting for whatsapp too
        urn = ContactURN.objects.create(
            org=self.org, scheme="whatsapp", path="12065551212", identity="whatsapp:12065551212", priority=50
        )
        self.assertEqual(urn.get_display(self.org), "(206) 555-1212")

        # use path for other schemes
        urn = ContactURN.objects.create(
            org=self.org, scheme="twitter", path="billy_bob", identity="twitter:billy_bob", priority=50
        )
        self.assertEqual(urn.get_display(self.org), "billy_bob")

        # unless there's a display property
        urn = ContactURN.objects.create(
            org=self.org,
            scheme="twitter",
            path="jimmy_john",
            identity="twitter:jimmy_john",
            priority=50,
            display="JIM",
        )
        self.assertEqual(urn.get_display(self.org), "JIM")

    def test_empty_scheme_disallowed(self):
        with self.assertRaises(IntegrityError):
            ContactURN.objects.create(org=self.org, scheme="", path="1234", identity=":1234")

    def test_empty_path_disallowed(self):
        with self.assertRaises(IntegrityError):
            ContactURN.objects.create(org=self.org, scheme="ext", path="", identity="ext:")

    def test_identity_mismatch_disallowed(self):
        with self.assertRaises(IntegrityError):
            ContactURN.objects.create(org=self.org, scheme="ext", path="1234", identity="ext:5678")

    def test_ensure_normalization(self):
        contact1 = self.create_contact("Bob", urns=["tel:+250788111111"])
        contact2 = self.create_contact("Jim", urns=["tel:+0788222222"])

        self.org.normalize_contact_tels()

        self.assertEqual("+250788111111", contact1.urns.get().path)
        self.assertEqual("+250788222222", contact2.urns.get().path)


class ContactFieldTest(TembaTest):
    def setUp(self):
        super().setUp()

        self.joe = self.create_contact(name="Joe Blow", phone="123")
        self.frank = self.create_contact(name="Frank Smith", phone="1234")

        self.contactfield_1 = self.create_field("first", "First", priority=10)
        self.contactfield_2 = self.create_field("second", "Second")
        self.contactfield_3 = self.create_field("third", "Third", priority=20)

        self.other_org_field = self.create_field("other", "Other", priority=10, org=self.org2)

    def test_get_or_create(self):
        # name can be generated
        field1 = ContactField.get_or_create(self.org, self.admin, "join_date")
        self.assertEqual("join_date", field1.key)
        self.assertEqual("Join Date", field1.name)
        self.assertEqual(ContactField.TYPE_TEXT, field1.value_type)
        self.assertFalse(field1.is_system)

        # or passed explicitly along with type
        field2 = ContactField.get_or_create(
            self.org, self.admin, "another", name="My Label", value_type=ContactField.TYPE_NUMBER
        )
        self.assertEqual("another", field2.key)
        self.assertEqual("My Label", field2.name)
        self.assertEqual(ContactField.TYPE_NUMBER, field2.value_type)

        # if there's an existing key with this key we get that with name and type updated
        field3 = ContactField.get_or_create(
            self.org, self.admin, "another", name="Updated Label", value_type=ContactField.TYPE_DATETIME
        )
        self.assertEqual(field2, field3)
        self.assertEqual("another", field3.key)
        self.assertEqual("Updated Label", field3.name)
        self.assertEqual(ContactField.TYPE_DATETIME, field3.value_type)

        field4 = ContactField.get_or_create(self.org, self.admin, "another", name="Updated Again Label")
        self.assertEqual(field3, field4)
        self.assertEqual("another", field4.key)
        self.assertEqual("Updated Again Label", field4.name)
        self.assertEqual(ContactField.TYPE_DATETIME, field4.value_type)  # unchanged

        # can't create with an invalid key
        for key in ContactField.RESERVED_KEYS:
            with self.assertRaises(ValueError):
                ContactField.get_or_create(self.org, self.admin, key, key, value_type=ContactField.TYPE_TEXT)

        # provided names are made unique
        field5 = ContactField.get_or_create(self.org, self.admin, "date_joined", name="join date")
        self.assertEqual("date_joined", field5.key)
        self.assertEqual("join date 2", field5.name)

        # and ignored if not valid
        field6 = ContactField.get_or_create(self.org, self.admin, "date_joined", name="  ")
        self.assertEqual(field5, field6)
        self.assertEqual("date_joined", field6.key)
        self.assertEqual("join date 2", field6.name)  # unchanged

        # same for creating a new field
        field7 = ContactField.get_or_create(self.org, self.admin, "new_key", name="  ")
        self.assertEqual("new_key", field7.key)
        self.assertEqual("New Key", field7.name)  # generated

    def test_make_key(self):
        self.assertEqual("first_name", ContactField.make_key("First Name"))
        self.assertEqual("second_name", ContactField.make_key("Second   Name  "))
        self.assertEqual("caf", ContactField.make_key("café"))
        self.assertEqual(
            "323_ffsn_slfs_ksflskfs_fk_anfaddgas",
            ContactField.make_key("  ^%$# %$$ $##323 ffsn slfs ksflskfs!!!! fk$%%%$$$anfaDDGAS ))))))))) "),
        )

    def test_is_valid_key(self):
        self.assertTrue(ContactField.is_valid_key("age"))
        self.assertTrue(ContactField.is_valid_key("age_now_2"))
        self.assertTrue(ContactField.is_valid_key("email"))
        self.assertFalse(ContactField.is_valid_key("Age"))  # must be lowercase
        self.assertFalse(ContactField.is_valid_key("age!"))  # can't have punctuation
        self.assertFalse(ContactField.is_valid_key("âge"))  # a-z only
        self.assertFalse(ContactField.is_valid_key("2up"))  # can't start with a number
        self.assertFalse(ContactField.is_valid_key("has"))  # can't be reserved key
        self.assertFalse(ContactField.is_valid_key("is"))
        self.assertFalse(ContactField.is_valid_key("fields"))
        self.assertFalse(ContactField.is_valid_key("urns"))
        self.assertFalse(ContactField.is_valid_key("a" * 37))  # too long

    def test_is_valid_name(self):
        self.assertTrue(ContactField.is_valid_name("Age"))
        self.assertTrue(ContactField.is_valid_name("Age Now 2"))
        self.assertFalse(ContactField.is_valid_name("Age_Now"))  # can't have punctuation
        self.assertFalse(ContactField.is_valid_name("âge"))  # a-z only

    @mock_mailroom
    def test_contact_field_list_sort_fields(self, mr_mocks):
        url = reverse("contacts.contact_list")
        self.login(self.admin)

        mr_mocks.contact_search("", contacts=[self.joe])
        mr_mocks.contact_search("Joe", contacts=[self.joe])

        response = self.client.get("%s?sort_on=%s" % (url, str(self.contactfield_1.key)))

        self.assertEqual(response.context["sort_field"], str(self.contactfield_1.key))
        self.assertEqual(response.context["sort_direction"], "asc")
        self.assertNotIn("search", response.context)

        response = self.client.get("%s?sort_on=-%s" % (url, str(self.contactfield_1.key)))

        self.assertEqual(response.context["sort_field"], str(self.contactfield_1.key))
        self.assertEqual(response.context["sort_direction"], "desc")
        self.assertNotIn("search", response.context)

        response = self.client.get("%s?sort_on=%s" % (url, "created_on"))

        self.assertEqual(response.context["sort_field"], "created_on")
        self.assertEqual(response.context["sort_direction"], "asc")
        self.assertNotIn("search", response.context)

        response = self.client.get("%s?sort_on=-%s&search=Joe" % (url, "created_on"))

        self.assertEqual(response.context["sort_field"], "created_on")
        self.assertEqual(response.context["sort_direction"], "desc")
        self.assertIn("search", response.context)

    def test_view_updatepriority_valid(self):
        org_fields = ContactField.user_fields.filter(org=self.org, is_active=True)

        self.assertListEqual([10, 0, 20], [cf.priority for cf in org_fields.order_by("id")])

        self.login(self.admin)
        updatepriority_cf_url = reverse("contacts.contactfield_update_priority")

        # there should be no updates because CFs with ids do not exist
        post_data = json.dumps({123_123: 1000, 123_124: 999, 123_125: 998})

        response = self.client.post(updatepriority_cf_url, post_data, content_type="application/json")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["status"], "OK")

        self.assertListEqual([10, 0, 20], [cf.priority for cf in org_fields.order_by("id")])

        # build valid post data
        post_data = json.dumps({cf.key: index for index, cf in enumerate(org_fields.order_by("id"))})

        # try to update as admin2
        self.login(self.admin2)
        response = self.client.post(updatepriority_cf_url, post_data, content_type="application/json")

        # nothing changed
        self.assertListEqual([10, 0, 20], [cf.priority for cf in org_fields.order_by("id")])

        # then as real admin
        self.login(self.admin)
        response = self.client.post(updatepriority_cf_url, post_data, content_type="application/json")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["status"], "OK")

        self.assertListEqual([0, 1, 2], [cf.priority for cf in org_fields.order_by("id")])

    def test_view_updatepriority_invalid(self):
        org_fields = ContactField.user_fields.filter(org=self.org, is_active=True)

        self.assertListEqual([10, 0, 20], [cf.priority for cf in org_fields.order_by("id")])

        self.login(self.admin)
        updatepriority_cf_url = reverse("contacts.contactfield_update_priority")

        post_data = '{invalid_json": 123}'

        response = self.client.post(updatepriority_cf_url, post_data, content_type="application/json")
        self.assertEqual(response.status_code, 400)
        response_json = response.json()
        self.assertEqual(response_json["status"], "ERROR")
        self.assertEqual(
            response_json["err_detail"], "Expecting property name enclosed in double quotes: line 1 column 2 (char 1)"
        )


class URNTest(TembaTest):
    def test_facebook_urn(self):
        self.assertTrue(URN.validate("facebook:ref:asdf"))

    def test_instagram_urn(self):
        self.assertTrue(URN.validate("instagram:12345678901234567"))

    def test_discord_urn(self):
        self.assertEqual("discord:750841288886321253", URN.from_discord("750841288886321253"))
        self.assertTrue(URN.validate(URN.from_discord("750841288886321253")))
        self.assertFalse(URN.validate(URN.from_discord("not-a-discord-id")))

    def test_whatsapp_urn(self):
        self.assertTrue(URN.validate("whatsapp:12065551212"))
        self.assertFalse(URN.validate("whatsapp:+12065551212"))

    def test_freshchat_urn(self):
        self.assertTrue(
            URN.validate("freshchat:c0534f78-b6e9-4f79-8853-11cedfc1f35b/c0534f78-b6e9-4f79-8853-11cedfc1f35b")
        )
        self.assertFalse(URN.validate("freshchat:+12065551212"))

    def test_from_parts(self):
        self.assertEqual(URN.from_parts("deleted", "12345"), "deleted:12345")
        self.assertEqual(URN.from_parts("tel", "12345"), "tel:12345")
        self.assertEqual(URN.from_parts("tel", "+12345"), "tel:+12345")
        self.assertEqual(URN.from_parts("tel", "(917) 992-5253"), "tel:(917) 992-5253")
        self.assertEqual(URN.from_parts("mailto", "a_b+c@d.com"), "mailto:a_b+c@d.com")
        self.assertEqual(URN.from_parts("twitterid", "2352362611", display="bobby"), "twitterid:2352362611#bobby")
        self.assertEqual(
            URN.from_parts("twitterid", "2352362611", query="foo=ba?r", display="bobby"),
            "twitterid:2352362611?foo=ba%3Fr#bobby",
        )

        self.assertEqual(URN.from_tel("+12345"), "tel:+12345")

        self.assertRaises(ValueError, URN.from_parts, "", "12345")
        self.assertRaises(ValueError, URN.from_parts, "tel", "")
        self.assertRaises(ValueError, URN.from_parts, "xxx", "12345")

    def test_to_parts(self):
        self.assertEqual(URN.to_parts("deleted:12345"), ("deleted", "12345", None, None))
        self.assertEqual(URN.to_parts("tel:12345"), ("tel", "12345", None, None))
        self.assertEqual(URN.to_parts("tel:+12345"), ("tel", "+12345", None, None))
        self.assertEqual(URN.to_parts("twitter:abc_123"), ("twitter", "abc_123", None, None))
        self.assertEqual(URN.to_parts("mailto:a_b+c@d.com"), ("mailto", "a_b+c@d.com", None, None))
        self.assertEqual(URN.to_parts("facebook:12345"), ("facebook", "12345", None, None))
        self.assertEqual(URN.to_parts("vk:12345"), ("vk", "12345", None, None))
        self.assertEqual(URN.to_parts("telegram:12345"), ("telegram", "12345", None, None))
        self.assertEqual(URN.to_parts("telegram:12345#foobar"), ("telegram", "12345", None, "foobar"))
        self.assertEqual(URN.to_parts("ext:Aa0()+,-.:=@;$_!*'"), ("ext", "Aa0()+,-.:=@;$_!*'", None, None))
        self.assertEqual(URN.to_parts("instagram:12345"), ("instagram", "12345", None, None))

        self.assertRaises(ValueError, URN.to_parts, "tel")
        self.assertRaises(ValueError, URN.to_parts, "tel:")  # missing scheme
        self.assertRaises(ValueError, URN.to_parts, ":12345")  # missing path
        self.assertRaises(ValueError, URN.to_parts, "x_y:123")  # invalid scheme
        self.assertRaises(ValueError, URN.to_parts, "xyz:{abc}")  # invalid path

    def test_normalize(self):
        # valid tel numbers
        self.assertEqual(URN.normalize("tel:0788383383", "RW"), "tel:+250788383383")
        self.assertEqual(URN.normalize("tel: +250788383383 ", "KE"), "tel:+250788383383")
        self.assertEqual(URN.normalize("tel:+250788383383", None), "tel:+250788383383")
        self.assertEqual(URN.normalize("tel:250788383383", None), "tel:+250788383383")
        self.assertEqual(URN.normalize("tel:2.50788383383E+11", None), "tel:+250788383383")
        self.assertEqual(URN.normalize("tel:2.50788383383E+12", None), "tel:+250788383383")
        self.assertEqual(URN.normalize("tel:(917)992-5253", "US"), "tel:+19179925253")
        self.assertEqual(URN.normalize("tel:19179925253", None), "tel:+19179925253")
        self.assertEqual(URN.normalize("tel:+62877747666", None), "tel:+62877747666")
        self.assertEqual(URN.normalize("tel:62877747666", "ID"), "tel:+62877747666")
        self.assertEqual(URN.normalize("tel:0877747666", "ID"), "tel:+62877747666")
        self.assertEqual(URN.normalize("tel:07531669965", "GB"), "tel:+447531669965")
        self.assertEqual(URN.normalize("tel:22658125926", ""), "tel:+22658125926")
        self.assertEqual(URN.normalize("tel:263780821000", "ZW"), "tel:+263780821000")
        self.assertEqual(URN.normalize("tel:+2203693333", ""), "tel:+2203693333")

        # un-normalizable tel numbers
        self.assertEqual(URN.normalize("tel:12345", "RW"), "tel:12345")
        self.assertEqual(URN.normalize("tel:0788383383", None), "tel:0788383383")
        self.assertEqual(URN.normalize("tel:0788383383", "ZZ"), "tel:0788383383")
        self.assertEqual(URN.normalize("tel:MTN", "RW"), "tel:mtn")

        # twitter handles remove @
        self.assertEqual(URN.normalize("twitter: @jimmyJO"), "twitter:jimmyjo")
        self.assertEqual(URN.normalize("twitterid:12345#@jimmyJO"), "twitterid:12345#jimmyjo")

        # email addresses
        self.assertEqual(URN.normalize("mailto: nAme@domAIN.cOm "), "mailto:name@domain.com")

        # external ids are case sensitive
        self.assertEqual(URN.normalize("ext: eXterNAL123 "), "ext:eXterNAL123")

    def test_validate(self):
        self.assertFalse(URN.validate("xxxx", None))  # un-parseable URNs don't validate

        # valid tel numbers
        self.assertTrue(URN.validate("tel:0788383383", "RW"))
        self.assertTrue(URN.validate("tel:+250788383383", "KE"))
        self.assertTrue(URN.validate("tel:+23761234567", "CM"))  # old Cameroon format
        self.assertTrue(URN.validate("tel:+237661234567", "CM"))  # new Cameroon format
        self.assertTrue(URN.validate("tel:+250788383383", None))

        # invalid tel numbers
        self.assertFalse(URN.validate("tel:0788383383", "ZZ"))  # invalid country
        self.assertFalse(URN.validate("tel:0788383383", None))  # no country
        self.assertFalse(URN.validate("tel:MTN", "RW"))
        self.assertFalse(URN.validate("tel:5912705", "US"))

        # twitter handles
        self.assertTrue(URN.validate("twitter:jimmyjo"))
        self.assertTrue(URN.validate("twitter:billy_bob"))
        self.assertFalse(URN.validate("twitter:jimmyjo!@"))
        self.assertFalse(URN.validate("twitter:billy bob"))

        # twitterid urns
        self.assertTrue(URN.validate("twitterid:12345#jimmyjo"))
        self.assertTrue(URN.validate("twitterid:12345#1234567"))
        self.assertFalse(URN.validate("twitterid:jimmyjo#1234567"))
        self.assertFalse(URN.validate("twitterid:123#a.!f"))

        # email addresses
        self.assertTrue(URN.validate("mailto:abcd+label@x.y.z.com"))
        self.assertFalse(URN.validate("mailto:@@@"))

        # viber urn
        self.assertTrue(URN.validate("viber:dKPvqVrLerGrZw15qTuVBQ=="))

        # facebook, telegram, vk and instagram URN paths must be integers
        self.assertTrue(URN.validate("telegram:12345678901234567"))
        self.assertFalse(URN.validate("telegram:abcdef"))
        self.assertTrue(URN.validate("facebook:12345678901234567"))
        self.assertFalse(URN.validate("facebook:abcdef"))
        self.assertTrue(URN.validate("vk:12345678901234567"))
        self.assertTrue(URN.validate("instagram:12345678901234567"))
        self.assertFalse(URN.validate("instagram:abcdef"))


class ContactImportTest(TembaTest):
    def test_parse_errors(self):
        # try to open an import that is completely empty
        with self.assertRaisesRegex(ValidationError, "Import file appears to be empty."):
            path = "media/test_imports/empty_all_rows.xlsx"  # No header row present either
            with open(path, "rb") as f:
                ContactImport.try_to_parse(self.org, f, path)

        def try_to_parse(name):
            path = f"media/test_imports/{name}"
            with open(path, "rb") as f:
                ContactImport.try_to_parse(self.org, f, path)

        # try to open an import that exceeds the record limit
        with patch("temba.contacts.models.ContactImport.MAX_RECORDS", 2):
            with self.assertRaisesRegex(ValidationError, r"Import files can contain a maximum of 2 records\."):
                try_to_parse("simple.xlsx")

        bad_files = [
            ("empty.xlsx", "Import file doesn't contain any records."),
            ("empty_header.xlsx", "Import file contains an empty header."),
            ("duplicate_urn.xlsx", "Import file contains duplicated contact URN 'tel:+250788382382' on row 4."),
            (
                "duplicate_uuid.xlsx",
                "Import file contains duplicated contact UUID 'f519ca1f-8513-49ba-8896-22bf0420dec7' on row 4.",
            ),
            ("invalid_scheme.xlsx", "Header 'URN:XXX' is not a valid URN type."),
            ("invalid_field_key.xlsx", "Header 'Field: #$^%' is not a valid field name."),
            ("reserved_field_key.xlsx", "Header 'Field:HAS' is not a valid field name."),
            ("no_urn_or_uuid.xlsx", "Import files must contain either UUID or a URN header."),
            ("uuid_only.xlsx", "Import files must contain columns besides UUID."),
            ("invalid.txt.xlsx", "Import file appears to be corrupted."),
        ]

        for imp_file, imp_error in bad_files:
            with self.assertRaises(ValidationError, msg=f"expected error in {imp_file}") as e:
                try_to_parse(imp_file)
            self.assertEqual(imp_error, e.exception.messages[0], f"error mismatch for {imp_file}")

    def test_extract_mappings(self):
        # try simple import in different formats
        for ext in ("xlsx",):
            imp = self.create_contact_import(f"media/test_imports/simple.{ext}")
            self.assertEqual(3, imp.num_records)
            self.assertEqual(
                [
                    {"header": "URN:Tel", "mapping": {"type": "scheme", "scheme": "tel"}},
                    {"header": "name", "mapping": {"type": "attribute", "name": "name"}},
                ],
                imp.mappings,
            )

        # try import with 2 URN types
        imp = self.create_contact_import("media/test_imports/twitter_and_phone.xlsx")
        self.assertEqual(
            [
                {"header": "URN:Tel", "mapping": {"type": "scheme", "scheme": "tel"}},
                {"header": "name", "mapping": {"type": "attribute", "name": "name"}},
                {"header": "URN:Twitter", "mapping": {"type": "scheme", "scheme": "twitter"}},
            ],
            imp.mappings,
        )

        # or with 3 URN columns
        imp = self.create_contact_import("media/test_imports/multiple_tel_urns.xlsx")
        self.assertEqual(
            [
                {"header": "Name", "mapping": {"type": "attribute", "name": "name"}},
                {"header": "URN:Tel", "mapping": {"type": "scheme", "scheme": "tel"}},
                {"header": "URN:Tel", "mapping": {"type": "scheme", "scheme": "tel"}},
                {"header": "URN:Tel", "mapping": {"type": "scheme", "scheme": "tel"}},
            ],
            imp.mappings,
        )

        imp = self.create_contact_import("media/test_imports/missing_name_header.xlsx")
        self.assertEqual([{"header": "URN:Tel", "mapping": {"type": "scheme", "scheme": "tel"}}], imp.mappings)

        self.create_field("goats", "Num Goats", ContactField.TYPE_NUMBER)

        imp = self.create_contact_import("media/test_imports/extra_fields_and_group.xlsx")
        self.assertEqual(
            [
                {"header": "URN:Tel", "mapping": {"type": "scheme", "scheme": "tel"}},
                {"header": "Name", "mapping": {"type": "attribute", "name": "name"}},
                {"header": "language", "mapping": {"type": "attribute", "name": "language"}},
                {"header": "Status", "mapping": {"type": "attribute", "name": "status"}},
                {"header": "Created On", "mapping": {"type": "ignore"}},
                {
                    "header": "field: goats",
                    "mapping": {"type": "field", "key": "goats", "name": "Num Goats"},  # matched by key
                },
                {
                    "header": "Field:Sheep",
                    "mapping": {"type": "new_field", "key": "sheep", "name": "Sheep", "value_type": "T"},
                },
                {"header": "Group:Testers", "mapping": {"type": "ignore"}},
            ],
            imp.mappings,
        )

        # it's possible for field keys and labels to be out of sync, in which case we match by label first because
        # that's how we export contacts
        self.create_field("num_goats", "Goats", ContactField.TYPE_NUMBER)

        imp = self.create_contact_import("media/test_imports/extra_fields_and_group.xlsx")
        self.assertEqual(
            {
                "header": "field: goats",
                "mapping": {"type": "field", "key": "num_goats", "name": "Goats"},  # matched by label
            },
            imp.mappings[5],
        )

        # a header can be a number but it will be ignored
        imp = self.create_contact_import("media/test_imports/numerical_header.xlsx")
        self.assertEqual(
            [
                {"header": "URN:Tel", "mapping": {"type": "scheme", "scheme": "tel"}},
                {"header": "Name", "mapping": {"name": "name", "type": "attribute"}},
                {"header": "123", "mapping": {"type": "ignore"}},
            ],
            imp.mappings,
        )

        self.create_field("a_number", "A-Number", ContactField.TYPE_NUMBER)

        imp = self.create_contact_import("media/test_imports/header_chars.xlsx")
        self.assertEqual(
            [
                {"header": "URN:Tel", "mapping": {"type": "scheme", "scheme": "tel"}},
                {"header": "Name", "mapping": {"type": "attribute", "name": "name"}},
                {"header": "Field: A-Number", "mapping": {"type": "field", "key": "a_number", "name": "A-Number"}},
            ],
            imp.mappings,
        )

    @mock_mailroom
    def test_batches(self, mr_mocks):
        imp = self.create_contact_import("media/test_imports/simple.xlsx")
        self.assertEqual(3, imp.num_records)
        self.assertIsNone(imp.started_on)

        # info can be fetched but it's empty
        self.assertEqual(
            {"status": "P", "num_created": 0, "num_updated": 0, "num_errored": 0, "errors": [], "time_taken": 0},
            imp.get_info(),
        )

        imp.start()
        batches = list(imp.batches.order_by("id"))

        self.assertIsNotNone(imp.started_on)
        self.assertEqual(1, len(batches))
        self.assertEqual(0, batches[0].record_start)
        self.assertEqual(3, batches[0].record_end)
        self.assertEqual(
            [
                {
                    "_import_row": 2,
                    "name": "Eric Newcomer",
                    "urns": ["tel:+250788382382"],
                    "groups": [str(imp.group.uuid)],
                },
                {
                    "_import_row": 3,
                    "name": "NIC POTTIER",
                    "urns": ["tel:+250788383383"],
                    "groups": [str(imp.group.uuid)],
                },
                {
                    "_import_row": 4,
                    "name": "jen newcomer",
                    "urns": ["tel:+250788383385"],
                    "groups": [str(imp.group.uuid)],
                },
            ],
            batches[0].specs,
        )

        # check batch was queued for import by mailroom
        self.assertEqual(
            [
                {
                    "type": "import_contact_batch",
                    "org_id": self.org.id,
                    "task": {"contact_import_batch_id": batches[0].id},
                    "queued_on": matchers.Datetime(),
                },
            ],
            mr_mocks.queued_batch_tasks,
        )

        # records are batched if they exceed batch size
        with patch("temba.contacts.models.ContactImport.BATCH_SIZE", 2):
            imp = self.create_contact_import("media/test_imports/simple.xlsx")
            imp.start()

        batches = list(imp.batches.order_by("id"))
        self.assertEqual(2, len(batches))
        self.assertEqual(0, batches[0].record_start)
        self.assertEqual(2, batches[0].record_end)
        self.assertEqual(2, batches[1].record_start)
        self.assertEqual(3, batches[1].record_end)

        # info is calculated across all batches
        self.assertEqual(
            {
                "status": "O",
                "num_created": 0,
                "num_updated": 0,
                "num_errored": 0,
                "errors": [],
                "time_taken": matchers.Int(min=0),
            },
            imp.get_info(),
        )

        # simulate mailroom starting to process first batch
        imp.batches.filter(id=batches[0].id).update(
            status="O", num_created=2, num_updated=1, errors=[{"record": 1, "message": "that's wrong"}]
        )

        self.assertEqual(
            {
                "status": "O",
                "num_created": 2,
                "num_updated": 1,
                "num_errored": 0,
                "errors": [{"record": 1, "message": "that's wrong"}],
                "time_taken": matchers.Int(min=0),
            },
            imp.get_info(),
        )

        # simulate mailroom completing first batch, starting second
        imp.batches.filter(id=batches[0].id).update(status="C", finished_on=timezone.now())
        imp.batches.filter(id=batches[1].id).update(
            status="O", num_created=3, num_updated=5, errors=[{"record": 3, "message": "that's not right"}]
        )

        self.assertEqual(
            {
                "status": "O",
                "num_created": 5,
                "num_updated": 6,
                "num_errored": 0,
                "errors": [{"record": 1, "message": "that's wrong"}, {"record": 3, "message": "that's not right"}],
                "time_taken": matchers.Int(min=0),
            },
            imp.get_info(),
        )

        # simulate mailroom completing second batch
        imp.batches.filter(id=batches[1].id).update(status="C", finished_on=timezone.now())
        imp.status = "C"
        imp.finished_on = timezone.now()
        imp.save(update_fields=("finished_on", "status"))

        self.assertEqual(
            {
                "status": "C",
                "num_created": 5,
                "num_updated": 6,
                "num_errored": 0,
                "errors": [{"record": 1, "message": "that's wrong"}, {"record": 3, "message": "that's not right"}],
                "time_taken": matchers.Int(min=0),
            },
            imp.get_info(),
        )

    @mock_mailroom
    def test_batches_with_fields(self, mr_mocks):
        self.create_field("goats", "Goats", ContactField.TYPE_NUMBER)

        imp = self.create_contact_import("media/test_imports/extra_fields_and_group.xlsx")
        imp.start()
        batch = imp.batches.get()  # single batch

        self.assertEqual(
            [
                {
                    "_import_row": 2,
                    "name": "John Doe",
                    "language": "eng",
                    "status": "archived",
                    "urns": ["tel:+250788123123"],
                    "fields": {"goats": "1", "sheep": "0"},
                    "groups": [str(imp.group.uuid)],
                },
                {
                    "_import_row": 3,
                    "name": "Mary Smith",
                    "language": "spa",
                    "status": "blocked",
                    "urns": ["tel:+250788456456"],
                    "fields": {"goats": "3", "sheep": "5"},
                    "groups": [str(imp.group.uuid)],
                },
                {
                    "_import_row": 4,
                    "urns": ["tel:+250788456678"],
                    "groups": [str(imp.group.uuid)],
                },  # blank values ignored
            ],
            batch.specs,
        )

        imp = self.create_contact_import("media/test_imports/with_empty_rows.xlsx")
        imp.start()
        batch = imp.batches.get()  # single batch

        # row 2 nad 3 is skipped
        self.assertEqual(
            [
                {
                    "_import_row": 2,
                    "name": "John Doe",
                    "language": "eng",
                    "urns": ["tel:+250788123123"],
                    "fields": {"goats": "1", "sheep": "0"},
                    "groups": [str(imp.group.uuid)],
                },
                {
                    "_import_row": 5,
                    "name": "Mary Smith",
                    "language": "spa",
                    "urns": ["tel:+250788456456"],
                    "fields": {"goats": "3", "sheep": "5"},
                    "groups": [str(imp.group.uuid)],
                },
                {
                    "_import_row": 6,
                    "urns": ["tel:+250788456678"],
                    "groups": [str(imp.group.uuid)],
                },  # blank values ignored
            ],
            batch.specs,
        )

        imp = self.create_contact_import("media/test_imports/with_uuid.xlsx")
        imp.start()
        batch = imp.batches.get()
        self.assertEqual(
            [
                {
                    "_import_row": 2,
                    "uuid": "f519ca1f-8513-49ba-8896-22bf0420dec7",
                    "name": "Joe",
                    "groups": [str(imp.group.uuid)],
                },
                {
                    "_import_row": 3,
                    "uuid": "989975f0-3bff-43d6-82c8-a6bbc201c938",
                    "name": "Frank",
                    "groups": [str(imp.group.uuid)],
                },
            ],
            batch.specs,
        )

        # cells with -- mean explicit clearing of those values
        imp = self.create_contact_import("media/test_imports/explicit_clearing.xlsx")
        imp.start()
        batch = imp.batches.get()  # single batch

        self.assertEqual(
            {
                "_import_row": 4,
                "name": "",
                "language": "",
                "urns": ["tel:+250788456678"],
                "fields": {"goats": "", "sheep": ""},
                "groups": [str(imp.group.uuid)],
            },
            batch.specs[2],
        )

        # uuids and languages converted to lowercase, case in names is preserved
        imp = self.create_contact_import("media/test_imports/uppercase.xlsx")
        imp.start()
        batch = imp.batches.get()
        self.assertEqual(
            [
                {
                    "_import_row": 2,
                    "uuid": "92faa753-6faa-474a-a833-788032d0b757",
                    "name": "Eric Newcomer",
                    "language": "eng",
                    "groups": [str(imp.group.uuid)],
                },
                {
                    "_import_row": 3,
                    "uuid": "3c11ac1f-c869-4247-a73c-9b97bff61659",
                    "name": "NIC POTTIER",
                    "language": "spa",
                    "groups": [str(imp.group.uuid)],
                },
            ],
            batch.specs,
        )

    @mock_mailroom
    def test_batches_with_invalid_urn(self, mr_mocks):
        imp = self.create_contact_import("media/test_imports/invalid_urn.xlsx")
        imp.start()
        batch = imp.batches.get()

        # invalid looking urns still passed to mailroom to decide how to handle them
        self.assertEqual(
            [
                {"_import_row": 2, "name": "Eric Newcomer", "urns": ["tel:+%3F"], "groups": [str(imp.group.uuid)]},
                {
                    "_import_row": 3,
                    "name": "Nic Pottier",
                    "urns": ["tel:2345678901234567890"],
                    "groups": [str(imp.group.uuid)],
                },
            ],
            batch.specs,
        )

    @mock_mailroom
    def test_batches_with_multiple_tels(self, mr_mocks):
        imp = self.create_contact_import("media/test_imports/multiple_tel_urns.xlsx")
        imp.start()
        batch = imp.batches.get()

        self.assertEqual(
            [
                {
                    "_import_row": 2,
                    "name": "Bob",
                    "urns": ["tel:+250788382001", "tel:+250788382002", "tel:+250788382003"],
                    "groups": [str(imp.group.uuid)],
                },
                {
                    "_import_row": 3,
                    "name": "Jim",
                    "urns": ["tel:+250788382004", "tel:+250788382005"],
                    "groups": [str(imp.group.uuid)],
                },
            ],
            batch.specs,
        )

    @mock_mailroom
    def test_batches_from_xlsx(self, mr_mocks):
        imp = self.create_contact_import("media/test_imports/simple.xlsx")
        imp.start()
        batch = imp.batches.get()

        self.assertEqual(
            [
                {
                    "_import_row": 2,
                    "name": "Eric Newcomer",
                    "urns": ["tel:+250788382382"],
                    "groups": [str(imp.group.uuid)],
                },
                {
                    "_import_row": 3,
                    "name": "NIC POTTIER",
                    "urns": ["tel:+250788383383"],
                    "groups": [str(imp.group.uuid)],
                },
                {
                    "_import_row": 4,
                    "name": "jen newcomer",
                    "urns": ["tel:+250788383385"],
                    "groups": [str(imp.group.uuid)],
                },
            ],
            batch.specs,
        )

    @mock_mailroom
    def test_batches_from_xlsx_with_formulas(self, mr_mocks):
        imp = self.create_contact_import("media/test_imports/formula_data.xlsx")
        imp.start()
        batch = imp.batches.get()

        self.assertEqual(
            [
                {
                    "_import_row": 2,
                    "fields": {"team": "Managers"},
                    "name": "John Smith",
                    "urns": ["tel:+12025550199"],
                    "groups": [str(imp.group.uuid)],
                },
                {
                    "_import_row": 3,
                    "fields": {"team": "Advisors"},
                    "name": "Mary Green",
                    "urns": ["tel:+14045550178"],
                    "groups": [str(imp.group.uuid)],
                },
            ],
            batch.specs,
        )

    @mock_mailroom
    def test_detect_spamminess(self, mr_mocks):
        imp = self.create_contact_import("media/test_imports/sequential_tels.xlsx")
        imp.start()

        self.org.refresh_from_db()
        self.assertTrue(self.org.is_flagged)

        with patch("temba.contacts.models.ContactImport.SEQUENTIAL_URNS_THRESHOLD", 3):
            self.assertFalse(ContactImport._detect_spamminess(["tel:+593979000001", "tel:+593979000002"]))
            self.assertFalse(
                ContactImport._detect_spamminess(
                    ["tel:+593979000001", "tel:+593979000003", "tel:+593979000005", "tel:+593979000007"]
                )
            )

            self.assertTrue(
                ContactImport._detect_spamminess(["tel:+593979000001", "tel:+593979000002", "tel:+593979000003"])
            )

            # order not important
            self.assertTrue(
                ContactImport._detect_spamminess(["tel:+593979000003", "tel:+593979000001", "tel:+593979000002"])
            )

            # non-numeric paths ignored
            self.assertTrue(
                ContactImport._detect_spamminess(
                    ["tel:+593979000001", "tel:ABC", "tel:+593979000002", "tel:+593979000003"]
                )
            )

    @mock_mailroom
    def test_detect_spamminess_verified_org(self, mr_mocks):
        # if an org is verified, no flagging occurs
        self.org.verify()

        imp = self.create_contact_import("media/test_imports/sequential_tels.xlsx")
        imp.start()

        self.org.refresh_from_db()
        self.assertFalse(self.org.is_flagged)

    def test_data_types(self):
        imp = self.create_contact_import("media/test_imports/data_formats.xlsx")
        imp.start()
        batch = imp.batches.get()
        self.assertEqual(
            [
                {
                    "_import_row": 2,
                    "uuid": "17c4388a-024f-4e67-937a-13be78a70766",
                    "fields": {
                        "a_number": "1234.5678",
                        "a_date": "2020-10-19T00:00:00+02:00",
                        "a_time": "13:17:00",
                        "a_datetime": "2020-10-19T13:18:00+02:00",
                        "price": "123.45",
                    },
                    "groups": [str(imp.group.uuid)],
                }
            ],
            batch.specs,
        )

    def test_parse_value(self):
        imp = self.create_contact_import("media/test_imports/simple.xlsx")
        kgl = ZoneInfo("Africa/Kigali")

        tests = [
            ("", ""),
            (" Yes ", "Yes"),
            (1234, "1234"),
            (123.456, "123.456"),
            (date(2020, 9, 18), "2020-09-18"),
            (datetime(2020, 9, 18, 15, 45, 30, 0), "2020-09-18T15:45:30+02:00"),
            (datetime(2020, 9, 18, 15, 45, 30, 0).replace(tzinfo=kgl), "2020-09-18T15:45:30+02:00"),
        ]
        for test in tests:
            self.assertEqual(test[1], imp._parse_value(test[0], tz=kgl))

    def test_get_default_group_name(self):
        self.create_group("Testers", contacts=[])
        tests = [
            ("simple.xlsx", "Simple"),
            ("testers.xlsx", "Testers 2"),  # group called Testers already exists
            ("contact-imports.xlsx", "Contact Imports"),
            ("abc_@@é.xlsx", "Abc É"),
            ("a_@@é.xlsx", "Import"),  # would be too short
            (f"{'x' * 100}.xlsx", "Xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx"),  # truncated
        ]
        for test in tests:
            self.assertEqual(test[1], ContactImport(org=self.org, original_filename=test[0]).get_default_group_name())

    @mock_mailroom
    def test_delete(self, mr_mocks):
        imp = self.create_contact_import("media/test_imports/simple.xlsx")
        imp.start()
        imp.delete()

        self.assertEqual(0, ContactImport.objects.count())
        self.assertEqual(0, ContactImportBatch.objects.count())


class ContactExportTest(TembaTest):
    def setUp(self):
        super().setUp()

        self.joe = self.create_contact(name="Joe Blow", phone="123")
        self.frank = self.create_contact(name="Frank Smith", phone="1234")

        self.contactfield_1 = self.create_field("first", "First", priority=10)
        self.contactfield_2 = self.create_field("second", "Second")
        self.contactfield_3 = self.create_field("third", "Third", priority=20)

    def _export(self, group, search="", with_groups=()):
        export = ContactExport.create(self.org, self.admin, group, search, with_groups=with_groups)
        with self.mockReadOnly(assert_models={Contact, ContactURN, ContactField}):
            export.perform()

        workbook = load_workbook(
            filename=default_storage.open(f"orgs/{self.org.id}/contact_exports/{export.uuid}.xlsx")
        )
        return workbook.worksheets, export

    @mock_mailroom
    def test_export(self, mr_mocks):
        # archive all our current contacts
        Contact.apply_action_block(self.admin, self.org.contacts.all())

        # make third a datetime
        self.contactfield_3.value_type = ContactField.TYPE_DATETIME
        self.contactfield_3.save()

        # start one of our contacts down it
        contact = self.create_contact(
            "Be\02n Haggerty",
            phone="+12067799294",
            fields={"first": "On\02e", "third": "20/12/2015 08:30"},
            last_seen_on=datetime(2020, 1, 1, 12, 0, 0, 0, tzinfo=tzone.utc),
        )

        flow = self.get_flow("color_v13")
        nodes = flow.get_definition()["nodes"]
        color_prompt = nodes[0]
        color_split = nodes[4]

        (
            MockSessionWriter(self.joe, flow)
            .visit(color_prompt)
            .send_msg("What is your favorite color?", self.channel)
            .visit(color_split)
            .wait()
            .save()
        )

        # create another contact, this should sort before Ben
        contact2 = self.create_contact("Adam Sumner", urns=["tel:+12067799191", "twitter:adam"], language="eng")
        urns = [str(urn) for urn in contact2.get_urns()]
        urns.append("mailto:adam@sumner.com")
        urns.append("telegram:1234")
        contact2.modify(self.admin, contact2.update_urns(urns))

        group1 = self.create_group("Poppin Tags", [contact, contact2])
        group2 = self.create_group("Dynamic", query="tel is 1234")
        group2.status = ContactGroup.STATUS_EVALUATING
        group2.save()

        # create orphaned URN in scheme that no contacts have a URN for
        ContactURN.objects.create(org=self.org, identity="line:12345", scheme="line", path="12345")

        def assertReimport(export):
            with default_storage.open(f"orgs/{self.org.id}/contact_exports/{export.uuid}.xlsx") as exp:
                with tempfile.NamedTemporaryFile(delete=False) as tmp:
                    tmp.write(exp.read())
                    tmp.close()

                    self.create_contact_import(tmp.name)

        with self.assertNumQueries(22):
            sheets, export = self._export(self.org.active_contacts_group, with_groups=[group1])
            self.assertEqual(2, export.num_records)
            self.assertEqual("C", export.status)
            self.assertExcelSheet(
                sheets[0],
                [
                    [
                        "Contact UUID",
                        "Name",
                        "Language",
                        "Status",
                        "Created On",
                        "Last Seen On",
                        "URN:Mailto",
                        "URN:Tel",
                        "URN:Telegram",
                        "URN:Twitter",
                        "Field:Third",
                        "Field:First",
                        "Field:Second",
                        "Group:Poppin Tags",
                    ],
                    [
                        contact.uuid,
                        "Ben Haggerty",
                        "",
                        "Active",
                        contact.created_on,
                        datetime(2020, 1, 1, 12, 0, 0, 0, tzinfo=tzone.utc),
                        "",
                        "+12067799294",
                        "",
                        "",
                        "20-12-2015 08:30",
                        "One",
                        "",
                        True,
                    ],
                    [
                        contact2.uuid,
                        "Adam Sumner",
                        "eng",
                        "Active",
                        contact2.created_on,
                        "",
                        "adam@sumner.com",
                        "+12067799191",
                        "1234",
                        "adam",
                        "",
                        "",
                        "",
                        True,
                    ],
                ],
                tz=self.org.timezone,
            )

        assertReimport(export)

        # check that notifications were created
        export = Export.objects.filter(export_type=ContactExport.slug).order_by("id").last()
        self.assertEqual(1, self.admin.notifications.filter(notification_type="export:finished", export=export).count())

        # change the order of the fields
        self.contactfield_2.priority = 15
        self.contactfield_2.save()

        with self.assertNumQueries(21):
            sheets, export = self._export(self.org.active_contacts_group, with_groups=[group1])
            self.assertEqual(2, export.num_records)
            self.assertEqual("C", export.status)
            self.assertExcelSheet(
                sheets[0],
                [
                    [
                        "Contact UUID",
                        "Name",
                        "Language",
                        "Status",
                        "Created On",
                        "Last Seen On",
                        "URN:Mailto",
                        "URN:Tel",
                        "URN:Telegram",
                        "URN:Twitter",
                        "Field:Third",
                        "Field:Second",
                        "Field:First",
                        "Group:Poppin Tags",
                    ],
                    [
                        contact.uuid,
                        "Ben Haggerty",
                        "",
                        "Active",
                        contact.created_on,
                        datetime(2020, 1, 1, 12, 0, 0, 0, tzinfo=tzone.utc),
                        "",
                        "+12067799294",
                        "",
                        "",
                        "20-12-2015 08:30",
                        "",
                        "One",
                        True,
                    ],
                    [
                        contact2.uuid,
                        "Adam Sumner",
                        "eng",
                        "Active",
                        contact2.created_on,
                        "",
                        "adam@sumner.com",
                        "+12067799191",
                        "1234",
                        "adam",
                        "",
                        "",
                        "",
                        True,
                    ],
                ],
                tz=self.org.timezone,
            )

        assertReimport(export)

        # more contacts do not increase the queries
        contact3 = self.create_contact("Luol Deng", urns=["tel:+12078776655", "twitter:deng"])
        contact4 = self.create_contact("Stephen", urns=["tel:+12078778899", "twitter:stephen"])
        contact.urns.create(org=self.org, identity="tel:+12062233445", scheme="tel", path="+12062233445")

        # but should have additional Twitter and phone columns
        with self.assertNumQueries(21):
            sheets, export = self._export(self.org.active_contacts_group, with_groups=[group1])
            self.assertEqual(4, export.num_records)
            self.assertExcelSheet(
                sheets[0],
                [
                    [
                        "Contact UUID",
                        "Name",
                        "Language",
                        "Status",
                        "Created On",
                        "Last Seen On",
                        "URN:Mailto",
                        "URN:Tel",
                        "URN:Tel",
                        "URN:Telegram",
                        "URN:Twitter",
                        "Field:Third",
                        "Field:Second",
                        "Field:First",
                        "Group:Poppin Tags",
                    ],
                    [
                        contact.uuid,
                        "Ben Haggerty",
                        "",
                        "Active",
                        contact.created_on,
                        datetime(2020, 1, 1, 12, 0, 0, 0, tzinfo=tzone.utc),
                        "",
                        "+12067799294",
                        "+12062233445",
                        "",
                        "",
                        "20-12-2015 08:30",
                        "",
                        "One",
                        True,
                    ],
                    [
                        contact2.uuid,
                        "Adam Sumner",
                        "eng",
                        "Active",
                        contact2.created_on,
                        "",
                        "adam@sumner.com",
                        "+12067799191",
                        "",
                        "1234",
                        "adam",
                        "",
                        "",
                        "",
                        True,
                    ],
                    [
                        contact3.uuid,
                        "Luol Deng",
                        "",
                        "Active",
                        contact3.created_on,
                        "",
                        "",
                        "+12078776655",
                        "",
                        "",
                        "deng",
                        "",
                        "",
                        "",
                        False,
                    ],
                    [
                        contact4.uuid,
                        "Stephen",
                        "",
                        "Active",
                        contact4.created_on,
                        "",
                        "",
                        "+12078778899",
                        "",
                        "",
                        "stephen",
                        "",
                        "",
                        "",
                        False,
                    ],
                ],
                tz=self.org.timezone,
            )

        assertReimport(export)

        # export a specified group of contacts (only Ben and Adam are in the group)
        with self.assertNumQueries(21):
            sheets, export = self._export(group1, with_groups=[group1])
            self.assertExcelSheet(
                sheets[0],
                [
                    [
                        "Contact UUID",
                        "Name",
                        "Language",
                        "Status",
                        "Created On",
                        "Last Seen On",
                        "URN:Mailto",
                        "URN:Tel",
                        "URN:Tel",
                        "URN:Telegram",
                        "URN:Twitter",
                        "Field:Third",
                        "Field:Second",
                        "Field:First",
                        "Group:Poppin Tags",
                    ],
                    [
                        contact.uuid,
                        "Ben Haggerty",
                        "",
                        "Active",
                        contact.created_on,
                        datetime(2020, 1, 1, 12, 0, 0, 0, tzinfo=tzone.utc),
                        "",
                        "+12067799294",
                        "+12062233445",
                        "",
                        "",
                        "20-12-2015 08:30",
                        "",
                        "One",
                        True,
                    ],
                    [
                        contact2.uuid,
                        "Adam Sumner",
                        "eng",
                        "Active",
                        contact2.created_on,
                        "",
                        "adam@sumner.com",
                        "+12067799191",
                        "",
                        "1234",
                        "adam",
                        "",
                        "",
                        "",
                        True,
                    ],
                ],
                tz=self.org.timezone,
            )

        assertReimport(export)

        contact5 = self.create_contact("George", urns=["tel:+1234567777"], status=Contact.STATUS_STOPPED)

        # export a specified status group of contacts (Stopped)
        sheets, export = self._export(self.org.groups.get(group_type="S"), with_groups=[group1])
        self.assertExcelSheet(
            sheets[0],
            [
                [
                    "Contact UUID",
                    "Name",
                    "Language",
                    "Status",
                    "Created On",
                    "Last Seen On",
                    "URN:Mailto",
                    "URN:Tel",
                    "URN:Tel",
                    "URN:Telegram",
                    "URN:Twitter",
                    "Field:Third",
                    "Field:Second",
                    "Field:First",
                    "Group:Poppin Tags",
                ],
                [
                    contact5.uuid,
                    "George",
                    "",
                    "Stopped",
                    contact5.created_on,
                    "",
                    "",
                    "1234567777",
                    "",
                    "",
                    "",
                    "",
                    "",
                    "",
                    False,
                ],
            ],
            tz=self.org.timezone,
        )

        # export a search
        mr_mocks.contact_export([contact2.id, contact3.id])
        with self.assertNumQueries(22):
            sheets, export = self._export(
                self.org.active_contacts_group, "name has adam or name has deng", with_groups=[group1]
            )
            self.assertExcelSheet(
                sheets[0],
                [
                    [
                        "Contact UUID",
                        "Name",
                        "Language",
                        "Status",
                        "Created On",
                        "Last Seen On",
                        "URN:Mailto",
                        "URN:Tel",
                        "URN:Tel",
                        "URN:Telegram",
                        "URN:Twitter",
                        "Field:Third",
                        "Field:Second",
                        "Field:First",
                        "Group:Poppin Tags",
                    ],
                    [
                        contact2.uuid,
                        "Adam Sumner",
                        "eng",
                        "Active",
                        contact2.created_on,
                        "",
                        "adam@sumner.com",
                        "+12067799191",
                        "",
                        "1234",
                        "adam",
                        "",
                        "",
                        "",
                        True,
                    ],
                    [
                        contact3.uuid,
                        "Luol Deng",
                        "",
                        "Active",
                        contact3.created_on,
                        "",
                        "",
                        "+12078776655",
                        "",
                        "",
                        "deng",
                        "",
                        "",
                        "",
                        False,
                    ],
                ],
                tz=self.org.timezone,
            )

            assertReimport(export)

        # export a search within a specified group of contacts
        mr_mocks.contact_export([contact.id])
        with self.assertNumQueries(20):
            sheets, export = self._export(group1, search="Hagg", with_groups=[group1])
            self.assertExcelSheet(
                sheets[0],
                [
                    [
                        "Contact UUID",
                        "Name",
                        "Language",
                        "Status",
                        "Created On",
                        "Last Seen On",
                        "URN:Mailto",
                        "URN:Tel",
                        "URN:Tel",
                        "URN:Telegram",
                        "URN:Twitter",
                        "Field:Third",
                        "Field:Second",
                        "Field:First",
                        "Group:Poppin Tags",
                    ],
                    [
                        contact.uuid,
                        "Ben Haggerty",
                        "",
                        "Active",
                        contact.created_on,
                        datetime(2020, 1, 1, 12, 0, 0, 0, tzinfo=tzone.utc),
                        "",
                        "+12067799294",
                        "+12062233445",
                        "",
                        "",
                        "20-12-2015 08:30",
                        "",
                        "One",
                        True,
                    ],
                ],
                tz=self.org.timezone,
            )

        assertReimport(export)

        # now try with an anonymous org
        with self.anonymous(self.org):
            sheets, export = self._export(self.org.active_contacts_group, with_groups=[group1])
            self.assertExcelSheet(
                sheets[0],
                [
                    [
                        "ID",
                        "Scheme",
                        "Contact UUID",
                        "Name",
                        "Language",
                        "Status",
                        "Created On",
                        "Last Seen On",
                        "Field:Third",
                        "Field:Second",
                        "Field:First",
                        "Group:Poppin Tags",
                    ],
                    [
                        str(contact.id),
                        "tel",
                        contact.uuid,
                        "Ben Haggerty",
                        "",
                        "Active",
                        contact.created_on,
                        datetime(2020, 1, 1, 12, 0, 0, 0, tzinfo=tzone.utc),
                        "20-12-2015 08:30",
                        "",
                        "One",
                        True,
                    ],
                    [
                        str(contact2.id),
                        "tel",
                        contact2.uuid,
                        "Adam Sumner",
                        "eng",
                        "Active",
                        contact2.created_on,
                        "",
                        "",
                        "",
                        "",
                        True,
                    ],
                    [
                        str(contact3.id),
                        "tel",
                        contact3.uuid,
                        "Luol Deng",
                        "",
                        "Active",
                        contact3.created_on,
                        "",
                        "",
                        "",
                        "",
                        False,
                    ],
                    [
                        str(contact4.id),
                        "tel",
                        contact4.uuid,
                        "Stephen",
                        "",
                        "Active",
                        contact4.created_on,
                        "",
                        "",
                        "",
                        "",
                        False,
                    ],
                ],
                tz=self.org.timezone,
            )
            assertReimport(export)


class FixStatusGroupNamesTest(MigrationTest):
    app = "contacts"
    migrate_from = "0192_alter_contactnote_text"
    migrate_to = "0193_fix_status_group_names"

    def setUpBeforeMigration(self, apps):
        # make org 1 look like an org with the old system groups
        self.org.groups.filter(group_type=ContactGroup.TYPE_DB_ACTIVE).update(name="Active")
        self.org.groups.filter(group_type=ContactGroup.TYPE_DB_BLOCKED).update(name="Blocked")
        self.org.groups.filter(group_type=ContactGroup.TYPE_DB_STOPPED).update(name="Stopped")
        self.org.groups.filter(group_type=ContactGroup.TYPE_DB_ARCHIVED).update(name="Archived")

        self.group1 = self.create_group("Active Contacts", contacts=[])

    def test_migration(self):
        self.assertEqual("\\Active", self.org.groups.get(group_type=ContactGroup.TYPE_DB_ACTIVE).name)
        self.assertEqual("\\Blocked", self.org.groups.get(group_type=ContactGroup.TYPE_DB_BLOCKED).name)
        self.assertEqual("\\Stopped", self.org.groups.get(group_type=ContactGroup.TYPE_DB_STOPPED).name)
        self.assertEqual("\\Archived", self.org.groups.get(group_type=ContactGroup.TYPE_DB_ARCHIVED).name)

        self.assertEqual("\\Active", self.org2.groups.get(group_type=ContactGroup.TYPE_DB_ACTIVE).name)
        self.assertEqual("\\Blocked", self.org2.groups.get(group_type=ContactGroup.TYPE_DB_BLOCKED).name)
        self.assertEqual("\\Stopped", self.org2.groups.get(group_type=ContactGroup.TYPE_DB_STOPPED).name)
        self.assertEqual("\\Archived", self.org2.groups.get(group_type=ContactGroup.TYPE_DB_ARCHIVED).name)

        # check user group unaffected
        self.group1.refresh_from_db()
        self.assertEqual("Active Contacts", self.group1.name)
