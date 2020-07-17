from collections import OrderedDict
from jellyfish import jaro_distance


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
                    if self_router["type"] == "switch" and self_router["operand"] != other_router["operand"]:
                        return False
                else:
                    return False

                if self_actions and other_actions and self_actions[0]["type"] != other_actions[0]["type"]:
                    return False

            if "send_msg" in common_types:
                for self_action in self.data.get("actions"):
                    for other_action in other.data.get("actions"):
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
            print(child, self.children)
            if child not in self.children:
                child.set_parent(self)
        return child

    def copy_data(self, origin):
        self.data.update(origin.data)

    def correct_uuids(self, used_exit_uuids: set):
        self.data["uuid"] = self.uuid

        # replace exit uuid with new uuid when exit uuid like that has been already used
        passible_uuids = set()
        current_uuids = {ext["uuid"] for ext in self.data["exits"]}
        duplicated_uuids = current_uuids.intersection(used_exit_uuids)
        if duplicated_uuids:
            if self.left_origin_node and self.right_origin_node:
                passible_uuids = {
                    *{ext["uuid"] for ext in self.left_origin_node.data["exits"]},
                    *{ext["uuid"] for ext in self.right_origin_node.data["exits"]},
                }
            else:
                passible_uuids = {
                    ext["uuid"] for ext in (self.left_origin_node or self.right_origin_node).data["exits"]
                }

        passible_uuids = passible_uuids.difference(current_uuids)
        for exit_uuid in duplicated_uuids:
            new_uuid = passible_uuids.pop()
            if "router" in self.data:
                if self.data["router"].get("default_category_uuid") == exit_uuid:
                    self.data["router"]["default_category_uuid"] = new_uuid

                if "categories" in self.data["router"]:
                    for category in self.data["router"]["categories"]:
                        if category["exit_uuid"] == exit_uuid:
                            category["exit_uuid"] = new_uuid

                for ext in self.data["exits"]:
                    if ext["uuid"] == exit_uuid:
                        ext["uuid"] = new_uuid

        # correct destination uuids according to new nodes
        for destination in self.data["exits"]:
            dest_uuid = destination.get("destination_uuid")
            if dest_uuid:
                child = self.get_child(dest_uuid)
                destination["destination_uuid"] = getattr(child, "uuid")

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
            if category in left and left.get(category) is not None:
                append_category(left_categories[category])
            elif category in right and right.get(category) is not None:
                append_category(right_categories[category])
            elif category in left:
                append_category(left_categories[category])
            else:
                append_category(right_categories[category])

        self.data["router"]["categories"] = categories
        self.data["router"]["default_category_uuid"] = default_category
        self.data["exits"] = exits

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

    def check_routers(self):
        left = (self.left_origin_node or {}).data.get("router")
        right = (self.right_origin_node or {}).data.get("router")

        if self.left_origin_node and self.right_origin_node:
            self.data["exits"] = self.left_origin_node.data["exits"]
        elif bool(self.left_origin_node) != bool(self.right_origin_node):
            self.data["exits"] = (self.left_origin_node or self.right_origin_node).data["exits"]

        if left and right:
            if left["type"] != right["type"]:
                self.conflicts.append(NodeConflictTypes.ROUTER_CONFLICT)
                return
            self.data["router"] = left
            self.data["exits"] = self.left_origin_node.data["exits"]
        elif bool(left) != bool(right):
            if left:
                self.data["router"] = left
                self.data["exits"] = self.left_origin_node.data["exits"]
            if right:
                self.data["router"] = right
                self.data["exits"] = self.right_origin_node.data["exits"]

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
        return any(is_similar)

    def update_used_exit_uuids(self, used_exit_uuids=None):
        if used_exit_uuids is not None and "exits" in self.data:
            for ext in self.data["exits"]:
                used_exit_uuids.add(ext["uuid"])

    def check_difference(self, used_exit_uuids=None):
        if bool(self.left_origin_node) != bool(self.right_origin_node):
            self.copy_data(self.left_origin_node or self.right_origin_node)
            self.correct_uuids(used_exit_uuids)
            return

        self.check_routers()
        self.check_categories()
        self.check_cases()
        self.check_actions()
        self.correct_uuids(used_exit_uuids)
        self.update_used_exit_uuids(used_exit_uuids)


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
        diff_children = []

        # adding children if exists
        if origin_node.children:
            for origin_child in origin_node.children:
                diff_child = self.create_diff_node_for_unmatched_node(origin_child.uuid, graph, parent=diff_node)
                diff_children.append(diff_child)
                self.create_diff_nodes_edge(diff_node.uuid, diff_child.uuid)

        # adding parent if not set but exists
        if origin_node.parent and not diff_node.parent:
            parent = self.diff_nodes_origin_map.get(origin_node.parent.uuid)
            if parent:
                diff_node.set_parent(parent)
                self.create_diff_nodes_edge(parent.uuid, diff_node.uuid)

        diff_node.children = diff_children
        self.diff_nodes_map[uuid] = diff_node
        self.diff_nodes_origin_map[uuid] = diff_node
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
        used_exit_uuids = set()
        conflicts = dict()
        nodes = list()
        for node in self.diff_nodes_map.values():
            node.check_difference(used_exit_uuids)
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

    def compare_graphs(self):
        self.match_flow_steps()
        self.check_differences()
        self.order_nodes()
        self.merge_ui_definition()
