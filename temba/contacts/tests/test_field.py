from django.urls import reverse

from temba.contacts.models import ContactField
from temba.tests import TembaTest, mock_mailroom
from temba.utils import json


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
