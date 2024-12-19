# Generated by Django 5.1.2 on 2024-11-27 14:42

import itertools

from django.db import migrations, transaction
from django.db.models import Q, Sum


def backfill_new_counts(apps, schema_editor):  # pragma: no cover
    Flow = apps.get_model("flows", "Flow")

    flow_ids = list(Flow.objects.filter(is_active=True).order_by("id").values_list("id", flat=True))

    print(f"Updating node and status counts for {len(flow_ids)} flows...")

    num_backfilled = 0

    for id_batch in itertools.batched(flow_ids, 500):
        flows = Flow.objects.filter(id__in=id_batch).only("id").order_by("id")
        for flow in flows:
            backfill_for_flow(apps, flow)

        num_backfilled += len(flows)
        print(f"> updated counts for {num_backfilled} of {len(flow_ids)} flows")


def backfill_for_flow(apps, flow) -> int:  # pragma: no cover
    FlowActivityCount = apps.get_model("flows", "FlowActivityCount")

    with transaction.atomic():
        to_create = []

        def add_count(scope: str, count: int):
            if count > 0:
                to_create.append(FlowActivityCount(flow=flow, scope=scope, count=count, is_squashed=True))

        by_node = flow.node_counts.values("node_uuid").annotate(total=Sum("count"))
        for count in by_node:
            add_count(f"node:{count['node_uuid']}", count["total"])

        by_status = flow.status_counts.values("status").annotate(total=Sum("count"))
        for count in by_status:
            add_count(f"status:{count['status']}", count["total"])

        flow.counts.filter(Q(scope__startswith="status:") | Q(scope__startswith="node:")).delete()
        FlowActivityCount.objects.bulk_create(to_create)
        return len(to_create)


def apply_manual():  # pragma: no cover
    from django.apps import apps

    backfill_new_counts(apps, None)


class Migration(migrations.Migration):

    dependencies = [("flows", "0342_update_triggers")]

    operations = [migrations.RunPython(backfill_new_counts, migrations.RunPython.noop)]
