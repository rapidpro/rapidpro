from django.core.management.base import BaseCommand

from temba import mailroom
from temba.flows.models import Flow
from temba.mailroom.client import FlowValidationException


def inspect_flows():
    client = mailroom.get_client()

    num_inspected = 0
    num_updated = 0
    num_invalid = 0

    for flow in Flow.objects.filter(is_active=True).order_by("id"):
        try:
            flow_info = client.flow_inspect(flow.org_id, flow.get_definition())
        except FlowValidationException:
            num_invalid += 1
            continue
        finally:
            num_inspected += 1

        has_issues = len(flow_info["issues"]) > 0

        if has_issues != flow.has_issues:
            flow.has_issues = has_issues
            flow.save(update_fields=("has_issues",))
            num_updated += 1

        if num_inspected % 100 == 0:  # pragma: no cover
            print(f" > Flows inspected: {num_inspected}, updated: {num_updated}, invalid: {num_invalid}")

    print(f"Total flows inspected: {num_inspected}, updated: {num_updated}, invalid: {num_invalid}")


class Command(BaseCommand):
    help = "Inspects all flows for issues"

    def handle(self, *args, **options):
        inspect_flows()
