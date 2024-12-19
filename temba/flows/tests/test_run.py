from datetime import datetime, timedelta, timezone as tzone
from unittest.mock import patch
from uuid import UUID

from django.utils import timezone

from temba.flows.models import FlowRun, FlowSession, FlowStart, FlowStartCount
from temba.tests import TembaTest, matchers
from temba.tests.engine import MockSessionWriter
from temba.utils.uuid import uuid4


class FlowRunTest(TembaTest):
    def setUp(self):
        super().setUp()

        self.contact = self.create_contact("Ben Haggerty", phone="+250788123123")

    def test_get_path(self):
        flow = self.create_flow("Test")
        session = FlowSession.objects.create(
            uuid=uuid4(),
            org=self.org,
            contact=self.contact,
            status=FlowSession.STATUS_COMPLETED,
            output_url="http://sessions.com/123.json",
            ended_on=timezone.now(),
            wait_resume_on_expire=False,
        )

        # create run with old style path JSON
        run = FlowRun.objects.create(
            uuid=uuid4(),
            org=self.org,
            session=session,
            flow=flow,
            contact=self.contact,
            status=FlowRun.STATUS_WAITING,
            path=[
                {
                    "uuid": "b5c3421c-3bbb-4dc7-9bda-683456588a6d",
                    "node_uuid": "857a1498-3d5f-40f5-8185-2ce596ce2677",
                    "arrived_on": "2021-12-20T08:47:30.123Z",
                    "exit_uuid": "6fc14d2c-3b4d-49c7-b342-4b2b2ebf7678",
                },
                {
                    "uuid": "4a254612-8437-47e1-b7bd-feb97ee60bf6",
                    "node_uuid": "59d992c6-c491-473d-a7e9-4f431d705c01",
                    "arrived_on": "2021-12-20T08:47:30.234Z",
                    "exit_uuid": None,
                },
            ],
            current_node_uuid="59d992c6-c491-473d-a7e9-4f431d705c01",
        )

        self.assertEqual(
            [
                FlowRun.Step(
                    node=UUID("857a1498-3d5f-40f5-8185-2ce596ce2677"),
                    time=datetime(2021, 12, 20, 8, 47, 30, 123000, tzinfo=tzone.utc),
                ),
                FlowRun.Step(
                    node=UUID("59d992c6-c491-473d-a7e9-4f431d705c01"),
                    time=datetime(2021, 12, 20, 8, 47, 30, 234000, tzinfo=tzone.utc),
                ),
            ],
            run.get_path(),
        )

        # create run with new style path fields
        run = FlowRun.objects.create(
            uuid=uuid4(),
            org=self.org,
            session=session,
            flow=flow,
            contact=self.contact,
            status=FlowRun.STATUS_WAITING,
            path_nodes=[UUID("857a1498-3d5f-40f5-8185-2ce596ce2677"), UUID("59d992c6-c491-473d-a7e9-4f431d705c01")],
            path_times=[
                datetime(2021, 12, 20, 8, 47, 30, 123000, tzinfo=tzone.utc),
                datetime(2021, 12, 20, 8, 47, 30, 234000, tzinfo=tzone.utc),
            ],
            current_node_uuid="59d992c6-c491-473d-a7e9-4f431d705c01",
        )

        self.assertEqual(
            [
                FlowRun.Step(
                    node=UUID("857a1498-3d5f-40f5-8185-2ce596ce2677"),
                    time=datetime(2021, 12, 20, 8, 47, 30, 123000, tzinfo=tzone.utc),
                ),
                FlowRun.Step(
                    node=UUID("59d992c6-c491-473d-a7e9-4f431d705c01"),
                    time=datetime(2021, 12, 20, 8, 47, 30, 234000, tzinfo=tzone.utc),
                ),
            ],
            run.get_path(),
        )

    def test_as_archive_json(self):
        flow = self.get_flow("color_v13")
        flow_nodes = flow.get_definition()["nodes"]
        color_prompt = flow_nodes[0]
        color_split = flow_nodes[4]
        color_other = flow_nodes[3]

        msg_in = self.create_incoming_msg(self.contact, "green")

        run = (
            MockSessionWriter(self.contact, flow)
            .visit(color_prompt)
            .send_msg("What is your favorite color?", self.channel)
            .visit(color_split)
            .wait()
            .resume(msg=msg_in)
            .set_result("Color", "green", "Other", "green")
            .visit(color_other)
            .send_msg("That is a funny color. Try again.", self.channel)
            .visit(color_split)
            .wait()
            .save()
        ).session.runs.get()

        run_json = run.as_archive_json()

        self.assertEqual(
            set(run_json.keys()),
            set(
                [
                    "id",
                    "uuid",
                    "flow",
                    "contact",
                    "responded",
                    "path",
                    "values",
                    "created_on",
                    "modified_on",
                    "exited_on",
                    "exit_type",
                ]
            ),
        )

        self.assertEqual(run.id, run_json["id"])
        self.assertEqual({"uuid": str(flow.uuid), "name": "Colors"}, run_json["flow"])
        self.assertEqual({"uuid": str(self.contact.uuid), "name": "Ben Haggerty"}, run_json["contact"])
        self.assertTrue(run_json["responded"])

        self.assertEqual(
            [
                {"node": matchers.UUID4String(), "time": matchers.ISODate()},
                {"node": matchers.UUID4String(), "time": matchers.ISODate()},
                {"node": matchers.UUID4String(), "time": matchers.ISODate()},
                {"node": matchers.UUID4String(), "time": matchers.ISODate()},
            ],
            run_json["path"],
        )

        self.assertEqual(
            {
                "color": {
                    "category": "Other",
                    "name": "Color",
                    "node": matchers.UUID4String(),
                    "time": matchers.ISODate(),
                    "value": "green",
                    "input": "green",
                }
            },
            run_json["values"],
        )

        self.assertEqual(run.created_on.isoformat(), run_json["created_on"])
        self.assertEqual(run.modified_on.isoformat(), run_json["modified_on"])
        self.assertIsNone(run_json["exit_type"])
        self.assertIsNone(run_json["exited_on"])

    def _check_deletion(self, by_archiver: bool, expected: dict, session_completed=True):
        """
        Runs our favorites flow, then deletes the run and asserts our final state
        """

        flow = self.get_flow("favorites_v13")
        flow_nodes = flow.get_definition()["nodes"]
        color_prompt = flow_nodes[0]
        color_split = flow_nodes[2]
        beer_prompt = flow_nodes[3]
        beer_split = flow_nodes[5]
        name_prompt = flow_nodes[6]
        name_split = flow_nodes[7]
        end_prompt = flow_nodes[8]

        start = FlowStart.create(flow, self.admin, contacts=[self.contact])
        if session_completed:
            (
                MockSessionWriter(self.contact, flow, start)
                .visit(color_prompt)
                .send_msg("What is your favorite color?", self.channel)
                .visit(color_split)
                .wait()
                .resume(msg=self.create_incoming_msg(self.contact, "blue"))
                .set_result("Color", "blue", "Blue", "blue")
                .visit(beer_prompt, exit_index=2)
                .send_msg("Good choice, I like Blue too! What is your favorite beer?")
                .visit(beer_split)
                .wait()
                .resume(msg=self.create_incoming_msg(self.contact, "primus"))
                .set_result("Beer", "primus", "Primus", "primus")
                .visit(name_prompt, exit_index=2)
                .send_msg("Mmmmm... delicious Turbo King. Lastly, what is your name?")
                .visit(name_split)
                .wait()
                .resume(msg=self.create_incoming_msg(self.contact, "Ryan Lewis"))
                .visit(end_prompt)
                .complete()
                .save()
            )
        else:
            (
                MockSessionWriter(self.contact, flow, start)
                .visit(color_prompt)
                .send_msg("What is your favorite color?", self.channel)
                .visit(color_split)
                .wait()
                .resume(msg=self.create_incoming_msg(self.contact, "blue"))
                .set_result("Color", "blue", "Blue", "blue")
                .visit(beer_prompt, exit_index=2)
                .send_msg("Good choice, I like Blue too! What is your favorite beer?")
                .visit(beer_split)
                .wait()
                .resume(msg=self.create_incoming_msg(self.contact, "primus"))
                .set_result("Beer", "primus", "Primus", "primus")
                .visit(name_prompt, exit_index=2)
                .send_msg("Mmmmm... delicious Turbo King. Lastly, what is your name?")
                .visit(name_split)
                .wait()
                .save()
            )

        run = FlowRun.objects.get(contact=self.contact)
        if by_archiver:
            super(FlowRun, run).delete()  # delete_from_counts left unset
        else:
            run.delete()  # delete_from_counts updated to true

        cat_counts = {c["key"]: c for c in flow.get_category_counts()}

        self.assertEqual(2, len(cat_counts))
        self.assertEqual(expected["red_count"], cat_counts["color"]["categories"][0]["count"])
        self.assertEqual(expected["primus_count"], cat_counts["color"]["categories"][0]["count"])

        self.assertEqual(expected["start_count"], FlowStartCount.get_count(start))
        self.assertEqual(expected["run_count"], flow.get_run_stats())

        self.assertFalse(FlowRun.objects.filter(id=run.id).exists())

    @patch("temba.mailroom.queue_interrupt")
    def test_delete_by_user_with_complete_session(self, mock_queue_interrupt):
        self._check_deletion(
            by_archiver=False,
            expected={
                "red_count": 0,
                "primus_count": 0,
                "start_count": 1,  # unchanged
                "run_count": {
                    "total": 0,
                    "status": {
                        "active": 0,
                        "waiting": 0,
                        "completed": 0,
                        "expired": 0,
                        "interrupted": 0,
                        "failed": 0,
                    },
                    "completion": 0,
                },
            },
        )
        self.assertFalse(mock_queue_interrupt.called)

    @patch("temba.mailroom.queue_interrupt")
    def test_delete_by_user_without_complete_session(self, mock_queue_interrupt):
        self._check_deletion(
            by_archiver=False,
            expected={
                "red_count": 0,
                "primus_count": 0,
                "start_count": 1,  # unchanged
                "run_count": {
                    "total": 0,
                    "status": {
                        "active": 0,
                        "waiting": 0,
                        "completed": 0,
                        "expired": 0,
                        "interrupted": 0,
                        "failed": 0,
                    },
                    "completion": 0,
                },
            },
            session_completed=False,
        )
        mock_queue_interrupt.assert_called_once()

    @patch("temba.mailroom.queue_interrupt")
    def test_delete_by_archiver(self, mock_queue_interrupt):
        self._check_deletion(
            by_archiver=True,
            expected={
                "red_count": 1,
                "primus_count": 1,
                "start_count": 1,  # unchanged
                "run_count": {  # unchanged
                    "total": 1,
                    "status": {
                        "active": 0,
                        "waiting": 0,
                        "completed": 1,
                        "expired": 0,
                        "interrupted": 0,
                        "failed": 0,
                    },
                    "completion": 100,
                },
            },
        )
        self.assertFalse(mock_queue_interrupt.called)

    def test_big_ids(self):
        # create a session and run with big ids
        session = FlowSession.objects.create(
            id=3_000_000_000,
            uuid=uuid4(),
            org=self.org,
            contact=self.contact,
            status=FlowSession.STATUS_WAITING,
            output_url="http://sessions.com/123.json",
            created_on=timezone.now(),
            wait_started_on=timezone.now(),
            wait_expires_on=timezone.now() + timedelta(days=7),
            wait_resume_on_expire=False,
        )
        FlowRun.objects.create(
            id=4_000_000_000,
            uuid=uuid4(),
            org=self.org,
            session=session,
            flow=self.create_flow("Test"),
            contact=self.contact,
            status=FlowRun.STATUS_WAITING,
            created_on=timezone.now(),
            modified_on=timezone.now(),
            path=[
                {
                    "uuid": "b5c3421c-3bbb-4dc7-9bda-683456588a6d",
                    "node_uuid": "857a1498-3d5f-40f5-8185-2ce596ce2677",
                    "arrived_on": "2021-12-20T08:47:30.123Z",
                    "exit_uuid": "6fc14d2c-3b4d-49c7-b342-4b2b2ebf7678",
                },
                {
                    "uuid": "4a254612-8437-47e1-b7bd-feb97ee60bf6",
                    "node_uuid": "59d992c6-c491-473d-a7e9-4f431d705c01",
                    "arrived_on": "2021-12-20T08:47:30.234Z",
                    "exit_uuid": None,
                },
            ],
            current_node_uuid="59d992c6-c491-473d-a7e9-4f431d705c01",
        )
