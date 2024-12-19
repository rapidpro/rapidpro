import tempfile
from datetime import datetime, timezone as tzone

from openpyxl import load_workbook

from django.core.files.storage import default_storage

from temba.contacts.models import Contact, ContactExport, ContactField, ContactGroup, ContactURN
from temba.orgs.models import Export
from temba.tests import TembaTest, mock_mailroom
from temba.tests.engine import MockSessionWriter


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
