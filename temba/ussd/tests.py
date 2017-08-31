# -*- coding: utf-8 -*-
from __future__ import unicode_literals

import json
import six
import uuid

from datetime import datetime, timedelta
from mock import patch

from django.core.urlresolvers import reverse
from django.conf import settings
from django.utils import timezone
from django_redis import get_redis_connection

from temba.channels.models import Channel
from temba.channels.tests import JunebugTestMixin
from temba.contacts.models import Contact, TEL_SCHEME
from temba.msgs.models import WIRED, MSG_SENT_KEY, SENT, Msg, INCOMING, OUTGOING, USSD, DELIVERED, FAILED, HANDLED
from temba.tests import TembaTest, MockResponse
from temba.triggers.models import Trigger
from temba.flows.models import FlowRun
from temba.utils import dict_to_struct

from .models import USSDSession


class USSDSessionTest(TembaTest):

    def setUp(self):
        super(USSDSessionTest, self).setUp()

        self.channel.delete()
        self.channel = Channel.create(self.org, self.user, 'RW', Channel.TYPE_JUNEBUG_USSD, None, '+250788123123',
                                      role=Channel.ROLE_USSD + Channel.DEFAULT_ROLE,
                                      uuid='00000000-0000-0000-0000-000000001234')

    def test_pull_async_trigger_start(self):
        flow = self.get_flow('ussd_example')

        starcode = "*113#"

        trigger, _ = Trigger.objects.get_or_create(channel=self.channel, keyword=starcode, flow=flow,
                                                   created_by=self.user, modified_by=self.user, org=self.org,
                                                   trigger_type=Trigger.TYPE_USSD_PULL)

        # handle a message that has an unmatched wrong keyword
        session, _ = USSDSession.handle_incoming(channel=self.channel, urn="+329732973", content="None",
                                                 status=USSDSession.TRIGGERED, date=timezone.now(), external_id="1234",
                                                 message_id="1111211", starcode="wrongkeyword")

        # check if session was started and created
        self.assertFalse(session)

        # handle a message that has an unmatched starcode
        session, _ = USSDSession.handle_incoming(channel=self.channel, urn="+329732973", content="None",
                                                 status=USSDSession.TRIGGERED, date=timezone.now(), external_id="1234",
                                                 message_id="1111211", starcode="*111#")

        # check if session was started and created
        self.assertFalse(session)

        session, _ = USSDSession.handle_incoming(channel=self.channel, urn="+329732973", content="None",
                                                 status=USSDSession.TRIGGERED, date=timezone.now(), external_id="1235",
                                                 message_id="1111131", starcode=starcode)

        # check session properties
        self.assertEqual(session.status, USSDSession.TRIGGERED)
        self.assertIsInstance(session.started_on, datetime)
        self.assertEqual(session.external_id, "1235")

    def test_push_async_start(self):
        flow = self.get_flow('ussd_example')

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
        msg = Msg.objects.get()

        self.assertEqual(flow.get_steps().get().messages.get().text, msg.text)

        return flow

    def test_async_content_handling(self):
        # start off a PUSH session
        flow = self.test_push_async_start()

        # send an incoming message through the channel
        session, _ = USSDSession.handle_incoming(channel=self.channel, urn="+250788383383", content="1",
                                                 date=timezone.now(), external_id="21345", message_id="123")

        # same session is modified
        self.assertEqual(USSDSession.objects.count(), 1)

        # new status
        self.assertEqual(session.status, USSDSession.IN_PROGRESS)

        # there should be 3 messages
        self.assertEqual(Msg.objects.count(), 3)

        # lets check the steps and incoming and outgoing messages
        # first step has 1 outgoing and the answer
        self.assertEqual(flow.get_steps().first().messages.count(), 2)
        self.assertEqual(flow.get_steps().first().messages.last().direction, OUTGOING)
        self.assertEqual(flow.get_steps().first().messages.last().text, u'What would you like to read about?')

        self.assertEqual(flow.get_steps().first().messages.first().direction, INCOMING)
        self.assertEqual(flow.get_steps().first().messages.first().text, u'1')

        # second step sent out the next message and waits for response
        self.assertEqual(flow.get_steps().last().messages.count(), 1)
        self.assertEqual(flow.get_steps().last().messages.first().direction, OUTGOING)
        self.assertEqual(flow.get_steps().last().messages.first().text, u'Thank you!')

    def test_expiration(self):
        # start off a PUSH session
        self.test_push_async_start()
        run = FlowRun.objects.last()
        run.expire()

        # we should be marked as interrupted now
        self.assertEqual(USSDSession.INTERRUPTED, run.session.status)

    def test_async_interrupt_handling(self):
        # start a flow
        flow = self.get_flow('ussd_example')
        contact = self.create_contact("Joe", "+250788383383")
        flow.start([], [contact])

        session = USSDSession.objects.get()
        # check if session was created
        self.assertEqual(session.direction, USSDSession.USSD_PUSH)
        self.assertEqual(session.status, USSDSession.INITIATED)
        self.assertIsInstance(session.started_on, datetime)

        # send an interrupt "signal"
        session, _ = USSDSession.handle_incoming(channel=self.channel, urn="+250788383383",
                                                 status=USSDSession.INTERRUPTED,
                                                 date=timezone.now(), external_id="21345")

        # same session is modified
        self.assertEqual(USSDSession.objects.count(), 1)

        # session is modified with proper status and end date
        self.assertEqual(session.status, USSDSession.INTERRUPTED)
        self.assertIsInstance(session.ended_on, datetime)
        self.assertEqual(session.external_id, "21345")

    @patch('temba.ussd.models.USSDSession.handle_session_async')
    def test_async_interrupt_handling_with_helper(self, handle_session_async):
        flow = self.get_flow('ussd_session_end')
        contact = self.create_contact("Joe", "+250788383383")
        flow.start([], [contact])

        session_id = "21345"

        session, _ = USSDSession.handle_incoming(channel=self.channel, urn="+250788383383", content="1",
                                                 date=timezone.now(), external_id=session_id)

        USSDSession.handle_interrupt(session_id)

        session.refresh_from_db()

        self.assertTrue(handle_session_async.called)
        self.assertEqual(session.status, USSDSession.INTERRUPTED)

    def test_end_with_menu_no_destination(self):
        flow = self.get_flow('ussd_session_end')
        contact = self.create_contact("Joe", "+250788383383")
        flow.start([], [contact])

        # choose option with no destination connected
        session, _ = USSDSession.handle_incoming(channel=self.channel, urn="+250788383383", content="4",
                                                 date=timezone.now(), external_id="21345")

        # there should be 2 outgoing messages, one with an empty message to close down the session gracefully
        self.assertEqual(Msg.objects.count(), 3)
        self.assertEqual(Msg.objects.filter(direction='O').count(), 2)

        msgs = Msg.objects.filter(direction='O').order_by('id')

        self.assertNotEqual(msgs[0].text, '')
        self.assertEqual(msgs[1].text, '')

        # the session should be marked as "ENDING"
        session.refresh_from_db()
        self.assertEqual(session.status, USSDSession.ENDING)
        self.assertIsNone(session.ended_on)
        self.assertEqual(session.external_id, "21345")

    def test_end_with_menu_destination_with_messaging(self):
        flow = self.get_flow('ussd_session_end')
        contact = self.create_contact("Joe", "+250788383383")
        flow.start([], [contact])

        # choose option with destination connected to another wait_menu ruleset
        session, _ = USSDSession.handle_incoming(channel=self.channel, urn="+250788383383", content="1",
                                                 date=timezone.now(), external_id="21345")

        # the session shouldn't be marked as ending
        self.assertEqual(session.status, USSDSession.IN_PROGRESS)
        self.assertIsNone(session.ended_on)
        self.assertEqual(session.external_id, "21345")

    def test_end_with_menu_destination_without_messaging(self):
        flow = self.get_flow('ussd_session_end')
        contact = self.create_contact("Joe", "+250788383383")
        flow.start([], [contact])

        # choose option with destination connected to another wait_menu ruleset
        session, _ = USSDSession.handle_incoming(channel=self.channel, urn="+250788383383", content="test",
                                                 date=timezone.now(), external_id="21345")

        # the session shouldn't be marked as ending
        self.assertEqual(session.status, USSDSession.IN_PROGRESS)
        self.assertIsNone(session.ended_on)
        self.assertEqual(session.external_id, "21345")

        # choose option with destination connected to actions (add to groups and set language later)
        session, _ = USSDSession.handle_incoming(channel=self.channel, urn="+250788383383", content="2",
                                                 date=timezone.now(), external_id="21345")

        # there should be a last message with no content to close the session
        msgs = Msg.objects.filter(direction='O').order_by('id')

        self.assertEqual(msgs.last().text, '')

        # the session should be marked as ending at this point, cos' there's no more messaging destination in the flow
        session.refresh_from_db()
        self.assertEqual(session.status, USSDSession.ENDING)
        self.assertIsNone(session.ended_on)
        self.assertEqual(session.external_id, "21345")

    def test_end_with_arbitrary_rules(self):
        flow = self.get_flow('ussd_session_end')
        contact = self.create_contact("Joe", "+250788383383")
        flow.start([], [contact])

        # choose option with destination connected to the arbitarty ussd ruleset
        session, _ = USSDSession.handle_incoming(channel=self.channel, urn="+250788383383", content="3",
                                                 date=timezone.now(), external_id="21345")

        # the session shouldn't be marked as ending
        self.assertEqual(session.status, USSDSession.IN_PROGRESS)
        self.assertIsNone(session.ended_on)
        self.assertEqual(session.external_id, "21345")

        # now we are at the ussd response, choose an option here
        session, _ = USSDSession.handle_incoming(channel=self.channel, urn="+250788383383", content="3",
                                                 date=timezone.now(), external_id="21345")

        session.refresh_from_db()
        self.assertEqual(session.status, USSDSession.ENDING)
        self.assertIsNone(session.ended_on)
        self.assertEqual(session.external_id, "21345")

    def test_end_with_end_action(self):
        flow = self.get_flow('ussd_session_end')
        contact = self.create_contact("Joe", "+250788383383")
        flow.start([], [contact])

        # choose option with destination connected to the arbitarty ussd ruleset
        session, _ = USSDSession.handle_incoming(channel=self.channel, urn="+250788383383", content="3",
                                                 date=timezone.now(), external_id="21345")

        # the session shouldn't be marked as ending
        self.assertEqual(session.status, USSDSession.IN_PROGRESS)
        self.assertIsNone(session.ended_on)
        self.assertEqual(session.external_id, "21345")

        # choose the Maybe option which leads to a USSD End with message action
        session, _ = USSDSession.handle_incoming(channel=self.channel, urn="+250788383383", content="Maybe",
                                                 date=timezone.now(), external_id="21345")

        # there should be a last message with the message of the end action
        msgs = Msg.objects.filter(direction='O').order_by('id')

        self.assertEqual(msgs.last().text, 'perfect, thanks')

        # same session is modified
        self.assertEqual(USSDSession.objects.count(), 1)

        session.refresh_from_db()

        self.assertEqual(session.status, USSDSession.ENDING)
        self.assertIsNone(session.ended_on)
        self.assertEqual(session.external_id, "21345")

    def test_end_with_end_action_with_more_actions_in_actionset(self):
        flow = self.get_flow('ussd_session_end')
        contact = self.create_contact("Joe", "+250788383383")
        flow.start([], [contact])

        # choose option with destination connected to a set of actions including an end session w message action
        session, _ = USSDSession.handle_incoming(channel=self.channel, urn="+250788383383", content="2",
                                                 date=timezone.now(), external_id="21345")

        # there should be a last message with the message of the end action
        msgs = Msg.objects.filter(direction='O').order_by('id')

        self.assertEqual(msgs.last().text, 'Im sorry, we will contact you')

        session.refresh_from_db()

        # the session should be marked as ending
        self.assertEqual(session.status, USSDSession.ENDING)
        self.assertIsNone(session.ended_on)
        self.assertEqual(session.external_id, "21345")

    def test_ruleset_through_ruleset_no_destination(self):
        flow = self.get_flow('ussd_session_end')
        contact = self.create_contact("Joe", "+250788383383")
        flow.start([], [contact])

        # choose option with destination connected to another type of ruleset (Split by Expression)
        session, _ = USSDSession.handle_incoming(channel=self.channel, urn="+250788383383", content="6",
                                                 date=timezone.now(), external_id="21345")

        # there should be a last message with empty msg just to close the session gracefully
        msgs = Msg.objects.filter(direction='O').order_by('id')

        self.assertEqual(msgs.last().text, '')

        session.refresh_from_db()
        # the session should be marked as ending since the next ruleset doesn't have any destination
        self.assertEqual(session.status, USSDSession.ENDING)
        self.assertIsNone(session.ended_on)
        self.assertEqual(session.external_id, "21345")

    def test_ruleset_through_ruleset_with_third_ruleset_destination(self):
        flow = self.get_flow('ussd_session_end')
        contact = self.create_contact("Joe", "+250788383383")
        flow.start([], [contact])

        # choose option with destination connected to another type of ruleset (Split by Expression)
        session, _ = USSDSession.handle_incoming(channel=self.channel, urn="+250788383383", content="5",
                                                 date=timezone.now(), external_id="21345")

        # there should be a last message with the message of the end action
        msgs = Msg.objects.filter(direction='O').order_by('id')

        self.assertEqual(msgs.last().text, 'a')

        session.refresh_from_db()

        self.assertEqual(session.status, USSDSession.ENDING)
        self.assertIsNone(session.ended_on)
        self.assertEqual(session.external_id, "21345")

    def test_ruleset_through_two_rulesets_ending_with_not_messaging_action(self):
        flow = self.get_flow('ussd_session_end')
        contact = self.create_contact("Joe", "+250788383383")
        flow.start([], [contact])

        # choose option with destination connected to another type of ruleset (Split by Expression)
        session, _ = USSDSession.handle_incoming(channel=self.channel, urn="+250788383383", content="8",
                                                 date=timezone.now(), external_id="21345")

        # there should be a last message with empty message
        msgs = Msg.objects.filter(direction='O').order_by('id')

        self.assertEqual(msgs.last().text, '')

        session.refresh_from_db()

        self.assertEqual(session.status, USSDSession.ENDING)
        self.assertIsNone(session.ended_on)
        self.assertEqual(session.external_id, "21345")

    def test_ruleset_through_ruleset_ending_with_end_action(self):
        flow = self.get_flow('ussd_session_end')
        contact = self.create_contact("Joe", "+250788383383")
        flow.start([], [contact])

        # choose option with destination connected to another type of ruleset (Split by Expression)
        session, _ = USSDSession.handle_incoming(channel=self.channel, urn="+250788383383", content="9",
                                                 date=timezone.now(), external_id="21345")

        # there should be a last message with the message of the end action
        msgs = Msg.objects.filter(direction='O').order_by('id')

        self.assertEqual(msgs.last().text, 'c')

        session.refresh_from_db()

        self.assertEqual(session.status, USSDSession.ENDING)
        self.assertIsNone(session.ended_on)
        self.assertEqual(session.external_id, "21345")

    def test_ruleset_through_rulesets_ending_with_no_action(self):
        flow = self.get_flow('ussd_session_end')
        contact = self.create_contact("Joe", "+250788383383")
        flow.start([], [contact])

        # choose option with destination connected to another type of ruleset (Split by Expression)
        session, _ = USSDSession.handle_incoming(channel=self.channel, urn="+250788383383", content="7",
                                                 date=timezone.now(), external_id="21345")

        # there should be a last message with empty message
        msgs = Msg.objects.filter(direction='O').order_by('id')

        self.assertEqual(msgs.last().text, '')

        session.refresh_from_db()

        self.assertEqual(session.status, USSDSession.ENDING)
        self.assertIsNone(session.ended_on)
        self.assertEqual(session.external_id, "21345")


class VumiUssdTest(TembaTest):

    def setUp(self):
        super(VumiUssdTest, self).setUp()

        self.channel.delete()
        self.channel = Channel.create(self.org, self.user, 'RW', Channel.TYPE_VUMI_USSD, None, '+250788123123',
                                      config=dict(account_key='vumi-key', access_token='vumi-token',
                                                  conversation_key='key'),
                                      uuid='00000000-0000-0000-0000-000000001234',
                                      role=Channel.ROLE_USSD)

    def test_send(self):
        joe = self.create_contact("Joe", "+250788383383")
        self.create_group("Reporters", [joe])
        inbound = Msg.create_incoming(
            self.channel, "tel:+250788383383", "Send an inbound message",
            external_id='vumi-message-id', msg_type=USSD)
        msg = inbound.reply("Test message", self.admin, trigger_send=False)[0]
        self.assertEqual(inbound.msg_type, USSD)
        self.assertEqual(msg.msg_type, USSD)

        # our outgoing message
        msg.refresh_from_db()
        r = get_redis_connection()

        try:
            settings.SEND_MESSAGES = True

            with patch('requests.put') as mock:
                mock.return_value = MockResponse(200, '{ "message_id": "1515" }')

                # manually send it off
                Channel.send_message(dict_to_struct('MsgStruct', msg.as_task_json()))

                # check the status of the message is now sent
                msg.refresh_from_db()
                self.assertEquals(WIRED, msg.status)
                self.assertTrue(msg.sent_on)
                self.assertEquals("1515", msg.external_id)
                self.assertEquals(1, mock.call_count)

                # should have a failsafe that it was sent
                self.assertTrue(r.sismember(timezone.now().strftime(MSG_SENT_KEY), str(msg.id)))

                # try sending again, our failsafe should kick in
                Channel.send_message(dict_to_struct('MsgStruct', msg.as_task_json()))

                # we shouldn't have been called again
                self.assertEquals(1, mock.call_count)

                self.clear_cache()
        finally:
            settings.SEND_MESSAGES = False

    def test_send_default_url(self):
        joe = self.create_contact("Joe", "+250788383383")
        self.create_group("Reporters", [joe])
        inbound = Msg.create_incoming(
            self.channel, "tel:+250788383383", "Send an inbound message",
            external_id='vumi-message-id', msg_type=USSD)
        msg = inbound.reply("Test message", self.admin, trigger_send=False)[0]

        # our outgoing message
        msg.refresh_from_db()

        try:
            settings.SEND_MESSAGES = True

            with patch('requests.put') as mock:
                mock.return_value = MockResponse(200, '{ "message_id": "1515" }')

                # manually send it off
                Channel.send_message(dict_to_struct('MsgStruct', msg.as_task_json()))

                self.assertEqual(mock.call_args[0][0],
                                 'https://go.vumi.org/api/v1/go/http_api_nostream/key/messages.json')

                self.clear_cache()

        finally:
            settings.SEND_MESSAGES = False

    def test_ack(self):
        joe = self.create_contact("Joe", "+250788383383")
        self.create_group("Reporters", [joe])
        inbound = Msg.create_incoming(
            self.channel, "tel:+250788383383", "Send an inbound message",
            external_id='vumi-message-id', msg_type=USSD)
        msg = inbound.reply("Test message", self.admin, trigger_send=False)[0]

        # our outgoing message
        msg.refresh_from_db()

        try:
            settings.SEND_MESSAGES = True

            with patch('requests.put') as mock:
                mock.return_value = MockResponse(200, '{ "message_id": "1515" }')

                # manually send it off
                Channel.send_message(dict_to_struct('MsgStruct', msg.as_task_json()))

                # check the status of the message is now sent
                msg.refresh_from_db()
                self.assertEquals(WIRED, msg.status)
                self.assertTrue(msg.sent_on)
                self.assertEquals("1515", msg.external_id)
                self.assertEquals(1, mock.call_count)

                # simulate Vumi calling back to us sending an ACK event
                data = {
                    "transport_name": "ussd_transport",
                    "event_type": "ack",
                    "event_id": six.text_type(uuid.uuid4()),
                    "sent_message_id": six.text_type(uuid.uuid4()),
                    "helper_metadata": {},
                    "routing_metadata": {},
                    "message_version": "20110921",
                    "timestamp": six.text_type(timezone.now()),
                    "transport_metadata": {},
                    "user_message_id": msg.external_id,
                    "message_type": "event"
                }
                callback_url = reverse('handlers.vumi_handler', args=['event', self.channel.uuid])
                self.client.post(callback_url, json.dumps(data), content_type="application/json")

                # it should be SENT now
                msg.refresh_from_db()
                self.assertEquals(SENT, msg.status)

                self.clear_cache()
        finally:
            settings.SEND_MESSAGES = False

    def test_nack(self):
        joe = self.create_contact("Joe", "+250788383383")
        self.create_group("Reporters", [joe])
        inbound = Msg.create_incoming(
            self.channel, "tel:+250788383383", "Send an inbound message",
            external_id='vumi-message-id', msg_type=USSD)
        msg = inbound.reply("Test message", self.admin, trigger_send=False)[0]

        # our outgoing message
        msg.refresh_from_db()
        r = get_redis_connection()

        try:
            settings.SEND_MESSAGES = True

            with patch('requests.put') as mock:
                mock.return_value = MockResponse(200, '{ "message_id": "1515" }')

                # manually send it off
                Channel.send_message(dict_to_struct('MsgStruct', msg.as_task_json()))

                # check the status of the message is now sent
                msg.refresh_from_db()
                self.assertEquals(WIRED, msg.status)
                self.assertTrue(msg.sent_on)
                self.assertEquals("1515", msg.external_id)
                self.assertEquals(1, mock.call_count)

                # should have a failsafe that it was sent
                self.assertTrue(r.sismember(timezone.now().strftime(MSG_SENT_KEY), str(msg.id)))

                # simulate Vumi calling back to us sending an NACK event
                data = {
                    "transport_name": "ussd_transport",
                    "event_type": "nack",
                    "nack_reason": "Unknown address.",
                    "event_id": six.text_type(uuid.uuid4()),
                    "timestamp": six.text_type(timezone.now()),
                    "message_version": "20110921",
                    "transport_metadata": {},
                    "user_message_id": msg.external_id,
                    "message_type": "event"
                }
                callback_url = reverse('handlers.vumi_handler', args=['event', self.channel.uuid])
                response = self.client.post(callback_url, json.dumps(data), content_type="application/json")

                self.assertEqual(response.status_code, 200)
                self.assertTrue(self.create_contact("Joe", "+250788383383").is_stopped)

                self.clear_cache()
        finally:
            settings.SEND_MESSAGES = False

    @patch('temba.msgs.models.Msg.create_incoming')
    @patch('temba.ussd.models.USSDSession.start_session_async')
    def test_triggered_ussd_pull(self, start_session_async, create_incoming):
        callback_url = reverse('handlers.vumi_handler', args=['receive', self.channel.uuid])

        ussd_code = "*111#"

        data = dict(timestamp="2016-04-18 03:54:20.570618", message_id="123456", from_addr="+250788383383",
                    content="None", transport_type='ussd', session_event="new", to_addr=ussd_code)

        response = self.client.post(callback_url, json.dumps(data), content_type="application/json")

        # getting a message without Trigger
        self.assertEqual(response.status_code, 400)

        flow = self.get_flow('ussd_example')

        trigger, _ = Trigger.objects.get_or_create(channel=self.channel, keyword=ussd_code, flow=flow,
                                                   created_by=self.user, modified_by=self.user, org=self.org,
                                                   trigger_type=Trigger.TYPE_USSD_PULL)

        # now we added the trigger, let's reinitiate the session
        response = self.client.post(callback_url, json.dumps(data), content_type="application/json")

        # should be handled now since we have a corresponding trigger
        self.assertEqual(response.status_code, 200)

        # no real messages stored
        self.assertEquals(Msg.objects.count(), 0)

        # ensure no messages has been created
        self.assertFalse(create_incoming.called)

        # check if session was started
        self.assertTrue(start_session_async.called)
        self.assertEqual(start_session_async.call_count, 1)

    def test_receive(self):
        # start a session
        self.test_triggered_ussd_pull()

        callback_url = reverse('handlers.vumi_handler', args=['receive', self.channel.uuid])

        response = self.client.get(callback_url)
        self.assertEqual(response.status_code, 405)

        response = self.client.post(callback_url, json.dumps(dict()), content_type="application/json")
        self.assertEqual(response.status_code, 404)

        from_addr = "+250788383383"

        data = dict(timestamp="2016-04-18 03:54:20.570618", message_id="123456", from_addr=from_addr,
                    content="Hello from Vumi", to_addr="*113#", transport_type='ussd')

        response = self.client.post(callback_url, json.dumps(data), content_type="application/json")

        self.assertEqual(response.status_code, 200)

        msg = Msg.objects.get()
        self.assertEquals(INCOMING, msg.direction)
        self.assertEquals(self.org, msg.org)
        self.assertEquals(self.channel, msg.channel)
        self.assertEquals("Hello from Vumi", msg.text)
        self.assertEquals('123456', msg.external_id)
        Msg.objects.all().delete()

        # test with vumi session data
        data = dict(timestamp="2016-04-18 03:54:20.570618", message_id="123457", from_addr=from_addr,
                    content="Hello from Vumi 2", to_addr="*113#", transport_type='ussd')

        session_start = "12341423453"
        data.update({
            "helper_metadata": {
                "session_metadata": {
                    "session_start": session_start
                }
            }
        })

        response = self.client.post(callback_url, json.dumps(data), content_type="application/json")

        self.assertEqual(response.status_code, 200)

        session = USSDSession.objects.last()
        self.assertEqual(session.external_id, str(int(from_addr) + int(session_start)))

        msg = Msg.objects.get()
        self.assertEquals(INCOMING, msg.direction)
        self.assertEquals(self.org, msg.org)
        self.assertEquals(self.channel, msg.channel)
        self.assertEquals("Hello from Vumi 2", msg.text)
        self.assertEquals('123457', msg.external_id)
        self.assertEquals(session, msg.session)

    @patch('temba.msgs.models.Msg.create_incoming')
    def test_interrupt(self, create_incoming):
        # start a session
        self.test_triggered_ussd_pull()

        callback_url = reverse('handlers.vumi_handler', args=['receive', self.channel.uuid])

        response = self.client.get(callback_url)
        self.assertEqual(response.status_code, 405)

        response = self.client.post(callback_url, json.dumps(dict()), content_type="application/json")
        self.assertEqual(response.status_code, 404)

        data = dict(timestamp="2016-04-18 03:54:20.570618", message_id="123456", from_addr="+250788383383",
                    content="Hello from Vumi", transport_type='ussd', session_event="close")

        response = self.client.post(callback_url, json.dumps(data), content_type="application/json")

        self.assertEqual(response.status_code, 200)

        # no real messages stored
        self.assertEquals(Msg.objects.count(), 0)

        self.assertTrue(create_incoming.called)
        self.assertEqual(create_incoming.call_count, 1)

        session = USSDSession.objects.get()
        self.assertIsInstance(session.ended_on, datetime)
        self.assertEqual(session.status, USSDSession.INTERRUPTED)

    def test_send_session_close(self):
        flow = self.get_flow('ussd_session_end')
        contact = self.create_contact("Joe", "+250788383383")

        try:
            settings.SEND_MESSAGES = True

            with patch('requests.put') as mock:
                mock.return_value = MockResponse(200, '{ "message_id": "1515" }')
                flow.start([], [contact])

                # our outgoing message
                msg = Msg.objects.filter(direction='O').order_by('id').last()

                self.assertEqual(msg.direction, 'O')
                self.assertTrue(msg.sent_on)
                self.assertEquals("1515", msg.external_id)
                self.assertEquals(msg.session.status, USSDSession.INITIATED)

                # reply and choose an option that doesn't have any destination thus needs to close the session
                USSDSession.handle_incoming(channel=self.channel, urn="+250788383383", content="4",
                                            date=timezone.now(), external_id="21345")

                # our outgoing message
                msg = Msg.objects.filter(direction='O').order_by('id').last()
                self.assertEqual(msg.direction, 'O')
                self.assertTrue(msg.sent_on)
                self.assertEquals("1515", msg.external_id)

                self.assertEquals(msg.session.status, USSDSession.COMPLETED)

                self.assertEquals(2, mock.call_count)

                self.clear_cache()
        finally:
            settings.SEND_MESSAGES = False

    def test_ussd_trigger_flow(self):
        # start a session
        callback_url = reverse('handlers.vumi_handler', args=['receive', self.channel.uuid])
        ussd_code = "*111#"
        data = dict(timestamp="2016-04-18 03:54:20.570618", message_id="123456", from_addr="+250788383383",
                    content="None", transport_type='ussd', session_event="new", to_addr=ussd_code)
        response = self.client.post(callback_url, json.dumps(data), content_type="application/json")
        flow = self.get_flow('ussd_trigger_flow')
        self.assertEquals(0, flow.runs.all().count())

        trigger, _ = Trigger.objects.get_or_create(channel=self.channel, keyword=ussd_code, flow=flow,
                                                   created_by=self.user, modified_by=self.user, org=self.org,
                                                   trigger_type=Trigger.TYPE_USSD_PULL)

        # now we added the trigger, let's reinitiate the session
        response = self.client.post(callback_url, json.dumps(data), content_type="application/json")

        msg = Msg.objects.all().first()
        self.assertEqual("Please enter a phone number", msg.text)

        from_addr = "+250788383383"
        data = dict(timestamp="2016-04-18 03:54:20.570618", message_id="123456", from_addr=from_addr,
                    content="250788123123", to_addr="*113#", transport_type='ussd')

        response = self.client.post(callback_url, json.dumps(data), content_type="application/json")

        self.assertEqual(response.status_code, 200)

        msg = Msg.objects.all().order_by('-created_on').first()

        # We should get the final message
        self.assertEqual("Thank you", msg.text)

        # Check the new contact was created
        new_contact = Contact.from_urn(self.org, "tel:+250788123123")
        self.assertIsNotNone(new_contact)


class JunebugUSSDTest(JunebugTestMixin, TembaTest):

    def setUp(self):
        super(JunebugUSSDTest, self).setUp()

        flow = self.get_flow('ussd_example')
        self.starcode = "*113#"

        self.channel.delete()
        self.channel = Channel.create(
            self.org, self.user, 'RW', Channel.TYPE_JUNEBUG_USSD, None, '1234',
            config=dict(username='junebug-user', password='junebug-pass', send_url='http://example.org/'),
            uuid='00000000-0000-0000-0000-000000001234', role=Channel.ROLE_USSD)

        self.trigger, _ = Trigger.objects.get_or_create(
            channel=self.channel, keyword=self.starcode, flow=flow,
            created_by=self.user, modified_by=self.user, org=self.org,
            trigger_type=Trigger.TYPE_USSD_PULL)

    def tearDown(self):
        super(JunebugUSSDTest, self).tearDown()
        settings.SEND_MESSAGES = False

    def test_status(self):
        joe = self.create_contact("Joe Biden", "+254788383383")
        msg = joe.send("Hey Joe, it's Obama, pick up!", self.admin, msg_type=USSD)[0]

        data = self.mk_event()
        msg.external_id = data['message_id']
        msg.save(update_fields=('external_id',))

        def assertStatus(sms, event_type, assert_status):
            data['event_type'] = event_type
            response = self.client.post(
                reverse('handlers.junebug_handler',
                        args=['event', self.channel.uuid]),
                data=json.dumps(data),
                content_type='application/json')
            self.assertEquals(200, response.status_code)
            sms = Msg.objects.get(pk=sms.id)
            self.assertEquals(assert_status, sms.status)

        assertStatus(msg, 'submitted', SENT)
        assertStatus(msg, 'delivery_succeeded', DELIVERED)
        assertStatus(msg, 'delivery_failed', FAILED)
        assertStatus(msg, 'rejected', FAILED)

    def test_receive_ussd(self):
        from temba.ussd.models import USSDSession
        from temba.channels.handlers import JunebugHandler

        data = self.mk_ussd_msg(content="événement", to=self.starcode)
        callback_url = reverse('handlers.junebug_handler',
                               args=['inbound', self.channel.uuid])
        response = self.client.post(callback_url, json.dumps(data),
                                    content_type='application/json')

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['status'], JunebugHandler.ACK)

        # load our message
        inbound_msg, outbound_msg = Msg.objects.all().order_by('pk')
        self.assertEquals(data["from"], outbound_msg.contact.get_urn(TEL_SCHEME).path)
        self.assertEquals(outbound_msg.response_to, inbound_msg)
        self.assertEquals(outbound_msg.session.status, USSDSession.TRIGGERED)
        self.assertEquals(inbound_msg.direction, INCOMING)

    def test_receive_with_session_id(self):
        from temba.ussd.models import USSDSession

        data = self.mk_ussd_msg(content="événement", session_id='session-id', to=self.starcode)
        callback_url = reverse('handlers.junebug_handler',
                               args=['inbound', self.channel.uuid])
        self.client.post(callback_url, json.dumps(data), content_type='application/json')

        # load our message
        inbound_msg, outbound_msg = Msg.objects.all().order_by('pk')
        self.assertEquals(outbound_msg.session.status, USSDSession.TRIGGERED)
        self.assertEquals(outbound_msg.session.external_id, 'session-id')
        self.assertEquals(inbound_msg.session.external_id, 'session-id')

    def test_receive_ussd_no_session(self):
        from temba.channels.handlers import JunebugHandler

        # Delete the trigger to prevent the sesion from being created
        self.trigger.delete()

        data = self.mk_ussd_msg(content="événement", to=self.starcode)
        callback_url = reverse('handlers.junebug_handler',
                               args=['inbound', self.channel.uuid])
        response = self.client.post(callback_url, json.dumps(data),
                                    content_type='application/json')

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()['status'], JunebugHandler.NACK)

    def test_send_ussd_continue_and_end_session(self):
        flow = self.get_flow('ussd_session_end')
        contact = self.create_contact("Joe", "+250788383383")

        try:
            settings.SEND_MESSAGES = True

            with patch('requests.post') as mock:
                mock.return_value = MockResponse(200, json.dumps({
                    'result': {
                        'message_id': '07033084-5cfd-4812-90a4-e4d24ffb6e3d',
                    }
                }))

                flow.start([], [contact])

                # our outgoing message
                msg = Msg.objects.filter(direction='O').order_by('id').last()

                self.assertEqual(msg.direction, 'O')
                self.assertTrue(msg.sent_on)
                self.assertEquals("07033084-5cfd-4812-90a4-e4d24ffb6e3d", msg.external_id)
                self.assertEquals(msg.session.status, USSDSession.INITIATED)

                # reply and choose an option that doesn't have any destination thus needs to close the session
                USSDSession.handle_incoming(channel=self.channel, urn="+250788383383", content="4",
                                            date=timezone.now(), external_id="21345", message_id='vumi-message-id')
                # our outgoing message
                msg = Msg.objects.filter(direction='O').order_by('id').last()
                self.assertEquals(WIRED, msg.status)
                self.assertEqual(msg.direction, 'O')
                self.assertTrue(msg.sent_on)
                self.assertEquals("07033084-5cfd-4812-90a4-e4d24ffb6e3d", msg.external_id)
                self.assertEquals("vumi-message-id", msg.response_to.external_id)

                self.assertEquals(msg.session.status, USSDSession.COMPLETED)
                self.assertTrue(isinstance(msg.session.get_duration(), timedelta))

                self.assertEquals(2, mock.call_count)

                # first outbound (session continued)
                call = mock.call_args_list[0]
                (args, kwargs) = call
                payload = kwargs['json']
                self.assertIsNone(payload.get('reply_to'))
                self.assertEquals(payload.get('to'), "+250788383383")
                self.assertEquals(payload['channel_data'], {
                    'continue_session': True
                })

                # second outbound (session ended)
                call = mock.call_args_list[1]
                (args, kwargs) = call
                payload = kwargs['json']
                self.assertEquals(payload['reply_to'], 'vumi-message-id')
                self.assertEquals(payload.get('to'), None)
                self.assertEquals(payload['channel_data'], {
                    'continue_session': False
                })

                self.clear_cache()
        finally:
            settings.SEND_MESSAGES = False


class InfoBipUSSDTest(TembaTest):

    def setUp(self):
        super(InfoBipUSSDTest, self).setUp()

        flow = self.get_flow('ussd_example_modern')
        self.starcode = "*113#"

        self.channel.delete()
        self.channel = Channel.create(self.org, self.user, 'NG', 'IBU', None, self.starcode,
                                      config=dict(sync_handling=True), role=Channel.ROLE_USSD,
                                      uuid='00000000-0000-0000-0000-000000001234')

        self.trigger, _ = Trigger.objects.get_or_create(
            channel=self.channel, keyword=self.starcode, flow=flow,
            created_by=self.user, modified_by=self.user, org=self.org,
            trigger_type=Trigger.TYPE_USSD_PULL)

    def test_start(self):
        callback_url = reverse('handlers.infobip_ussd_handler',
                               args=[self.channel.uuid, '13cc8b28afb86c69766531', 'start'])

        start_data = {
            'msisdn': '+2347030767143',
            'imsi': '8796df56as657',
            'shortCode': self.starcode,
            'ussdNodeId': 'testNodeId',
            'text': 'auxiliary data that was sent when triggering the session',
            'networkName': 'Orange',
            'countryName': 'NG'
        }

        response = self.client.post(callback_url, json.dumps(start_data), content_type='application/json')

        self.assertEqual(response.status_code, 200)

        session = USSDSession.objects.get()

        self.assertEqual(session.status, USSDSession.TRIGGERED)
        self.assertTrue(session.runs.first().is_active)

        msgs = session.msg_set.order_by('id').all()

        # should have two messages, one for the trigger, one for the first menu
        self.assertEqual(msgs.count(), 2)
        self.assertEqual(msgs.first().text, '')
        self.assertEqual(msgs.first().status, HANDLED)
        self.assertEqual(msgs.last().text, u"What's up?\n1: Good\n2: Nada")
        self.assertEqual(msgs.last().status, WIRED)

        self.assertEqual(response.json()['responseExitCode'], 200)
        self.assertEqual(response.json()['responseMessage'], '')
        self.assertEqual(response.json()['shouldClose'], 'true' if session.should_end else 'false')
        self.assertEqual(response.json()['ussdMenu'], msgs.last().text)

    def test_start_with_no_channel(self):
        self.channel.delete()

        callback_url = reverse('handlers.infobip_ussd_handler',
                               args=[self.channel.uuid, '13cc8b28afb86c69766531', 'start'])

        start_data = {
            'msisdn': '+2347030767143',
            'imsi': '8796df56as657',
            'shortCode': self.starcode,
            'ussdNodeId': 'testNodeId',
            'text': 'auxiliary data that was sent when triggering the session',
            'networkName': 'Orange',
            'countryName': 'NG'
        }

        response = self.client.post(callback_url, json.dumps(start_data), content_type='application/json')

        self.assertEqual(response.status_code, 200)

        self.assertEqual(response.json()['responseExitCode'], 400)
        self.assertEqual(response.json()['responseMessage'], "Channel not found.")

    def test_start_with_wrong_payload(self):
        callback_url = reverse('handlers.infobip_ussd_handler',
                               args=[self.channel.uuid, '13cc8b28afb86c69766531', 'start'])

        start_data = {
            'msisdn': '+2347030767143'
        }

        response = self.client.post(callback_url, start_data)

        self.assertEqual(response.status_code, 200)

        self.assertEqual(response.json()['responseExitCode'], 400)
        self.assertEqual(response.json()['responseMessage'], 'Error processing JSON data')

    def test_response(self):
        self.test_start()

        callback_url = reverse('handlers.infobip_ussd_handler',
                               args=[self.channel.uuid, '13cc8b28afb86c69766531', 'response'])

        start_data = {
            'msisdn': '+2347030767143',
            'imsi': '8796df56as657',
            'shortCode': self.starcode,
            'ussdNodeId': 'testNodeId',
            'text': '2',
            'networkName': 'Orange',
            'countryName': 'NG'
        }

        response = self.client.put(callback_url, json.dumps(start_data), content_type='application/json')

        self.assertEqual(response.status_code, 200)

        session = USSDSession.objects.get()

        self.assertEqual(session.status, USSDSession.IN_PROGRESS)
        self.assertTrue(session.runs.first().is_active)

        msgs = session.msg_set.order_by('id').all()

        self.assertEqual(msgs.count(), 4)
        self.assertEqual(msgs.last().text, u"Second menu\n1: Good\n2: Bad")
        self.assertEqual(msgs.last().status, WIRED)

        # let's make another response to close the session
        start_data['text'] = '1'
        response = self.client.put(callback_url, json.dumps(start_data), content_type='application/json')

        self.assertEqual(response.status_code, 200)

        session.refresh_from_db()

        # session is done
        self.assertEqual(session.status, USSDSession.COMPLETED)
        # run is inactive
        self.assertFalse(session.runs.first().is_active)

        msgs = session.msg_set.order_by('id').all()

        self.assertEqual(msgs.count(), 6)
        self.assertEqual(msgs.last().text, u"Closed with good")
        self.assertEqual(msgs.last().status, WIRED)

        self.assertEqual(response.json()['responseExitCode'], 200)
        self.assertEqual(response.json()['responseMessage'], '')
        self.assertEqual(response.json()['shouldClose'], 'true')
        self.assertEqual(response.json()['ussdMenu'], msgs.last().text)

    def test_response_with_no_session_closing_ruleset(self):
        self.test_start()

        callback_url = reverse('handlers.infobip_ussd_handler',
                               args=[self.channel.uuid, '13cc8b28afb86c69766531', 'response'])

        start_data = {
            'msisdn': '+2347030767143',
            'imsi': '8796df56as657',
            'shortCode': self.starcode,
            'ussdNodeId': 'testNodeId',
            'text': '1',
            'networkName': 'Orange',
            'countryName': 'NG'
        }

        response = self.client.put(callback_url, json.dumps(start_data), content_type='application/json')

        self.assertEqual(response.status_code, 200)

        session = USSDSession.objects.get()

        # session is done
        self.assertEqual(session.status, USSDSession.COMPLETED)
        # run is inactive
        self.assertFalse(session.runs.first().is_active)

        msgs = session.msg_set.order_by('id').all()

        self.assertEqual(msgs.count(), 4)
        self.assertEqual(msgs.last().text, '')
        self.assertEqual(msgs.last().status, WIRED)

        self.assertEqual(response.json()['responseExitCode'], 200)
        self.assertEqual(response.json()['responseMessage'], '')
        self.assertEqual(response.json()['shouldClose'], 'true')
        self.assertEqual(response.json()['ussdMenu'], msgs.last().text)

    def test_ended_normally(self):
        self.test_response_with_no_session_closing_ruleset()

        callback_url = reverse('handlers.infobip_ussd_handler',
                               args=[self.channel.uuid, '13cc8b28afb86c69766531', 'end'])

        start_data = {
            'reason': 'Session ended normally',
            'exitCode': '200',
            'networkName': 'Orange',
            'countryName': 'NG'
        }

        response = self.client.put(callback_url, json.dumps(start_data), content_type='application/json')

        self.assertEqual(response.status_code, 200)

        session = USSDSession.objects.get()

        # still completed and inactive
        self.assertEqual(session.status, USSDSession.COMPLETED)
        self.assertFalse(session.runs.first().is_active)

        msgs = session.msg_set.order_by('id').all()

        # there were no more messages sent
        self.assertEqual(msgs.count(), 4)

        self.assertEqual(response.json()['responseExitCode'], 200)
        self.assertEqual(response.json()['responseMessage'], '')

    def test_ended_with_error(self):
        self.test_start()

        callback_url = reverse('handlers.infobip_ussd_handler',
                               args=[self.channel.uuid, '13cc8b28afb86c69766531', 'end'])

        start_data = {
            'reason': 'timeout',
            'exitCode': '600',
            'networkName': 'Orange',
            'countryName': 'NG'
        }

        response = self.client.put(callback_url, json.dumps(start_data), content_type='application/json')

        self.assertEqual(response.status_code, 200)

        self.assertEqual(response.json()['responseExitCode'], 200)
        self.assertEqual(response.json()['responseMessage'], '')

        session = USSDSession.objects.get()

        self.assertEqual(session.status, USSDSession.INTERRUPTED)
        self.assertFalse(session.runs.first().is_active)
        self.assertIsInstance(session.ended_on, datetime)

        msgs = session.msg_set.order_by('id').all()

        # should be 3 messages all together with start the last one being the interrupt with no content
        self.assertEqual(msgs.count(), 3)
        self.assertEqual(msgs.last().text, '')
        self.assertEqual(msgs.last().status, HANDLED)

    def test_ended_with_error_session_not_found(self):
        callback_url = reverse('handlers.infobip_ussd_handler',
                               args=[self.channel.uuid, '13cc8b28afb86c69766531', 'end'])

        start_data = {
            'reason': 'timeout',
            'exitCode': '600',
            'networkName': 'Orange',
            'countryName': 'NG'
        }

        response = self.client.put(callback_url, json.dumps(start_data), content_type='application/json')

        self.assertEqual(response.status_code, 200)

        self.assertEqual(response.json()['responseExitCode'], 400)
        self.assertEqual(response.json()['responseMessage'], 'Session not found')

    def test_response_without_session(self):
        callback_url = reverse('handlers.infobip_ussd_handler',
                               args=[self.channel.uuid, '13cc8b28afb86c69766531', 'response'])

        start_data = {
            'msisdn': '+2347030767143',
            'imsi': '8796df56as657',
            'shortCode': self.starcode,
            'ussdNodeId': 'testNodeId',
            'text': '2',
            'networkName': 'Orange',
            'countryName': 'NG'
        }

        response = self.client.put(callback_url, json.dumps(start_data), content_type='application/json')

        self.assertEqual(response.status_code, 200)

        self.assertEqual(response.json()['responseExitCode'], 400)
        self.assertEqual(response.json()['responseMessage'], 'Session not found')
