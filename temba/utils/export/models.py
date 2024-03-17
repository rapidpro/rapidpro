import gc
import logging
from datetime import datetime

from xlsxlite.writer import XLSXBook

from django.core.files.temp import NamedTemporaryFile
from django.http import HttpResponse

from temba.utils.text import clean_string

logger = logging.getLogger(__name__)


def prepare_value(value, tz=None):
    """
    Converts a value into the format we want to write into an Excel cell
    """
    if value is None:
        return ""
    if isinstance(value, (bool, int, float)):
        return value
    elif isinstance(value, str):
        if value.startswith("="):  # escape = so value isn't mistaken for a formula
            value = "'" + value
        return clean_string(value)
    elif isinstance(value, datetime):
        return value.astimezone(tz).replace(microsecond=0, tzinfo=None)

    raise ValueError(f"Unsupported type for excel export: {type(value)}")


class MultiSheetExporter:
    """
    Utility to aid writing a stream of rows which may exceed the 1048576 limit on rows per sheet, and require adding
    new sheets.
    """

    MAX_EXCEL_ROWS = 1_048_576
    MAX_EXCEL_COLS = 16384

    def __init__(self, base_sheet_name: str, headers: list, tz):
        self.base_sheet_name = base_sheet_name
        self.headers = headers
        self.tz = tz

        self.current_sheet = 0
        self.current_row = 0

        self.workbook = XLSXBook()
        self.sheet_number = 0
        self._add_sheet()

    def _add_sheet(self):
        self.sheet_number += 1

        # add our sheet
        self.sheet = self.workbook.add_sheet(f"{self.base_sheet_name} {self.sheet_number}")
        self.sheet.append_row(*self.headers)
        self.sheet_row = 2

    def write_row(self, values):
        """
        Writes the passed in row to our exporter, taking care of creating new sheets if necessary
        """

        assert len(values) == len(self.headers), "need same number of column values as column headers"

        # time for a new sheet? do it
        if self.sheet_row > self.MAX_EXCEL_ROWS:
            self._add_sheet()

        self.sheet.append_row(*[prepare_value(v, self.tz) for v in values])
        self.sheet_row += 1

    def save_file(self):
        """
        Saves our data to a file, returning the file saved to and the extension
        """
        gc.collect()  # force garbage collection

        temp_file = NamedTemporaryFile(delete=False, suffix=".xlsx", mode="wb+")
        self.workbook.finalize(to_file=temp_file)
        temp_file.flush()

        return temp_file, "xlsx"


def response_from_workbook(workbook, filename: str) -> HttpResponse:
    """
    Creates an HTTP response from an openpyxl workbook
    """
    with NamedTemporaryFile() as tmp:
        workbook.save(tmp.name)
        tmp.seek(0)
        stream = tmp.read()

    response = HttpResponse(
        content=stream,
        content_type="application/ms-excel",
    )
    response["Content-Disposition"] = f"attachment; filename={filename}"
    return response
