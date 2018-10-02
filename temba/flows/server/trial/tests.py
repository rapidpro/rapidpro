from unittest.mock import patch

from django.test.utils import override_settings

from temba.contacts.models import Contact
from temba.flows.models import Flow, FlowRun
from temba.msgs.models import Msg
from temba.tests import TembaTest, skip_if_no_flowserver

from . import resumes
from ..client import FlowServerException


class ResumeTest(TembaTest):
    def setUp(self):
        super().setUp()

        self.contact = self.create_contact("Ben Haggerty", number="+12065552020")

    def test_is_flow_suitable(self):
        self.assertTrue(resumes.is_flow_suitable(self.get_flow("favorites")))
        self.assertTrue(resumes.is_flow_suitable(self.get_flow("action_packed")))

        self.assertFalse(resumes.is_flow_suitable(self.get_flow("airtime")))  # airtime rulesets
        self.assertFalse(resumes.is_flow_suitable(self.get_flow("call_me_maybe")))  # IVR

    def test_is_flow_simple(self):
        self.assertTrue(resumes.is_flow_simple(self.get_flow("favorites")))
        self.assertFalse(resumes.is_flow_simple(self.get_flow("action_packed")))

    @skip_if_no_flowserver
    @override_settings(FLOW_SERVER_TRIAL="on")
    @patch("temba.flows.server.trial.resumes.report_failure")
    @patch("temba.flows.server.trial.resumes.report_success")
    def test_trial_throttling(self, mock_report_success, mock_report_failure):
        # first resume in a suitable flow will be trialled
        favorites = self.get_flow("favorites")
        favorites.start([], [self.contact], interrupt=True)
        Msg.create_incoming(self.channel, "tel:+12065552020", "red")

        self.assertEqual(mock_report_success.call_count, 1)
        self.assertEqual(mock_report_failure.call_count, 0)

        Msg.create_incoming(self.channel, "tel:+12065552020", "primus")

        # second won't because its too soon
        self.assertEqual(mock_report_success.call_count, 1)
        self.assertEqual(mock_report_failure.call_count, 0)

    @skip_if_no_flowserver
    @override_settings(FLOW_SERVER_TRIAL="on")
    @patch("temba.flows.server.trial.resumes.report_failure")
    @patch("temba.flows.server.trial.resumes.report_success")
    def test_finding_session_runs(self, mock_report_success, mock_report_failure):
        contact2 = self.create_contact("Oprah Winfrey", "+12065552121")
        self.get_flow("hierarchy")

        hierarchy = Flow.objects.get(name="Hierarchy 1")
        hierarchy.start([], [self.contact])

        contact1_run1, contact1_run2 = FlowRun.objects.filter(contact=self.contact).order_by("id")
        contact2_run1, contact2_run2, contact2_run3 = FlowRun.objects.filter(contact=contact2).order_by("id")

        self.assertEqual(contact1_run2.parent, contact1_run1)
        self.assertEqual(contact2_run1.parent, contact1_run2)
        self.assertEqual(contact2_run2.parent, contact2_run1)
        self.assertEqual(contact2_run3.parent, contact2_run2)

        session = resumes.reconstruct_session(contact2_run3)

        # check the session runs don't include the runs for the other contact
        self.assertEqual(
            [r["uuid"] for r in session["runs"]],
            [str(contact2_run1.uuid), str(contact2_run2.uuid), str(contact2_run3.uuid)],
        )

        # but the one that triggered the runs for the second contact, is included on the trigger
        self.assertEqual(session["trigger"]["type"], "flow_action")
        self.assertEqual(session["trigger"]["run"]["uuid"], str(contact1_run2.uuid))

        # and that the parent field is set correctly on each session run
        self.assertNotIn("parent_uuid", session["runs"][0])  # because it's parent isn't in same session
        self.assertEqual(session["runs"][1]["parent_uuid"], str(contact2_run1.uuid))
        self.assertEqual(session["runs"][2]["parent_uuid"], str(contact2_run2.uuid))

    @skip_if_no_flowserver
    @override_settings(FLOW_SERVER_TRIAL="always")
    @patch("temba.flows.server.trial.resumes.report_failure")
    @patch("temba.flows.server.trial.resumes.report_success")
    def test_resume_with_message(self, mock_report_success, mock_report_failure):
        favorites = self.get_flow("favorites")

        run, = favorites.start([], [self.contact])

        # check the reconstructed session for this run
        session = resumes.reconstruct_session(run)
        self.assertEqual(len(session["runs"]), 1)
        self.assertEqual(session["runs"][0]["flow"]["uuid"], str(favorites.uuid))
        self.assertEqual(session["contact"]["uuid"], str(self.contact.uuid))
        self.assertNotIn("results", session)
        self.assertNotIn("events", session)

        # and then resume by replying
        Msg.create_incoming(
            self.channel, "tel:+12065552020", "I like red", attachments=["image/jpeg:http://example.com/red.jpg"]
        )
        run.refresh_from_db()

        self.assertEqual(mock_report_success.call_count, 1)
        self.assertEqual(mock_report_failure.call_count, 0)

        # and then resume by replying again
        Msg.create_incoming(self.channel, "tel:+12065552020", "ooh Primus")
        run.refresh_from_db()

        self.assertEqual(mock_report_success.call_count, 2)
        self.assertEqual(mock_report_failure.call_count, 0)

        # simulate session not containing this run
        self.assertEqual(set(resumes.compare(run, {"runs": []}).keys()), {"session"})

        # simulate differences in the path, results and events
        session = resumes.reconstruct_session(run)
        session["runs"][0]["path"][0]["node_uuid"] = "wrong node"
        session["runs"][0]["results"]["color"]["value"] = "wrong value"
        session["runs"][0]["events"][0]["msg"]["text"] = "wrong text"

        self.assertTrue(resumes.compare(run, session)["diffs"])

    @skip_if_no_flowserver
    @override_settings(FLOW_SERVER_TRIAL="always")
    @patch("temba.flows.server.trial.resumes.report_failure")
    @patch("temba.flows.server.trial.resumes.report_success")
    def test_resume_with_message_in_subflow(self, mock_report_success, mock_report_failure):
        self.get_flow("subflow")
        parent_flow = Flow.objects.get(org=self.org, name="Parent Flow")
        child_flow = Flow.objects.get(org=self.org, name="Child Flow")

        # start the parent flow and then trigger the subflow by picking an option
        parent_flow.start([], [self.contact])
        Msg.create_incoming(self.channel, "tel:+12065552020", "color")

        self.assertEqual(mock_report_success.call_count, 1)
        self.assertEqual(mock_report_failure.call_count, 0)

        parent_run, child_run = list(FlowRun.objects.order_by("created_on"))

        # check the reconstructed session for this run
        session = resumes.reconstruct_session(child_run)
        self.assertEqual(len(session["runs"]), 2)
        self.assertEqual(session["runs"][0]["flow"]["uuid"], str(parent_flow.uuid))
        self.assertEqual(session["runs"][1]["flow"]["uuid"], str(child_flow.uuid))
        self.assertEqual(session["contact"]["uuid"], str(self.contact.uuid))
        self.assertEqual(session["trigger"]["type"], "manual")
        self.assertNotIn("results", session)
        self.assertNotIn("events", session)

        # and then resume by replying
        Msg.create_incoming(self.channel, "tel:+12065552020", "I like red")
        child_run.refresh_from_db()
        parent_run.refresh_from_db()

        # subflow run has completed
        self.assertIsNotNone(child_run.exited_on)
        self.assertIsNone(parent_run.exited_on)

        self.assertEqual(mock_report_success.call_count, 2)
        self.assertEqual(mock_report_failure.call_count, 0)

    @skip_if_no_flowserver
    @override_settings(FLOW_SERVER_TRIAL="always")
    @patch("temba.flows.server.trial.resumes.report_failure")
    @patch("temba.flows.server.trial.resumes.report_success")
    def test_resume_with_expiration_in_subflow(self, mock_report_success, mock_report_failure):
        self.get_flow("subflow")
        parent_flow = Flow.objects.get(org=self.org, name="Parent Flow")

        # start the parent flow and then trigger the subflow by picking an option
        parent_flow.start([], [self.contact])
        Msg.create_incoming(self.channel, "tel:+12065552020", "color")

        parent_run, child_run = list(FlowRun.objects.order_by("created_on"))

        # resume by expiring the child run
        child_run.expire()
        child_run.refresh_from_db()
        parent_run.refresh_from_db()

        # which should end both our runs
        self.assertIsNotNone(child_run.exited_on)
        self.assertIsNotNone(parent_run.exited_on)

        self.assertEqual(mock_report_success.call_count, 2)
        self.assertEqual(mock_report_failure.call_count, 0)

    @skip_if_no_flowserver
    @patch("temba.flows.server.trial.resumes.report_failure")
    @patch("temba.flows.server.trial.resumes.report_success")
    def test_resume_in_triggered_session(self, mock_report_success, mock_report_failure):
        parent_flow = self.get_flow("action_packed")
        child_flow = Flow.objects.get(org=self.org, name="Favorite Color")

        parent_flow.start([], [self.contact], restart_participants=True)

        Msg.create_incoming(self.channel, "tel:+12065552020", "Trey Anastasio")
        Msg.create_incoming(self.channel, "tel:+12065552020", "Male")

        parent_run, child_run = list(FlowRun.objects.order_by("created_on"))
        child_contact = Contact.objects.get(name="Oprah Winfrey")

        self.assertEqual(parent_run.flow, parent_flow)
        self.assertEqual(parent_run.contact, self.contact)
        self.assertEqual(child_run.flow, child_flow)
        self.assertEqual(child_run.contact, child_contact)

        # check that the run which triggered the child run isn't part of its session, but is part of the trigger
        session = resumes.reconstruct_session(child_run)
        self.assertEqual(len(session["runs"]), 1)
        self.assertEqual(session["runs"][0]["flow"]["uuid"], str(child_flow.uuid))
        self.assertEqual(session["contact"]["uuid"], str(child_contact.uuid))
        self.assertEqual(session["trigger"]["type"], "flow_action")
        self.assertNotIn("results", session)
        self.assertNotIn("events", session)

        with override_settings(FLOW_SERVER_TRIAL="always"):
            # resume child run with a message
            Msg.create_incoming(self.channel, "tel:+12065552121", "red")
            child_run.refresh_from_db()

        # and it should now be complete
        self.assertIsNotNone(child_run.exited_on)

        self.assertEqual(mock_report_success.call_count, 1)
        self.assertEqual(mock_report_failure.call_count, 0)

    @skip_if_no_flowserver
    @override_settings(FLOW_SERVER_TRIAL="always")
    @patch("temba.flows.server.trial.resumes.report_failure")
    def test_trial_fault_tolerance(self, mock_report_failure):
        favorites = self.get_flow("favorites")

        # an exception in maybe_start shouldn't prevent normal flow execution
        with patch("temba.flows.server.trial.resumes.reconstruct_session") as mock_reconstruct_session:
            mock_reconstruct_session.side_effect = ValueError("BOOM")

            run, = favorites.start([], [self.contact])
            Msg.create_incoming(self.channel, "tel:+12065552020", "I like red")
            run.refresh_from_db()
            self.assertEqual(len(run.path), 4)

        # a flow server exception in end also shouldn't prevent normal flow execution
        with patch("temba.flows.server.trial.resumes.resume") as mock_resume:
            mock_resume.side_effect = FlowServerException("resume", {}, {"errors": ["Boom!"]})

            run, = favorites.start([], [self.contact], restart_participants=True)
            Msg.create_incoming(self.channel, "tel:+12065552020", "I like red")
            run.refresh_from_db()
            self.assertEqual(len(run.path), 4)

        # any other exception in end_resume also shouldn't prevent normal flow execution
        with patch("temba.flows.server.trial.resumes.resume") as mock_resume:
            mock_resume.side_effect = ValueError("BOOM")

            run, = favorites.start([], [self.contact], restart_participants=True)
            Msg.create_incoming(self.channel, "tel:+12065552020", "I like red")
            run.refresh_from_db()
            self.assertEqual(len(run.path), 4)

        # detected differences should be reported but shouldn't effect normal flow execution
        with patch("temba.flows.server.trial.resumes.compare") as mock_compare:
            mock_compare.return_value = {"diffs": ["a", "b"]}

            run, = favorites.start([], [self.contact], restart_participants=True)
            Msg.create_incoming(self.channel, "tel:+12065552020", "I like red")
            run.refresh_from_db()
            self.assertEqual(len(run.path), 4)

            self.assertEqual(mock_report_failure.call_count, 1)

    @skip_if_no_flowserver
    @override_settings(FLOW_SERVER_TRIAL="always", SEND_WEBHOOKS=True)
    @patch("temba.flows.server.trial.resumes.report_failure")
    @patch("temba.flows.server.trial.resumes.report_success")
    def test_webhook_mocking(self, mock_report_success, mock_report_failure):
        flow = self.get_flow("dual_webhook")

        # mock the two webhook calls in this flow
        self.mockRequest("POST", "/code", '{"code": "ABABUUDDLRS"}', content_type="application/json")
        self.mockRequest("GET", "/success", "Success")

        flow.start([], [self.contact])
        Msg.create_incoming(self.channel, "tel:+12065552020", "Bob")

        self.assertAllRequestsMade()

        self.assertEqual(mock_report_success.call_count, 1)
        self.assertEqual(mock_report_failure.call_count, 0)

    @skip_if_no_flowserver
    @override_settings(FLOW_SERVER_TRIAL="always")
    @patch("temba.flows.server.trial.resumes.report_failure")
    @patch("temba.flows.server.trial.resumes.report_success")
    def test_msg_events_with_attachments(self, mock_report_success, mock_report_failure):
        # test an outgoing message with media
        flow = self.get_flow("color")
        flow_json = flow.as_json()
        flow_json["action_sets"][2]["actions"][0]["media"] = {"base": "image/jpg:files/blue.jpg"}
        flow.update(flow_json)

        flow.start([], [self.contact])
        Msg.create_incoming(self.channel, "tel:+12065552020", "blue")

        self.assertEqual(mock_report_success.call_count, 1)
        self.assertEqual(mock_report_failure.call_count, 0)

    @skip_if_no_flowserver
    @override_settings(FLOW_SERVER_TRIAL="always")
    @patch("temba.flows.server.trial.resumes.report_failure")
    @patch("temba.flows.server.trial.resumes.report_success")
    def test_no_trial_for_triggers(self, mock_report_success, mock_report_failure):
        self.get_flow("keywords")
        Msg.create_incoming(self.channel, "tel:+12065552020", "Start")

        mock_report_success.assert_not_called()
        mock_report_failure.assert_not_called()
