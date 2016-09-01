# coding=utf-8
from __future__ import unicode_literals

import json
import pytz

from datetime import datetime, date, timedelta
from django.core.files.base import ContentFile
from django.core.urlresolvers import reverse
from django.conf import settings
from django.utils import timezone
from mock import patch
from smartmin.models import SmartImportRowError
from smartmin.tests import _CRUDLTest
from smartmin.csv_imports.models import ImportTask
from temba.campaigns.models import Campaign, CampaignEvent, EventFire
from temba.channels.models import Channel, ChannelEvent
from temba.contacts.templatetags.contacts import contact_field, osm_link, location, media_url, media_type
from temba.flows.models import FlowRun
from temba.ivr.models import NO_ANSWER, IVRCall
from temba.locations.models import AdminBoundary
from temba.msgs.models import Msg, Label, SystemLabel, Broadcast
from temba.orgs.models import Org
from temba.schedules.models import Schedule
from temba.tests import AnonymousOrg, TembaTest
from temba.triggers.models import Trigger
from temba.utils import datetime_to_str, datetime_to_ms, get_datetime_format
from temba.values.models import Value
from xlrd import open_workbook
from .models import Contact, ContactGroup, ContactField, ContactURN, ExportContactsTask, URN, EXTERNAL_SCHEME
from .models import TEL_SCHEME, TWITTER_SCHEME, EMAIL_SCHEME, ContactGroupCount
from .tasks import squash_contactgroupcounts


class ContactCRUDLTest(_CRUDLTest):
    def setUp(self):
        from temba.contacts.views import ContactCRUDL
        super(ContactCRUDLTest, self).setUp()

        self.country = AdminBoundary.objects.create(osm_id='171496', name='Rwanda', level=0)
        AdminBoundary.objects.create(osm_id='1708283', name='Kigali', level=1, parent=self.country)

        self.crudl = ContactCRUDL
        self.user = self.create_user("tito")
        self.org = Org.objects.create(name="Nyaruka Ltd.", timezone="Africa/Kigali", country=self.country,
                                      created_by=self.user, modified_by=self.user)
        self.org.administrators.add(self.user)
        self.user.set_org(self.org)
        self.org.initialize()

        ContactField.get_or_create(self.org, self.user, 'age', "Age", value_type='N')
        ContactField.get_or_create(self.org, self.user, 'home', "Home", value_type='S')

    def getCreatePostData(self):
        return dict(name="Joe Brady", urn__tel__0="+250785551212")

    def getUpdatePostData(self):
        return dict(name="Joe Brady", urn__tel__0="+250785551212")

    def getTestObject(self):
        if self.object:
            return self.object

        if self.getCRUDL().permissions:
            self.login(self.getUser())

        # create our object
        create_page = reverse(self.getCRUDL().url_name_for_action('create'))
        post_data = self.getCreatePostData()
        self.client.post(create_page, data=post_data)

        # find our created object
        self.object = Contact.objects.get(org=self.org, urns__path=post_data['urn__tel__0'], name=post_data['name'])
        return self.object

    def testList(self):
        self.joe = Contact.get_or_create(self.org, self.user, name='Joe', urns=['tel:123'])
        self.joe.set_field(self.user, 'age', 20)
        self.joe.set_field(self.user, 'home', 'Kigali')
        self.frank = Contact.get_or_create(self.org, self.user, name='Frank', urns=['tel:124'])
        self.frank.set_field(self.user, 'age', 18)

        response = self._do_test_view('list')
        self.assertEqual([self.frank, self.joe], list(response.context['object_list']))

        response = self._do_test_view('list', query_string='search=age+%3D+18')
        self.assertEqual([self.frank], list(response.context['object_list']))

        response = self._do_test_view('list', query_string='search=age+>+18+and+home+%3D+"Kigali"')
        self.assertEqual([self.joe], list(response.context['object_list']))

    def testRead(self):
        self.joe = Contact.get_or_create(self.org, self.user, name='Joe', urns=['tel:123'])

        read_url = reverse('contacts.contact_read', args=[self.joe.uuid])
        response = self.client.get(read_url)
        self.assertRedirect(response, '/users/login/')

        self.login(self.user)
        response = self.client.get(read_url)
        self.assertContains(response, "Joe")

        # make sure the block link is present
        block_url = reverse('contacts.contact_block', args=[self.joe.id])
        self.assertContains(response, block_url)

        # and that it works
        self.client.post(block_url, dict(id=self.joe.id))
        self.assertTrue(Contact.objects.get(pk=self.joe.id, is_blocked=True))

        # try unblocking now
        response = self.client.get(read_url)
        unblock_url = reverse('contacts.contact_unblock', args=[self.joe.id])
        self.assertContains(response, unblock_url)

        self.client.post(unblock_url, dict(id=self.joe.id))
        self.assertTrue(Contact.objects.get(pk=self.joe.id, is_blocked=False))

        response = self.client.get(read_url)
        unstop_url = reverse('contacts.contact_unstop', args=[self.joe.id])
        self.assertFalse(unstop_url in response)

        # stop the contact
        self.joe.stop(self.user)
        self.assertFalse(Contact.objects.filter(pk=self.joe.id, is_stopped=False))
        response = self.client.get(read_url)
        self.assertContains(response, unstop_url)

        self.client.post(unstop_url, dict(id=self.joe.id))
        self.assertTrue(Contact.objects.filter(pk=self.joe.id, is_stopped=False))

        # ok, what about deleting?
        response = self.client.get(read_url)
        delete_url = reverse('contacts.contact_delete', args=[self.joe.id])
        self.assertContains(response, delete_url)

        self.client.post(delete_url, dict(id=self.joe.id))
        self.assertIsNotNone(Contact.objects.get(pk=self.joe.id, is_active=False))

        # can no longer access
        response = self.client.get(read_url)
        self.assertEquals(response.status_code, 404)

        # invalid uuid should return 404
        response = self.client.get(reverse('contacts.contact_read', args=['invalid-uuid']))
        self.assertEquals(response.status_code, 404)

    def testDelete(self):
        object = self.getTestObject()
        self._do_test_view('delete', object, post_data=dict())
        self.assertFalse(self.getCRUDL().model.objects.get(pk=object.pk).is_active)  # check object is inactive
        self.assertEqual(0, ContactURN.objects.filter(contact=object).count())  # check no attached URNs


class ContactGroupTest(TembaTest):
    def setUp(self):
        super(ContactGroupTest, self).setUp()

        self.joe = Contact.get_or_create(self.org, self.admin, name="Joe Blow", urns=["tel:123"])
        self.frank = Contact.get_or_create(self.org, self.admin, name="Frank Smith", urns=["tel:1234"])
        self.mary = Contact.get_or_create(self.org, self.admin, name="Mary Mo", urns=["tel:345"])

    def test_create_static(self):
        group = ContactGroup.create_static(self.org, self.admin, " group one ")

        self.assertEqual(group.org, self.org)
        self.assertEqual(group.name, "group one")
        self.assertEqual(group.created_by, self.admin)

        # can't call update_query on a static group
        self.assertRaises(ValueError, group.update_query, "gender=M")

        # exception if group name is blank
        self.assertRaises(ValueError, ContactGroup.create_static, self.org, self.admin, "   ")

    def test_create_dynamic(self):
        age = ContactField.get_or_create(self.org, self.admin, 'age', value_type=Value.TYPE_DECIMAL)
        gender = ContactField.get_or_create(self.org, self.admin, 'gender')
        self.joe.set_field(self.admin, 'age', 17)
        self.joe.set_field(self.admin, 'gender', "male")
        self.mary.set_field(self.admin, 'age', 21)
        self.mary.set_field(self.admin, 'gender', "female")

        group = ContactGroup.create_dynamic(self.org, self.admin, "Group two",
                                            '(age < 18 and gender = "male") or (age > 18 and gender = "female")')

        self.assertEqual(group.query, '(age < 18 and gender = "male") or (age > 18 and gender = "female")')
        self.assertEqual(set(group.query_fields.all()), {age, gender})
        self.assertEqual(set(group.contacts.all()), {self.joe, self.mary})

        # update group query
        group.update_query('age > 18')

        group.refresh_from_db()
        self.assertEqual(group.query, 'age > 18')
        self.assertEqual(set(group.query_fields.all()), {age})
        self.assertEqual(set(group.contacts.all()), {self.mary})

        # can't create a dynamic group with empty query
        self.assertRaises(ValueError, ContactGroup.create_dynamic, self.org, self.admin, "Empty", "")

        # can't call update_contacts on a dynamic group
        self.assertRaises(ValueError, group.update_contacts, self.admin, [self.joe], True)

        # dynamic group should not have remove to group button
        self.login(self.admin)
        filter_url = reverse('contacts.contact_filter', args=[group.pk])
        response = self.client.get(filter_url)
        self.assertFalse('unlabel' in response.context['actions'])

    def test_get_or_create(self):
        group = ContactGroup.get_or_create(self.org, self.user, " first ")
        self.assertEqual(group.name, "first")
        self.assertFalse(group.is_dynamic)

        # name look up is case insensitive
        self.assertEqual(ContactGroup.get_or_create(self.org, self.user, "  FIRST"), group)

        # fetching by id shouldn't modify original group
        self.assertEqual(ContactGroup.get_or_create(self.org, self.user, "Kigali", group.uuid), group)

        group.refresh_from_db()
        self.assertEqual(group.name, "first")

    def test_get_user_groups(self):
        static = ContactGroup.create_static(self.org, self.admin, "Static")
        dynamic = ContactGroup.create_dynamic(self.org, self.admin, "Dynamic", "gender=M")
        deleted = ContactGroup.create_static(self.org, self.admin, "Deleted")
        deleted.is_active = False
        deleted.save()

        self.assertEqual(set(ContactGroup.get_user_groups(self.org)), {static, dynamic})
        self.assertEqual(set(ContactGroup.get_user_groups(self.org, dynamic=False)), {static})
        self.assertEqual(set(ContactGroup.get_user_groups(self.org, dynamic=True)), {dynamic})

    def test_is_valid_name(self):
        self.assertTrue(ContactGroup.is_valid_name('x'))
        self.assertTrue(ContactGroup.is_valid_name('1'))
        self.assertTrue(ContactGroup.is_valid_name('x' * 64))
        self.assertFalse(ContactGroup.is_valid_name(' '))
        self.assertFalse(ContactGroup.is_valid_name(' x'))
        self.assertFalse(ContactGroup.is_valid_name('x '))
        self.assertFalse(ContactGroup.is_valid_name('+x'))
        self.assertFalse(ContactGroup.is_valid_name('@x'))
        self.assertFalse(ContactGroup.is_valid_name('x' * 65))

    def test_member_count(self):
        group = self.create_group("Cool kids")

        # add contacts via the related field
        group.contacts.add(self.joe, self.frank)

        self.assertEquals(ContactGroup.user_groups.get(pk=group.pk).get_member_count(), 2)

        # add contacts via update_contacts
        group.update_contacts(self.user, [self.mary], add=True)

        self.assertEquals(ContactGroup.user_groups.get(pk=group.pk).get_member_count(), 3)

        # remove contacts via update_contacts
        group.update_contacts(self.user, [self.mary], add=False)

        self.assertEquals(ContactGroup.user_groups.get(pk=group.pk).get_member_count(), 2)

        # add test contact (will add to group but won't increment count)
        test_contact = Contact.get_test_contact(self.admin)
        group.update_contacts(self.user, [test_contact], add=True)

        group = ContactGroup.user_groups.get(pk=group.pk)
        self.assertEquals(group.get_member_count(), 2)
        self.assertEquals(set(group.contacts.all()), {self.joe, self.frank, test_contact})

        # blocking a contact removes them from all user groups
        self.joe.block(self.user)

        with self.assertRaises(ValueError):
            group.update_contacts(self.user, [self.joe], True)

        group = ContactGroup.user_groups.get(pk=group.pk)
        self.assertEquals(group.get_member_count(), 1)
        self.assertEquals(set(group.contacts.all()), {self.frank, test_contact})

        # unblocking won't re-add to any groups
        self.joe.unblock(self.user)

        self.assertEquals(ContactGroup.user_groups.get(pk=group.pk).get_member_count(), 1)

        # releasing also removes from all user groups
        self.frank.release(self.user)

        group = ContactGroup.user_groups.get(pk=group.pk)
        self.assertEquals(group.get_member_count(), 0)
        self.assertEquals(set(group.contacts.all()), {test_contact})

    def test_system_group_counts(self):
        Contact.objects.all().delete()  # start with none

        counts = ContactGroup.get_system_group_counts(self.org)
        self.assertEqual(counts, {ContactGroup.TYPE_ALL: 0, ContactGroup.TYPE_BLOCKED: 0, ContactGroup.TYPE_STOPPED: 0})

        self.create_contact("Hannibal", number="0783835001")
        face = self.create_contact("Face", number="0783835002")
        ba = self.create_contact("B.A.", number="0783835003")
        murdock = self.create_contact("Murdock", number="0783835004")

        counts = ContactGroup.get_system_group_counts(self.org)
        self.assertEqual(counts, {ContactGroup.TYPE_ALL: 4, ContactGroup.TYPE_BLOCKED: 0, ContactGroup.TYPE_STOPPED: 0})

        # call methods twice to check counts don't change twice
        murdock.block(self.user)
        murdock.block(self.user)
        face.block(self.user)
        ba.stop(self.user)
        ba.stop(self.user)

        counts = ContactGroup.get_system_group_counts(self.org)
        self.assertEqual(counts, {ContactGroup.TYPE_ALL: 1, ContactGroup.TYPE_BLOCKED: 2, ContactGroup.TYPE_STOPPED: 1})

        murdock.release(self.user)
        murdock.release(self.user)
        face.unblock(self.user)
        face.unblock(self.user)
        ba.unstop(self.user)
        ba.unstop(self.user)

        # squash all our counts, this shouldn't affect our overall counts, but we should now only have 3
        squash_contactgroupcounts()
        self.assertEqual(ContactGroupCount.objects.all().count(), 3)

        counts = ContactGroup.get_system_group_counts(self.org)
        self.assertEqual(counts, {ContactGroup.TYPE_ALL: 3, ContactGroup.TYPE_BLOCKED: 0, ContactGroup.TYPE_STOPPED: 0})

        # rebuild just our system contact group
        all_contacts = ContactGroup.all_groups.get(org=self.org, group_type=ContactGroup.TYPE_ALL)
        ContactGroupCount.populate_for_group(all_contacts)

        # assert our count is correct
        self.assertEqual(all_contacts.get_member_count(), 3)
        self.assertEqual(ContactGroupCount.objects.filter(group=all_contacts).count(), 1)

    def test_delete(self):
        group = self.create_group("one")
        flow = self.get_flow('favorites')

        self.login(self.admin)

        response = self.client.post(reverse('contacts.contactgroup_delete', args=[group.pk]), dict())
        self.assertIsNone(ContactGroup.user_groups.filter(pk=group.pk).first())
        self.assertFalse(ContactGroup.all_groups.get(pk=group.pk).is_active)

        group = self.create_group("one")
        delete_url = reverse('contacts.contactgroup_delete', args=[group.pk])

        trigger = Trigger.objects.create(org=self.org, flow=flow, keyword="join", created_by=self.admin, modified_by=self.admin)
        trigger.groups.add(group)

        second_trigger = Trigger.objects.create(org=self.org, flow=flow, keyword="register", created_by=self.admin, modified_by=self.admin)
        second_trigger.groups.add(group)

        response = self.client.post(delete_url, dict())
        self.assertEquals(302, response.status_code)
        response = self.client.post(delete_url, dict(), follow=True)
        self.assertTrue(ContactGroup.user_groups.get(pk=group.pk).is_active)
        self.assertEquals(response.request['PATH_INFO'], reverse('contacts.contact_filter', args=[group.pk]))

        # archive a trigger
        second_trigger.is_archived = True
        second_trigger.save()

        response = self.client.post(delete_url, dict())
        self.assertEquals(302, response.status_code)
        response = self.client.post(delete_url, dict(), follow=True)
        self.assertTrue(ContactGroup.user_groups.get(pk=group.pk).is_active)
        self.assertEquals(response.request['PATH_INFO'], reverse('contacts.contact_filter', args=[group.pk]))

        trigger.is_archived = True
        trigger.save()

        response = self.client.post(delete_url, dict())
        # group should have is_active = False and all its triggers
        self.assertIsNone(ContactGroup.user_groups.filter(pk=group.pk).first())
        self.assertFalse(ContactGroup.all_groups.get(pk=group.pk).is_active)
        self.assertFalse(Trigger.objects.get(pk=trigger.pk).is_active)
        self.assertFalse(Trigger.objects.get(pk=second_trigger.pk).is_active)


class ContactGroupCRUDLTest(TembaTest):
    def setUp(self):
        super(ContactGroupCRUDLTest, self).setUp()

        self.joe = Contact.get_or_create(self.org, self.user, name="Joe Blow", urns=["tel:123"])
        self.frank = Contact.get_or_create(self.org, self.user, name="Frank Smith", urns=["tel:1234"])

        self.joe_and_frank = self.create_group("Customers", [self.joe, self.frank])
        self.dynamic_group = self.create_group("Dynamic", query="joe")

    def test_create(self):
        url = reverse('contacts.contactgroup_create')

        # can't create group as viewer
        self.login(self.user)
        response = self.client.post(url, dict(name="Spammers"))
        self.assertLoginRedirect(response)

        self.login(self.admin)

        # try to create a contact group whose name is only whitespace
        response = self.client.post(url, dict(name="  "))
        self.assertFormError(response, 'form', 'name', "Group name must not be blank or begin with + or -")

        # try to create a contact group whose name begins with reserved character
        response = self.client.post(url, dict(name="+People"))
        self.assertFormError(response, 'form', 'name', "Group name must not be blank or begin with + or -")

        # try to create with name that's already taken
        response = self.client.post(url, dict(name="Customers"))
        self.assertFormError(response, 'form', 'name', "Name is used by another group")

        # create with valid name (that will be trimmed)
        response = self.client.post(url, dict(name="first  "))
        self.assertNoFormErrors(response)
        ContactGroup.user_groups.get(org=self.org, name="first")

        # create a group with preselected contacts
        self.client.post(url, dict(name="Everybody", preselected_contacts='%d,%d' % (self.joe.pk, self.frank.pk)))
        group = ContactGroup.user_groups.get(org=self.org, name="Everybody")
        self.assertEqual(set(group.contacts.all()), {self.joe, self.frank})

        # create a dynamic group using a query
        self.client.post(url, dict(name="Frank", group_query="frank"))
        group = ContactGroup.user_groups.get(org=self.org, name="Frank", query="frank")
        self.assertEqual(set(group.contacts.all()), {self.frank})

    def test_update(self):
        url = reverse('contacts.contactgroup_update', args=[self.joe_and_frank.pk])

        # can't create group as viewer
        self.login(self.user)
        response = self.client.post(url, dict(name="Spammers"))
        self.assertLoginRedirect(response)

        self.login(self.admin)

        # try to update name to only whitespace
        response = self.client.post(url, dict(name="   "))
        self.assertFormError(response, 'form', 'name', "Group name must not be blank or begin with + or -")

        # try to update name to start with reserved character
        response = self.client.post(url, dict(name="+People"))
        self.assertFormError(response, 'form', 'name', "Group name must not be blank or begin with + or -")

        # update with valid name (that will be trimmed)
        response = self.client.post(url, dict(name="new name   "))
        self.assertNoFormErrors(response)

        self.joe_and_frank.refresh_from_db()
        self.assertEqual(self.joe_and_frank.name, "new name")

        # now try a dynamic group
        url = reverse('contacts.contactgroup_update', args=[self.dynamic_group.pk])

        # update both name and query
        self.client.post(url, dict(name='Frank', query='frank'))
        self.assertNoFormErrors(response)

        self.dynamic_group.refresh_from_db()
        self.assertEqual(self.dynamic_group.name, "Frank")
        self.assertEqual(self.dynamic_group.query, "frank")
        self.assertEqual(set(self.dynamic_group.contacts.all()), {self.frank})

    def test_delete(self):
        url = reverse('contacts.contactgroup_delete', args=[self.joe_and_frank.pk])

        # can't delete group as viewer
        self.login(self.user)
        response = self.client.post(url)
        self.assertLoginRedirect(response)

        # can as admin user
        self.login(self.admin)
        response = self.client.post(url)
        self.assertRedirect(response, reverse('contacts.contact_list'))

        self.joe_and_frank.refresh_from_db()
        self.assertFalse(self.joe_and_frank.is_active)
        self.assertFalse(self.joe_and_frank.contacts.all())


class ContactTest(TembaTest):
    def setUp(self):
        super(ContactTest, self).setUp()

        self.user1 = self.create_user("nash")
        self.manager1 = self.create_user("mike")

        self.joe = self.create_contact(name="Joe Blow", number="123", twitter="blow80")
        self.frank = self.create_contact(name="Frank Smith", number="123222")
        self.billy = self.create_contact(name="Billy Nophone")
        self.voldemort = self.create_contact(number="+250788383383")

        # create an orphaned URN
        ContactURN.objects.create(org=self.org, scheme='tel', path='8888', urn='tel:8888', priority=50)

        # create an deleted contact
        self.jim = self.create_contact(name="Jim")
        self.jim.release(self.user)

    def create_campaign(self):
        # create a campaign with a future event and add joe
        self.farmers = self.create_group("Farmers", [self.joe])
        self.reminder_flow = self.create_flow()
        self.planting_date = ContactField.get_or_create(self.org, self.admin, 'planting_date', "Planting Date")
        self.campaign = Campaign.create(self.org, self.admin, "Planting Reminders", self.farmers)

        # create af flow event
        self.planting_reminder = CampaignEvent.create_flow_event(self.org, self.admin, self.campaign,
                                                                 relative_to=self.planting_date, offset=0, unit='D',
                                                                 flow=self.reminder_flow, delivery_hour=17)

        # and a message event
        self.message_event = CampaignEvent.create_message_event(self.org, self.admin, self.campaign,
                                                                relative_to=self.planting_date, offset=7, unit='D',
                                                                message='Sent 7 days after planting date')

    def test_get_or_create(self):

        # can't create without org or user
        with self.assertRaises(ValueError):
            Contact.get_or_create(None, None, name='Joe', urns=['tel:123'])

        # incoming channel with no urns
        with self.assertRaises(ValueError):
            Contact.get_or_create(self.org, self.user, channel=self.channel, name='Joe', urns=None)

        # incoming channel with two urns
        with self.assertRaises(ValueError):
            Contact.get_or_create(self.org, self.user, channel=self.channel, name='Joe', urns=['tel:123', 'tel:456'])

        # missing scheme
        with self.assertRaises(ValueError):
            Contact.get_or_create(self.org, self.user, name='Joe', urns=[':123'])

        # missing path
        with self.assertRaises(ValueError):
            Contact.get_or_create(self.org, self.user, name='Joe', urns=['tel:'])

        # name too long gets truncated
        contact = Contact.get_or_create(self.org, self.user, name='Roger ' + 'xxxxx' * 100)
        self.assertEqual(len(contact.name), 128)

        # create a contact with name, phone number and language
        joe = Contact.get_or_create(self.org, self.user, name="Joe", urns=['tel:0783835665'], language='fre')
        self.assertEqual(joe.org, self.org)
        self.assertEqual(joe.name, "Joe")
        self.assertEqual(joe.language, 'fre')

        # calling again with same URN updates and returns existing contact
        contact = Contact.get_or_create(self.org, self.user, name="Joey", urns=['tel:+250783835665'], language='eng')
        self.assertEqual(contact, joe)
        self.assertEqual(contact.name, "Joey")
        self.assertEqual(contact.language, 'eng')

        # create a URN-less contact and try to update them with a taken URN
        snoop = Contact.get_or_create(self.org, self.user, name='Snoop')
        with self.assertRaises(ValueError):
            Contact.get_or_create(self.org, self.user, uuid=snoop.uuid, urns=['tel:123'])

        # now give snoop his own urn
        Contact.get_or_create(self.org, self.user, uuid=snoop.uuid, urns=['tel:456'])

        self.assertIsNone(snoop.urns.all().first().channel)
        snoop = Contact.get_or_create(self.org, self.user, channel=self.channel, urns=['tel:456'])
        self.assertEquals(1, snoop.urns.all().count())

    def test_get_test_contact(self):
        test_contact_admin = Contact.get_test_contact(self.admin)
        self.assertTrue(test_contact_admin.is_test)
        self.assertEquals(test_contact_admin.created_by, self.admin)

        test_contact_user = Contact.get_test_contact(self.user)
        self.assertTrue(test_contact_user.is_test)
        self.assertEquals(test_contact_user.created_by, self.user)
        self.assertFalse(test_contact_admin == test_contact_user)

        test_contact_user2 = Contact.get_test_contact(self.user)
        self.assertTrue(test_contact_user2.is_test)
        self.assertEquals(test_contact_user2.created_by, self.user)
        self.assertTrue(test_contact_user2 == test_contact_user)

        # assign this URN to another contact
        other_contact = Contact.get_or_create(self.org, self.admin)
        test_urn = test_contact_user2.get_urn(TEL_SCHEME)
        test_urn.contact = other_contact
        test_urn.save()

        # fetching the test contact again should get us a new URN
        new_test_contact = Contact.get_test_contact(self.user)
        self.assertNotEqual(new_test_contact.get_urn(TEL_SCHEME), test_urn)

    def test_contact_create(self):
        self.login(self.admin)

        # try creating a contact with a number that belongs to another contact
        response = self.client.post(reverse('contacts.contact_create'), data=dict(name='Ben Haggerty', urn__tel__0="123"))
        self.assertEquals(1, len(response.context['form'].errors))

        # now repost with a unique phone number
        response = self.client.post(reverse('contacts.contact_create'), data=dict(name='Ben Haggerty', urn__tel__0="123-456"))
        self.assertNoFormErrors(response)

        # repost with the phone number of an orphaned URN
        response = self.client.post(reverse('contacts.contact_create'), data=dict(name='Ben Haggerty', urn__tel__0="8888"))
        self.assertNoFormErrors(response)

        # check that the orphaned URN has been associated with the contact
        self.assertEqual('Ben Haggerty', Contact.from_urn(self.org, 'tel:8888').name)

    def test_fail_and_block_and_release(self):
        msg1 = self.create_msg(text="Test 1", direction='I', contact=self.joe, msg_type='I', status='H')
        msg2 = self.create_msg(text="Test 2", direction='I', contact=self.joe, msg_type='F', status='H')
        msg3 = self.create_msg(text="Test 3", direction='I', contact=self.joe, msg_type='I', status='H', visibility='A')
        label = Label.get_or_create(self.org, self.user, "Interesting")
        label.toggle_label([msg1, msg2, msg3], add=True)
        static_group = self.create_group("Just Joe", [self.joe])

        # create a dynamic group and put joe in it
        ContactField.get_or_create(self.org, self.admin, 'gender', "Gender")
        dynamic_group = self.create_group("Dynamic", query="gender is M")
        self.joe.set_field(self.admin, 'gender', "M")
        self.assertEqual(set(dynamic_group.contacts.all()), {self.joe})

        self.clear_cache()

        msg_counts = SystemLabel.get_counts(self.org)
        self.assertEqual(1, msg_counts[SystemLabel.TYPE_INBOX])
        self.assertEqual(1, msg_counts[SystemLabel.TYPE_FLOWS])
        self.assertEqual(1, msg_counts[SystemLabel.TYPE_ARCHIVED])

        contact_counts = ContactGroup.get_system_group_counts(self.org)
        self.assertEqual(contact_counts, {ContactGroup.TYPE_ALL: 4,
                                          ContactGroup.TYPE_BLOCKED: 0,
                                          ContactGroup.TYPE_STOPPED: 0})

        self.assertEqual(set(label.msgs.all()), {msg1, msg2, msg3})
        self.assertEqual(set(static_group.contacts.all()), {self.joe})
        self.assertEqual(set(dynamic_group.contacts.all()), {self.joe})

        self.joe.stop(self.user)

        # check that joe is now stopped
        self.joe = Contact.objects.get(pk=self.joe.pk)
        self.assertTrue(self.joe.is_stopped)
        self.assertFalse(self.joe.is_blocked)
        self.assertTrue(self.joe.is_active)

        # and added to stopped group
        contact_counts = ContactGroup.get_system_group_counts(self.org)
        self.assertEqual(contact_counts, {ContactGroup.TYPE_ALL: 3,
                                          ContactGroup.TYPE_BLOCKED: 0,
                                          ContactGroup.TYPE_STOPPED: 1})
        self.assertEqual(set(static_group.contacts.all()), set())
        self.assertEqual(set(dynamic_group.contacts.all()), set())

        self.joe.block(self.user)

        # check that joe is now blocked and stopped
        self.joe = Contact.objects.get(pk=self.joe.pk)
        self.assertTrue(self.joe.is_stopped)
        self.assertTrue(self.joe.is_blocked)
        self.assertTrue(self.joe.is_active)

        # and that he's been removed from the all and failed groups, and added to the blocked group
        contact_counts = ContactGroup.get_system_group_counts(self.org)
        self.assertEqual(contact_counts, {ContactGroup.TYPE_ALL: 3,
                                          ContactGroup.TYPE_BLOCKED: 1,
                                          ContactGroup.TYPE_STOPPED: 1})

        # and removed from all groups
        self.assertEqual(set(static_group.contacts.all()), set())
        self.assertEqual(set(dynamic_group.contacts.all()), set())

        # but his messages are unchanged
        self.assertEqual(2, Msg.all_messages.filter(contact=self.joe, visibility='V').count())
        msg_counts = SystemLabel.get_counts(self.org)
        self.assertEqual(1, msg_counts[SystemLabel.TYPE_INBOX])
        self.assertEqual(1, msg_counts[SystemLabel.TYPE_FLOWS])
        self.assertEqual(1, msg_counts[SystemLabel.TYPE_ARCHIVED])

        self.joe.unblock(self.user)

        # check that joe is now unblocked but still stopped
        self.joe = Contact.objects.get(pk=self.joe.pk)
        self.assertTrue(self.joe.is_stopped)
        self.assertFalse(self.joe.is_blocked)
        self.assertTrue(self.joe.is_active)

        # and that he's been removed from the blocked group, and put back in the all and failed groups
        contact_counts = ContactGroup.get_system_group_counts(self.org)
        self.assertEqual(contact_counts, {ContactGroup.TYPE_ALL: 3,
                                          ContactGroup.TYPE_BLOCKED: 0,
                                          ContactGroup.TYPE_STOPPED: 1})

        # he should be back in the dynamic group
        self.assertEqual(set(static_group.contacts.all()), set())
        self.assertEqual(set(dynamic_group.contacts.all()), set())

        self.joe.unstop(self.user)

        # check that joe is now no longer failed
        self.joe = Contact.objects.get(pk=self.joe.pk)
        self.assertFalse(self.joe.is_stopped)
        self.assertFalse(self.joe.is_blocked)
        self.assertTrue(self.joe.is_active)

        # and that he's been removed from the stopped group
        contact_counts = ContactGroup.get_system_group_counts(self.org)
        self.assertEqual(contact_counts, {ContactGroup.TYPE_ALL: 4,
                                          ContactGroup.TYPE_BLOCKED: 0,
                                          ContactGroup.TYPE_STOPPED: 0})

        # back in the dynamic group
        self.assertEqual(set(static_group.contacts.all()), set())
        self.assertEqual(set(dynamic_group.contacts.all()), {self.joe})

        self.joe.release(self.user)

        # check that joe is no longer active
        self.joe = Contact.objects.get(pk=self.joe.pk)
        self.assertFalse(self.joe.is_stopped)
        self.assertFalse(self.joe.is_blocked)
        self.assertFalse(self.joe.is_active)

        contact_counts = ContactGroup.get_system_group_counts(self.org)
        self.assertEqual(contact_counts, {ContactGroup.TYPE_ALL: 3,
                                          ContactGroup.TYPE_BLOCKED: 0,
                                          ContactGroup.TYPE_STOPPED: 0})

        # joe's messages should be inactive, blank and have no labels
        self.assertEqual(0, Msg.all_messages.filter(contact=self.joe, visibility='V').count())
        self.assertEqual(0, Msg.all_messages.filter(contact=self.joe).exclude(text="").count())
        self.assertEqual(0, Label.label_objects.get(pk=label.pk).msgs.count())

        msg_counts = SystemLabel.get_counts(self.org)
        self.assertEqual(0, msg_counts[SystemLabel.TYPE_INBOX])
        self.assertEqual(0, msg_counts[SystemLabel.TYPE_FLOWS])
        self.assertEqual(0, msg_counts[SystemLabel.TYPE_ARCHIVED])

        # and he shouldn't be in any groups
        self.assertEqual(set(static_group.contacts.all()), set())
        self.assertEqual(set(dynamic_group.contacts.all()), set())

        # or have any URNs
        self.assertEqual(0, ContactURN.objects.filter(contact=self.joe).count())

        # blocking and failing an inactive contact won't change groups
        self.joe.block(self.user)
        self.joe.stop(self.user)

        contact_counts = ContactGroup.get_system_group_counts(self.org)
        self.assertEqual(contact_counts, {ContactGroup.TYPE_ALL: 3,
                                          ContactGroup.TYPE_BLOCKED: 0,
                                          ContactGroup.TYPE_STOPPED: 0})

        # we don't let users undo releasing a contact... but if we have to do it for some reason
        self.joe.is_active = True
        self.joe.save()

        # check joe goes into the appropriate groups
        contact_counts = ContactGroup.get_system_group_counts(self.org)
        self.assertEqual(contact_counts, {ContactGroup.TYPE_ALL: 3,
                                          ContactGroup.TYPE_BLOCKED: 1,
                                          ContactGroup.TYPE_STOPPED: 1})

        # don't allow blocking or failing of test contacts
        test_contact = Contact.get_test_contact(self.user)
        self.assertRaises(ValueError, test_contact.block, self.user)
        self.assertRaises(ValueError, test_contact.stop, self.user)

    def test_user_groups(self):
        # create some static groups
        spammers = self.create_group("Spammers", [])
        testers = self.create_group("Testers", [])

        # create a dynamic group and put joe in it
        ContactField.get_or_create(self.org, self.admin, 'gender', "Gender")
        dynamic = self.create_group("Dynamic", query="gender is M")
        self.joe.set_field(self.admin, 'gender', "M")
        self.assertEqual(set(dynamic.contacts.all()), {self.joe})

        self.joe.update_static_groups(self.user, [spammers, testers])
        self.assertEqual(set(self.joe.user_groups.all()), {spammers, testers, dynamic})

        self.joe.update_static_groups(self.user, [])
        self.assertEqual(set(self.joe.user_groups.all()), {dynamic})

        self.joe.update_static_groups(self.user, [testers])
        self.assertEqual(set(self.joe.user_groups.all()), {testers, dynamic})

        # blocking removes contact from all groups
        self.joe.block(self.user)
        self.assertEqual(set(self.joe.user_groups.all()), set())

        # can't add blocked contacts to a group
        self.assertRaises(ValueError, self.joe.update_static_groups, self.user, [spammers])

        # unblocking potentially puts contact back in dynamic groups
        self.joe.unblock(self.user)
        self.assertEqual(set(self.joe.user_groups.all()), {dynamic})

        self.joe.update_static_groups(self.user, [testers])

        # stopping removes people from groups
        self.joe.stop(self.admin)
        self.assertEqual(set(self.joe.user_groups.all()), set())

        # and unstopping potentially puts contact back in dynamic groups
        self.joe.unstop(self.admin)
        self.assertEqual(set(self.joe.user_groups.all()), {dynamic})

        self.joe.update_static_groups(self.user, [testers])

        # releasing removes contacts from all groups
        self.joe.release(self.user)
        self.assertEqual(set(self.joe.user_groups.all()), set())

        # can't add deleted contacts to a group
        self.assertRaises(ValueError, self.joe.update_static_groups, self.user, [spammers])

    def test_contact_display(self):
        mr_long_name = self.create_contact(name="Wolfeschlegelsteinhausenbergerdorff", number="8877")

        self.assertEqual("Joe Blow", self.joe.get_display(org=self.org, full=True))
        self.assertEqual("Joe Blow", self.joe.get_display(short=True))
        self.assertEqual("Joe Blow", self.joe.get_display())
        self.assertEqual("+250788383383", self.voldemort.get_display(org=self.org, full=True))
        self.assertEqual("0788 383 383", self.voldemort.get_display())
        self.assertEqual("Wolfeschlegelsteinhausenbergerdorff", mr_long_name.get_display())
        self.assertEqual("Wolfeschlegelstei...", mr_long_name.get_display(short=True))
        self.assertEqual("Billy Nophone", self.billy.get_display())

        self.assertEqual("123", self.joe.get_urn_display(scheme=TEL_SCHEME))
        self.assertEqual("blow80", self.joe.get_urn_display(org=self.org, full=True))
        self.assertEqual("blow80", self.joe.get_urn_display())
        self.assertEqual("+250788383383", self.voldemort.get_urn_display(org=self.org, full=True))
        self.assertEqual("0788 383 383", self.voldemort.get_urn_display())
        self.assertEqual("8877", mr_long_name.get_urn_display())
        self.assertEqual("", self.billy.get_urn_display())

        self.assertEqual("Joe Blow", self.joe.__unicode__())
        self.assertEqual("0788 383 383", self.voldemort.__unicode__())
        self.assertEqual("Wolfeschlegelsteinhausenbergerdorff", mr_long_name.__unicode__())
        self.assertEqual("Billy Nophone", self.billy.__unicode__())

        with AnonymousOrg(self.org):
            self.assertEqual("Joe Blow", self.joe.get_display(org=self.org, full=True))
            self.assertEqual("Joe Blow", self.joe.get_display(short=True))
            self.assertEqual("Joe Blow", self.joe.get_display())
            self.assertEqual("%010d" % self.voldemort.pk, self.voldemort.get_display())
            self.assertEqual("Wolfeschlegelsteinhausenbergerdorff", mr_long_name.get_display())
            self.assertEqual("Wolfeschlegelstei...", mr_long_name.get_display(short=True))
            self.assertEqual("Billy Nophone", self.billy.get_display())

            self.assertEqual(self.joe.anon_identifier, self.joe.get_urn_display(org=self.org, full=True))
            self.assertEqual(self.joe.anon_identifier, self.joe.get_urn_display())
            self.assertEqual(self.voldemort.anon_identifier, self.voldemort.get_urn_display())
            self.assertEqual(mr_long_name.anon_identifier, mr_long_name.get_urn_display())
            self.assertEqual(self.billy.anon_identifier, self.billy.get_urn_display())

            self.assertEqual("Joe Blow", self.joe.__unicode__())
            self.assertEqual("%010d" % self.voldemort.pk, self.voldemort.__unicode__())
            self.assertEqual("Wolfeschlegelsteinhausenbergerdorff", mr_long_name.__unicode__())
            self.assertEqual("Billy Nophone", self.billy.__unicode__())

    def test_bulk_cache_initialize(self):
        ContactField.get_or_create(self.org, self.admin, 'age', "Age", value_type='N', show_in_table=True)
        ContactField.get_or_create(self.org, self.admin, 'nick', "Nickname", value_type='T', show_in_table=False)

        self.joe.set_field(self.user, 'age', 32)
        self.joe.set_field(self.user, 'nick', 'Joey')
        self.joe = Contact.objects.get(pk=self.joe.pk)

        # check no caches
        self.assertFalse(hasattr(self.joe, '__urns') or hasattr(self.joe, '__field__age'))
        self.assertFalse(hasattr(self.frank, '__urns') or hasattr(self.frank, '__field__age'))
        self.assertFalse(hasattr(self.billy, '__urns') or hasattr(self.billy, '__field__age'))

        self.billy = Contact.objects.get(pk=self.billy.pk)

        all = (self.joe, self.frank, self.billy)
        Contact.bulk_cache_initialize(self.org, all, for_show_only=True)

        self.assertEqual([u.scheme for u in getattr(self.joe, '__urns')], [TWITTER_SCHEME, TEL_SCHEME])
        self.assertEqual([u.scheme for u in getattr(self.frank, '__urns')], [TEL_SCHEME])
        self.assertEqual(getattr(self.billy, '__urns'), list())

        self.assertEqual(getattr(self.joe, '__field__age').decimal_value, 32)
        self.assertIsNone(getattr(self.frank, '__field__age'))
        self.assertIsNone(getattr(self.billy, '__field__age'))
        self.assertFalse(hasattr(self.joe, '__field__nick'))
        self.assertFalse(hasattr(self.frank, '__field__nick'))
        self.assertFalse(hasattr(self.billy, '__field__nick'))

        Contact.bulk_cache_initialize(self.org, all)

        self.assertEqual(getattr(self.joe, '__field__age').decimal_value, 32)
        self.assertIsNone(getattr(self.frank, '__field__age'))
        self.assertIsNone(getattr(self.billy, '__field__age'))
        self.assertEqual(getattr(self.joe, '__field__nick').string_value, 'Joey')
        self.assertIsNone(getattr(self.frank, '__field__nick'))
        self.assertIsNone(getattr(self.billy, '__field__nick'))

    def test_contact_search(self):
        self.login(self.admin)

        # block the default contacts, these should be ignored in our searches
        Contact.objects.all().update(is_active=False, is_blocked=True)

        ContactField.get_or_create(self.org, self.admin, 'age', "Age", value_type='N')
        ContactField.get_or_create(self.org, self.admin, 'join_date', "Join Date", value_type='D')
        ContactField.get_or_create(self.org, self.admin, 'state', "Home State", value_type='S')
        ContactField.get_or_create(self.org, self.admin, 'home', "Home District", value_type='I')
        ContactField.get_or_create(self.org, self.admin, 'ward', "Home Ward", value_type='W')
        ContactField.get_or_create(self.org, self.admin, 'profession', "Profession", value_type='T')
        ContactField.get_or_create(self.org, self.admin, 'isureporter', "Is UReporter", value_type='T')
        ContactField.get_or_create(self.org, self.admin, 'hasbirth', "Has Birth", value_type='T')

        names = ['Trey', 'Mike', 'Paige', 'Fish']
        districts = ['Gatsibo', 'Kayônza', 'Rwamagana']
        wards = ['Kageyo', 'Kabara', 'Bukure']
        date_format = get_datetime_format(True)[0]

        # create some contacts
        for i in range(10, 100):
            name = names[(i + 2) % len(names)]
            number = "0788382%s" % str(i).zfill(3)
            twitter = "tweep_%d" % (i + 1)
            contact = self.create_contact(name=name, number=number, twitter=twitter)
            join_date = datetime_to_str(date(2013, 12, 22) + timezone.timedelta(days=i), date_format)

            # some field data so we can do some querying
            contact.set_field(self.user, 'age', '%s' % i)
            contact.set_field(self.user, 'join_date', '%s' % join_date)
            contact.set_field(self.user, 'state', "Eastern Province")
            contact.set_field(self.user, 'home', districts[i % len(districts)])
            contact.set_field(self.user, 'ward', wards[i % len(wards)])

            if i % 3 == 0:
                contact.set_field(self.user, 'profession', "Farmer")  # only some contacts have any value for this

            contact.set_field(self.user, 'isureporter', 'yes')
            contact.set_field(self.user, 'hasbirth', 'no')

        def q(query):
            return Contact.search(self.org, query)[0].count()

        # non-complex queries
        self.assertEqual(q('trey'), 23)
        self.assertEqual(q('MIKE'), 23)
        self.assertEqual(q('  paige  '), 22)
        self.assertEqual(q('fish'), 22)
        self.assertEqual(q('0788382011'), 1)  # does a contains

        # name as property
        self.assertEqual(q('name is "trey"'), 23)
        self.assertEqual(q('name is mike'), 23)
        self.assertEqual(q('name = paige'), 22)
        self.assertEqual(q('NAME=fish'), 22)
        self.assertEqual(q('name has e'), 68)

        # URN as property
        self.assertEqual(q('tel is +250788382011'), 1)
        self.assertEqual(q('tel has 0788382011'), 1)
        self.assertEqual(q('twitter = tweep_12'), 1)
        self.assertEqual(q('TWITTER has tweep'), 90)

        # contact field as property
        self.assertEqual(q('age > 30'), 69)
        self.assertEqual(q('age >= 30'), 70)
        self.assertEqual(q('age > 30 and age <= 40'), 10)
        self.assertEqual(q('AGE < 20'), 10)

        self.assertEqual(q('join_date = 1-1-14'), 1)
        self.assertEqual(q('join_date < 30/1/2014'), 29)
        self.assertEqual(q('join_date <= 30/1/2014'), 30)
        self.assertEqual(q('join_date > 30/1/2014'), 60)
        self.assertEqual(q('join_date >= 30/1/2014'), 61)
        self.assertEqual(q('join_date >= xxxx'), 0)  # invalid date

        self.assertEqual(q('state is "Eastern Province"'), 90)
        self.assertEqual(q('HOME is Kayônza'), 30)  # value with non-ascii character
        self.assertEqual(q('ward is kageyo'), 30)
        self.assertEqual(q('home has ga'), 60)

        self.assertEqual(q('home is ""'), 0)
        self.assertEqual(q('profession = ""'), 60)

        # contact fields beginning with 'is' or 'has'
        self.assertEqual(q('isureporter = "yes"'), 90)
        self.assertEqual(q('isureporter = yes'), 90)
        self.assertEqual(q('isureporter = no'), 0)

        self.assertEqual(q('hasbirth = "no"'), 90)
        self.assertEqual(q('hasbirth = no'), 90)
        self.assertEqual(q('hasbirth = yes'), 0)

        # boolean combinations
        self.assertEqual(q('name is trey or name is mike'), 46)
        self.assertEqual(q('name is trey and age < 20'), 3)
        self.assertEqual(q('(home is gatsibo or home is "Rwamagana")'), 60)
        self.assertEqual(q('(home is gatsibo or home is "Rwamagana") and name is mike'), 16)
        self.assertEqual(q('name is MIKE and profession = ""'), 15)

        # invalid queries - which revert to simple name/phone matches
        self.assertEqual(q('(('), 0)
        self.assertEqual(q('name = "trey'), 0)

        # create contact with no phone number, we'll try searching for it by id
        contact = self.create_contact(name="Id Contact")

        # non-anon orgs can't search by id (because they never see ids)
        self.assertFalse(contact in Contact.search(self.org, '%d' % contact.pk)[0])  # others may match by id on tel

        with AnonymousOrg(self.org):
            # still allow name and field searches
            self.assertEqual(q('trey'), 23)
            self.assertEqual(q('name is mike'), 23)
            self.assertEqual(q('age > 30'), 69)

            # don't allow matching on URNs
            self.assertEqual(q('0788382011'), 0)
            self.assertEqual(q('tel is +250788382011'), 0)
            self.assertEqual(q('twitter has blow'), 0)

            # anon orgs can search by id, with or without zero padding
            self.assertTrue(contact in Contact.search(self.org, '%d' % contact.pk)[0])
            self.assertTrue(contact in Contact.search(self.org, '%010d' % contact.pk)[0])

        # syntactically invalid queries should return no results
        self.assertEqual(q('name > trey'), 0)  # unrecognized non-field operator
        self.assertEqual(q('profession > trey'), 0)  # unrecognized text-field operator
        self.assertEqual(q('age has 4'), 0)  # unrecognized decimal-field operator
        self.assertEqual(q('age = x'), 0)  # unparseable decimal-field comparison
        self.assertEqual(q('join_date has 30/1/2014'), 0)  # unrecognized date-field operator
        self.assertEqual(q('join_date > xxxxx'), 0)  # unparseable date-field comparison
        self.assertEqual(q('home > kigali'), 0)  # unrecognized location-field operator

    def test_omnibox(self):
        # add a group with members and an empty group
        joe_and_frank = self.create_group("Joe and Frank", [self.joe, self.frank])
        men = self.create_group("Men", [], "gender=M")
        nobody = self.create_group("Nobody", [])

        joe_tel = self.joe.get_urn(TEL_SCHEME)
        joe_twitter = self.joe.get_urn(TWITTER_SCHEME)
        frank_tel = self.frank.get_urn(TEL_SCHEME)
        voldemort_tel = self.voldemort.get_urn(TEL_SCHEME)

        # Postgres will defer to strcoll for ordering which even for en_US.UTF-8 will return different results on OSX
        # and Ubuntu. To keep ordering consistent for this test, we don't let URNs start with +
        # (see http://postgresql.nabble.com/a-strange-order-by-behavior-td4513038.html)
        voldemort_tel.path = "250788383383"
        voldemort_tel.urn = "tel:250788383383"
        voldemort_tel.save()

        self.admin.set_org(self.org)
        self.login(self.admin)

        def omnibox_request(query):
            response = self.client.get("%s?%s" % (reverse("contacts.contact_omnibox"), query))
            return json.loads(response.content)['results']

        self.assertEqual(omnibox_request(""), [
            # all 3 groups A-Z
            dict(id='g-%s' % joe_and_frank.uuid, text="Joe and Frank", extra=2),
            dict(id='g-%s' % men.uuid, text="Men", extra=0),
            dict(id='g-%s' % nobody.uuid, text="Nobody", extra=0),

            # all 4 contacts A-Z
            dict(id='c-%s' % self.billy.uuid, text="Billy Nophone"),
            dict(id='c-%s' % self.frank.uuid, text="Frank Smith"),
            dict(id='c-%s' % self.joe.uuid, text="Joe Blow"),
            dict(id='c-%s' % self.voldemort.uuid, text="250788383383"),

            # 3 sendable URNs with names as extra
            dict(id='u-%d' % joe_tel.pk, text="123", extra="Joe Blow", scheme='tel'),
            dict(id='u-%d' % frank_tel.pk, text="123222", extra="Frank Smith", scheme='tel'),
            dict(id='u-%d' % voldemort_tel.pk, text="250788383383", extra=None, scheme='tel')
        ])

        # apply type filters...

        # g = just the 3 groups
        self.assertEqual(omnibox_request("types=g"), [
            dict(id='g-%s' % joe_and_frank.uuid, text="Joe and Frank", extra=2),
            dict(id='g-%s' % men.uuid, text="Men", extra=0),
            dict(id='g-%s' % nobody.uuid, text="Nobody", extra=0)
        ])

        # s = just the 2 non-dynamic (static) groups
        self.assertEqual(omnibox_request("types=s"), [
            dict(id='g-%s' % joe_and_frank.uuid, text="Joe and Frank", extra=2),
            dict(id='g-%s' % nobody.uuid, text="Nobody", extra=0)
        ])

        # c,u = contacts and URNs
        self.assertEqual(omnibox_request("types=c,u"), [
            dict(id='c-%s' % self.billy.uuid, text="Billy Nophone"),
            dict(id='c-%s' % self.frank.uuid, text="Frank Smith"),
            dict(id='c-%s' % self.joe.uuid, text="Joe Blow"),
            dict(id='c-%s' % self.voldemort.uuid, text="250788383383"),
            dict(id='u-%d' % joe_tel.pk, text="123", extra="Joe Blow", scheme='tel'),
            dict(id='u-%d' % frank_tel.pk, text="123222", extra="Frank Smith", scheme='tel'),
            dict(id='u-%d' % voldemort_tel.pk, text="250788383383", extra=None, scheme='tel')
        ])

        # search for Frank by phone
        self.assertEqual(omnibox_request("search=123222"), [
            dict(id='u-%d' % frank_tel.pk, text="123222", extra="Frank Smith", scheme='tel')
        ])

        # search for Joe by twitter - won't return anything because there is no twitter channel
        self.assertEqual(omnibox_request("search=blow80"), [])

        # create twitter channel
        Channel.create(self.org, self.user, None, 'TT')

        # search for again for Joe by twitter
        self.assertEqual(omnibox_request("search=blow80"), [
            dict(id='u-%d' % joe_twitter.pk, text="blow80", extra="Joe Blow", scheme='twitter')
        ])

        # search for Joe again - match on last name and twitter handle
        self.assertEqual(omnibox_request("search=BLOW"), [
            dict(id='c-%s' % self.joe.uuid, text="Joe Blow"),
            dict(id='u-%d' % joe_twitter.pk, text="blow80", extra="Joe Blow", scheme='twitter')
        ])

        # make sure our matches are ANDed
        self.assertEqual(omnibox_request("search=Joe+o&types=c"), [
            dict(id='c-%s' % self.joe.uuid, text="Joe Blow")
        ])
        self.assertEqual(omnibox_request("search=Joe+o&types=g"), [
            dict(id='g-%s' % joe_and_frank.uuid, text="Joe and Frank", extra=2),
        ])

        # lookup by contact ids
        self.assertEqual(omnibox_request("c=%s,%s" % (self.joe.uuid, self.frank.uuid)), [
            dict(id='c-%s' % self.frank.uuid, text="Frank Smith"),
            dict(id='c-%s' % self.joe.uuid, text="Joe Blow")
        ])

        # lookup by group id
        self.assertEqual(omnibox_request("g=%s" % joe_and_frank.uuid), [
            dict(id='g-%s' % joe_and_frank.uuid, text="Joe and Frank", extra=2)
        ])

        # lookup by URN ids
        urn_query = "u=%d,%d" % (self.joe.get_urn(TWITTER_SCHEME).pk, self.frank.get_urn(TEL_SCHEME).pk)
        self.assertEqual(omnibox_request(urn_query), [
            dict(id='u-%d' % frank_tel.pk, text="123222", extra="Frank Smith", scheme='tel'),
            dict(id='u-%d' % joe_twitter.pk, text="blow80", extra="Joe Blow", scheme='twitter')
        ])

        # lookup by message ids
        msg = self.create_msg(direction='I', contact=self.joe, text="some message")
        self.assertEqual(omnibox_request("m=%d" % msg.pk), [
            dict(id='c-%s' % self.joe.uuid, text="Joe Blow")
        ])

        # lookup by label ids
        label = Label.get_or_create(self.org, self.user, "msg label")
        self.assertEqual(omnibox_request("l=%d" % label.pk), [])

        msg.labels.add(label)
        self.assertEqual(omnibox_request("l=%d" % label.pk), [
            dict(id='c-%s' % self.joe.uuid, text="Joe Blow")
        ])

        with AnonymousOrg(self.org):
            self.assertEqual(omnibox_request(""), [
                # all 3 groups...
                dict(id='g-%s' % joe_and_frank.uuid, text="Joe and Frank", extra=2),
                dict(id='g-%s' % men.uuid, text="Men", extra=0),
                dict(id='g-%s' % nobody.uuid, text="Nobody", extra=0),

                # all 4 contacts A-Z
                dict(id='c-%s' % self.billy.uuid, text="Billy Nophone"),
                dict(id='c-%s' % self.frank.uuid, text="Frank Smith"),
                dict(id='c-%s' % self.joe.uuid, text="Joe Blow"),
                dict(id='c-%s' % self.voldemort.uuid, text=self.voldemort.anon_identifier)
            ])

            # can search by frank id
            self.assertEqual(omnibox_request("search=%d" % self.frank.pk), [
                dict(id='c-%s' % self.frank.uuid, text="Frank Smith")
            ])

            # but not by frank number
            self.assertEqual(omnibox_request("search=123222"), [])

    def test_history(self):
        url = reverse('contacts.contact_history', args=[self.joe.uuid])

        self.joe.created_on = timezone.now() - timedelta(days=1000)
        self.joe.save()

        self.create_campaign()

        # create some messages
        for i in range(100):
            self.create_msg(direction='I', contact=self.joe, text="Inbound message %d" % i,
                            created_on=timezone.now() - timedelta(days=(100 - i)))

        self.create_msg(direction='I', contact=self.joe, text="Very old inbound message",
                        created_on=timezone.now() - timedelta(days=500))

        # start a joe flow
        self.reminder_flow.start([], [self.joe])

        # create an event from the past
        scheduled = timezone.now() - timedelta(days=5)
        EventFire.objects.create(event=self.planting_reminder, contact=self.joe, scheduled=scheduled, fired=scheduled)

        # create a missed call
        ChannelEvent.create(self.channel, self.joe.get_urn(TEL_SCHEME).urn, ChannelEvent.TYPE_CALL_OUT_MISSED,
                            timezone.now(), 5)

        # try adding some failed calls
        IVRCall.objects.create(contact=self.joe, status=NO_ANSWER, created_by=self.admin,
                               modified_by=self.admin, channel=self.channel, org=self.org,
                               contact_urn=self.joe.urns.all().first())

        # fetch our contact history
        with self.assertNumQueries(70):
            response = self.fetch_protected(url, self.admin)

        self.assertTrue(response.context['has_older'])

        # activity should include all messages in the last 90 days, the channel event, the call, and the flow run
        activity = response.context['activity']
        self.assertEqual(len(activity), 94)
        self.assertIsInstance(activity[0], IVRCall)
        self.assertIsInstance(activity[1], ChannelEvent)
        self.assertIsInstance(activity[2], Msg)
        self.assertEqual(activity[2].direction, 'O')
        self.assertIsInstance(activity[3], FlowRun)
        self.assertIsInstance(activity[4], Msg)
        self.assertEqual(activity[4].text, "Inbound message 99")
        self.assertIsInstance(activity[8], EventFire)
        self.assertEqual(activity[-1].text, "Inbound message 11")

        # fetch next page
        before = response.context['start_time']
        response = self.fetch_protected(url + '?before=%d' % before, self.admin)
        self.assertFalse(response.context['has_older'])

        # activity should include 11 remaining messages and the event fire
        activity = response.context['activity']
        self.assertEqual(len(activity), 12)
        self.assertEqual(activity[0].text, "Inbound message 10")
        self.assertEqual(activity[10].text, "Inbound message 0")
        self.assertEqual(activity[11].text, "Very old inbound message")

        # if a broadcast is purged, it appears in place of the message
        bcast = Broadcast.objects.get()
        bcast.purged = True
        bcast.save()
        bcast.msgs.all().delete()

        response = self.fetch_protected(url, self.admin)
        activity = response.context['activity']
        self.assertEqual(len(activity), 94)
        self.assertIsInstance(activity[3], Broadcast)  # TODO fix order so initial broadcasts come after their run
        self.assertEqual(activity[3].text, "What is your favorite color?")
        self.assertEqual(activity[3].translated_text, "What is your favorite color?")

        # if a new message comes in
        self.create_msg(direction='I', contact=self.joe, text="Newer message")
        response = self.fetch_protected(url, self.admin)

        # now we'll see the message that just came in first, followed by the call event
        activity = response.context['activity']
        self.assertIsInstance(activity[0], Msg)
        self.assertEqual(activity[0].text, "Newer message")
        self.assertIsInstance(activity[1], IVRCall)

        recent_start = datetime_to_ms(timezone.now() - timedelta(days=1))
        response = self.fetch_protected(url + "?r=true&rs=%s" % recent_start, self.admin)

        # with our recent flag on, should not see the older messages
        activity = response.context['activity']
        self.assertEqual(len(activity), 5)

        # can view history as super user as well
        response = self.fetch_protected(url, self.superuser)
        activity = response.context['activity']
        self.assertEqual(len(activity), 95)

        self.login(self.admin)
        response = self.client.get(reverse('contacts.contact_history', args=['bad-uuid']))
        self.assertEqual(response.status_code, 404)

    def test_event_times(self):

        self.create_campaign()

        from temba.campaigns.models import CampaignEvent
        from temba.contacts.templatetags.contacts import event_time

        event = CampaignEvent.create_message_event(self.org, self.admin, self.campaign,
                                                   relative_to=self.planting_date, offset=7, unit='D',
                                                   message='A message to send')

        event.unit = 'D'
        self.assertEquals("7 days after Planting Date", event_time(event))

        event.unit = 'M'
        self.assertEquals("7 minutes after Planting Date", event_time(event))

        event.unit = 'H'
        self.assertEquals("7 hours after Planting Date", event_time(event))

        event.offset = -1
        self.assertEquals("1 hour before Planting Date", event_time(event))

        event.unit = 'D'
        self.assertEquals("1 day before Planting Date", event_time(event))

        event.unit = 'M'
        self.assertEquals("1 minute before Planting Date", event_time(event))

    def test_activity_icon(self):
        msg = Msg.create_incoming(self.channel, 'tel:+1234', "Inbound message")

        from temba.contacts.templatetags.contacts import activity_icon

        # inbound
        self.assertEquals('<span class="glyph icon-bubble-user"></span>', activity_icon(msg))

        # outgoing sent
        msg.direction = 'O'
        msg.status = 'S'
        self.assertEquals('<span class="glyph icon-bubble-right"></span>', activity_icon(msg))

        # outgoing delivered
        msg.status = 'D'
        self.assertEquals('<span class="glyph icon-bubble-check"></span>', activity_icon(msg))

        # failed
        msg.status = 'F'
        self.assertEquals('<span class="glyph icon-bubble-notification"></span>', activity_icon(msg))

        # outgoing voice
        msg.msg_type = 'V'
        self.assertEquals('<span class="glyph icon-phone"></span>', activity_icon(msg))

        # incoming voice
        msg.direction = 'I'
        self.assertEquals('<span class="glyph icon-phone"></span>', activity_icon(msg))

        # simulate a broadcast to 5 people
        from temba.msgs.models import Broadcast
        msg.broadcast = Broadcast.create(self.org, self.admin, 'Test message', [])
        msg.broadcast.recipient_count = 5
        self.assertEquals('<span class="glyph icon-bullhorn"></span>', activity_icon(msg))

    def test_media_tags(self):

        # malformed
        self.assertEqual(None, location('malformed'))
        self.assertEqual(None, location('geo:latlngs'))
        self.assertEqual(None, osm_link('malformed'))
        self.assertEqual(None, osm_link('geo:latlngs'))

        # valid
        media = 'geo:47.5414799,-122.6359908'
        self.assertEqual('http://www.openstreetmap.org/?mlat=47.5414799&mlon=-122.6359908#map=18/47.5414799/-122.6359908', osm_link(media))
        self.assertEqual('47.5414799,-122.6359908', location(media))

        # splitting the type and path
        self.assertEqual('geo', media_type(media))
        self.assertEqual('47.5414799,-122.6359908', media_url(media))

    def test_get_scheduled_messages(self):
        self.just_joe = self.create_group("Just Joe", [self.joe])

        self.assertFalse(self.joe.get_scheduled_messages())

        broadcast = Broadcast.create(self.org, self.admin, "Hello", [])

        self.assertFalse(self.joe.get_scheduled_messages())

        broadcast.contacts.add(self.joe)

        self.assertFalse(self.joe.get_scheduled_messages())

        schedule_time = timezone.now() + timedelta(days=2)
        broadcast.schedule = Schedule.create_schedule(schedule_time, 'O', self.admin)
        broadcast.save()

        self.assertEqual(self.joe.get_scheduled_messages().count(), 1)
        self.assertTrue(broadcast in self.joe.get_scheduled_messages())

        broadcast.contacts.remove(self.joe)
        self.assertFalse(self.joe.get_scheduled_messages())

        broadcast.groups.add(self.just_joe)
        self.assertEqual(self.joe.get_scheduled_messages().count(), 1)
        self.assertTrue(broadcast in self.joe.get_scheduled_messages())

        broadcast.groups.remove(self.just_joe)
        self.assertFalse(self.joe.get_scheduled_messages())

        broadcast.urns.add(self.joe.get_urn())
        self.assertEqual(self.joe.get_scheduled_messages().count(), 1)
        self.assertTrue(broadcast in self.joe.get_scheduled_messages())

        broadcast.schedule.reset()
        self.assertFalse(self.joe.get_scheduled_messages())

    def test_contact_update_urns_field(self):
        update_url = reverse('contacts.contact_update', args=[self.joe.pk])

        # we have a field to add new urns
        response = self.fetch_protected(update_url, self.admin)
        self.assertEquals(self.joe, response.context['object'])
        self.assertContains(response, 'Add Connection')

        # no field to add new urns for anon org
        with AnonymousOrg(self.org):
            response = self.fetch_protected(update_url, self.admin)
            self.assertEquals(self.joe, response.context['object'])
            self.assertNotContains(response, 'Add Connection')

    def test_read(self):
        read_url = reverse('contacts.contact_read', args=[self.joe.uuid])

        for i in range(5):
            self.create_msg(direction='I', contact=self.joe, text="some msg no %d 2 send in sms language if u wish" % i)
            i += 1

        from temba.campaigns.models import EventFire
        self.create_campaign()

        # create more events
        from temba.campaigns.models import CampaignEvent
        for i in range(5):
            self.message_event = CampaignEvent.create_message_event(self.org, self.admin, self.campaign,
                                                                    relative_to=self.planting_date,
                                                                    offset=i + 10, unit='D',
                                                                    message='Sent %d days after planting date' % (i + 10))

        now = timezone.now()
        self.joe.set_field(self.user, 'planting_date', unicode(now + timedelta(days=1)))
        EventFire.update_campaign_events(self.campaign)

        # should have seven fires, one for each campaign event
        self.assertEquals(7, EventFire.objects.all().count())

        # visit a contact detail page as a user but not belonging to this organization
        self.login(self.user1)
        response = self.client.get(read_url)
        self.assertEquals(302, response.status_code)

        # visit a contact detail page as a manager but not belonging to this organisation
        self.login(self.manager1)
        response = self.client.get(read_url)
        self.assertEquals(302, response.status_code)

        # visit a contact detail page as a manager within the organization
        response = self.fetch_protected(read_url, self.admin)
        self.assertEquals(self.joe, response.context['object'])

        with patch('temba.orgs.models.Org.get_schemes') as mock_get_schemes:
            mock_get_schemes.return_value = []

            response = self.fetch_protected(read_url, self.admin)
            self.assertEquals(self.joe, response.context['object'])
            self.assertFalse(response.context['has_sendable_urn'])

            mock_get_schemes.return_value = ['tel']

            response = self.fetch_protected(read_url, self.admin)
            self.assertEquals(self.joe, response.context['object'])
            self.assertTrue(response.context['has_sendable_urn'])

        response = self.fetch_protected(read_url, self.admin)
        self.assertEquals(self.joe, response.context['object'])
        self.assertTrue(response.context['has_sendable_urn'])
        upcoming = response.context['upcoming_events']

        # should show the next seven events to fire in reverse order
        self.assertEquals(7, len(upcoming))

        self.assertEquals("Sent 10 days after planting date", upcoming[4]['message'])
        self.assertEquals("Sent 7 days after planting date", upcoming[5]['message'])
        self.assertEquals(None, upcoming[6]['message'])
        self.assertEquals(self.reminder_flow.pk, upcoming[6]['flow_id'])
        self.assertEquals(self.reminder_flow.name, upcoming[6]['flow_name'])

        self.assertGreater(upcoming[4]['scheduled'], upcoming[5]['scheduled'])

        # add a scheduled broadcast
        broadcast = Broadcast.create(self.org, self.admin, "Hello", [])
        broadcast.contacts.add(self.joe)
        schedule_time = now + timedelta(days=5)
        broadcast.schedule = Schedule.create_schedule(schedule_time, 'O', self.admin)
        broadcast.save()

        response = self.fetch_protected(read_url, self.admin)
        self.assertEquals(self.joe, response.context['object'])
        upcoming = response.context['upcoming_events']

        # should show the next 2 events to fire and the scheduled broadcast in reverse order by schedule time
        self.assertEquals(8, len(upcoming))

        self.assertEquals("Sent 7 days after planting date", upcoming[5]['message'])
        self.assertEquals("Hello", upcoming[6]['message'])
        self.assertEquals(None, upcoming[7]['message'])
        self.assertEquals(self.reminder_flow.pk, upcoming[7]['flow_id'])
        self.assertEquals(self.reminder_flow.name, upcoming[7]['flow_name'])

        self.assertGreater(upcoming[6]['scheduled'], upcoming[7]['scheduled'])

        contact_no_name = self.create_contact(name=None, number="678")
        read_url = reverse('contacts.contact_read', args=[contact_no_name.uuid])
        response = self.fetch_protected(read_url, self.superuser)
        self.assertEquals(contact_no_name, response.context['object'])
        self.client.logout()

        # login as a manager from out of this organization
        self.login(self.manager1)

        # create kLab group, and add joe to the group
        klab = self.create_group("kLab", [self.joe])

        # post to read url, joe's contact and kLab group
        post_data = dict(contact=self.joe.id, group=klab.id)
        response = self.client.post(read_url, post_data, follow=True)

        # this manager cannot operate on this organization
        self.assertEquals(len(self.joe.user_groups.all()), 2)
        self.client.logout()

        # login as a manager of kLab
        self.login(self.admin)

        # remove this contact form kLab group
        response = self.client.post(read_url, post_data, follow=True)
        self.assertEqual(1, self.joe.user_groups.count())

        # try removing it again, should fail
        response = self.client.post(read_url, post_data, follow=True)
        self.assertEquals(200, response.status_code)

    def test_read_language(self):

        # this is a bogus
        self.joe.language = 'zzz'
        self.joe.save()
        response = self.fetch_protected(reverse('contacts.contact_read', args=[self.joe.uuid]), self.admin)

        # should just show the language code instead of the language name
        self.assertContains(response, 'zzz')

        self.joe.language = 'fra'
        self.joe.save()
        response = self.fetch_protected(reverse('contacts.contact_read', args=[self.joe.uuid]), self.admin)

        # with a proper code, we should see the language
        self.assertContains(response, 'French')

    def test_creating_duplicates(self):
        self.login(self.admin)

        self.client.post(reverse('contacts.contactgroup_create'), dict(name="First Group"))

        # assert it was created
        ContactGroup.user_groups.get(name="First Group")

        # try to create another group with the same name, but a dynamic query, should fail
        response = self.client.post(reverse('contacts.contactgroup_create'), dict(name="First Group", group_query='firsts'))
        self.assertFormError(response, 'form', 'name', "Name is used by another group")

        # try to create another group with same name, not dynamic, same thing
        response = self.client.post(reverse('contacts.contactgroup_create'), dict(name="First Group", group_query='firsts'))
        self.assertFormError(response, 'form', 'name', "Name is used by another group")

    def test_update_and_list(self):
        list_url = reverse('contacts.contact_list')

        self.just_joe = self.create_group("Just Joe", [self.joe])
        self.joe_and_frank = self.create_group("Joe and Frank", [self.joe, self.frank])

        self.joe_and_frank = ContactGroup.user_groups.get(pk=self.joe_and_frank.pk)

        self.assertEquals(self.joe.groups_as_text(), "Joe and Frank, Just Joe")
        group_analytic_json = self.joe_and_frank.analytics_json()
        self.assertEquals(group_analytic_json['id'], self.joe_and_frank.pk)
        self.assertEquals(group_analytic_json['name'], "Joe and Frank")
        self.assertEquals(2, group_analytic_json['count'])

        # list contacts as a user not in the organization
        self.login(self.user1)
        response = self.client.get(list_url)
        self.assertEquals(302, response.status_code)

        self.viewer = self.create_user("Viewer")
        self.org.viewers.add(self.viewer)
        self.viewer.set_org(self.org)

        self.login(self.viewer)

        response = self.client.get(list_url)
        self.assertContains(response, "Joe Blow")
        self.assertContains(response, "Frank Smith")
        self.assertContains(response, "Joe and Frank")
        self.assertEquals(response.context['actions'], ('label', 'block'))

        # this just_joe group has one contact and joe_and_frank group has two contacts
        self.assertEquals(len(self.just_joe.contacts.all()), 1)
        self.assertEquals(len(self.joe_and_frank.contacts.all()), 2)

        # viewer cannot remove Joe from the group
        post_data = dict()
        post_data['action'] = 'label'
        post_data['label'] = self.just_joe.id
        post_data['objects'] = self.joe.id
        post_data['add'] = False

        # no change
        self.client.post(list_url, post_data, follow=True)
        self.assertEquals(len(self.just_joe.contacts.all()), 1)
        self.assertEquals(len(self.joe_and_frank.contacts.all()), 2)

        # viewer also can't block
        post_data['action'] = 'block'
        self.client.post(list_url, post_data, follow=True)
        self.assertFalse(Contact.objects.get(pk=self.joe.id).is_blocked)

        # list the contacts as a manager of the organization
        self.login(self.admin)
        response = self.client.get(list_url)
        self.assertContains(response, "Joe Blow")
        self.assertContains(response, "Frank Smith")
        self.assertContains(response, "Joe and Frank")
        self.assertEquals(response.context['actions'], ('label', 'block'))

        # this just_joe group has one contact and joe_and_frank group has two contacts
        self.assertEquals(len(self.just_joe.contacts.all()), 1)
        self.assertEquals(len(self.joe_and_frank.contacts.all()), 2)

        # add a new group
        group = self.create_group("Test", [self.joe])

        # view our test group
        filter_url = reverse('contacts.contact_filter', args=[group.pk])
        response = self.client.get(filter_url)
        self.assertEquals(1, len(response.context['object_list']))
        self.assertEquals(self.joe, response.context['object_list'][0])

        # should have an edit button
        update_url = reverse('contacts.contactgroup_update', args=[group.pk])
        delete_url = reverse('contacts.contactgroup_delete', args=[group.pk])

        self.assertContains(response, update_url)
        response = self.client.get(update_url)
        self.assertTrue('name' in response.context['form'].fields)

        response = self.client.post(update_url, dict(name="New Test"))
        self.assertRedirect(response, filter_url)

        group = ContactGroup.user_groups.get(pk=group.pk)
        self.assertEquals("New Test", group.name)

        # post to our delete url
        response = self.client.post(delete_url, dict())
        self.assertRedirect(response, reverse('contacts.contact_list'))

        # make sure it is inactive
        self.assertIsNone(ContactGroup.user_groups.filter(name="New Test").first())
        self.assertFalse(ContactGroup.all_groups.get(name="New Test").is_active)

        # remove Joe from the group
        post_data = dict()
        post_data['action'] = 'label'
        post_data['label'] = self.just_joe.id
        post_data['objects'] = self.joe.id
        post_data['add'] = False

        # check the Joe is only removed from just_joe only and is still in joe_and_frank
        self.client.post(list_url, post_data, follow=True)
        self.assertEquals(len(self.just_joe.contacts.all()), 0)
        self.assertEquals(len(self.joe_and_frank.contacts.all()), 2)

        # Now add back Joe to the group
        post_data = dict()
        post_data['action'] = 'label'
        post_data['label'] = self.just_joe.id
        post_data['objects'] = self.joe.id
        post_data['add'] = True

        self.client.post(list_url, post_data, follow=True)
        self.assertEquals(len(self.just_joe.contacts.all()), 1)
        self.assertEquals(self.just_joe.contacts.all()[0].pk, self.joe.pk)
        self.assertEquals(len(self.joe_and_frank.contacts.all()), 2)

        # test filtering by group
        joe_and_frank_filter_url = reverse('contacts.contact_filter', args=[self.joe_and_frank.pk])

        # now test when the action with some data missing
        self.assertEquals(self.joe.user_groups.filter(is_active=True).count(), 2)

        post_data = dict()
        post_data['action'] = 'label'
        post_data['objects'] = self.joe.id
        post_data['add'] = True
        self.client.post(joe_and_frank_filter_url, post_data)
        self.assertEquals(self.joe.user_groups.filter(is_active=True).count(), 2)

        post_data = dict()
        post_data['action'] = 'unlabel'
        post_data['objects'] = self.joe.id
        post_data['add'] = True
        self.client.post(joe_and_frank_filter_url, post_data)
        self.assertEquals(self.joe.user_groups.filter(is_active=True).count(), 2)

        # Now archive Joe
        post_data = dict()
        post_data['action'] = 'block'
        post_data['objects'] = self.joe.id
        self.client.post(list_url, post_data, follow=True)

        self.joe = Contact.objects.filter(pk=self.joe.pk)[0]
        self.assertEquals(self.joe.is_blocked, True)
        self.assertEquals(len(self.just_joe.contacts.all()), 0)
        self.assertEquals(len(self.joe_and_frank.contacts.all()), 1)

        # shouldn't be any contacts on the stopped page
        response = self.client.get(reverse('contacts.contact_stopped'))
        self.assertEquals(0, len(response.context['object_list']))

        # mark frank as stopped
        self.frank.stop(self.user)

        stopped_url = reverse('contacts.contact_stopped')

        response = self.client.get(stopped_url)
        self.assertEquals(1, len(response.context['object_list']))
        self.assertEquals(1, response.context['object_list'].count())  # from cache

        # receiving an incoming message removes us from stopped
        Msg.create_incoming(self.channel, str(self.frank.get_urn('tel')), "Incoming message")

        response = self.client.get(stopped_url)
        self.assertEquals(0, len(response.context['object_list']))
        self.assertEquals(0, response.context['object_list'].count())  # from cache

        self.frank.refresh_from_db()
        self.assertFalse(self.frank.is_stopped)

        # mark frank stopped again
        self.frank.stop(self.user)

        # have the user unstop them
        post_data = dict()
        post_data['action'] = 'unstop'
        post_data['objects'] = self.frank.id
        self.client.post(stopped_url, post_data, follow=True)

        response = self.client.get(stopped_url)
        self.assertEquals(0, len(response.context['object_list']))
        self.assertEquals(0, response.context['object_list'].count())  # from cache

        self.frank.refresh_from_db()
        self.assertFalse(self.frank.is_stopped)

        # add him back to joe and frank
        self.joe_and_frank.contacts.add(self.frank)

        # Now let's visit the archived contacts page
        blocked_url = reverse('contacts.contact_blocked')

        # archived contact are not on the list page
        post_data = dict()
        post_data['action'] = 'unblock'
        post_data['objects'] = self.joe.id
        self.client.post(blocked_url, post_data, follow=True)

        # and check that Joe is restored to the contact list but the group not restored
        response = self.client.get(list_url)
        self.assertContains(response, "Joe Blow")
        self.assertContains(response, "Frank Smith")
        self.assertEquals(response.context['actions'], ('label', 'block'))
        self.assertEquals(len(self.just_joe.contacts.all()), 0)
        self.assertEquals(len(self.joe_and_frank.contacts.all()), 1)

        # now let's test removing a contact from a group
        post_data = dict()
        post_data['action'] = 'unlabel'
        post_data['label'] = self.joe_and_frank.id
        post_data['objects'] = self.frank.id
        post_data['add'] = True
        self.client.post(joe_and_frank_filter_url, post_data, follow=True)
        self.assertEquals(len(self.joe_and_frank.contacts.all()), 0)

        # add an extra field to the org
        ContactField.get_or_create(self.org, self.user, 'state', label="Home state", value_type=Value.TYPE_STATE)
        self.joe.set_field(self.user, 'state', " kiGali   citY ")  # should match "Kigali City"

        # check that the field appears on the update form
        response = self.client.get(reverse('contacts.contact_update', args=[self.joe.id]))
        self.assertEqual(response.context['form'].fields.keys(), ['name', 'groups', 'urn__twitter__0', 'urn__tel__1', 'loc'])
        self.assertEqual(response.context['form'].initial['name'], "Joe Blow")
        self.assertEqual(response.context['form'].fields['urn__tel__1'].initial, "123")

        response = self.client.get(reverse('contacts.contact_update_fields', args=[self.joe.id]))
        self.assertEqual(response.context['form'].fields['__field__state'].initial, "Kigali City")  # parsed name

        # update it to something else
        self.joe.set_field(self.user, 'state', "eastern province")

        # check the read page
        response = self.client.get(reverse('contacts.contact_read', args=[self.joe.uuid]))
        self.assertContains(response, "Eastern Province")

        # update joe - change his tel URN
        data = dict(name="Joe Blow", urn__tel__1="12345", order__urn__tel__1="0")
        self.client.post(reverse('contacts.contact_update', args=[self.joe.id]), data)

        # update the state contact field to something invalid
        self.client.post(reverse('contacts.contact_update_fields', args=[self.joe.id]), dict(__field__state='newyork'))

        # check that old URN is detached, new URN is attached, and Joe still exists
        self.joe = Contact.objects.get(pk=self.joe.id)
        self.assertEquals("12345", self.joe.get_urn_display(scheme=TEL_SCHEME))
        self.assertEquals(self.joe.get_field_raw('state'), "newyork")  # raw user input as location wasn't matched
        self.assertFalse(Contact.from_urn(self.org, "tel:123"))  # tel 123 is nobody now

        # update joe, change his number back
        data = dict(name="Joe Blow", urn__tel__0="123", order__urn__tel__0="0", __field__location="Kigali")
        self.client.post(reverse('contacts.contact_update', args=[self.joe.id]), data)

        # check that old URN is re-attached
        self.assertIsNone(ContactURN.objects.get(urn="tel:12345").contact)
        self.assertEquals(self.joe, ContactURN.objects.get(urn="tel:123").contact)

        # add another URN to joe
        ContactURN.create(self.org, self.joe, "tel:67890")

        # assert that our update form has the extra URN
        response = self.client.get(reverse('contacts.contact_update', args=[self.joe.id]))
        self.assertEquals("123", response.context['form'].fields['urn__tel__0'].initial)
        self.assertEquals("67890", response.context['form'].fields['urn__tel__1'].initial)

        # update joe, add him to "Just Joe" group
        post_data = dict(name="Joe Gashyantare", groups=[self.just_joe.id],
                         urn__tel__0="123", urn__tel__1="67890")
        response = self.client.post(reverse('contacts.contact_update', args=[self.joe.id]), post_data, follow=True)
        self.assertEqual(response.context['contact'].name, "Joe Gashyantare")
        self.assertEqual(set(self.joe.user_groups.all()), {self.just_joe})
        self.assertTrue(ContactURN.objects.filter(contact=self.joe, path="123"))
        self.assertTrue(ContactURN.objects.filter(contact=self.joe, path="67890"))

        # remove him from this group "Just joe", and his second number
        post_data = dict(name="Joe Gashyantare", urn__tel__0="12345", groups=[])
        response = self.client.post(reverse('contacts.contact_update', args=[self.joe.id]), post_data, follow=True)
        self.assertEqual(set(self.joe.user_groups.all()), set())
        self.assertTrue(ContactURN.objects.filter(contact=self.joe, path="12345"))
        self.assertFalse(ContactURN.objects.filter(contact=self.joe, path="67890"))
        self.assertFalse(ContactURN.objects.filter(contact=self.joe, path="1232"))

        # should no longer be in our update form either
        response = self.client.get(reverse('contacts.contact_update', args=[self.joe.id]))
        self.assertEquals("12345", response.context['form'].fields['urn__tel__0'].initial)
        self.assertFalse('urn__tel__1' in response.context['form'].fields)

        # check that groups field isn't displayed when contact is blocked
        self.joe.block(self.user)
        response = self.client.get(reverse('contacts.contact_update', args=[self.joe.id]))
        self.assertNotIn('groups', response.context['form'].fields)

        # and that we can still update the contact
        post_data = dict(name="Joe Bloggs", urn__tel__0="12345")
        self.client.post(reverse('contacts.contact_update', args=[self.joe.id]), post_data, follow=True)

        self.joe = Contact.objects.get(pk=self.joe.pk)
        self.assertEquals(self.joe.name, "Joe Bloggs")

        self.joe.unblock(self.user)

        # add new urn for joe
        self.client.post(reverse('contacts.contact_update', args=[self.joe.id]),
                         dict(name='Joey', urn__tel__0="12345", new_scheme="ext", new_path="EXT123"))

        urn = ContactURN.objects.filter(contact=self.joe, scheme='ext').first()
        self.assertIsNotNone(urn)
        self.assertEquals('EXT123', urn.path)

        # now try adding one that is invalid
        self.client.post(reverse('contacts.contact_update', args=[self.joe.id]),
                         dict(name='Joey', urn__tel__0="12345", new_scheme="mailto", new_path="malformed"))
        self.assertIsNone(ContactURN.objects.filter(contact=self.joe, scheme='mailto').first())

        # update our language to something not on the org
        self.joe.refresh_from_db()
        self.joe.language = 'fre'
        self.joe.save()

        # add some languages to our org, but not french
        self.client.post(reverse('orgs.org_languages'), dict(primary_lang='hat', languages='arc,spa'))
        response = self.client.get(reverse('contacts.contact_update', args=[self.joe.id]))

        self.assertContains(response, 'French (Missing)')
        self.client.post(reverse('contacts.contact_update', args=[self.joe.id]),
                         dict(name='Joey', urn__tel__0="12345", new_scheme="mailto", new_path="malformed"))

        # update our contact with some locations
        state = ContactField.get_or_create(self.org, self.admin, 'state', "Home State", value_type='S')
        ContactField.get_or_create(self.org, self.admin, 'home', "Home District", value_type='I')

        self.client.post(reverse('contacts.contact_update_fields', args=[self.joe.id]),
                         dict(__field__state='eastern province', __field__home='rwamagana'))

        response = self.client.get(reverse('contacts.contact_read', args=[self.joe.uuid]))
        self.assertContains(response, 'Eastern Province')
        self.assertContains(response, 'Rwamagana')

        # change the name of the Rwamagana boundary, our display should change appropriately as well
        rwamagana = AdminBoundary.objects.get(name="Rwamagana")
        rwamagana.update(name="Rwa-magana")
        self.assertEqual("Rwa-magana", rwamagana.name)
        self.assertTrue(Value.objects.filter(location_value=rwamagana, category="Rwa-magana"))

        # assert our read page is correct
        response = self.client.get(reverse('contacts.contact_read', args=[self.joe.uuid]))
        self.assertContains(response, 'Eastern Province')
        self.assertContains(response, 'Rwa-magana')

        # change our field to a text field
        state.value_type = Value.TYPE_TEXT
        state.save()
        value = self.joe.get_field('state')
        value.category = "Rwama Category"
        value.save()

        # should now be using stored category as value
        response = self.client.get(reverse('contacts.contact_read', args=[self.joe.uuid]))
        self.assertContains(response, 'Rwama Category')

        # try to push into a dynamic group
        self.login(self.admin)
        group = self.create_group('Dynamo', query='dynamo')

        with self.assertRaises(ValueError):
            post_data = dict()
            post_data['action'] = 'label'
            post_data['label'] = group.pk
            post_data['objects'] = self.frank.pk
            post_data['add'] = True
            self.client.post(list_url, post_data)

        # check updating when org is anon
        self.org.is_anon = True
        self.org.save()

        post_data = dict(name="Joe X", groups=[self.just_joe.id])
        self.client.post(reverse('contacts.contact_update', args=[self.joe.id]), post_data, follow=True)
        self.assertEquals(Contact.from_urn(self.org, "tel:12345"), self.joe)  # ensure Joe still has tel 12345
        self.assertEquals(Contact.from_urn(self.org, "tel:12345").name, "Joe X")

        # try delete action
        call = ChannelEvent.create(self.channel, self.frank.get_urn(TEL_SCHEME).urn, ChannelEvent.TYPE_CALL_OUT_MISSED,
                                   timezone.now(), 5)
        post_data['action'] = 'delete'
        post_data['objects'] = self.frank.pk

        self.client.post(list_url, post_data)
        self.frank.refresh_from_db()
        self.assertFalse(self.frank.is_active)
        call.refresh_from_db()

        # the call should be inactive now too
        self.assertFalse(call.is_active)

    def test_number_normalized(self):
        self.org.country = None
        self.org.save()

        self.channel.country = 'GB'
        self.channel.save()

        self.login(self.admin)

        self.client.post(reverse('contacts.contact_create'), dict(name="Ryan Lewis", urn__tel__0='07531669965'))
        contact = Contact.from_urn(self.org, 'tel:+447531669965')
        self.assertEqual("Ryan Lewis", contact.name)

        # try the update case
        self.client.post(reverse('contacts.contact_update', args=[contact.id]), dict(name="Marshal Mathers", urn__tel__0='07531669966'))
        contact = Contact.from_urn(self.org, 'tel:+447531669966')
        self.assertEqual("Marshal Mathers", contact.name)

    def test_contact_model(self):
        contact1 = self.create_contact(name=None, number="123456")

        contact1.set_first_name("Ludacris")
        self.assertEquals(contact1.name, "Ludacris")

        first_modified_on = contact1.modified_on
        contact1.set_field(self.editor, 'occupation', 'Musician')

        contact1.refresh_from_db()
        self.assertTrue(contact1.modified_on > first_modified_on)
        self.assertEqual(contact1.modified_by, self.editor)

        contact2 = self.create_contact(name="Boy", number="12345")
        self.assertEquals(contact2.get_display(), "Boy")

        contact3 = self.create_contact(name=None, number="0788111222")
        self.channel.country = 'RW'
        self.channel.save()

        normalized = contact3.get_urn(TEL_SCHEME).ensure_number_normalization(self.channel)
        self.assertEquals(normalized.path, "+250788111222")

        contact4 = self.create_contact(name=None, number="0788333444")
        normalized = contact4.get_urn(TEL_SCHEME).ensure_number_normalization(self.channel)
        self.assertEquals(normalized.path, "+250788333444")

        # check normalization leads to matching
        contact5 = self.create_contact(name='Jimmy', number="+250788333555")
        contact6 = self.create_contact(name='James', number="0788333555")
        self.assertEquals(contact5.pk, contact6.pk)

        contact5.update_urns(self.user, ['twitter:jimmy_woot', 'tel:0788333666'])

        # check old phone URN still existing but was detached
        self.assertIsNone(ContactURN.objects.get(urn='tel:+250788333555').contact)

        # check new URNs were created and attached
        self.assertEquals(contact5, ContactURN.objects.get(urn='tel:+250788333666').contact)
        self.assertEquals(contact5, ContactURN.objects.get(urn='twitter:jimmy_woot').contact)

        # check twitter URN takes priority if you don't specify scheme
        self.assertEqual('twitter:jimmy_woot', contact5.get_urn().urn)
        self.assertEquals('twitter:jimmy_woot', contact5.get_urn(schemes=[TWITTER_SCHEME]).urn)
        self.assertEquals('tel:+250788333666', contact5.get_urn(schemes=[TEL_SCHEME]).urn)
        self.assertIsNone(contact5.get_urn(schemes=['email']))
        self.assertIsNone(contact5.get_urn(schemes=['facebook']))

        # check that we can steal other contact's URNs
        contact5.update_urns(self.user, ['tel:0788333444'])
        self.assertEquals(contact5, ContactURN.objects.get(urn='tel:+250788333444').contact)
        self.assertFalse(contact4.urns.all())

    def test_from_urn(self):
        self.assertEqual(self.joe, Contact.from_urn(self.org, 'tel:123'))  # URN with contact
        self.assertIsNone(Contact.from_urn(self.org, 'tel:8888'))  # URN with no contact
        self.assertIsNone(Contact.from_urn(self.org, 'snoop@dogg.com'))  # URN with no scheme

    def test_validate_import_header(self):
        with self.assertRaises(Exception):
            Contact.validate_org_import_header([], self.org)

        with self.assertRaises(Exception):
            Contact.validate_org_import_header(['name'], self.org)  # missing a URN

        with self.assertRaises(Exception):
            Contact.validate_org_import_header(['phone', 'twitter', 'external'], self.org)  # missing name

        Contact.validate_org_import_header(['uuid'], self.org)
        Contact.validate_org_import_header(['uuid', 'age'], self.org)
        Contact.validate_org_import_header(['uuid', 'name'], self.org)
        Contact.validate_org_import_header(['name', 'phone', 'twitter', 'external'], self.org)
        Contact.validate_org_import_header(['name', 'phone'], self.org)
        Contact.validate_org_import_header(['name', 'twitter'], self.org)
        Contact.validate_org_import_header(['name', 'external'], self.org)

        with AnonymousOrg(self.org):
            Contact.validate_org_import_header(['uuid'], self.org)
            Contact.validate_org_import_header(['uuid', 'age'], self.org)
            Contact.validate_org_import_header(['uuid', 'name'], self.org)
            Contact.validate_org_import_header(['name', 'phone', 'twitter', 'external'], self.org)
            Contact.validate_org_import_header(['name', 'phone'], self.org)
            Contact.validate_org_import_header(['name', 'twitter'], self.org)
            Contact.validate_org_import_header(['name', 'external'], self.org)

    def test_get_import_file_headers(self):
        with open('%s/test_imports/sample_contacts_with_extra_fields.xls' % settings.MEDIA_ROOT, 'rb') as open_file:
            csv_file = ContentFile(open_file.read())
            headers = ['country', 'district', 'zip code', 'professional status', 'joined', 'vehicle', 'shoes']
            self.assertEqual(Contact.get_org_import_file_headers(csv_file, self.org), headers)

            self.assertFalse('email' in Contact.get_org_import_file_headers(csv_file, self.org))

        with open('%s/test_imports/sample_contacts_with_extra_fields_and_empty_headers.xls' % settings.MEDIA_ROOT,
                  'rb') as open_file:
            csv_file = ContentFile(open_file.read())
            headers = ['country', 'district', 'zip code', 'professional status', 'joined', 'vehicle', 'shoes']
            self.assertEqual(Contact.get_org_import_file_headers(csv_file, self.org), headers)

    def test_create_instance(self):
        # can't import contact without a user
        self.assertRaises(ValueError, Contact.create_instance, dict(org=self.org))

        # or without a number (exception type that goes back to the user)
        self.assertRaises(SmartImportRowError, Contact.create_instance, dict(org=self.org, created_by=self.admin))

        # or invalid phone number
        self.assertRaises(SmartImportRowError, Contact.create_instance,
                          dict(org=self.org, created_by=self.admin, phone="+121535e0884"))

        contact = Contact.create_instance(dict(org=self.org, created_by=self.admin, name="Bob", phone="+250788111111"))
        self.assertEqual(contact.org, self.org)
        self.assertEqual(contact.name, "Bob")
        self.assertEqual([u.urn for u in contact.urns.all()], ["tel:+250788111111"])
        self.assertEqual(contact.created_by, self.admin)

    def do_import(self, user, filename):

        import_params = dict(org_id=self.org.id, timezone=self.org.timezone, extra_fields=[],
                             original_filename=filename)

        task = ImportTask.objects.create(
            created_by=user, modified_by=user,
            csv_file='test_imports/' + filename,
            model_class="Contact", import_params=json.dumps(import_params), import_log="", task_id="A")

        return Contact.import_csv(task, log=None)

    def assertContactImport(self, filepath, expected_results=None, task_customize=None, custom_fields_number=None):
        csv_file = open(filepath, 'rb')
        post_data = dict(csv_file=csv_file)
        response = self.client.post(reverse('contacts.contact_import'), post_data, follow=True)

        self.assertIsNotNone(response.context['task'])

        if task_customize:
            self.assertEquals(response.request['PATH_INFO'], reverse('contacts.contact_customize',
                                                                     args=[response.context['task'].pk]))
            if custom_fields_number:
                self.assertEquals(len(response.context['form'].fields.keys()), custom_fields_number)

        else:
            self.assertEquals(response.context['results'], expected_results)

            # no errors so hide the import form
            if not expected_results.get('error_messages', []):
                self.assertFalse(response.context['show_form'])

            # we have records and added them to a group
            if expected_results.get('records', 0):
                self.assertIsNotNone(response.context['group'])

        return response

    def test_contact_import(self):
        #
        # first import brings in 3 contacts
        user = self.user
        records = self.do_import(user, 'sample_contacts.xls')
        self.assertEquals(3, len(records))

        self.assertEquals(1, len(ContactGroup.user_groups.all()))
        group = ContactGroup.user_groups.all()[0]
        self.assertEquals('Sample Contacts', group.name)
        self.assertEquals(3, group.contacts.count())

        self.assertEquals(1, Contact.objects.filter(name='Eric Newcomer').count())
        self.assertEquals(1, Contact.objects.filter(name='Nic Pottier').count())
        self.assertEquals(1, Contact.objects.filter(name='Jen Newcomer').count())

        jen_pk = Contact.objects.get(name='Jen Newcomer').pk

        # import again, should be no more records
        records = self.do_import(user, 'sample_contacts.xls')
        self.assertEquals(3, len(records))

        # But there should be another group
        self.assertEquals(2, len(ContactGroup.user_groups.all()))
        self.assertEquals(1, ContactGroup.user_groups.filter(name="Sample Contacts 2").count())

        # update file changes a name, and adds one more
        records = self.do_import(user, 'sample_contacts_update.csv')

        # now there are three groups
        self.assertEquals(3, len(ContactGroup.user_groups.all()))
        self.assertEquals(1, ContactGroup.user_groups.filter(name="Sample Contacts Update").count())

        self.assertEquals(1, Contact.objects.filter(name='Eric Newcomer').count())
        self.assertEquals(1, Contact.objects.filter(name='Nic Pottier').count())
        self.assertEquals(0, Contact.objects.filter(name='Jennifer Newcomer').count())
        self.assertEquals(1, Contact.objects.filter(name='Jackson Newcomer').count())
        self.assertEquals(1, Contact.objects.filter(name='Norbert Kwizera').count())

        # Jackson took over Jen's number
        self.assertEquals(Contact.objects.get(name='Jackson Newcomer').pk, jen_pk)
        self.assertEquals(4, len(records))

        # Empty import file, shouldn't create a contact group
        self.do_import(user, 'empty.csv')
        self.assertEquals(3, len(ContactGroup.user_groups.all()))

        # import twitter urns
        records = self.do_import(user, 'sample_contacts_twitter.xls')
        self.assertEquals(3, len(records))

        # now there are four groups
        self.assertEquals(4, len(ContactGroup.user_groups.all()))
        self.assertEquals(1, ContactGroup.user_groups.filter(name="Sample Contacts Twitter").count())

        self.assertEquals(1, Contact.objects.filter(name='Rapidpro').count())
        self.assertEquals(1, Contact.objects.filter(name='Textit').count())
        self.assertEquals(1, Contact.objects.filter(name='Nyaruka').count())

        # import twitter urns with phone
        records = self.do_import(user, 'sample_contacts_twitter_and_phone.xls')
        self.assertEquals(3, len(records))

        # now there are five groups
        self.assertEquals(5, len(ContactGroup.user_groups.all()))
        self.assertEquals(1, ContactGroup.user_groups.filter(name="Sample Contacts Twitter And Phone").count())

        self.assertEquals(1, Contact.objects.filter(name='Rapidpro').count())
        self.assertEquals(1, Contact.objects.filter(name='Textit').count())
        self.assertEquals(1, Contact.objects.filter(name='Nyaruka').count())

        import_url = reverse('contacts.contact_import')

        self.login(self.admin)
        response = self.client.get(import_url)
        self.assertTrue(response.context['show_form'])
        self.assertFalse(response.context['task'])
        self.assertEquals(response.context['group'], None)

        Contact.objects.all().delete()
        ContactGroup.user_groups.all().delete()
        contact = self.create_contact(name="Bob", number='+250788111111')
        contact.uuid = 'uuid-1111'
        contact.save()

        contact2 = self.create_contact(name='Kobe', number='+250788383396')
        contact2.uuid = 'uuid-4444'
        contact2.save()

        self.assertEqual(list(contact.get_urns().values_list('path', flat=True)), ['+250788111111'])
        self.assertEqual(list(contact2.get_urns().values_list('path', flat=True)), ['+250788383396'])

        with patch('temba.orgs.models.Org.lock_on') as mock_lock:
            # import contact with uuid will force update if existing contact for the uuid
            self.assertContactImport('%s/test_imports/sample_contacts_uuid.xls' % settings.MEDIA_ROOT,
                                     dict(records=4, errors=0, error_messages=[], creates=2, updates=2))
            self.assertEquals(mock_lock.call_count, 3)

        self.assertEquals(1, Contact.objects.filter(name='Eric Newcomer').count())
        self.assertEquals(0, Contact.objects.filter(name='Bob').count())
        self.assertEquals(0, Contact.objects.filter(name='Kobe').count())
        eric = Contact.objects.filter(name='Eric Newcomer').first()
        michael = Contact.objects.filter(name='Michael').first()
        self.assertEqual(eric.pk, contact.pk)
        self.assertEqual(michael.pk, contact2.pk)
        self.assertEquals('uuid-1111', eric.uuid)
        self.assertEquals('uuid-4444', michael.uuid)
        self.assertFalse(Contact.objects.filter(uuid='uuid-3333'))  # previously non-existent uuid ignored

        # new urn added for eric
        self.assertEqual(list(eric.get_urns().values_list('path', flat=True)), ['+250788111111', '+250788382382'])
        self.assertEqual(list(michael.get_urns().values_list('path', flat=True)), ['+250788383396'])

        Contact.objects.all().delete()
        ContactGroup.user_groups.all().delete()
        contact = self.create_contact(name="Bob", number='+250788111111')
        contact.uuid = 'uuid-1111'
        contact.save()

        contact2 = self.create_contact(name='Kobe', number='+250788383396')
        contact2.uuid = 'uuid-4444'
        contact2.save()

        self.assertEqual(list(contact.get_urns().values_list('path', flat=True)), ['+250788111111'])
        self.assertEqual(list(contact2.get_urns().values_list('path', flat=True)), ['+250788383396'])

        with AnonymousOrg(self.org):
            self.login(self.editor)

            with patch('temba.orgs.models.Org.lock_on') as mock_lock:
                # import contact with uuid will force update if existing contact for the uuid
                self.assertContactImport('%s/test_imports/sample_contacts_uuid.xls' % settings.MEDIA_ROOT,
                                         dict(records=4, errors=0, error_messages=[], creates=2, updates=2))

                # we ignore urns so 1 less lock
                self.assertEquals(mock_lock.call_count, 2)

            self.assertEquals(1, Contact.objects.filter(name='Eric Newcomer').count())
            self.assertEquals(0, Contact.objects.filter(name='Bob').count())
            self.assertEquals(0, Contact.objects.filter(name='Kobe').count())
            self.assertEquals('uuid-1111', Contact.objects.filter(name='Eric Newcomer').first().uuid)
            self.assertEquals('uuid-4444', Contact.objects.filter(name='Michael').first().uuid)
            self.assertFalse(Contact.objects.filter(uuid='uuid-3333'))  # previously non-existent uuid ignored

            eric = Contact.objects.filter(name='Eric Newcomer').first()
            michael = Contact.objects.filter(name='Michael').first()
            self.assertEqual(eric.pk, contact.pk)
            self.assertEqual(michael.pk, contact2.pk)
            self.assertEquals('uuid-1111', eric.uuid)
            self.assertEquals('uuid-4444', michael.uuid)
            self.assertFalse(Contact.objects.filter(uuid='uuid-3333'))  # previously non-existent uuid ignored

            # new urn ignored for eric
            self.assertEqual(list(eric.get_urns().values_list('path', flat=True)), ['+250788111111'])
            self.assertEqual(list(michael.get_urns().values_list('path', flat=True)), ['+250788383396'])

        # now log in as an admin, admins can import into anonymous imports
        self.login(self.admin)

        with AnonymousOrg(self.org):
            self.assertContactImport('%s/test_imports/sample_contacts_uuid.xls' % settings.MEDIA_ROOT,
                                     dict(records=4, errors=0, error_messages=[], creates=1, updates=3))

            Contact.objects.all().delete()
            ContactGroup.user_groups.all().delete()

            self.assertContactImport('%s/test_imports/sample_contacts.xls' % settings.MEDIA_ROOT,
                                     dict(records=3, errors=0, error_messages=[], creates=3, updates=0))

        Contact.objects.all().delete()
        ContactGroup.user_groups.all().delete()

        # import sample contact spreadsheet with valid headers
        self.assertContactImport('%s/test_imports/sample_contacts.xls' % settings.MEDIA_ROOT,
                                 dict(records=3, errors=0, error_messages=[], creates=3, updates=0))

        # import again to check contacts are updated
        self.assertContactImport('%s/test_imports/sample_contacts.xls' % settings.MEDIA_ROOT,
                                 dict(records=3, errors=0, error_messages=[], creates=0, updates=3))

        # import a spreadsheet that includes the test contact
        self.assertContactImport('%s/test_imports/sample_contacts_inc_test.xls' % settings.MEDIA_ROOT,
                                 dict(records=2, errors=1, creates=0, updates=2,
                                      error_messages=[dict(line=4, error="Ignored test contact")]))

        self.maxDiff = None

        # import a spreadsheet where a contact has a missing phone number and another has an invalid number
        self.assertContactImport('%s/test_imports/sample_contacts_with_missing_and_invalid_phones.xls' % settings.MEDIA_ROOT,
                                 dict(records=1, errors=2, creates=0, updates=1,
                                      error_messages=[dict(line=3,
                                                           error="Missing any valid URNs; at least one among phone, "
                                                                 "twitter, telegram, email, facebook, external should be provided"),
                                                      dict(line=4, error="Invalid Phone number 12345")]))

        # import a spreadsheet with a name and a twitter columns only
        self.assertContactImport('%s/test_imports/sample_contacts_twitter.xls' % settings.MEDIA_ROOT,
                                 dict(records=3, errors=0, error_messages=[], creates=3, updates=0))

        Contact.objects.all().delete()
        ContactGroup.user_groups.all().delete()

        # import a spreadsheet with phone, name and twitter columns
        self.assertContactImport('%s/test_imports/sample_contacts_twitter_and_phone.xls' % settings.MEDIA_ROOT,
                                 dict(records=3, errors=0, error_messages=[], creates=3, updates=0))

        self.assertEquals(3, Contact.objects.all().count())
        self.assertEquals(1, Contact.objects.filter(name='Rapidpro').count())
        self.assertEquals(1, Contact.objects.filter(name='Textit').count())
        self.assertEquals(1, Contact.objects.filter(name='Nyaruka').count())

        # import file with row different urn on different existing contacts should ignore those lines
        self.assertContactImport('%s/test_imports/sample_contacts_twitter_and_phone_conflicts.xls' % settings.MEDIA_ROOT,
                                 dict(records=2, errors=0, creates=0, updates=2, error_messages=[]))

        self.assertEquals(3, Contact.objects.all().count())
        self.assertEquals(1, Contact.objects.filter(name='Rapidpro').count())
        self.assertEquals(0, Contact.objects.filter(name='Textit').count())
        self.assertEquals(0, Contact.objects.filter(name='Nyaruka').count())
        self.assertEquals(1, Contact.objects.filter(name='Kigali').count())
        self.assertEquals(1, Contact.objects.filter(name='Klab').count())

        Contact.objects.all().delete()
        ContactGroup.user_groups.all().delete()

        # some columns have either twitter or phone
        self.assertContactImport('%s/test_imports/sample_contacts_twitter_and_phone_optional.xls' % settings.MEDIA_ROOT,
                                 dict(records=3, errors=0, error_messages=[], creates=3, updates=0))

        Contact.objects.all().delete()
        ContactGroup.user_groups.all().delete()
        contact = self.create_contact(name="Bob", number='+250788111111')
        contact.uuid = 'uuid-1111'
        contact.save()

        contact2 = self.create_contact(name='Kobe', number='+250788383396')
        contact2.uuid = 'uuid-4444'
        contact2.save()

        self.assertEqual(list(contact.get_urns().values_list('path', flat=True)), ['+250788111111'])
        self.assertEqual(list(contact2.get_urns().values_list('path', flat=True)), ['+250788383396'])

        with patch('temba.orgs.models.Org.lock_on') as mock_lock:
            # import contact with uuid will force update if existing contact for the uuid
            self.assertContactImport('%s/test_imports/sample_contacts_uuid_no_urns.xls' % settings.MEDIA_ROOT,
                                     dict(records=3, errors=1, creates=1, updates=2,
                                          error_messages=[dict(line=3,
                                                          error="Missing any valid URNs; at least one among phone, "
                                                                "twitter, telegram, email, facebook, external should be provided")]))

            # lock for creates only
            self.assertEquals(mock_lock.call_count, 1)

        self.assertEquals(1, Contact.objects.filter(name='Eric Newcomer').count())
        self.assertEquals(0, Contact.objects.filter(name='Bob').count())
        self.assertEquals(0, Contact.objects.filter(name='Kobe').count())
        eric = Contact.objects.filter(name='Eric Newcomer').first()
        michael = Contact.objects.filter(name='Michael').first()
        self.assertEqual(eric.pk, contact.pk)
        self.assertEqual(michael.pk, contact2.pk)
        self.assertEquals('uuid-1111', eric.uuid)
        self.assertEquals('uuid-4444', michael.uuid)
        self.assertFalse(Contact.objects.filter(uuid='uuid-3333'))  # previously non-existent uuid ignored

        # new urn added for eric
        self.assertEqual(list(eric.get_urns().values_list('path', flat=True)), ['+250788111111'])
        self.assertEqual(list(michael.get_urns().values_list('path', flat=True)), ['+250788383396'])

        with AnonymousOrg(self.org):
            with patch('temba.orgs.models.Org.lock_on') as mock_lock:
                # import contact with uuid will force update if existing contact for the uuid for anoa orrg as well
                self.assertContactImport('%s/test_imports/sample_contacts_uuid_no_urns.xls' % settings.MEDIA_ROOT,
                                         dict(records=4, errors=0, error_messages=[], creates=2, updates=2))

                # lock for creates only
                self.assertEquals(mock_lock.call_count, 2)

            self.assertEquals(1, Contact.objects.filter(name='Eric Newcomer').count())
            self.assertEquals(0, Contact.objects.filter(name='Bob').count())
            self.assertEquals(0, Contact.objects.filter(name='Kobe').count())
            self.assertEquals('uuid-1111', Contact.objects.filter(name='Eric Newcomer').first().uuid)
            self.assertEquals('uuid-4444', Contact.objects.filter(name='Michael').first().uuid)
            self.assertFalse(Contact.objects.filter(uuid='uuid-3333'))  # previously non-existent uuid ignored

            eric = Contact.objects.filter(name='Eric Newcomer').first()
            michael = Contact.objects.filter(name='Michael').first()
            self.assertEqual(eric.pk, contact.pk)
            self.assertEqual(michael.pk, contact2.pk)
            self.assertEquals('uuid-1111', eric.uuid)
            self.assertEquals('uuid-4444', michael.uuid)
            self.assertFalse(Contact.objects.filter(uuid='uuid-3333'))  # previously non-existent uuid ignored

            # new urn ignored for eric
            self.assertEqual(list(eric.get_urns().values_list('path', flat=True)), ['+250788111111'])
            self.assertEqual(list(michael.get_urns().values_list('path', flat=True)), ['+250788383396'])

        Contact.objects.all().delete()
        ContactGroup.user_groups.all().delete()
        contact = self.create_contact(name="Bob", number='+250788111111')
        contact.uuid = 'uuid-1111'
        contact.save()

        contact2 = self.create_contact(name='Kobe', number='+250788383396')
        contact2.uuid = 'uuid-4444'
        contact2.save()

        self.assertEqual(list(contact.get_urns().values_list('path', flat=True)), ['+250788111111'])
        self.assertEqual(list(contact2.get_urns().values_list('path', flat=True)), ['+250788383396'])

        with patch('temba.orgs.models.Org.lock_on') as mock_lock:
            # import contact with uuid will force update if existing contact for the uuid, csv file
            self.assertContactImport('%s/test_imports/sample_contacts_uuid_no_urns.csv' % settings.MEDIA_ROOT,
                                     dict(records=3, errors=1, creates=1, updates=2,
                                          error_messages=[dict(line=3,
                                                          error="Missing any valid URNs; at least one among phone, "
                                                                "twitter, telegram, email, facebook, external should be provided")]))

            # only lock for create
            self.assertEquals(mock_lock.call_count, 1)

        self.assertEquals(1, Contact.objects.filter(name='Eric Newcomer').count())
        self.assertEquals(0, Contact.objects.filter(name='Bob').count())
        self.assertEquals(0, Contact.objects.filter(name='Kobe').count())
        eric = Contact.objects.filter(name='Eric Newcomer').first()
        michael = Contact.objects.filter(name='Michael').first()
        self.assertEqual(eric.pk, contact.pk)
        self.assertEqual(michael.pk, contact2.pk)
        self.assertEquals('uuid-1111', eric.uuid)
        self.assertEquals('uuid-4444', michael.uuid)
        self.assertFalse(Contact.objects.filter(uuid='uuid-3333'))  # previously non-existent uuid ignored

        # new urn added for eric
        self.assertEqual(list(eric.get_urns().values_list('path', flat=True)), ['+250788111111'])
        self.assertEqual(list(michael.get_urns().values_list('path', flat=True)), ['+250788383396'])

        with AnonymousOrg(self.org):
            with patch('temba.orgs.models.Org.lock_on') as mock_lock:
                # import contact with uuid will force update if existing contact for the uuid,csv file for anon org
                self.assertContactImport('%s/test_imports/sample_contacts_uuid_no_urns.csv' % settings.MEDIA_ROOT,
                                         dict(records=4, errors=0, error_messages=[], creates=2, updates=2))

                # only lock for creates
                self.assertEquals(mock_lock.call_count, 2)

            self.assertEquals(1, Contact.objects.filter(name='Eric Newcomer').count())
            self.assertEquals(0, Contact.objects.filter(name='Bob').count())
            self.assertEquals(0, Contact.objects.filter(name='Kobe').count())
            self.assertEquals('uuid-1111', Contact.objects.filter(name='Eric Newcomer').first().uuid)
            self.assertEquals('uuid-4444', Contact.objects.filter(name='Michael').first().uuid)
            self.assertFalse(Contact.objects.filter(uuid='uuid-3333'))  # previously non-existent uuid ignored

            eric = Contact.objects.filter(name='Eric Newcomer').first()
            michael = Contact.objects.filter(name='Michael').first()
            self.assertEqual(eric.pk, contact.pk)
            self.assertEqual(michael.pk, contact2.pk)
            self.assertEquals('uuid-1111', eric.uuid)
            self.assertEquals('uuid-4444', michael.uuid)
            self.assertFalse(Contact.objects.filter(uuid='uuid-3333'))  # previously non-existent uuid ignored

            # new urn ignored for eric
            self.assertEqual(list(eric.get_urns().values_list('path', flat=True)), ['+250788111111'])
            self.assertEqual(list(michael.get_urns().values_list('path', flat=True)), ['+250788383396'])

        Contact.objects.all().delete()
        ContactGroup.user_groups.all().delete()
        contact = self.create_contact(name="Bob", number='+250788111111')
        contact.uuid = 'uuid-1111'
        contact.save()

        contact2 = self.create_contact(name='Kobe', number='+250788383396')
        contact2.uuid = 'uuid-4444'
        contact2.save()

        self.assertEqual(list(contact.get_urns().values_list('path', flat=True)), ['+250788111111'])
        self.assertEqual(list(contact2.get_urns().values_list('path', flat=True)), ['+250788383396'])

        with patch('temba.orgs.models.Org.lock_on') as mock_lock:
            # import contact with uuid column to group the contacts
            self.assertContactImport('%s/test_imports/sample_contacts_uuid_only.csv' % settings.MEDIA_ROOT,
                                     dict(records=3, errors=0, error_messages=[], creates=1, updates=2))

            # one lock for the create
            self.assertEquals(mock_lock.call_count, 1)

        self.assertEquals(1, Contact.objects.filter(name='Bob').count())
        self.assertEquals(1, Contact.objects.filter(name='Kobe').count())
        self.assertFalse(Contact.objects.filter(uuid='uuid-3333'))  # previously non-existent uuid ignored

        with AnonymousOrg(self.org):
            with patch('temba.orgs.models.Org.lock_on') as mock_lock:
                # import contact with uuid column to group the contacts for anon org
                self.assertContactImport('%s/test_imports/sample_contacts_uuid_only.csv' % settings.MEDIA_ROOT,
                                         dict(records=3, errors=0, error_messages=[], creates=1, updates=2))

                # one lock for the create
                self.assertEquals(mock_lock.call_count, 1)

            self.assertEquals(1, Contact.objects.filter(name='Bob').count())
            self.assertEquals(1, Contact.objects.filter(name='Kobe').count())
            self.assertFalse(Contact.objects.filter(uuid='uuid-3333'))  # previously non-existent uuid ignored

        Contact.objects.all().delete()
        ContactGroup.user_groups.all().delete()

        with patch('temba.contacts.models.Org.get_country_code') as mock_country_code:
            mock_country_code.return_value = None

            self.assertContactImport(
                '%s/test_imports/sample_contacts_org_missing_country.csv' % settings.MEDIA_ROOT,
                dict(records=0, errors=1,
                     error_messages=[dict(line=2,
                                          error="Invalid Phone number or no country code specified for 788383385")]))

        # try importing invalid spreadsheets with missing headers
        csv_file = open('%s/test_imports/sample_contacts_missing_name_header.xls' % settings.MEDIA_ROOT, 'rb')
        post_data = dict(csv_file=csv_file)
        response = self.client.post(import_url, post_data)
        self.assertFormError(response, 'form', 'csv_file',
                             'The file you provided is missing a required header called "Name".')

        csv_file = open('%s/test_imports/sample_contacts_missing_phone_header.xls' % settings.MEDIA_ROOT, 'rb')
        post_data = dict(csv_file=csv_file)
        response = self.client.post(import_url, post_data)
        self.assertFormError(response, 'form', 'csv_file',
                             'The file you provided is missing a required header. At least one of "Phone", "Twitter", '
                             '"Telegram", "Email", "Facebook", "External" should be included.')

        csv_file = open('%s/test_imports/sample_contacts_missing_name_phone_headers.xls' % settings.MEDIA_ROOT, 'rb')
        post_data = dict(csv_file=csv_file)
        response = self.client.post(import_url, post_data)
        self.assertFormError(response, 'form', 'csv_file',
                             'The file you provided is missing a required header. At least one of "Phone", "Twitter", '
                             '"Telegram", "Email", "Facebook", "External" should be included.')

        # check that no contacts or groups were created by any of the previous invalid imports
        self.assertEquals(Contact.objects.all().count(), 0)
        self.assertEquals(ContactGroup.user_groups.all().count(), 0)

        # existing field
        ContactField.get_or_create(self.org, self.admin, 'ride_or_drive', 'Vehicle')
        ContactField.get_or_create(self.org, self.admin, 'wears', 'Shoes')  # has trailing spaces on excel files as " Shoes  "

        # import spreadsheet with extra columns
        response = self.assertContactImport('%s/test_imports/sample_contacts_with_extra_fields.xls' % settings.MEDIA_ROOT,
                                            None, task_customize=True, custom_fields_number=21)

        # all checkboxes should default to True
        for key in response.context['form'].fields.keys():
            if key.endswith('_include'):
                self.assertTrue(response.context['form'].fields[key].initial)

        customize_url = reverse('contacts.contact_customize', args=[response.context['task'].pk])
        post_data = dict()
        post_data['column_country_include'] = 'on'
        post_data['column_professional_status_include'] = 'on'
        post_data['column_zip_code_include'] = 'on'
        post_data['column_joined_include'] = 'on'
        post_data['column_vehicle_include'] = 'on'
        post_data['column_shoes_include'] = 'on'

        post_data['column_country_label'] = '[_NEW_]Location'
        post_data['column_district_label'] = 'District'
        post_data['column_professional_status_label'] = 'Job and Projects'
        post_data['column_zip_code_label'] = 'Postal Code'
        post_data['column_joined_label'] = 'Joined'
        post_data['column_vehicle_label'] = 'Vehicle'
        post_data['column_shoes_label'] = ' Shoes  '

        post_data['column_country_type'] = 'T'
        post_data['column_district_type'] = 'T'
        post_data['column_professional_status_type'] = 'T'
        post_data['column_zip_code_type'] = 'N'
        post_data['column_joined_type'] = 'D'
        post_data['column_vehicle_type'] = 'T'
        post_data['column_shoes_type'] = 'N'

        response = self.client.post(customize_url, post_data, follow=True)
        self.assertEquals(response.context['results'], dict(records=3, errors=0, error_messages=[], creates=3,
                                                            updates=0))
        self.assertEquals(Contact.objects.all().count(), 3)
        self.assertEquals(ContactGroup.user_groups.all().count(), 1)
        self.assertEquals(ContactGroup.user_groups.all()[0].name, 'Sample Contacts With Extra Fields')

        contact1 = Contact.objects.all().order_by('name')[0]
        self.assertEquals(contact1.get_field_raw('location'), 'Rwanda')  # renamed from 'Country'
        self.assertEquals(contact1.get_field_display('location'), 'Rwanda')  # renamed from 'Country'

        self.assertEquals(contact1.get_field_raw('ride_or_drive'), 'Moto')  # the existing field was looked up by label
        self.assertEquals(contact1.get_field_raw('wears'), 'Nike')  # existing field was looked up by label & stripped

        self.assertEquals(contact1.get_urn(schemes=[TWITTER_SCHEME]).path, 'ewok')
        self.assertEquals(contact1.get_urn(schemes=[EXTERNAL_SCHEME]).path, 'abc-1111')
        self.assertEquals(contact1.get_urn(schemes=[EMAIL_SCHEME]).path, 'eric@example.com')

        # if we change the field type for 'location' to 'datetime' we shouldn't get a category
        ContactField.objects.filter(key='location').update(value_type=Value.TYPE_DATETIME)
        contact1 = Contact.objects.all().order_by('name')[0]

        # not a valid date, so should be None
        self.assertEquals(contact1.get_field_display('location'), None)

        # return it back to a state field
        ContactField.objects.filter(key='location').update(value_type=Value.TYPE_STATE)
        contact1 = Contact.objects.all().order_by('name')[0]

        self.assertIsNone(contact1.get_field_raw('district'))  # wasn't included
        self.assertEquals(contact1.get_field_raw('job_and_projects'), 'coach')  # renamed from 'Professional Status'
        self.assertEquals(contact1.get_field_raw('postal_code'), '600.0')
        self.assertEquals(contact1.get_field_raw('joined'), '31-12-2014 00:00')  # persisted value is localized to org
        self.assertEquals(contact1.get_field_display('joined'), '31-12-2014 00:00')  # display value is also localized

        self.assertTrue(ContactField.objects.filter(org=self.org, label="Job and Projects"))
        self.assertTrue(ContactField.objects.filter(org=self.org, label="Location"))

        # we never update existing contact fields labels or value types
        self.assertTrue(ContactField.objects.filter(org=self.org, label="Shoes", value_type='T'))
        self.assertFalse(ContactField.objects.filter(org=self.org, label="Shoes", value_type='N'))

        # import spreadsheet with extra columns again but check that giving column a reserved name
        # gives validation error
        response = self.assertContactImport('%s/test_imports/sample_contacts_with_extra_fields.xls' % settings.MEDIA_ROOT,
                                            None, task_customize=True)
        customize_url = reverse('contacts.contact_customize', args=[response.context['task'].pk])
        post_data = dict()
        post_data['column_country_include'] = 'on'
        post_data['column_professional_status_include'] = 'on'
        post_data['column_zip_code_include'] = 'on'
        post_data['column_joined_include'] = 'on'

        post_data['column_country_label'] = 'Name'  # reserved when slugified to 'name'
        post_data['column_district_label'] = 'District'
        post_data['column_professional_status_label'] = 'Job and Projects'
        post_data['column_zip_code_label'] = 'Postal Code'
        post_data['column_joined_label'] = 'Joined'

        post_data['column_country_type'] = 'T'
        post_data['column_district_type'] = 'T'
        post_data['column_professional_status_type'] = 'T'
        post_data['column_zip_code_type'] = 'N'
        post_data['column_joined_type'] = 'D'

        response = self.client.post(customize_url, post_data, follow=True)
        self.assertFormError(response, 'form', None, 'Name is an invalid name or is a reserved name for contact '
                                                     'fields, field names should start with a letter.')

        # we do not support names not starting by letter
        post_data['column_country_label'] = '12Project'  # reserved when slugified to 'name'

        response = self.client.post(customize_url, post_data, follow=True)
        self.assertFormError(response, 'form', None, '12Project is an invalid name or is a reserved name for contact '
                                                     'fields, field names should start with a letter.')

        # invalid label
        post_data['column_country_label'] = '}{i$t0rY'  # supports only numbers, letters, hyphens

        response = self.client.post(customize_url, post_data, follow=True)
        self.assertFormError(response, 'form', None, "Field names can only contain letters, numbers, hypens")

        post_data['column_joined_label'] = 'District'

        response = self.client.post(customize_url, post_data, follow=True)
        self.assertFormError(response, 'form', None, 'District should be used once')

        post_data['column_joined_label'] = '[_NEW_]District'

        response = self.client.post(customize_url, post_data, follow=True)
        self.assertFormError(response, 'form', None, 'District should be used once')

        # wrong field with reserve word key
        ContactField.objects.create(org=self.org, key='language', label='Lang',
                                    created_by=self.admin, modified_by=self.admin)

        response = self.assertContactImport(
            '%s/test_imports/sample_contacts_with_extra_fields_wrong_lang.xls' % settings.MEDIA_ROOT,
            None, task_customize=True)

        customize_url = reverse('contacts.contact_customize', args=[response.context['task'].pk])
        post_data = dict()
        post_data['column_lang_include'] = 'on'
        post_data['column_lang_label'] = 'Lang'
        post_data['column_lang_type'] = 'T'

        response = self.client.post(customize_url, post_data, follow=True)
        self.assertFormError(response, 'form', None, "'Lang' contact field has 'language' key which is reserved name. "
                                                     "Column cannot be imported")

        # we shouldn't be suspended
        self.org.refresh_from_db()
        self.assertFalse(self.org.is_suspended())

        # invalid import params
        with self.assertRaises(Exception):
            task = ImportTask.objects.create(
                created_by=user, modified_by=user,
                csv_file='test_imports/filename',
                model_class="Contact", import_params='bogus!', import_log="", task_id="A")
            Contact.import_csv(task, log=None)

    def test_contact_import_with_languages(self):
        self.create_contact(name="Eric", number="+250788382382")

        self.do_import(self.user, 'sample_contacts_with_language.xls')

        self.assertEqual(Contact.objects.get(urns__path="+250788382382").language, 'eng')  # updated
        self.assertEqual(Contact.objects.get(urns__path="+250788383383").language, 'fre')  # created with language
        self.assertEqual(Contact.objects.get(urns__path="+250788383385").language, None)   # no language

    def test_import_sequential_numbers(self):

        org = self.user.get_org()
        self.assertFalse(org.is_suspended())

        # importing sequential numbers should automatically suspend our org
        self.do_import(self.user, 'sample_contacts_sequential.xls')
        org.refresh_from_db()
        self.assertTrue(org.is_suspended())

        # now whitelist the account
        self.org.set_whitelisted()
        self.do_import(self.user, 'sample_contacts_sequential.xls')
        org.refresh_from_db()
        self.assertFalse(org.is_suspended())

    def test_import_methods(self):
        user = self.user
        c1 = self.create_contact(name=None, number='0788382382')
        c2 = self.create_contact(name=None, number='0788382382')
        self.assertEquals(c1.pk, c2.pk)

        field_dict = dict(phone='0788123123', created_by=user, modified_by=user, org=self.org, name='LaToya Jackson')
        c1 = Contact.create_instance(field_dict)

        field_dict = dict(phone='0788123123', created_by=user, modified_by=user, org=self.org, name='LaToya Jackson')
        field_dict['name'] = 'LaToya Jackson'
        c2 = Contact.create_instance(field_dict)
        self.assertEquals(c1.pk, c2.pk)

        c1.block(self.user)
        field_dict = dict(phone='0788123123', created_by=user, modified_by=user, org=self.org, name='LaToya Jackson')
        field_dict['name'] = 'LaToya Jackson'
        c2 = Contact.create_instance(field_dict)
        self.assertEquals(c1.pk, c2.pk)
        self.assertFalse(c2.is_blocked)

        import_params = dict(org_id=self.org.id, timezone=timezone.UTC, extra_fields=[
            dict(key='nick_name', header='nick name', label='Nickname', type='T')
        ])
        field_dict = dict(phone='0788123123', created_by=user, modified_by=user, org=self.org, name='LaToya Jackson')
        field_dict['yourmom'] = 'face'
        field_dict['nick name'] = 'bob'
        field_dict = Contact.prepare_fields(field_dict, import_params, user=user)
        self.assertNotIn('yourmom', field_dict)
        self.assertNotIn('nick name', field_dict)
        self.assertEquals(field_dict['nick_name'], 'bob')
        self.assertEquals(field_dict['org'], self.org)

        # missing important import params
        with self.assertRaises(Exception):
            Contact.prepare_fields(field_dict, dict())

        # check that trying to save an extra field with a reserved name throws an exception
        with self.assertRaises(Exception):
            import_params = dict(org_id=self.org.id, timezone=timezone.UTC, extra_fields=[
                dict(key='phone', header='phone', label='Phone')
            ])
            Contact.prepare_fields(field_dict, import_params)

        with AnonymousOrg(self.org):
            # should existing urns on anon org
            with self.assertRaises(SmartImportRowError):
                field_dict = dict(phone='0788123123', created_by=user, modified_by=user,
                                  org=self.org, name='LaToya Jackson')
                Contact.create_instance(field_dict)

            field_dict = dict(phone='0788123123', created_by=user, modified_by=user,
                              org=self.org, name='Janet Jackson', uuid=c1.uuid)
            c3 = Contact.create_instance(field_dict)
            self.assertEqual(c3.pk, c1.pk)
            self.assertEqual(c3.name, "Janet Jackson")

    def test_fields(self):
        # set a field on joe
        self.joe.set_field(self.user, 'abc_1234', 'Joe', label="Name")
        self.assertEquals('Joe', self.joe.get_field_raw('abc_1234'))

        self.joe.set_field(self.user, 'abc_1234', None)
        self.assertEquals(None, self.joe.get_field_raw('abc_1234'))

        # try storing an integer, should get turned into a string
        self.joe.set_field(self.user, 'abc_1234', 1)
        self.assertEquals('1', self.joe.get_field_raw('abc_1234'))

        # we should have a field with the key
        ContactField.objects.get(key='abc_1234', label="Name", org=self.joe.org)

        # setting with a different label should update it
        self.joe.set_field(self.user, 'abc_1234', 'Joe', label="First Name")
        self.assertEquals('Joe', self.joe.get_field_raw('abc_1234'))
        ContactField.objects.get(key='abc_1234', label="First Name", org=self.joe.org)

    def test_date_field(self):
        # create a new date field
        ContactField.get_or_create(self.org, self.admin, 'birth_date', label='Birth Date', value_type=Value.TYPE_TEXT)

        # set a field on our contact
        urn = 'urn:uuid:0f73262c-0623-3f0a-8651-1855e755d2ef'
        self.joe.set_field(self.user, 'birth_date', urn)

        # check that this field has been set
        self.assertEqual(self.joe.get_field('birth_date').string_value, urn)
        self.assertIsNone(self.joe.get_field('birth_date').decimal_value)
        self.assertIsNone(self.joe.get_field('birth_date').datetime_value)

    def test_serialize_field_value(self):
        registration_field = ContactField.get_or_create(self.org, self.admin, 'registration_date', "Registration Date",
                                                        None, Value.TYPE_DATETIME)

        weight_field = ContactField.get_or_create(self.org, self.admin, 'weight', "Weight", None, Value.TYPE_DECIMAL)
        color_field = ContactField.get_or_create(self.org, self.admin, 'color', "Color", None, Value.TYPE_TEXT)
        state_field = ContactField.get_or_create(self.org, self.admin, 'state', "State", None, Value.TYPE_STATE)

        joe = Contact.objects.get(pk=self.joe.pk)
        joe.set_field(self.user, 'registration_date', "2014-12-31 03:04:00")
        joe.set_field(self.user, 'weight', "75.888888")
        joe.set_field(self.user, 'color', "green")
        joe.set_field(self.user, 'state', "kigali city")

        value = joe.get_field(registration_field.key)
        self.assertEqual(Contact.serialize_field_value(registration_field, value), '2014-12-31T01:04:00.000000Z')

        value = joe.get_field(weight_field.key)
        self.assertEqual(Contact.serialize_field_value(weight_field, value), '75.888888')

        value = joe.get_field(state_field.key)
        self.assertEqual(Contact.serialize_field_value(state_field, value), 'Kigali City')

        value = joe.get_field(color_field.key)
        value.category = "Dark"
        value.save()

        self.assertEqual(Contact.serialize_field_value(color_field, value), 'Dark')

    def test_set_location_fields(self):
        district_field = ContactField.get_or_create(self.org, self.admin, 'district', 'District', None, Value.TYPE_DISTRICT)

        # add duplicate district in different states
        east_province = AdminBoundary.objects.create(osm_id='R005', name='East Province', level=1, parent=self.country)
        AdminBoundary.objects.create(osm_id='R004', name='Remera', level=2, parent=east_province)
        kigali = AdminBoundary.objects.get(name="Kigali City")
        AdminBoundary.objects.create(osm_id='R003', name='Remera', level=2, parent=kigali)

        joe = Contact.objects.get(pk=self.joe.pk)
        joe.set_field(self.user, 'district', 'Remera')
        value = Value.objects.filter(contact=joe, contact_field=district_field).first()
        self.assertFalse(value.location_value)

        state_field = ContactField.get_or_create(self.org, self.admin, 'state', 'State', None, Value.TYPE_STATE)

        joe.set_field(self.user, 'state', 'Kigali city')
        value = Value.objects.filter(contact=joe, contact_field=state_field).first()
        self.assertTrue(value.location_value)
        self.assertEquals(value.location_value.name, "Kigali City")

        joe.set_field(self.user, 'district', 'Remera')
        value = Value.objects.filter(contact=joe, contact_field=district_field).first()
        self.assertTrue(value.location_value)
        self.assertEquals(value.location_value.name, "Remera")
        self.assertEquals(value.location_value.parent, kigali)

    def test_set_location_ward_fields(self):

        state = AdminBoundary.objects.create(osm_id='3710302', name='Kano', level=1, parent=self.country)
        district = AdminBoundary.objects.create(osm_id='3710307', name='Bichi', level=2, parent=state)
        ward = AdminBoundary.objects.create(osm_id='3710377', name='Bichi', level=3, parent=district)
        user1 = self.create_user("mcren")

        ContactField.get_or_create(self.org, user1, 'state', 'State', None, Value.TYPE_STATE)
        ContactField.get_or_create(self.org, user1, 'district', 'District', None, Value.TYPE_DISTRICT)
        ward_field = ContactField.get_or_create(self.org, user1, 'ward', 'Ward', None, Value.TYPE_WARD)

        jemila = self.create_contact(name="Jemila Alley", number="123", twitter="fulani_p")
        jemila.set_field(user1, 'state', 'kano')
        jemila.set_field(user1, 'district', 'bichi')
        jemila.set_field(user1, 'ward', 'bichi')
        value = Value.objects.filter(contact=jemila, contact_field=ward_field).first()
        self.assertEquals(value.location_value, ward)

    def test_message_context(self):
        message_context = self.joe.build_message_context()

        self.assertEquals("Joe", message_context['first_name'])
        self.assertEquals("Joe Blow", message_context['name'])
        self.assertEquals("Joe Blow", message_context['__default__'])
        self.assertEquals("123", message_context['tel'])
        self.assertEquals("", message_context['groups'])
        self.assertTrue('uuid' in message_context)
        self.assertEquals(self.joe.uuid, message_context['uuid'])

        # add him to a group
        self.create_group("Reporters", [self.joe])

        # create a few contact fields, one active, one not
        ContactField.get_or_create(self.org, self.admin, 'team')
        fav_color = ContactField.get_or_create(self.org, self.admin, 'color')

        self.joe = Contact.objects.get(pk=self.joe.pk)
        self.joe.set_field(self.admin, 'color', "Blue")
        self.joe.set_field(self.admin, 'team', "SeaHawks")

        # make color inactivate
        fav_color.is_active = False
        fav_color.save()

        message_context = self.joe.build_message_context()

        self.assertEquals("Joe", message_context['first_name'])
        self.assertEquals("Joe Blow", message_context['name'])
        self.assertEquals("Joe Blow", message_context['__default__'])
        self.assertEquals("123", message_context['tel'])
        self.assertEquals("Reporters", message_context['groups'])

        self.assertEqual("SeaHawks", message_context['team'])
        self.assertFalse('color' in message_context)

    def test_urn_priority(self):
        bob = self.create_contact("Bob")

        bob.update_urns(self.user, ['tel:456', 'tel:789'])
        urns = bob.urns.all().order_by('-priority')
        self.assertEquals(2, len(urns))
        self.assertEquals('456', urns[0].path)
        self.assertEquals('789', urns[1].path)
        self.assertEquals(99, urns[0].priority)
        self.assertEquals(98, urns[1].priority)

        bob.update_urns(self.user, ['tel:789', 'tel:456'])
        urns = bob.urns.all().order_by('-priority')
        self.assertEquals(2, len(urns))
        self.assertEquals('789', urns[0].path)
        self.assertEquals('456', urns[1].path)

        # add an email urn
        bob.update_urns(self.user, ['mailto:bob@marley.com', 'tel:789', 'tel:456'])
        urns = bob.urns.all().order_by('-priority')
        self.assertEquals(3, len(urns))
        self.assertEquals(99, urns[0].priority)
        self.assertEquals(98, urns[1].priority)
        self.assertEquals(97, urns[2].priority)

        # it'll come back as the highest priority
        self.assertEquals('bob@marley.com', urns[0].path)

        # but not the highest 'sendable' urn
        contact, urn = Msg.resolve_recipient(self.org, self.admin, bob, self.channel)
        self.assertEquals(urn.path, '789')

        # swap our phone numbers
        bob.update_urns(self.user, ['mailto:bob@marley.com', 'tel:456', 'tel:789'])
        contact, urn = Msg.resolve_recipient(self.org, self.admin, bob, self.channel)
        self.assertEquals(urn.path, '456')

    def test_update_handling(self):
        bob = self.create_contact("Bob", "111222")
        bob.name = 'Bob Marley'
        bob.save()

        group = self.create_group("Customers", [])

        old_modified_on = bob.modified_on
        bob.update_urns(self.user, ['tel:111333'])
        self.assertTrue(bob.modified_on > old_modified_on)

        old_modified_on = bob.modified_on
        bob.update_static_groups(self.user, [group])

        bob.refresh_from_db()
        self.assertTrue(bob.modified_on > old_modified_on)

        old_modified_on = bob.modified_on
        bob.set_field(self.user, "nickname", "Bobby")
        self.assertTrue(bob.modified_on > old_modified_on)

        # run all tests as 2/Jan/2014 03:04 AFT
        tz = pytz.timezone('Asia/Kabul')
        with patch.object(timezone, 'now', return_value=tz.localize(datetime(2014, 1, 2, 3, 4, 5, 6))):
            ContactField.get_or_create(self.org, self.admin, 'age', "Age", value_type='N')
            ContactField.get_or_create(self.org, self.admin, 'gender', "Gender", value_type='T')
            joined_field = ContactField.get_or_create(self.org, self.admin, 'joined', "Join Date", value_type='D')

            # create groups based on name or URN (checks that contacts are added correctly on contact create)
            joes_group = self.create_group("People called Joe", query='name has joe')
            _123_group = self.create_group("People with number containing '123'", query='tel has "123"')

            self.mary = self.create_contact("Mary", "123456")
            self.mary.set_field(self.user, 'gender', "Female")
            self.mary.set_field(self.user, 'age', 21)
            self.mary.set_field(self.user, 'joined', '31/12/2013')
            self.annie = self.create_contact("Annie", "7879")
            self.annie.set_field(self.user, 'gender', "Female")
            self.annie.set_field(self.user, 'age', 9)
            self.annie.set_field(self.user, 'joined', '31/12/2013')
            self.joe.set_field(self.user, 'gender', "Male")
            self.joe.set_field(self.user, 'age', 25)
            self.joe.set_field(self.user, 'joined', '1/1/2014')
            self.frank.set_field(self.user, 'gender', "Male")
            self.frank.set_field(self.user, 'age', 50)
            self.frank.set_field(self.user, 'joined', '1/1/2014')

            # create more groups based on fields (checks that contacts are added correctly on group create)
            men_group = self.create_group("Girls", query='gender = "male" AND age >= 18')
            women_group = self.create_group("Girls", query='gender = "female" AND age >= 18')

            joe_flow = self.create_flow()
            joes_campaign = Campaign.create(self.org, self.admin, "Joe Reminders", joes_group)
            joes_event = CampaignEvent.create_flow_event(self.org, self.admin, joes_campaign, relative_to=joined_field,
                                                         offset=1, unit='W', flow=joe_flow, delivery_hour=17)
            EventFire.update_campaign_events(joes_campaign)

            # check initial group members added correctly
            self.assertEquals([self.frank, self.joe, self.mary], list(_123_group.contacts.order_by('name')))
            self.assertEquals([self.frank, self.joe], list(men_group.contacts.order_by('name')))
            self.assertEquals([self.mary], list(women_group.contacts.order_by('name')))
            self.assertEquals([self.joe], list(joes_group.contacts.order_by('name')))

            # try removing frank from dynamic group (shouldnt happen, ui doesnt allow this)
            with self.assertRaises(ValueError):
                self.login(self.admin)
                self.client.post(reverse('contacts.contact_read', args=[self.frank.uuid]),
                                 dict(contact=self.frank.pk, group=men_group.pk))

            # check event fire initialized correctly
            joe_fires = EventFire.objects.filter(event=joes_event)
            self.assertEquals(1, joe_fires.count())
            self.assertEquals(self.joe, joe_fires.first().contact)

            # Frank becomes Francine...
            self.frank.set_field(self.user, 'gender', "Female")
            self.assertEquals([self.joe], list(men_group.contacts.order_by('name')))
            self.assertEquals([self.frank, self.mary], list(women_group.contacts.order_by('name')))

            # Mary's name changes
            self.mary.name = "Mary Joe"
            self.mary.save()
            self.mary.handle_update(attrs=('name',))
            self.assertEquals([self.joe, self.mary], list(joes_group.contacts.order_by('name')))

            # Mary should also have an event fire now
            joe_fires = EventFire.objects.filter(event=joes_event)
            self.assertEquals(2, joe_fires.count())

            # change Mary's URNs
            self.mary.update_urns(self.user, ['tel:54321', 'twitter:mary_mary'])
            self.assertEquals([self.frank, self.joe], list(_123_group.contacts.order_by('name')))

    def test_simulator_contact_views(self):
        simulator_contact = Contact.get_test_contact(self.admin)

        other_contact = self.create_contact("Will", "+250788987987")

        group = self.create_group("Members", [simulator_contact, other_contact])

        self.login(self.admin)
        response = self.client.get(reverse('contacts.contact_read', args=[simulator_contact.uuid]))
        self.assertEquals(response.status_code, 404)

        response = self.client.get(reverse('contacts.contact_update', args=[simulator_contact.pk]))
        self.assertEquals(response.status_code, 404)

        response = self.client.get(reverse('contacts.contact_list'))
        self.assertEquals(response.status_code, 200)
        self.assertFalse(simulator_contact in response.context['object_list'])
        self.assertTrue(other_contact in response.context['object_list'])
        self.assertFalse("Simulator Contact" in response.content)

        response = self.client.get(reverse('contacts.contact_filter', args=[group.pk]))
        self.assertEquals(response.status_code, 200)
        self.assertFalse(simulator_contact in response.context['object_list'])
        self.assertTrue(other_contact in response.context['object_list'])
        self.assertFalse("Simulator Contact" in response.content)

    def test_preferred_channel(self):
        from temba.msgs.tasks import process_message_task

        # create some channels of various types
        twitter = Channel.create(self.org, self.user, None, 'TT', name="Twitter Channel", address="@rapidpro")
        Channel.create(self.org, self.user, None, 'TG', name="Twitter Channel", address="@rapidpro")

        # update our contact URNs, give them telegram and twitter with telegram being preferred
        self.joe.update_urns(self.admin, ['telegram:12515', 'twitter:macklemore'])

        # set the preferred channel to twitter
        self.joe.set_preferred_channel(twitter)

        # preferred URN should be twitter
        self.assertEqual(self.joe.urns.all()[0].scheme, TWITTER_SCHEME)

        # reset back to telegram being preferred
        self.joe.update_urns(self.admin, ['telegram:12515', 'twitter:macklemore'])

        # simulate an incoming message from Mage on Twitter
        msg = Msg.all_messages.create(org=self.org, channel=twitter, contact=self.joe,
                                      contact_urn=ContactURN.get_or_create(self.org, self.joe, 'twitter:macklemore', twitter),
                                      text="Incoming twitter DM", created_on=timezone.now())

        process_message_task(msg.id, from_mage=True, new_contact=False)

        # twitter should be preferred outgoing again
        self.assertEqual(self.joe.urns.all()[0].scheme, TWITTER_SCHEME)


class ContactURNTest(TembaTest):
    def setUp(self):
        super(ContactURNTest, self).setUp()

    def test_create(self):
        urn = ContactURN.create(self.org, None, 'tel:1234')
        self.assertEqual(urn.org, self.org)
        self.assertEqual(urn.contact, None)
        self.assertEqual(urn.urn, 'tel:1234')
        self.assertEqual(urn.scheme, 'tel')
        self.assertEqual(urn.path, '1234')
        self.assertEqual(urn.priority, 50)

    def test_get_display(self):
        urn = ContactURN.objects.create(org=self.org, scheme='tel', path='+250788383383', urn='tel:+250788383383', priority=50)
        self.assertEqual(urn.get_display(self.org), '0788 383 383')
        self.assertEqual(urn.get_display(self.org, full=True), '+250788383383')

        urn = ContactURN.objects.create(org=self.org, scheme='twitter', path='billy_bob', urn='twitter:billy_bob', priority=50)
        self.assertEqual(urn.get_display(self.org), 'billy_bob')


class ContactFieldTest(TembaTest):
    def setUp(self):
        self.user = self.create_user("tito")
        self.manager1 = self.create_user("mike")
        self.admin = self.create_user("ben")
        self.org = Org.objects.create(name="Nyaruka Ltd.", timezone="Africa/Kigali", created_by=self.admin, modified_by=self.admin)
        self.org.administrators.add(self.admin)
        self.org.initialize()

        self.user.set_org(self.org)
        self.admin.set_org(self.org)

        self.channel = Channel.create(self.org, self.admin, None, 'A', "Test Channel", "0785551212",
                                      secret="12345", gcm_id="123")

        self.joe = self.create_contact(name="Joe Blow", number="123")
        self.frank = self.create_contact(name="Frank Smith", number="1234")

        self.contactfield_1 = ContactField.get_or_create(self.org, self.admin, "first", "First")
        self.contactfield_2 = ContactField.get_or_create(self.org, self.admin, "second", "Second")
        self.contactfield_3 = ContactField.get_or_create(self.org, self.admin, "third", "Third")

    def test_get_or_create(self):
        join_date = ContactField.get_or_create(self.org, self.admin, "join_date")
        self.assertEqual(join_date.key, "join_date")
        self.assertEqual(join_date.label, "Join Date")
        self.assertEqual(join_date.value_type, Value.TYPE_TEXT)

        another = ContactField.get_or_create(self.org, self.admin, "another", "My Label", value_type=Value.TYPE_DECIMAL)
        self.assertEqual(another.key, "another")
        self.assertEqual(another.label, "My Label")
        self.assertEqual(another.value_type, Value.TYPE_DECIMAL)

        another = ContactField.get_or_create(self.org, self.admin, "another", "Updated Label", value_type=Value.TYPE_DATETIME)
        self.assertEqual(another.key, "another")
        self.assertEqual(another.label, "Updated Label")
        self.assertEqual(another.value_type, Value.TYPE_DATETIME)

        another = ContactField.get_or_create(self.org, self.admin, "another", "Updated Label", show_in_table=True, value_type=Value.TYPE_DATETIME)
        self.assertTrue(another.show_in_table)

        for elt in Contact.RESERVED_FIELDS:
            with self.assertRaises(ValueError):
                ContactField.get_or_create(self.org, self.admin, elt, elt, value_type=Value.TYPE_TEXT)

        groups_field = ContactField.get_or_create(self.org, self.admin, 'groups_field', 'Groups Field')
        self.assertEqual(groups_field.key, 'groups_field')
        self.assertEqual(groups_field.label, 'Groups Field')

        groups_field.label = 'Groups'
        groups_field.save()

        groups_field.refresh_from_db()

        self.assertEqual(groups_field.key, 'groups_field')
        self.assertEqual(groups_field.label, 'Groups')

        # we should lookup the existing field by label
        label_field = ContactField.get_or_create(self.org, self.admin, 'groups', 'Groups')

        self.assertEqual(label_field.key, 'groups_field')
        self.assertEqual(label_field.label, 'Groups')
        self.assertFalse(ContactField.objects.filter(key='groups'))
        self.assertEqual(label_field.pk, groups_field.pk)

        # exisiting field by label has invalid key we should try to create a new field
        groups_field.key = 'groups'
        groups_field.save()

        groups_field.refresh_from_db()

        # we throw since the key is a reserved word
        with self.assertRaises(ValueError):
            ContactField.get_or_create(self.org, self.admin, 'name', 'Groups')

        created_field = ContactField.get_or_create(self.org, self.admin, 'list', 'Groups')
        self.assertEqual(created_field.key, 'list')
        self.assertEqual(created_field.label, 'Groups')

        # this should be a different field
        self.assertFalse(created_field.pk == groups_field.pk)

        # check it is not possible to create two field with the same label
        self.assertFalse(ContactField.objects.filter(key='sport'))
        self.assertFalse(ContactField.objects.filter(key='play'))

        field1 = ContactField.get_or_create(self.org, self.admin, 'sport', 'Games')
        self.assertEqual(field1.key, 'sport')
        self.assertEqual(field1.label, 'Games')

        # should be the same field
        field2 = ContactField.get_or_create(self.org, self.admin, 'play', 'Games')

        self.assertEqual(field2.key, 'sport')
        self.assertEqual(field2.label, 'Games')
        self.assertEqual(field1.pk, field2.pk)

    def test_contact_templatetag(self):
        self.joe.set_field(self.user, 'First', 'Starter')
        self.assertEquals(contact_field(self.joe, 'First'), 'Starter')

    def test_make_key(self):
        self.assertEquals("first_name", ContactField.make_key("First Name"))
        self.assertEquals("second_name", ContactField.make_key("Second   Name  "))
        self.assertEquals("323_ffsn_slfs_ksflskfs_fk_anfaddgas", ContactField.make_key("  ^%$# %$$ $##323 ffsn slfs ksflskfs!!!! fk$%%%$$$anfaDDGAS ))))))))) "))

    def test_is_valid_key(self):
        self.assertTrue(ContactField.is_valid_key("age"))
        self.assertTrue(ContactField.is_valid_key("age_now_2"))
        self.assertFalse(ContactField.is_valid_key("Age"))   # must be lowercase
        self.assertFalse(ContactField.is_valid_key("age!"))  # can't have punctuation
        self.assertFalse(ContactField.is_valid_key("âge"))   # a-z only
        self.assertFalse(ContactField.is_valid_key("2up"))   # can't start with a number
        self.assertFalse(ContactField.is_valid_key("name"))  # can't be a reserved name
        self.assertFalse(ContactField.is_valid_key("uuid"))

    def test_is_valid_label(self):
        self.assertTrue(ContactField.is_valid_label("Age"))
        self.assertTrue(ContactField.is_valid_label("Age Now 2"))
        self.assertFalse(ContactField.is_valid_label("Age_Now"))  # can't have punctuation
        self.assertFalse(ContactField.is_valid_label("âge"))      # a-z only

    def test_export(self):
        self.clear_storage()

        self.login(self.admin)

        flow = self.create_flow()

        # archive all our current contacts
        Contact.objects.filter(org=self.org).update(is_blocked=True)

        # start one of our contacts down it
        contact = self.create_contact("Ben Haggerty", '+12067799294')
        contact.set_field(self.user, 'First', 'One')
        flow.start([], [contact])

        # create another contact, this should sort before Ben
        contact2 = self.create_contact("Adam Sumner", '+12067799191', twitter='adam')
        urns = [urn.urn for urn in contact2.get_urns()]
        urns.append("mailto:adam@sumner.com")
        urns.append("telegram:1234")
        contact2.update_urns(self.admin, urns)

        Contact.get_test_contact(self.user)  # create test contact to ensure they aren't included in the export

        # create a dummy export task so that we won't be able to export
        blocking_export = ExportContactsTask.objects.create(org=self.org,
                                                            created_by=self.admin, modified_by=self.admin)

        response = self.client.get(reverse('contacts.contact_export'), dict(), follow=True)
        self.assertContains(response, "already an export in progress")

        # ok, mark that one as finished and try again
        blocking_export.is_finished = True
        blocking_export.save()

        with self.assertNumQueries(35):
            self.client.get(reverse('contacts.contact_export'), dict())
            task = ExportContactsTask.objects.all().order_by('-id').first()

            filename = "%s/test_orgs/%d/contact_exports/%s.xls" % (settings.MEDIA_ROOT, self.org.pk, task.uuid)
            workbook = open_workbook(filename, 'rb')
            sheet = workbook.sheets()[0]

            # check our headers
            self.assertExcelRow(sheet, 0, ["UUID", "Name", "Email", "Phone", "Telegram", "Twitter", "First", "Second", "Third"])

            # first row should be Adam
            self.assertExcelRow(sheet, 1, [contact2.uuid, "Adam Sumner", "adam@sumner.com", "+12067799191", "1234", "adam", "", "", ""])

            # second should be Ben
            self.assertExcelRow(sheet, 2, [contact.uuid, "Ben Haggerty", "", "+12067799294", "", "", "One", "", ""])

            self.assertEqual(sheet.nrows, 3)  # no other contacts

        # more contacts do not increase the queries
        contact3 = self.create_contact('Luol Deng', '+12078776655', twitter='deng')
        contact4 = self.create_contact('Stephen', '+12078778899', twitter='stephen')
        ContactURN.create(self.org, contact, 'tel:+12062233445')

        with self.assertNumQueries(35):
            self.client.get(reverse('contacts.contact_export'), dict())
            task = ExportContactsTask.objects.all().order_by('-id').first()

            filename = "%s/test_orgs/%d/contact_exports/%s.xls" % (settings.MEDIA_ROOT, self.org.pk, task.uuid)
            workbook = open_workbook(filename, 'rb')
            sheet = workbook.sheets()[0]

            # check our headers have 2 phone columns and Twitter
            self.assertExcelRow(sheet, 0, ["UUID", "Name", "Email", "Phone", "Phone", "Telegram", "Twitter", "First", "Second", "Third"])

            self.assertExcelRow(sheet, 1, [contact2.uuid, "Adam Sumner", "adam@sumner.com", "+12067799191", "", "1234", "adam", "", "", ""])
            self.assertExcelRow(sheet, 2, [contact.uuid, "Ben Haggerty", "", "+12067799294", "+12062233445", "", "", "One", "", ""])
            self.assertExcelRow(sheet, 3, [contact3.uuid, "Luol Deng", "", "+12078776655", "", "", "deng", "", "", ""])
            self.assertExcelRow(sheet, 4, [contact4.uuid, "Stephen", "", "+12078778899", "", "", "stephen", "", "", ""])

            self.assertEqual(sheet.nrows, 5)  # no other contacts

        # export a specified group of contacts
        self.client.post(reverse('contacts.contactgroup_create'), dict(name="Poppin Tags", group_query='Haggerty'))
        group = ContactGroup.user_groups.get(name='Poppin Tags')
        self.client.get(reverse('contacts.contact_export'), dict(g=group.id))
        task = ExportContactsTask.objects.all().order_by('-id').first()
        filename = "%s/test_orgs/%d/contact_exports/%s.xls" % (settings.MEDIA_ROOT, self.org.pk, task.uuid)
        workbook = open_workbook(filename, 'rb')
        sheet = workbook.sheets()[0]

        # just the header and a single contact
        self.assertEqual(sheet.nrows, 2)

        # now try with an anonymous org
        with AnonymousOrg(self.org):
            self.client.get(reverse('contacts.contact_export'), dict())
            task = ExportContactsTask.objects.all().order_by('-id').first()

            filename = "%s/test_orgs/%d/contact_exports/%s.xls" % (settings.MEDIA_ROOT, self.org.pk, task.uuid)
            workbook = open_workbook(filename, 'rb')
            sheet = workbook.sheets()[0]

            # check our headers have 2 phone columns and Twitter
            self.assertExcelRow(sheet, 0, ["UUID", "Name", "First", "Second", "Third"])

            self.assertExcelRow(sheet, 1, [contact2.uuid, "Adam Sumner", "", "", ""])
            self.assertExcelRow(sheet, 2, [contact.uuid, "Ben Haggerty", "One", "", ""])
            self.assertExcelRow(sheet, 3, [contact3.uuid, "Luol Deng", "", "", ""])
            self.assertExcelRow(sheet, 4, [contact4.uuid, "Stephen", "", "", ""])

            self.assertEqual(sheet.nrows, 5)  # no other contacts

    def test_manage_fields(self):
        manage_fields_url = reverse('contacts.contactfield_managefields')

        self.login(self.manager1)
        response = self.client.get(manage_fields_url)

        # redirect to login because of no access to org
        self.assertEquals(302, response.status_code)

        self.login(self.admin)
        response = self.client.get(manage_fields_url)
        self.assertEquals(len(response.context['form'].fields), 16)

        post_data = dict()
        for id, field in response.context['form'].fields.items():
            if field.initial is None:
                post_data[id] = ''
            elif isinstance(field.initial, ContactField):
                post_data[id] = field.initial.pk
            else:
                post_data[id] = field.initial

        response = self.client.post(manage_fields_url, post_data, follow=True)
        self.assertEquals(response.status_code, 200)

        # make sure we didn't have an error
        self.assertTrue('form' not in response.context)

        # should still have three contact fields
        self.assertEquals(3, ContactField.objects.filter(org=self.org, is_active=True).count())

        # fields name should be unique case insensitively
        post_data['label_1'] = "Town"
        post_data['label_2'] = "town"

        response = self.client.post(manage_fields_url, post_data, follow=True)
        self.assertFormError(response, 'form', None, "Field names must be unique. 'Town' is duplicated")
        self.assertEquals(3, ContactField.objects.filter(org=self.org, is_active=True).count())
        self.assertFalse(ContactField.objects.filter(org=self.org, label__in=["town", "Town"]))

        # now remove the first field, rename the second and change the type on the third
        post_data['label_1'] = ''
        post_data['label_2'] = 'Number 2'
        post_data['type_3'] = 'N'
        post_data['label_4'] = "New Field"

        response = self.client.post(manage_fields_url, post_data, follow=True)
        self.assertEquals(response.status_code, 200)

        # make sure we didn't have an error
        self.assertTrue('form' not in response.context)

        # first field was blank, so it should be inactive
        self.assertIsNone(ContactField.objects.filter(org=self.org, key="first", is_active=True).first())

        # the second should be renamed
        self.assertEquals("Number 2", ContactField.objects.filter(org=self.org, key="second", is_active=True).first().label)

        # the third should have a different type
        self.assertEquals('N', ContactField.objects.filter(org=self.org, key="third", is_active=True).first().value_type)

        # we should have a fourth field now
        self.assertTrue(ContactField.objects.filter(org=self.org, key='new_field', label="New Field", value_type='T'))

        # check that a field name which is a reserved field, gives an error
        post_data['label_2'] = 'name'
        response = self.client.post(manage_fields_url, post_data, follow=True)
        self.assertFormError(response, 'form', None, "Field name 'name' is a reserved word")

        # check that a field name which contains disallowed characters, gives an error
        post_data['label_2'] = '@name'
        response = self.client.post(manage_fields_url, post_data, follow=True)
        self.assertFormError(response, 'form', None,
                             "Field names can only contain letters, numbers and hypens")

        post_data['label_2'] = 'Name'
        response = self.client.post(manage_fields_url, post_data, follow=True)
        self.assertFormError(response, 'form', None, "Field name 'Name' is a reserved word")

    def test_json(self):
        contact_field_json_url = reverse('contacts.contactfield_json')

        self.org2 = Org.objects.create(name="kLab", timezone="Africa/Kigali", created_by=self.admin, modified_by=self.admin)
        for i in range(30):
            key = 'key%d' % i
            label = 'label%d' % i
            ContactField.get_or_create(self.org, self.admin, key, label)
            ContactField.get_or_create(self.org2, self.admin, key, label)

        self.assertEquals(Org.objects.all().count(), 2)

        ContactField.objects.filter(org=self.org, key='key1').update(is_active=False)

        self.login(self.manager1)
        response = self.client.get(contact_field_json_url)

        # redirect to login because of no access to org
        self.assertEquals(302, response.status_code)

        self.login(self.admin)
        response = self.client.get(contact_field_json_url)

        response_json = json.loads(response.content)

        self.assertEquals(len(response_json), 40)
        self.assertEquals(response_json[0]['label'], 'Full name')
        self.assertEquals(response_json[0]['key'], 'name')
        self.assertEquals(response_json[1]['label'], 'External identifier')
        self.assertEquals(response_json[1]['key'], 'ext')
        self.assertEquals(response_json[2]['label'], 'Facebook identifier')
        self.assertEquals(response_json[2]['key'], 'facebook')
        self.assertEquals(response_json[3]['label'], 'Email address')
        self.assertEquals(response_json[3]['key'], 'mailto')
        self.assertEquals(response_json[4]['label'], 'Telegram identifier')
        self.assertEquals(response_json[4]['key'], 'telegram')
        self.assertEquals(response_json[5]['label'], 'Twitter handle')
        self.assertEquals(response_json[5]['key'], 'twitter')
        self.assertEquals(response_json[6]['label'], 'Phone number')
        self.assertEquals(response_json[6]['key'], 'tel_e164')
        self.assertEquals(response_json[7]['label'], 'Groups')
        self.assertEquals(response_json[7]['key'], 'groups')
        self.assertEquals(response_json[8]['label'], 'First')
        self.assertEquals(response_json[8]['key'], 'first')
        self.assertEquals(response_json[9]['label'], 'label0')
        self.assertEquals(response_json[9]['key'], 'key0')

        ContactField.objects.filter(org=self.org, key='key0').update(label='AAAA')

        response = self.client.get(contact_field_json_url)
        response_json = json.loads(response.content)

        self.assertEquals(len(response_json), 40)
        self.assertEquals(response_json[0]['label'], 'Full name')
        self.assertEquals(response_json[0]['key'], 'name')
        self.assertEquals(response_json[1]['label'], 'External identifier')
        self.assertEquals(response_json[1]['key'], 'ext')
        self.assertEquals(response_json[2]['label'], 'Facebook identifier')
        self.assertEquals(response_json[2]['key'], 'facebook')
        self.assertEquals(response_json[3]['label'], 'Email address')
        self.assertEquals(response_json[3]['key'], 'mailto')
        self.assertEquals(response_json[4]['label'], 'Telegram identifier')
        self.assertEquals(response_json[4]['key'], 'telegram')
        self.assertEquals(response_json[5]['label'], 'Twitter handle')
        self.assertEquals(response_json[5]['key'], 'twitter')
        self.assertEquals(response_json[6]['label'], 'Phone number')
        self.assertEquals(response_json[6]['key'], 'tel_e164')
        self.assertEquals(response_json[7]['label'], 'Groups')
        self.assertEquals(response_json[7]['key'], 'groups')
        self.assertEquals(response_json[8]['label'], 'AAAA')
        self.assertEquals(response_json[8]['key'], 'key0')
        self.assertEquals(response_json[9]['label'], 'First')
        self.assertEquals(response_json[9]['key'], 'first')


class URNTest(TembaTest):
    def test_from_parts(self):
        self.assertEqual(URN.from_parts("tel", "12345"), "tel:12345")
        self.assertEqual(URN.from_parts("tel", "+12345"), "tel:+12345")
        self.assertEqual(URN.from_parts("tel", "(917) 992-5253"), "tel:(917) 992-5253")
        self.assertEqual(URN.from_parts("mailto", "a_b+c@d.com"), "mailto:a_b+c@d.com")

        self.assertEqual(URN.from_tel("+12345"), "tel:+12345")
        self.assertEqual(URN.from_twitter("abc_123"), "twitter:abc_123")
        self.assertEqual(URN.from_email("a_b+c@d.com"), "mailto:a_b+c@d.com")
        self.assertEqual(URN.from_facebook(12345), "facebook:12345")
        self.assertEqual(URN.from_telegram(12345), "telegram:12345")
        self.assertEqual(URN.from_external("Aa0()+,-.:=@;$_!*'"), "ext:Aa0()+,-.:=@;$_!*'")

        self.assertRaises(ValueError, URN.from_parts, "", "12345")
        self.assertRaises(ValueError, URN.from_parts, "tel", "")
        self.assertRaises(ValueError, URN.from_parts, "xxx", "12345")

    def test_to_parts(self):
        self.assertEqual(URN.to_parts("tel:12345"), ("tel", "12345"))
        self.assertEqual(URN.to_parts("tel:+12345"), ("tel", "+12345"))
        self.assertEqual(URN.to_parts("twitter:abc_123"), ("twitter", "abc_123"))
        self.assertEqual(URN.to_parts("mailto:a_b+c@d.com"), ("mailto", "a_b+c@d.com"))
        self.assertEqual(URN.to_parts("facebook:12345"), ("facebook", "12345"))
        self.assertEqual(URN.to_parts("telegram:12345"), ("telegram", "12345"))
        self.assertEqual(URN.to_parts("ext:Aa0()+,-.:=@;$_!*'"), ("ext", "Aa0()+,-.:=@;$_!*'"))

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

        # un-normalizable tel numbers
        self.assertEqual(URN.normalize("tel:12345", "RW"), "tel:12345")
        self.assertEqual(URN.normalize("tel:0788383383", None), "tel:0788383383")
        self.assertEqual(URN.normalize("tel:0788383383", "ZZ"), "tel:0788383383")
        self.assertEqual(URN.normalize("tel:MTN", "RW"), "tel:mtn")

        # twitter handles remove @
        self.assertEqual(URN.normalize("twitter: @jimmyJO"), "twitter:jimmyjo")

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
        self.assertTrue(URN.validate("tel:0788383383", None))  # assumed valid because no country

        # invalid tel numbers
        self.assertFalse(URN.validate("tel:0788383383", "ZZ"))  # invalid country
        self.assertFalse(URN.validate("tel:MTN", "RW"))

        # twitter handles
        self.assertTrue(URN.validate("twitter:jimmyjo"))
        self.assertTrue(URN.validate("twitter:billy_bob"))
        self.assertFalse(URN.validate("twitter:jimmyjo!@"))
        self.assertFalse(URN.validate("twitter:billy bob"))

        # emil addresses
        self.assertTrue(URN.validate("mailto:abcd+label@x.y.z.com"))
        self.assertFalse(URN.validate("mailto:@@@"))

        # facebook and telegram URN paths must be integers
        self.assertTrue(URN.validate("telegram:12345678901234567"))
        self.assertFalse(URN.validate("telegram:abcdef"))
        self.assertTrue(URN.validate("facebook:12345678901234567"))
        self.assertFalse(URN.validate("facebook:abcdef"))
