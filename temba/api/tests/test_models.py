# -*- coding: utf-8 -*-
from __future__ import absolute_import, unicode_literals

import json

from datetime import timedelta
from django.conf import settings
from django.core.urlresolvers import reverse
from django.utils import timezone
from mock import patch
from temba.channels.models import SyncEvent
from temba.contacts.models import Contact, TEL_SCHEME
from temba.msgs.models import Broadcast, Call
from temba.orgs.models import ALL_EVENTS
from temba.tests import MockResponse, TembaTest
from urlparse import parse_qs
from ..models import WebHookEvent, WebHookResult, SMS_RECEIVED


class WebHookTest(TembaTest):

    def setUp(self):
        super(WebHookTest, self).setUp()
        self.joe = self.create_contact("Joe Blow", "0788123123")
        settings.SEND_WEBHOOKS = True

    def tearDown(self):
        super(WebHookTest, self).tearDown()
        settings.SEND_WEBHOOKS = False

    def assertStringContains(self, test, str):
        self.assertTrue(str.find(test) >= 0, "'%s' not found in '%s'" % (test, str))

    def setupChannel(self):
        org = self.channel.org
        org.webhook = u'{"url": "http://fake.com/webhook.php"}'
        org.webhook_events = ALL_EVENTS
        org.save()

        self.channel.address = "+250788123123"
        self.channel.save()

    def test_call_deliveries(self):
        self.setupChannel()
        now = timezone.now()
        call = Call.objects.create(org=self.org,
                                   channel=self.channel,
                                   contact=self.joe,
                                   call_type=Call.TYPE_IN_MISSED,
                                   time=now,
                                   created_by=self.admin,
                                   modified_by=self.admin)

        self.setupChannel()

        with patch('requests.Session.send') as mock:
            # clear out which events we listen for, we still shouldnt be notified though we have a webhook
            self.channel.org.webhook_events = 0
            self.channel.org.save()

            mock.return_value = MockResponse(200, "Hello World")

            # trigger an event, shouldnn't fire as we don't have a webhook
            WebHookEvent.trigger_call_event(call)
            self.assertFalse(WebHookEvent.objects.all())

        self.setupChannel()

        with patch('requests.Session.send') as mock:
            mock.return_value = MockResponse(200, "Hello World")

            # trigger an event
            WebHookEvent.trigger_call_event(call)
            event = WebHookEvent.objects.get()

            self.assertEquals('C', event.status)
            self.assertEquals(1, event.try_count)
            self.assertFalse(event.next_attempt)

            result = WebHookResult.objects.get()
            self.assertStringContains("Event delivered successfully", result.message)
            self.assertStringContains("not JSON", result.message)
            self.assertEquals(200, result.status_code)
            self.assertEquals("Hello World", result.body)

            self.assertTrue(mock.called)
            args = mock.call_args_list[0][0]
            prepared_request = args[0]
            self.assertEquals(self.channel.org.get_webhook_url(), prepared_request.url)

            data = parse_qs(prepared_request.body)
            self.assertEquals('+250788123123', data['phone'][0])
            self.assertEquals(call.pk, int(data['call'][0]))
            self.assertEquals(0, int(data['duration'][0]))
            self.assertEquals(call.call_type, data['event'][0])
            self.assertTrue('time' in data)
            self.assertEquals(self.channel.pk, int(data['channel'][0]))

    def test_alarm_deliveries(self):
        sync_event = SyncEvent.objects.create(channel=self.channel,
                                              power_source='AC',
                                              power_status='CHARGING',
                                              power_level=85,
                                              network_type='WIFI',
                                              pending_message_count=5,
                                              retry_message_count=4,
                                              incoming_command_count=0,
                                              created_by=self.admin,
                                              modified_by=self.admin)

        self.setupChannel()

        with patch('requests.Session.send') as mock:
            # clear out which events we listen for, we still shouldnt be notified though we have a webhook
            self.channel.org.webhook_events = 0
            self.channel.org.save()

            mock.return_value = MockResponse(200, "Hello World")

            # trigger an event, shouldnn't fire as we don't have a webhook
            WebHookEvent.trigger_channel_alarm(sync_event)
            self.assertFalse(WebHookEvent.objects.all())

        self.setupChannel()

        with patch('requests.Session.send') as mock:
            now = timezone.now()
            mock.return_value = MockResponse(200, "")

            # trigger an event
            WebHookEvent.trigger_channel_alarm(sync_event)
            event = WebHookEvent.objects.get()

            self.assertEquals('C', event.status)
            self.assertEquals(1, event.try_count)
            self.assertFalse(event.next_attempt)

            result = WebHookResult.objects.get()
            self.assertStringContains("Event delivered successfully", result.message)
            self.assertEquals(200, result.status_code)
            self.assertEquals("", result.body)

            self.assertTrue(mock.called)
            args = mock.call_args_list[0][0]
            prepared_request = args[0]
            self.assertEquals(self.channel.org.get_webhook_url(), prepared_request.url)

            data = parse_qs(prepared_request.body)
            self.assertEquals(self.channel.pk, int(data['channel'][0]))
            self.assertEquals(85, int(data['power_level'][0]))
            self.assertEquals('AC', data['power_source'][0])
            self.assertEquals('CHARGING', data['power_status'][0])
            self.assertEquals('WIFI', data['network_type'][0])
            self.assertEquals(5, int(data['pending_message_count'][0]))
            self.assertEquals(4, int(data['retry_message_count'][0]))

    def test_flow_event(self):
        self.setupChannel()

        org = self.channel.org
        org.save()

        from temba.flows.models import ActionSet, WebhookAction, Flow
        flow = self.create_flow()

        # replace our uuid of 4 with the right thing
        actionset = ActionSet.objects.get(x=4)
        actionset.set_actions_dict([WebhookAction(org.get_webhook_url()).as_json()])
        actionset.save()

        with patch('requests.Session.send') as mock:
            # run a user through this flow
            flow.start([], [self.joe])

            # have joe reply with mauve, which will put him in the other category that triggers the API Action
            sms = self.create_msg(contact=self.joe, direction='I', status='H', text="Mauve")

            mock.return_value = MockResponse(200, "{}")
            Flow.find_and_handle(sms)

            # should have one event created
            event = WebHookEvent.objects.get()

            self.assertEquals('C', event.status)
            self.assertEquals(1, event.try_count)
            self.assertFalse(event.next_attempt)

            result = WebHookResult.objects.get()
            self.assertStringContains("successfully", result.message)
            self.assertEquals(200, result.status_code)

            self.assertTrue(mock.called)

            args = mock.call_args_list[0][0]
            prepared_request = args[0]
            self.assertStringContains(self.channel.org.get_webhook_url(), prepared_request.url)

            data = parse_qs(prepared_request.body)
            self.assertEquals(self.channel.pk, int(data['channel'][0]))
            self.assertEquals(actionset.uuid, data['step'][0])
            self.assertEquals(flow.pk, int(data['flow'][0]))

            values = json.loads(data['values'][0])

            self.assertEquals('Other', values[0]['category']['base'])
            self.assertEquals('color', values[0]['label'])
            self.assertEquals('Mauve', values[0]['text'])
            self.assertTrue(values[0]['time'])
            self.assertTrue(data['time'])

    def test_event_deliveries(self):
        sms = self.create_msg(contact=self.joe, direction='I', status='H', text="I'm gonna pop some tags")

        with patch('requests.Session.send') as mock:
            now = timezone.now()
            mock.return_value = MockResponse(200, "Hello World")

            # trigger an event, shouldnn't fire as we don't have a webhook
            WebHookEvent.trigger_sms_event(SMS_RECEIVED, sms, now)
            self.assertFalse(WebHookEvent.objects.all())

        self.setupChannel()

        with patch('requests.Session.send') as mock:
            # clear out which events we listen for, we still shouldnt be notified though we have a webhook
            self.channel.org.webhook_events = 0
            self.channel.org.save()

            now = timezone.now()
            mock.return_value = MockResponse(200, "Hello World")

            # trigger an event, shouldnn't fire as we don't have a webhook
            WebHookEvent.trigger_sms_event(SMS_RECEIVED, sms, now)
            self.assertFalse(WebHookEvent.objects.all())

        self.setupChannel()

        with patch('requests.Session.send') as mock:
            # remove all the org users
            self.org.administrators.clear()
            self.org.editors.clear()
            self.org.viewers.clear()

            mock.return_value = MockResponse(200, "Hello World")

            # trigger an event
            WebHookEvent.trigger_sms_event(SMS_RECEIVED, sms, now)
            event = WebHookEvent.objects.get()

            self.assertEquals('F', event.status)
            self.assertEquals(0, event.try_count)
            self.assertFalse(event.next_attempt)

            result = WebHookResult.objects.get()
            self.assertStringContains("No active user", result.message)
            self.assertEquals(0, result.status_code)

            self.assertFalse(mock.called)

            # what if they send weird json back?
            WebHookEvent.objects.all().delete()
            WebHookResult.objects.all().delete()

        # add ad manager back in
        self.org.administrators.add(self.admin)
        self.admin.set_org(self.org)

        with patch('requests.Session.send') as mock:
            mock.return_value = MockResponse(200, "Hello World")

            # trigger an event
            WebHookEvent.trigger_sms_event(SMS_RECEIVED, sms, now)
            event = WebHookEvent.objects.get()

            self.assertEquals('C', event.status)
            self.assertEquals(1, event.try_count)
            self.assertFalse(event.next_attempt)

            result = WebHookResult.objects.get()
            self.assertStringContains("Event delivered successfully", result.message)
            self.assertStringContains("not JSON", result.message)
            self.assertEquals(200, result.status_code)

            self.assertTrue(mock.called)

            WebHookEvent.objects.all().delete()
            WebHookResult.objects.all().delete()

        with patch('requests.Session.send') as mock:
            # valid json, but not our format
            bad_json = '{ "thrift_shops": ["Goodwill", "Value Village"] }'
            mock.return_value = MockResponse(200, bad_json)

            next_retry_earliest = timezone.now() + timedelta(minutes=4)
            next_retry_latest = timezone.now() + timedelta(minutes=6)

            WebHookEvent.trigger_sms_event(SMS_RECEIVED, sms, now)
            event = WebHookEvent.objects.get()

            self.assertEquals('C', event.status)
            self.assertEquals(1, event.try_count)
            self.assertFalse(event.next_attempt)

            self.assertTrue(mock.called)

            result = WebHookResult.objects.get()
            self.assertStringContains("Event delivered successfully", result.message)
            self.assertStringContains("ignoring", result.message)
            self.assertEquals(200, result.status_code)
            self.assertEquals(bad_json, result.body)

            WebHookEvent.objects.all().delete()
            WebHookResult.objects.all().delete()

        with patch('requests.Session.send') as mock:
            mock.return_value = MockResponse(200, '{ "phone": "+250788123123", "text": "I am success" }')

            WebHookEvent.trigger_sms_event(SMS_RECEIVED, sms, now)
            event = WebHookEvent.objects.get()

            self.assertEquals('C', event.status)
            self.assertEquals(1, event.try_count)
            self.assertFalse(event.next_attempt)

            result = WebHookResult.objects.get()
            self.assertEquals(200, result.status_code)

            self.assertTrue(mock.called)

            broadcast = Broadcast.objects.get()
            contact = Contact.get_or_create(self.org, self.admin, name=None, urns=[(TEL_SCHEME, "+250788123123")],
                                            incoming_channel=self.channel)
            self.assertTrue("I am success", broadcast.text)
            self.assertTrue(contact, broadcast.contacts.all())

            self.assertTrue(mock.called)
            args = mock.call_args_list[0][0]
            prepared_request = args[0]
            self.assertEquals(self.org.get_webhook_url(), prepared_request.url)

            data = parse_qs(prepared_request.body)
            self.assertEquals(self.joe.get_urn(TEL_SCHEME).path, data['phone'][0])
            self.assertEquals(sms.pk, int(data['sms'][0]))
            self.assertEquals(self.channel.pk, int(data['channel'][0]))
            self.assertEquals(SMS_RECEIVED, data['event'][0])
            self.assertEquals("I'm gonna pop some tags", data['text'][0])
            self.assertTrue('time' in data)

            WebHookEvent.objects.all().delete()
            WebHookResult.objects.all().delete()

        with patch('requests.Session.send') as mock:
            mock.return_value = MockResponse(500, "I am error")

            next_attempt_earliest = timezone.now() + timedelta(minutes=4)
            next_attempt_latest = timezone.now() + timedelta(minutes=6)

            WebHookEvent.trigger_sms_event(SMS_RECEIVED, sms, now)
            event = WebHookEvent.objects.get()

            self.assertEquals('E', event.status)
            self.assertEquals(1, event.try_count)
            self.assertTrue(event.next_attempt)
            self.assertTrue(next_attempt_earliest < event.next_attempt and next_attempt_latest > event.next_attempt)

            result = WebHookResult.objects.get()
            self.assertStringContains("Error", result.message)
            self.assertEquals(500, result.status_code)
            self.assertEquals("I am error", result.body)

            # make sure things become failures after three retries
            event.try_count = 2
            event.deliver()
            event.save()

            self.assertTrue(mock.called)

            self.assertEquals('F', event.status)
            self.assertEquals(3, event.try_count)
            self.assertFalse(event.next_attempt)

            result = WebHookResult.objects.get()
            self.assertStringContains("Error", result.message)
            self.assertEquals(500, result.status_code)
            self.assertEquals("I am error", result.body)
            self.assertEquals("http://fake.com/webhook.php", result.url)
            self.assertTrue(result.data.find("pop+some+tags") > 0)

            # check out our api log
            response = self.client.get(reverse('api.log'))
            self.assertRedirect(response, reverse('users.user_login'))

            response = self.client.get(reverse('api.log_read', args=[event.pk]))
            self.assertRedirect(response, reverse('users.user_login'))

            WebHookEvent.objects.all().delete()
            WebHookResult.objects.all().delete()

        # add a webhook header to the org
        self.channel.org.webhook = u'{"url": "http://fake.com/webhook.php", "headers": {"X-My-Header": "foobar", "Authorization": "Authorization: Basic QWxhZGRpbjpvcGVuIHNlc2FtZQ=="}, "method": "POST"}'
        self.channel.org.save()

        # check that our webhook settings have saved
        self.assertEquals('http://fake.com/webhook.php', self.channel.org.get_webhook_url())
        self.assertDictEqual({'X-My-Header': 'foobar', 'Authorization': 'Authorization: Basic QWxhZGRpbjpvcGVuIHNlc2FtZQ=='}, self.channel.org.get_webhook_headers())

        with patch('requests.Session.send') as mock:
            mock.return_value = MockResponse(200, "Boom")
            WebHookEvent.trigger_sms_event(SMS_RECEIVED, sms, now)
            event = WebHookEvent.objects.get()

            result = WebHookResult.objects.get()
            # both headers should be in the json-encoded url string
            self.assertStringContains('X-My-Header: foobar', result.request)
            self.assertStringContains('Authorization: Authorization: Basic QWxhZGRpbjpvcGVuIHNlc2FtZQ==', result.request)

    def test_webhook(self):
        response = self.client.get(reverse('api.webhook'))
        self.assertEquals(200, response.status_code)
        self.assertContains(response, "Simulator")

        response = self.client.get(reverse('api.webhook_simulator'))
        self.assertEquals(200, response.status_code)
        self.assertContains(response, "Log in")

        self.login(self.admin)
        response = self.client.get(reverse('api.webhook_simulator'))
        self.assertEquals(200, response.status_code)
        self.assertNotContains(response, "Log in")

    def test_tunnel(self):
        response = self.client.post(reverse('api.webhook_tunnel'), dict())
        self.assertEquals(302, response.status_code)

        self.login(self.non_org_user)

        with patch('requests.post') as mock:
            mock.return_value = MockResponse(200, '{ "phone": "+250788123123", "text": "I am success" }')

            response = self.client.post(reverse('api.webhook_tunnel'),
                                        dict(url="http://webhook.url/", data="phone=250788383383&values=foo&bogus=2"))
            self.assertEquals(200, response.status_code)
            self.assertContains(response, "I am success")
            self.assertTrue('values' in mock.call_args[1]['data'])
            self.assertTrue('phone' in mock.call_args[1]['data'])
            self.assertFalse('bogus' in mock.call_args[1]['data'])

            response = self.client.post(reverse('api.webhook_tunnel'), dict())
            self.assertEquals(400, response.status_code)
            self.assertTrue(response.content.find("Must include") >= 0)
