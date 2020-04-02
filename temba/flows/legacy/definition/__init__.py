from packaging.version import Version

from .actions import *  # noqa
from .rules import *  # noqa

VERSIONS = [
    "1",
    "2",
    "3",
    "4",
    "5",
    "6",
    "7",
    "8",
    "9",
    "10",
    "10.1",
    "10.2",
    "10.3",
    "10.4",
    "11.0",
    "11.1",
    "11.2",
    "11.3",
    "11.4",
    "11.5",
    "11.6",
    "11.7",
    "11.8",
    "11.9",
    "11.10",
    "11.11",
    "11.12",
]


def get_versions_before(version_number):  # pragma: no cover
    # older flows had numeric versions, lets make sure we are dealing with strings
    version_number = Version(f"{version_number}")
    return [v for v in VERSIONS if Version(v) < version_number]


def get_versions_after(version_number):
    # older flows had numeric versions, lets make sure we are dealing with strings
    version_number = Version(f"{version_number}")
    return [v for v in VERSIONS if Version(v) > version_number]


def get_node(flow, uuid, destination_type):
    from temba.flows.models import Flow, ActionSet, RuleSet

    if not uuid or not destination_type:
        return None

    if destination_type == Flow.NODE_TYPE_RULESET:
        node = RuleSet.objects.filter(flow=flow, uuid=uuid).select_related("flow", "flow__org").first()
    else:
        node = ActionSet.objects.filter(flow=flow, uuid=uuid).select_related("flow", "flow__org").first()

    if node:
        node.flow = flow
    return node
