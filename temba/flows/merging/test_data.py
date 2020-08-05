from collections import OrderedDict

serialized_test_data = {
    "left_graph": OrderedDict(
        [
            (
                "resource",
                {
                    "_ui": {
                        "nodes": {
                            "99d93eb4-53da-4638-962a-9ddfc8f8bb6b": {
                                "position": {"left": 440, "top": 0},
                                "type": "execute_actions",
                            },
                            "98416034-f2f1-4bf7-af68-df6c687462bc": {
                                "config": {"cases": {}},
                                "position": {"left": 440, "top": 120},
                                "type": "wait_for_response",
                            },
                            "6cf462db-a29a-439f-82ce-76fc20a6002d": {
                                "position": {"left": 140, "top": 20},
                                "type": "execute_actions",
                            },
                            "62b84e54-9cbc-4895-bd92-0733bc256e90": {
                                "config": {"cases": {}},
                                "position": {"left": 160, "top": 160},
                                "type": "wait_for_response",
                            },
                        }
                    },
                    "expire_after_minutes": 10080,
                    "language": "base",
                    "localization": {},
                    "metadata": {"revision": 5},
                    "name": "Merge of Phone Call with Surveyor",
                    "nodes": [
                        {
                            "actions": [
                                {"text": "Hello", "type": "say_msg", "uuid": "5845954b-1e6d-4826-8dfe-4d7405236ed9"}
                            ],
                            "exits": [
                                {
                                    "destination_uuid": "98416034-f2f1-4bf7-af68-df6c687462bc",
                                    "uuid": "7b591a32-5286-4a60-8ff9-53fd53c9f19c",
                                }
                            ],
                            "uuid": "99d93eb4-53da-4638-962a-9ddfc8f8bb6b",
                        },
                        {
                            "actions": [
                                {
                                    "attachments": [],
                                    "quick_replies": [],
                                    "text": "Hello",
                                    "type": "send_msg",
                                    "uuid": "27f5bfe8-863c-42ec-8bff-6aa393c8ad23",
                                }
                            ],
                            "exits": [
                                {
                                    "destination_uuid": "62b84e54-9cbc-4895-bd92-0733bc256e90",
                                    "uuid": "a01f0d56-79c8-4ad6-9350-1b9fa2c2575e",
                                }
                            ],
                            "uuid": "6cf462db-a29a-439f-82ce-76fc20a6002d",
                        },
                        {
                            "actions": [],
                            "exits": [
                                {
                                    "uuid": "d012a99d-3f5b-4247-8c47-e01cb7edc064",
                                    "destination_uuid": "6cf462db-a29a-439f-82ce-76fc20a6002d",
                                },
                                {"uuid": "530df367-64c2-4e01-834b-db702e6c919d"},
                                {"destination_uuid": None, "uuid": "a52450ab-9a99-48a7-8554-aeb9c41065c3"},
                            ],
                            "router": {
                                "cases": [
                                    {
                                        "arguments": ["1"],
                                        "category_uuid": "3b4246c9-1846-4af1-99d0-3cb1896713b5",
                                        "type": "has_number_eq",
                                        "uuid": "7cd6da07-f5ce-477a-aee9-926e0be61fbd",
                                    },
                                    {
                                        "arguments": ["0"],
                                        "category_uuid": "9b184eb2-5444-42cb-8e9f-fa2c0a1599dd",
                                        "type": "has_number_eq",
                                        "uuid": "7380896c-5cc1-43dc-adf4-c31ee17d1780",
                                    },
                                ],
                                "categories": [
                                    {
                                        "exit_uuid": "d012a99d-3f5b-4247-8c47-e01cb7edc064",
                                        "name": "OK",
                                        "uuid": "3b4246c9-1846-4af1-99d0-3cb1896713b5",
                                    },
                                    {
                                        "exit_uuid": "530df367-64c2-4e01-834b-db702e6c919d",
                                        "name": "Cancel",
                                        "uuid": "9b184eb2-5444-42cb-8e9f-fa2c0a1599dd",
                                    },
                                    {
                                        "exit_uuid": "a52450ab-9a99-48a7-8554-aeb9c41065c3",
                                        "name": "Other",
                                        "uuid": "67514ed6-03fc-44d0-bfff-571d764cdfb6",
                                    },
                                ],
                                "default_category_uuid": "67514ed6-03fc-44d0-bfff-571d764cdfb6",
                                "operand": "@input.text",
                                "result_name": "result_1",
                                "type": "switch",
                                "wait": {"hint": {"count": 1, "type": "digits"}, "type": "msg"},
                            },
                            "uuid": "98416034-f2f1-4bf7-af68-df6c687462bc",
                        },
                        {
                            "actions": [],
                            "exits": [{"destination_uuid": None, "uuid": "5826040f-e9b2-4a12-9bcd-1814986184a6"}],
                            "router": {
                                "cases": [],
                                "categories": [
                                    {
                                        "exit_uuid": "5826040f-e9b2-4a12-9bcd-1814986184a6",
                                        "name": "All Responses",
                                        "uuid": "2deb0146-a3bc-4dee-8b05-a0c3215706b1",
                                    }
                                ],
                                "default_category_uuid": "2deb0146-a3bc-4dee-8b05-a0c3215706b1",
                                "operand": "@input.text",
                                "result_name": "Result 1",
                                "type": "switch",
                                "wait": {"type": "msg"},
                            },
                            "uuid": "62b84e54-9cbc-4895-bd92-0733bc256e90",
                        },
                    ],
                    "spec_version": "13.1.0",
                    "type": "voice",
                    "uuid": "37640fe3-49e4-4111-bc7b-5b4c54c54bf0",
                    "revision": 6,
                },
            ),
            (
                "nodes_map",
                {
                    "99d93eb4-53da-4638-962a-9ddfc8f8bb6b": OrderedDict(
                        [
                            ("uuid", "99d93eb4-53da-4638-962a-9ddfc8f8bb6b"),
                            ("node_types", ["say_msg"]),
                            ("parent", None),
                            ("children", ["98416034-f2f1-4bf7-af68-df6c687462bc"]),
                            ("has_router", False),
                            ("routing_categories", {}),
                            ("parent_routind_data", {}),
                            (
                                "data",
                                OrderedDict(
                                    [
                                        (
                                            "actions",
                                            [
                                                {
                                                    "text": "Hello",
                                                    "type": "say_msg",
                                                    "uuid": "5845954b-1e6d-4826-8dfe-4d7405236ed9",
                                                }
                                            ],
                                        ),
                                        (
                                            "exits",
                                            [
                                                {
                                                    "destination_uuid": "98416034-f2f1-4bf7-af68-df6c687462bc",
                                                    "uuid": "7b591a32-5286-4a60-8ff9-53fd53c9f19c",
                                                }
                                            ],
                                        ),
                                        ("uuid", "99d93eb4-53da-4638-962a-9ddfc8f8bb6b"),
                                    ]
                                ),
                            ),
                        ]
                    ),
                    "6cf462db-a29a-439f-82ce-76fc20a6002d": OrderedDict(
                        [
                            ("uuid", "6cf462db-a29a-439f-82ce-76fc20a6002d"),
                            ("node_types", ["send_msg"]),
                            ("parent", "98416034-f2f1-4bf7-af68-df6c687462bc"),
                            ("children", ["62b84e54-9cbc-4895-bd92-0733bc256e90"]),
                            ("has_router", False),
                            ("routing_categories", {}),
                            ("parent_routind_data", {"98416034-f2f1-4bf7-af68-df6c687462bc": "OK"}),
                            (
                                "data",
                                OrderedDict(
                                    [
                                        (
                                            "actions",
                                            [
                                                {
                                                    "attachments": [],
                                                    "quick_replies": [],
                                                    "text": "Hello",
                                                    "type": "send_msg",
                                                    "uuid": "27f5bfe8-863c-42ec-8bff-6aa393c8ad23",
                                                }
                                            ],
                                        ),
                                        (
                                            "exits",
                                            [
                                                {
                                                    "destination_uuid": "62b84e54-9cbc-4895-bd92-0733bc256e90",
                                                    "uuid": "a01f0d56-79c8-4ad6-9350-1b9fa2c2575e",
                                                }
                                            ],
                                        ),
                                        ("uuid", "6cf462db-a29a-439f-82ce-76fc20a6002d"),
                                    ]
                                ),
                            ),
                        ]
                    ),
                    "98416034-f2f1-4bf7-af68-df6c687462bc": OrderedDict(
                        [
                            ("uuid", "98416034-f2f1-4bf7-af68-df6c687462bc"),
                            ("node_types", ["switch"]),
                            ("parent", "99d93eb4-53da-4638-962a-9ddfc8f8bb6b"),
                            ("children", ["6cf462db-a29a-439f-82ce-76fc20a6002d"]),
                            ("has_router", True),
                            (
                                "routing_categories",
                                {"OK": "6cf462db-a29a-439f-82ce-76fc20a6002d", "Cancel": None, "Other": None},
                            ),
                            ("parent_routind_data", {}),
                            (
                                "data",
                                OrderedDict(
                                    [
                                        ("actions", []),
                                        (
                                            "exits",
                                            [
                                                {
                                                    "uuid": "d012a99d-3f5b-4247-8c47-e01cb7edc064",
                                                    "destination_uuid": "6cf462db-a29a-439f-82ce-76fc20a6002d",
                                                },
                                                {"uuid": "530df367-64c2-4e01-834b-db702e6c919d"},
                                                {
                                                    "destination_uuid": None,
                                                    "uuid": "a52450ab-9a99-48a7-8554-aeb9c41065c3",
                                                },
                                            ],
                                        ),
                                        (
                                            "router",
                                            {
                                                "cases": [
                                                    {
                                                        "arguments": ["1"],
                                                        "category_uuid": "3b4246c9-1846-4af1-99d0-3cb1896713b5",
                                                        "type": "has_number_eq",
                                                        "uuid": "7cd6da07-f5ce-477a-aee9-926e0be61fbd",
                                                    },
                                                    {
                                                        "arguments": ["0"],
                                                        "category_uuid": "9b184eb2-5444-42cb-8e9f-fa2c0a1599dd",
                                                        "type": "has_number_eq",
                                                        "uuid": "7380896c-5cc1-43dc-adf4-c31ee17d1780",
                                                    },
                                                ],
                                                "categories": [
                                                    {
                                                        "exit_uuid": "d012a99d-3f5b-4247-8c47-e01cb7edc064",
                                                        "name": "OK",
                                                        "uuid": "3b4246c9-1846-4af1-99d0-3cb1896713b5",
                                                    },
                                                    {
                                                        "exit_uuid": "530df367-64c2-4e01-834b-db702e6c919d",
                                                        "name": "Cancel",
                                                        "uuid": "9b184eb2-5444-42cb-8e9f-fa2c0a1599dd",
                                                    },
                                                    {
                                                        "exit_uuid": "a52450ab-9a99-48a7-8554-aeb9c41065c3",
                                                        "name": "Other",
                                                        "uuid": "67514ed6-03fc-44d0-bfff-571d764cdfb6",
                                                    },
                                                ],
                                                "default_category_uuid": "67514ed6-03fc-44d0-bfff-571d764cdfb6",
                                                "operand": "@input.text",
                                                "result_name": "result_1",
                                                "type": "switch",
                                                "wait": {"hint": {"count": 1, "type": "digits"}, "type": "msg"},
                                            },
                                        ),
                                        ("uuid", "98416034-f2f1-4bf7-af68-df6c687462bc"),
                                    ]
                                ),
                            ),
                        ]
                    ),
                    "62b84e54-9cbc-4895-bd92-0733bc256e90": OrderedDict(
                        [
                            ("uuid", "62b84e54-9cbc-4895-bd92-0733bc256e90"),
                            ("node_types", ["switch"]),
                            ("parent", "6cf462db-a29a-439f-82ce-76fc20a6002d"),
                            ("children", []),
                            ("has_router", True),
                            ("routing_categories", {}),
                            ("parent_routind_data", {}),
                            (
                                "data",
                                OrderedDict(
                                    [
                                        ("actions", []),
                                        (
                                            "exits",
                                            [
                                                {
                                                    "destination_uuid": None,
                                                    "uuid": "5826040f-e9b2-4a12-9bcd-1814986184a6",
                                                }
                                            ],
                                        ),
                                        (
                                            "router",
                                            {
                                                "cases": [],
                                                "categories": [
                                                    {
                                                        "exit_uuid": "5826040f-e9b2-4a12-9bcd-1814986184a6",
                                                        "name": "All Responses",
                                                        "uuid": "2deb0146-a3bc-4dee-8b05-a0c3215706b1",
                                                    }
                                                ],
                                                "default_category_uuid": "2deb0146-a3bc-4dee-8b05-a0c3215706b1",
                                                "operand": "@input.text",
                                                "result_name": "Result 1",
                                                "type": "switch",
                                                "wait": {"type": "msg"},
                                            },
                                        ),
                                        ("uuid", "62b84e54-9cbc-4895-bd92-0733bc256e90"),
                                    ]
                                ),
                            ),
                        ]
                    ),
                },
            ),
            (
                "edges_map",
                {
                    "99d93eb4-53da-4638-962a-9ddfc8f8bb6b": ["98416034-f2f1-4bf7-af68-df6c687462bc"],
                    "6cf462db-a29a-439f-82ce-76fc20a6002d": ["62b84e54-9cbc-4895-bd92-0733bc256e90"],
                    "98416034-f2f1-4bf7-af68-df6c687462bc": ["6cf462db-a29a-439f-82ce-76fc20a6002d"],
                },
            ),
        ]
    ),
    "right_graph": OrderedDict(
        [
            (
                "resource",
                {
                    "name": "Surveyor Flow",
                    "uuid": "2672b4d9-cac8-4797-acd5-0cb5e3af5f32",
                    "spec_version": "13.1.0",
                    "language": "eng",
                    "type": "messaging_offline",
                    "nodes": [],
                    "_ui": {},
                    "revision": 1,
                    "expire_after_minutes": 10080,
                },
            ),
            ("nodes_map", {}),
            ("edges_map", {}),
        ]
    ),
    "diff_nodes_map": {
        "99d93eb4-53da-4638-962a-9ddfc8f8bb6b": OrderedDict(
            [
                ("uuid", "99d93eb4-53da-4638-962a-9ddfc8f8bb6b"),
                (
                    "left_origin_node",
                    OrderedDict(
                        [
                            ("uuid", "99d93eb4-53da-4638-962a-9ddfc8f8bb6b"),
                            ("node_types", ["say_msg"]),
                            ("parent", None),
                            ("children", ["98416034-f2f1-4bf7-af68-df6c687462bc"]),
                            ("has_router", False),
                            ("routing_categories", {}),
                            ("parent_routind_data", {}),
                            (
                                "data",
                                OrderedDict(
                                    [
                                        (
                                            "actions",
                                            [
                                                {
                                                    "text": "Hello",
                                                    "type": "say_msg",
                                                    "uuid": "5845954b-1e6d-4826-8dfe-4d7405236ed9",
                                                }
                                            ],
                                        ),
                                        (
                                            "exits",
                                            [
                                                {
                                                    "destination_uuid": "98416034-f2f1-4bf7-af68-df6c687462bc",
                                                    "uuid": "7b591a32-5286-4a60-8ff9-53fd53c9f19c",
                                                }
                                            ],
                                        ),
                                        ("uuid", "99d93eb4-53da-4638-962a-9ddfc8f8bb6b"),
                                    ]
                                ),
                            ),
                        ]
                    ),
                ),
                ("right_origin_node", None),
                ("conflicts", []),
                (
                    "data",
                    OrderedDict(
                        [
                            ("uuid", "99d93eb4-53da-4638-962a-9ddfc8f8bb6b"),
                            (
                                "actions",
                                [{"text": "Hello", "type": "say_msg", "uuid": "5845954b-1e6d-4826-8dfe-4d7405236ed9"}],
                            ),
                            (
                                "exits",
                                [
                                    {
                                        "destination_uuid": "98416034-f2f1-4bf7-af68-df6c687462bc",
                                        "uuid": "7b591a32-5286-4a60-8ff9-53fd53c9f19c",
                                    }
                                ],
                            ),
                        ]
                    ),
                ),
            ]
        ),
        "98416034-f2f1-4bf7-af68-df6c687462bc": OrderedDict(
            [
                ("uuid", "98416034-f2f1-4bf7-af68-df6c687462bc"),
                (
                    "left_origin_node",
                    OrderedDict(
                        [
                            ("uuid", "98416034-f2f1-4bf7-af68-df6c687462bc"),
                            ("node_types", ["switch"]),
                            ("parent", "99d93eb4-53da-4638-962a-9ddfc8f8bb6b"),
                            ("children", ["6cf462db-a29a-439f-82ce-76fc20a6002d"]),
                            ("has_router", True),
                            (
                                "routing_categories",
                                {"OK": "6cf462db-a29a-439f-82ce-76fc20a6002d", "Cancel": None, "Other": None},
                            ),
                            ("parent_routind_data", {}),
                            (
                                "data",
                                OrderedDict(
                                    [
                                        ("actions", []),
                                        (
                                            "exits",
                                            [
                                                {
                                                    "uuid": "d012a99d-3f5b-4247-8c47-e01cb7edc064",
                                                    "destination_uuid": "6cf462db-a29a-439f-82ce-76fc20a6002d",
                                                },
                                                {"uuid": "530df367-64c2-4e01-834b-db702e6c919d"},
                                                {
                                                    "destination_uuid": None,
                                                    "uuid": "a52450ab-9a99-48a7-8554-aeb9c41065c3",
                                                },
                                            ],
                                        ),
                                        (
                                            "router",
                                            {
                                                "cases": [
                                                    {
                                                        "arguments": ["1"],
                                                        "category_uuid": "3b4246c9-1846-4af1-99d0-3cb1896713b5",
                                                        "type": "has_number_eq",
                                                        "uuid": "7cd6da07-f5ce-477a-aee9-926e0be61fbd",
                                                    },
                                                    {
                                                        "arguments": ["0"],
                                                        "category_uuid": "9b184eb2-5444-42cb-8e9f-fa2c0a1599dd",
                                                        "type": "has_number_eq",
                                                        "uuid": "7380896c-5cc1-43dc-adf4-c31ee17d1780",
                                                    },
                                                ],
                                                "categories": [
                                                    {
                                                        "exit_uuid": "d012a99d-3f5b-4247-8c47-e01cb7edc064",
                                                        "name": "OK",
                                                        "uuid": "3b4246c9-1846-4af1-99d0-3cb1896713b5",
                                                    },
                                                    {
                                                        "exit_uuid": "530df367-64c2-4e01-834b-db702e6c919d",
                                                        "name": "Cancel",
                                                        "uuid": "9b184eb2-5444-42cb-8e9f-fa2c0a1599dd",
                                                    },
                                                    {
                                                        "exit_uuid": "a52450ab-9a99-48a7-8554-aeb9c41065c3",
                                                        "name": "Other",
                                                        "uuid": "67514ed6-03fc-44d0-bfff-571d764cdfb6",
                                                    },
                                                ],
                                                "default_category_uuid": "67514ed6-03fc-44d0-bfff-571d764cdfb6",
                                                "operand": "@input.text",
                                                "result_name": "result_1",
                                                "type": "switch",
                                                "wait": {"hint": {"count": 1, "type": "digits"}, "type": "msg"},
                                            },
                                        ),
                                        ("uuid", "98416034-f2f1-4bf7-af68-df6c687462bc"),
                                    ]
                                ),
                            ),
                        ]
                    ),
                ),
                ("right_origin_node", None),
                ("conflicts", []),
                (
                    "data",
                    OrderedDict(
                        [
                            ("uuid", "98416034-f2f1-4bf7-af68-df6c687462bc"),
                            ("actions", []),
                            (
                                "exits",
                                [
                                    {
                                        "uuid": "d012a99d-3f5b-4247-8c47-e01cb7edc064",
                                        "destination_uuid": "6cf462db-a29a-439f-82ce-76fc20a6002d",
                                    },
                                    {"uuid": "530df367-64c2-4e01-834b-db702e6c919d"},
                                    {"destination_uuid": None, "uuid": "a52450ab-9a99-48a7-8554-aeb9c41065c3"},
                                ],
                            ),
                            (
                                "router",
                                {
                                    "cases": [
                                        {
                                            "arguments": ["1"],
                                            "category_uuid": "3b4246c9-1846-4af1-99d0-3cb1896713b5",
                                            "type": "has_number_eq",
                                            "uuid": "7cd6da07-f5ce-477a-aee9-926e0be61fbd",
                                        },
                                        {
                                            "arguments": ["0"],
                                            "category_uuid": "9b184eb2-5444-42cb-8e9f-fa2c0a1599dd",
                                            "type": "has_number_eq",
                                            "uuid": "7380896c-5cc1-43dc-adf4-c31ee17d1780",
                                        },
                                    ],
                                    "categories": [
                                        {
                                            "exit_uuid": "d012a99d-3f5b-4247-8c47-e01cb7edc064",
                                            "name": "OK",
                                            "uuid": "3b4246c9-1846-4af1-99d0-3cb1896713b5",
                                        },
                                        {
                                            "exit_uuid": "530df367-64c2-4e01-834b-db702e6c919d",
                                            "name": "Cancel",
                                            "uuid": "9b184eb2-5444-42cb-8e9f-fa2c0a1599dd",
                                        },
                                        {
                                            "exit_uuid": "a52450ab-9a99-48a7-8554-aeb9c41065c3",
                                            "name": "Other",
                                            "uuid": "67514ed6-03fc-44d0-bfff-571d764cdfb6",
                                        },
                                    ],
                                    "default_category_uuid": "67514ed6-03fc-44d0-bfff-571d764cdfb6",
                                    "operand": "@input.text",
                                    "result_name": "result_1",
                                    "type": "switch",
                                    "wait": {"hint": {"count": 1, "type": "digits"}, "type": "msg"},
                                },
                            ),
                        ]
                    ),
                ),
            ]
        ),
        "6cf462db-a29a-439f-82ce-76fc20a6002d": OrderedDict(
            [
                ("uuid", "6cf462db-a29a-439f-82ce-76fc20a6002d"),
                (
                    "left_origin_node",
                    OrderedDict(
                        [
                            ("uuid", "6cf462db-a29a-439f-82ce-76fc20a6002d"),
                            ("node_types", ["send_msg"]),
                            ("parent", "98416034-f2f1-4bf7-af68-df6c687462bc"),
                            ("children", ["62b84e54-9cbc-4895-bd92-0733bc256e90"]),
                            ("has_router", False),
                            ("routing_categories", {}),
                            ("parent_routind_data", {"98416034-f2f1-4bf7-af68-df6c687462bc": "OK"}),
                            (
                                "data",
                                OrderedDict(
                                    [
                                        (
                                            "actions",
                                            [
                                                {
                                                    "attachments": [],
                                                    "quick_replies": [],
                                                    "text": "Hello",
                                                    "type": "send_msg",
                                                    "uuid": "27f5bfe8-863c-42ec-8bff-6aa393c8ad23",
                                                }
                                            ],
                                        ),
                                        (
                                            "exits",
                                            [
                                                {
                                                    "destination_uuid": "62b84e54-9cbc-4895-bd92-0733bc256e90",
                                                    "uuid": "a01f0d56-79c8-4ad6-9350-1b9fa2c2575e",
                                                }
                                            ],
                                        ),
                                        ("uuid", "6cf462db-a29a-439f-82ce-76fc20a6002d"),
                                    ]
                                ),
                            ),
                        ]
                    ),
                ),
                ("right_origin_node", None),
                ("conflicts", []),
                (
                    "data",
                    OrderedDict(
                        [
                            ("uuid", "6cf462db-a29a-439f-82ce-76fc20a6002d"),
                            (
                                "actions",
                                [
                                    {
                                        "attachments": [],
                                        "quick_replies": [],
                                        "text": "Hello",
                                        "type": "send_msg",
                                        "uuid": "27f5bfe8-863c-42ec-8bff-6aa393c8ad23",
                                    }
                                ],
                            ),
                            (
                                "exits",
                                [
                                    {
                                        "destination_uuid": "62b84e54-9cbc-4895-bd92-0733bc256e90",
                                        "uuid": "a01f0d56-79c8-4ad6-9350-1b9fa2c2575e",
                                    }
                                ],
                            ),
                        ]
                    ),
                ),
            ]
        ),
        "62b84e54-9cbc-4895-bd92-0733bc256e90": OrderedDict(
            [
                ("uuid", "62b84e54-9cbc-4895-bd92-0733bc256e90"),
                (
                    "left_origin_node",
                    OrderedDict(
                        [
                            ("uuid", "62b84e54-9cbc-4895-bd92-0733bc256e90"),
                            ("node_types", ["switch"]),
                            ("parent", "6cf462db-a29a-439f-82ce-76fc20a6002d"),
                            ("children", []),
                            ("has_router", True),
                            ("routing_categories", {}),
                            ("parent_routind_data", {}),
                            (
                                "data",
                                OrderedDict(
                                    [
                                        ("actions", []),
                                        (
                                            "exits",
                                            [
                                                {
                                                    "destination_uuid": None,
                                                    "uuid": "5826040f-e9b2-4a12-9bcd-1814986184a6",
                                                }
                                            ],
                                        ),
                                        (
                                            "router",
                                            {
                                                "cases": [],
                                                "categories": [
                                                    {
                                                        "exit_uuid": "5826040f-e9b2-4a12-9bcd-1814986184a6",
                                                        "name": "All Responses",
                                                        "uuid": "2deb0146-a3bc-4dee-8b05-a0c3215706b1",
                                                    }
                                                ],
                                                "default_category_uuid": "2deb0146-a3bc-4dee-8b05-a0c3215706b1",
                                                "operand": "@input.text",
                                                "result_name": "Result 1",
                                                "type": "switch",
                                                "wait": {"type": "msg"},
                                            },
                                        ),
                                        ("uuid", "62b84e54-9cbc-4895-bd92-0733bc256e90"),
                                    ]
                                ),
                            ),
                        ]
                    ),
                ),
                ("right_origin_node", None),
                ("conflicts", []),
                (
                    "data",
                    OrderedDict(
                        [
                            ("uuid", "62b84e54-9cbc-4895-bd92-0733bc256e90"),
                            ("actions", []),
                            ("exits", [{"destination_uuid": None, "uuid": "5826040f-e9b2-4a12-9bcd-1814986184a6"}]),
                            (
                                "router",
                                {
                                    "cases": [],
                                    "categories": [
                                        {
                                            "exit_uuid": "5826040f-e9b2-4a12-9bcd-1814986184a6",
                                            "name": "All Responses",
                                            "uuid": "2deb0146-a3bc-4dee-8b05-a0c3215706b1",
                                        }
                                    ],
                                    "default_category_uuid": "2deb0146-a3bc-4dee-8b05-a0c3215706b1",
                                    "operand": "@input.text",
                                    "result_name": "Result 1",
                                    "type": "switch",
                                    "wait": {"type": "msg"},
                                },
                            ),
                        ]
                    ),
                ),
            ]
        ),
    },
    "diff_nodes_origin_map": {
        "99d93eb4-53da-4638-962a-9ddfc8f8bb6b": OrderedDict(
            [
                ("uuid", "99d93eb4-53da-4638-962a-9ddfc8f8bb6b"),
                (
                    "left_origin_node",
                    OrderedDict(
                        [
                            ("uuid", "99d93eb4-53da-4638-962a-9ddfc8f8bb6b"),
                            ("node_types", ["say_msg"]),
                            ("parent", None),
                            ("children", ["98416034-f2f1-4bf7-af68-df6c687462bc"]),
                            ("has_router", False),
                            ("routing_categories", {}),
                            ("parent_routind_data", {}),
                            (
                                "data",
                                OrderedDict(
                                    [
                                        (
                                            "actions",
                                            [
                                                {
                                                    "text": "Hello",
                                                    "type": "say_msg",
                                                    "uuid": "5845954b-1e6d-4826-8dfe-4d7405236ed9",
                                                }
                                            ],
                                        ),
                                        (
                                            "exits",
                                            [
                                                {
                                                    "destination_uuid": "98416034-f2f1-4bf7-af68-df6c687462bc",
                                                    "uuid": "7b591a32-5286-4a60-8ff9-53fd53c9f19c",
                                                }
                                            ],
                                        ),
                                        ("uuid", "99d93eb4-53da-4638-962a-9ddfc8f8bb6b"),
                                    ]
                                ),
                            ),
                        ]
                    ),
                ),
                ("right_origin_node", None),
                ("conflicts", []),
                (
                    "data",
                    OrderedDict(
                        [
                            ("uuid", "99d93eb4-53da-4638-962a-9ddfc8f8bb6b"),
                            (
                                "actions",
                                [{"text": "Hello", "type": "say_msg", "uuid": "5845954b-1e6d-4826-8dfe-4d7405236ed9"}],
                            ),
                            (
                                "exits",
                                [
                                    {
                                        "destination_uuid": "98416034-f2f1-4bf7-af68-df6c687462bc",
                                        "uuid": "7b591a32-5286-4a60-8ff9-53fd53c9f19c",
                                    }
                                ],
                            ),
                        ]
                    ),
                ),
            ]
        ),
        "98416034-f2f1-4bf7-af68-df6c687462bc": OrderedDict(
            [
                ("uuid", "98416034-f2f1-4bf7-af68-df6c687462bc"),
                (
                    "left_origin_node",
                    OrderedDict(
                        [
                            ("uuid", "98416034-f2f1-4bf7-af68-df6c687462bc"),
                            ("node_types", ["switch"]),
                            ("parent", "99d93eb4-53da-4638-962a-9ddfc8f8bb6b"),
                            ("children", ["6cf462db-a29a-439f-82ce-76fc20a6002d"]),
                            ("has_router", True),
                            (
                                "routing_categories",
                                {"OK": "6cf462db-a29a-439f-82ce-76fc20a6002d", "Cancel": None, "Other": None},
                            ),
                            ("parent_routind_data", {}),
                            (
                                "data",
                                OrderedDict(
                                    [
                                        ("actions", []),
                                        (
                                            "exits",
                                            [
                                                {
                                                    "uuid": "d012a99d-3f5b-4247-8c47-e01cb7edc064",
                                                    "destination_uuid": "6cf462db-a29a-439f-82ce-76fc20a6002d",
                                                },
                                                {"uuid": "530df367-64c2-4e01-834b-db702e6c919d"},
                                                {
                                                    "destination_uuid": None,
                                                    "uuid": "a52450ab-9a99-48a7-8554-aeb9c41065c3",
                                                },
                                            ],
                                        ),
                                        (
                                            "router",
                                            {
                                                "cases": [
                                                    {
                                                        "arguments": ["1"],
                                                        "category_uuid": "3b4246c9-1846-4af1-99d0-3cb1896713b5",
                                                        "type": "has_number_eq",
                                                        "uuid": "7cd6da07-f5ce-477a-aee9-926e0be61fbd",
                                                    },
                                                    {
                                                        "arguments": ["0"],
                                                        "category_uuid": "9b184eb2-5444-42cb-8e9f-fa2c0a1599dd",
                                                        "type": "has_number_eq",
                                                        "uuid": "7380896c-5cc1-43dc-adf4-c31ee17d1780",
                                                    },
                                                ],
                                                "categories": [
                                                    {
                                                        "exit_uuid": "d012a99d-3f5b-4247-8c47-e01cb7edc064",
                                                        "name": "OK",
                                                        "uuid": "3b4246c9-1846-4af1-99d0-3cb1896713b5",
                                                    },
                                                    {
                                                        "exit_uuid": "530df367-64c2-4e01-834b-db702e6c919d",
                                                        "name": "Cancel",
                                                        "uuid": "9b184eb2-5444-42cb-8e9f-fa2c0a1599dd",
                                                    },
                                                    {
                                                        "exit_uuid": "a52450ab-9a99-48a7-8554-aeb9c41065c3",
                                                        "name": "Other",
                                                        "uuid": "67514ed6-03fc-44d0-bfff-571d764cdfb6",
                                                    },
                                                ],
                                                "default_category_uuid": "67514ed6-03fc-44d0-bfff-571d764cdfb6",
                                                "operand": "@input.text",
                                                "result_name": "result_1",
                                                "type": "switch",
                                                "wait": {"hint": {"count": 1, "type": "digits"}, "type": "msg"},
                                            },
                                        ),
                                        ("uuid", "98416034-f2f1-4bf7-af68-df6c687462bc"),
                                    ]
                                ),
                            ),
                        ]
                    ),
                ),
                ("right_origin_node", None),
                ("conflicts", []),
                (
                    "data",
                    OrderedDict(
                        [
                            ("uuid", "98416034-f2f1-4bf7-af68-df6c687462bc"),
                            ("actions", []),
                            (
                                "exits",
                                [
                                    {
                                        "uuid": "d012a99d-3f5b-4247-8c47-e01cb7edc064",
                                        "destination_uuid": "6cf462db-a29a-439f-82ce-76fc20a6002d",
                                    },
                                    {"uuid": "530df367-64c2-4e01-834b-db702e6c919d"},
                                    {"destination_uuid": None, "uuid": "a52450ab-9a99-48a7-8554-aeb9c41065c3"},
                                ],
                            ),
                            (
                                "router",
                                {
                                    "cases": [
                                        {
                                            "arguments": ["1"],
                                            "category_uuid": "3b4246c9-1846-4af1-99d0-3cb1896713b5",
                                            "type": "has_number_eq",
                                            "uuid": "7cd6da07-f5ce-477a-aee9-926e0be61fbd",
                                        },
                                        {
                                            "arguments": ["0"],
                                            "category_uuid": "9b184eb2-5444-42cb-8e9f-fa2c0a1599dd",
                                            "type": "has_number_eq",
                                            "uuid": "7380896c-5cc1-43dc-adf4-c31ee17d1780",
                                        },
                                    ],
                                    "categories": [
                                        {
                                            "exit_uuid": "d012a99d-3f5b-4247-8c47-e01cb7edc064",
                                            "name": "OK",
                                            "uuid": "3b4246c9-1846-4af1-99d0-3cb1896713b5",
                                        },
                                        {
                                            "exit_uuid": "530df367-64c2-4e01-834b-db702e6c919d",
                                            "name": "Cancel",
                                            "uuid": "9b184eb2-5444-42cb-8e9f-fa2c0a1599dd",
                                        },
                                        {
                                            "exit_uuid": "a52450ab-9a99-48a7-8554-aeb9c41065c3",
                                            "name": "Other",
                                            "uuid": "67514ed6-03fc-44d0-bfff-571d764cdfb6",
                                        },
                                    ],
                                    "default_category_uuid": "67514ed6-03fc-44d0-bfff-571d764cdfb6",
                                    "operand": "@input.text",
                                    "result_name": "result_1",
                                    "type": "switch",
                                    "wait": {"hint": {"count": 1, "type": "digits"}, "type": "msg"},
                                },
                            ),
                        ]
                    ),
                ),
            ]
        ),
        "6cf462db-a29a-439f-82ce-76fc20a6002d": OrderedDict(
            [
                ("uuid", "6cf462db-a29a-439f-82ce-76fc20a6002d"),
                (
                    "left_origin_node",
                    OrderedDict(
                        [
                            ("uuid", "6cf462db-a29a-439f-82ce-76fc20a6002d"),
                            ("node_types", ["send_msg"]),
                            ("parent", "98416034-f2f1-4bf7-af68-df6c687462bc"),
                            ("children", ["62b84e54-9cbc-4895-bd92-0733bc256e90"]),
                            ("has_router", False),
                            ("routing_categories", {}),
                            ("parent_routind_data", {"98416034-f2f1-4bf7-af68-df6c687462bc": "OK"}),
                            (
                                "data",
                                OrderedDict(
                                    [
                                        (
                                            "actions",
                                            [
                                                {
                                                    "attachments": [],
                                                    "quick_replies": [],
                                                    "text": "Hello",
                                                    "type": "send_msg",
                                                    "uuid": "27f5bfe8-863c-42ec-8bff-6aa393c8ad23",
                                                }
                                            ],
                                        ),
                                        (
                                            "exits",
                                            [
                                                {
                                                    "destination_uuid": "62b84e54-9cbc-4895-bd92-0733bc256e90",
                                                    "uuid": "a01f0d56-79c8-4ad6-9350-1b9fa2c2575e",
                                                }
                                            ],
                                        ),
                                        ("uuid", "6cf462db-a29a-439f-82ce-76fc20a6002d"),
                                    ]
                                ),
                            ),
                        ]
                    ),
                ),
                ("right_origin_node", None),
                ("conflicts", []),
                (
                    "data",
                    OrderedDict(
                        [
                            ("uuid", "6cf462db-a29a-439f-82ce-76fc20a6002d"),
                            (
                                "actions",
                                [
                                    {
                                        "attachments": [],
                                        "quick_replies": [],
                                        "text": "Hello",
                                        "type": "send_msg",
                                        "uuid": "27f5bfe8-863c-42ec-8bff-6aa393c8ad23",
                                    }
                                ],
                            ),
                            (
                                "exits",
                                [
                                    {
                                        "destination_uuid": "62b84e54-9cbc-4895-bd92-0733bc256e90",
                                        "uuid": "a01f0d56-79c8-4ad6-9350-1b9fa2c2575e",
                                    }
                                ],
                            ),
                        ]
                    ),
                ),
            ]
        ),
        "62b84e54-9cbc-4895-bd92-0733bc256e90": OrderedDict(
            [
                ("uuid", "62b84e54-9cbc-4895-bd92-0733bc256e90"),
                (
                    "left_origin_node",
                    OrderedDict(
                        [
                            ("uuid", "62b84e54-9cbc-4895-bd92-0733bc256e90"),
                            ("node_types", ["switch"]),
                            ("parent", "6cf462db-a29a-439f-82ce-76fc20a6002d"),
                            ("children", []),
                            ("has_router", True),
                            ("routing_categories", {}),
                            ("parent_routind_data", {}),
                            (
                                "data",
                                OrderedDict(
                                    [
                                        ("actions", []),
                                        (
                                            "exits",
                                            [
                                                {
                                                    "destination_uuid": None,
                                                    "uuid": "5826040f-e9b2-4a12-9bcd-1814986184a6",
                                                }
                                            ],
                                        ),
                                        (
                                            "router",
                                            {
                                                "cases": [],
                                                "categories": [
                                                    {
                                                        "exit_uuid": "5826040f-e9b2-4a12-9bcd-1814986184a6",
                                                        "name": "All Responses",
                                                        "uuid": "2deb0146-a3bc-4dee-8b05-a0c3215706b1",
                                                    }
                                                ],
                                                "default_category_uuid": "2deb0146-a3bc-4dee-8b05-a0c3215706b1",
                                                "operand": "@input.text",
                                                "result_name": "Result 1",
                                                "type": "switch",
                                                "wait": {"type": "msg"},
                                            },
                                        ),
                                        ("uuid", "62b84e54-9cbc-4895-bd92-0733bc256e90"),
                                    ]
                                ),
                            ),
                        ]
                    ),
                ),
                ("right_origin_node", None),
                ("conflicts", []),
                (
                    "data",
                    OrderedDict(
                        [
                            ("uuid", "62b84e54-9cbc-4895-bd92-0733bc256e90"),
                            ("actions", []),
                            ("exits", [{"destination_uuid": None, "uuid": "5826040f-e9b2-4a12-9bcd-1814986184a6"}]),
                            (
                                "router",
                                {
                                    "cases": [],
                                    "categories": [
                                        {
                                            "exit_uuid": "5826040f-e9b2-4a12-9bcd-1814986184a6",
                                            "name": "All Responses",
                                            "uuid": "2deb0146-a3bc-4dee-8b05-a0c3215706b1",
                                        }
                                    ],
                                    "default_category_uuid": "2deb0146-a3bc-4dee-8b05-a0c3215706b1",
                                    "operand": "@input.text",
                                    "result_name": "Result 1",
                                    "type": "switch",
                                    "wait": {"type": "msg"},
                                },
                            ),
                        ]
                    ),
                ),
            ]
        ),
    },
    "diff_nodes_edges": {
        "6cf462db-a29a-439f-82ce-76fc20a6002d": ["62b84e54-9cbc-4895-bd92-0733bc256e90"],
        "98416034-f2f1-4bf7-af68-df6c687462bc": ["6cf462db-a29a-439f-82ce-76fc20a6002d"],
        "99d93eb4-53da-4638-962a-9ddfc8f8bb6b": ["98416034-f2f1-4bf7-af68-df6c687462bc"],
    },
    "definition": OrderedDict(
        [
            (
                "_ui",
                {
                    "nodes": {
                        "99d93eb4-53da-4638-962a-9ddfc8f8bb6b": {
                            "position": {"left": 440, "top": 0},
                            "type": "execute_actions",
                        },
                        "98416034-f2f1-4bf7-af68-df6c687462bc": {
                            "config": {"cases": {}},
                            "position": {"left": 440, "top": 120},
                            "type": "wait_for_response",
                        },
                        "6cf462db-a29a-439f-82ce-76fc20a6002d": {
                            "position": {"left": 140, "top": 20},
                            "type": "execute_actions",
                        },
                        "62b84e54-9cbc-4895-bd92-0733bc256e90": {
                            "config": {"cases": {}},
                            "position": {"left": 160, "top": 160},
                            "type": "wait_for_response",
                        },
                    }
                },
            ),
            ("expire_after_minutes", 10080),
            ("language", "base"),
            ("localization", {}),
            ("metadata", {"revision": 5}),
            ("name", "Merge of Phone Call with Surveyor"),
            (
                "nodes",
                [
                    OrderedDict(
                        [
                            ("uuid", "99d93eb4-53da-4638-962a-9ddfc8f8bb6b"),
                            (
                                "actions",
                                [{"text": "Hello", "type": "say_msg", "uuid": "5845954b-1e6d-4826-8dfe-4d7405236ed9"}],
                            ),
                            (
                                "exits",
                                [
                                    {
                                        "destination_uuid": "98416034-f2f1-4bf7-af68-df6c687462bc",
                                        "uuid": "7b591a32-5286-4a60-8ff9-53fd53c9f19c",
                                    }
                                ],
                            ),
                        ]
                    ),
                    OrderedDict(
                        [
                            ("uuid", "6cf462db-a29a-439f-82ce-76fc20a6002d"),
                            (
                                "actions",
                                [
                                    {
                                        "attachments": [],
                                        "quick_replies": [],
                                        "text": "Hello",
                                        "type": "send_msg",
                                        "uuid": "27f5bfe8-863c-42ec-8bff-6aa393c8ad23",
                                    }
                                ],
                            ),
                            (
                                "exits",
                                [
                                    {
                                        "destination_uuid": "62b84e54-9cbc-4895-bd92-0733bc256e90",
                                        "uuid": "a01f0d56-79c8-4ad6-9350-1b9fa2c2575e",
                                    }
                                ],
                            ),
                        ]
                    ),
                    OrderedDict(
                        [
                            ("uuid", "98416034-f2f1-4bf7-af68-df6c687462bc"),
                            ("actions", []),
                            (
                                "exits",
                                [
                                    {
                                        "uuid": "d012a99d-3f5b-4247-8c47-e01cb7edc064",
                                        "destination_uuid": "6cf462db-a29a-439f-82ce-76fc20a6002d",
                                    },
                                    {"uuid": "530df367-64c2-4e01-834b-db702e6c919d"},
                                    {"destination_uuid": None, "uuid": "a52450ab-9a99-48a7-8554-aeb9c41065c3"},
                                ],
                            ),
                            (
                                "router",
                                {
                                    "cases": [
                                        {
                                            "arguments": ["1"],
                                            "category_uuid": "3b4246c9-1846-4af1-99d0-3cb1896713b5",
                                            "type": "has_number_eq",
                                            "uuid": "7cd6da07-f5ce-477a-aee9-926e0be61fbd",
                                        },
                                        {
                                            "arguments": ["0"],
                                            "category_uuid": "9b184eb2-5444-42cb-8e9f-fa2c0a1599dd",
                                            "type": "has_number_eq",
                                            "uuid": "7380896c-5cc1-43dc-adf4-c31ee17d1780",
                                        },
                                    ],
                                    "categories": [
                                        {
                                            "exit_uuid": "d012a99d-3f5b-4247-8c47-e01cb7edc064",
                                            "name": "OK",
                                            "uuid": "3b4246c9-1846-4af1-99d0-3cb1896713b5",
                                        },
                                        {
                                            "exit_uuid": "530df367-64c2-4e01-834b-db702e6c919d",
                                            "name": "Cancel",
                                            "uuid": "9b184eb2-5444-42cb-8e9f-fa2c0a1599dd",
                                        },
                                        {
                                            "exit_uuid": "a52450ab-9a99-48a7-8554-aeb9c41065c3",
                                            "name": "Other",
                                            "uuid": "67514ed6-03fc-44d0-bfff-571d764cdfb6",
                                        },
                                    ],
                                    "default_category_uuid": "67514ed6-03fc-44d0-bfff-571d764cdfb6",
                                    "operand": "@input.text",
                                    "result_name": "result_1",
                                    "type": "switch",
                                    "wait": {"hint": {"count": 1, "type": "digits"}, "type": "msg"},
                                },
                            ),
                        ]
                    ),
                    OrderedDict(
                        [
                            ("uuid", "62b84e54-9cbc-4895-bd92-0733bc256e90"),
                            ("actions", []),
                            ("exits", [{"destination_uuid": None, "uuid": "5826040f-e9b2-4a12-9bcd-1814986184a6"}]),
                            (
                                "router",
                                {
                                    "cases": [],
                                    "categories": [
                                        {
                                            "exit_uuid": "5826040f-e9b2-4a12-9bcd-1814986184a6",
                                            "name": "All Responses",
                                            "uuid": "2deb0146-a3bc-4dee-8b05-a0c3215706b1",
                                        }
                                    ],
                                    "default_category_uuid": "2deb0146-a3bc-4dee-8b05-a0c3215706b1",
                                    "operand": "@input.text",
                                    "result_name": "Result 1",
                                    "type": "switch",
                                    "wait": {"type": "msg"},
                                },
                            ),
                        ]
                    ),
                ],
            ),
            ("spec_version", "13.1.0"),
            ("type", "voice"),
            ("uuid", "37640fe3-49e4-4111-bc7b-5b4c54c54bf0"),
            ("revision", 6),
        ]
    ),
    "conflicts": {},
}
