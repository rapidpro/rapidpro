from django.core.management.base import BaseCommand

from temba import mailroom
from temba.flows.models import Flow


class Command(BaseCommand):
    help = "Inspects all flows for issues"

    def handle(self, *args, **options):
        client = mailroom.get_client()

        num_total = Flow.objects.filter(is_active=True).count()
        num_inspected = 0
        num_updated = 0
        num_invalid = 0

        self.stdout.write(f"Inspecting {num_total} flows...")

        for flow in Flow.objects.filter(is_active=True).order_by("org", "id"):
            definition = flow.get_definition()

            if "spec_version" in definition and definition["spec_version"] != flow.version_number:
                self.stdout.write(f" > flow {flow.uuid} has a spec version mismatch")
                num_invalid += 1
                continue

            try:
                flow_info = client.flow_inspect(flow.org, definition)
            except mailroom.FlowValidationException:
                self.stdout.write(f" > flow {flow.uuid} doesn't have a valid definition")
                num_invalid += 1
                continue
            finally:
                num_inspected += 1

            has_issues = len(flow_info["issues"]) > 0

            if has_issues != flow.has_issues:
                flow.has_issues = has_issues
                flow.save(update_fields=("has_issues",))
                num_updated += 1

            if num_inspected % 100 == 0:
                self.stdout.write(
                    f" > inspected {num_inspected}/{num_total}, updated: {num_updated}, invalid: {num_invalid}"
                )

        self.stdout.write(f"Total flows inspected: {num_inspected}, updated: {num_updated}, invalid: {num_invalid}")
