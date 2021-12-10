import pickle

from temba.flows.merging import Node, Graph
from temba.flows.merging.merging import GraphDifferenceNode
from temba.tests import TembaTest


class TestMergingFlows(TembaTest):
    def setUp(self):
        super().setUp()
        self.flow1_json = {
            "nodes": [
                {
                    "exits": [
                        {
                            "destination_uuid": "bf69a5bf-7e2a-4a4a-965d-e67b8ee8086b",
                            "uuid": "627b3081-4dce-4867-aa64-43985bfce98e",
                        },
                        {
                            "uuid": "880080f2-0ec8-4c28-9505-c6f85fed72e4",
                            "destination_uuid": "25fc63ef-fadd-48f0-926d-44a0302110c3",
                        },
                        {
                            "uuid": "2570cb5b-084c-424b-b16c-bde2ab2e05f7",
                            "destination_uuid": "70b3ab7e-3ca1-4fa5-ad15-c3767ad25281",
                        },
                    ],
                    "router": {
                        "cases": [
                            {
                                "arguments": ["651145bb-8940-4dd8-8080-74a1285e5360", "Monkey Facts"],
                                "category_uuid": "6d7fd88c-1edc-4f72-a2d0-0d8dfcd4e965",
                                "type": "has_group",
                                "uuid": "64197de8-9b41-4467-8a76-ace089d8e8c6",
                            },
                            {
                                "arguments": ["d5d700b4-c232-4fee-901a-00df1d4143fc", "Fish Facts"],
                                "category_uuid": "12cd44e2-0c73-4a48-9da8-1cdc1e16b679",
                                "type": "has_group",
                                "uuid": "57a266d6-0c91-4243-962c-520161749378",
                            },
                        ],
                        "categories": [
                            {
                                "exit_uuid": "627b3081-4dce-4867-aa64-43985bfce98e",
                                "name": "Monkey Facts",
                                "uuid": "6d7fd88c-1edc-4f72-a2d0-0d8dfcd4e965",
                            },
                            {
                                "exit_uuid": "880080f2-0ec8-4c28-9505-c6f85fed72e4",
                                "name": "Fish Facts",
                                "uuid": "12cd44e2-0c73-4a48-9da8-1cdc1e16b679",
                            },
                            {
                                "exit_uuid": "2570cb5b-084c-424b-b16c-bde2ab2e05f7",
                                "name": "Other",
                                "uuid": "e63e7e4e-0a54-4cd3-a5b3-f17155533338",
                            },
                        ],
                        "default_category_uuid": "e63e7e4e-0a54-4cd3-a5b3-f17155533338",
                        "operand": "@contact.groups",
                        "result_name": "Group Split",
                        "type": "switch",
                    },
                    "uuid": "23a6a459-c166-4571-9235-917a9112a548",
                    "actions": [],
                },
                {
                    "exits": [{"uuid": "380a5dfe-6408-4393-af9d-eee667a6a53c"}],
                    "router": {
                        "cases": [],
                        "categories": [
                            {
                                "exit_uuid": "380a5dfe-6408-4393-af9d-eee667a6a53c",
                                "name": "All Responses",
                                "uuid": "fbb45f13-c54f-41f0-8137-d9c511c89888",
                            }
                        ],
                        "default_category_uuid": "fbb45f13-c54f-41f0-8137-d9c511c89888",
                        "operand": '@(if(is_error(fields.expression_split), "@contact.expression_split", fields.expression_split))',
                        "result_name": "Response 4",
                        "type": "switch",
                    },
                    "uuid": "bf69a5bf-7e2a-4a4a-965d-e67b8ee8086b",
                    "actions": [],
                },
                {
                    "uuid": "25fc63ef-fadd-48f0-926d-44a0302110c3",
                    "actions": [
                        {
                            "uuid": "7ebd7be9-18d4-4d06-b69e-63d11d7bb72e",
                            "type": "call_classifier",
                            "result_name": "_Result Classification",
                            "input": "@input.text",
                            "classifier": {"uuid": "891a1c5d-1140-4fd0-bd0d-a919ea25abb6", "name": "Feelings"},
                        }
                    ],
                    "router": {
                        "cases": [
                            {
                                "arguments": ["None", ".9"],
                                "type": "has_top_intent",
                                "uuid": "f7889a15-9d27-47e4-8132-011fd3e56473",
                                "category_uuid": "0a1fb250-2a5f-4995-81eb-0be34ee76f6d",
                            },
                            {
                                "uuid": "08cb55c0-9a89-41fb-a1d7-52f1aa65f5b9",
                                "type": "has_category",
                                "arguments": ["Success", "Skipped"],
                                "category_uuid": "d1c69a12-68d9-435a-80be-f1409b57c62f",
                            },
                        ],
                        "operand": "@results._result_classification",
                        "categories": [
                            {
                                "uuid": "0a1fb250-2a5f-4995-81eb-0be34ee76f6d",
                                "name": "None",
                                "exit_uuid": "89a4604e-d8bc-4836-8810-84ef86d7998c",
                            },
                            {
                                "uuid": "f113da14-35e1-4cd4-a0dc-c259f5e6f2e5",
                                "name": "Failure",
                                "exit_uuid": "509b4d09-c262-4fcb-a20d-ad2d431d1229",
                            },
                            {
                                "uuid": "d1c69a12-68d9-435a-80be-f1409b57c62f",
                                "name": "Other",
                                "exit_uuid": "4936d570-f4b4-407c-842f-59cf0b7bd54b",
                            },
                        ],
                        "type": "switch",
                        "default_category_uuid": "f113da14-35e1-4cd4-a0dc-c259f5e6f2e5",
                        "result_name": "Result",
                    },
                    "exits": [
                        {"uuid": "89a4604e-d8bc-4836-8810-84ef86d7998c"},
                        {"uuid": "4936d570-f4b4-407c-842f-59cf0b7bd54b"},
                        {"uuid": "509b4d09-c262-4fcb-a20d-ad2d431d1229"},
                    ],
                },
                {
                    "uuid": "70b3ab7e-3ca1-4fa5-ad15-c3767ad25281",
                    "actions": [
                        {
                            "uuid": "78f73b5c-b842-4b7c-ac74-54a2b1e31b79",
                            "type": "open_ticket",
                            "ticketer": {"uuid": "6ceb51cd-1d19-4f28-a9c3-2e244a9e2959", "name": "Zendesk"},
                            "subject": "@run.flow.name",
                            "body": "@results",
                            "result_name": "Result",
                        }
                    ],
                    "router": {
                        "type": "switch",
                        "operand": "@results.result",
                        "cases": [
                            {
                                "uuid": "e8af44bc-e5e0-46e7-816d-2eee0cf1928c",
                                "type": "has_category",
                                "arguments": ["Success"],
                                "category_uuid": "9ae9400a-eb5d-491a-bae8-ae295811c9d1",
                            }
                        ],
                        "categories": [
                            {
                                "uuid": "9ae9400a-eb5d-491a-bae8-ae295811c9d1",
                                "name": "Success",
                                "exit_uuid": "35d0d802-0d0f-418b-9028-5a0812761e97",
                            },
                            {
                                "uuid": "494df32e-1b36-4fd4-978a-0769c8b3b5ac",
                                "name": "Failure",
                                "exit_uuid": "2ec47a11-ee11-48b3-9207-3431fd354274",
                            },
                        ],
                        "default_category_uuid": "494df32e-1b36-4fd4-978a-0769c8b3b5ac",
                    },
                    "exits": [
                        {"uuid": "35d0d802-0d0f-418b-9028-5a0812761e97", "destination_uuid": None},
                        {"uuid": "2ec47a11-ee11-48b3-9207-3431fd354274", "destination_uuid": None},
                    ],
                },
            ]
        }

    def test_node(self):
        def copy(obj):
            return pickle.loads(pickle.dumps(obj))

        flow1 = self.get_flow("group_split_no_name")
        flow2 = self.get_flow("favorites_v13")

        flow_def = flow1.get_definition()
        flow_def2 = flow2.get_definition()

        node1 = Node(flow_def["nodes"][0]["uuid"])
        node3 = Node(flow_def2["nodes"][0]["uuid"])

        node1.data = flow_def["nodes"][0]
        node1.has_router = True
        node1.node_types = {"send_msg"}
        node2 = copy(node1)
        node2.uuid = "3d6c6132-8b84-4b0b-b945-8b4ae2ee8696"
        node3.data = flow_def2["nodes"][1]

        self.assertTrue(node1 == node2)
        self.assertFalse(node1 == node3)

        node2.has_router = False
        self.assertFalse(node1 == node2)
        node1.data["actions"] = [{"type": "enter_flow", "flow": {"uuid": "test-uuid"}, "result_name": ""}]
        node2 = copy(node1)
        self.assertTrue(node1 == node2)
        node1.data["actions"] = [{"type": "call_webhook", "result_name": ""}]
        node2 = copy(node1)
        self.assertTrue(node1 == node2)

        node1.data["actions"] = [{"type": "call_giftcard", "result_name": "Test"}]
        node2 = copy(node1)
        self.assertTrue(node1 == node2)

        node1_categories = node1.get_routing_categories()
        self.assertEqual(node1_categories, {"Approved": None, "Other": None})

        exits = [
            {
                "uuid": "d7a36118-0a38-4b35-a7e4-ae89042f0d3c",
                "destination_uuid": "3dcccbb4-d29c-41dd-a01f-16d814c9ab82",
            }
        ]
        router_replacement = {
            "type": "switch",
            "wait": {"type": "msg"},
            "categories": [
                {
                    "uuid": "37d8813f-1402-4ad2-9cc2-e9054a96525b",
                    "name": "All Responses",
                    "exit_uuid": "d7a36118-0a38-4b35-a7e4-ae89042f0d3c",
                }
            ],
            "operand": "@input.text",
            "default_category_uuid": "37d8813f-1402-4ad2-9cc2-e9054a96525b",
        }

        node2_categories = node2.get_routing_categories()
        self.assertEqual(node2_categories, {"Approved": None, "Other": None})

        node2.data["exits"] = exits
        node2.data["router"] = router_replacement
        self.assertEqual(node2_categories, {"Approved": None, "Other": None})
        node2.routing_categories = {}

        node2_categories = node2.get_routing_categories()
        self.assertEqual(node2_categories, {"All Responses": "3dcccbb4-d29c-41dd-a01f-16d814c9ab82"})

    def test_graph_class(self):
        graph1 = Graph(resource=self.flow1_json)

        self.assertEqual(len(graph1.result_names), 5)

        node_data1 = {"actions": []}
        node_data2 = {"actions": [{"result_name": "Color"}]}
        graph1.extract_result_names(node_data1)
        self.assertEqual(len(graph1.result_names), 5)
        graph1.extract_result_names(node_data2)
        self.assertEqual(len(graph1.result_names), 6)

        not_unique_results_name = graph1.get_not_unique_result_names()
        self.assertEquals(not_unique_results_name, ["Result"])

    def test_graph_difference_node(self):
        graph1 = Graph(resource=self.flow1_json)
        node_instances = list(graph1.nodes_map.values())
        main_node = node_instances[0]
        left_node = node_instances[1]
        right_node = node_instances[2]

        graph_node = GraphDifferenceNode(
            "3d6c6132-8b84-4b0b-b945-8b4ae2ee8696", left_node=left_node, right_node=right_node, graph=graph1
        )
        graph_node.data = main_node.data
        self.assertEqual(graph_node.data["uuid"], main_node.uuid)

        graph_node.correct_uuids()
        self.assertNotEqual(graph_node.data["uuid"], main_node.uuid)
        self.assertEqual(graph_node.data["uuid"], graph_node.uuid)

        self.assertEqual(len(graph_node.origin_exits_map), 0)
        graph_node.match_exits()
        self.assertEqual(len(graph_node.origin_exits_map), 2)
