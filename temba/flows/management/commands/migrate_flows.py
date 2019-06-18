import traceback

from django.core.management.base import BaseCommand

from temba.flows.models import Flow
from temba.utils import chunk_list


def migrate_flows(min_version=None):  # pragma: no cover
    to_version = min_version or Flow.FINAL_LEGACY_VERSION

    # get all flows below the min version
    old_versions = Flow.get_versions_before(to_version)

    flows_to_migrate = Flow.objects.filter(is_active=True, version_number__in=old_versions)

    flow_ids = list(flows_to_migrate.values_list("id", flat=True))
    total = len(flow_ids)

    if not total:
        print("All flows up to date")
        return True

    print(f"Found {len(flow_ids)} flows to migrate to {to_version}...")

    num_updated = 0
    num_errored = 0

    for id_batch in chunk_list(flow_ids, 5000):
        for flow in Flow.objects.filter(id__in=id_batch):
            try:
                flow.ensure_current_version(min_version=to_version)
                num_updated += 1
            except Exception:
                print(
                    f"Unable to migrate flow[uuid={str(flow.uuid)} name={flow.name} created_on={flow.created_on.isoformat()}]':"
                )
                print(traceback.format_exc())
                num_errored += 1

        print(f" > Flows migrated: {num_updated} of {total} ({num_errored} errored)")

    return num_errored == 0


class Command(BaseCommand):  # pragma: no cover
    help = "Migrates all flows which are not current the latest version forward"

    def handle(self, *args, **options):
        migrate_flows()
