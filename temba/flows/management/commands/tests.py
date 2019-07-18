from django.core.management import call_command

from temba.flows.models import FlowNodeCount
from temba.tests import FlowFileTest, TembaTest, uses_legacy_engine

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

    @uses_legacy_engine
    def test_recalc_node_counts(self):
        def check_node_count_rebuild(flow, assert_count):
            node_counts = FlowNodeCount.get_totals(flow)

            call_command("recalc_node_counts", flow_id=flow.id)

            new_counts = FlowNodeCount.get_totals(flow)
            self.assertEqual(new_counts, node_counts)
            self.assertEqual(sum(new_counts.values()), assert_count)

        flow = self.get_flow("favorites")

        flow.start([], [self.contact, self.contact2, self.contact3])

        # recalculate node counts and check they are the same
        check_node_count_rebuild(flow, 3)

        self.send_message(flow, "Blue", contact=self.contact)
        self.send_message(flow, "Beige", contact=self.contact2)
        self.send_message(flow, "Amber", contact=self.contact3)

        check_node_count_rebuild(flow, 3)

        self.send_message(flow, "Primus", contact=self.contact)
        self.send_message(flow, "Orange", contact=self.contact2)
        self.send_message(flow, "Amber", contact=self.contact3)

        check_node_count_rebuild(flow, 3)

        self.send_message(flow, "Bob", contact=self.contact)  # will complete the flow

        check_node_count_rebuild(flow, 2)
