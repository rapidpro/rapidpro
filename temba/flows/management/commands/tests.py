from django.core.management import call_command

from temba.flows.models import FlowNodeCount
from temba.tests import FlowFileTest, TembaTest
from temba.tests.engine import MockSessionWriter

from .run_audit import has_none_string_in


class MigrateFlowsTest(FlowFileTest):
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


class RecalcNodeCountsTest(FlowFileTest):
    def setUp(self):
        super().setUp()

        self.contact2 = self.create_contact("Joe", number="+12065550002")
        self.contact3 = self.create_contact("Frank", number="+12065550003")

    def test_recalc_node_counts(self):
        def check_node_count_rebuild(flow, assert_count):
            node_counts = FlowNodeCount.get_totals(flow)

            call_command("recalc_node_counts", flow_id=flow.id)

            new_counts = FlowNodeCount.get_totals(flow)
            self.assertEqual(new_counts, node_counts)
            self.assertEqual(assert_count, sum(new_counts.values()))

        flow = self.get_flow("favorites_v13")
        nodes = flow.as_json()["nodes"]

        color_prompt = nodes[0]
        color_other = nodes[1]
        color_split = nodes[2]
        beer_prompt = nodes[3]
        beer_split = nodes[5]
        name_prompt = nodes[6]
        name_split = nodes[7]
        name_reply = nodes[8]

        session1 = MockSessionWriter(self.contact, flow).visit(color_prompt).visit(color_split).wait().save()
        session2 = MockSessionWriter(self.contact2, flow).visit(color_prompt).visit(color_split).wait().save()
        session3 = MockSessionWriter(self.contact3, flow).visit(color_prompt).visit(color_split).wait().save()

        # recalculate node counts and check they are the same
        check_node_count_rebuild(flow, 3)

        (
            session1.resume(self.create_msg(text="Blue", contact=self.contact, direction="I"))
            .visit(beer_prompt)
            .visit(beer_split)
            .wait()
            .save()
        )
        (
            session2.resume(self.create_msg(text="Beige", contact=self.contact2, direction="I"))
            .visit(color_other)
            .visit(color_split)
            .wait()
            .save()
        )
        (
            session3.resume(self.create_msg(text="Amber", contact=self.contact3, direction="I"))
            .visit(color_other)
            .visit(color_split)
            .wait()
            .save()
        )

        check_node_count_rebuild(flow, 3)

        (
            session1.resume(self.create_msg(text="Primus", contact=self.contact, direction="I"))
            .visit(name_prompt)
            .visit(name_split)
            .wait()
            .save()
        )
        (
            session2.resume(self.create_msg(text="Orange", contact=self.contact2, direction="I"))
            .visit(color_other)
            .visit(color_split)
            .wait()
            .save()
        )
        (
            session3.resume(self.create_msg(text="Amber", contact=self.contact3, direction="I"))
            .visit(color_other)
            .visit(color_split)
            .wait()
            .save()
        )

        check_node_count_rebuild(flow, 3)

        # contact1 replies with name to complete the flow
        (
            session1.resume(self.create_msg(text="Bob", contact=self.contact, direction="I"))
            .visit(name_reply)
            .complete()
            .save()
        )

        check_node_count_rebuild(flow, 2)
