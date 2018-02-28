# -*- coding: utf-8 -*-
from __future__ import absolute_import, division, print_function, unicode_literals

import json

from datetime import datetime, timedelta
from mock import patch

from django.core.urlresolvers import reverse
from django.conf import settings
from django.utils import timezone

from temba.channels.models import Channel
from temba.channels.tests import JunebugTestMixin
from temba.contacts.models import TEL_SCHEME
from temba.flows.models import FlowRun
from temba.msgs.models import WIRED, SENT, Msg, INCOMING, OUTGOING, USSD, DELIVERED, FAILED, HANDLED
from temba.tests import TembaTest, MockResponse
from temba.triggers.models import Trigger

from .models import USSDSession


class USSDSessionTest(TembaTest):

    def setUp(self):
        super(USSDSessionTest, self).setUp()

        self.channel.delete()
        self.channel = Channel.create(self.org, self.user, 'RW', 'JNU', None, '+250788123123',
                                      role=Channel.ROLE_USSD + Channel.DEFAULT_ROLE,
                                      uuid='00000000-0000-0000-0000-000000001234')

    def test_pull_async_trigger_start(self):
        flow = self.get_flow('ussd_example')

        starcode = "*113#"

        trigger, _ = Trigger.objects.get_or_create(channel=self.channel, keyword=starcode, flow=flow,
                                                   created_by=self.user, modified_by=self.user, org=self.org,
                                                   trigger_type=Trigger.TYPE_USSD_PULL)

        # handle a message that has an unmatched wrong keyword
        session = USSDSession.handle_incoming(channel=self.channel, urn="+329732973", content="None",
                                              status=USSDSession.TRIGGERED, date=timezone.now(), external_id="1234",
                                              message_id="1111211", starcode="wrongkeyword")

        # check if session was started and created
        self.assertFalse(session)

        # handle a message that has an unmatched starcode
        session = USSDSession.handle_incoming(channel=self.channel, urn="+329732973", content="None",
                                              status=USSDSession.TRIGGERED, date=timezone.now(), external_id="1234",
                                              message_id="1111211", starcode="*111#")

        # check if session was started and created
        self.assertFalse(session)

        session = USSDSession.handle_incoming(channel=self.channel, urn="+329732973", content="None",
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
        session = USSDSession.handle_incoming(channel=self.channel, urn="+250788383383", content="1",
                                              date=timezone.now(), external_id="21345", message_id="123")

        # same session is modified
        self.assertEqual(USSDSession.objects.count(), 1)

        # new status
        self.assertEqual(session.status, USSDSession.IN_PROGRESS)

        # there should be 3 messages
        self.assertEqual(Msg.objects.count(), 3)

        # lets check the steps and incoming and outgoing messages
        # first step has 1 outgoing and the answer
        msgs = flow.get_steps().first().messages.order_by('id')
        self.assertEqual(len(msgs), 2)
        self.assertEqual(msgs[0].direction, OUTGOING)
        self.assertEqual(msgs[0].text, u'What would you like to read about?')
        self.assertEqual(msgs[1].direction, INCOMING)
        self.assertEqual(msgs[1].text, u'1')

        # second step sent out the next message and waits for response
        msgs = flow.get_steps().last().messages.order_by('id')
        self.assertEqual(len(msgs), 1)
        self.assertEqual(msgs[0].direction, OUTGOING)
        self.assertEqual(msgs[0].text, u'Thank you!')

    def test_expiration(self):
        # start off a PUSH session
        self.test_push_async_start()
        run = FlowRun.objects.last()
        run.expire()

        # we should be marked as interrupted now
        self.assertEqual(USSDSession.INTERRUPTED, run.connection.status)

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
        session = USSDSession.handle_incoming(channel=self.channel, urn="+250788383383", status=USSDSession.INTERRUPTED,
                                              date=timezone.now(), external_id="21345")

        # same session is modified
        self.assertEqual(USSDSession.objects.count(), 1)

        # session is modified with proper status and end date
        self.assertEqual(session.status, USSDSession.INTERRUPTED)
        self.assertIsInstance(session.ended_on, datetime)
        self.assertEqual(session.external_id, "21345")

    def test_end_with_menu_no_destination(self):
        flow = self.get_flow('ussd_session_end')
        contact = self.create_contact("Joe", "+250788383383")
        flow.start([], [contact])

        # choose option with no destination connected
        session = USSDSession.handle_incoming(channel=self.channel, urn="+250788383383", content="4",
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
        session = USSDSession.handle_incoming(channel=self.channel, urn="+250788383383", content="1",
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
        session = USSDSession.handle_incoming(channel=self.channel, urn="+250788383383", content="test",
                                              date=timezone.now(), external_id="21345")

        # the session shouldn't be marked as ending
        self.assertEqual(session.status, USSDSession.IN_PROGRESS)
        self.assertIsNone(session.ended_on)
        self.assertEqual(session.external_id, "21345")

        # choose option with destination connected to actions (add to groups and set language later)
        session = USSDSession.handle_incoming(channel=self.channel, urn="+250788383383", content="2",
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
        session = USSDSession.handle_incoming(channel=self.channel, urn="+250788383383", content="3",
                                              date=timezone.now(), external_id="21345")

        # the session shouldn't be marked as ending
        self.assertEqual(session.status, USSDSession.IN_PROGRESS)
        self.assertIsNone(session.ended_on)
        self.assertEqual(session.external_id, "21345")

        # now we are at the ussd response, choose an option here
        session = USSDSession.handle_incoming(channel=self.channel, urn="+250788383383", content="3",
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
        session = USSDSession.handle_incoming(channel=self.channel, urn="+250788383383", content="3",
                                              date=timezone.now(), external_id="21345")

        # the session shouldn't be marked as ending
        self.assertEqual(session.status, USSDSession.IN_PROGRESS)
        self.assertIsNone(session.ended_on)
        self.assertEqual(session.external_id, "21345")

        # choose the Maybe option which leads to a USSD End with message action
        session = USSDSession.handle_incoming(channel=self.channel, urn="+250788383383", content="Maybe",
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
        session = USSDSession.handle_incoming(channel=self.channel, urn="+250788383383", content="2",
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
        session = USSDSession.handle_incoming(channel=self.channel, urn="+250788383383", content="6",
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
        session = USSDSession.handle_incoming(channel=self.channel, urn="+250788383383", content="5",
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
        session = USSDSession.handle_incoming(channel=self.channel, urn="+250788383383", content="8",
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
        session = USSDSession.handle_incoming(channel=self.channel, urn="+250788383383", content="9",
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
        session = USSDSession.handle_incoming(channel=self.channel, urn="+250788383383", content="7",
                                              date=timezone.now(), external_id="21345")

        # there should be a last message with empty message
        msgs = Msg.objects.filter(direction='O').order_by('id')

        self.assertEqual(msgs.last().text, '')

        session.refresh_from_db()

        self.assertEqual(session.status, USSDSession.ENDING)
        self.assertIsNone(session.ended_on)
        self.assertEqual(session.external_id, "21345")


class JunebugUSSDTest(JunebugTestMixin, TembaTest):

    def setUp(self):
        super(JunebugUSSDTest, self).setUp()

        flow = self.get_flow('ussd_example')
        self.starcode = "*113#"

        self.channel.delete()
        self.channel = Channel.create(
            self.org, self.user, 'RW', 'JNU', None, '1234',
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
            self.assertEqual(200, response.status_code)
            sms = Msg.objects.get(pk=sms.id)
            self.assertEqual(assert_status, sms.status)

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
        self.assertEqual(data["from"], outbound_msg.contact.get_urn(TEL_SCHEME).path)
        self.assertEqual(outbound_msg.response_to, inbound_msg)
        self.assertEqual(outbound_msg.connection.status, USSDSession.TRIGGERED)
        self.assertEqual(inbound_msg.direction, INCOMING)
        self.assertEqual(inbound_msg.status, HANDLED)

    def test_receive_with_session_id(self):
        from temba.ussd.models import USSDSession

        data = self.mk_ussd_msg(content="événement", session_id='session-id', to=self.starcode)
        callback_url = reverse('handlers.junebug_handler',
                               args=['inbound', self.channel.uuid])
        self.client.post(callback_url, json.dumps(data), content_type='application/json')

        # load our message
        inbound_msg, outbound_msg = Msg.objects.all().order_by('pk')
        self.assertEqual(outbound_msg.connection.status, USSDSession.TRIGGERED)
        self.assertEqual(outbound_msg.connection.external_id, 'session-id')
        self.assertEqual(inbound_msg.connection.external_id, 'session-id')

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
                self.assertEqual("07033084-5cfd-4812-90a4-e4d24ffb6e3d", msg.external_id)
                self.assertEqual(msg.connection.status, USSDSession.INITIATED)

                # reply and choose an option that doesn't have any destination thus needs to close the session
                USSDSession.handle_incoming(channel=self.channel, urn="+250788383383", content="4",
                                            date=timezone.now(), external_id="21345", message_id='jn-message-id')
                # our outgoing message
                msg = Msg.objects.filter(direction='O').order_by('id').last()
                self.assertEqual(WIRED, msg.status)
                self.assertEqual(msg.direction, 'O')
                self.assertTrue(msg.sent_on)
                self.assertEqual("07033084-5cfd-4812-90a4-e4d24ffb6e3d", msg.external_id)
                self.assertEqual("jn-message-id", msg.response_to.external_id)

                self.assertEqual(msg.connection.status, USSDSession.COMPLETED)
                self.assertTrue(isinstance(msg.connection.get_duration(), timedelta))

                self.assertEqual(2, mock.call_count)

                # first outbound (session continued)
                call = mock.call_args_list[0]
                (args, kwargs) = call
                payload = kwargs['json']
                self.assertIsNone(payload.get('reply_to'))
                self.assertEqual(payload.get('to'), "+250788383383")
                self.assertEqual(payload['channel_data'], {
                    'continue_session': True
                })

                # second outbound (session ended)
                call = mock.call_args_list[1]
                (args, kwargs) = call
                payload = kwargs['json']
                self.assertEqual(payload['reply_to'], 'jn-message-id')
                self.assertEqual(payload.get('to'), None)
                self.assertEqual(payload['channel_data'], {
                    'continue_session': False
                })

                self.clear_cache()
        finally:
            settings.SEND_MESSAGES = False
