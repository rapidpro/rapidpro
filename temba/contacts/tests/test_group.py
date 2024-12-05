from datetime import timedelta

from django.urls import reverse
from django.utils import timezone

from temba import mailroom
from temba.campaigns.models import Campaign, CampaignEvent, EventFire
from temba.contacts.models import Contact, ContactField, ContactGroup, ContactGroupCount
from temba.contacts.tasks import squash_group_counts
from temba.schedules.models import Schedule
from temba.tests import TembaTest, mock_mailroom


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

        # squash all our counts, this shouldn't affect our overall counts, but we should now only have 3
        squash_group_counts()
        self.assertEqual(ContactGroupCount.objects.all().count(), 3)

        murdock.release(self.user)
        murdock.release(self.user)
        face.restore(self.user)
        face.restore(self.user)
        ba.restore(self.user)
        ba.restore(self.user)

        # squash again, this time we discard zero counts
        squash_group_counts()
        self.assertEqual(ContactGroupCount.objects.all().count(), 1)

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
