from collections import OrderedDict
from jellyfish import jaro_distance

from .test_data import get_name_of_flow_step


class NodeConflictTypes:
    ROUTER_CONFLICT = "ROUTER_CONFLICT"
    ACTION_CONFLICT = "ACTION_CONFLICT"


class Node:
    uuid: str = None
    node_types: set = None
    parent = None
    children: list = None
    has_router: bool = None
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
            if not (bool(common_types) and both_routers):
                return False

            if self.has_router and other.has_router:
                self_router = self.data.get("router")
                other_router = other.data.get("router")
                self_actions = self.data.get("actions")
                other_actions = other.data.get("actions")
                if len(self_actions) != len(other_actions):
                    return False

                if len(self_actions) > 1:
                    return False

                if self_router["type"] == other_router["type"]:
                    if self_router["type"] == "switch":
                        if jaro_distance(self_router["operand"], other_router["operand"]) < 0.9:
                            return False
                else:
                    return False

                if self_actions and other_actions and self_actions[0]["type"] != other_actions[0]["type"]:
                    return False

            if "send_msg" in common_types:
                for self_action in self.data.get("actions", []):
                    for other_action in other.data.get("actions", []):
                        if self_action["type"] == other_action["type"] and self_action["type"] == "send_msg":
                            if jaro_distance(self_action["text"], other_action["text"]) >= 0.8:
                                return True
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
        if resource:
            self.create_nodes()

    def __str__(self):
        return f"Graph -> Root {self.root} "

    def create_nodes(self):
        # create all nodes
        for node_data in self.resource["nodes"]:
            node = Node(node_data["uuid"])
            node.node_types = {action.get("type") for action in node_data.get("actions")}
            if node_data.get("router"):
                node.node_types.add(node_data["router"]["type"])
            node.data = OrderedDict(**node_data)
            node.has_router = "router" in node_data
            self.nodes_map[node.uuid] = node

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


class GraphDifferenceNode(Node):
    graph = None
    left_origin_node: Node = None
    right_origin_node: Node = None
    origin_exits_map: dict = None
    conflicts: list = None
    data: OrderedDict = None

    def __init__(self, *args, left_node=None, right_node=None, parent=None, conflicts=None, graph=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.left_origin_node = left_node
        self.right_origin_node = right_node
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

    def get_uuids(self):
        return [node.uuid for node in (self.left_origin_node, self.right_origin_node) if node]

    def get_child(self, uuid):
        child = self.graph.diff_nodes_origin_map.get(uuid)
        if child:
            if child not in self.children:
                child.set_parent(self)
        return child

    def copy_data(self, origin):
        self.data.update(origin.data)

    def correct_uuids(self):
        self.data["uuid"] = self.uuid

        if "categories" in self.data.get("router", {}):
            for category in self.data["router"]["categories"]:
                category["exit_uuid"] = self.origin_exits_map.get(category["exit_uuid"], category["exit_uuid"])

    def check_categories(self):
        if not (self.left_origin_node or self.right_origin_node).has_router:
            return

        left = {**self.left_origin_node.routing_categories}
        right = {**self.right_origin_node.routing_categories}
        default_category = None
        categories = []
        exits = []

        def append_category(category):
            categories.append(category["category"])
            exits.append(category["exit"])
            if category["category"]["name"].lower() in ("other", "all_responses"):
                nonlocal default_category
                default_category = category["category"]["uuid"]

        def get_category_exits_dict(source):
            _categories = {}
            _exits = {exit_item["uuid"]: exit_item for exit_item in source["exits"]}
            for category in source["router"]["categories"]:
                _categories[category["name"]] = {"category": category, "exit": _exits[category["exit_uuid"]]}
            return _categories

        left_categories = get_category_exits_dict(self.left_origin_node.data)
        right_categories = get_category_exits_dict(self.right_origin_node.data)
        all_categories = set({*left_categories.keys(), *right_categories.keys()})

        if "All Responses" in all_categories and len(all_categories) > 1:
            all_categories.remove("All Responses")
            for categories_dict in (left, right, left_categories, right_categories):
                if "All Responses" in categories_dict:
                    data = categories_dict["All Responses"]
                    if "category" in data:
                        data["category"]["name"] = "Other"
                    categories_dict["Other"] = data

        for category in all_categories:
            if category in left_categories and left.get(category) is not None:
                append_category(left_categories[category])
            elif category in right_categories and right.get(category) is not None:
                append_category(right_categories[category])
            elif category in left_categories:
                append_category(left_categories[category])
            elif category in right_categories:
                append_category(right_categories[category])

        self.data["router"]["categories"] = categories
        self.data["router"]["default_category_uuid"] = default_category

    def check_cases(self):
        if "router" not in self.data:
            return

        cases = []
        category_uuids = [category["uuid"] for category in self.data["router"]["categories"]]

        left = {case["category_uuid"]: case for case in self.left_origin_node.data.get("router", {}).get("cases", [])}
        right = {
            case["category_uuid"]: case for case in self.right_origin_node.data.get("router", {}).get("cases", [])
        }
        origin_cases = {**left, **right}
        for uuid in category_uuids:
            case = origin_cases.get(uuid)
            if case:
                cases.append(case)

        self.data["router"]["cases"] = cases

    def check_exits(self):
        def get_exits_data(data):
            if not data:
                return {}

            result_data = {}
            categories_data = {}
            if data.get("router", {}).get("categories"):
                categories_data = {
                    category["exit_uuid"]: "Other" if category["name"] == "All Responses" else category["name"]
                    for category in data["router"]["categories"]
                }
            for exit_item in data["exits"]:
                result_data[exit_item["uuid"]] = {
                    "category": categories_data.get(exit_item["uuid"], "All Responses"),
                    "destination": getattr(
                        self.graph.diff_nodes_origin_map.get(exit_item.get("destination_uuid")), "uuid", None
                    ),
                }
            return result_data

        left_data = get_exits_data(getattr(self.left_origin_node, "data", {}))
        right_data = get_exits_data(getattr(self.right_origin_node, "data", {}))
        merged_exits = []

        if left_data and right_data:
            matches = []
            for uuid_l, data_l in left_data.items():
                for uuid_r, data_r in right_data.items():
                    if data_l["category"] == data_r["category"]:
                        matches.append((uuid_l, uuid_r))
                        merged_exits.append(
                            {
                                "uuid": uuid_l,
                                "destination_uuid": data_l.get("destination") or data_r.get("destination"),
                            }
                        )
                        self.origin_exits_map[uuid_l] = uuid_l
                        self.origin_exits_map[uuid_r] = uuid_l

            left_matched, right_matched = zip(*matches)

            for uuid_l, data_l in left_data.items():
                if uuid_l not in left_matched:
                    self.origin_exits_map[uuid_l] = uuid_l
                    merged_exits.append({"uuid": uuid_l, "destination_uuid": data_l.get("destination")})

            for uuid_r, data_r in right_data.items():
                if uuid_r not in right_matched:
                    self.origin_exits_map[uuid_r] = uuid_r
                    merged_exits.append({"uuid": uuid_r, "destination_uuid": data_r.get("destination")})
        else:
            exits_data = left_data or right_data
            for uuid, data in exits_data.items():
                self.origin_exits_map[uuid] = uuid
                merged_exits.append({"uuid": uuid, "destination_uuid": data.get("destination")})
        self.data["exits"] = merged_exits

    def check_routers(self):
        left = (self.left_origin_node or {}).data.get("router")
        right = (self.right_origin_node or {}).data.get("router")

        if left and right:
            self.data["router"] = left
            conflicts = self.check_router_conflicts(left, right)
            if conflicts:
                self.conflicts.extend(conflicts)
        elif bool(left) != bool(right):
            if left:
                self.data["router"] = left
            if right:
                self.data["router"] = right

    def check_router_conflicts(self, left_router, right_router):
        conflicts = []
        conflict_base = {
            "conflict_type": NodeConflictTypes.ROUTER_CONFLICT,
            "left_router": left_router,
            "right_router": right_router,
        }
        if left_router["type"] != right_router["type"]:
            conflict = dict(conflict_base)
            conflict.update({"field": "type"})
            conflicts.append(conflict)
            return conflicts

        if left_router.get("operand") != right_router.get("operand"):
            conflict = dict(conflict_base)
            conflict.update({"field": "operand"})
            conflicts.append(conflict)

        if left_router.get("result_name") != right_router.get("result_name"):
            conflict = dict(conflict_base)
            conflict.update({"field": "result_name"})
            conflicts.append(conflict)

        return conflicts

    def check_actions(self):
        left = self.left_origin_node.data["actions"]
        right = self.right_origin_node.data["actions"]
        actions = []

        if "router" in self.data:
            actions = left or right
        else:
            matched = []
            if len(left) == len(right):
                for l_action in left:
                    for r_action in right:
                        if self.check_actions_pair(l_action, r_action):
                            conflicts = self.check_actions_conflicts(l_action, r_action)
                            if conflicts:
                                self.conflicts.extend(conflicts)
                            else:
                                actions.append(l_action)
                            matched.append((l_action.get("uuid"), r_action.get("uuid")))

            left_matched, right_matched = zip(*matched) if matched else ([], [])
            for l_action in left:
                if l_action["uuid"] not in left_matched:
                    actions.append(l_action)

            for r_action in right:
                if r_action["uuid"] not in right_matched:
                    actions.append(r_action)

        self.data["actions"] = actions

    def check_actions_pair(self, left, right):
        if left["type"] != right["type"]:
            return

        is_similar = [
            jaro_distance(left.get("body", ""), right.get("body", "")) >= 0.8,
            jaro_distance(left.get("name", ""), right.get("name", "")) >= 0.8,
            jaro_distance(left.get("path", ""), right.get("path", "")) >= 0.8,
            jaro_distance(left.get("scheme", ""), right.get("scheme", "")) >= 0.8,
            jaro_distance(left.get("subject", ""), right.get("subject", "")) >= 0.8,
            jaro_distance(left.get("text", ""), right.get("text", "")) >= 0.8,
        ]
        is_similar.append("labels" in left and "labels" in right)
        is_similar.append("groups" in left and "groups" in right)
        is_similar.append("set_contact" in left["type"] and "set_contact" in right["type"])
        return any(is_similar)

    def check_actions_conflicts(self, l_action, r_action):
        conflict = {
            "conflict_type": NodeConflictTypes.ACTION_CONFLICT,
            "left_action": l_action,
            "right_action": r_action,
        }

        if l_action["type"] in ("send_broadcast", "start_session"):
            contacts = [contact["uuid"] for contact in l_action["contacts"]]
            groups = [group["uuid"] for group in l_action["groups"]]
            for contact in r_action["contacts"]:
                if contact["uuid"] not in contacts:
                    l_action["contacts"].append(contact)
            for group in r_action["groups"]:
                if group["uuid"] not in groups:
                    l_action["groups"].append(group)

        if l_action["type"] == "add_input_labels":
            labels = [label["uuid"] for label in l_action["labels"]]
            for label in r_action["labels"]:
                if label["uuid"] not in labels:
                    l_action["labels"].append(label)

        if l_action["type"] in ("send_msg", "say_msg", "send_broadcast"):
            if l_action["text"] != r_action["text"]:
                conflict["field"] = "text"
                return [conflict]

        if l_action["type"] == "add_contact_urn":
            if l_action["path"] != r_action["path"]:
                conflict["field"] = "path"
                return [conflict]

        if l_action["type"] == "remove_contact_groups":
            if any((l_action["all_groups"], r_action["all_groups"])):
                l_action["all_groups"] = True
                l_action["groups"] = []
            else:
                groups = [group["uuid"] for group in l_action["groups"]]
                for group in r_action["groups"]:
                    if group["uuid"] not in groups:
                        l_action["groups"].append(group)

        if l_action["type"] == "send_email":
            conflicts = []
            l_action["addresses"] = list(set([*l_action["addresses"], *r_action["addresses"]]))
            l_action["attachments"] = list(set([*l_action["attachments"], *r_action["attachments"]]))
            if l_action["body"] != r_action["body"]:
                body_conflict = dict(conflict)
                body_conflict["field"] = "body"
                conflicts.append(body_conflict)

            if l_action["subject"] != r_action["subject"]:
                subject_conflict = dict(conflict)
                subject_conflict["field"] = "subject"
                conflicts.append(subject_conflict)
            return conflicts

        if l_action["type"] == "set_contact_name":
            if l_action["name"] != r_action["name"]:
                conflict["filed"] = "name"
                return [conflict]

        if l_action["type"] == "set_contact_language":
            if l_action["language"] != r_action["language"]:
                conflict["filed"] = "language"
                return [conflict]

        if l_action["type"] == "set_contact_channel":
            if l_action["channel"]["uuid"] != r_action["channel"]["uuid"]:
                conflict["filed"] = "channel"
                return [conflict]

        if l_action["type"] == "set_contact_field":
            conflicts = []
            if l_action["field"]["key"] != r_action["field"]["key"]:
                field_conflict = dict(conflict)
                field_conflict["field"] = "field"
                conflicts.append(field_conflict)

            if l_action["value"] != r_action["value"]:
                value_conflict = dict(conflict)
                value_conflict["field"] = "value"
                conflicts.append(value_conflict)
            return conflicts

        if l_action["type"] in ("start_session", "enter_flow"):
            if l_action["flow"]["uuid"] != r_action["flow"]["uuid"]:
                conflict["field"] = "flow"
                return [conflict]

    def check_difference(self):
        if bool(self.left_origin_node) != bool(self.right_origin_node):
            self.copy_data(self.left_origin_node or self.right_origin_node)
            self.check_exits()
            self.correct_uuids()
            return

        self.check_routers()
        self.check_exits()
        self.check_categories()
        self.check_cases()
        self.check_actions()
        self.correct_uuids()

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
        self.definition = OrderedDict(**_left.resource)

    def match_flow_steps(self):
        matched_pairs = self.find_matching_nodes()
        matched_pair = matched_pairs.pop(0) if matched_pairs else None
        ignored_pairs = []

        # add all nodes that are matching to difference map
        while matched_pair:
            had_parents = matched_pair[0].parent and matched_pair[1].parent
            parents_match = had_parents and matched_pair[0].parent == matched_pair[1].parent
            children_pairs, had_children = self.find_matching_children(*matched_pair, ignored_pairs)

            if had_parents and parents_match:
                parents_pair = (matched_pair[0].parent, matched_pair[1].parent)
                parent_node = self.create_diff_node_for_matched_nodes_pair(parents_pair)
                node = self.create_diff_node_for_matched_nodes_pair(matched_pair)
                node.set_parent(parent_node)
                self.create_diff_nodes_edge(parent_node.uuid, node.uuid)
                self.mark_nodes_as_matched(parents_pair, matched_pairs, ignored_pairs)
                self.mark_nodes_as_matched(matched_pair, matched_pairs, ignored_pairs)

            if had_children and children_pairs:
                node = self.create_diff_node_for_matched_nodes_pair(matched_pair)
                self.mark_nodes_as_matched(matched_pair, matched_pairs, ignored_pairs)
                for children_pair in children_pairs:
                    child = self.create_diff_node_for_matched_nodes_pair(children_pair, parent=node)
                    node.children.append(child)
                    self.create_diff_nodes_edge(node.uuid, child.uuid)
                    self.mark_nodes_as_matched(children_pair, matched_pairs, ignored_pairs)

            if not had_children and not had_parents:
                node = self.create_diff_node_for_matched_nodes_pair(matched_pair)
                self.mark_nodes_as_matched(matched_pair, matched_pairs, ignored_pairs)
            else:
                ignored_pairs.append(matched_pair)
            matched_pair = matched_pairs.pop(0) if matched_pairs else None

        # add all nodes that are not matching to difference map
        self.create_diff_nodes_for_unmatched_nodes()

    def create_diff_node_for_matched_nodes_pair(self, matched_pair, parent=None):
        left, right = matched_pair
        uuid = left.uuid or right.uuid
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

    def mark_nodes_as_matched(self, matched_pair, matched_pairs=None, ignored_pairs=None):
        left, right = matched_pair
        need_to_remove_from_matched_pairs = []
        if left:
            if left.uuid in self.unmatched_nodes_in_left:
                self.unmatched_nodes_in_left.remove(left.uuid)
            need_to_remove_from_matched_pairs += filter(
                lambda node_pair: node_pair[0].uuid == left.uuid, matched_pairs or []
            )
        if right:
            if right.uuid in self.unmatched_nodes_in_right:
                self.unmatched_nodes_in_right.remove(right.uuid)
            need_to_remove_from_matched_pairs += filter(
                lambda node_pair: node_pair[1].uuid == right.uuid, matched_pairs or []
            )

        ignored_pairs.extend(need_to_remove_from_matched_pairs)
        for item in set(need_to_remove_from_matched_pairs):
            matched_pairs.remove(item)

    def find_matching_nodes(self, ignore=None):
        pairs = []
        if ignore is None or type(ignore) not in (list, tuple):
            ignore = []

        for uuid_a, node_a in self.left_graph.nodes_map.items():
            for uuid_b, node_b in self.right_graph.nodes_map.items():
                # here we using comarator `__eq__` to define that some node from first flow
                # is equal to node from another
                if node_a == node_b and (uuid_a, uuid_b) not in ignore:
                    pairs.append((node_a, node_b))
        return pairs

    def find_matching_children(self, node_a: Node, node_b: Node, ignored_pairs=None):
        pairs = []
        for sub_node_a in node_a.children:
            for sub_node_b in node_b.children:
                if sub_node_a == sub_node_b:
                    if (ignored_pairs and (sub_node_a, sub_node_b) not in ignored_pairs) or (not ignored_pairs):
                        pairs.append((sub_node_a, sub_node_b))
        return pairs, bool(node_a.children and node_b.children)

    def check_differences(self):
        conflicts = dict()
        nodes = list()
        for node in self.diff_nodes_map.values():
            node.check_difference()
            if node.conflicts:
                conflicts[node.uuid] = node.conflicts
            nodes.append(node.data)
        self.definition["nodes"] = nodes
        self.conflicts = conflicts

    def order_nodes(self):
        left_order = {node["uuid"]: index for index, node in enumerate(self.left_graph.resource["nodes"])}
        right_order = {node["uuid"]: index for index, node in enumerate(self.right_graph.resource["nodes"])}
        ordering = {**left_order, **right_order}
        self.definition["nodes"].sort(key=lambda node: ordering[node["uuid"]])

    def merge_ui_definition(self):
        origin_ui = {
            **self.left_graph.resource["_ui"].get("nodes", {}),
            **self.right_graph.resource["_ui"].get("nodes", {}),
        }
        merged_ui = {flow_uuid: origin_ui[flow_uuid] for flow_uuid in self.diff_nodes_map.keys()}
        self.definition["_ui"]["nodes"] = merged_ui

    def merge_localizations(self):
        localization = self.left_graph.resource["localization"]
        for key, value in self.right_graph.resource["localization"].items():
            if key in localization:
                localization[key].update(value)
            else:
                localization[key] = value
        self.definition["localization"] = localization

    def get_conflict_solutions(self):
        conflict_solutions = []

        def get_node_label(node, conflict):
            flow_step_name = (
                "Flow step (Action)"
                if conflict["conflict_type"] == NodeConflictTypes.ACTION_CONFLICT
                else "Flow step (Router)"
            )
            flow_step_name = get_name_of_flow_step(node.data, default=flow_step_name) if node else flow_step_name
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
                    self.diff_nodes_origin_map.get(uuid).left_origin_node
                    or self.diff_nodes_origin_map.get(uuid).right_origin_node
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
            node_uuid, action_uuid, field_name = conflict_uuid.split("_")
            updated_conflicts = self.diff_nodes_map.get(node_uuid).resolve_conflict(action_uuid, field_name, resolving)
            if updated_conflicts:
                self.conflicts[node_uuid] = updated_conflicts
            else:
                del self.conflicts[node_uuid]
        self.definition["nodes"] = [node.data for node in self.diff_nodes_map.values()]

    def compare_graphs(self):
        self.match_flow_steps()
        self.check_differences()
        self.order_nodes()
        self.merge_ui_definition()
        self.merge_localizations()
