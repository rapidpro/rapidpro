from __future__ import print_function, unicode_literals

from django.core.management.base import BaseCommand
from temba.flows.models import Flow
from temba.orgs.models import get_current_export_version
from temba.utils import chunk_list


class Command(BaseCommand):  # pragma: no cover
    help = "Migrates all flows which are not current the latest version forward"

    def handle(self, *args, **options):
        # get all flows not at the current version
        latest_version = get_current_export_version()
        flows_to_migrate = Flow.objects.filter(is_active=True).exclude(version_number=latest_version)

        flow_ids = list(flows_to_migrate.values_list('id', flat=True))
        total = len(flow_ids)

        if not total:
            print("All flows up to date")
            return

        print("Found %d flows to migrate to %s..." % (len(flow_ids), latest_version))

        num_updated = 0
        num_errored = 0

        for id_batch in chunk_list(flow_ids, 1000):
            for flow in Flow.objects.filter(id__in=id_batch):
                try:
                    flow.ensure_current_version()
                    num_updated += 1
                except Exception:
                    print("Unable to migrate flow '%s' (#%d)" % (flow.name, flow.id))
                    num_errored += 1

            print("> Flows migrated: %d of %d (%d errored)" % (num_updated, total, num_errored))
