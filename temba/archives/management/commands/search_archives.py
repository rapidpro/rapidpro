import time

from django.core.management.base import BaseCommand, CommandError

from temba.archives.models import Archive
from temba.orgs.models import Org
from temba.utils import json


class Command(BaseCommand):  # pragma: no cover
    help = "Searches through archives"

    def add_arguments(self, parser):
        parser.add_argument("org_id", help="ID of the org whose archives will be searched")
        parser.add_argument(
            "archive_type", choices=[Archive.TYPE_MSG, Archive.TYPE_FLOWRUN], help="The type of archives to search"
        )
        parser.add_argument(
            "--expression",
            type=str,
            action="store",
            dest="expression",
            default="",
            help="An optional S3 Select SQL expression",
        )
        parser.add_argument(
            "--limit",
            type=int,
            action="store",
            dest="limit",
            default=10,
            help="The maximum number of records to return",
        )
        parser.add_argument("--raw", action="store_true", help="Output unformatted JSONL")

    def handle(self, org_id, archive_type, expression, limit, raw, **options):
        org = Org.objects.filter(id=org_id).first()
        if not org:
            raise CommandError(f"No such org with id {org_id}")

        start = time.perf_counter()
        records = Archive.iter_all_records(org, archive_type, expression=expression)

        num_records = 0
        for record in records:
            dumps_kwargs = {} if raw else dict(indent=2)
            self.stdout.write(json.dumps(record, **dumps_kwargs))

            num_records += 1
            if num_records == limit:
                break

        time_taken = int((time.perf_counter() - start) * 1000)

        if not raw:
            self.stdout.write(f"Fetched {num_records} records in {time_taken} ms")
