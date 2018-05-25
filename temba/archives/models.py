from gettext import gettext as _
from urllib.parse import urlparse

import boto3
from django.conf import settings
from django.db import models
from django.utils import timezone

from temba.utils import sizeof_fmt


class Archive(models.Model):
    DOWNLOAD_EXPIRES = 600
    TYPE_MSG = "messages"
    TYPE_FLOWRUN = "runs"

    PERIOD_MONTHLY = "monthly"
    PERIOD_DAILY = "daily"

    TYPE_CHOICES = ((TYPE_MSG, _("Message")), (TYPE_FLOWRUN, _("Run")))

    DAY = "D"
    MONTH = "M"

    PERIOD_CHOICES = ((DAY, "Day"), (MONTH, "Month"))

    org = models.ForeignKey("orgs.Org", db_constraint=False, help_text="The org this archive is for")
    archive_type = models.CharField(
        choices=TYPE_CHOICES, max_length=16, help_text="The type of record this is an archive for"
    )
    created_on = models.DateTimeField(default=timezone.now, help_text="When this archive was created")
    period = models.CharField(
        max_length=1, choices=PERIOD_CHOICES, default=DAY, help_text="The length of time this archive covers"
    )

    start_date = models.DateField(help_text="The starting modified_on date for records in this archive (inclusive")

    record_count = models.IntegerField(default=0, help_text="The number of records in this archive")

    size = models.BigIntegerField(default=0, help_text="The size of this archive in bytes (after gzipping)")
    hash = models.TextField(help_text="The md5 hash of this archive (after gzipping)")
    url = models.URLField(help_text="The full URL for this archive")

    is_purged = models.BooleanField(
        default=False, help_text="Whether the records in this archive have been purged from the database"
    )
    build_time = models.IntegerField(help_text="The number of milliseconds it took to build and upload this archive")

    rollup = models.ForeignKey(
        "archives.Archive",
        null=True,
        on_delete=models.SET_NULL,
        help_text=_("The archive we were rolled up into, if any"),
    )

    def archive_size_display(self):
        return sizeof_fmt(self.size)

    def get_s3_location(self):
        url_parts = urlparse(self.url)
        return dict(Bucket=url_parts.netloc.split(".")[0], Key=url_parts.path[1:])

    def get_download_link(self):
        session = boto3.Session(
            aws_access_key_id=settings.AWS_ACCESS_KEY_ID, aws_secret_access_key=settings.AWS_SECRET_ACCESS_KEY
        )
        s3 = session.client("s3")

        s3_params = {
            **self.get_s3_location(),
            # force browser to download and not uncompress our gzipped files
            "ResponseContentDisposition": "attachment;",
            "ResponseContentType": "application/octet",
            "ResponseContentEncoding": "none",
        }

        return s3.generate_presigned_url("get_object", Params=s3_params, ExpiresIn=Archive.DOWNLOAD_EXPIRES)
