from django.urls import reverse

from temba.flows.models import FlowStart, FlowStartCount
from temba.tests import CRUDLTestMixin, TembaTest


class FlowStartCRUDLTest(TembaTest, CRUDLTestMixin):
    def test_list(self):
        list_url = reverse("flows.flowstart_list")

        flow1 = self.create_flow("Test Flow 1")
        flow2 = self.create_flow("Test 2")

        contact = self.create_contact("Bob", phone="+1234567890")
        group = self.create_group("Testers", contacts=[contact])
        start1 = FlowStart.create(flow1, self.admin, contacts=[contact])
        start2 = FlowStart.create(
            flow1, self.admin, query="name ~ Bob", start_type="A", exclusions={"started_previously": True}
        )
        start3 = FlowStart.create(flow2, self.admin, groups=[group], start_type="Z", exclusions={"in_a_flow": True})

        flow2.release(self.admin)

        FlowStartCount.objects.create(start=start3, count=1000)
        FlowStartCount.objects.create(start=start3, count=234)

        other_org_flow = self.create_flow("Test", org=self.org2)
        FlowStart.create(other_org_flow, self.admin2)

        self.assertRequestDisallowed(list_url, [None, self.agent])
        response = self.assertListFetch(
            list_url, [self.user, self.editor, self.admin], context_objects=[start3, start2, start1]
        )

        self.assertContains(response, "Test Flow 1")
        self.assertNotContains(response, "Test Flow 2")
        self.assertContains(response, "A deleted flow")
        self.assertContains(response, "was started by admin@textit.com")
        self.assertContains(response, "was started by an API call")
        self.assertContains(response, "was started by Zapier")
        self.assertContains(response, "Not in a flow")

        response = self.assertListFetch(list_url + "?type=manual", [self.admin], context_objects=[start1])
        self.assertTrue(response.context["filtered"])
        self.assertEqual(response.context["url_params"], "?type=manual&")

    def test_status(self):
        flow = self.create_flow("Test Flow 1")
        contact = self.create_contact("Bob", phone="+1234567890")
        start = FlowStart.create(flow, self.admin, contacts=[contact])

        status_url = f"{reverse('flows.flowstart_status')}?id={start.id}&status=P"
        self.assertRequestDisallowed(status_url, [self.agent])
        response = self.assertReadFetch(status_url, [self.editor, self.admin])

        # status returns json
        self.assertEqual("Pending", response.json()["results"][0]["status"])

    def test_interrupt(self):
        flow = self.create_flow("Test Flow 1")
        contact = self.create_contact("Bob", phone="+1234567890")
        start = FlowStart.create(flow, self.admin, contacts=[contact])

        interrupt_url = reverse("flows.flowstart_interrupt", args=[start.id])
        self.assertRequestDisallowed(interrupt_url, [None, self.user, self.agent])
        self.requestView(interrupt_url, self.admin, post_data={})

        start.refresh_from_db()
        self.assertEqual(FlowStart.STATUS_INTERRUPTED, start.status)
