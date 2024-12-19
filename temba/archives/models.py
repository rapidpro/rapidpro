import base64
import gzip
import hashlib
import io
import re
import tempfile
from datetime import date, datetime
from gettext import gettext as _

from dateutil.relativedelta import relativedelta

from django.core.files.storage import storages
from django.db import models
from django.db.models import Q
from django.utils import timezone

from temba.utils import json, s3
from temba.utils.s3 import EventStreamReader

KEY_PATTERN = re.compile(r"^(?P<org>\d+)/(?P<type>run|message)_(?P<period>(D|M)\d+)_(?P<hash>[0-9a-f]{32})\.jsonl\.gz$")


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

    period = models.CharField(max_length=1, choices=PERIOD_CHOICES, default=PERIOD_DAILY)
    start_date = models.DateField()  # the earliest modified_on date for records (inclusive)
    record_count = models.IntegerField(default=0)  # number of records in this archive
    size = models.BigIntegerField(default=0)  # size in bytes of the archive contents (after compression)
    hash = models.TextField()  # MD5 hash of the archive contents (after compression)
    url = models.URLField()  # full URL of this archive
    build_time = models.IntegerField()  # time in ms it took to build and upload this archive

    # archive we were rolled up into, if any
    rollup = models.ForeignKey("archives.Archive", on_delete=models.PROTECT, null=True)

    # whether the records in this archive need to be deleted
    needs_deletion = models.BooleanField(default=False)

    # when this archive's records where deleted (if any)
    deleted_on = models.DateTimeField(null=True)

    @classmethod
    def storage(cls):
        return storages["archives"]

    def get_storage_location(self) -> tuple:
        """
        Returns a tuple of the storage bucket and key
        """
        return s3.split_url(self.url)

    def get_end_date(self):
        """
        Gets the date this archive ends non-inclusive
        """
        if self.period == Archive.PERIOD_DAILY:
            return self.start_date + relativedelta(days=1)
        else:
            return self.start_date + relativedelta(months=1)

    @classmethod
    def delete_for_org(cls, org):
        """
        Deletes all the archives for an org and any additional archive files in storage.
        """

        for archive in Archive.objects.filter(org=org):
            archive.delete()

        # find any remaining S3 files and remove them for this org
        s3_bucket = cls.storage().bucket.name
        s3_client = s3.client()

        paginator = s3_client.get_paginator("list_objects_v2")
        for page in paginator.paginate(Bucket=s3_bucket, Prefix=f"{org.id}/"):
            archive_objs = page.get("Contents", [])
            if archive_objs:
                s3_client.delete_objects(
                    Bucket=s3_bucket, Delete={"Objects": [{"Key": o["Key"]} for o in archive_objs]}
                )

    def get_download_link(self):
        if self.url:
            s3_client = s3.client()
            bucket, key = self.get_storage_location()
            s3_params = {
                "Bucket": bucket,
                "Key": key,
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
        cls, org, archive_type: str, after: datetime = None, before: datetime = None, where: dict = None
    ):
        """
        Creates a record iterator across archives of the given type for records which match the given criteria
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
                for record in archive.iter_records(where=where):
                    yield record

        return generator()

    def iter_records(self, *, where: dict = None):
        """
        Creates an iterator for the records in this archive, streaming and decompressing on the fly
        """

        s3_client = s3.client()

        if where:
            bucket, key = self.get_storage_location()
            response = s3_client.select_object_content(
                Bucket=bucket,
                Key=key,
                ExpressionType="SQL",
                Expression=s3.compile_select(where=where),
                InputSerialization={"CompressionType": "GZIP", "JSON": {"Type": "LINES"}},
                OutputSerialization={"JSON": {"RecordDelimiter": "\n"}},
            )

            def generator():
                for record in EventStreamReader(response["Payload"]):
                    yield record

            return generator()

        else:
            bucket, key = self.get_storage_location()
            s3_obj = s3_client.get_object(Bucket=bucket, Key=key)
            return jsonlgz_iterate(s3_obj["Body"])

    def rewrite(self, transform, delete_old=False):
        s3_client = s3.client()
        bucket, key = self.get_storage_location()

        s3_obj = s3_client.get_object(Bucket=bucket, Key=key)
        old_file = s3_obj["Body"]

        new_file = tempfile.TemporaryFile()
        new_hash, new_size = jsonlgz_rewrite(old_file, new_file, transform)

        new_file.seek(0)

        match = KEY_PATTERN.match(key)
        new_key = f"{self.org.id}/{match.group('type')}_{match.group('period')}_{new_hash.hexdigest()}.jsonl.gz"
        new_url = f"https://{bucket}.s3.amazonaws.com/{new_key}"
        new_hash_base64 = base64.standard_b64encode(new_hash.digest()).decode()

        s3_client.put_object(
            Bucket=bucket,
            Key=new_key,
            Body=new_file,
            ContentType="application/json",
            ContentEncoding="gzip",
            ACL="private",
            ContentMD5=new_hash_base64,
            Metadata={"md5chksum": new_hash_base64},
        )

        self.url = new_url
        self.hash = new_hash.hexdigest()
        self.size = new_size
        self.save(update_fields=("url", "hash", "size"))

        if delete_old:
            s3_client.delete_object(Bucket=bucket, Key=key)

    def delete(self):
        # detach us from our rollups
        Archive.objects.filter(rollup=self).update(rollup=None)

        # delete our archive file from storage
        if self.url:
            bucket, key = self.get_storage_location()
            s3.client().delete_object(Bucket=bucket, Key=key)

        # and lastly delete ourselves
        super().delete()

    class Meta:
        unique_together = ("org", "archive_type", "start_date", "period")


def jsonlgz_iterate(in_file):
    """
    Iterates over a records in a gzipped JSONL stream
    """
    in_stream = gzip.GzipFile(fileobj=in_file, mode="r")

    def generator():
        for line in in_stream:
            record = json.loads(line.decode("utf-8"))
            yield record

    return generator()


def jsonlgz_rewrite(in_file, out_file, transform) -> tuple:
    """
    Rewrites a stream of gzipped JSONL using a transformation function and returns the new MD5 hash and size
    """
    out_wrapped = FileAndHash(out_file)
    out_stream = gzip.GzipFile(fileobj=out_wrapped, mode="w")

    for record in jsonlgz_iterate(in_file):
        record = transform(record)
        if record is not None:
            new_line = (json.dumps(record) + "\n").encode("utf-8")
            out_stream.write(new_line)

    out_stream.close()

    return out_wrapped.hash, out_wrapped.size


def jsonlgz_encode(records: list) -> tuple:
    stream = io.BytesIO()
    wrapper = FileAndHash(stream)
    gz = gzip.GzipFile(fileobj=wrapper, mode="wb")

    for record in records:
        gz.write(json.dumps(record).encode("utf-8"))
        gz.write(b"\n")
    gz.close()

    return stream, wrapper.hash.hexdigest(), wrapper.size


class FileAndHash:
    """
    Stream which writes to both a child stream and a MD5 hash
    """

    def __init__(self, f):
        self.f = f
        self.hash = hashlib.md5()
        self.size = 0

    def write(self, data):
        self.f.write(data)
        self.hash.update(data)
        self.size += len(data)

    def flush(self):  # pragma: no cover
        self.f.flush()

    def close(self):  # pragma: no cover
        self.f.close()
