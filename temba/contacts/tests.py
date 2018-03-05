# -*- coding: utf-8 -*-
from __future__ import absolute_import, division, print_function, unicode_literals

import json
import pytz
import six

from datetime import datetime, date, timedelta
from django.core.files.base import ContentFile
from django.core.urlresolvers import reverse
from django.conf import settings
from django.db.models import Value as DbValue
from django.db.models.functions import Substr, Concat
from django.test import TestCase
from django.utils import timezone
from mock import patch
from openpyxl import load_workbook
from smartmin.models import SmartImportRowError
from smartmin.tests import _CRUDLTest
from smartmin.csv_imports.models import ImportTask
from temba.api.models import WebHookEvent, WebHookResult
from temba.campaigns.models import Campaign, CampaignEvent, EventFire
from temba.channels.models import Channel, ChannelEvent, ChannelLog
from temba.contacts.search import is_phonenumber
from temba.flows.models import FlowRun
from temba.ivr.models import IVRCall
from temba.locations.models import AdminBoundary
from temba.msgs.models import Msg, Label, SystemLabel, Broadcast, BroadcastRecipient
from temba.orgs.models import Org
from temba.schedules.models import Schedule
from temba.tests import AnonymousOrg, TembaTest
from temba.triggers.models import Trigger
from temba.utils.dates import datetime_to_str, datetime_to_ms, get_datetime_format
from temba.utils.profiler import QueryTracker
from temba.values.models import Value
from .models import Contact, ContactGroup, ContactField, ContactURN, ExportContactsTask, URN, EXTERNAL_SCHEME
from .models import TEL_SCHEME, TWITTER_SCHEME, ContactGroupCount
from .search import parse_query, ContactQuery, Condition, IsSetCondition, BoolCombination, SinglePropCombination, SearchException
from .tasks import squash_contactgroupcounts
from .templatetags.contacts import contact_field, activity_icon, history_class


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
        self.joe, urn_obj = Contact.get_or_create(self.org, 'tel:123', user=self.user, name='Joe')
        self.joe.set_field(self.user, 'age', 20)
        self.joe.set_field(self.user, 'home', 'Kigali')
        self.frank, urn_obj = Contact.get_or_create(self.org, "tel:124", user=self.user, name='Frank')
        self.frank.set_field(self.user, 'age', 18)

        response = self._do_test_view('list')
        self.assertEqual(list(response.context['object_list']), [self.frank, self.joe])
        self.assertIsNone(response.context['search_error'])

        response = self._do_test_view('list', query_string='search=age+%3D+18')
        self.assertEqual(list(response.context['object_list']), [self.frank])
        self.assertEqual(response.context['search'], 'age = 18')
        self.assertEqual(response.context['save_dynamic_search'], True)
        self.assertIsNone(response.context['search_error'])

        response = self._do_test_view('list', query_string='search=age+>+18+and+home+%3D+"Kigali"')
        self.assertEqual(list(response.context['object_list']), [self.joe])
        self.assertEqual(response.context['search'], 'age > 18 AND home = "Kigali"')
        self.assertEqual(response.context['save_dynamic_search'], True)
        self.assertIsNone(response.context['search_error'])

        response = self._do_test_view('list', query_string='search=Joe')
        self.assertEqual(list(response.context['object_list']), [self.joe])
        self.assertEqual(response.context['search'], 'name ~ "Joe"')
        self.assertEqual(response.context['save_dynamic_search'], False)
        self.assertIsNone(response.context['search_error'])

        # try with invalid search string
        response = self._do_test_view('list', query_string='search=(((')
        self.assertEqual(list(response.context['object_list']), [])
        self.assertEqual(response.context['search_error'], "Search query contains an error")

    def testRead(self):
        self.joe, urn_obj = Contact.get_or_create(self.org, "tel:123", user=self.user, name='Joe')

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
        self.assertEqual(response.status_code, 404)

        # invalid uuid should return 404
        response = self.client.get(reverse('contacts.contact_read', args=['invalid-uuid']))
        self.assertEqual(response.status_code, 404)

    def testDelete(self):
        object = self.getTestObject()
        self._do_test_view('delete', object, post_data=dict())
        self.assertFalse(self.getCRUDL().model.objects.get(pk=object.pk).is_active)  # check object is inactive
        self.assertEqual(0, ContactURN.objects.filter(contact=object).count())  # check no attached URNs


class ContactGroupTest(TembaTest):
    def setUp(self):
        super(ContactGroupTest, self).setUp()

        self.joe, urn_obj = Contact.get_or_create(self.org, "tel:123", user=self.admin, name="Joe Blow")
        self.frank, urn_obj = Contact.get_or_create(self.org, "tel:1234", user=self.admin, name="Frank Smith")
        self.mary, urn_obj = Contact.get_or_create(self.org, "tel:345", user=self.admin, name="Mary Mo")

    def test_create_static(self):
        group = ContactGroup.create_static(self.org, self.admin, " group one ")

        self.assertEqual(group.org, self.org)
        self.assertEqual(group.name, "group one")
        self.assertEqual(group.created_by, self.admin)
        self.assertEqual(group.status, ContactGroup.STATUS_READY)

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

        group = ContactGroup.create_dynamic(
            self.org, self.admin, "Group two",
            '(Age < 18 and gender = "male") or (Age > 18 and gender = "female")'
        )

        group.refresh_from_db()
        self.assertEqual(group.query, '(age < 18 AND gender = "male") OR (age > 18 AND gender = "female")')
        self.assertEqual(set(group.query_fields.all()), {age, gender})
        self.assertEqual(set(group.contacts.all()), {self.joe, self.mary})
        self.assertEqual(group.status, ContactGroup.STATUS_READY)

        # update group query
        group.update_query('age > 18')

        group.refresh_from_db()
        self.assertEqual(group.query, 'age > 18')
        self.assertEqual(set(group.query_fields.all()), {age})
        self.assertEqual(set(group.contacts.all()), {self.mary})
        self.assertEqual(group.status, ContactGroup.STATUS_READY)

        # can't create a dynamic group with empty query
        self.assertRaises(ValueError, ContactGroup.create_dynamic, self.org, self.admin, "Empty", "")

        # can't create a dynamic group with name attribute
        self.assertRaises(ValueError, ContactGroup.create_dynamic, self.org, self.admin, 'Jose', 'name = "Jose"')

        # can't create a dynamic group with id attribute
        self.assertRaises(ValueError, ContactGroup.create_dynamic, self.org, self.admin, 'Bose', 'id = 123')

        # can't call update_contacts on a dynamic group
        self.assertRaises(ValueError, group.update_contacts, self.admin, [self.joe], True)

        # dynamic group should not have remove to group button
        self.login(self.admin)
        filter_url = reverse('contacts.contact_filter', args=[group.uuid])
        response = self.client.get(filter_url)
        self.assertNotIn('unlabel', response.context['actions'])

        # put group back into evaluation state
        group.status = ContactGroup.STATUS_EVALUATING
        group.save(update_fields=('status',))

        # can't update query again while it is in this state
        with self.assertRaises(ValueError):
            group.update_query('age = 18')

        # can't call reevaluate on it while it is in this state
        with self.assertRaises(ValueError):
            group.reevaluate()

    def test_evaluate_dynamic_groups_from_flow(self):
        flow = self.get_flow('initialize')
        self.joe, urn_obj = Contact.get_or_create(self.org, "tel:123", user=self.admin, name="Joe Blow")

        fields = ['total_calls_made', 'total_emails_sent', 'total_faxes_sent', 'total_letters_mailed', 'address_changes', 'name_changes', 'total_editorials_submitted']
        for key in fields:
            ContactField.get_or_create(self.org, self.admin, key, value_type=Value.TYPE_DECIMAL)
            ContactGroup.create_dynamic(self.org, self.admin, "Group %s" % (key), '(%s > 10)' % key)

        with QueryTracker(assert_query_count=216, stack_count=16, skip_unique_queries=False):
            flow.start([], [self.joe])

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
        self.create_field('gender', "Gender")
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

        self.assertEqual(ContactGroup.user_groups.get(pk=group.pk).get_member_count(), 2)

        # add contacts via update_contacts
        group.update_contacts(self.user, [self.mary], add=True)

        self.assertEqual(ContactGroup.user_groups.get(pk=group.pk).get_member_count(), 3)

        # remove contacts via update_contacts
        group.update_contacts(self.user, [self.mary], add=False)

        self.assertEqual(ContactGroup.user_groups.get(pk=group.pk).get_member_count(), 2)

        # add test contact (will add to group but won't increment count)
        test_contact = Contact.get_test_contact(self.admin)
        group.update_contacts(self.user, [test_contact], add=True)

        group = ContactGroup.user_groups.get(pk=group.pk)
        self.assertEqual(group.get_member_count(), 2)
        self.assertEqual(set(group.contacts.all()), {self.joe, self.frank, test_contact})

        # blocking a contact removes them from all user groups
        self.joe.block(self.user)

        with self.assertRaises(ValueError):
            group.update_contacts(self.user, [self.joe], True)

        group = ContactGroup.user_groups.get(pk=group.pk)
        self.assertEqual(group.get_member_count(), 1)
        self.assertEqual(set(group.contacts.all()), {self.frank, test_contact})

        # unblocking won't re-add to any groups
        self.joe.unblock(self.user)

        self.assertEqual(ContactGroup.user_groups.get(pk=group.pk).get_member_count(), 1)

        # releasing also removes from all user groups
        self.frank.release(self.user)

        group = ContactGroup.user_groups.get(pk=group.pk)
        self.assertEqual(group.get_member_count(), 0)
        self.assertEqual(set(group.contacts.all()), {test_contact})

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

        self.client.post(reverse('contacts.contactgroup_delete', args=[group.pk]), dict())
        self.assertIsNone(ContactGroup.user_groups.filter(pk=group.pk).first())
        self.assertFalse(ContactGroup.all_groups.get(pk=group.pk).is_active)

        group = self.create_group("one")
        delete_url = reverse('contacts.contactgroup_delete', args=[group.pk])

        trigger = Trigger.objects.create(org=self.org, flow=flow, keyword="join", created_by=self.admin, modified_by=self.admin)
        trigger.groups.add(group)

        second_trigger = Trigger.objects.create(org=self.org, flow=flow, keyword="register", created_by=self.admin, modified_by=self.admin)
        second_trigger.groups.add(group)

        response = self.client.get(delete_url, dict(), HTTP_X_PJAX=True)
        self.assertContains(response, "This group is used by 2 triggers.")

        response = self.client.post(delete_url, dict())
        self.assertEqual(302, response.status_code)
        response = self.client.post(delete_url, dict(), follow=True)
        self.assertTrue(ContactGroup.user_groups.get(pk=group.pk).is_active)
        self.assertEqual(response.request['PATH_INFO'], reverse('contacts.contact_filter', args=[group.uuid]))

        # archive a trigger
        second_trigger.is_archived = True
        second_trigger.save()

        response = self.client.post(delete_url, dict())
        self.assertEqual(302, response.status_code)
        response = self.client.post(delete_url, dict(), follow=True)
        self.assertTrue(ContactGroup.user_groups.get(pk=group.pk).is_active)
        self.assertEqual(response.request['PATH_INFO'], reverse('contacts.contact_filter', args=[group.uuid]))

        trigger.is_archived = True
        trigger.save()

        self.client.post(delete_url, dict())
        # group should have is_active = False and all its triggers
        self.assertIsNone(ContactGroup.user_groups.filter(pk=group.pk).first())
        self.assertFalse(ContactGroup.all_groups.get(pk=group.pk).is_active)
        self.assertFalse(Trigger.objects.get(pk=trigger.pk).is_active)
        self.assertFalse(Trigger.objects.get(pk=second_trigger.pk).is_active)

    def test_delete_fail_with_dependencies(self):
        self.login(self.admin)

        self.get_flow('dependencies')

        from temba.flows.models import Flow
        flow = Flow.objects.filter(name='Dependencies').first()
        cats = ContactGroup.user_groups.filter(name='Cat Facts').first()
        delete_url = reverse('contacts.contactgroup_delete', args=[cats.pk])

        # can't delete if it is a dependency
        response = self.client.post(delete_url, dict())
        self.assertEqual(302, response.status_code)
        self.assertTrue(ContactGroup.user_groups.get(id=cats.id).is_active)

        # get the dependency details
        response = self.client.get(delete_url, dict(), HTTP_X_PJAX=True)
        self.assertEqual(200, response.status_code)
        self.assertContains(response, "Dependencies")

        # remove it from our list of dependencies
        flow.group_dependencies.remove(cats)

        # now it should be gone
        response = self.client.get(delete_url, dict(), HTTP_X_PJAX=True)
        self.assertNotContains(response, "Dependencies")

        response = self.client.post(delete_url, dict(), HTTP_X_PJAX=True)
        self.assertIsNone(ContactGroup.user_groups.filter(id=cats.id).first())


class ContactGroupCRUDLTest(TembaTest):
    def setUp(self):
        super(ContactGroupCRUDLTest, self).setUp()

        self.joe, urn_obj = Contact.get_or_create(self.org, "tel:123", user=self.user, name="Joe Blow")
        self.frank = Contact.get_or_create_by_urns(self.org, self.user, name="Frank Smith", urns=["tel:1234", "twitter:hola"])

        self.joe_and_frank = self.create_group("Customers", [self.joe, self.frank])
        self.dynamic_group = self.create_group("Dynamic", query="tel is 1234")

    @patch.object(ContactGroup, "MAX_ORG_CONTACTGROUPS", new=10)
    def test_create(self):
        url = reverse('contacts.contactgroup_create')

        # can't create group as viewer
        self.login(self.user)
        response = self.client.post(url, dict(name="Spammers"))
        self.assertLoginRedirect(response)

        self.login(self.admin)

        # try to create a contact group whose name is only whitespace
        response = self.client.post(url, dict(name="  "))
        self.assertFormError(response, 'form', 'name', "This field is required.")

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
        self.client.post(url, dict(name="Frank", group_query="tel = 1234"))
        group = ContactGroup.user_groups.get(org=self.org, name="Frank", query="tel = 1234")
        self.assertEqual(set(group.contacts.all()), {self.frank})

        self.create_secondary_org()
        ContactGroup.user_groups.all().delete()

        for i in range(ContactGroup.MAX_ORG_CONTACTGROUPS):
            ContactGroup.create_static(self.org2, self.admin2, 'group%d' % i)

        response = self.client.post(url, dict(name="People"))
        self.assertNoFormErrors(response)
        ContactGroup.user_groups.get(org=self.org, name="People")

        ContactGroup.user_groups.all().delete()

        for i in range(ContactGroup.MAX_ORG_CONTACTGROUPS):
            ContactGroup.create_static(self.org, self.admin, 'group%d' % i)

        self.assertEqual(ContactGroup.user_groups.all().count(), ContactGroup.MAX_ORG_CONTACTGROUPS)
        response = self.client.post(url, dict(name="People"))
        self.assertFormError(response, 'form', 'name', 'This org has 10 groups and the limit is 10. '
                                                       'You must delete existing ones before you can create new ones.')

    def test_update(self):
        url = reverse('contacts.contactgroup_update', args=[self.joe_and_frank.pk])

        # can't create group as viewer
        self.login(self.user)
        response = self.client.post(url, dict(name="Spammers"))
        self.assertLoginRedirect(response)

        self.login(self.admin)

        # try to update name to only whitespace
        response = self.client.post(url, dict(name="   "))
        self.assertFormError(response, 'form', 'name', "This field is required.")

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

        # update both name and query, form should fail, because group can not be saved as a dynamic group
        response = self.client.post(url, dict(name='Frank', query='frank'))
        self.assertFormError(
            response, 'form', 'query',
            'You cannot create a dynamic group based on "name" or "id".'
        )

        # update both name and query, form should fail, because query is not parsable
        response = self.client.post(url, dict(name='Frank', query='(!))!)'))
        self.assertFormError(response, 'form', 'query', 'Search query contains an error at: !')

        response = self.client.post(url, dict(name='Frank', query='id = 123'))
        self.assertFormError(
            response, 'form', 'query',
            'You cannot create a dynamic group based on "name" or "id".'
        )

        response = self.client.post(url, dict(name='Frank', query='twitter is "hola"'))
        self.assertNoFormErrors(response)

        self.dynamic_group.refresh_from_db()
        self.assertEqual(self.dynamic_group.name, "Frank")
        self.assertEqual(self.dynamic_group.query, 'twitter = "hola"')
        self.assertEqual(set(self.dynamic_group.contacts.all()), {self.frank})

        # mark our dynamic group as evaluating
        self.dynamic_group.status = ContactGroup.STATUS_EVALUATING
        self.dynamic_group.save(update_fields=('status',))

        # and check we can't change the query while that is the case
        response = self.client.post(url, dict(name='Frank', query='twitter = "hello"'))
        self.assertFormError(
            response, 'form', 'query',
            'You cannot update the query of a group that is evaluating.'
        )

        # but can change the name
        response = self.client.post(url, dict(name='Frank2', query='twitter is "hola"'))
        self.assertNoFormErrors(response)

        self.dynamic_group.refresh_from_db()
        self.assertEqual(self.dynamic_group.name, "Frank2")

    def test_delete(self):
        url = reverse('contacts.contactgroup_delete', args=[self.joe_and_frank.pk])

        # can't delete group as viewer
        self.login(self.user)
        response = self.client.post(url)
        self.assertLoginRedirect(response)

        # can as admin user
        self.login(self.admin)
        response = self.client.post(url, HTTP_X_PJAX=True)
        self.assertEqual(200, response.status_code)
        self.assertContains(response, '/contact/')

        self.joe_and_frank.refresh_from_db()
        self.assertFalse(self.joe_and_frank.is_active)
        self.assertFalse(self.joe_and_frank.contacts.all())


class ContactTest(TembaTest):
    def setUp(self):
        super(ContactTest, self).setUp()

        self.user1 = self.create_user("nash")

        self.joe = self.create_contact(name="Joe Blow", number="+250781111111", twitter="blow80")
        self.frank = self.create_contact(name="Frank Smith", number="+250782222222")
        self.billy = self.create_contact(name="Billy Nophone")
        self.voldemort = self.create_contact(number="+250768383383")

        # create an orphaned URN
        ContactURN.objects.create(org=self.org, scheme='tel', path='+250788888888', identity='tel:+250788888888', priority=50)

        # create an deleted contact
        self.jim = self.create_contact(name="Jim")
        self.jim.release(self.user)

    def create_campaign(self):
        # create a campaign with a future event and add joe
        self.farmers = self.create_group("Farmers", [self.joe])
        self.reminder_flow = self.get_flow('color')
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

        # can't create without org
        with self.assertRaises(ValueError):
            Contact.get_or_create(None, "tel:+250781111111", self.channel)

        with self.assertRaises(ValueError):
            Contact.get_or_create(self.org, "tel:+250781111111", None)

        contact, urn_obj = Contact.get_or_create(self.org, "tel:+250781111111", self.channel)
        self.assertEqual(contact.pk, self.joe.pk)

        contact, urn_obj = Contact.get_or_create(self.org, "tel:+250781111111", self.channel, name="Kendrick")
        self.assertEqual(contact.name, "Joe Blow")  # should not change the name for existing contact

        contact, urn_obj = Contact.get_or_create(self.org, "tel:124", self.channel, name="Kendrick")
        self.assertEqual(contact.name, "Kendrick")

        contact, urn_obj = Contact.get_or_create(self.org, "tel:+250781111111", None, None, user=self.user)
        self.assertEqual(contact.pk, self.joe.pk)

        urn = ContactURN.get_or_create(self.org, contact, "tel:+250781111111", self.channel)
        urn.contact = None
        urn.save()

        # existing urn without a contact should be used on the new contact
        contact, urn_obj = Contact.get_or_create(self.org, "tel:+250781111111", self.channel, name="Kendrick")
        self.assertEqual(contact.name, "Kendrick")  # should not change the name for existing contact
        self.assertEqual(1, contact.urns.all().count())

    def test_get_or_create_by_urns(self):

        # can't create without org or user
        with self.assertRaises(ValueError):
            Contact.get_or_create_by_urns(None, None, name='Joe', urns=['tel:123'])

        # incoming channel with no urns
        with self.assertRaises(ValueError):
            Contact.get_or_create_by_urns(self.org, self.user, channel=self.channel, name='Joe', urns=None)

        # incoming channel with two urns
        with self.assertRaises(ValueError):
            Contact.get_or_create_by_urns(self.org, self.user, channel=self.channel, name='Joe', urns=['tel:123', 'tel:456'])

        # missing scheme
        with self.assertRaises(ValueError):
            Contact.get_or_create_by_urns(self.org, self.user, name='Joe', urns=[':123'])

        # missing path
        with self.assertRaises(ValueError):
            Contact.get_or_create_by_urns(self.org, self.user, name='Joe', urns=['tel:'])

        # name too long gets truncated
        contact = Contact.get_or_create_by_urns(self.org, self.user, name='Roger ' + 'xxxxx' * 100)
        self.assertEqual(len(contact.name), 128)

        # create a contact with name, phone number and language
        joe = Contact.get_or_create_by_urns(self.org, self.user, name="Joe", urns=['tel:0783835665'], language='fra')
        self.assertEqual(joe.org, self.org)
        self.assertEqual(joe.name, "Joe")
        self.assertEqual(joe.language, 'fra')

        # calling again with same URN updates and returns existing contact
        contact = Contact.get_or_create_by_urns(self.org, self.user, name="Joey", urns=['tel:+250783835665'], language='eng')
        self.assertEqual(contact, joe)
        self.assertEqual(contact.name, "Joey")
        self.assertEqual(contact.language, 'eng')

        # calling again with same URN updates and returns existing contact
        contact = Contact.get_or_create_by_urns(self.org, self.user, name="Joey", urns=['tel:+250783835665'], language='eng', force_urn_update=True)
        self.assertEqual(contact, joe)
        self.assertEqual(contact.name, "Joey")
        self.assertEqual(contact.language, 'eng')

        # create a URN-less contact and try to update them with a taken URN
        snoop = Contact.get_or_create_by_urns(self.org, self.user, name='Snoop')
        with self.assertRaises(ValueError):
            Contact.get_or_create_by_urns(self.org, self.user, uuid=snoop.uuid, urns=['tel:+250781111111'])

        # now give snoop his own urn
        Contact.get_or_create_by_urns(self.org, self.user, uuid=snoop.uuid, urns=['tel:456'])

        self.assertIsNone(snoop.urns.all().first().channel)
        snoop = Contact.get_or_create_by_urns(self.org, self.user, channel=self.channel, urns=['tel:456'], auth='12345')
        self.assertEqual(1, snoop.urns.all().count())
        self.assertEqual(snoop.urns.first().auth, "12345")

        snoop = Contact.get_or_create_by_urns(self.org, self.user, uuid=snoop.uuid, channel=self.channel,
                                              urns=['tel:456'], auth='12345678')
        self.assertEqual(1, snoop.urns.all().count())
        self.assertEqual(snoop.urns.first().auth, "12345678")

        # create contact with new urns one normalized and the other not
        jimmy = Contact.get_or_create_by_urns(self.org, self.user, name="Jimmy", urns=['tel:+250788112233', 'tel:0788112233'])
        self.assertEqual(1, jimmy.urns.all().count())

    def test_get_test_contact(self):
        test_contact_admin = Contact.get_test_contact(self.admin)
        self.assertTrue(test_contact_admin.is_test)
        self.assertEqual(test_contact_admin.created_by, self.admin)

        test_contact_user = Contact.get_test_contact(self.user)
        self.assertTrue(test_contact_user.is_test)
        self.assertEqual(test_contact_user.created_by, self.user)
        self.assertFalse(test_contact_admin == test_contact_user)

        test_contact_user2 = Contact.get_test_contact(self.user)
        self.assertTrue(test_contact_user2.is_test)
        self.assertEqual(test_contact_user2.created_by, self.user)
        self.assertTrue(test_contact_user2 == test_contact_user)

        # assign this URN to another contact
        other_contact = Contact.get_or_create_by_urns(self.org, self.admin)
        test_urn = test_contact_user2.get_urn(TEL_SCHEME)
        test_urn.contact = other_contact
        test_urn.save()

        # fetching the test contact again should get us a new URN
        new_test_contact = Contact.get_test_contact(self.user)
        self.assertNotEqual(new_test_contact.get_urn(TEL_SCHEME), test_urn)

    def test_contact_create(self):
        self.login(self.admin)

        # try creating a contact with a number that belongs to another contact
        response = self.client.post(reverse('contacts.contact_create'), data=dict(name='Ben Haggerty', urn__tel__0="+250781111111"))
        self.assertFormError(response, 'form', 'urn__tel__0', "Used by another contact")

        # now repost with a unique phone number
        response = self.client.post(reverse('contacts.contact_create'), data=dict(name='Ben Haggerty', urn__tel__0="+250 783-835665"))
        self.assertNoFormErrors(response)

        # repost with the phone number of an orphaned URN
        response = self.client.post(reverse('contacts.contact_create'), data=dict(name='Ben Haggerty', urn__tel__0="+250788888888"))
        self.assertNoFormErrors(response)

        # check that the orphaned URN has been associated with the contact
        self.assertEqual('Ben Haggerty', Contact.from_urn(self.org, 'tel:+250788888888').name)

        # check we display error for invalid input
        response = self.client.post(reverse('contacts.contact_create'),
                                    data=dict(name='Ben Haggerty', urn__tel__0="="))
        self.assertFormError(response, 'form', 'urn__tel__0', "Invalid input")

    def test_block_contact_clear_triggers(self):
        flow = self.get_flow('favorites')
        trigger = Trigger.objects.create(org=self.org, flow=flow, keyword="join", created_by=self.admin,
                                         modified_by=self.admin)
        trigger.contacts.add(self.joe)

        trigger2 = Trigger.objects.create(org=self.org, flow=flow, keyword="register", created_by=self.admin,
                                          modified_by=self.admin)
        trigger2.contacts.add(self.joe)
        trigger2.contacts.add(self.frank)
        self.assertEqual(Trigger.objects.filter(is_archived=False).count(), 2)

        self.assertTrue(self.joe.trigger_set.all())

        self.joe.block(self.admin)

        self.assertFalse(self.joe.trigger_set.all())

        self.assertEqual(Trigger.objects.filter(is_archived=True).count(), 1)
        self.assertEqual(Trigger.objects.filter(is_archived=False).count(), 1)

    def test_contact_send_all(self):
        contact = self.create_contact('Stephen', '+12078778899', twitter='stephen')
        Channel.create(self.org, self.user, None, 'TT')

        msgs = contact.send('Allo', self.admin, all_urns=True)
        self.assertEqual(len(msgs), 2)
        out_msgs = Msg.objects.filter(contact=contact, direction='O')
        self.assertEqual(out_msgs.count(), 2)
        self.assertIsNotNone(out_msgs.filter(contact_urn__path='stephen').first())
        self.assertIsNotNone(out_msgs.filter(contact_urn__path='+12078778899').first())

    def test_stop_contact_clear_triggers(self):
        flow = self.get_flow('favorites')
        trigger = Trigger.objects.create(org=self.org, flow=flow, keyword="join", created_by=self.admin,
                                         modified_by=self.admin)
        trigger.contacts.add(self.joe)

        trigger2 = Trigger.objects.create(org=self.org, flow=flow, keyword="register", created_by=self.admin,
                                          modified_by=self.admin)
        trigger2.contacts.add(self.joe)
        trigger2.contacts.add(self.frank)
        self.assertEqual(Trigger.objects.filter(is_archived=False).count(), 2)

        self.assertTrue(self.joe.trigger_set.all())

        self.joe.stop(self.admin)

        self.assertFalse(self.joe.trigger_set.all())
        self.assertEqual(Trigger.objects.filter(is_archived=True).count(), 1)
        self.assertEqual(Trigger.objects.filter(is_archived=False).count(), 1)

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
        self.assertEqual(2, Msg.objects.filter(contact=self.joe, visibility='V').count())
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
        self.assertEqual(0, Msg.objects.filter(contact=self.joe, visibility='V').count())
        self.assertEqual(0, Msg.objects.filter(contact=self.joe).exclude(text="").count())
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

        # create some dynamic groups
        ContactField.get_or_create(self.org, self.admin, 'gender', "Gender")
        ContactField.get_or_create(self.org, self.admin, 'age', "Age", value_type=Value.TYPE_DECIMAL)
        has_twitter = self.create_group("Has twitter", query='twitter != ""')
        no_gender = self.create_group("No gender", query='gender is ""')
        males = self.create_group("Male", query='gender is M or gender is Male')
        youth = self.create_group("Male", query='age > 18 or age < 30')
        joes = self.create_group("Joes", query='twitter = "blow80"')

        self.assertEqual(set(has_twitter.contacts.all()), {self.joe})
        self.assertEqual(set(no_gender.contacts.all()), {self.joe, self.frank, self.billy, self.voldemort})
        self.assertEqual(set(males.contacts.all()), set())
        self.assertEqual(set(youth.contacts.all()), set())
        self.assertEqual(set(joes.contacts.all()), {self.joe})

        self.joe.update_urns(self.admin, ['tel:+250781111111'])
        self.joe.set_field(self.admin, 'gender', "M")
        self.joe.set_field(self.admin, 'age', "28")

        self.assertEqual(set(has_twitter.contacts.all()), set())
        self.assertEqual(set(no_gender.contacts.all()), {self.frank, self.billy, self.voldemort})
        self.assertEqual(set(males.contacts.all()), {self.joe})
        self.assertEqual(set(youth.contacts.all()), {self.joe})

        # add joe's twitter account, dynamic group
        self.joe.update_urns(self.admin, ['twitter:blow80'])

        self.joe.update_static_groups(self.user, [spammers, testers])
        self.assertEqual(set(self.joe.user_groups.all()), {spammers, has_twitter, testers, males, youth, joes})

        self.joe.update_static_groups(self.user, [])
        self.assertEqual(set(self.joe.user_groups.all()), {males, youth, joes, has_twitter})

        self.joe.update_static_groups(self.user, [testers])
        self.assertEqual(set(self.joe.user_groups.all()), {testers, males, youth, joes, has_twitter})

        # blocking removes contact from all groups
        self.joe.block(self.user)
        self.assertEqual(set(self.joe.user_groups.all()), set())

        # can't add blocked contacts to a group
        self.assertRaises(ValueError, self.joe.update_static_groups, self.user, [spammers])

        # unblocking potentially puts contact back in dynamic groups
        self.joe.unblock(self.user)
        self.assertEqual(set(self.joe.user_groups.all()), {males, youth, joes, has_twitter})

        self.joe.update_static_groups(self.user, [testers])

        # stopping removes people from groups
        self.joe.stop(self.admin)
        self.assertEqual(set(self.joe.user_groups.all()), set())

        # and unstopping potentially puts contact back in dynamic groups
        self.joe.unstop(self.admin)
        self.assertEqual(set(self.joe.user_groups.all()), {males, youth, joes, has_twitter})

        self.joe.update_static_groups(self.user, [testers])

        # releasing removes contacts from all groups
        self.joe.release(self.user)
        self.assertEqual(set(self.joe.user_groups.all()), set())

        # can't add deleted contacts to a group
        self.assertRaises(ValueError, self.joe.update_static_groups, self.user, [spammers])

    def test_contact_display(self):
        mr_long_name = self.create_contact(name="Wolfeschlegelsteinhausenbergerdorff", number="8877")

        self.assertEqual("Joe Blow", self.joe.get_display(org=self.org, formatted=False))
        self.assertEqual("Joe Blow", self.joe.get_display(short=True))
        self.assertEqual("Joe Blow", self.joe.get_display())
        self.assertEqual("+250768383383", self.voldemort.get_display(org=self.org, formatted=False))
        self.assertEqual("0768 383 383", self.voldemort.get_display())
        self.assertEqual("Wolfeschlegelsteinhausenbergerdorff", mr_long_name.get_display())
        self.assertEqual("Wolfeschlegelstei...", mr_long_name.get_display(short=True))
        self.assertEqual("Billy Nophone", self.billy.get_display())

        self.assertEqual("0781 111 111", self.joe.get_urn_display(scheme=TEL_SCHEME))
        self.assertEqual("blow80", self.joe.get_urn_display(org=self.org, formatted=False))
        self.assertEqual("blow80", self.joe.get_urn_display())
        self.assertEqual("+250768383383", self.voldemort.get_urn_display(org=self.org, formatted=False))
        self.assertEqual("+250768383383", self.voldemort.get_urn_display(org=self.org, formatted=False, international=True))
        self.assertEqual("+250 768 383 383", self.voldemort.get_urn_display(org=self.org, international=True))
        self.assertEqual("0768 383 383", self.voldemort.get_urn_display())
        self.assertEqual("8877", mr_long_name.get_urn_display())
        self.assertEqual("", self.billy.get_urn_display())

        self.assertEqual("Joe Blow", six.text_type(self.joe))
        self.assertEqual("0768 383 383", six.text_type(self.voldemort))
        self.assertEqual("Wolfeschlegelsteinhausenbergerdorff", six.text_type(mr_long_name))
        self.assertEqual("Billy Nophone", six.text_type(self.billy))

        with AnonymousOrg(self.org):
            self.assertEqual("Joe Blow", self.joe.get_display(org=self.org, formatted=False))
            self.assertEqual("Joe Blow", self.joe.get_display(short=True))
            self.assertEqual("Joe Blow", self.joe.get_display())
            self.assertEqual("%010d" % self.voldemort.pk, self.voldemort.get_display())
            self.assertEqual("Wolfeschlegelsteinhausenbergerdorff", mr_long_name.get_display())
            self.assertEqual("Wolfeschlegelstei...", mr_long_name.get_display(short=True))
            self.assertEqual("Billy Nophone", self.billy.get_display())

            self.assertEqual(ContactURN.ANON_MASK, self.joe.get_urn_display(org=self.org, formatted=False))
            self.assertEqual(ContactURN.ANON_MASK, self.joe.get_urn_display())
            self.assertEqual(ContactURN.ANON_MASK, self.voldemort.get_urn_display())
            self.assertEqual(ContactURN.ANON_MASK, mr_long_name.get_urn_display())
            self.assertEqual('', self.billy.get_urn_display())
            self.assertEqual('', self.billy.get_urn_display(scheme=TEL_SCHEME))

            self.assertEqual("Joe Blow", six.text_type(self.joe))
            self.assertEqual("%010d" % self.voldemort.pk, six.text_type(self.voldemort))
            self.assertEqual("Wolfeschlegelsteinhausenbergerdorff", six.text_type(mr_long_name))
            self.assertEqual("Billy Nophone", six.text_type(self.billy))

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

    def test_contact_search_parsing(self):
        # implicit condition on name
        self.assertEqual(parse_query('will'), ContactQuery(Condition('name', '~', 'will')))
        self.assertEqual(parse_query('1will2'), ContactQuery(Condition('name', '~', '1will2')))

        self.assertEqual(parse_query('will').as_text(), 'name ~ "will"')
        self.assertEqual(parse_query('1will2').as_text(), 'name ~ "1will2"')

        # implicit condition on tel if value is all tel chars
        self.assertEqual(parse_query('1234'), ContactQuery(Condition('tel', '~', '1234')))
        self.assertEqual(parse_query('+12-34'), ContactQuery(Condition('tel', '~', '1234')))
        self.assertEqual(parse_query('1234', as_anon=True), ContactQuery(Condition('id', '=', '1234')))
        self.assertEqual(parse_query('+12-34', as_anon=True), ContactQuery(Condition('name', '~', '+12-34')))
        self.assertEqual(parse_query('bob', as_anon=True), ContactQuery(Condition('name', '~', 'bob')))

        self.assertEqual(parse_query('1234').as_text(), 'tel ~ 1234')
        self.assertEqual(parse_query('+12-34').as_text(), 'tel ~ 1234')

        # boolean combinations of implicit conditions
        self.assertEqual(parse_query('will felix', optimize=False), ContactQuery(
            BoolCombination(BoolCombination.AND, Condition('name', '~', 'will'), Condition('name', '~', 'felix'))
        ))
        self.assertEqual(parse_query('will felix'), ContactQuery(
            SinglePropCombination('name', BoolCombination.AND, Condition('name', '~', 'will'), Condition('name', '~', 'felix'))
        ))
        self.assertEqual(parse_query('will and felix', optimize=False), ContactQuery(
            BoolCombination(BoolCombination.AND, Condition('name', '~', 'will'), Condition('name', '~', 'felix'))
        ))
        self.assertEqual(parse_query('will or felix or matt', optimize=False), ContactQuery(
            BoolCombination(BoolCombination.OR,
                            BoolCombination(BoolCombination.OR,
                                            Condition('name', '~', 'will'),
                                            Condition('name', '~', 'felix')),
                            Condition('name', '~', 'matt'))
        ))

        # property conditions
        self.assertEqual(parse_query('name=will'), ContactQuery(Condition('name', '=', 'will')))
        self.assertEqual(parse_query('name ~ "felix"'), ContactQuery(Condition('name', '~', 'felix')))

        # empty string conditions
        self.assertEqual(parse_query('name is ""'), ContactQuery(IsSetCondition('name', 'is')))
        self.assertEqual(parse_query('name!=""'), ContactQuery(IsSetCondition('name', '!=')))

        # boolean combinations of property conditions
        self.assertEqual(parse_query('name=will or name ~ "felix"', optimize=False), ContactQuery(
            BoolCombination(BoolCombination.OR, Condition('name', '=', 'will'), Condition('name', '~', 'felix'))
        ))
        self.assertEqual(parse_query('name=will or name ~ "felix"'), ContactQuery(
            SinglePropCombination('name', BoolCombination.OR, Condition('name', '=', 'will'),
                                  Condition('name', '~', 'felix'))
        ))

        # mixture of simple and property conditions
        self.assertEqual(parse_query('will or name ~ "felix"'), ContactQuery(
            SinglePropCombination('name', BoolCombination.OR, Condition('name', '~', 'will'),
                                  Condition('name', '~', 'felix'))
        ))

        # optimization will merge conditions combined with the same op
        self.assertEqual(parse_query('will or felix or matt'), ContactQuery(
            SinglePropCombination('name', BoolCombination.OR, Condition('name', '~', 'will'),
                                  Condition('name', '~', 'felix'), Condition('name', '~', 'matt'))
        ))

        # but not conditions combined with different ops
        self.assertEqual(parse_query('will or felix and matt'), ContactQuery(
            BoolCombination(BoolCombination.OR,
                            Condition('name', '~', 'will'),
                            SinglePropCombination('name', BoolCombination.AND,
                                                  Condition('name', '~', 'felix'),
                                                  Condition('name', '~', 'matt')))
        ))

        # optimization respects explicit precedence defined with parentheses
        self.assertEqual(parse_query('(will or felix) and matt'), ContactQuery(
            BoolCombination(BoolCombination.AND,
                            SinglePropCombination('name', BoolCombination.OR,
                                                  Condition('name', '~', 'will'),
                                                  Condition('name', '~', 'felix')),
                            Condition('name', '~', 'matt'))
        ))

        # implicit ANDing of conditions
        query = parse_query('will felix name ~ "matt"')
        self.assertEqual(query, ContactQuery(
            SinglePropCombination('name', BoolCombination.AND,
                                  Condition('name', '~', 'will'),
                                  Condition('name', '~', 'felix'),
                                  Condition('name', '~', 'matt'))
        ))
        self.assertEqual(query.as_text(), 'name ~ "will" AND name ~ "felix" AND name ~ "matt"')

        self.assertEqual(parse_query('will felix name ~ "matt"', optimize=False), ContactQuery(
            BoolCombination(BoolCombination.AND,
                            BoolCombination(BoolCombination.AND,
                                            Condition('name', '~', 'will'),
                                            Condition('name', '~', 'felix')),
                            Condition('name', '~', 'matt'))
        ))

        # boolean operator precedence is AND before OR, even when AND is implicit
        self.assertEqual(parse_query('will and felix or matt amber', optimize=False), ContactQuery(
            BoolCombination(BoolCombination.OR,
                            BoolCombination(BoolCombination.AND,
                                            Condition('name', '~', 'will'),
                                            Condition('name', '~', 'felix')),
                            BoolCombination(BoolCombination.AND,
                                            Condition('name', '~', 'matt'),
                                            Condition('name', '~', 'amber')))
        ))

        # boolean combinations can themselves be combined
        query = parse_query('(Age < 18 and Gender = "male") or (Age > 18 and Gender = "female")')
        self.assertEqual(query, ContactQuery(
            BoolCombination(BoolCombination.OR,
                            BoolCombination(BoolCombination.AND,
                                            Condition('age', '<', '18'),
                                            Condition('gender', '=', 'male')),
                            BoolCombination(BoolCombination.AND,
                                            Condition('age', '>', '18'),
                                            Condition('gender', '=', 'female')))
        ))
        self.assertEqual(query.as_text(), '(age < 18 AND gender = "male") OR (age > 18 AND gender = "female")')

        self.assertEqual(str(parse_query('Age < 18 and Gender = "male"')), "AND(age<18, gender=male)")
        self.assertEqual(str(parse_query('Age > 18 and Age < 30')), "AND[age](>18, <30)")

        # query with UTF-8 characters (non-ascii)
        query = parse_query('district="Kayônza"')
        self.assertEqual(query.as_text(), 'district = "Kayônza"')

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

        names = ['Trey', 'Mike', 'Paige', 'Fish', "", None]
        districts = ['Gatsibo', 'Kayônza', 'Rwamagana']
        wards = ['Kageyo', 'Kabara', 'Bukure']
        date_format = get_datetime_format(True)[0]

        # create some contacts
        for i in range(90):
            name = names[i % len(names)]
            number = "0788382%s" % str(i).zfill(3)
            twitter = ("tweep_%d" % (i + 1)) if (i % 3 == 0) else None  # 1 in 3 have twitter URN
            contact = self.create_contact(name=name, number=number, twitter=twitter)
            join_date = datetime_to_str(date(2014, 1, 1) + timezone.timedelta(days=i), date_format)

            # some field data so we can do some querying
            contact.set_field(self.user, 'age', str(i + 10))
            contact.set_field(self.user, 'join_date', str(join_date))
            contact.set_field(self.user, 'state', "Eastern Province")
            contact.set_field(self.user, 'home', districts[i % len(districts)])
            contact.set_field(self.user, 'ward', wards[i % len(wards)])

            if i % 3 == 0:
                contact.set_field(self.user, 'profession', "Farmer")  # only some contacts have any value for this

            contact.set_field(self.user, 'isureporter', 'yes')
            contact.set_field(self.user, 'hasbirth', 'no')

        def q(query):
            qs, _ = Contact.search(self.org, query)
            return qs.count()

        # implicit property queries (name or URN path)
        self.assertEqual(q('trey'), 15)
        self.assertEqual(q('MIKE'), 15)
        self.assertEqual(q('  paige  '), 15)
        self.assertEqual(q('0788382011'), 1)
        self.assertEqual(q('trey 0788382'), 15)

        # name as property
        self.assertEqual(q('name is "trey"'), 15)
        self.assertEqual(q('name is mike'), 15)
        self.assertEqual(q('name = paige'), 15)
        self.assertEqual(q('name is ""'), 30)  # includes null and blank names
        self.assertEqual(q('NAME=""'), 30)
        self.assertEqual(q('name has e'), 45)

        # URN as property
        self.assertEqual(q('tel is +250788382011'), 1)
        self.assertEqual(q('tel has 0788382011'), 1)
        self.assertEqual(q('twitter = tweep_13'), 1)
        self.assertEqual(q('twitter = ""'), 60)
        self.assertEqual(q('twitter != ""'), 30)
        self.assertEqual(q('TWITTER has tweep'), 30)

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

        self.assertEqual(q('state is "Eastern Province"'), 90)
        self.assertEqual(q('HOME is Kayônza'), 30)  # value with non-ascii character
        self.assertEqual(q('ward is kageyo'), 30)
        self.assertEqual(q('home has ga'), 60)

        self.assertEqual(q('home is ""'), 0)
        self.assertEqual(q('profession = ""'), 60)
        self.assertEqual(q('profession is ""'), 60)
        self.assertEqual(q('profession != ""'), 30)

        # contact fields beginning with 'is' or 'has'
        self.assertEqual(q('isureporter = "yes"'), 90)
        self.assertEqual(q('isureporter = yes'), 90)
        self.assertEqual(q('isureporter = no'), 0)

        self.assertEqual(q('hasbirth = "no"'), 90)
        self.assertEqual(q('hasbirth = no'), 90)
        self.assertEqual(q('hasbirth = yes'), 0)

        # boolean combinations
        self.assertEqual(q('name is trey or name is mike'), 30)
        self.assertEqual(q('name is trey and age < 20'), 2)
        self.assertEqual(q('(home is gatsibo or home is "Rwamagana")'), 60)
        self.assertEqual(q('(home is gatsibo or home is "Rwamagana") and name is trey'), 15)
        self.assertEqual(q('name is MIKE and profession = ""'), 15)
        self.assertEqual(q('profession = doctor or profession = farmer'), 30)  # same field
        self.assertEqual(q('age = 20 or age = 21'), 2)
        self.assertEqual(q('join_date = 30/1/2014 or join_date = 31/1/2014'), 2)

        # create contact with no phone number, we'll try searching for it by id
        contact = self.create_contact(name="Id Contact")

        # non-anon orgs can't search by id (because they never see ids)
        self.assertFalse(contact in Contact.search(self.org, '%d' % contact.pk))  # others may match by id on tel

        with AnonymousOrg(self.org):
            # still allow name and field searches
            self.assertEqual(q('trey'), 15)
            self.assertEqual(q('name is mike'), 15)
            self.assertEqual(q('age > 30'), 69)

            # don't allow matching on URNs
            self.assertEqual(q('0788382011'), 0)
            self.assertEqual(q('tel is +250788382011'), 0)
            self.assertEqual(q('twitter has blow'), 0)
            self.assertEqual(q('twitter = ""'), 0)

            # anon orgs can search by id, with or without zero padding
            self.assertTrue(contact in Contact.search(self.org, '%d' % contact.pk)[0])
            self.assertTrue(contact in Contact.search(self.org, '%010d' % contact.pk)[0])

        # invalid queries
        self.assertRaises(SearchException, q, '((')
        self.assertRaises(SearchException, q, 'name = "trey')  # unterminated string literal
        self.assertRaises(SearchException, q, 'name > trey')  # unrecognized non-field operator
        self.assertRaises(SearchException, q, 'profession > trey')  # unrecognized text-field operator
        self.assertRaises(SearchException, q, 'age has 4')  # unrecognized decimal-field operator
        self.assertRaises(SearchException, q, 'age = x')  # unparseable decimal-field comparison
        self.assertRaises(SearchException, q, 'join_date has 30/1/2014')  # unrecognized date-field operator
        self.assertRaises(SearchException, q, 'join_date > xxxxx')  # unparseable date-field comparison
        self.assertRaises(SearchException, q, 'home > kigali')  # unrecognized location-field operator
        self.assertRaises(SearchException, q, 'credits > 10')  # non-existent field or attribute
        self.assertRaises(SearchException, q, 'tel < +250788382011')  # unsupported comparator for a URN
        self.assertRaises(SearchException, q, 'tel < ""')  # unsupported comparator for an empty string
        self.assertRaises(SearchException, q, 'data=“not empty”')  # unicode “,” are not accepted characters

    def test_contact_search_is_set(self):
        ContactField.get_or_create(self.org, self.admin, 'age', "Age", value_type='N')
        self.joe.set_field(self.admin, 'age', "X")  # creates a value with string_value=X decimal_value=None

        self.assertIn(self.joe, Contact.search(self.org, 'age = ""')[0])
        self.assertNotIn(self.joe, Contact.search(self.org, 'age != ""')[0])

    def test_contact_create_with_dynamicgroup_reevaluation(self):

        ContactField.get_or_create(self.org, self.admin, 'age', label='Age', value_type=Value.TYPE_DECIMAL)
        ContactField.get_or_create(self.org, self.admin, 'gender', label='Gender', value_type=Value.TYPE_TEXT)

        ContactGroup.create_dynamic(
            self.org, self.admin, 'simple group',
            '(Age < 18 and gender = "male") or (Age > 18 and gender = "female")'
        )
        ContactGroup.create_dynamic(self.org, self.admin, 'cannon fodder', 'age > 18 and gender = "male"')
        ContactGroup.create_dynamic(self.org, self.admin, 'Empty age field', 'age = ""')
        ContactGroup.create_dynamic(self.org, self.admin, 'Age field is set', 'age != ""')
        ContactGroup.create_dynamic(self.org, self.admin, 'urn group', 'twitter = "helio"')

        # when creating a new contact we should only reevaluate 'empty age field' and 'urn group' groups
        with self.assertNumQueries(38):
            contact = Contact.get_or_create_by_urns(self.org, self.admin, name='Željko', urns=['twitter:helio'])

        six.assertCountEqual(
            self,
            [group.name for group in contact.user_groups.filter(is_active=True).all()], ['Empty age field', 'urn group']
        )

        # field update works as expected
        contact.set_field(self.user, 'gender', 'male')
        contact.set_field(self.user, 'age', 20)

        six.assertCountEqual(
            self,
            [group.name for group in contact.user_groups.filter(is_active=True).all()],
            ['cannon fodder', 'urn group', 'Age field is set']
        )

    def test_omnibox(self):
        # add a group with members and an empty group
        self.create_field('gender', "Gender")
        joe_and_frank = self.create_group("Joe and Frank", [self.joe, self.frank])
        men = self.create_group("Men", query="gender=M")
        nobody = self.create_group("Nobody", [])

        # a group which is being re-evaluated and shouldn't appear in any omnibox results
        unready = self.create_group("Group being re-evaluated...", query="gender=M")
        unready.status = ContactGroup.STATUS_EVALUATING
        unready.save(update_fields=('status',))

        joe_tel = self.joe.get_urn(TEL_SCHEME)
        joe_twitter = self.joe.get_urn(TWITTER_SCHEME)
        frank_tel = self.frank.get_urn(TEL_SCHEME)
        voldemort_tel = self.voldemort.get_urn(TEL_SCHEME)

        # Postgres will defer to strcoll for ordering which even for en_US.UTF-8 will return different results on OSX
        # and Ubuntu. To keep ordering consistent for this test, we don't let URNs start with +
        # (see http://postgresql.nabble.com/a-strange-order-by-behavior-td4513038.html)
        ContactURN.objects.filter(path__startswith="+").update(path=Substr('path', 2), identity=Concat(DbValue('tel:'), Substr('path', 2)))

        self.admin.set_org(self.org)
        self.login(self.admin)

        def omnibox_request(query):
            response = self.client.get("%s?%s" % (reverse("contacts.contact_omnibox"), query))
            return response.json()['results']

        self.assertEqual(omnibox_request(""), [
            # all 3 groups A-Z
            dict(id='g-%s' % joe_and_frank.uuid, text="Joe and Frank", extra=2),
            dict(id='g-%s' % men.uuid, text="Men", extra=0),
            dict(id='g-%s' % nobody.uuid, text="Nobody", extra=0),

            # all 4 contacts A-Z
            dict(id='c-%s' % self.billy.uuid, text="Billy Nophone"),
            dict(id='c-%s' % self.frank.uuid, text="Frank Smith"),
            dict(id='c-%s' % self.joe.uuid, text="Joe Blow"),
            dict(id='c-%s' % self.voldemort.uuid, text="250768383383"),

            # 3 sendable URNs with names as extra
            dict(id='u-%d' % voldemort_tel.pk, text="250768383383", extra=None, scheme='tel'),
            dict(id='u-%d' % joe_tel.pk, text="250781111111", extra="Joe Blow", scheme='tel'),
            dict(id='u-%d' % frank_tel.pk, text="250782222222", extra="Frank Smith", scheme='tel'),
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
            dict(id='c-%s' % self.voldemort.uuid, text="250768383383"),
            dict(id='u-%d' % voldemort_tel.pk, text="250768383383", extra=None, scheme='tel'),
            dict(id='u-%d' % joe_tel.pk, text="250781111111", extra="Joe Blow", scheme='tel'),
            dict(id='u-%d' % frank_tel.pk, text="250782222222", extra="Frank Smith", scheme='tel')
        ])

        # search for Frank by phone
        self.assertEqual(omnibox_request("search=222"), [
            dict(id='u-%d' % frank_tel.pk, text="250782222222", extra="Frank Smith", scheme='tel')
        ])

        # search for Joe by twitter - won't return anything because there is no twitter channel
        self.assertEqual(omnibox_request("search=blow80"), [])

        # create twitter channel
        Channel.create(self.org, self.user, None, 'TT')

        # add add an external channel so numbers get normalized
        Channel.create(self.org, self.user, 'RW', 'EX', schemes=[TEL_SCHEME])

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
            dict(id='u-%d' % frank_tel.pk, text="0782 222 222", extra="Frank Smith", scheme='tel'),
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
            self.assertEqual(omnibox_request("search=222"), [])

        # exclude blocked and stopped contacts
        self.joe.block(self.admin)
        self.frank.stop(self.admin)

        # lookup by contact uuids
        self.assertEqual(omnibox_request("c=%s,%s" % (self.joe.uuid, self.frank.uuid)), [])

        # but still lookup by URN ids
        urn_query = "u=%d,%d" % (self.joe.get_urn(TWITTER_SCHEME).pk, self.frank.get_urn(TEL_SCHEME).pk)
        self.assertEqual(omnibox_request(urn_query), [
            dict(id='u-%d' % frank_tel.pk, text="0782 222 222", extra="Frank Smith", scheme='tel'),
            dict(id='u-%d' % joe_twitter.pk, text="blow80", extra="Joe Blow", scheme='twitter')
        ])

    def test_history(self):

        # use a max history size of 100
        with patch('temba.contacts.models.MAX_HISTORY', 100):
            url = reverse('contacts.contact_history', args=[self.joe.uuid])

            kurt = self.create_contact("Kurt", "123123")
            self.joe.created_on = timezone.now() - timedelta(days=1000)
            self.joe.save()

            self.create_campaign()

            # add a message with some attachments
            self.create_msg(direction='I', contact=self.joe, text="Message caption", created_on=timezone.now(),
                            attachments=[
                                "audio/mp3:http://blah/file.mp3",
                                "video/mp4:http://blah/file.mp4",
                                "geo:47.5414799,-122.6359908"])

            # create some messages
            for i in range(99):
                self.create_msg(direction='I', contact=self.joe, text="Inbound message %d" % i,
                                created_on=timezone.now() - timedelta(days=(100 - i)))

            # because messages are stored with timestamps from external systems, possible to have initial message
            # which is little bit older than the contact itself
            self.create_msg(direction='I', contact=self.joe, text="Very old inbound message",
                            created_on=self.joe.created_on - timedelta(seconds=10))

            # start a joe flow
            self.reminder_flow.start([], [self.joe, kurt])

            # mark an outgoing message as failed
            failed = Msg.objects.get(direction='O', contact=self.joe)
            failed.status = 'F'
            failed.save()
            log = ChannelLog.objects.create(channel=failed.channel, msg=failed, is_error=True,
                                            description="It didn't send!!")

            # pretend that flow run made a webhook request
            WebHookEvent.trigger_flow_webhook(FlowRun.objects.get(contact=self.joe), 'https://example.com', '1234', msg=None)

            # create an event from the past
            scheduled = timezone.now() - timedelta(days=5)
            EventFire.objects.create(event=self.planting_reminder, contact=self.joe, scheduled=scheduled, fired=scheduled)

            # create a missed call
            ChannelEvent.create(self.channel, six.text_type(self.joe.get_urn(TEL_SCHEME)), ChannelEvent.TYPE_CALL_OUT_MISSED,
                                timezone.now(), {})

            # try adding some failed calls
            IVRCall.objects.create(contact=self.joe, status=IVRCall.NO_ANSWER, created_by=self.admin,
                                   modified_by=self.admin, channel=self.channel, org=self.org,
                                   contact_urn=self.joe.urns.all().first())

            # fetch our contact history
            with self.assertNumQueries(67):
                response = self.fetch_protected(url, self.admin)

            # activity should include all messages in the last 90 days, the channel event, the call, and the flow run
            activity = response.context['activity']
            self.assertEqual(len(activity), 95)
            self.assertIsInstance(activity[0]['obj'], IVRCall)
            self.assertIsInstance(activity[1]['obj'], ChannelEvent)
            self.assertIsInstance(activity[2]['obj'], WebHookResult)
            self.assertIsInstance(activity[3]['obj'], Msg)
            self.assertEqual(activity[3]['obj'].direction, 'O')
            self.assertIsInstance(activity[4]['obj'], FlowRun)
            self.assertIsInstance(activity[5]['obj'], Msg)
            self.assertIsInstance(activity[6]['obj'], Msg)
            self.assertEqual(activity[6]['obj'].text, "Inbound message 98")
            self.assertIsInstance(activity[9]['obj'], EventFire)
            self.assertEqual(activity[-1]['obj'].text, "Inbound message 11")

            self.assertContains(response, '<audio ')
            self.assertContains(response, '<source type="audio/mp3" src="http://blah/file.mp3" />')
            self.assertContains(response, '<video ')
            self.assertContains(response, '<source type="video/mp4" src="http://blah/file.mp4" />')
            self.assertContains(response, 'http://www.openstreetmap.org/?mlat=47.5414799&amp;mlon=-122.6359908#map=18/47.5414799/-122.6359908')
            self.assertContains(response, '/channels/channellog/read/%d/' % log.id)

            # fetch next page
            before = datetime_to_ms(timezone.now() - timedelta(days=90))
            response = self.fetch_protected(url + '?before=%d' % before, self.admin)
            self.assertFalse(response.context['has_older'])

            # none of our messages have a failed status yet
            self.assertNotContains(response, 'icon-bubble-notification')

            # activity should include 11 remaining messages and the event fire
            activity = response.context['activity']
            self.assertEqual(len(activity), 12)
            self.assertEqual(activity[0]['obj'].text, "Inbound message 10")
            self.assertEqual(activity[10]['obj'].text, "Inbound message 0")
            self.assertEqual(activity[11]['obj'].text, "Very old inbound message")

            # if a broadcast is purged, it appears in place of the message
            bcast = Broadcast.objects.get()
            bcast.purged = True
            bcast.save()
            bcast.msgs.all().delete()

            recipient = BroadcastRecipient.objects.filter(contact=self.joe, broadcast=bcast).first()
            recipient.purged_status = 'F'
            recipient.save()

            response = self.fetch_protected(url, self.admin)
            activity = response.context['activity']

            # our broadcast recipient purged_status is failed
            self.assertContains(response, 'icon-bubble-notification')

            self.assertEqual(len(activity), 95)
            self.assertIsInstance(activity[4]['obj'], Broadcast)  # TODO fix order so initial broadcasts come after their run
            self.assertEqual(activity[4]['obj'].text, {
                'base': "What is your favorite color?", 'fra': "Quelle est votre couleur préférée?"
            })
            self.assertEqual(activity[4]['obj'].translated_text, "What is your favorite color?")

            # if a new message comes in
            self.create_msg(direction='I', contact=self.joe, text="Newer message")
            response = self.fetch_protected(url, self.admin)

            # now we'll see the message that just came in first, followed by the call event
            activity = response.context['activity']
            self.assertIsInstance(activity[0]['obj'], Msg)
            self.assertEqual(activity[0]['obj'].text, "Newer message")
            self.assertIsInstance(activity[1]['obj'], IVRCall)

            recent_start = datetime_to_ms(timezone.now() - timedelta(days=1))
            response = self.fetch_protected(url + "?after=%s" % recent_start, self.admin)

            # with our recent flag on, should not see the older messages
            activity = response.context['activity']
            self.assertEqual(len(activity), 7)
            self.assertContains(response, 'file.mp4')

            # can't view history of contact in another org
            self.create_secondary_org()
            hans = self.create_contact("Hans", twitter="hans", org=self.org2)
            response = self.client.get(reverse('contacts.contact_history', args=[hans.uuid]))
            self.assertLoginRedirect(response)

            # invalid UUID should return 404
            response = self.client.get(reverse('contacts.contact_history', args=['bad-uuid']))
            self.assertEqual(response.status_code, 404)

            # super users can view history of any contact
            response = self.fetch_protected(reverse('contacts.contact_history', args=[self.joe.uuid]), self.superuser)
            self.assertEqual(len(response.context['activity']), 96)
            response = self.fetch_protected(reverse('contacts.contact_history', args=[hans.uuid]), self.superuser)
            self.assertEqual(len(response.context['activity']), 0)

            # exit flow runs
            FlowRun.bulk_exit(self.joe.runs.all(), FlowRun.EXIT_TYPE_COMPLETED)

            # add a new run
            self.reminder_flow.start([], [self.joe], restart_participants=True)
            response = self.fetch_protected(reverse('contacts.contact_history', args=[self.joe.uuid]), self.admin)
            activity = response.context['activity']
            self.assertEqual(len(activity), 99)

            # before date should not match our last activity, that only happens when we truncate
            self.assertNotEqual(response.context['before'], datetime_to_ms(response.context['activity'][-1]['time']))

            self.assertIsInstance(activity[0]['obj'], Msg)
            self.assertEqual(activity[0]['obj'].direction, 'O')
            self.assertEqual(activity[1]['type'], 'run-start')
            self.assertIsInstance(activity[1]['obj'], FlowRun)
            self.assertEqual(activity[1]['obj'].exit_type, None)
            self.assertEqual(activity[2]['type'], 'run-exit')
            self.assertIsInstance(activity[2]['obj'], FlowRun)
            self.assertEqual(activity[2]['obj'].exit_type, FlowRun.EXIT_TYPE_COMPLETED)
            self.assertIsInstance(activity[3]['obj'], Msg)
            self.assertEqual(activity[3]['obj'].direction, 'I')
            self.assertIsInstance(activity[4]['obj'], IVRCall)
            self.assertIsInstance(activity[5]['obj'], ChannelEvent)
            self.assertIsInstance(activity[6]['obj'], WebHookResult)
            self.assertIsInstance(activity[7]['obj'], FlowRun)

        # with a max history of one, we should see this event first
        with patch('temba.contacts.models.MAX_HISTORY', 1):
            # make our message event older than our planting reminder
            self.message_event.created_on = self.planting_reminder.created_on - timedelta(days=1)
            self.message_event.save()

            # but fire it immediately
            scheduled = timezone.now()
            EventFire.objects.create(event=self.message_event, contact=self.joe, scheduled=scheduled, fired=scheduled)

            # when fetched in a bit, it should be the first event we see
            response = self.fetch_protected(reverse('contacts.contact_history', args=[self.joe.uuid]) + '?before=%d' % datetime_to_ms(scheduled + timedelta(minutes=5)), self.admin)
            self.assertEqual(self.message_event, response.context['activity'][0]['obj'].event)

        # now try the proper max history to test truncation
        response = self.fetch_protected(reverse('contacts.contact_history', args=[self.joe.uuid]) + '?before=%d' % datetime_to_ms(timezone.now()), self.admin)

        # our before should be the same as the last item
        last_item_date = datetime_to_ms(response.context['activity'][-1]['time'])
        self.assertEqual(response.context['before'], last_item_date)

        # and our after should be 90 days earlier
        self.assertEqual(response.context['after'], last_item_date - (90 * 24 * 60 * 60 * 1000))
        self.assertEqual(len(response.context['activity']), 50)

        # and we should have a marker for older items
        self.assertTrue(response.context['has_older'])

    def test_event_times(self):

        self.create_campaign()

        from temba.campaigns.models import CampaignEvent
        from temba.contacts.templatetags.contacts import event_time

        event = CampaignEvent.create_message_event(self.org, self.admin, self.campaign,
                                                   relative_to=self.planting_date, offset=7, unit='D',
                                                   message='A message to send')

        event.unit = 'D'
        self.assertEqual("7 days after Planting Date", event_time(event))

        event.unit = 'M'
        self.assertEqual("7 minutes after Planting Date", event_time(event))

        event.unit = 'H'
        self.assertEqual("7 hours after Planting Date", event_time(event))

        event.offset = -1
        self.assertEqual("1 hour before Planting Date", event_time(event))

        event.unit = 'D'
        self.assertEqual("1 day before Planting Date", event_time(event))

        event.unit = 'M'
        self.assertEqual("1 minute before Planting Date", event_time(event))

    def test_activity_tags(self):
        self.create_campaign()

        contact = self.create_contact('Joe Blow', 'tel:+1234')
        msg = Msg.create_incoming(self.channel, 'tel:+1234', "Inbound message")

        self.reminder_flow.start([], [self.joe])

        # pretend that flow run made a webhook request
        WebHookEvent.trigger_flow_webhook(FlowRun.objects.get(), 'https://example.com', '1234', msg=None)
        result = WebHookResult.objects.get()

        item = {'type': 'webhook-result', 'obj': result}
        self.assertEqual(history_class(item), 'non-msg')

        result.status_code = 404
        self.assertEqual(history_class(item), 'non-msg warning')

        call = IVRCall.create_incoming(self.channel, contact, contact.urns.all().first(),
                                       self.admin, self.admin)

        item = {'type': 'call', 'obj': call}
        self.assertEqual(history_class(item), 'non-msg')

        call.status = IVRCall.FAILED
        self.assertEqual(history_class(item), 'non-msg warning')

        # inbound
        item = {'type': 'msg', 'obj': msg}
        self.assertEqual(activity_icon(item), '<span class="glyph icon-bubble-user"></span>')

        # outgoing sent
        msg.direction = 'O'
        msg.status = 'S'
        self.assertEqual(activity_icon(item), '<span class="glyph icon-bubble-right"></span>')

        # outgoing delivered
        msg.status = 'D'
        self.assertEqual(activity_icon(item), '<span class="glyph icon-bubble-check"></span>')

        # failed
        msg.status = 'F'
        self.assertEqual(activity_icon(item), '<span class="glyph icon-bubble-notification"></span>')
        self.assertEqual(history_class(item), 'msg warning')

        # outgoing voice
        msg.msg_type = 'V'
        self.assertEqual(activity_icon(item), '<span class="glyph icon-call-outgoing"></span>')
        self.assertEqual(history_class(item), 'msg warning')

        # incoming voice
        msg.direction = 'I'
        self.assertEqual(activity_icon(item), '<span class="glyph icon-call-incoming"></span>')
        self.assertEqual(history_class(item), 'msg warning')

        # simulate a broadcast to 5 people
        msg.broadcast = Broadcast.create(self.org, self.admin, 'Test message', [])
        msg.broadcast.recipient_count = 5
        msg.status = 'F'
        self.assertEqual(activity_icon(item), '<span class="glyph icon-bubble-notification"></span>')

        msg.status = 'S'
        self.assertEqual(activity_icon(item), '<span class="glyph icon-bullhorn"></span>')

        flow = self.create_flow()
        flow.start([], [self.joe])
        run = FlowRun.objects.last()

        item = {'type': 'run-start', 'obj': run}
        self.assertEqual(activity_icon(item), '<span class="glyph icon-tree-2"></span>')

        run.run_event_type = 'Invalid'
        self.assertEqual(activity_icon(item), '<span class="glyph icon-tree-2"></span>')

        item = {'type': 'run-exit', 'obj': run}

        run.exit_type = FlowRun.EXIT_TYPE_COMPLETED
        self.assertEqual(activity_icon(item), '<span class="glyph icon-checkmark"></span>')

        run.exit_type = FlowRun.EXIT_TYPE_INTERRUPTED
        self.assertEqual(activity_icon(item), '<span class="glyph icon-warning"></span>')

        run.exit_type = FlowRun.EXIT_TYPE_EXPIRED
        self.assertEqual(activity_icon(item), '<span class="glyph icon-clock"></span>')

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
        self.assertEqual(self.joe, response.context['object'])
        self.assertContains(response, 'Add Connection')

        # no field to add new urns for anon org
        with AnonymousOrg(self.org):
            response = self.fetch_protected(update_url, self.admin)
            self.assertEqual(self.joe, response.context['object'])
            self.assertNotContains(response, 'Add Connection')

    def test_read(self):
        read_url = reverse('contacts.contact_read', args=[self.joe.uuid])

        for i in range(5):
            self.create_msg(direction='I', contact=self.joe, text="some msg no %d 2 send in sms language if u wish" % i)
            i += 1

        self.create_campaign()

        # create more events
        for i in range(5):
            msg = "Sent %d days after planting date" % (i + 10)
            self.message_event = CampaignEvent.create_message_event(self.org, self.admin, self.campaign,
                                                                    relative_to=self.planting_date,
                                                                    offset=i + 10, unit='D', message=msg)

        now = timezone.now()
        self.joe.set_field(self.user, 'planting_date', (now + timedelta(days=1)).isoformat())
        EventFire.update_campaign_events(self.campaign)

        # should have seven fires, one for each campaign event
        self.assertEqual(7, EventFire.objects.all().count())

        # visit a contact detail page as a user but not belonging to this organization
        self.login(self.user1)
        response = self.client.get(read_url)
        self.assertEqual(302, response.status_code)

        # visit a contact detail page as a manager but not belonging to this organisation
        self.login(self.non_org_user)
        response = self.client.get(read_url)
        self.assertEqual(302, response.status_code)

        # visit a contact detail page as a manager within the organization
        response = self.fetch_protected(read_url, self.admin)
        self.assertEqual(self.joe, response.context['object'])

        with patch('temba.orgs.models.Org.get_schemes') as mock_get_schemes:
            mock_get_schemes.return_value = []

            response = self.fetch_protected(read_url, self.admin)
            self.assertEqual(self.joe, response.context['object'])
            self.assertFalse(response.context['has_sendable_urn'])

            mock_get_schemes.return_value = ['tel']

            response = self.fetch_protected(read_url, self.admin)
            self.assertEqual(self.joe, response.context['object'])
            self.assertTrue(response.context['has_sendable_urn'])

        response = self.fetch_protected(read_url, self.admin)
        self.assertEqual(self.joe, response.context['object'])
        self.assertTrue(response.context['has_sendable_urn'])
        upcoming = response.context['upcoming_events']

        # should show the next seven events to fire in reverse order
        self.assertEqual(7, len(upcoming))

        self.assertEqual(upcoming[4]['message'], "Sent 10 days after planting date")
        self.assertEqual(upcoming[5]['message'], "Sent 7 days after planting date")
        self.assertEqual(upcoming[6]['message'], None)
        self.assertEqual(upcoming[6]['flow_uuid'], self.reminder_flow.uuid)
        self.assertEqual(upcoming[6]['flow_name'], self.reminder_flow.name)

        self.assertGreater(upcoming[4]['scheduled'], upcoming[5]['scheduled'])

        # add a scheduled broadcast
        broadcast = Broadcast.create(self.org, self.admin, "Hello", [])
        broadcast.contacts.add(self.joe)
        schedule_time = now + timedelta(days=5)
        broadcast.schedule = Schedule.create_schedule(schedule_time, 'O', self.admin)
        broadcast.save()

        response = self.fetch_protected(read_url, self.admin)
        self.assertEqual(self.joe, response.context['object'])
        upcoming = response.context['upcoming_events']

        # should show the next 2 events to fire and the scheduled broadcast in reverse order by schedule time
        self.assertEqual(8, len(upcoming))

        self.assertEqual(upcoming[5]['message'], "Sent 7 days after planting date")
        self.assertEqual(upcoming[6]['message'], "Hello")
        self.assertEqual(upcoming[7]['message'], None)
        self.assertEqual(upcoming[7]['flow_uuid'], self.reminder_flow.uuid)
        self.assertEqual(upcoming[7]['flow_name'], self.reminder_flow.name)

        self.assertGreater(upcoming[6]['scheduled'], upcoming[7]['scheduled'])

        contact_no_name = self.create_contact(name=None, number="678")
        read_url = reverse('contacts.contact_read', args=[contact_no_name.uuid])
        response = self.fetch_protected(read_url, self.superuser)
        self.assertEqual(contact_no_name, response.context['object'])
        self.client.logout()

        # login as a manager from out of this organization
        self.login(self.non_org_user)

        # create kLab group, and add joe to the group
        klab = self.create_group("kLab", [self.joe])

        # post to read url, joe's contact and kLab group
        post_data = dict(contact=self.joe.id, group=klab.id)
        response = self.client.post(read_url, post_data, follow=True)

        # this manager cannot operate on this organization
        self.assertEqual(len(self.joe.user_groups.all()), 2)
        self.client.logout()

        # login as a manager of kLab
        self.login(self.admin)

        # remove this contact form kLab group
        response = self.client.post(read_url, post_data, follow=True)
        self.assertEqual(1, self.joe.user_groups.count())

        # try removing it again, should fail
        response = self.client.post(read_url, post_data, follow=True)
        self.assertEqual(200, response.status_code)

        # can't view contact in another org
        self.create_secondary_org()
        hans = self.create_contact("Hans", twitter="hans", org=self.org2)
        response = self.client.get(reverse('contacts.contact_read', args=[hans.uuid]))
        self.assertLoginRedirect(response)

        # invalid UUID should return 404
        response = self.client.get(reverse('contacts.contact_read', args=['bad-uuid']))
        self.assertEqual(response.status_code, 404)

        # super users can view history of any contact
        response = self.fetch_protected(reverse('contacts.contact_read', args=[self.joe.uuid]), self.superuser)
        self.assertEqual(response.status_code, 200)
        response = self.fetch_protected(reverse('contacts.contact_read', args=[hans.uuid]), self.superuser)
        self.assertEqual(response.status_code, 200)

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

        self.assertEqual(self.joe.groups_as_text(), "Joe and Frank, Just Joe")
        group_analytic_json = self.joe_and_frank.analytics_json()
        self.assertEqual(group_analytic_json['id'], self.joe_and_frank.pk)
        self.assertEqual(group_analytic_json['name'], "Joe and Frank")
        self.assertEqual(2, group_analytic_json['count'])

        # try to list contacts as a user not in the organization
        self.login(self.user1)
        response = self.client.get(list_url)
        self.assertEqual(302, response.status_code)

        # login as an org viewer
        self.login(self.user)

        response = self.client.get(list_url)
        self.assertContains(response, "Joe Blow")
        self.assertContains(response, "Frank Smith")
        self.assertContains(response, "Billy Nophone")
        self.assertContains(response, "Joe and Frank")
        self.assertEqual(response.context['actions'], ('label', 'block'))

        # make sure Joe's preferred URN is in the list
        self.assertContains(response, "blow80")

        # this just_joe group has one contact and joe_and_frank group has two contacts
        self.assertEqual(len(self.just_joe.contacts.all()), 1)
        self.assertEqual(len(self.joe_and_frank.contacts.all()), 2)

        # viewer cannot remove Joe from the group
        post_data = dict()
        post_data['action'] = 'label'
        post_data['label'] = self.just_joe.id
        post_data['objects'] = self.joe.id
        post_data['add'] = False

        # no change
        self.client.post(list_url, post_data, follow=True)
        self.assertEqual(len(self.just_joe.contacts.all()), 1)
        self.assertEqual(len(self.joe_and_frank.contacts.all()), 2)

        # viewer also can't block
        post_data['action'] = 'block'
        self.client.post(list_url, post_data, follow=True)
        self.assertFalse(Contact.objects.get(pk=self.joe.id).is_blocked)

        # list the contacts as a manager of the organization
        self.login(self.admin)
        response = self.client.get(list_url)
        self.assertEqual(list(response.context['object_list']), [self.voldemort, self.billy, self.frank, self.joe])
        self.assertEqual(response.context['actions'], ('label', 'block'))

        # this just_joe group has one contact and joe_and_frank group has two contacts
        self.assertEqual(len(self.just_joe.contacts.all()), 1)
        self.assertEqual(len(self.joe_and_frank.contacts.all()), 2)

        # add a new group
        group = self.create_group("Test", [self.joe])

        # view our test group
        filter_url = reverse('contacts.contact_filter', args=[group.uuid])
        response = self.client.get(filter_url)
        self.assertEqual(1, len(response.context['object_list']))
        self.assertEqual(self.joe, response.context['object_list'][0])

        # should have the export link
        export_url = "%s?g=%s" % (reverse('contacts.contact_export'), group.uuid)
        self.assertContains(response, export_url)

        # should have an edit button
        update_url = reverse('contacts.contactgroup_update', args=[group.pk])
        delete_url = reverse('contacts.contactgroup_delete', args=[group.pk])

        self.assertContains(response, update_url)
        response = self.client.get(update_url)
        self.assertIn('name', response.context['form'].fields)

        response = self.client.post(update_url, dict(name="New Test"))
        self.assertRedirect(response, filter_url)

        group = ContactGroup.user_groups.get(pk=group.pk)
        self.assertEqual("New Test", group.name)

        # post to our delete url
        response = self.client.post(delete_url, dict(), HTTP_X_PJAX=True)
        self.assertEqual(200, response.status_code)

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
        self.assertEqual(len(self.just_joe.contacts.all()), 0)
        self.assertEqual(len(self.joe_and_frank.contacts.all()), 2)

        # Now add back Joe to the group
        post_data = dict()
        post_data['action'] = 'label'
        post_data['label'] = self.just_joe.id
        post_data['objects'] = self.joe.id
        post_data['add'] = True

        self.client.post(list_url, post_data, follow=True)
        self.assertEqual(len(self.just_joe.contacts.all()), 1)
        self.assertEqual(self.just_joe.contacts.all()[0].pk, self.joe.pk)
        self.assertEqual(len(self.joe_and_frank.contacts.all()), 2)

        # test filtering by group
        joe_and_frank_filter_url = reverse('contacts.contact_filter', args=[self.joe_and_frank.uuid])

        # now test when the action with some data missing
        self.assertEqual(self.joe.user_groups.filter(is_active=True).count(), 2)

        post_data = dict()
        post_data['action'] = 'label'
        post_data['objects'] = self.joe.id
        post_data['add'] = True
        self.client.post(joe_and_frank_filter_url, post_data)
        self.assertEqual(self.joe.user_groups.filter(is_active=True).count(), 2)

        post_data = dict()
        post_data['action'] = 'unlabel'
        post_data['objects'] = self.joe.id
        post_data['add'] = True
        self.client.post(joe_and_frank_filter_url, post_data)
        self.assertEqual(self.joe.user_groups.filter(is_active=True).count(), 2)

        # Now block Joe
        post_data = dict()
        post_data['action'] = 'block'
        post_data['objects'] = self.joe.id
        self.client.post(list_url, post_data, follow=True)

        self.joe = Contact.objects.filter(pk=self.joe.pk)[0]
        self.assertEqual(self.joe.is_blocked, True)
        self.assertEqual(len(self.just_joe.contacts.all()), 0)
        self.assertEqual(len(self.joe_and_frank.contacts.all()), 1)

        # shouldn't be any contacts on the stopped page
        response = self.client.get(reverse('contacts.contact_stopped'))
        self.assertEqual(0, len(response.context['object_list']))

        # mark frank as stopped
        self.frank.stop(self.user)

        stopped_url = reverse('contacts.contact_stopped')

        response = self.client.get(stopped_url)
        self.assertEqual(1, len(response.context['object_list']))
        self.assertEqual(1, response.context['object_list'].count())  # from ContactGroupCount

        # receiving an incoming message removes us from stopped
        Msg.create_incoming(self.channel, str(self.frank.get_urn('tel')), "Incoming message")

        response = self.client.get(stopped_url)
        self.assertEqual(0, len(response.context['object_list']))
        self.assertEqual(0, response.context['object_list'].count())  # from ContactGroupCount

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
        self.assertEqual(0, len(response.context['object_list']))
        self.assertEqual(0, response.context['object_list'].count())  # from ContactGroupCount

        self.frank.refresh_from_db()
        self.assertFalse(self.frank.is_stopped)

        # add him back to joe and frank
        self.joe_and_frank.contacts.add(self.frank)

        # Now let's visit the blocked contacts page
        blocked_url = reverse('contacts.contact_blocked')

        self.billy.block(self.admin)

        response = self.client.get(blocked_url)
        self.assertEqual(list(response.context['object_list']), [self.billy, self.joe])

        # can search blocked contacts from this page
        response = self.client.get(blocked_url + '?search=Joe')
        self.assertEqual(list(response.context['object_list']), [self.joe])

        # can unblock contacts from this page
        self.client.post(blocked_url, {'action': 'unblock', 'objects': self.joe.id}, follow=True)

        # and check that Joe is restored to the contact list but the group not restored
        response = self.client.get(list_url)
        self.assertContains(response, "Joe Blow")
        self.assertContains(response, "Frank Smith")
        self.assertEqual(response.context['actions'], ('label', 'block'))
        self.assertEqual(len(self.just_joe.contacts.all()), 0)
        self.assertEqual(len(self.joe_and_frank.contacts.all()), 1)

        # now let's test removing a contact from a group
        post_data = dict()
        post_data['action'] = 'unlabel'
        post_data['label'] = self.joe_and_frank.id
        post_data['objects'] = self.frank.id
        post_data['add'] = True
        self.client.post(joe_and_frank_filter_url, post_data, follow=True)
        self.assertEqual(len(self.joe_and_frank.contacts.all()), 0)

        # add an extra field to the org
        ContactField.get_or_create(self.org, self.user, 'state', label="Home state", value_type=Value.TYPE_STATE)
        self.joe.set_field(self.user, 'state', " kiGali   citY ")  # should match "Kigali City"

        # check that the field appears on the update form
        response = self.client.get(reverse('contacts.contact_update', args=[self.joe.id]))
        self.assertEqual(list(response.context['form'].fields.keys()), ['name', 'groups', 'urn__twitter__0', 'urn__tel__1', 'loc'])
        self.assertEqual(response.context['form'].initial['name'], "Joe Blow")
        self.assertEqual(response.context['form'].fields['urn__tel__1'].initial, "+250781111111")

        contact_field = ContactField.objects.filter(key='state').first()
        response = self.client.get('%s?field=%s' % (reverse('contacts.contact_update_fields', args=[self.joe.id]), contact_field.id))
        self.assertEqual('Home state', response.context['contact_field'].label)

        # grab our input field which is loaded async
        response = self.client.get('%s?field=%s' % (reverse('contacts.contact_update_fields_input', args=[self.joe.id]), contact_field.id))
        self.assertContains(response, 'Kigali City')

        # update it to something else
        self.joe.set_field(self.user, 'state', "eastern province")

        # check the read page
        response = self.client.get(reverse('contacts.contact_read', args=[self.joe.uuid]))
        self.assertContains(response, "Eastern Province")

        # update joe - change his tel URN
        data = dict(name="Joe Blow", urn__tel__1="+250 783835665", order__urn__tel__1="0")
        self.client.post(reverse('contacts.contact_update', args=[self.joe.id]), data)

        # update the state contact field to something invalid
        self.client.post(reverse('contacts.contact_update_fields', args=[self.joe.id]), dict(contact_field=contact_field.id, field_value='newyork'))

        # check that old URN is detached, new URN is attached, and Joe still exists
        self.joe = Contact.objects.get(pk=self.joe.id)
        self.assertEqual(self.joe.get_urn_display(scheme=TEL_SCHEME), "0783 835 665")
        self.assertEqual(self.joe.get_field_raw('state'), "newyork")  # raw user input as location wasn't matched
        self.assertIsNone(Contact.from_urn(self.org, "tel:+250781111111"))  # original tel is nobody now

        # update joe, change his number back
        data = dict(name="Joe Blow", urn__tel__0="+250781111111", order__urn__tel__0="0", __field__location="Kigali")
        self.client.post(reverse('contacts.contact_update', args=[self.joe.id]), data)

        # check that old URN is re-attached
        self.assertIsNone(ContactURN.objects.get(identity="tel:+250783835665").contact)
        self.assertEqual(self.joe, ContactURN.objects.get(identity="tel:+250781111111").contact)

        # add another URN to joe
        ContactURN.create(self.org, self.joe, "tel:+250786666666")

        # assert that our update form has the extra URN
        response = self.client.get(reverse('contacts.contact_update', args=[self.joe.id]))
        self.assertEqual(response.context['form'].fields['urn__tel__0'].initial, "+250781111111")
        self.assertEqual(response.context['form'].fields['urn__tel__1'].initial, "+250786666666")

        # update joe, add him to "Just Joe" group
        post_data = dict(name="Joe Gashyantare", groups=[self.just_joe.id],
                         urn__tel__0="+250781111111", urn__tel__1="+250786666666")
        response = self.client.post(reverse('contacts.contact_update', args=[self.joe.id]), post_data, follow=True)
        self.assertEqual(response.context['contact'].name, "Joe Gashyantare")
        self.assertEqual(set(self.joe.user_groups.all()), {self.just_joe})
        self.assertTrue(ContactURN.objects.filter(contact=self.joe, path="+250781111111"))
        self.assertTrue(ContactURN.objects.filter(contact=self.joe, path="+250786666666"))

        # remove him from this group "Just joe", and his second number
        post_data = dict(name="Joe Gashyantare", urn__tel__0="+250781111111", groups=[])
        response = self.client.post(reverse('contacts.contact_update', args=[self.joe.id]), post_data, follow=True)
        self.assertEqual(set(self.joe.user_groups.all()), set())
        self.assertTrue(ContactURN.objects.filter(contact=self.joe, path="+250781111111"))
        self.assertFalse(ContactURN.objects.filter(contact=self.joe, path="+250786666666"))

        # should no longer be in our update form either
        response = self.client.get(reverse('contacts.contact_update', args=[self.joe.id]))
        self.assertEqual(response.context['form'].fields['urn__tel__0'].initial, "+250781111111")
        self.assertNotIn('urn__tel__1', response.context['form'].fields)

        # check that groups field isn't displayed when contact is blocked
        self.joe.block(self.user)
        response = self.client.get(reverse('contacts.contact_update', args=[self.joe.id]))
        self.assertNotIn('groups', response.context['form'].fields)

        # and that we can still update the contact
        post_data = dict(name="Joe Bloggs", urn__tel__0="+250781111111")
        self.client.post(reverse('contacts.contact_update', args=[self.joe.id]), post_data, follow=True)

        self.joe = Contact.objects.get(pk=self.joe.pk)
        self.assertEqual(self.joe.name, "Joe Bloggs")

        self.joe.unblock(self.user)

        # add new urn for joe
        self.client.post(reverse('contacts.contact_update', args=[self.joe.id]),
                         dict(name='Joey', urn__tel__0="+250781111111", new_scheme="ext", new_path="EXT123"))

        urn = ContactURN.objects.filter(contact=self.joe, scheme='ext').first()
        self.assertIsNotNone(urn)
        self.assertEqual('EXT123', urn.path)

        # now try adding one that is invalid
        self.client.post(reverse('contacts.contact_update', args=[self.joe.id]),
                         dict(name='Joey', urn__tel__0="+250781111111", new_scheme="mailto", new_path="malformed"))
        self.assertIsNone(ContactURN.objects.filter(contact=self.joe, scheme='mailto').first())

        # update our language to something not on the org
        self.joe.refresh_from_db()
        self.joe.language = 'fra'
        self.joe.save()

        # add some languages to our org, but not french
        self.client.post(reverse('orgs.org_languages'), dict(primary_lang='hat', languages='arc,spa'))

        response = self.client.get(reverse('contacts.contact_update', args=[self.joe.id]))
        self.assertContains(response, 'French (Missing)')

        # update our contact with some locations
        state = ContactField.get_or_create(self.org, self.admin, 'state', "Home State", value_type='S')
        district = ContactField.get_or_create(self.org, self.admin, 'home', "Home District", value_type='I')

        self.client.post(reverse('contacts.contact_update_fields', args=[self.joe.id]), dict(contact_field=state.id, field_value='eastern province'))
        self.client.post(reverse('contacts.contact_update_fields', args=[self.joe.id]), dict(contact_field=district.id, field_value='rwamagana'))

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
        value.string_value = "Rwama Value"
        value.save()

        # should now be using stored string_value instead of state name
        response = self.client.get(reverse('contacts.contact_read', args=[self.joe.uuid]))
        self.assertContains(response, 'Rwama Value')

        # bad field
        contact_field = ContactField.objects.create(org=self.org, key='language', label='User Language',
                                                    created_by=self.admin, modified_by=self.admin)

        response = self.client.post(reverse('contacts.contact_update_fields', args=[self.joe.id]),
                                    dict(contact_field=contact_field.id, field_value='Kinyarwanda'))

        self.assertFormError(response, 'form', None, "Field key language has invalid characters or is a reserved field name")

        # try to push into a dynamic group
        self.login(self.admin)
        group = self.create_group('Dynamo', query='tel = 325423')

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

        self.joe.refresh_from_db()
        self.assertEqual(self.joe.name, "Joe X")
        self.assertEqual({six.text_type(u) for u in self.joe.urns.all()}, {"tel:+250781111111", "ext:EXT123"})  # urns unaffected

        # remove all of joe's URNs
        ContactURN.objects.filter(contact=self.joe).update(contact=None)
        response = self.client.get(list_url)

        # no more URN listed
        self.assertNotContains(response, "blow80")

        # try delete action
        event = ChannelEvent.create(self.channel, six.text_type(self.frank.get_urn(TEL_SCHEME)),
                                    ChannelEvent.TYPE_CALL_OUT_MISSED, timezone.now(), {})
        post_data['action'] = 'delete'
        post_data['objects'] = self.frank.pk

        self.client.post(list_url, post_data)
        self.assertFalse(ChannelEvent.objects.filter(contact=self.frank))
        self.assertFalse(ChannelEvent.objects.filter(id=event.id))

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
        self.assertEqual(contact1.name, "Ludacris")

        first_modified_on = contact1.modified_on
        contact1.set_field(self.editor, 'occupation', 'Musician')

        contact1.refresh_from_db()
        self.assertTrue(contact1.modified_on > first_modified_on)
        self.assertEqual(contact1.modified_by, self.editor)

        contact2 = self.create_contact(name="Boy", number="12345")
        self.assertEqual(contact2.get_display(), "Boy")

        contact3 = self.create_contact(name=None, number="0788111222")
        self.channel.country = 'RW'
        self.channel.save()

        normalized = contact3.get_urn(TEL_SCHEME).ensure_number_normalization(self.channel)
        self.assertEqual(normalized.path, "+250788111222")

        contact4 = self.create_contact(name=None, number="0788333444")
        normalized = contact4.get_urn(TEL_SCHEME).ensure_number_normalization(self.channel)
        self.assertEqual(normalized.path, "+250788333444")

        # check normalization leads to matching
        contact5 = self.create_contact(name='Jimmy', number="+250788333555")
        contact6 = self.create_contact(name='James', number="0788333555")
        self.assertEqual(contact5.pk, contact6.pk)

        contact5.update_urns(self.user, ['twitter:jimmy_woot', 'tel:0788333666'])

        # check old phone URN still existing but was detached
        self.assertIsNone(ContactURN.objects.get(identity='tel:+250788333555').contact)

        # check new URNs were created and attached
        self.assertEqual(contact5, ContactURN.objects.get(identity='tel:+250788333666').contact)
        self.assertEqual(contact5, ContactURN.objects.get(identity='twitter:jimmy_woot').contact)

        # check twitter URN takes priority if you don't specify scheme
        self.assertEqual('twitter:jimmy_woot', six.text_type(contact5.get_urn()))
        self.assertEqual('twitter:jimmy_woot', six.text_type(contact5.get_urn(schemes=[TWITTER_SCHEME])))
        self.assertEqual('tel:+250788333666', six.text_type(contact5.get_urn(schemes=[TEL_SCHEME])))
        self.assertIsNone(contact5.get_urn(schemes=['email']))
        self.assertIsNone(contact5.get_urn(schemes=['facebook']))

        # check that we can steal other contact's URNs
        contact5.update_urns(self.user, ['tel:0788333444'])
        self.assertEqual(contact5, ContactURN.objects.get(identity='tel:+250788333444').contact)
        self.assertFalse(contact4.urns.all())

    def test_from_urn(self):
        self.assertEqual(Contact.from_urn(self.org, 'tel:+250781111111'), self.joe)  # URN with contact
        self.assertIsNone(Contact.from_urn(self.org, 'tel:+250788888888'))  # URN with no contact
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

            headers = ['country', 'district', 'zip code', 'professional status', 'joined', 'vehicle', 'shoes', 'email']
            self.assertEqual(Contact.get_org_import_file_headers(csv_file, self.org), headers)

            self.assertNotIn('twitter', Contact.get_org_import_file_headers(csv_file, self.org))

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
        self.assertEqual([six.text_type(u) for u in contact.urns.all()], ["tel:+250788111111"])
        self.assertEqual(contact.created_by, self.admin)

    def test_create_instance_with_language(self):
        contact = Contact.create_instance(dict(
            org=self.org, created_by=self.admin, name="Bob", phone="+250788111111", language="fra"
        ))
        self.assertEqual(contact.language, 'fra')

        # language is not defined in iso639-3
        self.assertRaises(SmartImportRowError, Contact.create_instance, dict(
            org=self.org, created_by=self.admin, name="Mob", phone="+250788111112", language="123"
        ))

    def do_import(self, user, filename):

        import_params = dict(org_id=self.org.id, timezone=six.text_type(self.org.timezone), extra_fields=[],
                             original_filename=filename)

        task = ImportTask.objects.create(
            created_by=user, modified_by=user,
            csv_file='test_imports/' + filename,
            model_class="Contact", import_params=json.dumps(import_params), import_log="", task_id="A")

        return Contact.import_csv(task, log=None), task

    def assertContactImport(self, filepath, expected_results=None, task_customize=None, custom_fields_number=None):
        csv_file = open(filepath, 'rb')
        post_data = dict(csv_file=csv_file)
        response = self.client.post(reverse('contacts.contact_import'), post_data, follow=True)

        self.assertIsNotNone(response.context['task'])

        if task_customize:
            self.assertEqual(response.request['PATH_INFO'], reverse('contacts.contact_customize',
                                                                    args=[response.context['task'].pk]))
            if custom_fields_number:
                self.assertEqual(len(response.context['form'].fields.keys()), custom_fields_number)

        else:
            self.assertEqual(response.context['results'], expected_results)

            # no errors so hide the import form
            if not expected_results.get('error_messages', []):
                self.assertFalse(response.context['show_form'])

            # we have records and added them to a group
            if expected_results.get('records', 0):
                self.assertIsNotNone(response.context['group'])

        return response

    @patch.object(ContactGroup, "MAX_ORG_CONTACTGROUPS", new=10)
    def test_contact_import(self):
        #
        # first import brings in 3 contacts
        user = self.user
        records, _ = self.do_import(user, 'sample_contacts.xls')
        self.assertEqual(3, len(records))

        self.assertEqual(1, len(ContactGroup.user_groups.all()))
        group = ContactGroup.user_groups.all()[0]
        self.assertEqual('Sample Contacts', group.name)
        self.assertEqual(3, group.contacts.count())

        self.assertEqual(1, Contact.objects.filter(name='Eric Newcomer').count())
        self.assertEqual(1, Contact.objects.filter(name='Nic Pottier').count())
        self.assertEqual(1, Contact.objects.filter(name='Jen Newcomer').count())

        # eric opts out
        eric = Contact.objects.get(name='Eric Newcomer')
        eric.stop(self.admin)

        jen_pk = Contact.objects.get(name='Jen Newcomer').pk

        # import again, should be no more records
        records, _ = self.do_import(user, 'sample_contacts.xls')
        self.assertEqual(3, len(records))

        # But there should be another group
        self.assertEqual(2, len(ContactGroup.user_groups.all()))
        self.assertEqual(1, ContactGroup.user_groups.filter(name="Sample Contacts 2").count())

        # assert eric didn't get added to a group
        eric.refresh_from_db()
        self.assertEqual(0, eric.user_groups.count())

        # ok, unstop eric
        eric.unstop(self.admin)

        # update file changes a name, and adds one more
        records, _ = self.do_import(user, 'sample_contacts_update.csv')

        # now there are three groups
        self.assertEqual(3, len(ContactGroup.user_groups.all()))
        self.assertEqual(1, ContactGroup.user_groups.filter(name="Sample Contacts Update").count())

        self.assertEqual(1, Contact.objects.filter(name='Eric Newcomer').count())
        self.assertEqual(1, Contact.objects.filter(name='Nic Pottier').count())
        self.assertEqual(0, Contact.objects.filter(name='Jennifer Newcomer').count())
        self.assertEqual(1, Contact.objects.filter(name='Jackson Newcomer').count())
        self.assertEqual(1, Contact.objects.filter(name='Norbert Kwizera').count())

        # Jackson took over Jen's number
        self.assertEqual(Contact.objects.get(name='Jackson Newcomer').pk, jen_pk)
        self.assertEqual(4, len(records))

        # Empty import file, shouldn't create a contact group
        self.do_import(user, 'empty.csv')
        self.assertEqual(3, len(ContactGroup.user_groups.all()))

        # import twitter urns
        records, _ = self.do_import(user, 'sample_contacts_twitter.xls')
        self.assertEqual(3, len(records))

        # now there are four groups
        self.assertEqual(4, len(ContactGroup.user_groups.all()))
        self.assertEqual(1, ContactGroup.user_groups.filter(name="Sample Contacts Twitter").count())

        self.assertEqual(1, Contact.objects.filter(name='Rapidpro').count())
        self.assertEqual(1, Contact.objects.filter(name='Textit').count())
        self.assertEqual(1, Contact.objects.filter(name='Nyaruka').count())

        # import twitter urns with phone
        records, _ = self.do_import(user, 'sample_contacts_twitter_and_phone.xls')
        self.assertEqual(3, len(records))

        # now there are five groups
        self.assertEqual(5, len(ContactGroup.user_groups.all()))
        self.assertEqual(1, ContactGroup.user_groups.filter(name="Sample Contacts Twitter And Phone").count())

        self.assertEqual(1, Contact.objects.filter(name='Rapidpro').count())
        self.assertEqual(1, Contact.objects.filter(name='Textit').count())
        self.assertEqual(1, Contact.objects.filter(name='Nyaruka').count())

        import_url = reverse('contacts.contact_import')

        self.login(self.admin)
        response = self.client.get(import_url)
        self.assertTrue(response.context['show_form'])
        self.assertFalse(response.context['task'])
        self.assertEqual(response.context['group'], None)

        Contact.objects.all().delete()
        ContactGroup.user_groups.all().delete()

        records, _ = self.do_import(user, 'sample_contacts_UPPER.XLS')
        self.assertEqual(3, len(records))

        self.assertEqual(1, len(ContactGroup.user_groups.all()))
        group = ContactGroup.user_groups.all()[0]
        self.assertEqual(group.name, "Sample Contacts Upper")
        self.assertEqual(3, group.contacts.count())

        Contact.objects.all().delete()
        ContactGroup.user_groups.all().delete()

        records, _ = self.do_import(user, 'sample_contacts_with_filename_very_long_that_it_will_not_validate.xls')
        self.assertEqual(2, len(records))

        self.assertEqual(1, len(ContactGroup.user_groups.all()))
        group = ContactGroup.user_groups.all()[0]
        self.assertEqual(group.name, "Sample Contacts With Filename Very Long That It Will N")
        self.assertEqual(2, group.contacts.count())

        records, _ = self.do_import(user, 'sample_contacts_with_filename_very_long_that_it_will_not_validate.xls')
        self.assertEqual(2, len(records))

        self.assertEqual(2, len(ContactGroup.user_groups.all()))
        group = ContactGroup.user_groups.all()[0]
        self.assertEqual(2, group.contacts.count())
        group = ContactGroup.user_groups.all()[1]
        self.assertEqual(2, group.contacts.count())
        self.assertEqual(set(["Sample Contacts With Filename Very Long That It Will N",
                              "Sample Contacts With Filename Very Long That It Will N 2"]),
                         set(ContactGroup.user_groups.all().values_list('name', flat=True)))

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
            self.assertEqual(mock_lock.call_count, 3)

        self.assertEqual(1, Contact.objects.filter(name='Eric Newcomer').count())
        self.assertEqual(0, Contact.objects.filter(name='Bob').count())
        self.assertEqual(0, Contact.objects.filter(name='Kobe').count())
        eric = Contact.objects.filter(name='Eric Newcomer').first()
        michael = Contact.objects.filter(name='Michael').first()
        self.assertEqual(eric.pk, contact.pk)
        self.assertEqual(michael.pk, contact2.pk)
        self.assertEqual('uuid-1111', eric.uuid)
        self.assertEqual('uuid-4444', michael.uuid)
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
                self.assertEqual(mock_lock.call_count, 2)

            self.assertEqual(1, Contact.objects.filter(name='Eric Newcomer').count())
            self.assertEqual(0, Contact.objects.filter(name='Bob').count())
            self.assertEqual(0, Contact.objects.filter(name='Kobe').count())
            self.assertEqual('uuid-1111', Contact.objects.filter(name='Eric Newcomer').first().uuid)
            self.assertEqual('uuid-4444', Contact.objects.filter(name='Michael').first().uuid)
            self.assertFalse(Contact.objects.filter(uuid='uuid-3333'))  # previously non-existent uuid ignored

            eric = Contact.objects.filter(name='Eric Newcomer').first()
            michael = Contact.objects.filter(name='Michael').first()
            self.assertEqual(eric.pk, contact.pk)
            self.assertEqual(michael.pk, contact2.pk)
            self.assertEqual('uuid-1111', eric.uuid)
            self.assertEqual('uuid-4444', michael.uuid)
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
                                                                 "facebook, twitter, twitterid, viber, line, telegram, mailto, "
                                                                 "external, jiochat, fcm, whatsapp should be provided or a Contact UUID"),
                                                      dict(line=4, error="Invalid Phone number 12345")]))

        # import a spreadsheet with a name and a twitter columns only
        self.assertContactImport('%s/test_imports/sample_contacts_twitter.xls' % settings.MEDIA_ROOT,
                                 dict(records=3, errors=0, error_messages=[], creates=3, updates=0))

        Contact.objects.all().delete()
        ContactGroup.user_groups.all().delete()

        self.assertContactImport('%s/test_imports/sample_contacts_bad_unicode.xls' % settings.MEDIA_ROOT,
                                 dict(records=2, errors=0, creates=2, updates=0, error_messages=[]))

        self.assertEqual(1, Contact.objects.filter(name='John Doe').count())
        self.assertEqual(1, Contact.objects.filter(name='Mary Smith').count())

        contact = Contact.objects.filter(name='John Doe').first()
        contact2 = Contact.objects.filter(name='Mary Smith').first()

        self.assertEqual(list(contact.get_urns().values_list('path', flat=True)), ['+250788123123'])
        self.assertEqual(list(contact2.get_urns().values_list('path', flat=True)), ['+250788345345'])

        Contact.objects.all().delete()
        ContactGroup.user_groups.all().delete()

        # import a spreadsheet with phone, name and twitter columns
        self.assertContactImport('%s/test_imports/sample_contacts_twitter_and_phone.xls' % settings.MEDIA_ROOT,
                                 dict(records=3, errors=0, error_messages=[], creates=3, updates=0))

        self.assertEqual(3, Contact.objects.all().count())
        self.assertEqual(1, Contact.objects.filter(name='Rapidpro').count())
        self.assertEqual(1, Contact.objects.filter(name='Textit').count())
        self.assertEqual(1, Contact.objects.filter(name='Nyaruka').count())

        # import file with row different urn on different existing contacts should ignore those lines
        self.assertContactImport('%s/test_imports/sample_contacts_twitter_and_phone_conflicts.xls' % settings.MEDIA_ROOT,
                                 dict(records=2, errors=0, creates=0, updates=2, error_messages=[]))

        self.assertEqual(3, Contact.objects.all().count())
        self.assertEqual(1, Contact.objects.filter(name='Rapidpro').count())
        self.assertEqual(0, Contact.objects.filter(name='Textit').count())
        self.assertEqual(0, Contact.objects.filter(name='Nyaruka').count())
        self.assertEqual(1, Contact.objects.filter(name='Kigali').count())
        self.assertEqual(1, Contact.objects.filter(name='Klab').count())

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
                                                                "facebook, twitter, twitterid, viber, line, telegram, mailto, "
                                                                "external, jiochat, fcm, whatsapp should be provided or a Contact UUID")]))

            # lock for creates only
            self.assertEqual(mock_lock.call_count, 1)

        self.assertEqual(1, Contact.objects.filter(name='Eric Newcomer').count())
        self.assertEqual(0, Contact.objects.filter(name='Bob').count())
        self.assertEqual(0, Contact.objects.filter(name='Kobe').count())
        eric = Contact.objects.filter(name='Eric Newcomer').first()
        michael = Contact.objects.filter(name='Michael').first()
        self.assertEqual(eric.pk, contact.pk)
        self.assertEqual(michael.pk, contact2.pk)
        self.assertEqual('uuid-1111', eric.uuid)
        self.assertEqual('uuid-4444', michael.uuid)
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
                self.assertEqual(mock_lock.call_count, 2)

            self.assertEqual(1, Contact.objects.filter(name='Eric Newcomer').count())
            self.assertEqual(0, Contact.objects.filter(name='Bob').count())
            self.assertEqual(0, Contact.objects.filter(name='Kobe').count())
            self.assertEqual('uuid-1111', Contact.objects.filter(name='Eric Newcomer').first().uuid)
            self.assertEqual('uuid-4444', Contact.objects.filter(name='Michael').first().uuid)
            self.assertFalse(Contact.objects.filter(uuid='uuid-3333'))  # previously non-existent uuid ignored

            eric = Contact.objects.filter(name='Eric Newcomer').first()
            michael = Contact.objects.filter(name='Michael').first()
            self.assertEqual(eric.pk, contact.pk)
            self.assertEqual(michael.pk, contact2.pk)
            self.assertEqual('uuid-1111', eric.uuid)
            self.assertEqual('uuid-4444', michael.uuid)
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
                                                                "facebook, twitter, twitterid, viber, line, telegram, mailto, "
                                                                "external, jiochat, fcm, whatsapp should be provided or a Contact UUID")]))

            # only lock for create
            self.assertEqual(mock_lock.call_count, 1)

        self.assertEqual(1, Contact.objects.filter(name='Eric Newcomer').count())
        self.assertEqual(0, Contact.objects.filter(name='Bob').count())
        self.assertEqual(0, Contact.objects.filter(name='Kobe').count())
        eric = Contact.objects.filter(name='Eric Newcomer').first()
        michael = Contact.objects.filter(name='Michael').first()
        self.assertEqual(eric.pk, contact.pk)
        self.assertEqual(michael.pk, contact2.pk)
        self.assertEqual('uuid-1111', eric.uuid)
        self.assertEqual('uuid-4444', michael.uuid)
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
                self.assertEqual(mock_lock.call_count, 2)

            self.assertEqual(1, Contact.objects.filter(name='Eric Newcomer').count())
            self.assertEqual(0, Contact.objects.filter(name='Bob').count())
            self.assertEqual(0, Contact.objects.filter(name='Kobe').count())
            self.assertEqual('uuid-1111', Contact.objects.filter(name='Eric Newcomer').first().uuid)
            self.assertEqual('uuid-4444', Contact.objects.filter(name='Michael').first().uuid)
            self.assertFalse(Contact.objects.filter(uuid='uuid-3333'))  # previously non-existent uuid ignored

            eric = Contact.objects.filter(name='Eric Newcomer').first()
            michael = Contact.objects.filter(name='Michael').first()
            self.assertEqual(eric.pk, contact.pk)
            self.assertEqual(michael.pk, contact2.pk)
            self.assertEqual('uuid-1111', eric.uuid)
            self.assertEqual('uuid-4444', michael.uuid)
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
            self.assertEqual(mock_lock.call_count, 1)

        self.assertEqual(1, Contact.objects.filter(name='Bob').count())
        self.assertEqual(1, Contact.objects.filter(name='Kobe').count())
        self.assertFalse(Contact.objects.filter(uuid='uuid-3333'))  # previously non-existent uuid ignored

        with AnonymousOrg(self.org):
            with patch('temba.orgs.models.Org.lock_on') as mock_lock:
                # import contact with uuid column to group the contacts for anon org
                self.assertContactImport('%s/test_imports/sample_contacts_uuid_only.csv' % settings.MEDIA_ROOT,
                                         dict(records=3, errors=0, error_messages=[], creates=1, updates=2))

                # one lock for the create
                self.assertEqual(mock_lock.call_count, 1)

            self.assertEqual(1, Contact.objects.filter(name='Bob').count())
            self.assertEqual(1, Contact.objects.filter(name='Kobe').count())
            self.assertFalse(Contact.objects.filter(uuid='uuid-3333'))  # previously non-existent uuid ignored

        csv_file = open('%s/test_imports/sample_contacts_UPPER.XLS' % settings.MEDIA_ROOT, 'rb')
        post_data = dict(csv_file=csv_file)
        response = self.client.post(import_url, post_data)
        self.assertNoFormErrors(response)

        Contact.objects.all().delete()
        ContactGroup.user_groups.all().delete()

        records, _ = self.do_import(user, 'sample_contacts.xlsx')
        self.assertEqual(3, len(records))

        self.assertEqual(1, len(ContactGroup.user_groups.all()))
        group = ContactGroup.user_groups.all()[0]
        self.assertEqual('Sample Contacts', group.name)
        self.assertEqual(3, group.contacts.count())

        self.assertEqual(1, Contact.objects.filter(name='Eric Newcomer').count())
        self.assertEqual(1, Contact.objects.filter(name='Nic Pottier').count())
        self.assertEqual(1, Contact.objects.filter(name='Jen Newcomer').count())

        Contact.objects.all().delete()
        ContactGroup.user_groups.all().delete()

        with patch('temba.contacts.models.Org.get_country_code') as mock_country_code:
            mock_country_code.return_value = None

            self.assertContactImport(
                '%s/test_imports/sample_contacts_org_missing_country.csv' % settings.MEDIA_ROOT,
                dict(records=0, errors=1,
                     error_messages=[dict(line=2,
                                          error="Invalid Phone number or no country code specified for 788383385")]))

        # try importing a file with a unicode in the name
        csv_file = open('%s/test_imports/abc_@@é.xls' % settings.MEDIA_ROOT, 'rb')
        post_data = dict(csv_file=csv_file)
        response = self.client.post(import_url, post_data)
        self.assertFormError(response, 'form', 'csv_file',
                             'Please make sure the file name only contains alphanumeric characters [0-9a-zA-Z] and '
                             'special characters in -, _, ., (, )')

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
                             'The file you provided is missing a required header. At least one of "Phone", "Facebook", '
                             '"Twitter", "Twitterid", "Viber", "Line", "Telegram", "Mailto", "External", '
                             '"Jiochat", "Fcm", "Whatsapp" or "Contact UUID" should be included.')

        csv_file = open('%s/test_imports/sample_contacts_missing_name_phone_headers.xls' % settings.MEDIA_ROOT, 'rb')
        post_data = dict(csv_file=csv_file)
        response = self.client.post(import_url, post_data)
        self.assertFormError(response, 'form', 'csv_file',
                             'The file you provided is missing a required header. At least one of "Phone", "Facebook", '
                             '"Twitter", "Twitterid", "Viber", "Line", "Telegram", "Mailto", "External", '
                             '"Jiochat", "Fcm", "Whatsapp" or "Contact UUID" should be included.')

        for i in range(ContactGroup.MAX_ORG_CONTACTGROUPS):
            ContactGroup.create_static(self.org, self.admin, 'group%d' % i)

        csv_file = open('%s/test_imports/sample_contacts.xls' % settings.MEDIA_ROOT, 'rb')
        post_data = dict(csv_file=csv_file)
        response = self.client.post(import_url, post_data)
        self.assertFormError(response, 'form', '__all__',
                             "This org has 10 groups and the limit is 10. "
                             "You must delete existing ones before you can create new ones.")

        ContactGroup.user_groups.all().delete()

        # check that no contacts or groups were created by any of the previous invalid imports
        self.assertEqual(Contact.objects.all().count(), 0)
        self.assertEqual(ContactGroup.user_groups.all().count(), 0)

        # existing field
        ContactField.get_or_create(self.org, self.admin, 'ride_or_drive', 'Vehicle')
        ContactField.get_or_create(self.org, self.admin, 'wears', 'Shoes')  # has trailing spaces on excel files as " Shoes  "

        # import spreadsheet with extra columns
        response = self.assertContactImport('%s/test_imports/sample_contacts_with_extra_fields.xls' % settings.MEDIA_ROOT,
                                            None, task_customize=True, custom_fields_number=24)

        # all checkboxes should default to True
        for key in response.context['form'].fields.keys():
            if key.endswith('_include'):
                self.assertTrue(response.context['form'].fields[key].initial)

        customize_url = reverse('contacts.contact_customize', args=[response.context['task'].pk])
        post_data = {
            'column_country_include': 'on',
            'column_professional_status_include': 'on',
            'column_zip_code_include': 'on',
            'column_joined_include': 'on',
            'column_vehicle_include': 'on',
            'column_shoes_include': 'on',
            'column_email_include': 'on',
            'column_country_label': '[_NEW_]Location',
            'column_district_label': 'District',
            'column_professional_status_label': 'Job and Projects',
            'column_zip_code_label': 'Postal Code',
            'column_joined_label': 'Joined',
            'column_vehicle_label': 'Vehicle',
            'column_shoes_label': ' Shoes  ',
            'column_email_label': 'Email',
            'column_country_type': 'T',
            'column_district_type': 'T',
            'column_professional_status_type': 'T',
            'column_zip_code_type': 'N',
            'column_joined_type': 'D',
            'column_vehicle_type': 'T',
            'column_shoes_type': 'N',
            'column_email_type': 'T',
        }

        response = self.client.post(customize_url, post_data, follow=True)
        self.assertEqual(
            response.context['results'], dict(
                records=2, errors=1, creates=2, updates=0,
                error_messages=[{'error': "Language: 'fre' is not a valid ISO639-3 code", 'line': 3}]
            )
        )
        self.assertEqual(Contact.objects.all().count(), 2)
        self.assertEqual(ContactGroup.user_groups.all().count(), 1)
        self.assertEqual(ContactGroup.user_groups.all()[0].name, 'Sample Contacts With Extra Fields')

        contact1 = Contact.objects.all().order_by('name')[0]
        self.assertEqual(contact1.get_field_raw('location'), 'Rwanda')  # renamed from 'Country'
        self.assertEqual(contact1.get_field_display('location'), 'Rwanda')  # renamed from 'Country'

        self.assertEqual(contact1.get_field_raw('ride_or_drive'), 'Moto')  # the existing field was looked up by label
        self.assertEqual(contact1.get_field_raw('wears'), 'Bứnto')  # existing field was looked up by label & stripped
        self.assertEqual(contact1.get_field_raw('email'), 'eric@example.com')

        self.assertEqual(contact1.get_urn(schemes=[TWITTER_SCHEME]).path, 'ewok')
        self.assertEqual(contact1.get_urn(schemes=[EXTERNAL_SCHEME]).path, 'abc-1111')

        # if we change the field type for 'location' to 'datetime' we shouldn't get a category
        ContactField.objects.filter(key='location').update(value_type=Value.TYPE_DATETIME)
        contact1 = Contact.objects.all().order_by('name')[0]

        # not a valid date, so should be None
        self.assertEqual(contact1.get_field_display('location'), None)

        # return it back to a state field
        ContactField.objects.filter(key='location').update(value_type=Value.TYPE_STATE)
        contact1 = Contact.objects.all().order_by('name')[0]

        self.assertIsNone(contact1.get_field_raw('district'))  # wasn't included
        self.assertEqual(contact1.get_field_raw('job_and_projects'), 'coach')  # renamed from 'Professional Status'
        self.assertEqual(contact1.get_field_raw('postal_code'), '600.35')
        self.assertEqual(contact1.get_field_raw('joined'), '31-12-2014 00:00')  # persisted value is localized to org
        self.assertEqual(contact1.get_field_display('joined'), '31-12-2014 00:00')  # display value is also localized

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

        Contact.objects.all().delete()
        ContactGroup.user_groups.all().delete()

        # existing datetime field
        ContactField.objects.create(org=self.org, key='startdate', label='StartDate', value_type=Value.TYPE_DATETIME,
                                    created_by=self.admin, modified_by=self.admin)

        response = self.assertContactImport(
            '%s/test_imports/sample_contacts_with_extra_field_date_joined.xls' % settings.MEDIA_ROOT,
            None, task_customize=True)

        customize_url = reverse('contacts.contact_customize', args=[response.context['task'].pk])

        post_data = dict()
        post_data['column_joined_include'] = 'on'
        post_data['column_joined_type'] = 'D'
        post_data['column_joined_label'] = 'StartDate'
        response = self.client.post(customize_url, post_data, follow=True)
        self.assertEqual(response.context['results'], dict(records=3, errors=0, error_messages=[], creates=3,
                                                           updates=0))

        contact1 = Contact.objects.all().order_by('name')[0]
        self.assertEqual(contact1.get_field_raw('startdate'), '31-12-2014 10:00')

    def test_contact_import_handle_update_contact(self):
        self.login(self.admin)
        self.create_campaign()

        self.create_field('team', "Team")
        ballers = self.create_group("Ballers", query='team = ballers')

        self.campaign.group = ballers
        self.campaign.save()

        self.assertEqual(self.campaign.group, ballers)

        response = self.assertContactImport(
            '%s/test_imports/sample_contacts_with_extra_field_date_planting.xls' % settings.MEDIA_ROOT,
            None, task_customize=True)

        customize_url = reverse('contacts.contact_customize', args=[response.context['task'].pk])

        post_data = dict()
        post_data['column_planting_date_include'] = 'on'
        post_data['column_planting_date_type'] = 'D'
        post_data['column_planting_date_label'] = 'Planting Date'

        post_data['column_team_include'] = 'on'
        post_data['column_team_type'] = 'T'
        post_data['column_team_label'] = 'Team'

        response = self.client.post(customize_url, post_data, follow=True)
        self.assertEqual(response.context['results'], dict(records=1, errors=0, error_messages=[], creates=0,
                                                           updates=1))

        contact1 = Contact.objects.filter(name='John Blow').first()
        self.assertEqual(contact1.get_field_raw('planting_date'), '31-12-2020 10:00')
        self.assertEqual(contact1.get_field_raw('team'), 'Ballers')

        event_fire = EventFire.objects.filter(event=self.message_event, contact=contact1,
                                              event__campaign__group__in=[ballers]).first()
        contact1_planting_date = contact1.get_field('planting_date').datetime_value.replace(second=0, microsecond=0)
        self.assertEqual(event_fire.scheduled, contact1_planting_date + timedelta(days=7))

    def test_contact_import_with_languages(self):
        self.create_contact(name="Eric", number="+250788382382")

        imported_contacts, import_task = self.do_import(self.user, 'sample_contacts_with_language.xls')

        self.assertEqual(2, len(imported_contacts))
        self.assertEqual(Contact.objects.get(urns__path="+250788382382").language, 'eng')  # updated
        self.assertEqual(Contact.objects.get(urns__path="+250788383385").language, None)  # no language

        import_error_messages = json.loads(import_task.import_results)['error_messages']
        self.assertEqual(len(import_error_messages), 1)
        self.assertEqual(import_error_messages[0]['error'], "Language: 'fre' is not a valid ISO639-3 code")

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
        self.assertEqual(c1.pk, c2.pk)

        field_dict = dict(phone='0788123123', created_by=user, modified_by=user, org=self.org, name='LaToya Jackson')
        c1 = Contact.create_instance(field_dict)

        field_dict = dict(phone='0788123123', created_by=user, modified_by=user, org=self.org, name='LaToya Jackson')
        field_dict['name'] = 'LaToya Jackson'
        c2 = Contact.create_instance(field_dict)
        self.assertEqual(c1.pk, c2.pk)

        c1.block(self.user)
        field_dict = dict(phone='0788123123', created_by=user, modified_by=user, org=self.org, name='LaToya Jackson')
        field_dict['name'] = 'LaToya Jackson'
        c2 = Contact.create_instance(field_dict)
        self.assertEqual(c1.pk, c2.pk)
        self.assertFalse(c2.is_blocked)

        import_params = dict(org_id=self.org.id, timezone=timezone.utc, extra_fields=[
            dict(key='nick_name', header='nick name', label='Nickname', type='T')
        ])
        field_dict = dict(phone='0788123123', created_by=user, modified_by=user, org=self.org, name='LaToya Jackson')
        field_dict['yourmom'] = 'face'
        field_dict['nick name'] = 'bob'
        field_dict = Contact.prepare_fields(field_dict, import_params, user=user)
        self.assertNotIn('yourmom', field_dict)
        self.assertNotIn('nick name', field_dict)
        self.assertEqual(field_dict['nick_name'], 'bob')
        self.assertEqual(field_dict['org'], self.org)

        # missing important import params
        with self.assertRaises(Exception):
            Contact.prepare_fields(field_dict, dict())

        # check that trying to save an extra field with a reserved name throws an exception
        with self.assertRaises(Exception):
            import_params = dict(org_id=self.org.id, timezone=timezone.utc, extra_fields=[
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
                              org=self.org, name='Janet Jackson')
            field_dict['contact uuid'] = c1.uuid

            c3 = Contact.create_instance(field_dict)
            self.assertEqual(c3.pk, c1.pk)
            self.assertEqual(c3.name, "Janet Jackson")

        field_dict = dict(phone='0788123123', created_by=user, modified_by=user,
                          org=self.org, name='Josh Childress')
        field_dict['contact uuid'] = c1.uuid

        c4 = Contact.create_instance(field_dict)
        self.assertEqual(c4.pk, c1.pk)
        self.assertEqual(c4.name, "Josh Childress")

        field_dict = dict(phone='0788123123', created_by=user, modified_by=user,
                          org=self.org, name='Goran Dragic')
        field_dict['uuid'] = c1.uuid

        c5 = Contact.create_instance(field_dict)
        self.assertEqual(c5.pk, c1.pk)
        self.assertEqual(c5.name, "Goran Dragic")

    def test_fields(self):
        # set a field on joe
        self.joe.set_field(self.user, 'abc_1234', 'Joe', label="Name")
        self.assertEqual('Joe', self.joe.get_field_raw('abc_1234'))

        self.joe.set_field(self.user, 'abc_1234', None)
        self.assertEqual(None, self.joe.get_field_raw('abc_1234'))

        # try storing an integer, should get turned into a string
        self.joe.set_field(self.user, 'abc_1234', 1)
        self.assertEqual('1', self.joe.get_field_raw('abc_1234'))

        # we should have a field with the key
        ContactField.objects.get(key='abc_1234', label="Name", org=self.joe.org)

        # setting with a different label should update it
        self.joe.set_field(self.user, 'abc_1234', 'Joe', label="First Name")
        self.assertEqual('Joe', self.joe.get_field_raw('abc_1234'))
        ContactField.objects.get(key='abc_1234', label="First Name", org=self.joe.org)

        modified_on = self.joe.modified_on

        # set_field should only write to the database if the value changes
        with self.assertNumQueries(7):
            self.joe.set_field(self.user, 'abc_1234', 'Joe')

        self.joe.refresh_from_db()
        self.assertEqual(self.joe.modified_on, modified_on)

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

    def test_field_values(self):
        registration_field = ContactField.get_or_create(self.org, self.admin, 'registration_date', "Registration Date",
                                                        None, Value.TYPE_DATETIME)

        weight_field = ContactField.get_or_create(self.org, self.admin, 'weight', "Weight", None, Value.TYPE_DECIMAL)
        color_field = ContactField.get_or_create(self.org, self.admin, 'color', "Color", None, Value.TYPE_TEXT)
        state_field = ContactField.get_or_create(self.org, self.admin, 'state', "State", None, Value.TYPE_STATE)

        joe = Contact.objects.get(pk=self.joe.pk)
        joe.set_field(self.user, 'registration_date', "2014-12-31T01:04:00Z")
        joe.set_field(self.user, 'weight', "75.888888")
        joe.set_field(self.user, 'color', "green")
        joe.set_field(self.user, 'state', "kigali city")

        # none value instances
        self.assertEqual(Contact.serialize_field_value(weight_field, None), None)
        self.assertEqual(Contact.get_field_display_for_value(weight_field, None), None)
        self.assertEqual(Contact.serialize_field_value(registration_field, None), None)
        self.assertEqual(Contact.get_field_display_for_value(registration_field, None), None)

        value = joe.get_field(registration_field.key)
        self.assertEqual(Contact.serialize_field_value(registration_field, value), '2014-12-31T03:04:00+02:00')

        value = joe.get_field(weight_field.key)
        self.assertEqual(Contact.serialize_field_value(weight_field, value), '75.888888')
        self.assertEqual(Contact.get_field_display_for_value(weight_field, value), '75.888888')

        joe.set_field(self.user, 'weight', "0")
        value = joe.get_field(weight_field.key)
        self.assertEqual(Contact.serialize_field_value(weight_field, value), "0")
        self.assertEqual(Contact.get_field_display_for_value(weight_field, value), "0")

        # passing something non-numeric to a decimal field
        joe.set_field(self.user, 'weight', "xxx")
        value = joe.get_field(weight_field.key)
        self.assertEqual(Contact.serialize_field_value(weight_field, value), None)
        self.assertEqual(Contact.get_field_display_for_value(weight_field, value), "")

        value = joe.get_field(state_field.key)
        self.assertEqual(Contact.serialize_field_value(state_field, value), 'Rwanda > Kigali City')
        self.assertEqual(Contact.get_field_display_for_value(state_field, value), 'Kigali City')

        value = joe.get_field(color_field.key)
        self.assertEqual(Contact.serialize_field_value(color_field, value), 'green')
        self.assertEqual(Contact.get_field_display_for_value(color_field, value), 'green')

    def test_set_location_fields(self):
        district_field = ContactField.get_or_create(self.org, self.admin, 'district', 'District', None, Value.TYPE_DISTRICT)
        not_state_field = ContactField.get_or_create(self.org, self.admin, 'not_state', 'Not State', None, Value.TYPE_TEXT)

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
        self.assertEqual(value.location_value.name, "Kigali City")
        self.assertEqual("Kigali City", joe.get_field_display_for_value(state_field, value))
        self.assertEqual("Rwanda > Kigali City", joe.serialize_field_value(state_field, value))

        # test that we don't normalize non-location fields
        joe.set_field(self.user, 'not_state', 'kigali city')
        value = Value.objects.filter(contact=joe, contact_field=not_state_field).first()
        self.assertEqual("kigali city", joe.get_field_display_for_value(not_state_field, value))
        self.assertEqual("kigali city", joe.serialize_field_value(not_state_field, value))

        joe.set_field(self.user, 'district', 'Remera')
        value = Value.objects.filter(contact=joe, contact_field=district_field).first()
        self.assertTrue(value.location_value)
        self.assertEqual(value.location_value.name, "Remera")
        self.assertEqual(value.location_value.parent, kigali)

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
        self.assertEqual(value.location_value, ward)

    def test_expressions_context(self):
        self.joe.urns.filter(scheme='twitter').delete()
        ContactURN.create(self.joe.org, self.joe, "twitterid:12345#therealjoe")

        context = self.joe.build_expressions_context()

        self.assertEqual("Joe", context['first_name'])
        self.assertEqual("Joe Blow", context['name'])
        self.assertEqual("Joe Blow", context['__default__'])

        self.assertEqual("0781 111 111", context['tel']['__default__'])
        self.assertEqual("+250781111111", context['tel']['path'])
        self.assertEqual("tel", context['tel']['scheme'])
        self.assertEqual("0781 111 111", context['tel']['display'])
        self.assertEqual("tel:+250781111111", context['tel']['urn'])

        self.assertEqual("", context['groups'])
        self.assertEqual(context['uuid'], self.joe.uuid)
        self.assertEqual(self.joe.uuid, context['uuid'])

        self.assertEqual("therealjoe", context['twitter']['__default__'])

        self.assertEqual("therealjoe", context['twitterid']['__default__'])
        self.assertEqual("12345", context['twitterid']['path'])
        self.assertEqual("twitterid:12345#therealjoe", context['twitterid']['urn'])

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

        context = self.joe.build_expressions_context()

        self.assertEqual("Joe", context['first_name'])
        self.assertEqual("Joe Blow", context['name'])
        self.assertEqual("Joe Blow", context['__default__'])
        self.assertEqual("0781 111 111", context['tel']['__default__'])
        self.assertEqual("Reporters", context['groups'])
        self.assertNotIn('id', context)

        self.assertEqual("SeaHawks", context['team'])
        self.assertNotIn('color', context)

        # switch our org to anonymous
        with AnonymousOrg(self.org):
            self.joe.org.refresh_from_db()

            context = self.joe.build_expressions_context()
            self.assertEqual("********", context['tel'])
            self.assertEqual(self.joe.id, context['id'])

    def test_urn_priority(self):
        bob = self.create_contact("Bob")

        bob.update_urns(self.user, ['tel:456', 'tel:789'])
        urns = bob.urns.all().order_by('-priority')
        self.assertEqual(2, len(urns))
        self.assertEqual('456', urns[0].path)
        self.assertEqual('789', urns[1].path)
        self.assertEqual(99, urns[0].priority)
        self.assertEqual(98, urns[1].priority)

        bob.update_urns(self.user, ['tel:789', 'tel:456'])
        urns = bob.urns.all().order_by('-priority')
        self.assertEqual(2, len(urns))
        self.assertEqual('789', urns[0].path)
        self.assertEqual('456', urns[1].path)

        # add an email urn
        bob.update_urns(self.user, ['mailto:bob@marley.com', 'tel:789', 'tel:456'])
        urns = bob.urns.all().order_by('-priority')
        self.assertEqual(3, len(urns))
        self.assertEqual(99, urns[0].priority)
        self.assertEqual(98, urns[1].priority)
        self.assertEqual(97, urns[2].priority)

        # it'll come back as the highest priority
        self.assertEqual('bob@marley.com', urns[0].path)

        # but not the highest 'sendable' urn
        contact, urn = Msg.resolve_recipient(self.org, self.admin, bob, self.channel)
        self.assertEqual(urn.path, '789')

        # swap our phone numbers
        bob.update_urns(self.user, ['mailto:bob@marley.com', 'tel:456', 'tel:789'])
        contact, urn = Msg.resolve_recipient(self.org, self.admin, bob, self.channel)
        self.assertEqual(urn.path, '456')

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
            joes_group = self.create_group("People called Joe", query='twitter = "blow80"')
            mtn_group = self.create_group("People with number containing '078'", query='tel has "078"')

            self.mary = self.create_contact("Mary", "+250783333333")
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
            men_group = self.create_group("Boys", query='gender = "male" AND age >= 18')
            women_group = self.create_group("Girls", query='gender = "female" AND age >= 18')

            joe_flow = self.create_flow()
            joes_campaign = Campaign.create(self.org, self.admin, "Joe Reminders", joes_group)
            joes_event = CampaignEvent.create_flow_event(self.org, self.admin, joes_campaign, relative_to=joined_field,
                                                         offset=1, unit='W', flow=joe_flow, delivery_hour=17)
            EventFire.update_campaign_events(joes_campaign)

            # check initial group members added correctly
            self.assertEqual([self.frank, self.joe, self.mary], list(mtn_group.contacts.order_by('name')))
            self.assertEqual([self.frank, self.joe], list(men_group.contacts.order_by('name')))
            self.assertEqual([self.mary], list(women_group.contacts.order_by('name')))
            self.assertEqual([self.joe], list(joes_group.contacts.order_by('name')))

            # try removing frank from dynamic group (shouldnt happen, ui doesnt allow this)
            with self.assertRaises(ValueError):
                self.login(self.admin)
                self.client.post(reverse('contacts.contact_read', args=[self.frank.uuid]),
                                 dict(contact=self.frank.pk, group=men_group.pk))

            # check event fire initialized correctly
            joe_fires = EventFire.objects.filter(event=joes_event)
            self.assertEqual(1, joe_fires.count())
            self.assertEqual(self.joe, joe_fires.first().contact)

            # Frank becomes Francine...
            self.frank.set_field(self.user, 'gender', "Female")
            self.assertEqual([self.joe], list(men_group.contacts.order_by('name')))
            self.assertEqual([self.frank, self.mary], list(women_group.contacts.order_by('name')))

            # Mary changes her twitter handle
            self.mary.update_urns(self.user, ['twitter:blow80'])
            self.assertEqual([self.joe, self.mary], list(joes_group.contacts.order_by('name')))

            # Mary should also have an event fire now
            joe_fires = EventFire.objects.filter(event=joes_event)
            self.assertEqual(2, joe_fires.count())

            # change Mary's URNs
            self.mary.update_urns(self.user, ['tel:54321', 'twitter:mary_mary'])
            self.assertEqual([self.frank, self.joe], list(mtn_group.contacts.order_by('name')))

    def test_simulator_contact_views(self):
        simulator_contact = Contact.get_test_contact(self.admin)

        other_contact = self.create_contact("Will", "+250788987987")

        group = self.create_group("Members", [simulator_contact, other_contact])

        self.login(self.admin)
        response = self.client.get(reverse('contacts.contact_read', args=[simulator_contact.uuid]))
        self.assertEqual(response.status_code, 404)

        response = self.client.get(reverse('contacts.contact_update', args=[simulator_contact.pk]))
        self.assertEqual(response.status_code, 404)

        response = self.client.get(reverse('contacts.contact_list'))
        self.assertEqual(response.status_code, 200)
        self.assertFalse(simulator_contact in response.context['object_list'])
        self.assertTrue(other_contact in response.context['object_list'])
        self.assertNotContains(response, "Simulator Contact")

        response = self.client.get(reverse('contacts.contact_filter', args=[group.uuid]))
        self.assertEqual(response.status_code, 200)
        self.assertFalse(simulator_contact in response.context['object_list'])
        self.assertTrue(other_contact in response.context['object_list'])
        self.assertNotContains(response, "Simulator Contact")

    def test_preferred_channel(self):
        from temba.msgs.tasks import process_message_task

        ContactField.get_or_create(self.org, self.admin, 'age', label='Age', value_type=Value.TYPE_DECIMAL)
        ContactField.get_or_create(self.org, self.admin, 'gender', label='Gender', value_type=Value.TYPE_TEXT)

        ContactGroup.create_dynamic(
            self.org, self.admin, 'simple group',
            '(Age < 18 and gender = "male") or (Age > 18 and gender = "female")'
        )
        ContactGroup.create_dynamic(self.org, self.admin, 'Empty age field', 'age = ""')
        ContactGroup.create_dynamic(self.org, self.admin, 'urn group', 'twitter = "macklemore"')

        # create some channels of various types
        twitter = Channel.create(self.org, self.user, None, 'TT', name="Twitter Channel", address="@rapidpro")
        Channel.create(self.org, self.user, None, 'TG', name="Twitter Channel", address="@rapidpro")

        # can't set preferred channel for test contacts
        self.test_contact = self.create_contact(name="Test Contact", number="+12065551212", is_test=True)
        self.test_contact.update_urns(self.admin, ['tel:+12065551212', 'telegram:12515', 'twitter:macklemore'])
        self.test_contact.set_preferred_channel(twitter)
        self.assertEqual(self.test_contact.urns.all()[0].scheme, TEL_SCHEME)

        # update our contact URNs, give them telegram and twitter with telegram being preferred
        self.joe.update_urns(self.admin, ['telegram:12515', 'twitter:macklemore'])

        # set the preferred channel to twitter
        self.joe.set_preferred_channel(twitter)

        # preferred URN should be twitter
        self.assertEqual(self.joe.urns.all()[0].scheme, TWITTER_SCHEME)

        # reset back to telegram being preferred
        self.joe.update_urns(self.admin, ['telegram:12515', 'twitter:macklemore'])

        # simulate an incoming message from Mage on Twitter
        msg = Msg.objects.create(
            org=self.org, channel=twitter, contact=self.joe,
            contact_urn=ContactURN.get_or_create(self.org, self.joe, 'twitter:macklemore', twitter),
            text="Incoming twitter DM", created_on=timezone.now()
        )

        with self.assertNumQueries(13):
            process_message_task(dict(id=msg.id, from_mage=True, new_contact=False))

        # twitter should be preferred outgoing again
        self.assertEqual(self.joe.urns.all()[0].scheme, TWITTER_SCHEME)

        # simulate an incoming message from Mage on Twitter, for a new contact
        msg = Msg.objects.create(
            org=self.org, channel=twitter, contact=self.joe,
            contact_urn=ContactURN.get_or_create(self.org, self.joe, 'twitter:macklemore', twitter),
            text="Incoming twitter DM", created_on=timezone.now()
        )

        with self.assertNumQueries(21):
            process_message_task(dict(id=msg.id, from_mage=True, new_contact=True))

        six.assertCountEqual(
            self,
            [group.name for group in self.joe.user_groups.filter(is_active=True).all()],
            ['Empty age field', 'urn group']
        )


class ContactURNTest(TembaTest):
    def setUp(self):
        super(ContactURNTest, self).setUp()

    def test_create(self):
        urn = ContactURN.create(self.org, None, 'tel:1234')
        self.assertEqual(urn.org, self.org)
        self.assertEqual(urn.contact, None)
        self.assertEqual(urn.identity, 'tel:1234')
        self.assertEqual(urn.scheme, 'tel')
        self.assertEqual(urn.path, '1234')
        self.assertEqual(urn.priority, 50)

        urn = ContactURN.get_or_create(self.org, None, "twitterid:12345#fooman")
        self.assertEqual("twitterid:12345", urn.identity)
        self.assertEqual("fooman", urn.display)

        urn2 = ContactURN.get_or_create(self.org, None, "twitter:fooman")
        self.assertEqual(urn, urn2)

        with patch('temba.contacts.models.ContactURN.lookup') as mock_lookup:
            mock_lookup.side_effect = [None, urn]
            ContactURN.get_or_create(self.org, None, "twitterid:12345#fooman")

    def test_get_display(self):
        urn = ContactURN.objects.create(org=self.org, scheme='tel', path='+250788383383', identity='tel:+250788383383', priority=50)
        self.assertEqual(urn.get_display(self.org), '0788 383 383')
        self.assertEqual(urn.get_display(self.org, formatted=False), '+250788383383')
        self.assertEqual(urn.get_display(self.org, international=True), '+250 788 383 383')
        self.assertEqual(urn.get_display(self.org, formatted=False, international=True), '+250788383383')

        urn = ContactURN.objects.create(org=self.org, scheme='twitter', path='billy_bob', identity='twitter:billy_bob', priority=50)
        self.assertEqual(urn.get_display(self.org), 'billy_bob')


class ContactFieldTest(TembaTest):
    def setUp(self):
        super(ContactFieldTest, self).setUp()

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

        for key in Contact.RESERVED_FIELD_KEYS:
            with self.assertRaises(ValueError):
                ContactField.get_or_create(self.org, self.admin, key, key, value_type=Value.TYPE_TEXT)

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
        self.assertEqual(contact_field(self.joe, 'First'), 'Starter')

    def test_make_key(self):
        self.assertEqual("first_name", ContactField.make_key("First Name"))
        self.assertEqual("second_name", ContactField.make_key("Second   Name  "))
        self.assertEqual("323_ffsn_slfs_ksflskfs_fk_anfaddgas", ContactField.make_key("  ^%$# %$$ $##323 ffsn slfs ksflskfs!!!! fk$%%%$$$anfaDDGAS ))))))))) "))

    def test_is_valid_key(self):
        self.assertTrue(ContactField.is_valid_key("age"))
        self.assertTrue(ContactField.is_valid_key("age_now_2"))
        self.assertTrue(ContactField.is_valid_key("email"))
        self.assertFalse(ContactField.is_valid_key("Age"))     # must be lowercase
        self.assertFalse(ContactField.is_valid_key("age!"))    # can't have punctuation
        self.assertFalse(ContactField.is_valid_key("âge"))     # a-z only
        self.assertFalse(ContactField.is_valid_key("2up"))     # can't start with a number
        self.assertFalse(ContactField.is_valid_key("name"))    # can't be a contact attribute
        self.assertFalse(ContactField.is_valid_key("uuid"))
        self.assertFalse(ContactField.is_valid_key("tel"))     # can't be URN scheme
        self.assertFalse(ContactField.is_valid_key("mailto"))
        self.assertFalse(ContactField.is_valid_key("a" * 37))  # too long

    def test_is_valid_label(self):
        self.assertTrue(ContactField.is_valid_label("Age"))
        self.assertTrue(ContactField.is_valid_label("Age Now 2"))
        self.assertFalse(ContactField.is_valid_label("Age_Now"))  # can't have punctuation
        self.assertFalse(ContactField.is_valid_label("âge"))      # a-z only

    def test_contact_export(self):
        self.clear_storage()

        self.login(self.admin)

        flow = self.create_flow()

        # archive all our current contacts
        Contact.objects.filter(org=self.org).update(is_blocked=True)

        # start one of our contacts down it
        contact = self.create_contact("Be\02n Haggerty", '+12067799294')
        contact.set_field(self.user, 'First', 'On\02e')

        # make third a datetime
        self.contactfield_3.value_type = Value.TYPE_DATETIME
        self.contactfield_3.save()

        contact.set_field(self.user, 'Third', "20/12/2015 08:30")

        flow.start([], [contact])

        # create another contact, this should sort before Ben
        contact2 = self.create_contact("Adam Sumner", '+12067799191', twitter='adam', language='eng')
        urns = [six.text_type(urn) for urn in contact2.get_urns()]
        urns.append("mailto:adam@sumner.com")
        urns.append("telegram:1234")
        contact2.update_urns(self.admin, urns)

        group = self.create_group('Poppin Tags', [contact, contact2])

        Contact.get_test_contact(self.user)  # create test contact to ensure they aren't included in the export

        # create a dummy export task so that we won't be able to export
        blocking_export = ExportContactsTask.create(self.org, self.admin)

        response = self.client.get(reverse('contacts.contact_export'), dict(), follow=True)
        self.assertContains(response, "already an export in progress")

        # ok, mark that one as finished and try again
        blocking_export.update_status(ExportContactsTask.STATUS_COMPLETE)

        def request_export(query=''):
            self.client.get(reverse('contacts.contact_export') + query)
            task = ExportContactsTask.objects.all().order_by('-id').first()
            filename = "%s/test_orgs/%d/contact_exports/%s.xlsx" % (settings.MEDIA_ROOT, self.org.pk, task.uuid)
            workbook = load_workbook(filename=filename)
            return workbook.worksheets[0]

        # no group specified, so will default to 'All Contacts'
        with self.assertNumQueries(41):
            self.assertExcelSheet(request_export(), [
                ["Contact UUID", "Name", "Language", "Email", "Phone", "Telegram", "Twitter", "First", "Second", "Third"],
                [contact2.uuid, "Adam Sumner", "eng", "adam@sumner.com", "+12067799191", "1234", "adam", "", "", ""],
                [contact.uuid, "Ben Haggerty", "", "", "+12067799294", "", "", "One", "", "20-12-2015 08:30"],
            ])

        # more contacts do not increase the queries
        contact3 = self.create_contact('Luol Deng', '+12078776655', twitter='deng')
        contact4 = self.create_contact('Stephen', '+12078778899', twitter='stephen')
        ContactURN.create(self.org, contact, 'tel:+12062233445')

        # but should have additional Twitter and phone columns
        with self.assertNumQueries(41):
            self.assertExcelSheet(request_export(), [
                ["Contact UUID", "Name", "Language", "Email", "Phone", "Phone", "Telegram", "Twitter", "First", "Second", "Third"],
                [contact2.uuid, "Adam Sumner", "eng", "adam@sumner.com", "+12067799191", "", "1234", "adam", "", "", ""],
                [contact.uuid, "Ben Haggerty", "", "", "+12067799294", "+12062233445", "", "", "One", "", "20-12-2015 08:30"],
                [contact3.uuid, "Luol Deng", "", "", "+12078776655", "", "", "deng", "", "", ""],
                [contact4.uuid, "Stephen", "", "", "+12078778899", "", "", "stephen", "", "", ""],
            ])

        # export a specified group of contacts (only Ben and Adam are in the group)
        with self.assertNumQueries(42):
            self.assertExcelSheet(request_export('?g=%s' % group.uuid), [
                ["Contact UUID", "Name", "Language", "Email", "Phone", "Phone", "Telegram", "Twitter", "First", "Second", "Third"],
                [contact2.uuid, "Adam Sumner", "eng", "adam@sumner.com", "+12067799191", "", "1234", "adam", "", "", ""],
                [contact.uuid, "Ben Haggerty", "", "", "+12067799294", "+12062233445", "", "", "One", "", "20-12-2015 08:30"],
            ])

        # export a search
        with self.assertNumQueries(41):
            self.assertExcelSheet(request_export('?s=name+has+adam+or+name+has+deng'), [
                ["Contact UUID", "Name", "Language", "Email", "Phone", "Phone", "Telegram", "Twitter", "First", "Second", "Third"],
                [contact2.uuid, "Adam Sumner", "eng", "adam@sumner.com", "+12067799191", "", "1234", "adam", "", "", ""],
                [contact3.uuid, "Luol Deng", "", "", "+12078776655", "", "", "deng", "", "", ""],
            ])

        # export a search within a specified group of contacts
        with self.assertNumQueries(42):
            self.assertExcelSheet(request_export('?g=%s&s=Hagg' % group.uuid), [
                ["Contact UUID", "Name", "Language", "Email", "Phone", "Phone", "Telegram", "Twitter", "First", "Second", "Third"],
                [contact.uuid, "Ben Haggerty", "", "", "+12067799294", "+12062233445", "", "", "One", "", "20-12-2015 08:30"],
            ])

        # now try with an anonymous org
        with AnonymousOrg(self.org):
            self.assertExcelSheet(request_export(), [
                ["ID", "Contact UUID", "Name", "Language", "First", "Second", "Third"],
                [six.text_type(contact2.id), contact2.uuid, "Adam Sumner", "eng", "", "", ""],
                [six.text_type(contact.id), contact.uuid, "Ben Haggerty", "", "One", "", "20-12-2015 08:30"],
                [six.text_type(contact3.id), contact3.uuid, "Luol Deng", "", "", "", ""],
                [six.text_type(contact4.id), contact4.uuid, "Stephen", "", "", "", ""],
            ])

    def test_contact_field_list(self):
        url = reverse('contacts.contactfield_list')
        self.login(self.admin)
        response = self.client.get(url)

        # label and key
        self.assertContains(response, 'First')
        self.assertContains(response, 'first')
        self.assertContains(response, 'Second')
        self.assertContains(response, 'second')

        # try a search and make sure we filter out the second one
        response = self.client.get('%s?search=first' % url)
        self.assertContains(response, 'First')
        self.assertContains(response, 'first')
        self.assertNotContains(response, 'Second')

    def test_delete_with_flow_dependency(self):
        self.login(self.admin)
        self.get_flow('dependencies')

        manage_fields_url = reverse('contacts.contactfield_managefields')
        response = self.client.get(manage_fields_url)

        # prep our post_data from the form in our response
        post_data = dict()
        for id, field in response.context['form'].fields.items():
            if field.initial is None:
                post_data[id] = ''
            elif isinstance(field.initial, ContactField):
                post_data[id] = field.initial.pk
            else:
                post_data[id] = field.initial

        # find our favorite_cat contact field
        favorite_cat = None
        for key, value in six.iteritems(post_data):
            if value == 'Favorite Cat':
                favorite_cat = key
        self.assertIsNotNone(favorite_cat)

        # try deleting favorite_cat, should not work since our flow depends on it
        before = ContactField.objects.filter(org=self.org, is_active=True).count()

        # make sure we can't delete it directly
        with self.assertRaises(Exception):
            ContactField.hide_field(self.org, self.admin, 'favorite_cat')

        # or through the ui
        post_data[favorite_cat] = ''
        response = self.client.post(manage_fields_url, post_data, follow=True)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(before, ContactField.objects.filter(org=self.org, is_active=True).count())
        self.assertFormError(response, 'form', None, 'The field "Favorite Cat" cannot be removed while it is still used in the flow "Dependencies"')

        # remove it from our list of dependencies
        from temba.flows.models import Flow
        flow = Flow.objects.filter(name='Dependencies').first()
        flow.field_dependencies.clear()

        # now we should be successful
        response = self.client.post(manage_fields_url, post_data, follow=True)
        self.assertEqual(response.status_code, 200)
        self.assertNotIn('form', response.context)
        self.assertEqual(before - 1, ContactField.objects.filter(org=self.org, is_active=True).count())

    def test_manage_fields(self):
        manage_fields_url = reverse('contacts.contactfield_managefields')

        self.login(self.non_org_user)
        response = self.client.get(manage_fields_url)

        # redirect to login because of no access to org
        self.assertEqual(302, response.status_code)

        self.login(self.admin)
        response = self.client.get(manage_fields_url)
        self.assertEqual(len(response.context['form'].fields), 16)

        post_data = dict()
        for id, field in response.context['form'].fields.items():
            if field.initial is None:
                post_data[id] = ''
            elif isinstance(field.initial, ContactField):
                post_data[id] = field.initial.pk
            else:
                post_data[id] = field.initial

        response = self.client.post(manage_fields_url, post_data, follow=True)
        self.assertEqual(response.status_code, 200)

        # make sure we didn't have an error
        self.assertNotIn('form', response.context)

        # should still have three contact fields
        self.assertEqual(3, ContactField.objects.filter(org=self.org, is_active=True).count())

        # fields name should be unique case insensitively
        post_data['label_1'] = "Town"
        post_data['label_2'] = "town"

        response = self.client.post(manage_fields_url, post_data, follow=True)
        self.assertFormError(response, 'form', None, "Field names must be unique. 'Town' is duplicated")
        self.assertEqual(3, ContactField.objects.filter(org=self.org, is_active=True).count())
        self.assertFalse(ContactField.objects.filter(org=self.org, label__in=["town", "Town"]))

        # now remove the first field, rename the second and change the type on the third
        post_data['label_1'] = ''
        post_data['label_2'] = 'Number 2'
        post_data['type_3'] = 'N'
        post_data['label_4'] = "New Field"

        response = self.client.post(manage_fields_url, post_data, follow=True)
        self.assertEqual(response.status_code, 200)

        # make sure we didn't have an error
        self.assertNotIn('form', response.context)

        # first field was blank, so it should be inactive
        self.assertIsNone(ContactField.objects.filter(org=self.org, key="first", is_active=True).first())

        # the second should be renamed
        self.assertEqual("Number 2", ContactField.objects.filter(org=self.org, key="second", is_active=True).first().label)

        # the third should have a different type
        self.assertEqual('N', ContactField.objects.filter(org=self.org, key="third", is_active=True).first().value_type)

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

        # bad field
        ContactField.objects.create(org=self.org, key='language', label='User Language',
                                    created_by=self.admin, modified_by=self.admin)

        self.assertEqual(4, ContactField.objects.filter(org=self.org, is_active=True).count())

        response = self.client.get(manage_fields_url)
        post_data = dict()
        for id, field in response.context['form'].fields.items():
            if field.initial is None:
                post_data[id] = ''
            elif isinstance(field.initial, ContactField):
                post_data[id] = field.initial.pk
            else:
                post_data[id] = field.initial

        response = self.client.post(manage_fields_url, post_data, follow=True)
        self.assertFormError(response, 'form', None, "Field key language has invalid characters "
                                                     "or is a reserved field name")

    def test_json(self):
        contact_field_json_url = reverse('contacts.contactfield_json')

        self.org2 = Org.objects.create(name="kLab", timezone="Africa/Kigali", created_by=self.admin, modified_by=self.admin)
        for i in range(30):
            key = 'key%d' % i
            label = 'label%d' % i
            ContactField.get_or_create(self.org, self.admin, key, label)
            ContactField.get_or_create(self.org2, self.admin, key, label)

        self.assertEqual(Org.objects.all().count(), 2)

        ContactField.objects.filter(org=self.org, key='key1').update(is_active=False)

        self.login(self.non_org_user)
        response = self.client.get(contact_field_json_url)

        # redirect to login because of no access to org
        self.assertEqual(302, response.status_code)

        self.login(self.admin)
        response = self.client.get(contact_field_json_url)

        response_json = response.json()

        self.assertEqual(len(response_json), 46)
        self.assertEqual(response_json[0]['label'], 'Full name')
        self.assertEqual(response_json[0]['key'], 'name')
        self.assertEqual(response_json[1]['label'], 'Phone number')
        self.assertEqual(response_json[1]['key'], 'tel_e164')
        self.assertEqual(response_json[2]['label'], 'Facebook identifier')
        self.assertEqual(response_json[2]['key'], 'facebook')
        self.assertEqual(response_json[3]['label'], 'Twitter handle')
        self.assertEqual(response_json[3]['key'], 'twitter')
        self.assertEqual(response_json[4]['label'], 'Twitter ID')
        self.assertEqual(response_json[4]['key'], 'twitterid')
        self.assertEqual(response_json[5]['label'], 'Viber identifier')
        self.assertEqual(response_json[5]['key'], 'viber')
        self.assertEqual(response_json[6]['label'], 'LINE identifier')
        self.assertEqual(response_json[6]['key'], 'line')
        self.assertEqual(response_json[7]['label'], 'Telegram identifier')
        self.assertEqual(response_json[7]['key'], 'telegram')
        self.assertEqual(response_json[8]['label'], 'Email address')
        self.assertEqual(response_json[8]['key'], 'mailto')
        self.assertEqual(response_json[9]['label'], 'External identifier')
        self.assertEqual(response_json[9]['key'], 'ext')
        self.assertEqual(response_json[10]['label'], 'Jiochat identifier')
        self.assertEqual(response_json[10]['key'], 'jiochat')
        self.assertEqual(response_json[11]['label'], 'Firebase Cloud Messaging identifier')
        self.assertEqual(response_json[11]['key'], 'fcm')
        self.assertEqual(response_json[12]['label'], 'WhatsApp identifier')
        self.assertEqual(response_json[12]['key'], 'whatsapp')
        self.assertEqual(response_json[13]['label'], 'Groups')
        self.assertEqual(response_json[13]['key'], 'groups')
        self.assertEqual(response_json[14]['label'], 'First')
        self.assertEqual(response_json[14]['key'], 'first')
        self.assertEqual(response_json[15]['label'], 'label0')
        self.assertEqual(response_json[15]['key'], 'key0')

        ContactField.objects.filter(org=self.org, key='key0').update(label='AAAA')

        response = self.client.get(contact_field_json_url)
        response_json = response.json()

        self.assertEqual(response_json[14]['label'], 'AAAA')
        self.assertEqual(response_json[14]['key'], 'key0')
        self.assertEqual(response_json[15]['label'], 'First')
        self.assertEqual(response_json[15]['key'], 'first')


class URNTest(TembaTest):

    def test_fb_urn(self):
        self.assertEqual('facebook:ref:asdf', URN.from_facebook(URN.path_from_fb_ref('asdf')))
        self.assertEqual('asdf', URN.fb_ref_from_path(URN.path_from_fb_ref('asdf')))
        self.assertTrue(URN.validate(URN.from_facebook(URN.path_from_fb_ref('asdf'))))

    def test_whatsapp_urn(self):
        self.assertEqual('whatsapp:12065551212', URN.from_whatsapp('12065551212'))
        self.assertTrue(URN.validate('whatsapp:12065551212'))
        self.assertFalse(URN.validate('whatsapp:+12065551212'))

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
        self.assertEqual(URN.to_parts("tel:12345"), ("tel", "12345", None))
        self.assertEqual(URN.to_parts("tel:+12345"), ("tel", "+12345", None))
        self.assertEqual(URN.to_parts("twitter:abc_123"), ("twitter", "abc_123", None))
        self.assertEqual(URN.to_parts("mailto:a_b+c@d.com"), ("mailto", "a_b+c@d.com", None))
        self.assertEqual(URN.to_parts("facebook:12345"), ("facebook", "12345", None))
        self.assertEqual(URN.to_parts("telegram:12345"), ("telegram", "12345", None))
        self.assertEqual(URN.to_parts("telegram:12345#foobar"), ("telegram", "12345", "foobar"))
        self.assertEqual(URN.to_parts("ext:Aa0()+,-.:=@;$_!*'"), ("ext", "Aa0()+,-.:=@;$_!*'", None))

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

        # facebook and telegram URN paths must be integers
        self.assertTrue(URN.validate("telegram:12345678901234567"))
        self.assertFalse(URN.validate("telegram:abcdef"))
        self.assertTrue(URN.validate("facebook:12345678901234567"))
        self.assertFalse(URN.validate("facebook:abcdef"))


class PhoneNumberTest(TestCase):
    def test_is_phonenumber(self):
        # these should match as phone numbers
        self.assertEqual(is_phonenumber('+12345678901'), (True, '12345678901'))
        self.assertEqual(is_phonenumber('+1-234-567-8901'), (True, '12345678901'))
        self.assertEqual(is_phonenumber('+1 (234) 567-8901'), (True, '12345678901'))
        self.assertEqual(is_phonenumber('+12345678901'), (True, '12345678901'))
        self.assertEqual(is_phonenumber('+12 34 567 8901'), (True, '12345678901'))
        self.assertEqual(is_phonenumber(' 234 567 8901 '), (True, '2345678901'))

        # these should not be parsed as numbers
        self.assertEqual(is_phonenumber('+12345678901 not a number'), (False, None))
        self.assertEqual(is_phonenumber(''), (False, None))
        self.assertEqual(is_phonenumber('AMAZONS'), (False, None))
        self.assertEqual(is_phonenumber('name = "Jack"'), (False, None))
        self.assertEqual(is_phonenumber('(social = "234-432-324")'), (False, None))
