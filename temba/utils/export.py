import gc
import logging
import os
import time
from datetime import datetime, timedelta

from xlsxlite.writer import XLSXBook

from django.core.files import File
from django.core.files.temp import NamedTemporaryFile
from django.db import models
from django.utils import timezone
from django.utils.translation import ugettext_lazy as _

from temba.assets.models import BaseAssetStore, get_asset_store

from . import analytics
from .email import send_template_email
from .models import TembaModel
from .text import clean_string

logger = logging.getLogger(__name__)


class BaseExportAssetStore(BaseAssetStore):
    def is_asset_ready(self, asset):
        return asset.status == BaseExportTask.STATUS_COMPLETE


class BaseExportTask(TembaModel):
    """
    Base class for export task models, i.e. contacts, messages and flow results
    """

    analytics_key = None
    asset_type = None

    MAX_EXCEL_ROWS = 1_048_576
    MAX_EXCEL_COLS = 16384

    WIDTH_SMALL = 15
    WIDTH_MEDIUM = 20
    WIDTH_LARGE = 100

    STATUS_PENDING = "P"
    STATUS_PROCESSING = "O"
    STATUS_COMPLETE = "C"
    STATUS_FAILED = "F"
    STATUS_CHOICES = (
        (STATUS_PENDING, _("Pending")),
        (STATUS_PROCESSING, _("Processing")),
        (STATUS_COMPLETE, _("Complete")),
        (STATUS_FAILED, _("Failed")),
    )

    # log progress after this number of exported objects have been exported
    LOG_PROGRESS_PER_ROWS = 10000

    org = models.ForeignKey(
        "orgs.Org", on_delete=models.PROTECT, related_name="%(class)ss", help_text=_("The organization of the user.")
    )

    status = models.CharField(max_length=1, default=STATUS_PENDING, choices=STATUS_CHOICES)

    def perform(self):
        """
        Performs the actual export. If export generation throws an exception it's caught here and the task is marked
        as failed.
        """
        try:
            self.update_status(self.STATUS_PROCESSING)
            print(f"Started perfoming {self.analytics_key} with ID {self.id}")

            start = time.time()

            temp_file, extension = self.write_export()

            get_asset_store(model=self.__class__).save(self.id, File(temp_file), extension)

            branding = self.org.get_branding()

            # notify user who requested this export
            send_template_email(
                self.created_by.username,
                self.email_subject % self.org.name,
                self.email_template,
                self.get_email_context(branding),
                branding,
            )

            # remove temporary file
            if hasattr(temp_file, "delete"):
                if temp_file.delete is False:  # pragma: no cover
                    os.unlink(temp_file.name)
            else:  # pragma: no cover
                os.unlink(temp_file.name)

        except Exception as e:
            logger.error(f"Unable to perform export: {str(e)}", exc_info=True)
            self.update_status(self.STATUS_FAILED)
            print(f"Failed to complete {self.analytics_key} with ID {self.id}")

            raise e  # log the error to sentry
        else:
            self.update_status(self.STATUS_COMPLETE)
            elapsed = time.time() - start
            print(f"Completed {self.analytics_key} with ID {self.id} in {elapsed:.1f} seconds")
            analytics.track(
                self.created_by.username, "temba.%s_latency" % self.analytics_key, properties=dict(value=elapsed)
            )
        finally:
            gc.collect()  # force garbage collection

    def write_export(self):  # pragma: no cover
        """
        Should return a file handle for a temporary file and the file extension
        """
        pass

    def update_status(self, status):
        self.status = status
        self.save(update_fields=("status", "modified_on"))

    @classmethod
    def get_recent_unfinished(cls, org):
        """
        Checks for unfinished exports created in the last 24 hours for this org, and returns the most recent
        """
        return (
            cls.objects.filter(
                org=org,
                created_on__gt=timezone.now() - timedelta(hours=24),
                status__in=(cls.STATUS_PENDING, cls.STATUS_PROCESSING),
            )
            .order_by("-created_on")
            .first()
        )

    def append_row(self, sheet, values):
        sheet.append_row(*[self.prepare_value(v) for v in values])

    def prepare_value(self, value):
        if value is None:
            return ""
        elif isinstance(value, str):
            if value.startswith("="):  # escape = so value isn't mistaken for a formula
                value = "'" + value
            return clean_string(value)
        elif isinstance(value, datetime):
            return value.astimezone(self.org.timezone).replace(microsecond=0, tzinfo=None)
        elif isinstance(value, bool):
            return value
        else:
            return clean_string(str(value))

    def get_email_context(self, branding):
        asset_store = get_asset_store(model=self.__class__)

        return {"link": branding["link"] + asset_store.get_asset_url(self.id)}

    class Meta:
        abstract = True


class TableExporter(object):
    """
    Class that abstracts out writing a table of data to a CSV or Excel file. This only works for exports that
    have a single sheet (as CSV's don't have sheets) but takes care of writing to a CSV in the case
    where there are more than 16384 columns, which Excel doesn't support.

    When writing to an Excel sheet, this also takes care of creating different sheets every 1048576
    rows, as again, Excel file only support that many per sheet.
    """

    def __init__(self, task, sheet_name, columns):
        self.task = task
        self.columns = columns
        self.sheet_name = sheet_name

        self.current_sheet = 0
        self.current_row = 0

        self.workbook = XLSXBook()
        self.sheet_number = 0
        self._add_sheet()

    def _add_sheet(self):
        self.sheet_number += 1

        # add our sheet
        self.sheet = self.workbook.add_sheet("%s %d" % (self.sheet_name, self.sheet_number))
        self.sheet.append_row(*self.columns)

        self.sheet_row = 2

    def write_row(self, values):
        """
        Writes the passed in row to our exporter, taking care of creating new sheets if necessary
        """
        # time for a new sheet? do it
        if self.sheet_row > BaseExportTask.MAX_EXCEL_ROWS:
            self._add_sheet()

        self.sheet.append_row(*values)

        self.sheet_row += 1

    def save_file(self):
        """
        Saves our data to a file, returning the file saved to and the extension
        """
        gc.collect()  # force garbage collection

        print("Writing Excel workbook...")
        temp_file = NamedTemporaryFile(delete=False, suffix=".xlsx", mode="wb+")
        self.workbook.finalize(to_file=temp_file)
        temp_file.flush()

        return temp_file, "xlsx"
