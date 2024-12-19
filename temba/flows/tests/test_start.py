from temba import mailroom
from temba.flows.models import FlowStart
from temba.tests import TembaTest, mock_mailroom


class FlowStartTest(TembaTest):
    def test_model(self):
        flow = self.create_flow("Test Flow")
        contact = self.create_contact("Bob", phone="+1234567890")
        start = FlowStart.create(flow, self.admin, contacts=[contact])

        self.assertEqual(f'<FlowStart: id={start.id} flow="{start.flow.uuid}">', repr(start))
        self.assertTrue(FlowStart.has_unfinished(self.org))

        start.interrupt(self.editor)

        start.refresh_from_db()
        self.assertEqual(FlowStart.STATUS_INTERRUPTED, start.status)
        self.assertEqual(self.editor, start.modified_by)
        self.assertIsNotNone(start.modified_on)
        self.assertFalse(FlowStart.has_unfinished(self.org))

    @mock_mailroom
    def test_preview(self, mr_mocks):
        flow = self.create_flow("Test")
        contact1 = self.create_contact("Ann", phone="+1234567111")
        contact2 = self.create_contact("Bob", phone="+1234567222")
        doctors = self.create_group("Doctors", contacts=[contact1, contact2])

        mr_mocks.flow_start_preview(query='group = "Doctors" AND status = "active"', total=100)

        query, total = FlowStart.preview(
            flow,
            include=mailroom.Inclusions(group_uuids=[str(doctors.uuid)]),
            exclude=mailroom.Exclusions(non_active=True),
        )

        self.assertEqual('group = "Doctors" AND status = "active"', query)
        self.assertEqual(100, total)
