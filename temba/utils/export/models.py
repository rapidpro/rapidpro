import gc
import logging
import os
import time
from datetime import datetime, timedelta

from smartmin.models import SmartModel
from xlsxlite.writer import XLSXBook

from django.core.files import File
from django.core.files.temp import NamedTemporaryFile
from django.db import models
from django.http import HttpResponse
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from temba.assets.models import BaseAssetStore, get_asset_store
from temba.utils import analytics
from temba.utils.models import TembaUUIDMixin
from temba.utils.text import clean_string

logger = logging.getLogger(__name__)


class BaseExportAssetStore(BaseAssetStore):
    def is_asset_ready(self, asset):
        return asset.status == BaseExport.STATUS_COMPLETE


class BaseExport(TembaUUIDMixin, SmartModel):
    """
    Base class for export task models, i.e. contacts, messages and flow results
    """

    analytics_key = None
    asset_type = None
    notification_export_type = None

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

    org = models.ForeignKey("orgs.Org", on_delete=models.PROTECT, related_name="%(class)ss")
    status = models.CharField(max_length=1, default=STATUS_PENDING, choices=STATUS_CHOICES)
    num_records = models.IntegerField(null=True)

    def perform(self):
        """
        Performs the actual export. If export generation throws an exception it's caught here and the task is marked
        as failed.
        """
        from temba.notifications.types.builtin import ExportFinishedNotificationType

        try:
            self.update_status(self.STATUS_PROCESSING)

            print(f"Started performing {self.analytics_key} with ID {self.id}")

            start = time.time()

            temp_file, extension, num_records = self.write_export()

            get_asset_store(model=self.__class__).save(self.id, File(temp_file), extension)

            # remove temporary file
            if hasattr(temp_file, "delete"):
                if temp_file.delete is False:  # pragma: no cover
                    os.unlink(temp_file.name)
            else:  # pragma: no cover
                os.unlink(temp_file.name)

        except Exception as e:  # pragma: no cover
            logger.error(f"Unable to perform export: {str(e)}", exc_info=True)
            self.update_status(self.STATUS_FAILED)
            print(f"Failed to complete {self.analytics_key} with ID {self.id}")

            raise e  # log the error to sentry
        else:
            self.update_status(self.STATUS_COMPLETE, num_records)
            elapsed = time.time() - start
            print(f"Completed {self.analytics_key} with ID {self.id} in {elapsed:.1f} seconds")
            analytics.track(self.created_by, "temba.%s_latency" % self.analytics_key, properties=dict(value=elapsed))

            ExportFinishedNotificationType.create(self)
        finally:
            gc.collect()  # force garbage collection

    def write_export(self):  # pragma: no cover
        """
        Should return 1) file handle for a temporary file, 2) file extension, 3) count of items exported
        """
        pass

    def update_status(self, status: str, num_records: int = None):
        self.status = status
        self.num_records = num_records
        self.save(update_fields=("status", "num_records", "modified_on"))

    @classmethod
    def get_unfinished(cls):
        """
        Returns all unfinished exports
        """
        return cls.objects.filter(status__in=(cls.STATUS_PENDING, cls.STATUS_PROCESSING))

    @classmethod
    def get_recent_unfinished(cls, org):
        """
        Checks for unfinished exports created in the last 4 hours for this org, and returns the most recent
        """

        day_ago = timezone.now() - timedelta(hours=4)

        return cls.get_unfinished().filter(org=org, created_on__gt=day_ago).order_by("created_on").last()

    def append_row(self, sheet, values):
        sheet.append_row(*[prepare_value(v, self.org.timezone) for v in values])

    def get_download_url(self) -> str:
        asset_store = get_asset_store(model=self.__class__)
        return asset_store.get_asset_url(self.id)

    def get_notification_scope(self) -> str:
        return f"{self.notification_export_type}:{self.id}"

    class Meta:
        abstract = True


class BaseDateRangeExport(BaseExport):
    """
    Base export class for exports that have a date range.
    """

    start_date = models.DateField()
    end_date = models.DateField()

    def _get_date_range(self) -> tuple:
        """
        Gets the since > until datetimes of items to export.
        """
        tz = self.org.timezone
        return (
            max(datetime.combine(self.start_date, datetime.min.time()).replace(tzinfo=tz), self.org.created_on),
            datetime.combine(self.end_date, datetime.max.time()).replace(tzinfo=tz),
        )

    class Meta:
        abstract = True


class BaseItemWithContactExport(BaseDateRangeExport):
    """
    Base export class for exports that are an item with an associated contact.
    """

    with_fields = models.ManyToManyField("contacts.ContactField", related_name="%(class)s_exports")
    with_groups = models.ManyToManyField("contacts.ContactGroup", related_name="%(class)s_exports")

    def _get_contact_headers(self) -> list:
        """
        Gets the header values common to exports with contacts.
        """
        cols = ["Contact UUID", "Contact Name", "URN Scheme"]
        if self.org.is_anon:
            cols.append("Anon Value")
        else:
            cols.append("URN Value")

        for cf in self.with_fields.all():
            cols.append("Field:%s" % cf.name)

        for cg in self.with_groups.all():
            cols.append("Group:%s" % cg.name)

        return cols

    def _get_contact_columns(self, contact, urn: str = "") -> list:
        """
        Gets the column values for the given contact.
        """
        from temba.contacts.models import URN

        if urn == "":
            urn_obj = contact.get_urn()
            urn_scheme, urn_path = (urn_obj.scheme, urn_obj.path) if urn_obj else (None, None)
        elif urn is not None:
            urn_scheme = URN.to_parts(urn)[0]
            urn_path = URN.format(urn, international=False, formatted=False)
        else:
            urn_scheme, urn_path = None, None

        cols = [str(contact.uuid), contact.name, urn_scheme]
        if self.org.is_anon:
            cols.append(contact.anon_display)
        else:
            cols.append(urn_path)

        for cf in self.with_fields.all():
            cols.append(contact.get_field_display(cf))

        memberships = set(contact.groups.all())

        for cg in self.with_groups.all():
            cols.append(cg in memberships)

        return cols

    class Meta:
        abstract = True


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
        if self.sheet_row > BaseExport.MAX_EXCEL_ROWS:
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
