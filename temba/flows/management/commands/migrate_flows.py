import traceback

from django.core.management.base import BaseCommand

from temba.flows.models import Flow
from temba.utils import chunk_list


class Command(BaseCommand):
    help = "Migrates forward all flows which are not current version"

    def handle(self, *args, **options):
        self.migrate_flows()

    def migrate_flows(self):
        flows_to_migrate = (
            Flow.objects.filter(is_active=True)
            .exclude(version_number=Flow.CURRENT_SPEC_VERSION)
            .order_by("org_id", "id")
        )
        flow_ids = list(flows_to_migrate.values_list("id", flat=True))
        total = len(flow_ids)

        if total == 0:
            self.stdout.write("All flows up to date")
            return

        self.stdout.write(f"Found {len(flow_ids)} flows to migrate...")

        num_updated = 0
        num_errored = 0

        for id_batch in chunk_list(flow_ids, 1000):
            for flow in Flow.objects.filter(id__in=id_batch):
                try:
                    flow.ensure_current_version()
                    num_updated += 1
                except Exception:
                    self.stderr.write(f"Unable to migrate flow {str(flow.uuid)}:")
                    self.stderr.write(traceback.format_exc())
                    num_errored += 1

            self.stdout.write(f" > Flows migrated: {num_updated} of {total} ({num_errored} errored)")
