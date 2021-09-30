import gzip

from django.core.management.base import BaseCommand, CommandError

from temba.archives.models import Archive
from temba.orgs.models import Org
from temba.utils import s3

# if an archive has a record longer than this, select_object_content throws a OverMaxRecordSize error
S3_RECORD_MAX_CHARS = 1_048_576


class Command(BaseCommand):  # pragma: no cover
    help = "Audits archives"

    def add_arguments(self, parser):
        parser.add_argument("org_id", help="ID of the org whose archives will be audited")
        parser.add_argument(
            "archive_type", choices=[Archive.TYPE_MSG, Archive.TYPE_FLOWRUN], help="The type of archives to audit"
        )

    def handle(self, org_id, archive_type, **options):
        org = Org.objects.filter(id=org_id).first()
        if not org:
            raise CommandError(f"No such org with id {org_id}")

        self.stdout.write(f"Auditing archives for org '{org.name}'...")

        s3_client = s3.client()

        for archive in Archive._get_covering_period(org, archive_type):
            location = archive.s3_location()
            s3_obj = s3_client.get_object(**location)
            stream = gzip.GzipFile(fileobj=s3_obj["Body"])

            num_records = 0
            num_too_long = 0
            total_chars = 0

            while True:
                line = stream.readline()
                if not line:
                    break

                num_records += 1
                record = line.decode("utf-8")
                if len(record) > S3_RECORD_MAX_CHARS:
                    num_too_long += 1
                total_chars += len(record)

            self.stdout.write(
                f" > id={archive.id} start_date={archive.start_date.isoformat()} "
                f"bucket={location['Bucket']} key={location['Key']} "
                f"records={num_records} num_too_long={num_too_long} "
                f"avg_chars={total_chars // num_records}"
            )

            if archive.record_count != num_records:
                self.stdout.write(f"   ! record count mismatch, db={archive.record_count} file={num_records}")
