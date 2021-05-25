from django.core.management.base import BaseCommand

from temba import mailroom
from temba.flows.models import Flow


def inspect_flows():
    client = mailroom.get_client()

    num_inspected = 0
    num_updated = 0

    for flow in Flow.objects.filter(is_active=True).order_by("id"):
        flow_info = client.flow_inspect(flow.org_id, flow.get_definition())
        has_issues = len(flow_info["issues"]) > 0
        num_inspected += 1

        if has_issues != flow.has_issues:
            flow.has_issues = has_issues
            flow.save(update_fields=("has_issues",))
            num_updated += 1

        if num_inspected % 100 == 0:  # pragma: no cover
            print(f" > Flows inspected: {num_inspected}, updated: {num_updated}")

    print(f"Total flows inspected: {num_inspected}, updated: {num_updated}")


class Command(BaseCommand):
    help = "Inspects all flows for issues"

    def handle(self, *args, **options):
        inspect_flows()
