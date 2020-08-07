from django.core.management.base import BaseCommand, CommandError

from temba.archives.models import Archive
from temba.orgs.models import Org
from temba.utils import json


class Command(BaseCommand):
    help = "Searches through archives"

    def add_arguments(self, parser):
        parser.add_argument("org_id", help="ID of the org whose archives will be searched")
        parser.add_argument("archive_type", choices=[Archive.TYPE_MSG, Archive.TYPE_FLOWRUN])
        parser.add_argument("--expression", type=str, action="store", dest="expression", default="")
        parser.add_argument("--limit", type=int, action="store", dest="limit", default=10)

    def handle(self, org_id, archive_type, expression, limit, **options):
        org = Org.objects.filter(id=org_id).first()
        if not org:
            raise CommandError(f"No such org with id {org_id}")

        records = Archive.iter_all_records(org, archive_type, expression=expression)

        num_records = 0
        for record in records:
            self.stdout.write(json.dumps(record, indent=2))

            num_records += 1
            if num_records == limit:
                break
