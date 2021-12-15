from temba.tests import TembaTest
from temba.flows.models import FlowRun
from temba.flows.search.parser import FlowRunSearch
from temba.tests.engine import MockSessionWriter


class FlowRunSearchTest(TembaTest):
    def setUp(self):
        super().setUp()

        self.contact = self.create_contact("Eric", phone="+250788382382")

    def test_flow_search(self):
        flow = self.get_flow("color_v13")
        flow_nodes = flow.get_definition()["nodes"]
        color_prompt = flow_nodes[0]
        color_split = flow_nodes[4]

        msg = self.create_incoming_msg(self.contact, "blue")
        (
            MockSessionWriter(self.contact, flow)
            .visit(color_prompt)
            .send_msg("What is your favorite color?", self.channel)
            .visit(color_split)
            .wait()
            .resume(msg=msg)
            .set_result("Color", "blue", "Blue", "blue")
            .complete()
            .save()
        ).session.runs.get()

        queryset = FlowRun.objects.all()

        run_search = FlowRunSearch(query="Color=blue", base_queryset=queryset)
        queryset, e = run_search.search()

        self.assertEqual(len(queryset), 1)
