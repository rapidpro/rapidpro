from datetime import datetime, timedelta, timezone as tzone

from django.utils import timezone

from temba.flows.models import FlowRun, FlowSession
from temba.flows.tasks import interrupt_flow_sessions, trim_flow_sessions
from temba.tests import TembaTest, matchers, mock_mailroom
from temba.utils.uuid import uuid4


class FlowSessionTest(TembaTest):
    @mock_mailroom
    def test_interrupt(self, mr_mocks):
        contact = self.create_contact("Ben Haggerty", phone="+250788123123")

        def create_session(org, created_on: datetime):
            return FlowSession.objects.create(
                uuid=uuid4(),
                org=org,
                contact=contact,
                created_on=created_on,
                output_url="http://sessions.com/123.json",
                status=FlowSession.STATUS_WAITING,
                wait_started_on=timezone.now(),
                wait_expires_on=timezone.now() + timedelta(days=7),
                wait_resume_on_expire=False,
            )

        create_session(self.org, timezone.now() - timedelta(days=88))
        session2 = create_session(self.org, timezone.now() - timedelta(days=90))
        session3 = create_session(self.org, timezone.now() - timedelta(days=91))
        session4 = create_session(self.org2, timezone.now() - timedelta(days=92))

        interrupt_flow_sessions()

        self.assertEqual(
            [
                {
                    "type": "interrupt_sessions",
                    "org_id": self.org.id,
                    "queued_on": matchers.Datetime(),
                    "task": {"session_ids": [session2.id, session3.id]},
                },
                {
                    "type": "interrupt_sessions",
                    "org_id": self.org2.id,
                    "queued_on": matchers.Datetime(),
                    "task": {"session_ids": [session4.id]},
                },
            ],
            mr_mocks.queued_batch_tasks,
        )

    def test_trim(self):
        contact = self.create_contact("Ben Haggerty", phone="+250788123123")
        flow = self.create_flow("Test")

        # create some runs that have sessions
        session1 = FlowSession.objects.create(
            uuid=uuid4(),
            org=self.org,
            contact=contact,
            output_url="http://sessions.com/123.json",
            status=FlowSession.STATUS_WAITING,
            wait_started_on=timezone.now(),
            wait_expires_on=timezone.now() + timedelta(days=7),
            wait_resume_on_expire=False,
        )
        session2 = FlowSession.objects.create(
            uuid=uuid4(),
            org=self.org,
            contact=contact,
            output_url="http://sessions.com/234.json",
            status=FlowSession.STATUS_WAITING,
            wait_started_on=timezone.now(),
            wait_expires_on=timezone.now() + timedelta(days=7),
            wait_resume_on_expire=False,
        )
        session3 = FlowSession.objects.create(
            uuid=uuid4(),
            org=self.org,
            contact=contact,
            output_url="http://sessions.com/345.json",
            status=FlowSession.STATUS_WAITING,
            wait_started_on=timezone.now(),
            wait_expires_on=timezone.now() + timedelta(days=7),
            wait_resume_on_expire=False,
        )
        run1 = FlowRun.objects.create(
            org=self.org, flow=flow, contact=contact, session=session1, status=FlowRun.STATUS_WAITING
        )
        run2 = FlowRun.objects.create(
            org=self.org, flow=flow, contact=contact, session=session2, status=FlowRun.STATUS_WAITING
        )
        run3 = FlowRun.objects.create(
            org=self.org, flow=flow, contact=contact, session=session3, status=FlowRun.STATUS_WAITING
        )

        # create an IVR call with session
        call = self.create_incoming_call(flow, contact)
        run4 = call.session.runs.get()

        self.assertIsNotNone(run1.session)
        self.assertIsNotNone(run2.session)
        self.assertIsNotNone(run3.session)
        self.assertIsNotNone(run4.session)

        # end run1 and run4's sessions in the past
        run1.status = FlowRun.STATUS_COMPLETED
        run1.exited_on = datetime(2015, 9, 15, 0, 0, 0, 0, tzone.utc)
        run1.save(update_fields=("status", "exited_on"))
        run1.session.status = FlowSession.STATUS_COMPLETED
        run1.session.ended_on = datetime(2015, 9, 15, 0, 0, 0, 0, tzone.utc)
        run1.session.save(update_fields=("status", "ended_on"))

        run4.status = FlowRun.STATUS_INTERRUPTED
        run4.exited_on = datetime(2015, 9, 15, 0, 0, 0, 0, tzone.utc)
        run4.save(update_fields=("status", "exited_on"))
        run4.session.status = FlowSession.STATUS_INTERRUPTED
        run4.session.ended_on = datetime(2015, 9, 15, 0, 0, 0, 0, tzone.utc)
        run4.session.save(update_fields=("status", "ended_on"))

        # end run2's session now
        run2.status = FlowRun.STATUS_EXPIRED
        run2.exited_on = timezone.now()
        run2.save(update_fields=("status", "exited_on"))
        run4.session.status = FlowSession.STATUS_EXPIRED
        run2.session.ended_on = timezone.now()
        run2.session.save(update_fields=("status", "ended_on"))

        trim_flow_sessions()

        run1, run2, run3, run4 = FlowRun.objects.order_by("id")

        self.assertIsNone(run1.session)
        self.assertIsNotNone(run2.session)  # ended too recently to be deleted
        self.assertIsNotNone(run3.session)  # never ended
        self.assertIsNone(run4.session)

        # only sessions for run2 and run3 are left
        self.assertEqual(FlowSession.objects.count(), 2)
