# -*- coding: utf-8 -*-
from __future__ import absolute_import, division, print_function, unicode_literals

from django.core.management.base import BaseCommand
from temba.flows.models import Flow
from temba.orgs.models import get_current_export_version
from temba.utils import chunk_list


def migrate_flows(min_version=None):  # pragma: no cover
    to_version = min_version or get_current_export_version()

    # get all flows below the min version
    old_versions = Flow.get_versions_before(to_version)

    flows_to_migrate = Flow.objects.filter(is_active=True, version_number__in=old_versions)

    flow_ids = list(flows_to_migrate.values_list('id', flat=True))
    total = len(flow_ids)

    if not total:
        print("All flows up to date")
        return True

    print("Found %d flows to migrate to %s..." % (len(flow_ids), to_version))

    num_updated = 0
    errored = []

    for id_batch in chunk_list(flow_ids, 1000):
        for flow in Flow.objects.filter(id__in=id_batch):
            try:
                flow.ensure_current_version(min_version=to_version)
                num_updated += 1
            except Exception:
                print("Unable to migrate flow '%s' (#%d)" % (flow.name, flow.id))
                errored.append(flow)

        print(" > Flows migrated: %d of %d (%d errored)" % (num_updated, total, len(errored)))

    if errored:
        print(" > Errored flows: %s" % (", ".join([str(e.id) for e in errored])))

    return len(errored) == 0


class Command(BaseCommand):  # pragma: no cover
    help = "Migrates all flows which are not current the latest version forward"

    def handle(self, *args, **options):
        migrate_flows()
