from django.urls import reverse
from django.utils import timezone

from temba.flows.models import FlowRun
from temba.tests import CRUDLTestMixin, TembaTest
from temba.utils.uuid import uuid4


class FlowRunCRUDLTest(TembaTest, CRUDLTestMixin):
    def test_delete(self):
        contact = self.create_contact("Ann", phone="+1234567890")
        flow = self.create_flow("Test")

        run1 = FlowRun.objects.create(
            uuid=uuid4(),
            org=self.org,
            flow=flow,
            contact=contact,
            status=FlowRun.STATUS_COMPLETED,
            created_on=timezone.now(),
            modified_on=timezone.now(),
            exited_on=timezone.now(),
        )
        run2 = FlowRun.objects.create(
            uuid=uuid4(),
            org=self.org,
            flow=flow,
            contact=contact,
            status=FlowRun.STATUS_COMPLETED,
            created_on=timezone.now(),
            modified_on=timezone.now(),
            exited_on=timezone.now(),
        )

        delete_url = reverse("flows.flowrun_delete", args=[run1.id])

        self.assertDeleteSubmit(delete_url, self.admin, object_deleted=run1, success_status=200)

        self.assertFalse(FlowRun.objects.filter(id=run1.id).exists())
        self.assertTrue(FlowRun.objects.filter(id=run2.id).exists())  # unchanged
