
from datetime import datetime

import pytz
from mock import patch

from django.test.utils import override_settings

from temba.channels.models import Channel
from temba.contacts.models import Contact
from temba.flows.models import Flow, FlowRun
from temba.msgs.models import Label, Msg
from temba.tests import MockResponse, TembaTest, skip_if_no_flowserver
from temba.values.constants import Value

from . import trial
from .client import FlowServerException, get_client, serialize_channel, serialize_field, serialize_label


class SerializationTest(TembaTest):

    def test_serialize_field(self):
        gender = self.create_field("gender", "Gender", Value.TYPE_TEXT)
        age = self.create_field("age", "Age", Value.TYPE_NUMBER)

        self.assertEqual(serialize_field(gender), {"key": "gender", "name": "Gender", "value_type": "text"})
        self.assertEqual(serialize_field(age), {"key": "age", "name": "Age", "value_type": "number"})

    def test_serialize_label(self):
        spam = Label.get_or_create(self.org, self.admin, "Spam")
        self.assertEqual(serialize_label(spam), {"uuid": str(spam.uuid), "name": "Spam"})

    def test_serialize_channel(self):
        self.assertEqual(
            serialize_channel(self.channel),
            {
                "uuid": str(self.channel.uuid),
                "name": "Test Channel",
                "address": "+250785551212",
                "roles": ["send", "receive"],
                "schemes": ["tel"],
            },
        )


class ClientTest(TembaTest):

    def setUp(self):
        super().setUp()

        self.gender = self.create_field("gender", "Gender", Value.TYPE_TEXT)
        self.age = self.create_field("age", "Age", Value.TYPE_NUMBER)
        self.contact = self.create_contact("Bob", number="+12345670987", urn="twitterid:123456785#bobby")
        self.testers = self.create_group("Testers", [self.contact])
        self.client = get_client()

    def test_add_contact_changed(self):
        twitter = Channel.create(
            self.org, self.admin, None, "TT", "Twitter", "nyaruka", schemes=["twitter", "twitterid"]
        )
        self.contact.set_preferred_channel(twitter)
        self.contact.urns.filter(scheme="twitterid").update(channel=twitter)
        self.contact.clear_urn_cache()

        with patch("django.utils.timezone.now", return_value=datetime(2018, 1, 18, 14, 24, 30, 0, tzinfo=pytz.UTC)):
            self.contact.set_field(self.admin, "gender", "M")
            self.contact.set_field(self.admin, "age", 36)

            self.assertEqual(
                self.client.request_builder(self.org, 1234).add_contact_changed(self.contact).request["events"],
                [
                    {
                        "type": "contact_changed",
                        "created_on": "2018-01-18T14:24:30+00:00",
                        "contact": {
                            "uuid": str(self.contact.uuid),
                            "id": self.contact.id,
                            "name": "Bob",
                            "language": None,
                            "timezone": "UTC",
                            "urns": [
                                "twitterid:123456785?channel=%s#bobby" % str(twitter.uuid),
                                "tel:+12345670987?channel=%s" % str(self.channel.uuid),
                            ],
                            "fields": {"gender": {"text": "M"}, "age": {"text": "36", "number": "36"}},
                            "groups": [{"uuid": str(self.testers.uuid), "name": "Testers"}],
                        },
                    }
                ],
            )

    def test_add_environment_changed(self):
        with patch("django.utils.timezone.now", return_value=datetime(2018, 1, 18, 14, 24, 30, 0, tzinfo=pytz.UTC)):
            self.assertEqual(
                self.client.request_builder(self.org, 1234).add_environment_changed().request["events"],
                [
                    {
                        "type": "environment_changed",
                        "created_on": "2018-01-18T14:24:30+00:00",
                        "environment": {
                            "date_format": "DD-MM-YYYY",
                            "languages": [],
                            "time_format": "tt:mm",
                            "timezone": "Africa/Kigali",
                        },
                    }
                ],
            )

    def test_add_run_expired(self):
        flow = self.get_flow("color")
        run, = flow.start([], [self.contact])
        run.set_interrupted()

        with patch("django.utils.timezone.now", return_value=datetime(2018, 1, 18, 14, 24, 30, 0, tzinfo=pytz.UTC)):

            self.assertEqual(
                self.client.request_builder(self.org, 1234).add_run_expired(run).request["events"],
                [{"type": "run_expired", "created_on": run.exited_on.isoformat(), "run_uuid": str(run.uuid)}],
            )

    @patch("requests.post")
    def test_request_failure(self, mock_post):
        mock_post.return_value = MockResponse(400, '{"errors":["Bad request", "Doh!"]}')

        flow = self.get_flow("color")
        contact = self.create_contact("Joe", number="+29638356667")

        with self.assertRaises(FlowServerException) as e:
            self.client.request_builder(self.org, 1234).start_manual(contact, flow)

        self.assertEqual(str(e.exception), "Invalid request: Bad request\nDoh!")


class TrialTest(TembaTest):

    def setUp(self):
        super().setUp()

        self.contact = self.create_contact("Ben Haggerty", number="+12065552020")

    def test_is_flow_suitable(self):
        self.assertTrue(trial.is_flow_suitable(self.get_flow("favorites")))
        self.assertTrue(trial.is_flow_suitable(self.get_flow("action_packed")))

        self.assertFalse(trial.is_flow_suitable(self.get_flow("airtime")))  # airtime rulesets
        self.assertFalse(trial.is_flow_suitable(self.get_flow("call_me_maybe")))  # IVR
        self.assertFalse(trial.is_flow_suitable(self.get_flow("resthooks")))  # resthooks

    @skip_if_no_flowserver
    @override_settings(FLOW_SERVER_TRIAL="on")
    @patch("temba.flows.server.trial.report_failure")
    @patch("temba.flows.server.trial.report_success")
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
    @override_settings(FLOW_SERVER_TRIAL="always")
    @patch("temba.flows.server.trial.report_failure")
    @patch("temba.flows.server.trial.report_success")
    def test_resume_with_message(self, mock_report_success, mock_report_failure):
        favorites = self.get_flow("favorites")

        run, = favorites.start([], [self.contact])

        # check the reconstructed session for this run
        session = trial.reconstruct_session(run)
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
        self.assertEqual(set(trial.compare_run(run, {"runs": []}).keys()), {"session"})

        # simulate differences in the path, results and events
        session = trial.reconstruct_session(run)
        session["runs"][0]["path"][0]["node_uuid"] = "wrong node"
        session["runs"][0]["results"]["color"]["value"] = "wrong value"
        session["runs"][0]["events"][0]["msg"]["text"] = "wrong text"

        self.assertTrue(trial.compare_run(run, session)["diffs"])

    @skip_if_no_flowserver
    @override_settings(FLOW_SERVER_TRIAL="always")
    @patch("temba.flows.server.trial.report_failure")
    @patch("temba.flows.server.trial.report_success")
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
        session = trial.reconstruct_session(child_run)
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
    @patch("temba.flows.server.trial.report_failure")
    @patch("temba.flows.server.trial.report_success")
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
    @patch("temba.flows.server.trial.report_failure")
    @patch("temba.flows.server.trial.report_success")
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
        session = trial.reconstruct_session(child_run)
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
    @patch("temba.flows.server.trial.report_failure")
    def test_trial_fault_tolerance(self, mock_report_failure):
        favorites = self.get_flow("favorites")

        # an exception in maybe_start_resume shouldn't prevent normal flow execution
        with patch("temba.flows.server.trial.reconstruct_session") as mock_reconstruct_session:
            mock_reconstruct_session.side_effect = ValueError("BOOM")

            run, = favorites.start([], [self.contact])
            Msg.create_incoming(self.channel, "tel:+12065552020", "I like red")
            run.refresh_from_db()
            self.assertEqual(len(run.path), 4)

        # an exception in end_resume also shouldn't prevent normal flow execution
        with patch("temba.flows.server.trial.resume") as mock_resume:
            mock_resume.side_effect = ValueError("BOOM")

            run, = favorites.start([], [self.contact], restart_participants=True)
            Msg.create_incoming(self.channel, "tel:+12065552020", "I like red")
            run.refresh_from_db()
            self.assertEqual(len(run.path), 4)

        # detected differences should be reported but shouldn't effect normal flow execution
        with patch("temba.flows.server.trial.compare_run") as mock_compare_run:
            mock_compare_run.return_value = {"diffs": ["a", "b"]}

            run, = favorites.start([], [self.contact], restart_participants=True)
            Msg.create_incoming(self.channel, "tel:+12065552020", "I like red")
            run.refresh_from_db()
            self.assertEqual(len(run.path), 4)

            self.assertEqual(mock_report_failure.call_count, 1)

    @skip_if_no_flowserver
    @override_settings(FLOW_SERVER_TRIAL="always", SEND_WEBHOOKS=True)
    @patch("temba.flows.server.trial.report_failure")
    @patch("temba.flows.server.trial.report_success")
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
