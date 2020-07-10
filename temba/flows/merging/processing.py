from collections import OrderedDict


class NodeConflictTypes:
    EXTRA_ACTION = "EXTRA_ACTION"
    DIFFERENCE_IN_TEXT = "DIFFERENCE_IN_TEXT"


class InnerNode:
    uuid: str = None
    node_type: str = None
    text: str = None

    def __init__(self, _uuid, _type, _text=None):
        self.uuid = _uuid
        self.node_type = _type
        self.text = _text


class Node:
    uuid: str = None
    node_type: str = None
    parent = None
    children: list = None
    data: OrderedDict = None

    def __init__(self, _uuid):
        self.uuid = _uuid
        self.children = []
    
    def __str__(self):
        return f"Node: {self.node_type + '_' + self.uuid}, Children: {', '.join(str(item) for item in self.children)}"

    def __repr__(self):
        return self.uuid

    def __hash__(self):
        return hash(self.uuid)

    def __eq__(self, other):
        # Here we have method that allows us to compare similar nodes
        if isinstance(other, Node):
            # TODO: Need to add set of instructions to chack if one node match with another by different metrics
            one = self.node_type == other.node_type
            two = len(self.data["actions"]) == len(other.data["actions"])
            return all((one, two))
    
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
            node.node_type = node_data["router"]["type"] if node_data.get("router") else "actions"
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
    source_node_from_first_flow:Node = None
    source_node_from_second_flow:Node = None
    conflicts: list = None

    def __init__(self, *args, ff_node=None, sf_node=None, parent=None, conflicts=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.source_node_from_first_flow = ff_node
        self.source_node_from_second_flow = sf_node
        self.parent = parent
        self.conflicts = conflicts if conflicts else []


class GraphDifferenceMap:
    first_graph: Graph = None
    second_graph: Graph = None
    unmatched_nodes_in_first: list = None
    unmatched_nodes_in_second: list = None
    diff_nodes_map: dict = None
    diff_nodes_edges: dict = None
    graph_diff = None
    diff_nodes_origin_map = None

    def __init__(self, a, b):
        self.first_graph = a
        self.second_graph = b
        self.unmatched_nodes_in_first = list(self.first_graph.nodes_map.keys())
        self.unmatched_nodes_in_second = list(self.second_graph.nodes_map.keys())
        self.diff_nodes_map = {}
        self.diff_nodes_edges = {}
        self.diff_nodes_origin_map = {}

    def compare_graphs(self):
        matched_pairs = self.find_matching_nodes()
        matched_pair = matched_pairs.pop(0) if matched_pairs else None
        ignored_pairs = []

        # add all nodes that are matching to difference map
        while matched_pair:
            parents_match = (
                ((matched_pair[0].parent and matched_pair[0].parent) and (matched_pair[0].parent == matched_pair[0].parent)) or
                not (matched_pair[0].parent and matched_pair[0].parent)
            )
            children_pairs, had_children = self.find_matching_children(*matched_pair)
            if not had_children and parents_match:
                self.create_diff_node_for_matched_nodes_pair(matched_pair)
                self.mark_nodes_as_matched(matched_pair, matched_pairs)
            elif had_children and children_pairs:
                node = self.create_diff_node_for_matched_nodes_pair(matched_pair)
                self.mark_nodes_as_matched(matched_pair, matched_pairs)
                childrens = []
                for children_pair in children_pairs:
                    child = self.create_diff_node_for_matched_nodes_pair(children_pair, node, parent=node)
                    childrens.append(child)
                    self.create_diff_nodes_edge(node.uuid, child.uuid)
                    self.mark_nodes_as_matched(children_pair, matched_pairs)
                    if children_pair in matched_pairs:
                        matched_pairs.remove(children_pair)
            else:
                ignored_pairs.append(matched_pair)
            matched_pair = matched_pairs.pop(0) if matched_pairs else None
        
        # add all nodes that are not matching to difference map
        self.create_diff_nodes_for_unmatched_nodes()
    
    def create_diff_node_for_matched_nodes_pair(self, matched_pair, parent=Node):
        first, second = matched_pair
        uuid = first.uuid or second.uuid
        diff_node = GraphDifferenceNode(uuid, ff_node=first, sf_node=second, parent=parent)
        self.diff_nodes_map[uuid] = diff_node
        for origin_node in matched_pair:
            if origin_node:
                self.diff_nodes_origin_map[origin_node.uuid] = diff_node

    def create_diff_nodes_for_unmatched_nodes(self):
        for origin_node_uuid in self.unmatched_nodes_in_first:
            self.create_diff_node_for_unmatched_node(origin_node_uuid, self.first_graph)
        
        for origin_node_uuid in self.unmatched_nodes_in_second:
            self.create_diff_node_for_unmatched_node(origin_node_uuid, self.second_graph)

        
    def create_diff_node_for_unmatched_node(self, uuid, graph: Graph, parent=None):
        origin_node: Node = graph.nodes_map.get(uuid)
        diff_node: Node = self.diff_nodes_origin_map.get(uuid)
        
        # if node already exist we set parent to this node and return this node
        if diff_node:
            if parent and not diff_node.parent:
                diff_node.parent = parent
                self.create_diff_nodes_edge(parent.uuid, diff_node.uuid)
            return diff_node
        
        kwargs = {("ff_node" if graph == self.first_graph else "sf_node"): origin_node, "parent": parent}
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
            parent = self.diff_nodes_origin_map.get(origin_node.uuid)
            if parent:
                diff_node.parent = parent
                self.create_diff_nodes_edge(parent.uuid, diff_node.uuid)
        
        diff_node.children = diff_children
        self.diff_nodes_map[uuid] = diff_node
        self.diff_nodes_origin_map[uuid] = diff_node
        return diff_node

    def create_diff_nodes_edge(self, from_node, to_node):
        self.diff_nodes_edges[from_node] = {*self.diff_nodes_edges.get(from_node, set()), to_node}

    def mark_nodes_as_matched(self, matched_pair, marched_pairs=None):
        first, second = matched_pair
        need_to_remove_from_matched_pairs = []
        if first:
            self.unmatched_nodes_in_first.remove(first.uuid)
            need_to_remove_from_matched_pairs += filter(lambda node, _: node.id==first.uuid, marched_pairs)
        if second:
            self.unmatched_nodes_in_second.remove(second.uuid)
            need_to_remove_from_matched_pairs += filter(lambda _, node: node.id==second.uuid, marched_pairs)

        for item in need_to_remove_from_matched_pairs:
            marched_pairs.remove(item)

    def find_matching_nodes(self, ignore=None):
        pairs = []
        if ignore is None or type(ignore) not in (list, tuple):
            ignore = []

        for uuid_a, node_a in self.first_graph.nodes_map.items():
            for uuid_b, node_b in self.second_graph.nodes_map.items():
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

    def __str__(self):
        return str([
            (
                getattr(diff_node.source_node_from_first_flow, "uuid", None),
                getattr(diff_node.source_node_from_second_flow, "uuid", None),
            )
            for diff_node in self.diff_nodes_map.values()
        ])
