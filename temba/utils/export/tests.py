import os
from datetime import datetime
from unittest.mock import PropertyMock, patch
from zoneinfo import ZoneInfo

from openpyxl import load_workbook

from temba.contacts.models import ContactExport
from temba.orgs.models import Export
from temba.tests import TembaTest

from .models import MultiSheetExporter, prepare_value


class ExportTest(TembaTest):
    def setUp(self):
        super().setUp()

        self.group = self.create_group("New contacts", [])
        self.task = ContactExport.create(
            org=self.org,
            user=self.admin,
            group=self.group,
        )

    def test_prepare_value(self):
        self.assertEqual("", prepare_value(None))
        self.assertEqual("'=()", prepare_value("=()"))  # escape formulas
        self.assertEqual(123, prepare_value(123))
        self.assertEqual(123.5, prepare_value(123.5))
        self.assertEqual(True, prepare_value(True))
        self.assertEqual(False, prepare_value(False))

        dt = datetime(2017, 2, 7, 15, 41, 23, 123_456).replace(tzinfo=ZoneInfo("Africa/Nairobi"))
        self.assertEqual(datetime(2017, 2, 7, 14, 41, 23, 0), prepare_value(dt, self.org.timezone))

        with self.assertRaises(ValueError):
            prepare_value(self)

    def test_task_status(self):
        self.assertEqual(self.task.status, Export.STATUS_PENDING)

        self.task.perform()

        self.assertEqual(self.task.status, Export.STATUS_COMPLETE)

        task2 = ContactExport.create(org=self.org, user=self.admin, group=self.group)

        # if task throws exception, will be marked as failed
        with patch("temba.contacts.models.ContactExport.write") as mock_export_write:
            mock_export_write.side_effect = ValueError("Problem!")

            with self.assertRaises(Exception):
                task2.perform()

            self.assertEqual(task2.status, Export.STATUS_FAILED)

    @patch("temba.utils.export.BaseExport.MAX_EXCEL_ROWS", new_callable=PropertyMock)
    def test_multisheetexporter(self, mock_max_rows):
        test_max_rows = 1500
        mock_max_rows.return_value = test_max_rows

        cols = []
        for i in range(32):
            cols.append("Column %d" % i)

        extra_cols = []
        for i in range(16):
            extra_cols.append("Extra Column %d" % i)

        exporter = MultiSheetExporter("test", cols + extra_cols, self.org.timezone)

        values = []
        for i in range(32):
            values.append("Value %d" % i)

        extra_values = []
        for i in range(16):
            extra_values.append("Extra Value %d" % i)

        # write out 1050000 rows, that'll make two sheets
        for i in range(test_max_rows + 200):
            exporter.write_row(values + extra_values)

        temp_file, file_ext = exporter.save_file()
        workbook = load_workbook(filename=temp_file.name)

        self.assertEqual(2, len(workbook.worksheets))

        # check our sheet 1 values
        sheet1 = workbook.worksheets[0]

        rows = tuple(sheet1.rows)

        self.assertEqual(cols + extra_cols, [cell.value for cell in rows[0]])
        self.assertEqual(values + extra_values, [cell.value for cell in rows[1]])

        self.assertEqual(test_max_rows, len(list(sheet1.rows)))
        self.assertEqual(32 + 16, len(list(sheet1.columns)))

        sheet2 = workbook.worksheets[1]
        rows = tuple(sheet2.rows)
        self.assertEqual(cols + extra_cols, [cell.value for cell in rows[0]])
        self.assertEqual(values + extra_values, [cell.value for cell in rows[1]])

        self.assertEqual(200 + 2, len(list(sheet2.rows)))
        self.assertEqual(32 + 16, len(list(sheet2.columns)))

        os.unlink(temp_file.name)
