from .actions import *  # noqa
from .rules import *  # noqa


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
