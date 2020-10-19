from django.core.management import call_command

from temba.flows.models import FlowNodeCount
from temba.tests import TembaTest
from temba.tests.engine import MockSessionWriter

from .run_audit import has_none_string_in


class MigrateFlowsTest(TembaTest):
    def test_migrate_flows(self):
        call_command("migrate_flows")


class RunAuditTest(TembaTest):
    def test_has_none_string_in(self):
        self.assertTrue(has_none_string_in("None"))
        self.assertTrue(has_none_string_in({"foo": "None"}))
        self.assertTrue(has_none_string_in(["None"]))
        self.assertTrue(has_none_string_in({"foo": {"bar": ["None"]}}))

        self.assertFalse(has_none_string_in(None))
        self.assertFalse(has_none_string_in({"foo": None}))
        self.assertFalse(has_none_string_in("abc"))
        self.assertFalse(has_none_string_in("123"))
        self.assertFalse(has_none_string_in({"foo": {"bar": ["abc"]}}))


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
