from copy import deepcopy
from difflib import unified_diff

from django.core.management.base import BaseCommand, CommandError

from temba.orgs.models import Org
from temba.utils import json


def remove_invalid_translations(definition: dict):
    """
    Removes translations of things that users shouldn't be able to localize and can't from the editor
    """
    localization = definition.get("localization", {})
    ui_nodes = definition.get("_ui", {}).get("nodes", {})

    def remove_from_localization(item_uuid: str, key: str):
        for lang, trans in localization.items():
            item_trans = trans.get(item_uuid)
            if item_trans and key in item_trans:
                del item_trans[key]

    for node in definition.get("nodes", []):
        ui_node_type = ui_nodes.get(node["uuid"], {}).get("type")
        if ui_node_type in ("split_by_webhook", "split_by_subflow"):
            for category in node["router"]["categories"]:
                remove_from_localization(category["uuid"], "name")
            for caze in node["router"]["cases"]:
                remove_from_localization(caze["uuid"], "arguments")


fixers = [
    remove_invalid_translations,
]


class Command(BaseCommand):
    help = "Fixes problems in flows"

    def add_arguments(self, parser):
        parser.add_argument(type=int, action="store", dest="org_id", help="ID of org to fix flows for")
        parser.add_argument("--preview", action="store_true", dest="preview", help="Just preview changes")

    def handle(self, org_id: int, preview: bool, *args, **options):
        org = Org.objects.filter(id=org_id).first()
        if not org:
            raise CommandError(f"no such org with id {org_id}")

        self.stdout.write(f"Fixing flows for org '{org.name}'...")

        num_fixed = 0
        for flow in org.flows.filter(is_active=True):
            if self.fix_flow(flow, preview):
                num_fixed += 1

        self.stdout.write(f" > fixed {num_fixed} flows")

    def fix_flow(self, flow, preview: bool) -> bool:
        original = flow.get_definition()
        definition = deepcopy(original)

        for fixer in fixers:
            fixer(definition)

        old_lines = json.dumps(original, indent=2).splitlines(keepends=True)
        new_lines = json.dumps(definition, indent=2).splitlines(keepends=True)
        diff_lines = list(unified_diff(old_lines, new_lines, fromfile="original", tofile="fixed"))

        if diff_lines:
            for line in diff_lines:
                self.stdout.write(line, ending="")

            if not preview:
                new_rev, issues = flow.save_revision(None, definition)
                self.stdout.write(f" > new revision ({new_rev.revision}) saved for flow '{flow.name}'")
            return True
        else:
            return False
