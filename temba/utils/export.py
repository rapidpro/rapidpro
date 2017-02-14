from __future__ import unicode_literals

import csv
import gc
import pytz
import six
import time

from datetime import datetime
from django.core.files.temp import NamedTemporaryFile
from django.db import models
from django.utils.translation import ugettext_lazy as _
from openpyxl import Workbook
from openpyxl.utils.cell import get_column_letter
from openpyxl.writer.write_only import WriteOnlyCell
from smartmin.models import SmartModel
from . import clean_string, analytics


class BaseExportTask(SmartModel):
    """
    Base class for export task models, i.e. contacts, messages and flow results
    """
    EXPORT_NAME = None

    MAX_EXCEL_ROWS = 1048576
    MAX_EXCEL_COLS = 16384

    org = models.ForeignKey('orgs.Org', related_name='%(class)ss', help_text=_("The organization of the user."))

    task_id = models.CharField(null=True, max_length=64)

    uuid = models.CharField(max_length=36, null=True,
                            help_text=_("The uuid used to name the resulting export file"))

    is_finished = models.BooleanField(default=False,
                                      help_text=_("Whether this export has completed"))

    def start_export(self):
        """
        Starts our export, this just wraps our do-export in a try/finally so we can track
        when the export is complete.
        """
        try:
            start = time.time()
            self.do_export()
        finally:
            elapsed = time.time() - start
            analytics.track(self.created_by.username, 'temba.%s_latency' % self.EXPORT_NAME, properties=dict(value=elapsed))

            self.is_finished = True
            self.save(update_fields=('is_finished',))

            gc.collect()  # force garbage collection

    @classmethod
    def append_row(cls, sheet, values):
        row = []
        for value in values:
            cell = WriteOnlyCell(sheet, value=cls.prepare_value(value))
            row.append(cell)
        sheet.append(row)

    @staticmethod
    def prepare_value(value):
        if value is None:
            return ''
        elif isinstance(value, six.string_types):
            value = value.strip()
            if value.startswith('='):  # escape = so value isn't mistaken for a formula
                value = '\'' + value
            return clean_string(value)
        elif isinstance(value, datetime):
            return value.astimezone(pytz.utc).replace(microsecond=0, tzinfo=None)
        else:
            return six.text_type(value)

    @staticmethod
    def set_sheet_column_widths(sheet, widths):
        for index, width in enumerate(widths):
            sheet.column_dimensions[get_column_letter(index + 1)].width = widths[index]

    class Meta:
        abstract = True


class TableExporter(object):
    """
    Class that abstracts out writing a table of data to a CSV or Excel file. This only works for exports that
    have a single sheet (as CSV's don't have sheets) but takes care of writing to a CSV in the case
    where there are more than 256 columns, which Excel doesn't support.

    When writing to an Excel sheet, this also takes care of creating different sheets every 65535
    rows, as again, Excel file only support that many per sheet.
    """
    def __init__(self, sheet_name, columns):
        self.columns = columns
        self.is_csv = len(self.columns) > BaseExportTask.MAX_EXCEL_COLS
        self.sheet_name = sheet_name

        self.current_sheet = 0
        self.current_row = 0

        self.file = NamedTemporaryFile(delete=True, suffix='.xlsx')

        # if this is a csv file, create our csv writer and write our header
        if self.is_csv:
            self.writer = csv.writer(self.file, quoting=csv.QUOTE_ALL)
            self.writer.writerow([s.encode('utf-8') for s in columns])

        # otherwise, just open a workbook, initializing the first sheet
        else:
            self.workbook = Workbook(write_only=True)
            self.sheet_number = 0
            self._add_sheet()

    def _add_sheet(self):
        self.sheet_number += 1

        # add our sheet
        self.sheet = self.workbook.create_sheet(u"%s %d" % (self.sheet_name, self.sheet_number))

        BaseExportTask.append_row(self.sheet, self.columns)

        self.sheet_row = 2

    def write_row(self, values):
        """
        Writes the passed in row to our exporter, taking care of creating new sheets if necessary
        """
        if self.is_csv:
            self.writer.writerow([s.encode('utf-8') for s in values])

        else:
            # time for a new sheet? do it
            if self.sheet_row > BaseExportTask.MAX_EXCEL_ROWS:
                self._add_sheet()

            BaseExportTask.append_row(self.sheet, values)

            self.sheet_row += 1

    def save_file(self):
        """
        Saves our data to a file, returning the file saved to
        """
        # have to flush the XLS file
        if not self.is_csv:
            self.workbook.save(self.file)

        self.file.flush()
        return self.file
