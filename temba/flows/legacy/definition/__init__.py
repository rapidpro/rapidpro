from .actions import *  # noqa
from .rules import *  # noqa


def get_node(flow, uuid, destination_type):
    from temba.flows.models import Flow, ActionSet, RuleSet

    if not uuid or not destination_type:
        return None

    if destination_type == Flow.NODE_TYPE_RULESET:
        node = RuleSet.get(flow, uuid)
    else:
        node = ActionSet.get(flow, uuid)

    if node:
        node.flow = flow
    return node
