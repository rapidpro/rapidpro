# coding=utf-8
from __future__ import unicode_literals

import json
import pytz
import time

from datetime import datetime, date, timedelta
from django.core.urlresolvers import reverse
from django.conf import settings
from django.utils import timezone
from mock import patch
from smartmin.tests import _CRUDLTest
from smartmin.csv_imports.models import ImportTask
from temba.contacts.models import Contact, ContactGroup, ContactField, ContactURN, ExportContactsTask, EXTERNAL_SCHEME
from temba.contacts.models import TEL_SCHEME, TWITTER_SCHEME, SmartImportRowError
from temba.contacts.templatetags.contacts import contact_field
from temba.locations.models import AdminBoundary
from temba.orgs.models import Org, Language
from temba.channels.models import Channel, TWITTER
from temba.msgs.models import Msg, Call, Label, SystemLabel, Broadcast
from temba.schedules.models import Schedule
from temba.tests import AnonymousOrg, TembaTest
from temba.triggers.models import Trigger
from temba.utils import datetime_to_str, get_datetime_format
from temba.values.models import STATE, DATETIME, DISTRICT, Value, DECIMAL, TEXT


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

        ContactField.get_or_create(self.org, 'age', "Age", value_type='N')
        ContactField.get_or_create(self.org, 'home', "Home", value_type='S')

    def getCreatePostData(self):
        return dict(name="Joe Brady", __urn__tel__0="+250785551212")

    def getUpdatePostData(self):
        return dict(name="Joe Brady", __urn__tel__0="+250785551212")

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
        self.object = Contact.objects.get(org=self.org, urns__path=post_data['__urn__tel__0'], name=post_data['name'])
        return self.object

    def testList(self):
        self.joe = Contact.get_or_create(self.org, self.user, name='Joe', urns=[(TEL_SCHEME, '123')])
        self.joe.set_field('age', 20)
        self.joe.set_field('home', 'Kigali')
        self.frank = Contact.get_or_create(self.org, self.user, name='Frank', urns=[(TEL_SCHEME, '124')])
        self.frank.set_field('age', 18)

        response = self._do_test_view('list')
        self.assertEqual([self.frank, self.joe], list(response.context['object_list']))

        response = self._do_test_view('list', query_string='search=age+%3D+18')
        self.assertEqual([self.frank], list(response.context['object_list']))

        response = self._do_test_view('list', query_string='search=age+>+18+and+home+%3D+"Kigali"')
        self.assertEqual([self.joe], list(response.context['object_list']))

    def testRead(self):
        self.joe = Contact.get_or_create(self.org, self.user, name='Joe', urns=[(TEL_SCHEME, '123')])

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

        # ok, what about deleting?
        response = self.client.get(read_url)
        delete_url = reverse('contacts.contact_delete', args=[self.joe.id])
        self.assertContains(response, delete_url)

        self.client.post(delete_url, dict(id=self.joe.id))
        self.assertTrue(Contact.objects.get(pk=self.joe.id, is_active=False))

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


class ContactGroupCRUDLTest(_CRUDLTest):
    def setUp(self):
        from temba.contacts.views import ContactGroupCRUDL

        super(ContactGroupCRUDLTest, self).setUp()
        self.crudl = ContactGroupCRUDL
        self.user = self.create_user("tito")
        self.org = Org.objects.create(name="Nyaruka Ltd.", timezone="Africa/Kigali", created_by=self.user, modified_by=self.user)
        self.org.administrators.add(self.user)
        self.org.initialize()

        self.user.set_org(self.org)
        self.channel = Channel.create(self.org, self.user, 'RW', 'A', "Test Channel", "0785551212",
                                      secret="12345", gcm_id="123")

        self.joe = Contact.get_or_create(self.org, self.user, name="Joe Blow", urns=[(TEL_SCHEME, "123")])
        self.frank = Contact.get_or_create(self.org, self.user, name="Frank Smith", urns=[(TEL_SCHEME, "1234")])

    def getCreatePostData(self):
        return dict(name="My Group")

    def getUpdatePostData(self):
        return dict(name="My Updated Group", contacts="%s" % self.frank.pk, join_keyword="updated", join_response="Thanks for joining the group")

    def getManager(self):
        return ContactGroup.user_groups

    def testDelete(self):
        obj = self.getTestObject()
        self._do_test_view('delete', obj, post_data=dict())
        self.assertFalse(self.getCRUDL().model.user_groups.get(pk=obj.pk).is_active)

    def test_create(self):
        create_url = reverse('contacts.contactgroup_create')
        self.login(self.user)

        # clear our current groups
        ContactGroup.user_groups.filter(org=self.org).delete()

        # try to create a contact group whose name is only whitespace
        response = self.client.post(create_url, dict(name="  "))
        self.assertFormError(response, 'form', 'name', "Group name must not be blank or begin with + or -")

        # try to create a contact group whose name begins with reserved character
        response = self.client.post(create_url, dict(name="+People"))
        self.assertFormError(response, 'form', 'name', "Group name must not be blank or begin with + or -")

        response = self.client.post(create_url, dict(name="first  "))
        self.assertNoFormErrors(response)
        group = ContactGroup.user_groups.get(org=self.org, name="first")

        # try to create another with the same name, nothing happens
        response = self.client.post(create_url, dict(name="First"))
        self.assertNoFormErrors(response)
        self.assertEquals(1, ContactGroup.user_groups.filter(org=self.org).count())

        # direct calls are the same thing
        existing_group = ContactGroup.get_or_create(self.org, self.user, "  FIRST")
        self.assertEquals(group, existing_group)

    def test_update(self):
        group = ContactGroup.create(self.org, self.user, "one")

        update_url = reverse('contacts.contactgroup_update', args=[group.pk])
        self.login(self.user)

        # try to update name to only whitespace
        response = self.client.post(update_url, dict(name="   "))
        self.assertFormError(response, 'form', 'name', "Group name must not be blank or begin with + or -")

        # try to update name to start with reserved character
        response = self.client.post(update_url, dict(name="+People"))
        self.assertFormError(response, 'form', 'name', "Group name must not be blank or begin with + or -")

        response = self.client.post(update_url, dict(name="new name   "))
        self.assertNoFormErrors(response)
        ContactGroup.user_groups.get(org=self.org, name="new name")


class ContactGroupTest(TembaTest):
    def setUp(self):
        super(ContactGroupTest, self).setUp()

        self.joe = Contact.get_or_create(self.org, self.admin, name="Joe Blow", urns=[(TEL_SCHEME, "123")])
        self.frank = Contact.get_or_create(self.org, self.admin, name="Frank Smith", urns=[(TEL_SCHEME, "1234")])
        self.mary = Contact.get_or_create(self.org, self.admin, name="Mary Mo", urns=[(TEL_SCHEME, "345")])

    def test_create(self):
        # exception if group name is blank
        self.assertRaises(ValueError, ContactGroup.create, self.org, self.admin, "   ")

        ContactGroup.create(self.org, self.admin, " group one ")
        self.assertEquals(1, ContactGroup.user_groups.count())
        self.assertTrue(ContactGroup.user_groups.get(org=self.org, name="group one"))

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
        group = ContactGroup.create(self.org, self.user, "Cool kids")

        # add contacts via the related field
        group.contacts.add(self.joe, self.frank)

        self.assertEquals(ContactGroup.user_groups.get(pk=group.pk).count, 2)

        # add contacts via update_contacts
        group.update_contacts([self.mary], add=True)

        self.assertEquals(ContactGroup.user_groups.get(pk=group.pk).count, 3)

        # remove contacts via update_contacts
        group.update_contacts([self.mary], add=False)

        self.assertEquals(ContactGroup.user_groups.get(pk=group.pk).count, 2)

        # add test contact (will add to group but won't increment count)
        test_contact = Contact.get_test_contact(self.admin)
        group.update_contacts([test_contact], add=True)

        group = ContactGroup.user_groups.get(pk=group.pk)
        self.assertEquals(group.count, 2)
        self.assertEquals(set(group.contacts.all()), {self.joe, self.frank, test_contact})

        # blocking a contact removes them from all user groups
        self.joe.block()

        with self.assertRaises(ValueError):
            group.update_contacts([self.joe], True)

        group = ContactGroup.user_groups.get(pk=group.pk)
        self.assertEquals(group.count, 1)
        self.assertEquals(set(group.contacts.all()), {self.frank, test_contact})

        # unblocking won't re-add to any groups
        self.joe.unblock()

        self.assertEquals(ContactGroup.user_groups.get(pk=group.pk).count, 1)

        # releasing also removes from all user groups
        self.frank.release()

        group = ContactGroup.user_groups.get(pk=group.pk)
        self.assertEquals(group.count, 0)
        self.assertEquals(set(group.contacts.all()), {test_contact})

    def test_system_group_counts(self):
        Contact.objects.all().delete()  # start with none

        counts = ContactGroup.get_system_group_counts(self.org)
        self.assertEqual(counts, {ContactGroup.TYPE_ALL: 0, ContactGroup.TYPE_BLOCKED: 0, ContactGroup.TYPE_FAILED: 0})

        hannibal = self.create_contact("Hannibal", number="0783835001")
        face = self.create_contact("Face", number="0783835002")
        ba = self.create_contact("B.A.", number="0783835003")
        murdock = self.create_contact("Murdock", number="0783835004")

        counts = ContactGroup.get_system_group_counts(self.org)
        self.assertEqual(counts, {ContactGroup.TYPE_ALL: 4, ContactGroup.TYPE_BLOCKED: 0, ContactGroup.TYPE_FAILED: 0})

        # call methods twice to check counts don't change twice
        murdock.block()
        murdock.block()
        face.block()
        ba.fail()
        ba.fail()

        counts = ContactGroup.get_system_group_counts(self.org)
        self.assertEqual(counts, {ContactGroup.TYPE_ALL: 2, ContactGroup.TYPE_BLOCKED: 2, ContactGroup.TYPE_FAILED: 1})

        murdock.release()
        murdock.release()
        face.unblock()
        face.unblock()
        ba.unfail()
        ba.unfail()

        counts = ContactGroup.get_system_group_counts(self.org)
        self.assertEqual(counts, {ContactGroup.TYPE_ALL: 3, ContactGroup.TYPE_BLOCKED: 0, ContactGroup.TYPE_FAILED: 0})

    def test_update_query(self):
        age = ContactField.get_or_create(self.org, 'age')
        gender = ContactField.get_or_create(self.org, 'gender')
        group = ContactGroup.create(self.org, self.admin, "Group 1")

        group.update_query('(age < 18 and gender = "male") or (age > 18 and gender = "female")')
        self.assertEqual([age, gender], list(ContactGroup.user_groups.get(pk=group.id).query_fields.all().order_by('key')))

        group.update_query('height > 100')
        self.assertEqual(0, ContactGroup.user_groups.get(pk=group.id).query_fields.count())

        # dynamic group should not have remove to group button
        self.login(self.admin)
        filter_url = reverse('contacts.contact_filter', args=[group.pk])
        response = self.client.get(filter_url)
        self.assertFalse('unlabel' in response.context['actions'])

    def test_delete(self):
        group = ContactGroup.create(self.org, self.user, "one")

        self.login(self.admin)

        response = self.client.post(reverse('contacts.contactgroup_delete', args=[group.pk]), dict())
        self.assertFalse(ContactGroup.user_groups.get(pk=group.pk).is_active)

        group = ContactGroup.create(self.org, self.user, "one")
        delete_url = reverse('contacts.contactgroup_delete', args=[group.pk])

        trigger = Trigger.objects.create(org=self.org, keyword="join", created_by=self.admin, modified_by=self.admin)
        trigger.groups.add(group)

        second_trigger = Trigger.objects.create(org=self.org, keyword="register", created_by=self.admin, modified_by=self.admin)
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
        self.assertFalse(ContactGroup.user_groups.get(pk=group.pk).is_active)
        self.assertFalse(Trigger.objects.get(pk=trigger.pk).is_active)
        self.assertFalse(Trigger.objects.get(pk=second_trigger.pk).is_active)


class ContactTest(TembaTest):
    def setUp(self):
        super(ContactTest, self).setUp()

        self.user1 = self.create_user("nash")
        self.manager1 = self.create_user("mike")

        self.joe = self.create_contact(name="Joe Blow", number="123", twitter="blow80")
        self.frank = self.create_contact(name="Frank Smith", number="1234")
        self.billy = self.create_contact(name="Billy Nophone")
        self.voldemort = self.create_contact(number="+250788383383")

        # create an orphaned URN
        ContactURN.objects.create(org=self.org, scheme='tel', path='8888', urn='tel:8888', priority=50)

        # create an deleted contact
        self.jim = self.create_contact(name="Jim")
        self.jim.release()

    def create_campaign(self):

        # create a campaign with a future event and add joe
        from temba.campaigns.models import Campaign, CampaignEvent, EventFire
        self.farmers = self.create_group("Farmers", [self.joe])
        self.reminder_flow = self.create_flow()
        self.planting_date = ContactField.get_or_create(self.org, 'planting_date', "Planting Date")
        self.campaign = Campaign.create(self.org, self.admin, "Planting Reminders", self.farmers)

        # create af flow event
        self.planting_reminder = CampaignEvent.create_flow_event(self.org, self.admin, self.campaign,
                                                                 relative_to=self.planting_date, offset=0, unit='D',
                                                                 flow=self.reminder_flow, delivery_hour=17)

        # and a message event
        self.message_event = CampaignEvent.create_message_event(self.org, self.admin, self.campaign,
                                           relative_to=self.planting_date, offset=7, unit='D',
                                           message='Sent 7 days after planting date')


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
        response = self.client.post(reverse('contacts.contact_create'), data=dict(name='Ben Haggerty', __urn__tel__0="123"))
        self.assertEquals(1, len(response.context['form'].errors))

        # now repost with a unique phone number
        response = self.client.post(reverse('contacts.contact_create'), data=dict(name='Ben Haggerty', __urn__tel__0="123-456"))
        self.assertNoFormErrors(response)

        # repost with the phone number of an orphaned URN
        response = self.client.post(reverse('contacts.contact_create'), data=dict(name='Ben Haggerty', __urn__tel__0="8888"))
        self.assertNoFormErrors(response)

        # check that the orphaned URN has been associated with the contact
        self.assertEqual('Ben Haggerty', Contact.from_urn(self.org, TEL_SCHEME, '8888').name)

    def test_fail_and_block_and_release(self):
        msg1 = self.create_msg(text="Test 1", direction='I', contact=self.joe, msg_type='I', status='H')
        msg2 = self.create_msg(text="Test 2", direction='I', contact=self.joe, msg_type='F', status='H')
        msg3 = self.create_msg(text="Test 3", direction='I', contact=self.joe, msg_type='I', status='H', visibility='A')
        label = Label.get_or_create(self.org, self.user, "Interesting")
        label.toggle_label([msg1, msg2, msg3], add=True)
        group = self.create_group("Just Joe", [self.joe])

        self.clear_cache()

        msg_counts = SystemLabel.get_counts(self.org)
        self.assertEqual(1, msg_counts[SystemLabel.TYPE_INBOX])
        self.assertEqual(1, msg_counts[SystemLabel.TYPE_FLOWS])
        self.assertEqual(1, msg_counts[SystemLabel.TYPE_ARCHIVED])

        contact_counts = ContactGroup.get_system_group_counts(self.org)
        self.assertEqual(contact_counts, {ContactGroup.TYPE_ALL: 4,
                                          ContactGroup.TYPE_BLOCKED: 0,
                                          ContactGroup.TYPE_FAILED: 0})

        self.assertEqual(3, label.msgs.count())
        self.assertEqual(1, group.contacts.count())

        self.joe.fail()

        # check that joe is now failed
        self.joe = Contact.objects.get(pk=self.joe.pk)
        self.assertTrue(self.joe.is_failed)
        self.assertFalse(self.joe.is_blocked)
        self.assertTrue(self.joe.is_active)

        # and added to failed group
        contact_counts = ContactGroup.get_system_group_counts(self.org)
        self.assertEqual(contact_counts, {ContactGroup.TYPE_ALL: 4,
                                          ContactGroup.TYPE_BLOCKED: 0,
                                          ContactGroup.TYPE_FAILED: 1})
        self.assertEqual(1, group.contacts.count())

        self.joe.block()

        # check that joe is now blocked and failed
        self.joe = Contact.objects.get(pk=self.joe.pk)
        self.assertTrue(self.joe.is_failed)
        self.assertTrue(self.joe.is_blocked)
        self.assertTrue(self.joe.is_active)

        # and that he's been removed from the all and failed groups, and added to the blocked group
        contact_counts = ContactGroup.get_system_group_counts(self.org)
        self.assertEqual(contact_counts, {ContactGroup.TYPE_ALL: 3,
                                          ContactGroup.TYPE_BLOCKED: 1,
                                          ContactGroup.TYPE_FAILED: 0})

        # and removed from the single user group
        self.assertEqual(0, ContactGroup.user_groups.get(pk=group.pk).contacts.count())

        # but his messages are unchanged
        self.assertEqual(2, Msg.all_messages.filter(contact=self.joe, visibility='V').count())
        msg_counts = SystemLabel.get_counts(self.org)
        self.assertEqual(1, msg_counts[SystemLabel.TYPE_INBOX])
        self.assertEqual(1, msg_counts[SystemLabel.TYPE_FLOWS])
        self.assertEqual(1, msg_counts[SystemLabel.TYPE_ARCHIVED])

        self.joe.unblock()

        # check that joe is now unblocked but still failed
        self.joe = Contact.objects.get(pk=self.joe.pk)
        self.assertTrue(self.joe.is_failed)
        self.assertFalse(self.joe.is_blocked)
        self.assertTrue(self.joe.is_active)

        # and that he's been removed from the blocked group, and put back in the all and failed groups
        contact_counts = ContactGroup.get_system_group_counts(self.org)
        self.assertEqual(contact_counts, {ContactGroup.TYPE_ALL: 4,
                                          ContactGroup.TYPE_BLOCKED: 0,
                                          ContactGroup.TYPE_FAILED: 1})

        self.joe.unfail()

        # check that joe is now no longer failed
        self.joe = Contact.objects.get(pk=self.joe.pk)
        self.assertFalse(self.joe.is_failed)
        self.assertFalse(self.joe.is_blocked)
        self.assertTrue(self.joe.is_active)

        # and that he's been removed from the failed group
        contact_counts = ContactGroup.get_system_group_counts(self.org)
        self.assertEqual(contact_counts, {ContactGroup.TYPE_ALL: 4,
                                          ContactGroup.TYPE_BLOCKED: 0,
                                          ContactGroup.TYPE_FAILED: 0})

        self.joe.release()

        # check that joe is no longer active
        self.joe = Contact.objects.get(pk=self.joe.pk)
        self.assertFalse(self.joe.is_failed)
        self.assertFalse(self.joe.is_blocked)
        self.assertFalse(self.joe.is_active)

        contact_counts = ContactGroup.get_system_group_counts(self.org)
        self.assertEqual(contact_counts, {ContactGroup.TYPE_ALL: 3,
                                          ContactGroup.TYPE_BLOCKED: 0,
                                          ContactGroup.TYPE_FAILED: 0})

        # joe's messages should be inactive, blank and have no labels
        self.assertEqual(0, Msg.all_messages.filter(contact=self.joe, visibility='V').count())
        self.assertEqual(0, Msg.all_messages.filter(contact=self.joe).exclude(text="").count())
        self.assertEqual(0, Label.label_objects.get(pk=label.pk).msgs.count())

        msg_counts = SystemLabel.get_counts(self.org)
        self.assertEqual(0, msg_counts[SystemLabel.TYPE_INBOX])
        self.assertEqual(0, msg_counts[SystemLabel.TYPE_FLOWS])
        self.assertEqual(0, msg_counts[SystemLabel.TYPE_ARCHIVED])

        # and he shouldn't be in any groups
        self.assertEqual(0, ContactGroup.user_groups.get(pk=group.pk).contacts.count())

        # or have any URNs
        self.assertEqual(0, ContactURN.objects.filter(contact=self.joe).count())

        # blocking and failing an inactive contact won't change groups
        self.joe.block()
        self.joe.fail()

        contact_counts = ContactGroup.get_system_group_counts(self.org)
        self.assertEqual(contact_counts, {ContactGroup.TYPE_ALL: 3,
                                          ContactGroup.TYPE_BLOCKED: 0,
                                          ContactGroup.TYPE_FAILED: 0})

        # we don't let users undo releasing a contact... but if we have to do it for some reason
        self.joe.is_active = True
        self.joe.save()

        # check joe goes into the appropriate groups
        contact_counts = ContactGroup.get_system_group_counts(self.org)
        self.assertEqual(contact_counts, {ContactGroup.TYPE_ALL: 3,
                                          ContactGroup.TYPE_BLOCKED: 1,
                                          ContactGroup.TYPE_FAILED: 1})

        # don't allow blocking or failing of test contacts
        test_contact = Contact.get_test_contact(self.user)
        self.assertRaises(ValueError, test_contact.block)
        self.assertRaises(ValueError, test_contact.fail)

    def test_update_groups(self):
        spammers = self.create_group("Spammers", [])
        testers = self.create_group("Testers", [])

        self.joe.update_groups([spammers, testers])
        self.assertEqual(set(self.joe.user_groups.all()), {spammers, testers})

        self.joe.update_groups([testers])
        self.assertEqual(set(self.joe.user_groups.all()), {testers})

        self.joe.update_groups([])
        self.assertEqual(set(self.joe.user_groups.all()), set())

        # can't add blocked contacts to a group
        self.joe.block()
        self.assertRaises(ValueError, self.joe.update_groups, [spammers])

        # can't add deleted contacts to a group
        self.joe.unblock()
        self.joe.release()
        self.assertRaises(ValueError, self.joe.update_groups, [spammers])

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
        ContactField.get_or_create(self.org, 'age', "Age", value_type='N', show_in_table=True)
        ContactField.get_or_create(self.org, 'nick', "Nickname", value_type='T', show_in_table=False)

        self.joe.set_field('age', 32)
        self.joe.set_field('nick', 'Joey')
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

        ContactField.get_or_create(self.org, 'age', "Age", value_type='N')
        ContactField.get_or_create(self.org, 'join_date', "Join Date", value_type='D')
        ContactField.get_or_create(self.org, 'home', "Home District", value_type='I')
        state_field = ContactField.get_or_create(self.org, 'state', "Home State", value_type='S')

        africa = AdminBoundary.objects.create(osm_id='R001', name='Africa', level=0)
        rwanda = AdminBoundary.objects.create(osm_id='R002', name='Rwanda', level=1, parent=africa)
        gatsibo = AdminBoundary.objects.create(osm_id='R003', name='Gatsibo', level=2, parent=rwanda)
        kayonza = AdminBoundary.objects.create(osm_id='R004', name='Kayonza', level=2, parent=rwanda)
        kigali = AdminBoundary.objects.create(osm_id='R005', name='Kigali', level=2, parent=rwanda)

        locations_boundaries = [gatsibo, kayonza, kigali]
        locations = ['Gatsibo', 'Kayonza', 'Kigali']
        names = ['Trey', 'Mike', 'Paige', 'Fish']
        date_format = get_datetime_format(True)[0]

        # create some contacts
        for i in range(10, 100):
            name = names[(i + 2) % len(names)]
            number = "0788382%s" % str(i).zfill(3)
            twitter = "tweep_%d" % (i + 1)
            contact = self.create_contact(name=name, number=number, twitter=twitter)

            # some field data so we can do some querying
            contact.set_field('age', '%s' % i)
            contact.set_field('join_date', '%s' % datetime_to_str(date(2013, 12, 22) + timezone.timedelta(days=i),
                                                                  date_format))
            contact.set_field('state', "Rwanda")
            index = (i + 2) % len(locations)
            with patch('temba.orgs.models.Org.parse_location') as mock_parse_location:
                mock_parse_location.return_value = AdminBoundary.objects.filter(name__iexact=locations[index])
                contact.set_field('home', locations[index])

        q = lambda query: Contact.search(self.org, query)[0].count()

        # non-complex queries
        self.assertEquals(23, q('trey'))
        self.assertEquals(23, q('MIKE'))
        self.assertEquals(22, q('  paige  '))
        self.assertEquals(22, q('fish'))
        self.assertEquals(1, q('0788382011'))  # does a contains

        # name as property
        self.assertEquals(23, q('name is "trey"'))
        self.assertEquals(23, q('name is mike'))
        self.assertEquals(22, q('name = paige'))
        self.assertEquals(22, q('NAME=fish'))
        self.assertEquals(68, q('name has e'))

        # URN as property
        self.assertEquals(1, q('tel is +250788382011'))
        self.assertEquals(1, q('tel has 0788382011'))
        self.assertEquals(1, q('twitter = tweep_12'))
        self.assertEquals(90, q('TWITTER has tweep'))

        # contact field as property
        self.assertEquals(69, q('age > 30'))
        self.assertEquals(70, q('age >= 30'))
        self.assertEquals(10, q('age > 30 and age <= 40'))
        self.assertEquals(10, q('AGE < 20'))

        self.assertEquals(1, q('join_date = 1-1-14'))
        self.assertEquals(29, q('join_date < 30/1/2014'))
        self.assertEquals(30, q('join_date <= 30/1/2014'))
        self.assertEquals(60, q('join_date > 30/1/2014'))
        self.assertEquals(61, q('join_date >= 30/1/2014'))
        self.assertEquals(0, q('join_date >= xxxx'))  # invalid date

        self.assertEquals(30, q('home is Kayonza'))
        self.assertEquals(30, q('HOME is "kigali"'))
        self.assertEquals(60, q('home has k'))

        # boolean combinations
        self.assertEquals(46, q('name is trey or name is mike'))
        self.assertEquals(3, q('name is trey and age < 20'))
        self.assertEquals(60, q('(home is gatsibo or home is "kigali")'))
        self.assertEquals(15, q('(home is gatsibo or home is "kigali") and name is mike'))

        # invalid queries - which revert to simple name/phone matches
        self.assertEquals(0, q('(('))
        self.assertEquals(0, q('name = "trey'))

        # non-anon orgs can't search by id (because they never see ids)
        contact = Contact.objects.filter(is_active=True).last()
        self.assertFalse('%d' % contact.pk in contact.get_urn().path)  # check this contact's id isn't in their tel
        self.assertFalse(contact in Contact.search(self.org, '%d' % contact.pk)[0])  # others may match by id on tel

        with AnonymousOrg(self.org):
            # still allow name and field searches
            self.assertEquals(23, q('trey'))
            self.assertEquals(23, q('name is mike'))
            self.assertEquals(69, q('age > 30'))

            # don't allow matching on URNs
            self.assertEquals(0, q('0788382011'))
            self.assertEquals(0, q('tel is +250788382011'))
            self.assertEquals(0, q('twitter has blow'))

            # anon orgs can search by id, with or without zero padding
            self.assertTrue(contact in Contact.search(self.org, '%d' % contact.pk)[0])
            self.assertTrue(contact in Contact.search(self.org, '%010d' % contact.pk)[0])

    def test_omnibox(self):
        # add a group with members and an empty group
        joe_and_frank = self.create_group("Joe and Frank", [self.joe, self.frank])
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

        response = json.loads(self.client.get("%s" % reverse("contacts.contact_omnibox")).content)
        self.assertEquals(9, len(response['results']))

        # both groups...
        self.assertEquals(dict(id='g-%d' % joe_and_frank.pk, text="Joe and Frank", extra=2), response['results'][0])
        self.assertEquals(dict(id='g-%d' % nobody.pk, text="Nobody", extra=0), response['results'][1])

        # all 4 contacts A-Z
        self.assertEquals(dict(id='c-%d' % self.billy.pk, text="Billy Nophone"), response['results'][2])
        self.assertEquals(dict(id='c-%d' % self.frank.pk, text="Frank Smith"), response['results'][3])
        self.assertEquals(dict(id='c-%d' % self.joe.pk, text="Joe Blow"), response['results'][4])
        self.assertEquals(dict(id='c-%d' % self.voldemort.pk, text="250788383383"), response['results'][5])

        # 3 sendable URNs with names as extra
        self.assertEquals(dict(id='u-%d' % joe_tel.pk, text="123", extra="Joe Blow", scheme='tel'), response['results'][6])
        self.assertEquals(dict(id='u-%d' % frank_tel.pk, text="1234", extra="Frank Smith", scheme='tel'), response['results'][7])
        self.assertEquals(dict(id='u-%d' % voldemort_tel.pk, text="250788383383", extra=None, scheme='tel'), response['results'][8])

        # apply type filters...
        response = json.loads(self.client.get("%s?types=g" % reverse("contacts.contact_omnibox")).content)
        self.assertEquals(2, len(response['results']))

        # just 2 groups
        self.assertEquals(dict(id='g-%d' % joe_and_frank.pk, text="Joe and Frank", extra=2), response['results'][0])
        self.assertEquals(dict(id='g-%d' % nobody.pk, text="Nobody", extra=0), response['results'][1])

        response = json.loads(self.client.get("%s?types=c,u" % reverse("contacts.contact_omnibox")).content)
        self.assertEquals(7, len(response['results']))

        # all 4 contacts A-Z
        self.assertEquals(dict(id='c-%d' % self.billy.pk, text="Billy Nophone"), response['results'][0])
        self.assertEquals(dict(id='c-%d' % self.frank.pk, text="Frank Smith"), response['results'][1])
        self.assertEquals(dict(id='c-%d' % self.joe.pk, text="Joe Blow"), response['results'][2])
        self.assertEquals(dict(id='c-%d' % self.voldemort.pk, text="250788383383"), response['results'][3])

        # 3 sendable URNs with names as extra
        self.assertEquals(dict(id='u-%d' % joe_tel.pk, text="123", extra="Joe Blow", scheme='tel'), response['results'][4])
        self.assertEquals(dict(id='u-%d' % frank_tel.pk, text="1234", extra="Frank Smith", scheme='tel'), response['results'][5])
        self.assertEquals(dict(id='u-%d' % voldemort_tel.pk, text="250788383383", extra=None, scheme='tel'), response['results'][6])

        # search for Frank by phone
        response = json.loads(self.client.get("%s?search=1234" % reverse("contacts.contact_omnibox")).content)
        self.assertEquals(dict(id='u-%d' % frank_tel.pk, text="1234", extra="Frank Smith", scheme='tel'), response['results'][0])
        self.assertEquals(1, len(response['results']))

        # search for Joe by twitter - won't return anything because there is no twitter channel
        response = json.loads(self.client.get("%s?search=blow80" % reverse("contacts.contact_omnibox")).content)
        self.assertEquals(0, len(response['results']))

        # create twitter channel
        Channel.create(self.org, self.user, None, 'TT')

        # search for again for Joe by twitter
        response = json.loads(self.client.get("%s?search=blow80" % reverse("contacts.contact_omnibox")).content)
        self.assertEquals(dict(id='u-%d' % joe_twitter.pk, text="blow80", extra="Joe Blow", scheme='twitter'), response['results'][0])
        self.assertEquals(1, len(response['results']))

        # search for Joe again - match on last name and twitter handle
        response = json.loads(self.client.get("%s?search=BLOW" % reverse("contacts.contact_omnibox")).content)
        self.assertEquals(dict(id='c-%d' % self.joe.pk, text="Joe Blow"), response['results'][0])
        self.assertEquals(dict(id='u-%d' % joe_twitter.pk, text="blow80", extra="Joe Blow", scheme='twitter'), response['results'][1])
        self.assertEquals(2, len(response['results']))

        # make sure our matches are ANDed
        response = json.loads(self.client.get("%s?search=Joe+o&types=c" % reverse("contacts.contact_omnibox")).content)
        self.assertEquals(dict(id='c-%d' % self.joe.pk, text="Joe Blow"), response['results'][0])
        self.assertEquals(1, len(response['results']))

        # lookup by contact ids
        contact_ids = "%d,%d" % (self.joe.pk, self.frank.pk)
        response = json.loads(self.client.get("%s?&c=%s" % (reverse("contacts.contact_omnibox"), contact_ids)).content)
        self.assertEquals(dict(id='c-%d' % self.frank.pk, text="Frank Smith"), response['results'][0])
        self.assertEquals(dict(id='c-%d' % self.joe.pk, text="Joe Blow"), response['results'][1])
        self.assertEquals(2, len(response['results']))

        # lookup by group id
        response = json.loads(self.client.get("%s?&g=%d" % (reverse("contacts.contact_omnibox"), joe_and_frank.pk)).content)
        self.assertEquals(dict(id='g-%d' % joe_and_frank.pk, text="Joe and Frank", extra=2), response['results'][0])
        self.assertEquals(1, len(response['results']))

        # lookup by URN ids
        urn_ids = "%d,%d" % (self.joe.get_urn(TWITTER_SCHEME).pk, self.frank.get_urn(TEL_SCHEME).pk)
        response = json.loads(self.client.get("%s?&u=%s" % (reverse("contacts.contact_omnibox"), urn_ids)).content)
        self.assertEquals(dict(id='u-%d' % frank_tel.pk, text="1234", extra="Frank Smith", scheme='tel'), response['results'][0])
        self.assertEquals(dict(id='u-%d' % joe_twitter.pk, text="blow80", extra="Joe Blow", scheme='twitter'), response['results'][1])
        self.assertEquals(2, len(response['results']))

        # lookup by message ids
        msg = self.create_msg(direction='I', contact=self.joe, text="some message")
        response = json.loads(self.client.get("%s?m=%s" % (reverse("contacts.contact_omnibox"), msg.pk)).content)
        self.assertEquals(dict(id='c-%d' % self.joe.pk, text="Joe Blow"), response['results'][0])
        self.assertEquals(1, len(response['results']))

        # lookup by label ids
        label = Label.get_or_create(self.org, self.user, "msg label")
        response = json.loads(self.client.get("%s?l=%s" % (reverse("contacts.contact_omnibox"), label.pk)).content)
        self.assertEquals(0, len(response['results']))

        msg.labels.add(label)
        response = json.loads(self.client.get("%s?l=%s" % (reverse("contacts.contact_omnibox"), label.pk)).content)
        self.assertEquals(dict(id='c-%d' % self.joe.pk, text="Joe Blow"), response['results'][0])
        self.assertEquals(1, len(response['results']))

        with AnonymousOrg(self.org):
            response = json.loads(self.client.get("%s" % reverse("contacts.contact_omnibox")).content)
            self.assertEquals(6, len(response['results']))

            # both groups...
            self.assertEquals(dict(id='g-%d' % joe_and_frank.pk, text="Joe and Frank", extra=2), response['results'][0])
            self.assertEquals(dict(id='g-%d' % nobody.pk, text="Nobody", extra=0), response['results'][1])

            # all 4 contacts A-Z
            self.assertEquals(dict(id='c-%d' % self.billy.pk, text="Billy Nophone"), response['results'][2])
            self.assertEquals(dict(id='c-%d' % self.frank.pk, text="Frank Smith"), response['results'][3])
            self.assertEquals(dict(id='c-%d' % self.joe.pk, text="Joe Blow"), response['results'][4])
            self.assertEquals(dict(id='c-%d' % self.voldemort.pk, text=self.voldemort.anon_identifier), response['results'][5])

            # can search by frank id
            response = json.loads(self.client.get("%s?search=%d" %
                                        (reverse("contacts.contact_omnibox"), self.frank.pk)).content)
            self.assertEquals(dict(id='c-%d' % self.frank.pk, text="Frank Smith"), response['results'][0])
            self.assertEquals(1, len(response['results']))

            # but not by frank number
            response = json.loads(self.client.get("%s?search=1234" % reverse("contacts.contact_omnibox")).content)
            self.assertEquals(0, len(response['results']))

    def test_history(self):

        self.create_campaign()

        # create some messages
        msgs = []
        for i in range(105):
            msgs.append(self.create_msg(direction='I', contact=self.joe, text="Inbound message %d" % i))
            i += 1

        # start a joe flow
        from temba.flows.models import FlowRun
        self.reminder_flow.start([], [self.joe])

        # create an event from the past
        from temba.campaigns.models import EventFire

        scheduled = timezone.now() - timedelta(days=5)
        EventFire.objects.create(event=self.planting_reminder, contact=self.joe, scheduled=scheduled, fired=scheduled)

        # create a missed call
        Call.create_call(self.channel, self.joe.get_urn(TEL_SCHEME).path, timezone.now(), 5, Call.TYPE_OUT_MISSED)

        # fetch our contact history
        response = self.fetch_protected(reverse('contacts.contact_history', args=[self.joe.uuid]), self.admin)
        activity = response.context['activity']

        # even though there are no messages after it, should still see the recent call
        self.assertTrue(isinstance(activity[0], Call))

        # 100 messages, a call, and a flow run
        self.assertEquals(102, len(activity))
        self.assertTrue(response.context['more'])

        # fetch page 2
        response = self.fetch_protected('%s?page=2' % reverse('contacts.contact_history', args=[self.joe.uuid]), self.admin)
        activity = response.context['activity']
        self.assertFalse(response.context['more'])

        # six remaining messages
        self.assertEquals(7, len(activity))
        self.assertTrue(isinstance(activity[6], EventFire))

        # most recent thing is a message followed by a flow run
        response = self.fetch_protected(reverse('contacts.contact_history', args=[self.joe.uuid]), self.admin)
        activity = response.context['activity']
        self.assertTrue(isinstance(activity[1], Msg))
        self.assertTrue(isinstance(activity[2], FlowRun))

        # if a new message comes in
        self.create_msg(direction='I', contact=self.joe, text="Newer message")
        response = self.fetch_protected(reverse('contacts.contact_history', args=[self.joe.uuid]), self.admin)
        activity = response.context['activity']

        # now we'll see the message that just came in first, followed by the Call event
        self.assertEquals('Newer message', activity[0].text)
        self.assertTrue(isinstance(activity[0], Msg))
        self.assertTrue(isinstance(activity[1], Call))

        # remove 50 messages
        for i in range(50):
            msgs[i].delete()

        # add five more messages from eight days ago
        for i in range(5):
            self.create_msg(direction='I', contact=self.joe, text="Old Message", created_on=timezone.now() - timedelta(days=8))

        # number of items on the first page should be 65 now
        response = self.fetch_protected((reverse('contacts.contact_history', args=[self.joe.uuid])), self.admin)
        activity = response.context['activity']
        self.assertEquals(65, len(activity))

        recent_seconds = int(time.mktime((timezone.now() - timedelta(days=7)).timetuple()))
        response = self.fetch_protected("%s?r=true&rs=%s" % (reverse('contacts.contact_history', args=[self.joe.uuid]), recent_seconds), self.admin)
        activity = response.context['activity']

        # with our recent flag on, should not see the 5 older messages
        self.assertEquals(60, len(activity))

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
        msg = Msg.create_incoming(self.channel, (TEL_SCHEME, '+1234'), "Inbound message")

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
                                               relative_to=self.planting_date, offset=i+10, unit='D',
                                               message='Sent %d days after planting date' % (i+10))

        now = timezone.now()
        self.joe.set_field('planting_date', unicode(now + timedelta(days=1)))
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
        kLab = self.create_group("kLab", [self.joe])

        # post to read url, joe's contact and kLab group
        post_data = dict(contact=self.joe.id, group=kLab.id)
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

    def test_update_and_list(self):
        from temba.msgs.tasks import check_messages_task
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
        self.assertFalse(ContactGroup.user_groups.get(name="New Test").is_active)

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

        # Now let's test the filters
        just_joe_filter_url = reverse('contacts.contact_filter', args=[self.just_joe.pk])
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

        # shouldn't be any contacts on the failed page
        response = self.client.get(reverse('contacts.contact_failed'))
        self.assertEquals(0, len(response.context['object_list']))

        # create a failed message for joe
        sms = Msg.create_outgoing(self.org, self.admin, self.frank, "Failed Outgoing")
        sms.status = 'F'
        sms.save()

        check_messages_task()

        response = self.client.get(reverse('contacts.contact_failed'))
        self.assertEquals(1, len(response.context['object_list']))
        self.assertEquals(1, response.context['object_list'].count())  # from cache

        # having another message that is successful removes us from the list though
        sms = Msg.create_outgoing(self.org, self.admin, self.frank, "Delivered Outgoing")
        sms.status = 'D'
        sms.save()

        check_messages_task()

        response = self.client.get(reverse('contacts.contact_failed'))
        self.assertEquals(0, len(response.context['object_list']))
        self.assertEquals(0, response.context['object_list'].count())  # from cache

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
        ContactField.objects.create(org=self.org, key='state', label="Home state", value_type=STATE)
        self.joe.set_field('state', " kiGali   citY ")  # should match "Kigali City"

        # check that the field appears on the update form
        response = self.client.get(reverse('contacts.contact_update', args=[self.joe.id]))
        self.assertEqual(response.context['form'].fields.keys(), ['name', 'groups', 'loc', '__urn__tel__0', '__field__state'])
        self.assertEqual(response.context['form'].initial['name'], "Joe Blow")
        self.assertEqual(response.context['form'].fields['__urn__tel__0'].initial, "123")
        self.assertEqual(response.context['form'].fields['__field__state'].initial, "Kigali City")  # parsed name

        # update it to something else
        self.joe.set_field('state', "eastern province")

        # check the read page
        response = self.client.get(reverse('contacts.contact_read', args=[self.joe.uuid]))
        self.assertContains(response, "Eastern Province")

        # update joe - change his tel URN and state field (to something invalid)
        data = dict(name="Joe Blow", __urn__tel__0="12345", __field__state="newyork")
        self.client.post(reverse('contacts.contact_update', args=[self.joe.id]), data)

        # check that old URN is detached, new URN is attached, and Joe still exists
        self.joe = Contact.objects.get(pk=self.joe.id)
        self.assertEquals("12345", self.joe.get_urn_display(scheme=TEL_SCHEME))
        self.assertEquals(self.joe.get_field_raw('state'), "newyork")  # raw user input as location wasn't matched
        self.assertFalse(Contact.from_urn(self.org, TEL_SCHEME, "123"))  # tel 123 is nobody now

        # update joe, change his number back
        data = dict(name="Joe Blow", __urn__tel__0="123", __field__location="Kigali")
        self.client.post(reverse('contacts.contact_update', args=[self.joe.id]), data)

        # check that old URN is re-attached
        self.assertIsNone(ContactURN.objects.get(urn="tel:12345").contact)
        self.assertEquals(self.joe, ContactURN.objects.get(urn="tel:123").contact)

        # add another URN to joe
        ContactURN.create(self.org, self.joe, TEL_SCHEME, "67890")

        # assert that our update form has the extra URN
        response = self.client.get(reverse('contacts.contact_update', args=[self.joe.id]))
        self.assertEquals("123", response.context['form'].fields['__urn__tel__0'].initial)
        self.assertEquals("67890", response.context['form'].fields['__urn__tel__1'].initial)

        # update joe, add him to "Just Joe" group
        post_data = dict(name="Joe Gashyantare", groups=[self.just_joe.id],
                         __urn__tel__0="123", __urn__tel__1="67890")
        response = self.client.post(reverse('contacts.contact_update', args=[self.joe.id]), post_data, follow=True)
        self.assertEqual(response.context['contact'].name, "Joe Gashyantare")
        self.assertEqual(set(self.joe.user_groups.all()), {self.just_joe})
        self.assertTrue(ContactURN.objects.filter(contact=self.joe, path="123"))
        self.assertTrue(ContactURN.objects.filter(contact=self.joe, path="67890"))

        # remove him from this group "Just joe", and his second number
        post_data = dict(name="Joe Gashyantare", __urn__tel__0="12345", groups=[])
        response = self.client.post(reverse('contacts.contact_update', args=[self.joe.id]), post_data, follow=True)
        self.assertEqual(set(self.joe.user_groups.all()), set())
        self.assertTrue(ContactURN.objects.filter(contact=self.joe, path="12345"))
        self.assertFalse(ContactURN.objects.filter(contact=self.joe, path="67890"))
        self.assertFalse(ContactURN.objects.filter(contact=self.joe, path="1232"))

        # should no longer be in our update form either
        response = self.client.get(reverse('contacts.contact_update', args=[self.joe.id]))
        self.assertEquals("12345", response.context['form'].fields['__urn__tel__0'].initial)
        self.assertFalse('__urn__tel__1' in response.context['form'].fields)

        # check that groups field isn't displayed when contact is blocked
        self.joe.block()
        response = self.client.get(reverse('contacts.contact_update', args=[self.joe.id]))
        self.assertNotIn('groups', response.context['form'].fields)

        # and that we can still update the contact
        post_data = dict(name="Joe Bloggs", __urn__tel__0="12345")
        self.client.post(reverse('contacts.contact_update', args=[self.joe.id]), post_data, follow=True)

        self.joe = Contact.objects.get(pk=self.joe.pk)
        self.assertEquals(self.joe.name, "Joe Bloggs")

        self.joe.unblock()

        # check updating when org is anon
        self.org.is_anon = True
        self.org.save()

        post_data = dict(name="Joe X", groups=[self.just_joe.id])
        self.client.post(reverse('contacts.contact_update', args=[self.joe.id]), post_data, follow=True)
        self.assertEquals(Contact.from_urn(self.org, TEL_SCHEME, "12345"), self.joe)  # ensure Joe still has tel 12345
        self.assertEquals(Contact.from_urn(self.org, TEL_SCHEME, "12345").name, "Joe X")

    def test_contact_model(self):
        contact1 = self.create_contact(name=None, number="123456")

        contact1.set_first_name("Ludacris")
        self.assertEquals(contact1.name, "Ludacris")

        first_modified_on = contact1.modified_on
        contact1.set_field('occupation', 'Musician')
        self.assertTrue(Contact.objects.get(pk=contact1.pk).modified_on > first_modified_on)

        contact2 = self.create_contact(name="Boy", number="12345")
        self.assertEquals(contact2.get_display(), "Boy")

        # try to create an instance contact without number, the contact object is not created
        fields = dict(org=self.org, name="Paul Chris")
        with self.assertRaises(SmartImportRowError):
            Contact.create_instance(fields)

        contact3 = self.create_contact(name=None, number="0788111222")
        self.channel.country = 'RW'
        self.channel.save()

        normalized = contact3.get_urn(TEL_SCHEME).ensure_number_normalization(self.channel)
        self.assertEquals(normalized.path, "+250788111222")

        contact4 = self.create_contact(name=None, number="+250788333444")
        normalized = contact4.get_urn(TEL_SCHEME).ensure_number_normalization(self.channel)
        self.assertEquals(normalized.path, "+250788333444")

        # check normalization leads to matching
        contact5 = self.create_contact(name='Jimmy', number="+250788333555")
        contact6 = self.create_contact(name='James', number="0788333555")
        self.assertEquals(contact5.pk, contact6.pk)

        contact5.update_urns([(TEL_SCHEME, '0788333666'), (TWITTER_SCHEME, 'jimmy_woot')])

        # check old phone URN still existing but was detached
        self.assertIsNone(ContactURN.objects.get(urn='tel:+250788333555').contact)

        # check new URNs were created and attached
        self.assertEquals(contact5, ContactURN.objects.get(urn='tel:+250788333666').contact)
        self.assertEquals(contact5, ContactURN.objects.get(urn='twitter:jimmy_woot').contact)

        # check twitter URN takes priority if you don't specify scheme
        self.assertEquals('twitter:jimmy_woot', contact5.get_urn().urn)
        self.assertEquals('twitter:jimmy_woot', contact5.get_urn(schemes=[TWITTER_SCHEME]).urn)
        self.assertEquals('tel:+250788333666', contact5.get_urn(schemes=[TEL_SCHEME]).urn)
        self.assertIsNone(contact5.get_urn(schemes=['email']))
        self.assertIsNone(contact5.get_urn(schemes=['facebook']))

        # check that we can steal other contact's URNs
        contact5.update_urns([(TEL_SCHEME, '0788333444')])
        self.assertEquals(contact5, ContactURN.objects.get(urn='tel:+250788333444').contact)
        self.assertFalse(contact4.urns.all())

    def test_from_urn(self):
        self.assertEqual(self.joe, Contact.from_urn(self.org, 'tel', '123'))  # URN with contact
        self.assertIsNone(Contact.from_urn(self.org, 'tel', '8888'))  # URN with no contact

    def test_validate_import_header(self):
        with self.assertRaises(Exception):
            Contact.validate_import_header([])

        with self.assertRaises(Exception):
            Contact.validate_import_header(['name'])  # missing a URN

        with self.assertRaises(Exception):
            Contact.validate_import_header(['phone', 'twitter', 'external'])  # missing name

        Contact.validate_import_header(['name', 'phone', 'twitter', 'external'])
        Contact.validate_import_header(['name', 'phone'])
        Contact.validate_import_header(['name', 'twitter'])
        Contact.validate_import_header(['name', 'external'])

    def do_import(self, user, filename):

        import_params = dict(org_id=self.org.id, timezone=self.org.timezone, extra_fields=[],
                             original_filename=filename)

        task = ImportTask.objects.create(
            created_by=user, modified_by=user,
            csv_file='test_imports/' + filename,
            model_class="Contact", import_params=json.dumps(import_params), import_log="", task_id="A")

        return Contact.import_csv(task, log=None)

    def test_contact_import(self):
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

        with patch('temba.orgs.models.Org.lock_on') as mock_lock:
            # import contact with uuid will force update if existing contact for the uuid
            csv_file = open('%s/test_imports/sample_contacts_uuid.xls' % settings.MEDIA_ROOT, 'rb')
            post_data = dict(csv_file=csv_file)
            response = self.client.post(import_url, post_data, follow=True)
            self.assertIsNotNone(response.context['task'])
            self.assertIsNotNone(response.context['group'])
            self.assertFalse(response.context['show_form'])
            self.assertEquals(response.context['results'], dict(records=4, errors=0, error_messages=[],
                                                                creates=2, updates=2))

            self.assertEquals(mock_lock.call_count, 3)

        self.assertEquals(1, Contact.objects.filter(name='Eric Newcomer').count())
        self.assertEquals(0, Contact.objects.filter(name='Bob').count())
        self.assertEquals(0, Contact.objects.filter(name='Jeff').count())
        self.assertEquals('uuid-1111', Contact.objects.filter(name='Eric Newcomer').first().uuid)
        self.assertEquals('uuid-4444', Contact.objects.filter(name='Michael').first().uuid)
        self.assertFalse(Contact.objects.filter(uuid='uuid-3333')) # previously inexistent uuid ignored

        Contact.objects.all().delete()
        ContactGroup.user_groups.all().delete()

        # import sample contact spreadsheet with valid headers
        csv_file = open('%s/test_imports/sample_contacts.xls' % settings.MEDIA_ROOT, 'rb')
        post_data = dict(csv_file=csv_file)
        response = self.client.post(import_url, post_data, follow=True)
        self.assertIsNotNone(response.context['task'])
        self.assertIsNotNone(response.context['group'])
        self.assertFalse(response.context['show_form'])
        self.assertEquals(response.context['results'], dict(records=3, errors=0, error_messages=[],
                                                            creates=3, updates=0))

        # import again to check contacts are updated
        csv_file = open('%s/test_imports/sample_contacts.xls' % settings.MEDIA_ROOT, 'rb')
        post_data = dict(csv_file=csv_file)
        response = self.client.post(import_url, post_data, follow=True)
        self.assertEquals(response.context['results'], dict(records=3, errors=0, error_messages=[],
                                                            creates=0, updates=3))

        # import a spreadsheet that includes the test contact
        csv_file = open('%s/test_imports/sample_contacts_inc_test.xls' % settings.MEDIA_ROOT, 'rb')
        response = self.client.post(import_url, dict(csv_file=csv_file), follow=True)
        self.assertEquals(response.context['results'], dict(records=2, errors=1, creates=0, updates=2,
                                                            error_messages=[dict(line=4,
                                                                                 error="Ignored test contact")]))

        # import a spreadsheet where a contact has a missing phone number and another has an invalid number
        csv_file = open('%s/test_imports/sample_contacts_with_missing_and_invalid_phones.xls' % settings.MEDIA_ROOT,
                        'rb')
        post_data = dict(csv_file=csv_file)
        response = self.client.post(import_url, post_data, follow=True)
        self.assertEquals(response.context['results'], dict(records=1, errors=2, creates=0, updates=1,
                                                            error_messages=[dict(line=3,
                                                                                 error="Missing any valid URNs; at "
                                                                                       "least one among 'twitter, ext "
                                                                                       "or phone' should be provided"),
                                                                            dict(line=4,
                                                                                 error="Invalid Phone number 12345")]))

        csv_file = open('%s/test_imports/sample_contacts_twitter.xls' % settings.MEDIA_ROOT, 'rb')
        post_data = dict(csv_file=csv_file)
        response = self.client.post(import_url, post_data, follow=True)
        self.assertIsNotNone(response.context['task'])
        self.assertIsNotNone(response.context['group'])
        self.assertFalse(response.context['show_form'])
        self.assertEquals(response.context['results'], dict(records=3, errors=0, error_messages=[],
                                                            creates=3, updates=0))

        Contact.objects.all().delete()
        ContactGroup.user_groups.all().delete()

        csv_file = open('%s/test_imports/sample_contacts_twitter_and_phone.xls' % settings.MEDIA_ROOT, 'rb')
        post_data = dict(csv_file=csv_file)
        response = self.client.post(import_url, post_data, follow=True)
        self.assertIsNotNone(response.context['task'])
        self.assertIsNotNone(response.context['group'])
        self.assertFalse(response.context['show_form'])
        self.assertEquals(response.context['results'], dict(records=3, errors=0, error_messages=[],
                                                            creates=3, updates=0))

        self.assertEquals(3, Contact.objects.all().count())
        self.assertEquals(1, Contact.objects.filter(name='Rapidpro').count())
        self.assertEquals(1, Contact.objects.filter(name='Textit').count())
        self.assertEquals(1, Contact.objects.filter(name='Nyaruka').count())

        # import file with row different urn on different existing contacts should ignore those lines
        csv_file = open('%s/test_imports/sample_contacts_twitter_and_phone_conflicts.xls' % settings.MEDIA_ROOT, 'rb')
        post_data = dict(csv_file=csv_file)
        response = self.client.post(import_url, post_data, follow=True)
        self.assertEquals(response.context['results'], dict(records=2, errors=0, creates=0, updates=2,
                                                            error_messages=[]))

        self.assertEquals(3, Contact.objects.all().count())
        self.assertEquals(1, Contact.objects.filter(name='Rapidpro').count())
        self.assertEquals(0, Contact.objects.filter(name='Textit').count())
        self.assertEquals(0, Contact.objects.filter(name='Nyaruka').count())
        self.assertEquals(1, Contact.objects.filter(name='Kigali').count())
        self.assertEquals(1, Contact.objects.filter(name='Klab').count())

        Contact.objects.all().delete()
        ContactGroup.user_groups.all().delete()

        csv_file = open('%s/test_imports/sample_contacts_twitter_and_phone_optional.xls' % settings.MEDIA_ROOT, 'rb')
        post_data = dict(csv_file=csv_file)
        response = self.client.post(import_url, post_data, follow=True)
        self.assertIsNotNone(response.context['task'])
        self.assertIsNotNone(response.context['group'])
        self.assertFalse(response.context['show_form'])
        self.assertEquals(response.context['results'], dict(records=3, errors=0, error_messages=[],
                                                            creates=3, updates=0))

        Contact.objects.all().delete()
        ContactGroup.user_groups.all().delete()

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
                             'The file you provided is missing a required header. At least one of '
                             '"Phone", "Twitter", "External" should be included.')

        csv_file = open('%s/test_imports/sample_contacts_missing_name_phone_headers.xls' % settings.MEDIA_ROOT, 'rb')
        post_data = dict(csv_file=csv_file)
        response = self.client.post(import_url, post_data)
        self.assertFormError(response, 'form', 'csv_file',
                             'The file you provided is missing required headers called "Name" and one of '
                             '"Phone", "Twitter", "External".')

        # check that no contacts or groups were created by any of the previous invalid imports
        self.assertEquals(Contact.objects.all().count(), 0)
        self.assertEquals(ContactGroup.user_groups.all().count(), 0)

        # existing field
        ContactField.get_or_create(self.org, 'ride_or_drive', 'Vehicle')
        ContactField.get_or_create(self.org, 'wears', 'Shoes')  # has trailing spaces on excel files as " Shoes  "


        # import spreadsheet with extra columns
        csv_file = open('%s/test_imports/sample_contacts_with_extra_fields.xls' % settings.MEDIA_ROOT, 'rb')
        post_data = dict(csv_file=csv_file)
        response = self.client.post(import_url, post_data, follow=True)
        self.assertIsNotNone(response.context['task'])
        self.assertEquals(response.request['PATH_INFO'], reverse('contacts.contact_customize',
                                                                 args=[response.context['task'].pk]))
        self.assertEquals(len(response.context['form'].fields.keys()), 21)

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

        # if we change the field type for 'location' to 'datetime' we shouldn't get a category
        ContactField.objects.filter(key='location').update(value_type=DATETIME)
        contact1 = Contact.objects.all().order_by('name')[0]

        # Not a valid date, so should be None
        self.assertEquals(contact1.get_field_display('location'), None)

        # return it back to a state field
        ContactField.objects.filter(key='location').update(value_type=STATE)
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
        csv_file = open('%s/test_imports/sample_contacts_with_extra_fields.xls' % settings.MEDIA_ROOT, 'rb')
        post_data = dict(csv_file=csv_file)
        response = self.client.post(import_url, post_data, follow=True)
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
        self.assertFormError(response, 'form', None, 'Name is a reserved name for contact fields')

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

    def test_contact_import_with_languages(self):
        self.create_contact(name="Eric", number="+250788382382")

        self.do_import(self.user, 'sample_contacts_with_language.xls')

        self.assertEqual(Contact.objects.get(urns__path="+250788382382").language, 'eng')  # updated
        self.assertEqual(Contact.objects.get(urns__path="+250788383383").language, 'fre')  # created with language
        self.assertEqual(Contact.objects.get(urns__path="+250788383385").language, None)   # no language

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

        c1.block()
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
        field_dict = Contact.prepare_fields(field_dict, import_params)
        self.assertNotIn('yourmom', field_dict)
        self.assertNotIn('nick name', field_dict)
        self.assertEquals(field_dict['nick_name'], 'bob')
        self.assertEquals(field_dict['org'], self.org)

        # check that trying to save an extra field with a reserved name throws an exception
        with self.assertRaises(Exception):
            import_params = dict(org_id=self.org.id, timezone=timezone.UTC, extra_fields=[
                dict(key='phone', header='phone', label='Phone')
            ])
            Contact.prepare_fields(field_dict, import_params)

    def test_fields(self):
        # set a field on joe
        self.joe.set_field('abc_1234', 'Joe', label="Name")
        self.assertEquals('Joe', self.joe.get_field_raw('abc_1234'))

        self.joe.set_field('abc_1234', None)
        self.assertEquals(None, self.joe.get_field_raw('abc_1234'))

        # try storing an integer, should get turned into a string
        self.joe.set_field('abc_1234', 1)
        self.assertEquals('1', self.joe.get_field_raw('abc_1234'))

        # we should have a field with the key
        ContactField.objects.get(key='abc_1234', label="Name", org=self.joe.org)

        # setting with a different label should update it
        self.joe.set_field('abc_1234', 'Joe', label="First Name")
        self.assertEquals('Joe', self.joe.get_field_raw('abc_1234'))
        ContactField.objects.get(key='abc_1234', label="First Name", org=self.joe.org)

    def test_serialize_field_value(self):
        registration_field = ContactField.get_or_create(self.org, 'registration_date', "Registration Date",
                                                        None, DATETIME)

        weight_field = ContactField.get_or_create(self.org, 'weight', "Weight", None, DECIMAL)
        color_field = ContactField.get_or_create(self.org, 'color', "Color", None, TEXT)

        joe = Contact.objects.get(pk=self.joe.pk)
        joe.set_field('registration_date', "2014-12-31 03:04:00")
        joe.set_field('weight', "75.888888")
        joe.set_field('color', "green")

        value = joe.get_field(registration_field.key)
        self.assertEqual(Contact.serialize_field_value(registration_field, value), '2014-12-31T01:04:00.000000Z')

        value = joe.get_field(weight_field.key)
        self.assertEqual(Contact.serialize_field_value(weight_field, value), '75.888888')

        value = joe.get_field(color_field.key)
        value.category = "Dark"
        value.save()

        self.assertEqual(Contact.serialize_field_value(color_field, value), 'Dark')

    def test_set_location_fields(self):
        district_field = ContactField.get_or_create(self.org, 'district', 'District', None, DISTRICT)

        # add duplicate district in different states
        east_province = AdminBoundary.objects.create(osm_id='R005', name='East Province', level=1, parent=self.country)
        AdminBoundary.objects.create(osm_id='R004', name='Remera', level=2, parent=east_province)
        kigali = AdminBoundary.objects.get(name="Kigali City")
        AdminBoundary.objects.create(osm_id='R003', name='Remera', level=2, parent=kigali)

        joe = Contact.objects.get(pk=self.joe.pk)
        joe.set_field('district', 'Remera')
        value = Value.objects.filter(contact=joe, contact_field=district_field).first()
        self.assertFalse(value.location_value)

        state_field = ContactField.get_or_create(self.org, 'state', 'State', None, STATE)

        joe.set_field('state', 'Kigali city')
        value = Value.objects.filter(contact=joe, contact_field=state_field).first()
        self.assertTrue(value.location_value)
        self.assertEquals(value.location_value.name, "Kigali City")

        joe.set_field('district', 'Remera')
        value = Value.objects.filter(contact=joe, contact_field=district_field).first()
        self.assertTrue(value.location_value)
        self.assertEquals(value.location_value.name, "Remera")
        self.assertEquals(value.location_value.parent, kigali)

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

        self.joe = Contact.objects.get(pk=self.joe.pk)
        message_context = self.joe.build_message_context()

        self.assertEquals("Joe", message_context['first_name'])
        self.assertEquals("Joe Blow", message_context['name'])
        self.assertEquals("Joe Blow", message_context['__default__'])
        self.assertEquals("123", message_context['tel'])
        self.assertEquals("Reporters", message_context['groups'])

    def test_update_handling(self):
        from temba.campaigns.models import Campaign, CampaignEvent, EventFire

        def create_dynamic_group(name, query):
            return ContactGroup.create(self.org, self.user, name, query=query)

        self.bob = self.create_contact("Bob", "111222")
        self.bob.name = 'Bob Marley'
        self.bob.save()
        old_modified_on = self.bob.modified_on
        self.bob.handle_update()
        self.assertTrue(self.bob.modified_on > old_modified_on)

        old_modified_on = self.bob.modified_on
        self.bob.update_urns([('tel', "111333")])
        self.assertTrue(self.bob.modified_on > old_modified_on)

        # run all tests as 2/Jan/2014 03:04 AFT
        tz = pytz.timezone('Asia/Kabul')
        with patch.object(timezone, 'now', return_value=tz.localize(datetime(2014, 1, 2, 3, 4, 5, 6))):
            age_field = ContactField.get_or_create(self.org, 'age', "Age", value_type='N')
            gender_field = ContactField.get_or_create(self.org, 'gender', "Gender", value_type='T')
            joined_field = ContactField.get_or_create(self.org, 'joined', "Join Date", value_type='D')

            # create groups based on name or URN (checks that contacts are added correctly on contact create)
            joes_group = create_dynamic_group("People called Joe", 'name has joe')
            _123_group = create_dynamic_group("People with number containing '123'", 'tel has "123"')

            self.mary = self.create_contact("Mary", "123456")
            self.mary.set_field('gender', "Female")
            self.mary.set_field('age', 21)
            self.mary.set_field('joined', '31/12/2013')
            self.annie = self.create_contact("Annie", "7879")
            self.annie.set_field('gender', "Female")
            self.annie.set_field('age', 9)
            self.annie.set_field('joined', '31/12/2013')
            self.joe.set_field('gender', "Male")
            self.joe.set_field('age', 25)
            self.joe.set_field('joined', '1/1/2014')
            self.frank.set_field('gender', "Male")
            self.frank.set_field('age', 50)
            self.frank.set_field('joined', '1/1/2014')

            # create more groups based on fields (checks that contacts are added correctly on group create)
            men_group = create_dynamic_group("Girls", 'gender = "male" AND age >= 18')
            women_group = create_dynamic_group("Girls", 'gender = "female" AND age >= 18')

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

            # check event fire initialized correctly
            joe_fires = EventFire.objects.filter(event=joes_event)
            self.assertEquals(1, joe_fires.count())
            self.assertEquals(self.joe, joe_fires.first().contact)

            # Frank becomes Francine...
            self.frank.set_field('gender', "Female")
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
            self.mary.update_urns([('tel', "54321"), ('twitter', 'mary_mary')])
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


class ContactURNTest(TembaTest):
    def setUp(self):
        TembaTest.setUp(self)

    def test_parse_urn(self):
        urn_tuple = lambda p: (p.scheme, p.path)

        self.assertEquals(('tel', '+1234'), urn_tuple(ContactURN.parse_urn('tel:+1234')))
        self.assertEquals(('twitter', 'billy_bob'), urn_tuple(ContactURN.parse_urn('twitter:billy_bob')))
        self.assertRaises(Exception, ContactURN.parse_urn, 'tel : 1234')  # URNs can't have spaces
        self.assertRaises(Exception, ContactURN.parse_urn, 'xxx:1234')  # no such scheme

    def test_format_urn(self):
        self.assertEquals('tel:+1234', ContactURN.format_urn('tel', '+1234'))

    def test_normalize_urn(self):
        # valid tel numbers
        self.assertEquals(('tel', "+250788383383"), ContactURN.normalize_urn('TEL', "0788383383", "RW"))
        self.assertEquals(('tel', "+250788383383"), ContactURN.normalize_urn('tel', "+250788383383", "KE"))
        self.assertEquals(('tel', "+250788383383"), ContactURN.normalize_urn('tel', "+250788383383", None))
        self.assertEquals(('tel', "+250788383383"), ContactURN.normalize_urn('tel', "250788383383", None))
        self.assertEquals(('tel', "+250788383383"), ContactURN.normalize_urn('tel', "2.50788383383E+11", None))
        self.assertEquals(('tel', "+250788383383"), ContactURN.normalize_urn('tel', "2.50788383383E+12", None))
        self.assertEquals(('tel', "+19179925253"), ContactURN.normalize_urn('tel', "(917) 992-5253", "US"))
        self.assertEquals(('tel', "+19179925253"), ContactURN.normalize_urn('tel', "19179925253", None))
        self.assertEquals(('tel', "+62877747666"), ContactURN.normalize_urn('tel', "+62877747666", None))
        self.assertEquals(('tel', "+62877747666"), ContactURN.normalize_urn('tel', "62877747666", "ID"))
        self.assertEquals(('tel', "+62877747666"), ContactURN.normalize_urn('tel', "0877747666", "ID"))

        # invalid tel numbers
        self.assertEquals(('tel', "12345"), ContactURN.normalize_urn(TEL_SCHEME, "12345", "RW"))
        self.assertEquals(('tel', "0788383383"), ContactURN.normalize_urn(TEL_SCHEME, "0788383383", None))
        self.assertEquals(('tel', "0788383383"), ContactURN.normalize_urn(TEL_SCHEME, "0788383383", "ZZ"))
        self.assertEquals(('tel', "mtn"), ContactURN.normalize_urn(TEL_SCHEME, "MTN", "RW"))

        # twitter handles
        self.assertEquals(('twitter', "jimmyjo"), ContactURN.normalize_urn('TWITTER', "jimmyjo"))
        self.assertEquals(('twitter', "billy_bob"), ContactURN.normalize_urn('twitter', " @billy_bob "))

    def test_validate_urn(self):
        # valid tel numbers
        self.assertTrue(ContactURN.validate_urn('tel', "0788383383", "RW"))
        self.assertTrue(ContactURN.validate_urn('tel', "+250788383383", "KE"))
        self.assertTrue(ContactURN.validate_urn('tel', "+23761234567", "CM"))  # old Cameroon format
        self.assertTrue(ContactURN.validate_urn('tel', "+237661234567", "CM"))  # new Cameroon format
        self.assertTrue(ContactURN.validate_urn('tel', "+250788383383", None))
        self.assertTrue(ContactURN.validate_urn('tel', "0788383383", None))  # assumed valid because no country

        # invalid tel numbers
        self.assertFalse(ContactURN.validate_urn('tel', "0788383383", "ZZ"))  # invalid country
        self.assertFalse(ContactURN.validate_urn('tel', "MTN", "RW"))

        # valid twitter handles
        self.assertTrue(ContactURN.validate_urn('twitter', "jimmyjo"))
        self.assertTrue(ContactURN.validate_urn('twitter', "billy_bob"))

        # invalid twitter handles
        self.assertFalse(ContactURN.validate_urn('twitter', "jimmyjo!@"))
        self.assertFalse(ContactURN.validate_urn('twitter', "billy bob"))

    def test_get_display(self):
        urn = ContactURN.objects.create(org=self.org, scheme='tel', path='+250788383383', urn='tel:+250788383383', priority=50)
        self.assertEquals('0788 383 383', urn.get_display(self.org))
        self.assertEquals('+250788383383', urn.get_display(self.org, full=True))

        urn = ContactURN.objects.create(org=self.org, scheme='twitter', path='billy_bob', urn='twitter:billy_bob', priority=50)
        self.assertEquals('billy_bob', urn.get_display(self.org))


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

        self.contactfield_1 = ContactField.get_or_create(self.org, "first", "First")
        self.contactfield_2 = ContactField.get_or_create(self.org, "second", "Second")
        self.contactfield_3 = ContactField.get_or_create(self.org, "third", "Third")

    def test_get_or_create(self):
        join_date = ContactField.get_or_create(self.org, "join_date")
        self.assertEqual(join_date.key, "join_date")
        self.assertEqual(join_date.label, "Join Date")
        self.assertEqual(join_date.value_type, TEXT)

        another = ContactField.get_or_create(self.org, "another", "My Label", value_type=DECIMAL)
        self.assertEqual(another.key, "another")
        self.assertEqual(another.label, "My Label")
        self.assertEqual(another.value_type, DECIMAL)

        another = ContactField.get_or_create(self.org, "another", "Updated Label", value_type=DATETIME)
        self.assertEqual(another.key, "another")
        self.assertEqual(another.label, "Updated Label")
        self.assertEqual(another.value_type, DATETIME)

        for elt in Contact.RESERVED_FIELDS:
            with self.assertRaises(ValueError):
                ContactField.get_or_create(self.org, elt, elt, value_type=TEXT)

    def test_contact_templatetag(self):
        self.joe.set_field('First', 'Starter')
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
        from xlrd import open_workbook
        self.clear_storage()

        self.login(self.admin)

        flow = self.create_flow()

        # archive all our current contacts
        Contact.objects.filter(org=self.org).update(is_blocked=True)

        # start one of our contacts down it
        contact = self.create_contact("Ben Haggerty", '+12067799294')
        contact.set_field('First', 'One')
        flow.start([], [contact])

        # create another contact, this should sort before Ben
        contact2 = self.create_contact("Adam Sumner", '+12067799191', twitter='adam')

        Contact.get_test_contact(self.user)  # create test contact to ensure they aren't included in the export

        # create a dummy export task so that we won't be able to export
        blocking_export = ExportContactsTask.objects.create(org=self.org, host='test',
                                                            created_by=self.admin, modified_by=self.admin)

        response = self.client.get(reverse('contacts.contact_export'), dict(), follow=True)
        self.assertContains(response, "already an export in progress")

        # ok, mark that one as finished and try again
        blocking_export.is_finished = True
        blocking_export.save()

        with self.assertNumQueries(32):
            self.client.get(reverse('contacts.contact_export'), dict())
            task = ExportContactsTask.objects.all().order_by('-id').first()

            filename = "%s/test_orgs/%d/contact_exports/%s.xls" % (settings.MEDIA_ROOT, self.org.pk, task.uuid)
            workbook = open_workbook(filename, 'rb')
            sheet = workbook.sheets()[0]

            # check our headers
            self.assertExcelRow(sheet, 0, ["UUID", "Name", "Phone", "Twitter", "First", "Second", "Third"])

            # first row should be Adam
            self.assertExcelRow(sheet, 1, [contact2.uuid, "Adam Sumner", "+12067799191", "adam", "", "", ""])

            # second should be Ben
            self.assertExcelRow(sheet, 2, [contact.uuid, "Ben Haggerty", "+12067799294", "", "One", "", ""])

            self.assertEqual(sheet.nrows, 3)  # no other contacts

        # more contacts do not increase the queries
        contact3 = self.create_contact('Luol Deng', '+12078776655', twitter='deng')
        contact4 = self.create_contact('Stephen', '+12078778899', twitter='stephen')
        ContactURN.create(self.org, contact, TEL_SCHEME, '+12062233445')

        with self.assertNumQueries(32):
            self.client.get(reverse('contacts.contact_export'), dict())
            task = ExportContactsTask.objects.all().order_by('-id').first()

            filename = "%s/test_orgs/%d/contact_exports/%s.xls" % (settings.MEDIA_ROOT, self.org.pk, task.uuid)
            workbook = open_workbook(filename, 'rb')
            sheet = workbook.sheets()[0]

            # check our headers have 2 phone columns and Twitter
            self.assertExcelRow(sheet, 0, ["UUID", "Name", "Phone", "Phone", "Twitter", "First", "Second", "Third"])

            self.assertExcelRow(sheet, 1, [contact2.uuid, "Adam Sumner", "+12067799191", "", "adam", "", "", ""])
            self.assertExcelRow(sheet, 2, [contact.uuid, "Ben Haggerty", "+12067799294", "+12062233445", "", "One", "", ""])
            self.assertExcelRow(sheet, 3, [contact3.uuid, "Luol Deng", "+12078776655", "", "deng", "", "", ""])
            self.assertExcelRow(sheet, 4, [contact4.uuid, "Stephen", "+12078778899", "", "stephen", "", "", ""])

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
        self.assertFormError(response, 'form', None, "Field names must be unique")
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
            ContactField.get_or_create(self.org, key, label)
            ContactField.get_or_create(self.org2, key, label)

        self.assertEquals(Org.objects.all().count(), 2)

        ContactField.objects.filter(org=self.org, key='key1').update(is_active=False)

        self.login(self.manager1)
        response = self.client.get(contact_field_json_url)

        # redirect to login because of no access to org
        self.assertEquals(302, response.status_code)

        self.login(self.admin)
        response = self.client.get(contact_field_json_url)

        response_json = json.loads(response.content)

        self.assertEquals(len(response_json), 32)
        self.assertEquals(response_json[0]['label'], 'First')
        self.assertEquals(response_json[0]['key'], 'first')
        self.assertEquals(response_json[1]['label'], 'label0')
        self.assertEquals(response_json[1]['key'], 'key0')

        ContactField.objects.filter(org=self.org, key='key0').update(label='AAAA')

        response = self.client.get(contact_field_json_url)
        response_json = json.loads(response.content)

        self.assertEquals(len(response_json), 32)
        self.assertEquals(response_json[0]['label'], 'AAAA')
        self.assertEquals(response_json[0]['key'], 'key0')
        self.assertEquals(response_json[1]['label'], 'First')
        self.assertEquals(response_json[1]['key'], 'first')
