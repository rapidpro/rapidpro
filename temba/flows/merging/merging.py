import math

from collections import OrderedDict, Counter, defaultdict
from jellyfish import jaro_similarity

from .helpers import get_flow_step_name, get_flow_step_type


def all_equal(iterable):
    return len(set(iterable)) <= 1


def has_result(node_data):
    result_name = node_data.get("router", {}).get("result_name")
    action_result_names = [
        action["result_name"] for action in node_data.get("actions", []) if action.get("result_name")
    ]
    return result_name or (action_result_names[0] if action_result_names else None)


def group_by(iterable, key):
    result_dict = defaultdict(list)
    for item in iterable:
        result_dict[key(item)].append(item)
    return result_dict


class NodeConflictTypes:
    ROUTER_CONFLICT = "ROUTER_CONFLICT"
    ACTION_CONFLICT = "ACTION_CONFLICT"


class Node:
    uuid: str = None
    node_types: set = None
    parent = None
    children: list = None
    has_router: bool = None
    result_name: str = None
    routing_categories: dict = None
    parent_routind_data: dict = None
    data: OrderedDict = None

    def __init__(self, _uuid):
        self.uuid = _uuid
        self.children = []
        self.routing_categories = {}
        self.parent_routind_data = {}
        self.node_types = set()

    def __str__(self):
        return f"Node: {str(self.node_types) + '_' + self.uuid}"

    def __repr__(self):
        return self.uuid

    def __hash__(self):
        return hash(self.uuid)

    def __eq__(self, other):
        # Here we have method that allows us to compare similar nodes
        if isinstance(other, Node):
            # set of instructions to chack if one node match another node by different metrics
            common_types = self.node_types.intersection(other.node_types)
            both_routers = self.has_router == other.has_router
            self_router = self.data.get("router")
            other_router = other.data.get("router")
            self_actions = self.data.get("actions", [])
            other_actions = other.data.get("actions", [])

            def get_action_pairs_for_comparing(action_type):
                for self_action in self.data.get("actions", []):
                    for other_action in other.data.get("actions", []):
                        if all_equal((action_type, self_action["type"], other_action["type"])):
                            yield (self_action, other_action)

            if not (bool(common_types) and both_routers):
                return False

            if self.has_router and len(self_actions) != len(other_actions):
                return False

            if self.has_router and other.has_router:
                if self_actions and (len(self_actions) > 1 or self_actions[0]["type"] != other_actions[0]["type"]):
                    return False

                if self_router["type"] != other_router["type"]:
                    return False

                if self_router.get("result_name") != other_router.get("result_name"):
                    return False

                if self_actions and self_actions[0]["type"] == "enter_flow":
                    for self_action, other_action in get_action_pairs_for_comparing("enter_flow"):
                        if (
                            self_action["flow"]["uuid"] == other_action["flow"]["uuid"]
                            or jaro_similarity(self_action["flow"]["name"], other_action["flow"]["name"]) >= 0.7
                        ):
                            return True
                    return False

                if self_actions and self_actions[0]["type"] == "call_webhook":
                    for self_action, other_action in get_action_pairs_for_comparing("call_webhook"):
                        if self_action["result_name"] == other_action["result_name"]:
                            return True
                    return False

                if self_actions and self_actions[0]["type"] == "call_lookup":
                    for self_action, other_action in get_action_pairs_for_comparing("call_lookup"):
                        if self_action["result_name"] == other_action["result_name"]:
                            return True
                    return False

                if self_actions and self_actions[0]["type"] == "call_giftcard":
                    for self_action, other_action in get_action_pairs_for_comparing("call_giftcard"):
                        if self_action["result_name"] == other_action["result_name"]:
                            return True
                    return False

                if self_router["type"] == "switch":
                    if self_router.get("result_name"):
                        return self_router.get("result_name") == other_router.get("result_name")
                    else:
                        return self_router.get("operand") == other_router.get("operand")

            def check_send_message():
                for self_action, other_action in get_action_pairs_for_comparing("send_msg"):
                    if jaro_similarity(self_action["text"], other_action["text"]) >= 0.7:
                        return True

            def check_update_contact():
                for self_action, other_action in get_action_pairs_for_comparing("set_contact_field"):
                    if self_action["field"]["key"] == other_action["field"]["key"]:
                        return True

            action_checks = {"send_msg": check_send_message, "set_contact_field": check_update_contact}
            common_actions = {"send_msg", "set_contact_field"}.intersection(common_types)
            if common_actions and not all([action_checks[action]() for action in common_actions]):
                return False
            return True

    def set_parent(self, parent):
        # if node already has parent we don't set new parent,
        # but add current node as child to the new parent
        if self.parent:
            parent.children.append(self)
            return

        self.parent = parent
        parent.children.append(self)

    def get_routing_categories(self):
        if self.routing_categories or not self.has_router:
            return self.routing_categories

        categories = self.data["router"]["categories"]
        exits = {item["uuid"]: item.get("destination_uuid") for item in self.data["exits"]}
        for category in categories:
            name = category["name"]
            destination = exits.get(category["exit_uuid"])
            self.routing_categories[name] = destination

        return self.routing_categories

    def get_parent(self):
        return self.parent.uuid if self.parent else None

    def get_children(self):
        return [child.uuid for child in self.children]


class Graph:
    resource: dict = None
    nodes_map = {}
    edges_map = {}

    def __init__(self, resource: dict = None):
        self.nodes_map = {}
        self.edges_map = {}
        self.resource = resource
        self.result_names = []
        if resource:
            self.create_nodes()

    def __str__(self):
        return f"Graph -> Root {self.root} "

    def create_nodes(self):
        # create all nodes
        for node_data in self.resource["nodes"]:
            node = Node(node_data["uuid"])
            node.node_types = {action.get("type") for action in node_data.get("actions", [])}
            if node_data.get("router"):
                node.node_types.add(node_data["router"]["type"])
            node.data = OrderedDict(**node_data)
            node.has_router = "router" in node_data
            node.result_name = has_result(node_data)
            self.nodes_map[node.uuid] = node
            self.extract_result_names(node_data)

            destinations = {
                node_exit["destination_uuid"] for node_exit in node_data["exits"] if node_exit.get("destination_uuid")
            }
            if destinations:
                self.edges_map[node.uuid] = destinations

        # create childrens and parents
        for parent, children in self.edges_map.items():
            parent = self.nodes_map.get(parent)
            if parent.has_router:
                categories = parent.get_routing_categories()
                categories_uuid = {uuid: name for name, uuid in categories.items()}
                for child in children:
                    child = self.nodes_map.get(child)
                    child.set_parent(parent)
                    child.parent_routind_data[parent.uuid] = categories_uuid[child.uuid]
            else:
                for child in children:
                    self.nodes_map.get(child).set_parent(parent)

    def extract_result_names(self, node_data):
        for action in node_data.get("actions", []):
            if "result_name" in action:
                self.result_names.append(action["result_name"])
        if "result_name" in node_data.get("router", {}):
            self.result_names.append(node_data["router"]["result_name"])

    def get_not_unique_result_names(self):
        counter = Counter(self.result_names)
        not_unique = [result_name for result_name, times in counter.items() if times > 1]
        return not_unique


class GraphDifferenceNode(Node):
    graph = None
    source_node: Node = None
    destination_node: Node = None
    origin_exits_map: dict = None
    conflicts: list = None
    data: OrderedDict = None

    def __init__(self, *args, left_node=None, right_node=None, parent=None, conflicts=None, graph=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.source_node = left_node
        self.destination_node = right_node
        self.parent = parent
        self.graph = graph
        self.conflicts = conflicts if conflicts else []
        self.data = OrderedDict(uuid=self.uuid)
        self.origin_exits_map = {}

    def __str__(self):
        return self.uuid

    def set_parent(self, parent):
        # if node already has parent we don't set new parent,
        # but add current node as child to the new parent
        if self.parent:
            parent.children.append(self)
            return

        self.parent = parent
        self.parent.children.append(self)

    def correct_uuids(self):
        self.data["uuid"] = self.uuid

        if "categories" in self.data.get("router", {}):
            for category in self.data["router"]["categories"]:
                category["exit_uuid"] = self.origin_exits_map.get(category["exit_uuid"], category["exit_uuid"])

    def resolve_conflict(self, action_uuid, field_name, value):
        def get_conflict(action_uuid):
            for index, conflict in enumerate(self.conflicts):
                if action_uuid == "router":
                    if "left_router" in conflict and field_name == conflict["field"]:
                        return self.conflicts.pop(index)
                else:
                    is_exact_action = "left_action" in conflict and conflict["left_action"]["uuid"] == action_uuid
                    if is_exact_action and field_name == conflict["field"]:
                        return self.conflicts.pop(index)

        conflict = get_conflict(action_uuid)
        if not conflict:
            return self.conflicts

        if conflict["conflict_type"] == NodeConflictTypes.ACTION_CONFLICT:
            already_created = False
            action = conflict["left_action"]
            for action_ in self.data["actions"]:
                if action["uuid"] == action_["uuid"]:
                    action = action_
                    already_created = True

            if conflict["field"] in ("flow", "channel", "field"):
                import json

                try:
                    value = json.loads(value.replace("'", '"'))
                except json.decoder.JSONDecodeError:
                    pass

            action[conflict["field"]] = value
            if not already_created:
                self.data["actions"].append(action)
        elif conflict["conflict_type"] == NodeConflictTypes.ROUTER_CONFLICT:
            if conflict["field"] == "type":
                if conflict["right_router"]["type"] == value:
                    self.data["router"] = conflict["right_router"]
                    self.correct_uuids()
            else:
                self.data["router"][conflict["field"]] = value
        return self.conflicts

    def match_exits(self):
        def get_exits_data(data):
            if not data or len(data.get("exits", [])) == 0:
                return {}

            categories_data = {}
            if data.get("router", {}).get("categories") and len(data["router"]["categories"]) == len(data["exits"]):
                categories_data = {
                    category["exit_uuid"]: "Other" if category["name"] == "All Responses" else category["name"]
                    for category in data["router"]["categories"]
                }
            elif len(data.get("exits", [])) == 1:
                categories_data[data["exits"][0]["uuid"]] = "Other"
            return categories_data

        source_exits = get_exits_data(getattr(self.source_node, "data", {}))
        destination_exits = get_exits_data(getattr(self.destination_node, "data", {}))

        for dest_exit, dest_category in destination_exits.items():
            for src_exit, src_category in source_exits.items():
                if dest_category == src_category:
                    self.origin_exits_map[src_exit] = dest_exit
                    self.origin_exits_map[dest_exit] = dest_exit

    def get_definition(self):
        self.data = self.destination_node and self.destination_node.data or self.source_node and self.source_node.data
        return self.data


class GraphDifferenceMap:
    left_graph: Graph = None
    right_graph: Graph = None
    unmatched_nodes_in_left: list = None
    unmatched_nodes_in_right: list = None
    diff_nodes_map: dict = None
    diff_nodes_edges: dict = None
    diff_nodes_origin_map = None
    definition: OrderedDict = None
    conflicts: dict = None

    def __init__(self, _left, _right):
        self.left_graph = _left
        self.right_graph = _right
        self.unmatched_nodes_in_left = list(self.left_graph.nodes_map.keys())
        self.unmatched_nodes_in_right = list(self.right_graph.nodes_map.keys())
        self.diff_nodes_map = {}
        self.diff_nodes_edges = {}
        self.diff_nodes_origin_map = {}
        self.definition = OrderedDict(**_right.resource)
        self.conflicts = {}

    def flow_step_matching(self):
        source_grouped_steps = group_by(self.left_graph.nodes_map.values(), key=get_flow_step_type)
        destination_grouped_steps = group_by(self.right_graph.nodes_map.values(), key=get_flow_step_type)

        for s_group, s_steps in source_grouped_steps.items():
            d_steps = destination_grouped_steps.get(s_group)
            already_matched = {}
            best_matches_tab = {}
            for s_step in s_steps:
                matches = self.find_matches_in_group(s_step, d_steps)
                if not matches:
                    continue
                elif len(matches) == 1:
                    if matches[0] in already_matched:
                        new_match, previous_match = (s_step, matches[0]), (already_matched[matches[0]], matches[0])
                        best_matches_tab[new_match] = self.calculate_matching_coeficient(*new_match)
                        best_matches_tab[previous_match] = self.calculate_matching_coeficient(*previous_match)
                        del already_matched[matches[0]]
                    elif any([d_steps == matches[0] for _, d_step in best_matches_tab.keys()]):
                        new_match = (s_step, matches[0])
                        best_matches_tab[new_match] = self.calculate_matching_coeficient(*new_match)
                    else:
                        already_matched[matches[0]] = s_step
                else:
                    for d_step in matches:
                        new_match = (s_step, d_step)
                        best_matches_tab[new_match] = self.calculate_matching_coeficient(*new_match)

            for d_step, s_step in already_matched.items():
                self.create_diff_node_for_matched_nodes_pair((s_step, d_step))

            to_be_processed = list(dict(best_matches_tab.keys()))
            for s_step in to_be_processed:
                filtered = list(filter(lambda x: x[0][0].uuid == s_step.uuid, best_matches_tab.items()))
                if filtered:
                    best_match, *_ = max(filtered, key=lambda x: x[1])
                    self.create_diff_node_for_matched_nodes_pair(best_match)
                    # remove other matches for pair of nodes
                    best_matches_tab = dict(
                        filter(
                            lambda x: x[0][0].uuid != best_match[0].uuid and x[0][1].uuid != best_match[1].uuid,
                            best_matches_tab.items(),
                        )
                    )
        # add all nodes that are not matched to difference map
        self.create_diff_nodes_for_unmatched_nodes()

    def find_matches_in_group(self, node, nodes):
        matches = []
        for node_ in nodes or []:
            if node == node_:
                matches.append(node_)
        return matches

    def calculate_matching_coeficient(self, s_node, d_node):
        coefficient = 0.0
        if s_node.parent and d_node.parent and s_node.parent == d_node.parent:
            coefficient += 1.0

        matched_children, _ = self.find_matching_children(s_node, d_node)
        coefficient += len(matched_children)

        if self.max_distance:
            coefficient -= self.get_distance(s_node, d_node) / self.max_distance

        return coefficient

    def get_distance(self, s_node, d_node):
        distance = float("inf")
        s_position = self.left_graph.resource.get("_ui", {}).get("nodes", {}).get(s_node.uuid, {}).get("position", {})
        d_position = self.right_graph.resource.get("_ui", {}).get("nodes", {}).get(d_node.uuid, {}).get("position", {})
        if s_position and d_position:
            distance = math.sqrt(
                (s_position["top"] - d_position["top"]) ** 2 + (s_position["left"] - d_position["left"]) ** 2
            )
        return distance

    def create_diff_node_for_matched_nodes_pair(self, matched_pair, parent=None):
        left, right = matched_pair
        uuid = right.uuid or left.uuid
        if uuid in self.diff_nodes_map:
            diff_node = self.diff_nodes_map[uuid]
            if parent:
                diff_node.set_parent(parent)
                self.create_diff_nodes_edge(parent.uuid, diff_node.uuid)
            return diff_node

        diff_node = GraphDifferenceNode(uuid, left_node=left, right_node=right, parent=parent, graph=self)
        diff_node.node_types = set([*getattr(left, "node_types", []), *getattr(right, "node_types", [])])
        self.diff_nodes_map[uuid] = diff_node
        self.diff_nodes_origin_map[left.uuid] = diff_node
        self.diff_nodes_origin_map[right.uuid] = diff_node
        return diff_node

    def create_diff_nodes_for_unmatched_nodes(self):
        # creates difference nodes for unmatched nodes for both graphs
        for origin_node_uuid in self.unmatched_nodes_in_left:
            self.create_diff_node_for_unmatched_node(origin_node_uuid, self.left_graph)

        for origin_node_uuid in self.unmatched_nodes_in_right:
            self.create_diff_node_for_unmatched_node(origin_node_uuid, self.right_graph)

    def create_diff_node_for_unmatched_node(self, uuid, graph: Graph, parent=None):
        origin_node: Node = graph.nodes_map.get(uuid)
        diff_node: Node = self.diff_nodes_origin_map.get(uuid)

        # if node already exist we set parent to this node and return this node
        if diff_node:
            if parent and not diff_node.parent:
                diff_node.set_parent(parent)
                self.create_diff_nodes_edge(parent.uuid, diff_node.uuid)
            return diff_node

        kwargs = {("left_node" if graph == self.left_graph else "right_node"): origin_node, "parent": parent}
        kwargs["graph"] = self
        diff_node = GraphDifferenceNode(uuid, **kwargs)
        self.diff_nodes_map[uuid] = diff_node
        self.diff_nodes_origin_map[uuid] = diff_node
        diff_children = []

        # adding children if exists
        if origin_node.children:
            for origin_child in origin_node.children:
                # here we check whether child node have not been processed yet to prevent infinite recursion
                if origin_child.uuid not in self.diff_nodes_origin_map:
                    diff_child = self.create_diff_node_for_unmatched_node(origin_child.uuid, graph, parent=diff_node)
                    diff_children.append(diff_child)
                    self.create_diff_nodes_edge(diff_node.uuid, diff_child.uuid)
                else:
                    diff_child = self.diff_nodes_origin_map[origin_child.uuid]
                    diff_child.parent = diff_child.parent if diff_child.parent else diff_node
                    diff_children.append(diff_child)
                    self.create_diff_nodes_edge(diff_node.uuid, diff_child.uuid)

        # adding parent if not set but exists
        if origin_node.parent and not diff_node.parent:
            parent = self.diff_nodes_origin_map.get(origin_node.parent.uuid)
            if parent:
                diff_node.set_parent(parent)
                self.create_diff_nodes_edge(parent.uuid, diff_node.uuid)

        diff_node.children = diff_children
        diff_node.node_types = origin_node.node_types
        return diff_node

    def create_diff_nodes_edge(self, from_node, to_node):
        self.diff_nodes_edges[from_node] = {*self.diff_nodes_edges.get(from_node, set()), to_node}

    def find_matching_children(self, node_a: Node, node_b: Node, ignored_pairs=None):
        pairs = []
        for sub_node_a in node_a.children:
            for sub_node_b in node_b.children:
                if sub_node_a == sub_node_b:
                    if (ignored_pairs and (sub_node_a, sub_node_b) not in ignored_pairs) or (not ignored_pairs):
                        pairs.append((sub_node_a, sub_node_b))
        return pairs, bool(node_a.children and node_b.children)

    def order_nodes(self):
        left_order = {node["uuid"]: index for index, node in enumerate(self.left_graph.resource["nodes"])}
        right_order = {node["uuid"]: index for index, node in enumerate(self.right_graph.resource["nodes"])}
        ordering = {**left_order, **right_order}
        self.definition["nodes"].sort(key=lambda node: ordering[node["uuid"]])

    def get_conflict_solutions(self):
        conflict_solutions = []

        def get_node_label(node, conflict):
            flow_step_name = (
                "Flow step (Action)"
                if conflict["conflict_type"] == NodeConflictTypes.ACTION_CONFLICT
                else "Flow step (Router)"
            )
            flow_step_name = get_flow_step_name(node.data, default=flow_step_name) if node else flow_step_name
            field_name = f"With {' '.join(conflict['field'].lower().split('_'))} set as "
            field_value = (
                conflict["left_action"][conflict["field"]]
                if "left_action" in conflict
                else conflict["left_router"][conflict["field"]]
            )
            return f"{flow_step_name}: {field_name} '{field_value}'"

        for uuid, conflicts in self.conflicts.items():
            for conflict in conflicts:
                origin_node = (
                    self.diff_nodes_origin_map.get(uuid).source_node
                    or self.diff_nodes_origin_map.get(uuid).destination_node
                )

                if conflict["conflict_type"] == NodeConflictTypes.ACTION_CONFLICT:
                    conflict_solutions.append(
                        {
                            "uuid": f'{uuid}_{conflict["left_action"]["uuid"]}_{conflict["field"]}',
                            "node_label": get_node_label(origin_node, conflict),
                            "solutions": [
                                conflict["left_action"][conflict["field"]],
                                conflict["right_action"][conflict["field"]],
                            ],
                        }
                    )
                elif conflict["conflict_type"] == NodeConflictTypes.ROUTER_CONFLICT:
                    conflict_solutions.append(
                        {
                            "uuid": f'{uuid}_router_{conflict["field"]}',
                            "node_label": get_node_label(origin_node, conflict),
                            "solutions": [
                                conflict["left_router"][conflict["field"]],
                                conflict["right_router"][conflict["field"]],
                            ],
                        }
                    )
        return conflict_solutions

    def apply_conflict_resolving(self, conflict_resolving):
        for conflict_uuid, resolving in conflict_resolving.items():
            node_uuid, action_uuid, *field_name = conflict_uuid.split("_")
            field_name = "_".join(field_name)
            updated_conflicts = self.diff_nodes_map.get(node_uuid).resolve_conflict(action_uuid, field_name, resolving)
            if updated_conflicts:
                self.conflicts[node_uuid] = updated_conflicts
            else:
                del self.conflicts[node_uuid]
        self.definition["nodes"] = [node.data for node in self.diff_nodes_map.values()]

    def fill_missed_parents(self):
        for node in self.diff_nodes_map.values():
            if node.parent is None:
                parent = None
                if all((node.source_node, node.destination_node)):
                    origin_parent = node.source_node.parent or node.destination_node.parent
                    parent_uuid = origin_parent.uuid if origin_parent else None
                    parent = self.diff_nodes_origin_map.get(parent_uuid)
                elif any((node.source_node, node.destination_node)):
                    origin_node = node.source_node or node.destination_node
                    origin_parent = origin_node.parent
                    parent_uuid = origin_parent.uuid if origin_parent else None
                    parent = self.diff_nodes_origin_map.get(parent_uuid)
                if parent:
                    node.set_parent(parent)

    def delete_unmatched_source_nodes(self):
        node_keys = list(self.diff_nodes_map.keys())
        for key in node_keys:
            diff_node = self.diff_nodes_map[key]
            if diff_node.source_node and not diff_node.destination_node:
                del self.diff_nodes_map[key]
                del self.diff_nodes_origin_map[key]
                if key in self.diff_nodes_edges:
                    del self.diff_nodes_edges[key]
                for parent, children in self.diff_nodes_edges.items():
                    if key in children:
                        children.remove(key)

    def match_flow_step_exits(self):
        for node in self.diff_nodes_map.values():
            node.match_exits()

    def prepare_definition(self):
        nodes = [node.get_definition() for node in self.diff_nodes_map.values()]
        self.definition["nodes"] = nodes

    def calculate_max_distance(self):
        max_top, max_left = 0, 0
        for node in [
            *self.left_graph.resource.get("_ui", {}).get("nodes", {}).values(),
            *self.right_graph.resource.get("_ui", {}).get("nodes", {}).values(),
        ]:
            position = node.get("position", {})
            if position.get("top", max_top) > max_top:
                max_top = position["top"]
            if position.get("left", max_left) > max_left:
                max_left = position["left"]
        self.max_distance = math.sqrt(max_top ** 2 + max_left ** 2)

    def compare_graphs(self):
        self.calculate_max_distance()
        self.flow_step_matching()
        self.delete_unmatched_source_nodes()
        self.match_flow_step_exits()
        self.prepare_definition()
        self.fill_missed_parents()
        self.order_nodes()
