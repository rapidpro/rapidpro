from datetime import datetime

from django.utils import timezone

from temba.channels.models import Channel
from temba.flows.models import FlowRun
from temba.msgs.models import INCOMING, OUTGOING, Msg
from temba.tests import TembaTest
from temba.triggers.models import Trigger

from .models import USSDSession


class USSDSessionTest(TembaTest):
    def setUp(self):
        super().setUp()

        self.channel.delete()
        self.channel = Channel.create(
            self.org,
            self.user,
            "RW",
            "JNU",
            None,
            "+250788123123",
            role=Channel.ROLE_USSD + Channel.DEFAULT_ROLE,
            uuid="00000000-0000-0000-0000-000000001234",
        )

    def test_pull_async_trigger_start(self):
        flow = self.get_flow("ussd_example")

        starcode = "*113#"

        trigger, _ = Trigger.objects.get_or_create(
            channel=self.channel,
            keyword=starcode,
            flow=flow,
            created_by=self.user,
            modified_by=self.user,
            org=self.org,
            trigger_type=Trigger.TYPE_USSD_PULL,
        )

        # handle a message that has an unmatched wrong keyword
        session = USSDSession.handle_incoming(
            channel=self.channel,
            urn="+329732973",
            content="None",
            status=USSDSession.TRIGGERED,
            date=timezone.now(),
            external_id="1234",
            message_id="1111211",
            starcode="wrongkeyword",
        )

        # check if session was started and created
        self.assertFalse(session)

        # handle a message that has an unmatched starcode
        session = USSDSession.handle_incoming(
            channel=self.channel,
            urn="+329732973",
            content="None",
            status=USSDSession.TRIGGERED,
            date=timezone.now(),
            external_id="1234",
            message_id="1111211",
            starcode="*111#",
        )

        # check if session was started and created
        self.assertFalse(session)

        session = USSDSession.handle_incoming(
            channel=self.channel,
            urn="+329732973",
            content="None",
            status=USSDSession.TRIGGERED,
            date=timezone.now(),
            external_id="1235",
            message_id="1111131",
            starcode=starcode,
        )

        # check session properties
        self.assertEqual(session.status, USSDSession.TRIGGERED)
        self.assertIsInstance(session.started_on, datetime)
        self.assertEqual(session.external_id, "1235")

    def test_push_async_start(self):
        flow = self.get_flow("ussd_example")

        contact = self.create_contact("Joe", "+250788383383")

        flow.start([], [contact])

        session = USSDSession.objects.get()

        # flow start created a session
        self.assertTrue(session)

        # session's status has to be INITIATED and direction is outgoing (aka USSD_PUSH)
        self.assertEqual(session.direction, USSDSession.USSD_PUSH)
        self.assertEqual(session.status, USSDSession.INITIATED)
        self.assertIsInstance(session.started_on, datetime)

        # message created and sent out
        self.assertIsNotNone(Msg.objects.filter(direction="O").first())

        return flow

    def test_async_content_handling(self):
        # start off a PUSH session
        self.test_push_async_start()

        # send an incoming message through the channel
        session = USSDSession.handle_incoming(
            channel=self.channel,
            urn="+250788383383",
            content="1",
            date=timezone.now(),
            external_id="21345",
            message_id="123",
        )

        # same session is modified
        self.assertEqual(USSDSession.objects.count(), 1)

        # new status
        self.assertEqual(session.status, USSDSession.IN_PROGRESS)

        # there should be 3 messages
        run = FlowRun.objects.get()
        msg1, msg2, msg3 = run.get_messages().order_by("id")

        # check the incoming and outgoing messages
        self.assertEqual(msg1.direction, OUTGOING)
        self.assertEqual(msg1.text, "What would you like to read about?")

        self.assertEqual(msg2.direction, INCOMING)
        self.assertEqual(msg2.text, "1")

        self.assertEqual(msg3.direction, OUTGOING)
        self.assertEqual(msg3.text, "Thank you!")

    def test_expiration(self):
        # start off a PUSH session
        self.test_push_async_start()
        run = FlowRun.objects.last()
        run.expire()

        # we should be marked as interrupted now
        self.assertEqual(USSDSession.INTERRUPTED, run.connection.status)

    def test_async_interrupt_handling(self):
        # start a flow
        flow = self.get_flow("ussd_example")
        contact = self.create_contact("Joe", "+250788383383")
        flow.start([], [contact])

        session = USSDSession.objects.get()
        # check if session was created
        self.assertEqual(session.direction, USSDSession.USSD_PUSH)
        self.assertEqual(session.status, USSDSession.INITIATED)
        self.assertIsInstance(session.started_on, datetime)
        self.assertIsNotNone(FlowRun.objects.filter(connection=session).first().expires_on)

        # send an interrupt "signal"
        session = USSDSession.handle_incoming(
            channel=self.channel,
            urn="+250788383383",
            status=USSDSession.INTERRUPTED,
            date=timezone.now(),
            external_id="21345",
        )

        # same session is modified
        self.assertEqual(USSDSession.objects.count(), 1)

        # session is modified with proper status and end date
        self.assertEqual(session.status, USSDSession.INTERRUPTED)
        self.assertIsInstance(session.ended_on, datetime)
        self.assertEqual(session.external_id, "21345")

    def test_end_with_menu_no_destination(self):
        flow = self.get_flow("ussd_session_end")
        contact = self.create_contact("Joe", "+250788383383")
        flow.start([], [contact])

        # choose option with no destination connected
        session = USSDSession.handle_incoming(
            channel=self.channel, urn="+250788383383", content="4", date=timezone.now(), external_id="21345"
        )

        # there should be 2 outgoing messages, one with an empty message to close down the session gracefully
        self.assertEqual(Msg.objects.count(), 3)
        self.assertEqual(Msg.objects.filter(direction="O").count(), 2)

        msgs = Msg.objects.filter(direction="O").order_by("id")

        self.assertNotEqual(msgs[0].text, "")
        self.assertEqual(msgs[1].text, "")

        # the session should be marked as "ENDING"
        session.refresh_from_db()
        self.assertEqual(session.status, USSDSession.ENDING)
        self.assertIsNone(session.ended_on)
        self.assertEqual(session.external_id, "21345")

    def test_end_with_menu_destination_with_messaging(self):
        flow = self.get_flow("ussd_session_end")
        contact = self.create_contact("Joe", "+250788383383")
        flow.start([], [contact])

        # choose option with destination connected to another wait_menu ruleset
        session = USSDSession.handle_incoming(
            channel=self.channel, urn="+250788383383", content="1", date=timezone.now(), external_id="21345"
        )

        # the session shouldn't be marked as ending
        self.assertEqual(session.status, USSDSession.IN_PROGRESS)
        self.assertIsNone(session.ended_on)
        self.assertEqual(session.external_id, "21345")

    def test_end_with_menu_destination_without_messaging(self):
        flow = self.get_flow("ussd_session_end")
        contact = self.create_contact("Joe", "+250788383383")
        flow.start([], [contact])

        # choose option with destination connected to another wait_menu ruleset
        session = USSDSession.handle_incoming(
            channel=self.channel, urn="+250788383383", content="test", date=timezone.now(), external_id="21345"
        )

        # the session shouldn't be marked as ending
        self.assertEqual(session.status, USSDSession.IN_PROGRESS)
        self.assertIsNone(session.ended_on)
        self.assertEqual(session.external_id, "21345")

        # choose option with destination connected to actions (add to groups and set language later)
        session = USSDSession.handle_incoming(
            channel=self.channel, urn="+250788383383", content="2", date=timezone.now(), external_id="21345"
        )

        # there should be a last message with no content to close the session
        msgs = Msg.objects.filter(direction="O").order_by("id")

        self.assertEqual(msgs.last().text, "")

        # the session should be marked as ending at this point, cos' there's no more messaging destination in the flow
        session.refresh_from_db()
        self.assertEqual(session.status, USSDSession.ENDING)
        self.assertIsNone(session.ended_on)
        self.assertEqual(session.external_id, "21345")

    def test_end_with_arbitrary_rules(self):
        flow = self.get_flow("ussd_session_end")
        contact = self.create_contact("Joe", "+250788383383")
        flow.start([], [contact])

        # choose option with destination connected to the arbitarty ussd ruleset
        session = USSDSession.handle_incoming(
            channel=self.channel, urn="+250788383383", content="3", date=timezone.now(), external_id="21345"
        )

        # the session shouldn't be marked as ending
        self.assertEqual(session.status, USSDSession.IN_PROGRESS)
        self.assertIsNone(session.ended_on)
        self.assertEqual(session.external_id, "21345")

        # now we are at the ussd response, choose an option here
        session = USSDSession.handle_incoming(
            channel=self.channel, urn="+250788383383", content="3", date=timezone.now(), external_id="21345"
        )

        session.refresh_from_db()
        self.assertEqual(session.status, USSDSession.ENDING)
        self.assertIsNone(session.ended_on)
        self.assertEqual(session.external_id, "21345")

    def test_end_with_end_action(self):
        flow = self.get_flow("ussd_session_end")
        contact = self.create_contact("Joe", "+250788383383")
        flow.start([], [contact])

        # choose option with destination connected to the arbitarty ussd ruleset
        session = USSDSession.handle_incoming(
            channel=self.channel, urn="+250788383383", content="3", date=timezone.now(), external_id="21345"
        )

        # the session shouldn't be marked as ending
        self.assertEqual(session.status, USSDSession.IN_PROGRESS)
        self.assertIsNone(session.ended_on)
        self.assertEqual(session.external_id, "21345")

        # choose the Maybe option which leads to a USSD End with message action
        session = USSDSession.handle_incoming(
            channel=self.channel, urn="+250788383383", content="Maybe", date=timezone.now(), external_id="21345"
        )

        # there should be a last message with the message of the end action
        msgs = Msg.objects.filter(direction="O").order_by("id")

        self.assertEqual(msgs.last().text, "perfect, thanks")

        # same session is modified
        self.assertEqual(USSDSession.objects.count(), 1)

        session.refresh_from_db()

        self.assertEqual(session.status, USSDSession.ENDING)
        self.assertIsNone(session.ended_on)
        self.assertEqual(session.external_id, "21345")

    def test_end_with_end_action_with_more_actions_in_actionset(self):
        flow = self.get_flow("ussd_session_end")
        contact = self.create_contact("Joe", "+250788383383")
        flow.start([], [contact])

        # choose option with destination connected to a set of actions including an end session w message action
        session = USSDSession.handle_incoming(
            channel=self.channel, urn="+250788383383", content="2", date=timezone.now(), external_id="21345"
        )

        # there should be a last message with the message of the end action
        msgs = Msg.objects.filter(direction="O").order_by("id")

        self.assertEqual(msgs.last().text, "Im sorry, we will contact you")

        session.refresh_from_db()

        # the session should be marked as ending
        self.assertEqual(session.status, USSDSession.ENDING)
        self.assertIsNone(session.ended_on)
        self.assertEqual(session.external_id, "21345")

    def test_ruleset_through_ruleset_no_destination(self):
        flow = self.get_flow("ussd_session_end")
        contact = self.create_contact("Joe", "+250788383383")
        flow.start([], [contact])

        # choose option with destination connected to another type of ruleset (Split by Expression)
        session = USSDSession.handle_incoming(
            channel=self.channel, urn="+250788383383", content="6", date=timezone.now(), external_id="21345"
        )

        # there should be a last message with empty msg just to close the session gracefully
        msgs = Msg.objects.filter(direction="O").order_by("id")

        self.assertEqual(msgs.last().text, "")

        session.refresh_from_db()
        # the session should be marked as ending since the next ruleset doesn't have any destination
        self.assertEqual(session.status, USSDSession.ENDING)
        self.assertIsNone(session.ended_on)
        self.assertEqual(session.external_id, "21345")

    def test_ruleset_through_ruleset_with_third_ruleset_destination(self):
        flow = self.get_flow("ussd_session_end")
        contact = self.create_contact("Joe", "+250788383383")
        flow.start([], [contact])

        # choose option with destination connected to another type of ruleset (Split by Expression)
        session = USSDSession.handle_incoming(
            channel=self.channel, urn="+250788383383", content="5", date=timezone.now(), external_id="21345"
        )

        # there should be a last message with the message of the end action
        msgs = Msg.objects.filter(direction="O").order_by("id")

        self.assertEqual(msgs.last().text, "a")

        session.refresh_from_db()

        self.assertEqual(session.status, USSDSession.ENDING)
        self.assertIsNone(session.ended_on)
        self.assertEqual(session.external_id, "21345")

    def test_ruleset_through_two_rulesets_ending_with_not_messaging_action(self):
        flow = self.get_flow("ussd_session_end")
        contact = self.create_contact("Joe", "+250788383383")
        flow.start([], [contact])

        # choose option with destination connected to another type of ruleset (Split by Expression)
        session = USSDSession.handle_incoming(
            channel=self.channel, urn="+250788383383", content="8", date=timezone.now(), external_id="21345"
        )

        # there should be a last message with empty message
        msgs = Msg.objects.filter(direction="O").order_by("id")

        self.assertEqual(msgs.last().text, "")

        session.refresh_from_db()

        self.assertEqual(session.status, USSDSession.ENDING)
        self.assertIsNone(session.ended_on)
        self.assertEqual(session.external_id, "21345")

    def test_ruleset_through_ruleset_ending_with_end_action(self):
        flow = self.get_flow("ussd_session_end")
        contact = self.create_contact("Joe", "+250788383383")
        flow.start([], [contact])

        # choose option with destination connected to another type of ruleset (Split by Expression)
        session = USSDSession.handle_incoming(
            channel=self.channel, urn="+250788383383", content="9", date=timezone.now(), external_id="21345"
        )

        # there should be a last message with the message of the end action
        msgs = Msg.objects.filter(direction="O").order_by("id")

        self.assertEqual(msgs.last().text, "c")

        session.refresh_from_db()

        self.assertEqual(session.status, USSDSession.ENDING)
        self.assertIsNone(session.ended_on)
        self.assertEqual(session.external_id, "21345")

    def test_ruleset_through_rulesets_ending_with_no_action(self):
        flow = self.get_flow("ussd_session_end")
        contact = self.create_contact("Joe", "+250788383383")
        flow.start([], [contact])

        # choose option with destination connected to another type of ruleset (Split by Expression)
        session = USSDSession.handle_incoming(
            channel=self.channel, urn="+250788383383", content="7", date=timezone.now(), external_id="21345"
        )

        # there should be a last message with empty message
        msgs = Msg.objects.filter(direction="O").order_by("id")

        self.assertEqual(msgs.last().text, "")

        session.refresh_from_db()

        self.assertEqual(session.status, USSDSession.ENDING)
        self.assertIsNone(session.ended_on)
        self.assertEqual(session.external_id, "21345")
