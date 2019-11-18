import gzip
from gettext import gettext as _
from urllib.parse import urlparse

import boto3
from dateutil.relativedelta import relativedelta

from django.conf import settings
from django.db import models
from django.utils import timezone

from temba.utils import json, sizeof_fmt


class Archive(models.Model):
    DOWNLOAD_EXPIRES = 60 * 60 * 24  # Up to 24 hours
    TYPE_MSG = "message"
    TYPE_FLOWRUN = "run"

    PERIOD_MONTHLY = "monthly"
    PERIOD_DAILY = "daily"

    TYPE_CHOICES = ((TYPE_MSG, _("Message")), (TYPE_FLOWRUN, _("Run")))

    PERIOD_DAILY = "D"
    PERIOD_MONTHLY = "M"

    PERIOD_CHOICES = ((PERIOD_DAILY, "Day"), (PERIOD_MONTHLY, "Month"))

    org = models.ForeignKey(
        "orgs.Org", related_name="archives", on_delete=models.PROTECT, help_text="The org this archive is for"
    )
    archive_type = models.CharField(
        choices=TYPE_CHOICES, max_length=16, help_text="The type of record this is an archive for"
    )
    created_on = models.DateTimeField(default=timezone.now, help_text="When this archive was created")
    period = models.CharField(
        max_length=1, choices=PERIOD_CHOICES, default=PERIOD_DAILY, help_text="The length of time this archive covers"
    )

    start_date = models.DateField(help_text="The starting modified_on date for records in this archive (inclusive")

    record_count = models.IntegerField(default=0, help_text="The number of records in this archive")

    size = models.BigIntegerField(default=0, help_text="The size of this archive in bytes (after gzipping)")
    hash = models.TextField(help_text="The md5 hash of this archive (after gzipping)")
    url = models.URLField(help_text="The full URL for this archive")

    needs_deletion = models.BooleanField(
        default=False, help_text="Whether the records in this archive need to be deleted"
    )
    build_time = models.IntegerField(help_text="The number of milliseconds it took to build and upload this archive")

    rollup = models.ForeignKey(
        "archives.Archive",
        on_delete=models.PROTECT,
        null=True,
        help_text=_("The archive we were rolled up into, if any"),
    )

    deleted_on = models.DateTimeField(null=True, help_text="When this archive's records where deleted (if any)")

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
    def s3_client(cls):
        session = boto3.Session(
            aws_access_key_id=settings.AWS_ACCESS_KEY_ID, aws_secret_access_key=settings.AWS_SECRET_ACCESS_KEY
        )
        return session.client("s3")

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
        s3 = cls.s3_client()
        archive_files = s3.list_objects_v2(Bucket=settings.ARCHIVE_BUCKET, Prefix=f"{org.id}/").get("Contents", [])
        for archive_file in archive_files:
            s3.delete_object(Bucket=settings.ARCHIVE_BUCKET, Key=archive_file["Key"])

    def filename(self):
        url_parts = urlparse(self.url)
        return url_parts.path.split("/")[-1]

    def get_download_link(self):
        if self.url:
            s3 = self.s3_client()
            s3_params = {
                **self.s3_location(),
                # force browser to download and not uncompress our gzipped files
                "ResponseContentDisposition": "attachment;",
                "ResponseContentType": "application/octet",
                "ResponseContentEncoding": "none",
            }

            return s3.generate_presigned_url("get_object", Params=s3_params, ExpiresIn=Archive.DOWNLOAD_EXPIRES)
        else:
            return ""

    def iter_records(self):
        """
        Creates an iterator for the records in this archive, streaming and decompressing on the fly
        """
        s3 = self.s3_client()
        s3_obj = s3.get_object(**self.s3_location())
        stream = gzip.GzipFile(fileobj=s3_obj["Body"])

        while True:
            line = stream.readline()
            if not line:
                break

            yield json.loads(line.decode("utf-8"))

    def release(self):

        # detach us from our rollups
        Archive.objects.filter(rollup=self).update(rollup=None)

        # delete our archive file from s3
        if self.url:
            s3 = self.s3_client()
            s3.delete_object(**self.s3_location())

        # and lastly delete ourselves
        self.delete()

    class Meta:
        unique_together = ("org", "archive_type", "start_date", "period")
