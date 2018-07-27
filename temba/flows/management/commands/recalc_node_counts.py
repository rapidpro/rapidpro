from collections import defaultdict

from django.core.management.base import BaseCommand
from django.db import transaction

from temba.flows.models import Flow, FlowNodeCount


def recalc_node_counts(flow):
    node_counts = defaultdict(int)

    all_runs = (
        flow.runs.filter(is_active=True).exclude(current_node_uuid=None).only("id", "current_node_uuid").order_by("id")
    )
    max_id = 0

    while True:
        batch = list(all_runs.filter(id__gt=max_id)[:1000])
        if not batch:
            break
        max_id = batch[-1].id

        for run in batch:
            node_counts[run.current_node_uuid] += 1

    records = []
    for node_uuid, count in node_counts.items():
        records.append(FlowNodeCount(flow=flow, node_uuid=node_uuid, count=count))

    with transaction.atomic():
        FlowNodeCount.objects.filter(flow=flow).delete()
        FlowNodeCount.objects.bulk_create(records)


class Command(BaseCommand):
    help = "Re-calculates node counts for a flow"

    def add_arguments(self, parser):
        parser.add_argument("--flow", type=int, action="store", dest="flow_id", help="ID of flow to fix")

    def handle(self, flow_id, *args, **options):
        flow = Flow.objects.get(id=flow_id)

        print(f"Re-calculating flow node counts for '{flow.name}' (#{flow.id})...")

        recalc_node_counts(flow)
