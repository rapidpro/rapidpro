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
    data: OrderedDict = None

    def __init__(self, _uuid):
        self.uuid = _uuid
        self.children = []
    
    def __str__(self):
        return f"Node: {self.node_types + '_' + self.uuid}"

    def __repr__(self):
        return self.uuid

    def __hash__(self):
        return hash(self.uuid)

    def __eq__(self, other):
        # Here we have method that allows us to compare similar nodes
        if isinstance(other, Node):
            # TODO: Need to add set of instructions to chack if one node match with another by different metrics
            common_types = self.node_types.intersection(other.node_types)
            # if "send_msg" in common_types:
            #     for self_action in self.data.get("actions"):
            #         for other_action in other.data.get("actions"):
            #             if self_action["type"] == other_action["type"] and self_action["type"] == "send_msg":
            #                 if jaro_distance(self_action["text"], other_action["text"]) >= 0.8:
            #                     return True
            #     return False
            return bool(common_types)
    
    def set_parent(self, parent):
        self.parent = parent
        parent.children.append(self)


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
            self.nodes_map[node.uuid] = node

            destinations = {node_exit["destination_uuid"] for node_exit in node_data["exits"] if node_exit.get("destination_uuid")}
            if destinations:
                self.edges_map[node.uuid] = destinations

        # create childrens and parents
        for parent, children in self.edges_map.items():
            parent = self.nodes_map.get(parent)
            for child in children:
                self.nodes_map.get(child).set_parent(parent)


class GraphDifferenceNode(Node):
    left_origin_node:Node = None
    right_origin_node:Node = None
    conflicts: list = None
    data: OrderedDict = None

    def __init__(self, *args, left_node=None, right_node=None, parent=None, conflicts=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.left_origin_node = left_node
        self.right_origin_node = right_node
        self.parent = parent
        self.conflicts = conflicts if conflicts else []
        self.data = OrderedDict(uuid=self.uuid)
    
    def set_parent(self, parent):
        self.parent = parent
        self.parent.children.append(self)

    def get_uuids(self):
        return [node.uuid for node in (self.left_origin_node, self.right_origin_node) if node]

    def get_child(self, uuid):
        for child in self.children:
            if uuid in child.get_uuids():
                return child

    def copy_data(self, origin):
        self.data.update(origin.data)

    def correct_uuids(self):
        self.data["uuid"] = self.uuid
        for destination in self.data["exits"]:
            dest_uuid = destination.get("destination_uuid")
            if dest_uuid:
                child = self.get_child(dest_uuid)
                destination["destination_uuid"] = child.uuid

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
        l_flow, r_flow = getattr(left.get("flow"), "uuid", None), getattr(right.get("flow"), "uuid", None)
        is_similar.append(l_flow is not None and l_flow == r_flow)
        return any(is_similar)

    def check_difference(self):
        if bool(self.left_origin_node) != bool(self.right_origin_node):
            self.copy_data(self.left_origin_node or self.right_origin_node)
            self.correct_uuids()
            return
        
        self.check_routers()
        self.check_actions()
        self.correct_uuids()


class GraphDifferenceMap:
    left_graph: Graph = None
    right_graph: Graph = None
    unmatched_nodes_in_left: list = None
    unmatched_nodes_in_right: list = None
    diff_nodes_map: dict = None
    diff_nodes_edges: dict = None
    diff_nodes_origin_map = None
    definition: OrderedDict = None

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
            children_pairs, had_children = self.find_matching_children(*matched_pair)

            if had_parents and parents_match:
                parents_pair = (matched_pair[0].parent, matched_pair[1].parent)
                parent_node = self.create_diff_node_for_matched_nodes_pair(parents_pair)
                node = self.create_diff_node_for_matched_nodes_pair(matched_pair)
                node.set_parent(parent_node)
                if parents_pair in matched_pairs:
                    matched_pairs.remove(parents_pair)
            
            if had_children and children_pairs:
                node = self.create_diff_node_for_matched_nodes_pair(matched_pair)
                self.mark_nodes_as_matched(matched_pair, matched_pairs)
                for children_pair in children_pairs:
                    child = self.create_diff_node_for_matched_nodes_pair(children_pair, parent=node)
                    node.children.append(child)
                    self.create_diff_nodes_edge(node.uuid, child.uuid)
                    self.mark_nodes_as_matched(children_pair, matched_pairs)
                    if children_pair in matched_pairs:
                        matched_pairs.remove(children_pair)
    
            if not had_children and not had_parents:
                node = self.create_diff_node_for_matched_nodes_pair(matched_pair)
                self.mark_nodes_as_matched(matched_pair, matched_pairs)
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

        diff_node = GraphDifferenceNode(uuid, left_node=left, right_node=right, parent=parent)
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

    def mark_nodes_as_matched(self, matched_pair, matched_pairs=None):
        left, right = matched_pair
        need_to_remove_from_matched_pairs = []
        if left:
            self.unmatched_nodes_in_left.remove(left.uuid)
            need_to_remove_from_matched_pairs += filter(lambda node_pair: node_pair[0].uuid==left.uuid, matched_pairs or [])
        if right:
            self.unmatched_nodes_in_right.remove(right.uuid)
            need_to_remove_from_matched_pairs += filter(lambda node_pair: node_pair[1].uuid==right.uuid, matched_pairs or [])

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


    def find_matching_children(self, node_a: Node, node_b: Node):
        pairs = []
        for sub_node_a in node_a.children:
            for sub_node_b in node_b.children:
                if sub_node_a == sub_node_b:
                    pairs.append((sub_node_a, sub_node_b))
        return pairs, bool(node_a.children and node_b.children)

    def check_differences(self):
        conflicts = {}
        nodes = []
        for node in self.diff_nodes_map.values():
            node.check_difference()
            if node.conflicts:
                conflicts[node.uuid] = node.conflicts
            nodes.append(node.data)
        self.definition["nodes"] = nodes

    def compare_graphs(self):
        self.match_flow_steps()
        self.check_differences()

