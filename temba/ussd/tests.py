import json
import uuid

from datetime import datetime
from mock import patch

from django.core.urlresolvers import reverse
from django.conf import settings
from django.test import TestCase
from django.utils import timezone
from django_redis import get_redis_connection

from temba.channels.models import Channel
from temba.msgs.models import WIRED, MSG_SENT_KEY, SENT, Msg, INCOMING, ERRORED
from temba.tests import TembaTest, MockResponse
from temba.triggers.models import Trigger
from temba.utils import dict_to_struct

from .models import USSDSession


class USSDSessionTest(TembaTest):
    # def setUp(self):
    #     super(USSDSessionTest, self).setUp()
    #
    #     self.channel.delete()
    #     self.channel = Channel.create(self.org, self.user, 'RW', Channel.TYPE_VUMI_USSD, None, '+250788123123',
    #                                   config=dict(account_key='vumi-key', access_token='vumi-token',
    #                                               conversation_key='key'),
    #                                   uuid='00000000-0000-0000-0000-000000001234')

    @patch('temba.ussd.models.USSDSession.start_session_async')
    def test_pull_async_trigger_start(self, start_session_async):
        flow = self.get_flow('ussd_example')

        startcode = "*113#"

        trigger, _ = Trigger.objects.get_or_create(channel=self.channel, keyword=startcode, flow=flow,
                                                   created_by=self.user, modified_by=self.user, org=self.org,
                                                   trigger_type=Trigger.TYPE_USSD_PULL)

        # handle a message that has an unmatched starcode
        session = USSDSession.handle_incoming(channel=self.channel, urn="+329732973", content="None",
                                              status=USSDSession.TRIGGERED, date=timezone.now(), external_id="1234",
                                              message_id="1111211", starcode="*111#")

        # check if session was started and created
        self.assertFalse(session)
        self.assertFalse(start_session_async.called)

        session = USSDSession.handle_incoming(channel=self.channel, urn="+329732973", content="None",
                                              status=USSDSession.TRIGGERED, date=timezone.now(), external_id="1235",
                                              message_id="1111131", starcode=startcode)

        # session should have started now
        self.assertTrue(start_session_async.called)
        self.assertEqual(start_session_async.call_count, 1)

        # check session properties
        self.assertEqual(session.status, USSDSession.TRIGGERED)
        self.assertEqual(session.external_id, "1235")
        self.assertEqual(session.message_id, "1111131")

    def test_push_async_start(self):
        pass

    # @patch('temba.msgs.models.Msg.create_incoming')
    # def test_async_content_handling(self):
    #     args, kwargs = create_incoming.call_args
    #     self.assertEqual(kwargs['status'], INTERRUPTED)


    @patch('temba.msgs.models.Msg.create_incoming')
    def test_async_interrupt_handling(self, create_incoming):
        self.assertTrue(create_incoming.called)
        self.assertEqual(create_incoming.call_count, 1)

        args, kwargs = create_incoming.call_args
        self.assertEqual(kwargs['status'], ERRORED)


class VumiUssdTest(TembaTest):
    def setUp(self):
        super(VumiUssdTest, self).setUp()

        self.channel.delete()
        self.channel = Channel.create(self.org, self.user, 'RW', Channel.TYPE_VUMI_USSD, None, '+250788123123',
                                      config=dict(account_key='vumi-key', access_token='vumi-token',
                                                  conversation_key='key'),
                                      uuid='00000000-0000-0000-0000-000000001234')

    def test_send(self):
        joe = self.create_contact("Joe", "+250788383383")
        self.create_group("Reporters", [joe])
        msg = joe.send("Test message", self.admin, trigger_send=False)

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

    def test_ack(self):
        joe = self.create_contact("Joe", "+250788383383")
        self.create_group("Reporters", [joe])
        msg = joe.send("Test message", self.admin, trigger_send=False)

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
                    "event_id": unicode(uuid.uuid4()),
                    "sent_message_id": unicode(uuid.uuid4()),
                    "helper_metadata": {},
                    "routing_metadata": {},
                    "message_version": "20110921",
                    "timestamp": unicode(timezone.now()),
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
        msg = joe.send("Test message", self.admin, trigger_send=False)

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
                    "event_id": unicode(uuid.uuid4()),
                    "timestamp": unicode(timezone.now()),
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

        data = dict(timestamp="2016-04-18 03:54:20.570618", message_id="123456", from_addr="+250788383383",
                    content="Hello from Vumi", to_addr="*113#", transport_type='ussd')

        response = self.client.post(callback_url, json.dumps(data), content_type="application/json")

        self.assertEqual(response.status_code, 200)

        msg = Msg.objects.get()
        self.assertEquals(INCOMING, msg.direction)
        self.assertEquals(self.org, msg.org)
        self.assertEquals(self.channel, msg.channel)
        self.assertEquals("Hello from Vumi", msg.text)
        self.assertEquals('123456', msg.external_id)

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

        args, kwargs = create_incoming.call_args
        self.assertEqual(kwargs['status'], ERRORED)

        session = USSDSession.objects.get()
        self.assertIsInstance(session.ended_on, datetime)
        self.assertEqual(session.status, USSDSession.INTERRUPTED)
