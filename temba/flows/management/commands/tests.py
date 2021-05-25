from django.core.management import call_command

from temba.flows.models import FlowNodeCount
from temba.tests import TembaTest
from temba.tests.engine import MockSessionWriter


class MigrateFlowsTest(TembaTest):
    def test_command(self):
        call_command("migrate_flows")


class InspectFlowsTest(TembaTest):
    def test_command(self):
        # flow which wrongly has has_issues set
        flow1 = self.create_flow("No Problems")
        flow1.has_issues = True
        flow1.save(update_fields=("has_issues",))

        # create flow with a bad_regex issue but clear has_issues
        flow2 = self.create_flow(
            "Bad Regex",
            nodes=[
                {
                    "uuid": "f3d5ccd0-fee0-4955-bcb7-21613f049eae",
                    "router": {
                        "type": "switch",
                        "categories": [
                            {
                                "uuid": "fc4ee6b0-af6f-42e3-ae84-153c313e390a",
                                "name": "Bad Regex",
                                "exit_uuid": "72a3f1da-bde1-4549-a986-d35809807be8",
                            },
                            {
                                "uuid": "78ae8f05-f92e-43b2-a886-406eaea1b8e0",
                                "name": "Other",
                                "exit_uuid": "72a3f1da-bde1-4549-a986-d35809807be8",
                            },
                        ],
                        "default_category_uuid": "78ae8f05-f92e-43b2-a886-406eaea1b8e0",
                        "operand": "@input.text",
                        "cases": [
                            {
                                "uuid": "98503572-25bf-40ce-ad72-8836b6549a38",
                                "type": "has_pattern",
                                "arguments": ["[["],
                                "category_uuid": "fc4ee6b0-af6f-42e3-ae84-153c313e390a",
                            }
                        ],
                    },
                    "exits": [{"uuid": "72a3f1da-bde1-4549-a986-d35809807be8"}],
                }
            ],
        )
        flow2.has_issues = False
        flow2.save(update_fields=("has_issues",))

        call_command("inspect_flows")

        flow1.refresh_from_db()
        self.assertFalse(flow1.has_issues)

        flow2.refresh_from_db()
        self.assertTrue(flow2.has_issues)


class RecalcNodeCountsTest(TembaTest):
    def test_recalc_node_counts(self):
        contact1 = self.create_contact("Ben Haggerty", phone="+12065552020")
        contact2 = self.create_contact("Joe", phone="+12065550002")
        contact3 = self.create_contact("Frank", phone="+12065550003")

        def check_node_count_rebuild(flow, assert_count):
            node_counts = FlowNodeCount.get_totals(flow)

            call_command("recalc_node_counts", flow_id=flow.id)

            new_counts = FlowNodeCount.get_totals(flow)
            self.assertEqual(new_counts, node_counts)
            self.assertEqual(assert_count, sum(new_counts.values()))

        flow = self.get_flow("favorites_v13")
        nodes = flow.get_definition()["nodes"]

        color_prompt = nodes[0]
        color_other = nodes[1]
        color_split = nodes[2]
        beer_prompt = nodes[3]
        beer_split = nodes[5]
        name_prompt = nodes[6]
        name_split = nodes[7]
        name_reply = nodes[8]

        session1 = MockSessionWriter(contact1, flow).visit(color_prompt).visit(color_split).wait().save()
        session2 = MockSessionWriter(contact2, flow).visit(color_prompt).visit(color_split).wait().save()
        session3 = MockSessionWriter(contact3, flow).visit(color_prompt).visit(color_split).wait().save()

        # recalculate node counts and check they are the same
        check_node_count_rebuild(flow, 3)

        (
            session1.resume(self.create_incoming_msg(contact1, "Blue"))
            .visit(beer_prompt)
            .visit(beer_split)
            .wait()
            .save()
        )
        (
            session2.resume(self.create_incoming_msg(contact2, "Beige"))
            .visit(color_other)
            .visit(color_split)
            .wait()
            .save()
        )
        (
            session3.resume(self.create_incoming_msg(contact3, "Amber"))
            .visit(color_other)
            .visit(color_split)
            .wait()
            .save()
        )

        check_node_count_rebuild(flow, 3)

        (
            session1.resume(self.create_incoming_msg(contact1, "Primus"))
            .visit(name_prompt)
            .visit(name_split)
            .wait()
            .save()
        )
        (
            session2.resume(self.create_incoming_msg(contact2, "Orange"))
            .visit(color_other)
            .visit(color_split)
            .wait()
            .save()
        )
        (
            session3.resume(self.create_incoming_msg(contact3, "Amber"))
            .visit(color_other)
            .visit(color_split)
            .wait()
            .save()
        )

        check_node_count_rebuild(flow, 3)

        # contact1 replies with name to complete the flow
        (session1.resume(self.create_incoming_msg(contact1, "Bob")).visit(name_reply).complete().save())

        check_node_count_rebuild(flow, 2)
