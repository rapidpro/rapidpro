from datetime import date, datetime
from unittest.mock import patch
from zoneinfo import ZoneInfo

from django.core.validators import ValidationError
from django.utils import timezone

from temba.contacts.models import ContactField, ContactImport, ContactImportBatch
from temba.tests import TembaTest, matchers, mock_mailroom


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
