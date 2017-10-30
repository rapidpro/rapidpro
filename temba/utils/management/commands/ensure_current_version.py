from __future__ import unicode_literals

from django.core.management.base import BaseCommand
from django.db.models import Q
from temba.flows.models import Flow
from temba.orgs.models import CURRENT_EXPORT_VERSION
from temba.utils import chunk_list


class Command(BaseCommand):  # pragma: no cover
    help = "Migrates all flows which are not current the latest version forward"

    def handle(self, *args, **options):
        # all flows not current set at the current version
        flow_ids = list(Flow.objects.filter(is_active=True).filter(~Q(version_number=CURRENT_EXPORT_VERSION)).values_list('id', flat=True))
        total = len(flow_ids)
        updated = 0
        for id_batch in chunk_list(flow_ids, 1000):
            for flow in Flow.objects.filter(id__in=id_batch):
                flow.ensure_current_version()
            updated += len(id_batch)
            print("Flows Migrated: %d of %d" % (updated, total))

        if not total:
            print("All flows up to date")
