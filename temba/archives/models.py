import gzip
from datetime import date, datetime
from gettext import gettext as _
from urllib.parse import urlparse

from dateutil.relativedelta import relativedelta

from django.conf import settings
from django.db import models
from django.db.models import Q
from django.utils import timezone

from temba.utils import json, s3, sizeof_fmt
from temba.utils.s3 import EventStreamReader


class Archive(models.Model):
    DOWNLOAD_EXPIRES = 60 * 60 * 24  # Up to 24 hours

    TYPE_MSG = "message"
    TYPE_FLOWRUN = "run"
    TYPE_CHOICES = ((TYPE_MSG, _("Message")), (TYPE_FLOWRUN, _("Run")))

    PERIOD_DAILY = "D"
    PERIOD_MONTHLY = "M"
    PERIOD_CHOICES = ((PERIOD_DAILY, "Day"), (PERIOD_MONTHLY, "Month"))

    org = models.ForeignKey("orgs.Org", related_name="archives", on_delete=models.PROTECT)

    archive_type = models.CharField(choices=TYPE_CHOICES, max_length=16)

    created_on = models.DateTimeField(default=timezone.now)

    # the length of time this archive covers
    period = models.CharField(max_length=1, choices=PERIOD_CHOICES, default=PERIOD_DAILY)

    # the earliest modified_on date for records in this archive (inclusive)
    start_date = models.DateField()

    # number of records in this archive
    record_count = models.IntegerField(default=0)

    # size in bytes of the archive contents (after compression)
    size = models.BigIntegerField(default=0)

    # MD5 hash of the archive contents (after compression)
    hash = models.TextField()

    # full URL of this archive
    url = models.URLField()

    # whether the records in this archive need to be deleted
    needs_deletion = models.BooleanField(default=False)

    # number of milliseconds it took to build and upload this archive
    build_time = models.IntegerField()

    # archive we were rolled up into, if any
    rollup = models.ForeignKey("archives.Archive", on_delete=models.PROTECT, null=True)

    # when this archive's records where deleted (if any)
    deleted_on = models.DateTimeField(null=True)

    def size_display(self):
        return sizeof_fmt(self.size)

    def s3_location(self):
        url_parts = urlparse(self.url)
        return dict(Bucket=url_parts.netloc.split(".")[0], Key=url_parts.path[1:])

    def get_end_date(self):
        """
        Gets the date this archive ends non-inclusive
        """
        if self.period == Archive.PERIOD_DAILY:
            return self.start_date + relativedelta(days=1)
        else:
            return self.start_date + relativedelta(months=1)

    @classmethod
    def release_org_archives(cls, org):
        """
        Deletes all the archives for an org, also iterating any remaining files in S3 and removing that path
        as well.
        """
        # release all of our archives in turn
        for archive in Archive.objects.filter(org=org):
            archive.release()

        # find any remaining S3 files and remove them for this org
        s3_client = s3.client()
        archive_files = s3_client.list_objects_v2(Bucket=settings.ARCHIVE_BUCKET, Prefix=f"{org.id}/").get(
            "Contents", []
        )
        for archive_file in archive_files:
            s3_client.delete_object(Bucket=settings.ARCHIVE_BUCKET, Key=archive_file["Key"])

    def filename(self):
        url_parts = urlparse(self.url)
        return url_parts.path.split("/")[-1]

    def get_download_link(self):
        if self.url:
            s3_client = s3.client()
            s3_params = {
                **self.s3_location(),
                # force browser to download and not uncompress our gzipped files
                "ResponseContentDisposition": "attachment;",
                "ResponseContentType": "application/octet",
                "ResponseContentEncoding": "none",
            }

            return s3_client.generate_presigned_url("get_object", Params=s3_params, ExpiresIn=Archive.DOWNLOAD_EXPIRES)
        else:
            return ""

    @classmethod
    def _get_covering_period(cls, org, archive_type: str, after: datetime = None, before: datetime = None):
        """
        Gets the archives which cover the given period, which may include records outside of that period
        """
        archives = org.archives.filter(archive_type=archive_type, record_count__gt=0, rollup=None)

        if after:
            earliest_day = after.date()
            earliest_month = date(earliest_day.year, earliest_day.month, 1)

            archives = archives.filter(
                Q(period=cls.PERIOD_MONTHLY, start_date__gte=earliest_month)
                | Q(period=cls.PERIOD_DAILY, start_date__gte=earliest_day)
            )

        if before:
            latest_day = before.date()
            latest_month = date(latest_day.year, latest_day.month, 1)

            archives = archives.filter(
                Q(period=cls.PERIOD_MONTHLY, start_date__lte=latest_month)
                | Q(period=cls.PERIOD_DAILY, start_date__lte=latest_day)
            )

        return archives.order_by("start_date")

    @classmethod
    def iter_all_records(
        cls,
        org,
        archive_type: str,
        after: datetime = None,
        before: datetime = None,
        where: dict = None,
        raw_where: str = None,
    ):
        """
        Creates a record iterator across archives of the given type for records which match the given criteria

        Expression should be SQL with s prefix for fields, e.g. s.direction = 'in' AND s.type = 'flow'
        """

        if not where:
            where = {}
        if after:
            where["created_on__gte"] = after
        if before:
            where["created_on__lte"] = before

        archives = cls._get_covering_period(org, archive_type, after, before)

        def generator():
            for archive in archives:
                for record in archive.iter_records(where=where, raw_where=raw_where):
                    yield record

        return generator()

    def iter_records(self, *, where: dict = None, raw_where: str = None):
        """
        Creates an iterator for the records in this archive, streaming and decompressing on the fly
        """

        s3_client = s3.client()

        def generator():
            if where or raw_where:
                response = s3_client.select_object_content(
                    **self.s3_location(),
                    ExpressionType="SQL",
                    Expression=s3.compile_select(where=where, raw_where=raw_where),
                    InputSerialization={"CompressionType": "GZIP", "JSON": {"Type": "LINES"}},
                    OutputSerialization={"JSON": {"RecordDelimiter": "\n"}},
                )

                for record in EventStreamReader(response["Payload"]):
                    yield record
            else:
                s3_obj = s3_client.get_object(**self.s3_location())
                stream = gzip.GzipFile(fileobj=s3_obj["Body"])

                while True:
                    line = stream.readline()
                    if not line:
                        break

                    yield json.loads(line.decode("utf-8"))

        return generator()

    def release(self):

        # detach us from our rollups
        Archive.objects.filter(rollup=self).update(rollup=None)

        # delete our archive file from s3
        if self.url:
            s3.client().delete_object(**self.s3_location())

        # and lastly delete ourselves
        self.delete()

    class Meta:
        unique_together = ("org", "archive_type", "start_date", "period")
