from __future__ import unicode_literals

import csv

from django.core.files.temp import NamedTemporaryFile
from openpyxl import Workbook
from openpyxl.writer.write_only import WriteOnlyCell


class TableExporter(object):
    """
    Class that abstracts out writing a table of data to a CSV or Excel file. This only works for exports that
    have a single sheet (as CSV's don't have sheets) but takes care of writing to a CSV in the case
    where there are more than 256 columns, which Excel doesn't support.

    When writing a to an Excel sheet, this also takes care of creating different sheets every 65535
    rows, as again, Excel file only support that many per sheet.
    """
    MAX_XLS_COLS = 16384
    MAX_XLS_ROWS = 1048576

    def __init__(self, sheet_name, columns):
        self.columns = columns
        self.is_csv = len(self.columns) > TableExporter.MAX_XLS_COLS
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

        row_cells = []
        for col, label in enumerate(self.columns):
            row_cells.append(WriteOnlyCell(self.sheet, value=unicode(label)))
        self.sheet.append(row_cells)
        self.sheet_row = 2

    def write_row(self, values):
        """
        Writes the passed in row to our exporter, taking care of creating new sheets if necessary
        """
        if self.is_csv:
            self.writer.writerow([s.encode('utf-8') for s in values])

        else:
            # time for a new sheet? do it
            if self.sheet_row > TableExporter.MAX_XLS_ROWS:
                self._add_sheet()

            row_cells = []
            for col, value in enumerate(values):
                row_cells.append(WriteOnlyCell(self.sheet, value=unicode(value) if value is not None else ''))

            self.sheet.append(row_cells)
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
