import re
import json
from django.core.exceptions import ValidationError
from rest_framework import serializers
from .merging import Node, Graph, GraphDifferenceNode, GraphDifferenceMap


class NodeSerializer(serializers.Serializer):
    uuid = serializers.CharField()
    node_types = serializers.ListField()
    parent = serializers.CharField(source="get_parent", allow_null=True, read_only=True)
    children = serializers.ListField(child=serializers.CharField(), source="get_children", read_only=True)
    has_router = serializers.BooleanField()
    routing_categories = serializers.JSONField()
    parent_routind_data = serializers.JSONField()
    data = serializers.JSONField()

    def create(self, validated_data):
        node = Node(validated_data.get("uuid"))
        node.node_types = set(validated_data.get("node_types", []))
        node.has_router = validated_data.get("has_router", False)
        node.routing_categories = validated_data.get("routing_categories", {})
        node.parent_routind_data = validated_data.get("routing_categories", {})
        node.data = validated_data.get("data", {})
        return node


class GraphSerializer(serializers.Serializer):
    resource = serializers.JSONField()
    nodes_map = serializers.DictField(child=NodeSerializer(many=False))
    edges_map = serializers.DictField(child=serializers.ListField(child=serializers.CharField()))

    def validate_edges_map(self, value):
        return value

    def create(self, validated_data):
        graph = Graph()
        graph.resource = validated_data.get("resource", {})
        graph.edges_map = validated_data.get("edges_map", {})
        nodes_map = {}
        for node in validated_data.get("nodes_map", {}).values():
            node_serializer = NodeSerializer(data=node)
            if node_serializer.is_valid():
                node_obj = node_serializer.save()
                nodes_map[node_obj.uuid] = node_obj

        for parent_uuid, chilren_uuids in graph.edges_map.items():
            parent = nodes_map.get(parent_uuid)
            for child_uuid in chilren_uuids:
                child = nodes_map.get(child_uuid)
                child.set_parent(parent)

        graph.nodes_map = nodes_map
        return graph


class DiffNodeSerializer(serializers.Serializer):
    uuid = serializers.CharField(required=False)
    node_types = serializers.ListField()
    left_origin_node = NodeSerializer(many=False, allow_null=True)
    right_origin_node = NodeSerializer(many=False, allow_null=True)
    conflicts = serializers.ListField(child=serializers.JSONField())
    origin_exits_map = serializers.DictField(child=serializers.CharField())
    data = serializers.JSONField()

    def create(self, validated_data):
        node = GraphDifferenceNode(validated_data.get("uuid"))
        node.node_types = set(validated_data.get("node_types", []))
        node.conflicts = validated_data.get("conflicts", [])
        node.origin_exits_map = validated_data.get("origin_exits_map", {})
        node.data = validated_data.get("data")
        return node


class DiffGraphSerializer(serializers.Serializer):
    left_graph = GraphSerializer(many=False)
    right_graph = GraphSerializer(many=False)
    diff_nodes_map = serializers.DictField(child=DiffNodeSerializer(many=False))
    diff_nodes_origin_map = serializers.DictField(child=DiffNodeSerializer(many=False))
    diff_nodes_edges = serializers.DictField(child=serializers.ListField(child=serializers.CharField()))
    definition = serializers.JSONField()
    conflicts = serializers.JSONField()

    def create(self, validated_data):
        diff_graph = None
        left_graph_serializer = GraphSerializer(data=validated_data.pop("left_graph"))
        right_graph_serializer = GraphSerializer(data=validated_data.pop("right_graph"))
        if left_graph_serializer.is_valid() and right_graph_serializer.is_valid():
            diff_graph = GraphDifferenceMap(left_graph_serializer.save(), right_graph_serializer.save())
            diff_graph.diff_nodes_edges = validated_data.get("diff_nodes_edges", {})
            diff_graph.definition = validated_data.get("definition", {})
            diff_graph.conflicts = validated_data.get("conflicts", {})
            diff_graph.diff_nodes_map = self.create_nodes(diff_graph, validated_data)
            diff_graph.diff_nodes_origin_map = self.create_diff_nodes_origin_map(
                diff_graph.diff_nodes_map, validated_data
            )
        else:
            raise ValidationError("Origin graphs not valid.")

        return diff_graph

    def create_nodes(self, diff_graph, validated_data):
        nodes_map = {}
        for node in validated_data.get("diff_nodes_map", {}).values():
            node_serializer = DiffNodeSerializer(data=node)
            if node_serializer.is_valid():
                node_obj = node_serializer.save()
                node_obj.left_origin_node = self.get_origin_node(
                    getattr(node.get("left_origin_node"), "uuid", None), diff_graph.left_graph
                )
                node_obj.right_origin_node = self.get_origin_node(
                    getattr(node.get("right_origin_node"), "uuid", None), diff_graph.right_graph
                )
                nodes_map[node_obj.uuid] = node_obj

        for parent_uuid, children_uuids in validated_data.get("diff_nodes_edges", {}).items():
            parent = nodes_map.get(parent_uuid)
            for child_uuid in children_uuids:
                child = nodes_map.get(child_uuid)
                child.set_parent(parent)

        return nodes_map

    def create_diff_nodes_origin_map(self, diff_nodes_map, validated_data):
        diff_nodes_origin_map = {}
        for node_uuid, origin_node in validated_data.get("diff_nodes_origin_map", {}).items():
            diff_nodes_origin_map[node_uuid] = diff_nodes_map.get(origin_node.get("uuid"))
        return diff_nodes_origin_map

    def get_origin_node(self, uuid, origin_graph):
        origin_node = origin_graph.nodes_map.get(uuid, None)
        return origin_node


def serialize_difference_graph(instance, dumps=False):
    data = DiffGraphSerializer(instance=instance).data
    return json.dumps(data, default=lambda obj: list(obj) if isinstance(obj, set) else obj) if dumps else data


def deserialize_difference_graph(data, loads=False):
    if loads:
        data = json.loads(data)

    serializer = DiffGraphSerializer(data=data)
    if serializer.is_valid():
        return serializer.save()


def deserialize_dict_param_from_request(param, request_data):
    pattern = r"^%s\[([\w\-]+)\]$" % param
    keys = filter(lambda x: re.match(pattern, x), request_data.keys())
    result_data = {}
    for key in keys:
        clear_key = re.search(pattern, key).group(1)
        result_data[clear_key] = request_data[key]
    return result_data
