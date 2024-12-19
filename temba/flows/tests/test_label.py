from temba.flows.models import FlowLabel
from temba.tests import TembaTest


class FlowLabelTest(TembaTest):
    def test_model(self):
        label = FlowLabel.create(self.org, self.admin, "Cool Flows")
        self.assertEqual("Cool Flows", label.name)

        # can't create with invalid name
        with self.assertRaises(AssertionError):
            FlowLabel.create(self.org, self.admin, '"Cool"')

        # can't create with duplicate name
        with self.assertRaises(AssertionError):
            FlowLabel.create(self.org, self.admin, "Cool Flows")

        flow1 = self.create_flow("Flow 1")
        flow2 = self.create_flow("Flow 2")

        label.toggle_label([flow1, flow2], add=True)
        self.assertEqual({flow1, flow2}, set(label.get_flows()))

        label.toggle_label([flow1], add=False)
        self.assertEqual({flow2}, set(label.get_flows()))
