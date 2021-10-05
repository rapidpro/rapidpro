import gzip

from django.core.management.base import BaseCommand, CommandError

from temba.archives.models import Archive
from temba.orgs.models import Org
from temba.utils import s3

# if an archive has a record longer than this, select_object_content throws a OverMaxRecordSize error
S3_RECORD_MAX_CHARS = 1_048_576

MAX_PATH_LEN = 500


class Command(BaseCommand):  # pragma: no cover
    help = "Audits archives"

    def add_arguments(self, parser):
        parser.add_argument("org_id", help="ID of the org whose archives will be audited")
        parser.add_argument(
            "archive_type", choices=[Archive.TYPE_MSG, Archive.TYPE_FLOWRUN], help="The type of archives to audit"
        )
        parser.add_argument("--fix", action="store_true", help="Fix archives with records that are too long")

    def handle(self, org_id, archive_type, fix, **options):
        org = Org.objects.filter(id=org_id).first()
        if not org:
            raise CommandError(f"No such org with id {org_id}")

        self.stdout.write(f"Auditing archives for org '{org.name}'...")

        s3_client = s3.client()

        for archive in Archive._get_covering_period(org, archive_type):
            bucket, key = archive.get_storage_location()
            s3_obj = s3_client.get_object(Bucket=bucket, Key=key)
            stream = gzip.GzipFile(fileobj=s3_obj["Body"])

            num_records = 0
            num_too_long = 0
            total_chars = 0

            for line in stream:
                num_records += 1
                record = line.decode("utf-8")
                if len(record) > S3_RECORD_MAX_CHARS:
                    num_too_long += 1
                total_chars += len(record)

            self.stdout.write(
                f" > id={archive.id} start_date={archive.start_date.isoformat()} bucket={bucket} key={key} "
                f"records={num_records} num_too_long={num_too_long} avg_chars={total_chars // num_records}"
            )

            if archive.record_count != num_records:
                self.stdout.write(f"   ⚠️ record count mismatch, db={archive.record_count} file={num_records}")

            if num_too_long > 0 and archive_type == Archive.TYPE_FLOWRUN and fix:
                self.fix_run_archive(archive)

    def fix_run_archive(self, archive):
        self.stdout.write("    🔧 fixing run archive...")

        bucket, key = archive.get_storage_location()

        progress = {"records": 0}

        def trim_path(record) -> dict:
            record["path"] = record["path"][:MAX_PATH_LEN]
            progress["records"] += 1

            if progress["records"] % 100_000 == 0:
                percent = 100 * progress["records"] // archive.record_count
                self.stdout.write(f"    ⏳ {percent}%")

            return record

        archive.rewrite(trim_path, delete_old=True)
        bucket, new_key = archive.get_storage_location()

        self.stdout.write(f"    ✅️ {key} replaced by {new_key}")
