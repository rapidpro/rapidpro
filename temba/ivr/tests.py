# -*- coding: utf-8 -*-
from __future__ import absolute_import, division, print_function, unicode_literals

import json
import nexmo
import os
import re

from datetime import timedelta
from django.conf import settings
from django.contrib.auth.models import Group
from django.core.files import File
from django.core.urlresolvers import reverse
from django.utils import timezone
from django.utils.encoding import force_text
from mock import patch, MagicMock
from platform import python_version
from temba.channels.models import Channel, ChannelLog, ChannelSession
from temba.contacts.models import Contact
from temba.flows.models import Flow, FlowRun, ActionLog, FlowStep, FlowRevision
from temba.msgs.models import Msg, IVR, OUTGOING, PENDING
from temba.orgs.models import get_current_export_version
from temba.tests import FlowFileTest, MockResponse
from temba.tests.twilio import MockTwilioClient, MockRequestValidator
from six.moves.urllib.parse import urlparse
from .clients import IVRException
from .models import IVRCall


class IVRTests(FlowFileTest):

    def setUp(self):
        super(IVRTests, self).setUp()
        settings.SEND_CALLS = True

        # configure our account to be IVR enabled
        self.channel.channel_type = 'T'
        self.channel.role = Channel.ROLE_CALL + Channel.ROLE_ANSWER + Channel.ROLE_SEND
        self.channel.save()
        self.admin.groups.add(Group.objects.get(name="Beta"))
        self.login(self.admin)

    def tearDown(self):
        super(IVRTests, self).tearDown()
        settings.SEND_CALLS = False

    @patch('nexmo.Client.create_application')
    @patch('nexmo.Client.create_call')
    @patch('nexmo.Client.update_call')
    @patch('temba.ivr.clients.TwilioClient', MockTwilioClient)
    @patch('twilio.util.RequestValidator', MockRequestValidator)
    def test_preferred_channel(self, mock_update_call, mock_create_call, mock_create_application):
        mock_create_application.return_value = dict(id='app-id', keys=dict(private_key='private-key'))
        mock_create_call.return_value = dict(uuid='12345')
        mock_update_call.return_value = dict(uuid='12345')

        flow = self.get_flow('call_me_maybe')

        # start our flow
        contact = self.create_contact('Chuck D', number='+13603621737')
        flow.start([], [contact])

        call = IVRCall.objects.get()
        self.assertEqual(IVRCall.PENDING, call.status)

        # call should be on a Twilio channel since that's all we have
        self.assertEqual('T', call.channel.channel_type)

        # connect Nexmo instead
        self.org.connect_nexmo('123', '456', self.admin)
        self.org.save()

        # manually create a Nexmo channel
        nexmo = Channel.create(self.org, self.user, 'RW', 'NX', role=Channel.ROLE_CALL + Channel.ROLE_ANSWER + Channel.ROLE_SEND,
                               name="Nexmo Channel", address="+250785551215")

        # set the preferred channel on this contact to Twilio
        contact.set_preferred_channel(self.channel)

        # restart the flow
        flow.start([], [contact], restart_participants=True)

        call = IVRCall.objects.all().last()
        self.assertEqual(IVRCall.PENDING, call.status)
        self.assertEqual('T', call.channel.channel_type)

        # switch back to Nexmo being the preferred channel
        contact.set_preferred_channel(nexmo)

        # clear open calls and runs
        IVRCall.objects.all().delete()
        FlowRun.objects.all().delete()

        # restart the flow
        flow.start([], [contact], restart_participants=True)

        call = IVRCall.objects.all().last()
        self.assertEqual(IVRCall.PENDING, call.status)
        self.assertEqual('NX', call.channel.channel_type)

    @patch('temba.ivr.clients.TwilioClient', MockTwilioClient)
    @patch('twilio.util.RequestValidator', MockRequestValidator)
    def test_twilio_failed_auth(self):

        def create(self, to=None, from_=None, url=None, status_callback=None):
            from twilio import TwilioRestException
            raise TwilioRestException(403, 'http://twilio.com', code=20003)
        MockTwilioClient.MockCalls.create = create

        # connect it and check our client is configured
        self.org.connect_twilio("TEST_SID", "TEST_TOKEN", self.admin)
        self.org.save()

        # import an ivr flow
        self.import_file('call_me_maybe')
        flow = Flow.objects.filter(name='Call me maybe').first()

        user_settings = self.admin.get_settings()
        user_settings.tel = '+18005551212'
        user_settings.save()

        test_contact = Contact.get_test_contact(self.admin)
        Contact.set_simulation(True)
        flow.start([], [test_contact])

        log = ActionLog.objects.all().order_by('-pk').first()
        self.assertEqual(log.text, 'Call ended. Could not authenticate with your Twilio account. '
                                   'Check your token and try again.')

    @patch('twilio.util.RequestValidator', MockRequestValidator)
    def test_call_logging(self):
        # create our ivr setup
        self.org.connect_twilio("TEST_SID", "TEST_TOKEN", self.admin)
        self.org.save()

        with patch('twilio.rest.resources.base.make_request') as mock:
            mock.return_value = MockResponse(200, '{"sid": "CAa346467ca321c71dbd5e12f627deb854"}')
            self.import_file('capture_recording')
            flow = Flow.objects.filter(name='Capture Recording').first()

            # start our flow
            contact = self.create_contact('Chuck D', number='+13603621737')
            flow.start([], [contact])

            # should have a channel log for starting the call
            log = ChannelLog.objects.get(is_error=False)
            self.assertEqual(log.response, mock.return_value.text)

            # expire our flow, causing the call to hang up
            mock.return_value = MockResponse(200, '{"sid": "CAa346467ca321c71dbd5e12f627deb855"}')
            run = flow.runs.get()
            run.expire()

            # two channel logs now
            log = ChannelLog.objects.exclude(id=log.id).get(is_error=False)
            self.assertEqual(log.response, mock.return_value.text)

    @patch('temba.ivr.clients.TwilioClient', MockTwilioClient)
    @patch('twilio.util.RequestValidator', MockRequestValidator)
    def test_disable_calls(self):
        with self.settings(SEND_CALLS=False):
            self.org.connect_twilio("TEST_SID", "TEST_TOKEN", self.admin)
            self.org.save()

            with patch('twilio.rest.resources.calls.Calls.create') as mock:
                self.import_file('call_me_maybe')
                flow = Flow.objects.filter(name='Call me maybe').first()

                # start our flow
                contact = self.create_contact('Chuck D', number='+13603621737')
                flow.start([], [contact])

                self.assertEqual(mock.call_count, 0)
                call = IVRCall.objects.get()
                self.assertEqual(IVRCall.FAILED, call.status)

    @patch('temba.ivr.clients.TwilioClient', MockTwilioClient)
    @patch('twilio.util.RequestValidator', MockRequestValidator)
    def test_bogus_call(self):
        # create our ivr setup
        self.org.connect_twilio("TEST_SID", "TEST_TOKEN", self.admin)
        self.org.save()
        self.import_file('capture_recording')

        # post to a bogus call id
        post_data = dict(CallSid='CallSid', CallStatus='in-progress', CallDuration=20)
        response = self.client.post(reverse('ivr.ivrcall_handle', args=[999999999]), post_data)
        self.assertEqual(404, response.status_code)

        # start a real call
        flow = Flow.objects.filter(name='Capture Recording').first()
        contact = self.create_contact('Chuck D', number='+13603621737')
        flow.start([], [contact])
        call = IVRCall.objects.filter(direction=IVRCall.OUTGOING).first()

        # now trigger a hangup
        post_data = dict(CallSid='CallSid', CallStatus='in-progress', CallDuration=20, hangup=1)
        response = self.client.post(reverse('ivr.ivrcall_handle', args=[call.pk]), post_data)
        self.assertEqual(200, response.status_code)

    @patch('temba.ivr.clients.TwilioClient', MockTwilioClient)
    @patch('twilio.util.RequestValidator', MockRequestValidator)
    def test_ivr_recording(self):

        # create our ivr setup
        self.org.connect_twilio("TEST_SID", "TEST_TOKEN", self.admin)
        self.org.save()
        self.import_file('capture_recording')
        flow = Flow.objects.filter(name='Capture Recording').first()

        # start our flow
        contact = self.create_contact('Chuck D', number='+13603621737')
        flow.start([], [contact])
        call = IVRCall.objects.get(direction=IVRCall.OUTGOING)

        # after a call is picked up, twilio will call back to our server
        post_data = dict(CallSid='CallSid', CallStatus='in-progress', CallDuration=20)
        response = self.client.post(reverse('ivr.ivrcall_handle', args=[call.pk]), post_data)
        self.assertContains(response, '<Say>Please make a recording after the tone.</Say>')
        self.assertEqual(response._headers['content-type'][1], 'text/xml; charset=utf-8')

        self.assertEqual(ChannelLog.objects.all().count(), 1)
        channel_log = ChannelLog.objects.last()
        self.assertEqual(channel_log.connection.id, call.id)
        self.assertEqual(channel_log.description, "Incoming request for call")

        # simulate the caller making a recording and then hanging up, first they'll give us the
        # recording (they give us a call status of completed at the same time)
        from temba.tests import MockResponse
        with patch('requests.get') as response:
            mock1 = MockResponse(404, 'No such file')
            mock2 = MockResponse(200, 'Fake Recording Bits')
            mock2.add_header('Content-Type', 'audio/x-wav')
            response.side_effect = (mock1, mock2)

            self.client.post(reverse('ivr.ivrcall_handle', args=[call.pk]),
                             dict(CallStatus='completed',
                                  Digits='hangup',
                                  RecordingUrl='http://api.twilio.com/ASID/Recordings/SID',
                                  RecordingSid='FAKESID'))

        self.assertEqual(ChannelLog.objects.all().count(), 2)
        channel_log = ChannelLog.objects.last()
        self.assertEqual(channel_log.connection.id, call.id)
        self.assertEqual(channel_log.description, "Incoming request for call")

        # we should have captured the recording, and ended the call
        call = IVRCall.objects.get(pk=call.pk)
        self.assertEqual(IVRCall.COMPLETED, call.status)

        # twilio will also send us a final completion message with the call duration (status of completed again)
        self.client.post(reverse('ivr.ivrcall_handle', args=[call.pk]),
                         dict(CallStatus='completed', CallDuration='15'))

        self.assertEqual(ChannelLog.objects.all().count(), 3)
        channel_log = ChannelLog.objects.last()
        self.assertEqual(channel_log.connection.id, call.id)
        self.assertEqual(channel_log.description, "Updated call status")

        call = IVRCall.objects.get(pk=call.pk)
        self.assertEqual(IVRCall.COMPLETED, call.status)
        self.assertEqual(15, call.duration)

        messages = Msg.objects.filter(msg_type=IVR).order_by('pk')
        self.assertEqual(4, messages.count())
        self.assertEqual(4, self.org.get_credits_used())

        # we should have played a recording from the contact back to them
        outbound_msg = messages[1]
        self.assertTrue(outbound_msg.attachments[0].startswith('audio/x-wav:https://'))
        self.assertTrue(outbound_msg.attachments[0].endswith('.wav'))
        self.assertTrue(outbound_msg.text.startswith('https://'))
        self.assertTrue(outbound_msg.text.endswith('.wav'))

        media_msg = messages[2]
        self.assertTrue(media_msg.attachments[0].startswith('audio/x-wav:https://'))
        self.assertTrue(media_msg.attachments[0].endswith('.wav'))
        self.assertEqual('Played contact recording', media_msg.text)

        (host, directory, filename) = media_msg.attachments[0].rsplit('/', 2)
        recording = '%s/%s/%s/media/%s/%s' % (settings.MEDIA_ROOT, settings.STORAGE_ROOT_DIR,
                                              self.org.pk, directory, filename)
        self.assertTrue(os.path.isfile(recording))

        from temba.flows.models import FlowStep
        steps = FlowStep.objects.all()
        self.assertEqual(4, steps.count())

        # each of our steps should have exactly one message
        for step in steps:
            self.assertEqual(1, step.messages.all().count(), msg="Step '%s' does not have exactly one message" % step)

        # each message should have exactly one step
        for msg in messages:
            self.assertEqual(1, msg.steps.all().count(), msg="Message '%s' is not attached to exactly one step" % msg.text)

        # run same flow in simulator
        Contact.set_simulation(True)
        test_contact = Contact.get_test_contact(self.admin)
        flow.start([], [test_contact])
        test_call = IVRCall.objects.get(direction=IVRCall.OUTGOING, contact=test_contact)

        self.client.post(reverse('ivr.ivrcall_handle', args=[test_call.id]), {
            'CallSid': 'CallSid',
            'CallStatus': 'in-progress',
            'CallDuration': 20
        })

        with patch('requests.get') as response:
            response.side_effect = (
                MockResponse(404, 'No such file'),
                MockResponse(200, 'Fake Recording Bits', {'Content-Type': 'audio/x-wav'})
            )
            self.client.post(reverse('ivr.ivrcall_handle', args=[test_call.id]), {
                'CallStatus': 'completed',
                'Digits': 'hangup',
                'RecordingUrl': 'http://api.twilio.com/ASID/Recordings/SID',
                'RecordingSid': 'FAKESID'
            })

        out_msg = Msg.objects.get(contact=test_contact, direction='O', msg_type='V', text__contains='Played contact recording')
        recording_url = out_msg.attachments[0].split(':', 1)[1]

        self.assertTrue(ActionLog.objects.filter(text="Played recording at &quot;%s&quot;" % recording_url).exists())

    @patch('jwt.encode')
    @patch('nexmo.Client.create_application')
    @patch('requests.post')
    def test_ivr_recording_with_nexmo(self, mock_create_call, mock_create_application, mock_jwt):
        mock_create_application.return_value = dict(id='app-id', keys=dict(private_key='private-key'))
        mock_create_call.return_value = MockResponse(200, json.dumps(dict(uuid='12345')))
        mock_jwt.return_value = b'Encoded data'

        # connect Nexmo
        self.org.connect_nexmo('123', '456', self.admin)
        self.org.save()

        self.channel.channel_type = 'NX'
        self.channel.save()

        self.import_file('capture_recording')
        flow = Flow.objects.filter(name='Capture Recording').first()

        # start our flow
        contact = self.create_contact('Chuck D', number='+13603621737')
        flow.start([], [contact])
        call = IVRCall.objects.filter(direction=IVRCall.OUTGOING).first()

        callback_url = reverse('ivr.ivrcall_handle', args=[call.pk])

        # after a call is picked up, nexmo will send a get call back to our server
        response = self.client.post(callback_url, content_type='application/json',
                                    data=json.dumps(dict(status='ringing', duration=0)))

        self.assertEqual(ChannelLog.objects.all().count(), 2)
        channel_log = ChannelLog.objects.first()
        self.assertEqual(channel_log.connection.id, call.id)
        self.assertEqual(channel_log.description, "Started call")

        channel_log = ChannelLog.objects.last()
        self.assertEqual(channel_log.connection.id, call.id)
        self.assertEqual(channel_log.description, "Incoming request for call")

        # we have a talk action
        self.assertContains(response, '"action": "talk",')
        self.assertContains(response, '"text": "Please make a recording after the tone."')

        # we have a record action
        self.assertContains(response, '"action": "record"')
        self.assertContains(response, '"eventUrl": ["https://%s%s"]' % (self.channel.callback_domain, callback_url))

        # we have an input to redirect so we save the recording
        # hack to make the recording look synchronous for our flows
        self.assertContains(response, '"action": "input"')
        self.assertContains(response, '"eventUrl": ["https://%s%s?save_media=1"]' % (self.channel.callback_domain, callback_url))

        # any request with has_event params return empty content response
        response = self.client.get("%s?has_event=1" % callback_url)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json().get('description'), 'Updated call status')
        self.assertEqual(response.json().get('call').get('status'), 'Ringing')

        with patch('temba.utils.nexmo.NexmoClient.download_recording') as mock_download_recording:
            mock_download_recording.return_value = MockResponse(200, "SOUND_BITS",
                                                                headers={"Content-Type": "audio/x-wav"})

            # async callback to tell us the recording url
            response = self.client.post(callback_url, content_type='application/json',
                                        data=json.dumps(dict(recording_url='http://example.com/allo.wav')))

            self.assertEqual(response.json().get('message'), 'Saved media url')
            self.assertEqual(ChannelLog.objects.all().count(), 4)
            channel_log = ChannelLog.objects.last()
            self.assertEqual(channel_log.connection.id, call.id)
            self.assertEqual(channel_log.description, "Saved media url")

            # hack input call back to tell us to save the recording and an empty input submission
            self.client.post("%s?save_media=1" % callback_url, content_type='application/json',
                             data=json.dumps(dict(status='answered', duration=2, dtmf='')))

            self.assertEqual(ChannelLog.objects.all().count(), 6)
            channel_log = ChannelLog.objects.last()
            self.assertEqual(channel_log.connection.id, call.id)
            self.assertEqual(channel_log.description, "Incoming request for call")

            log = ChannelLog.objects.filter(description="Downloaded media", connection_id=call.id).first()
            self.assertIsNotNone(log)

            # our log should be the newly saved url
            self.assertIn('http', log.response)

        # nexmo will also send us a final completion message with the call duration
        self.client.post(reverse('ivr.ivrcall_handle', args=[call.pk]), content_type='application/json',
                         data=json.dumps({"status": "completed", "duration": "15"}))

        self.assertEqual(ChannelLog.objects.all().count(), 7)
        channel_log = ChannelLog.objects.last()
        self.assertEqual(channel_log.connection.id, call.id)
        self.assertEqual(channel_log.description, "Updated call status")

        # we should have captured the recording, and ended the call
        call = IVRCall.objects.get(pk=call.pk)
        self.assertEqual(IVRCall.COMPLETED, call.status)
        self.assertEqual(15, call.duration)

        messages = Msg.objects.filter(msg_type=IVR).order_by('pk')
        self.assertEqual(4, messages.count())
        self.assertEqual(4, self.org.get_credits_used())

        # we should have played a recording from the contact back to them
        outbound_msg = messages[1]
        self.assertTrue(outbound_msg.attachments[0].startswith('audio/x-wav:https://'))
        self.assertTrue(outbound_msg.attachments[0].endswith('.wav'))
        self.assertTrue(outbound_msg.text.startswith('https://'))
        self.assertTrue(outbound_msg.text.endswith('.wav'))

        media_msg = messages[2]
        self.assertTrue(media_msg.attachments[0].startswith('audio/x-wav:https://'))
        self.assertTrue(media_msg.attachments[0].endswith('.wav'))
        self.assertEqual('Played contact recording', media_msg.text)

        (host, directory, filename) = media_msg.attachments[0].rsplit('/', 2)
        recording = '%s/%s/%s/media/%s/%s' % (settings.MEDIA_ROOT, settings.STORAGE_ROOT_DIR,
                                              self.org.pk, directory, filename)
        self.assertTrue(os.path.isfile(recording))

        from temba.flows.models import FlowStep
        steps = FlowStep.objects.all()
        self.assertEqual(4, steps.count())

        # each of our steps should have exactly one message
        for step in steps:
            self.assertEqual(1, step.messages.all().count(), msg="Step '%s' does not have exactly one message" % step)

        # each message should have exactly one step
        for msg in messages:
            self.assertEqual(1, msg.steps.all().count(), msg="Message '%s' is not attached to exactly one step" % msg.text)

        # create a valid call first
        flow.start([], [contact], restart_participants=True)

        # now create an errored call
        mock_create_call.side_effect = Exception('Kab00m!')
        nexmo_client = self.org.get_nexmo_client()
        with self.assertRaises(IVRException):
            nexmo_client.start_call(call, '+13603621737', self.channel.address, None)

        call.refresh_from_db()
        self.assertEqual(ChannelSession.FAILED, call.status)

        # check that our channel logs are there
        response = self.client.get(reverse("channels.channellog_list") + '?channel=%d&sessions=1' % self.channel.id)
        self.assertContains(response, "15 seconds")
        self.assertContains(response, "2 results")

        # our channel logs with the error flag
        response = self.client.get(reverse("channels.channellog_list") + '?channel=%d&sessions=1&errors=1' % self.channel.id)
        self.assertContains(response, "warning")
        self.assertContains(response, "1 result")

        # view the errored call read page
        response = self.client.get(reverse("channels.channellog_session", args=[call.id]))
        self.assertContains(response, "https://api.nexmo.com/v1/calls")
        self.assertContains(response, "Kab00m!")

    @patch('temba.ivr.clients.TwilioClient', MockTwilioClient)
    @patch('twilio.util.RequestValidator', MockRequestValidator)
    def test_ivr_subflow(self):

        with patch('temba.ivr.models.IVRCall.start_call') as start_call:
            self.org.connect_twilio("TEST_SID", "TEST_TOKEN", self.admin)
            self.org.save()

            self.get_flow('ivr_subflow')
            parent_flow = Flow.objects.filter(name='Parent Flow').first()

            ben = self.create_contact('Ben', '+12345')
            parent_flow.start(groups=[], contacts=[ben])
            call = IVRCall.objects.get(direction=IVRCall.OUTGOING)

            post_data = dict(CallSid='CallSid', CallStatus='in-progress', CallDuration=20)
            response = self.client.post(reverse('ivr.ivrcall_handle', args=[call.pk]), post_data)

            # should have two runs, but still one call
            self.assertEqual(1, IVRCall.objects.all().count())
            self.assertEqual(2, FlowRun.objects.filter(is_active=True).count())
            self.assertEqual(2, FlowStep.objects.all().count())

            # should give us a redirect, but without the empty flag
            self.assertContains(response, 'Redirect')
            self.assertNotContains(response, 'empty=1')

            # they should call back to us
            response = self.client.post(reverse('ivr.ivrcall_handle', args=[call.pk]), post_data)

            # which should result in two more messages and a gather
            self.assertContains(response, 'Gather')
            self.assertContains(response, 'This is a child flow')
            self.assertContains(response, 'What is your favorite color?')

            self.assertEqual(3, Msg.objects.all().count())
            self.assertEqual(4, FlowStep.objects.all().count())

            # answer back with red
            response = self.client.post(reverse('ivr.ivrcall_handle', args=[call.pk]), dict(Digits=1))

            self.assertContains(response, 'Thanks, returning to the parent flow now.')
            self.assertContains(response, 'Redirect')
            self.assertContains(response, 'resume=1')

            # back down to our original run
            self.assertEqual(1, FlowRun.objects.filter(is_active=True).count())
            run = FlowRun.objects.filter(is_active=True).first()

            response = self.client.post(reverse('ivr.ivrcall_handle', args=[call.pk]) + '?resume=1', post_data)
            self.assertContains(response, 'In the child flow you picked Red.')
            self.assertNotContains(response, 'Redirect')

            # make sure we only called to start the call once
            self.assertEqual(1, start_call.call_count)

            # since we are an ivr flow, we aren't complete until the provider notifies us
            run.refresh_from_db()
            self.assertFalse(run.is_completed())

    @patch('temba.ivr.clients.TwilioClient', MockTwilioClient)
    @patch('twilio.util.RequestValidator', MockRequestValidator)
    def test_ivr_start_flow(self):
        self.org.connect_twilio("TEST_SID", "TEST_TOKEN", self.admin)
        self.org.save()

        msg_flow = self.get_flow('ivr_child_flow')
        ivr_flow = Flow.objects.get(name="Voice Flow")

        # start macklemore in the flow
        ben = self.create_contact('Ben', '+12345')
        ivr_flow.start(groups=[], contacts=[ben])
        call = IVRCall.objects.get(direction=IVRCall.OUTGOING)

        post_data = dict(CallSid='CallSid', CallStatus='in-progress', CallDuration=20)
        self.client.post(reverse('ivr.ivrcall_handle', args=[call.pk]), post_data)

        self.assertEqual(2, FlowStep.objects.all().count())

        # press 1
        response = self.client.post(reverse('ivr.ivrcall_handle', args=[call.pk]), dict(Digits=1))
        self.assertContains(response, '<Say>I just sent you a text.')

        # should have also started a new flow and received our text
        self.assertTrue(FlowRun.objects.filter(contact=ben, flow=msg_flow).first())
        self.assertTrue(Msg.objects.filter(direction=IVRCall.OUTGOING, contact=ben, text="You said foo!").first())

    @patch('temba.ivr.clients.TwilioClient', MockTwilioClient)
    @patch('twilio.util.RequestValidator', MockRequestValidator)
    def test_ivr_call_redirect(self):
        self.org.connect_twilio("TEST_SID", "TEST_TOKEN", self.admin)
        self.org.save()

        # import our flows
        self.get_flow('ivr_call_redirect')

        flow_1 = Flow.objects.get(name="Call Number 1")
        Flow.objects.get(name="Call Number 2")

        shawn = self.create_contact('Marshawn', '+24')
        flow_1.start(groups=[], contacts=[shawn])

        # we should have one call now
        calls = IVRCall.objects.filter(direction=IVRCall.OUTGOING)
        self.assertEqual(1, calls.count())

        # once the first set of actions are processed, we'll initiate a second call
        post_data = dict(CallSid='CallSid', CallStatus='in-progress', CallDuration=20)
        self.client.post(reverse('ivr.ivrcall_handle', args=[calls[0].pk]), post_data)

        calls = IVRCall.objects.filter(direction=IVRCall.OUTGOING).order_by('created_on')
        self.assertEqual(1, calls.count())
        call = calls[0]

        # complete the call
        post_data = dict(CallSid='CallSid', CallStatus='completed', CallDuration=30)
        self.client.post(reverse('ivr.ivrcall_handle', args=[call.pk]), post_data)
        call.refresh_from_db()
        self.assertEqual(IVRCall.COMPLETED, call.status)

    @patch('temba.ivr.clients.TwilioClient', MockTwilioClient)
    @patch('twilio.util.RequestValidator', MockRequestValidator)
    def test_text_trigger_ivr(self):
        self.org.connect_twilio("TEST_SID", "TEST_TOKEN", self.admin)
        self.org.save()

        # import our flows
        self.get_flow('text_trigger_ivr')

        msg_flow = Flow.objects.get(name="Message Flow - Parent")
        Flow.objects.get(name="IVR Flow - Child")

        shawn = self.create_contact('Marshawn', '+24')
        msg_flow.start(groups=[], contacts=[shawn])

        # our message flow triggers an ivr flow
        self.assertEqual(2, FlowRun.objects.all().count())
        self.assertEqual(1, IVRCall.objects.filter(direction=IVRCall.OUTGOING).count())

        # one text message
        self.assertEqual(1, Msg.objects.all().count())

        # now twilio calls back to initiate the triggered call
        call = IVRCall.objects.filter(direction=IVRCall.OUTGOING).first()
        post_data = dict(CallSid='CallSid', CallStatus='in-progress', CallDuration=20)
        self.client.post(reverse('ivr.ivrcall_handle', args=[call.pk]), post_data)

        # still same number of runs and calls, but one more (ivr) message
        self.assertEqual(2, FlowRun.objects.all().count())
        self.assertEqual(1, IVRCall.objects.filter(direction=IVRCall.OUTGOING).count())
        self.assertEqual(2, Msg.objects.all().count())

    @patch('temba.ivr.clients.TwilioClient', MockTwilioClient)
    @patch('twilio.util.RequestValidator', MockRequestValidator)
    def test_non_blocking_rule_ivr(self):

        self.org.connect_twilio("TEST_SID", "TEST_TOKEN", self.admin)
        self.org.save()

        # flow goes: passive -> recording -> msg
        flow = self.get_flow('non_blocking_rule_ivr')

        print(json.dumps(flow.as_json(), indent=2))

        # start marshall in the flow
        eminem = self.create_contact('Eminem', '+12345')
        flow.start(groups=[], contacts=[eminem])
        call = IVRCall.objects.filter(direction=IVRCall.OUTGOING).first()
        self.assertNotEqual(call, None)

        # after a call is picked up, twilio will call back to our server
        post_data = dict(CallSid='CallSid', CallStatus='in-progress', CallDuration=20)
        self.client.post(reverse('ivr.ivrcall_handle', args=[call.pk]), post_data)

        # should have two steps so far, right up to the recording
        self.assertEqual(2, FlowStep.objects.all().count())

        # no outbound yet
        self.assertEqual(None, Msg.objects.filter(direction='O', contact=eminem).first())

        # now pretend we got a recording
        from temba.tests import MockResponse
        with patch('requests.get') as response:
            mock = MockResponse(200, 'Fake Recording Bits')
            mock.add_header('Content-Disposition', 'filename="audio0000.wav"')
            mock.add_header('Content-Type', 'audio/x-wav')
            response.return_value = mock

            self.client.post(reverse('ivr.ivrcall_handle', args=[call.pk]),
                             dict(CallStatus='in-progress', Digits='#',
                                  RecordingUrl='http://api.twilio.com/ASID/Recordings/SID', RecordingSid='FAKESID'))

        # now we should have an outbound message
        self.assertEqual('Hi there Eminem', Msg.objects.filter(direction='O', contact=eminem).first().text)

    @patch('temba.ivr.clients.TwilioClient', MockTwilioClient)
    @patch('twilio.util.RequestValidator', MockRequestValidator)
    def test_ivr_digit_gather(self):

        self.org.connect_twilio("TEST_SID", "TEST_TOKEN", self.admin)
        self.org.save()

        # import an ivr flow
        self.import_file('gather_digits')

        # make sure our flow is there as expected
        flow = Flow.objects.filter(name='Gather Digits').first()

        # start our flow
        eric = self.create_contact('Eric Newcomer', number='+13603621737')
        flow.start([], [eric])
        call = IVRCall.objects.filter(direction=IVRCall.OUTGOING).first()

        # our run shouldn't have an initial expiration yet
        self.assertIsNone(FlowRun.objects.filter(connection=call).first().expires_on)

        # after a call is picked up, twilio will call back to our server
        post_data = dict(CallSid='CallSid', CallStatus='in-progress', CallDuration=20)
        response = self.client.post(reverse('ivr.ivrcall_handle', args=[call.pk]), post_data)

        # once the call is handled, it should have one
        self.assertIsNotNone(FlowRun.objects.filter(connection=call).first().expires_on)

        # make sure we send the finishOnKey attribute to twilio
        self.assertContains(response, 'finishOnKey="#"')

        # make sure we have a redirect to deal with empty responses
        self.assertContains(response, 'empty=1')

        # only have our initial outbound message
        self.assertEqual(1, Msg.objects.all().count())

        # simulate a gather timeout
        post_data['Digits'] = ''
        response = self.client.post(reverse('ivr.ivrcall_handle', args=[call.pk]) + '?empty=1', post_data)

        expiration = call.runs.all().first().expires_on

        # we should be routed through 'other' case
        self.assertContains(response, 'Please enter a number')

        # should now only have two outbound messages and no inbound ones
        self.assertEqual(2, Msg.objects.filter(direction='O').count())
        self.assertEqual(0, Msg.objects.filter(direction='I').count())

        # verify that our expiration didn't change by way of the timeout
        self.assertEqual(expiration, call.runs.all().first().expires_on)

    @patch('nexmo.Client.create_application')
    @patch('nexmo.Client.create_call')
    def test_ivr_digital_gather_with_nexmo(self, mock_create_call, mock_create_application):
        mock_create_application.return_value = dict(id='app-id', keys=dict(private_key='private-key'))
        mock_create_call.return_value = dict(uuid='12345')

        self.org.connect_nexmo('123', '456', self.admin)
        self.org.save()

        self.channel.channel_type = 'NX'
        self.channel.save()

        # import an ivr flow
        self.import_file('gather_digits')

        # make sure our flow is there as expected
        flow = Flow.objects.filter(name='Gather Digits').first()

        # start our flow
        eric = self.create_contact('Eric Newcomer', number='+13603621737')
        flow.start([], [eric])
        call = IVRCall.objects.filter(direction=IVRCall.OUTGOING).first()

        callback_url = reverse('ivr.ivrcall_handle', args=[call.pk])

        # after a call is picked up, nexmo will send a callback to our server
        response = self.client.post(callback_url, content_type='application/json',
                                    data=json.dumps(dict(status='answered', duration=0)))

        call.refresh_from_db()
        self.assertEqual(ChannelSession.IN_PROGRESS, call.status)

        self.assertTrue(dict(action='talk', bargeIn=True, text="Enter your phone number followed by the pound sign.")
                        in response.json())

        # we have an input to collect the digits
        self.assertContains(response, '"action": "input",')

        # make sure we set submitOnHash to true nexmo
        self.assertContains(response, '"submitOnHash": true,')

        self.assertContains(response, '"eventUrl": ["https://%s%s"]}]' % (self.channel.callback_domain, callback_url))

    @patch('jwt.encode')
    @patch('requests.put')
    @patch('nexmo.Client.create_application')
    @patch('requests.post')
    def test_expiration_hangup(self, mock_create_call, mock_create_application, mock_put, mock_jwt):
        mock_create_application.return_value = dict(id='app-id', keys=dict(private_key='private-key'))
        mock_create_call.return_value = MockResponse(200, json.dumps(dict(call=dict(uuid='12345'))))
        mock_jwt.return_value = b"Encoded data"

        request = MagicMock()
        request.body = json.dumps(dict(call_id='12345'))
        request.url = "http://api.nexmo.com/../"
        request.method = "PUT"

        self.org.connect_nexmo('123', '456', self.admin)
        self.org.save()

        self.channel.channel_type = 'NX'
        self.channel.save()

        # import an ivr flow
        self.import_file('gather_digits')

        # make sure our flow is there as expected
        flow = Flow.objects.filter(name='Gather Digits').first()

        # start our flow
        eric = self.create_contact('Eric Newcomer', number='+13603621737')
        flow.start([], [eric])

        # since it hasn't started, our call should be pending and run should have no expiration
        call = IVRCall.objects.filter(direction=IVRCall.OUTGOING).first()
        run = FlowRun.objects.get()
        self.assertEqual(ChannelSession.PENDING, call.status)
        self.assertIsNone(run.expires_on)

        # trigger a status update to show the call was answered
        callback_url = reverse('ivr.ivrcall_handle', args=[call.pk])
        self.client.post(callback_url, content_type='application/json',
                         data=json.dumps(dict(status='answered', duration=0)))

        call.refresh_from_db()
        run.refresh_from_db()
        self.assertEqual(ChannelSession.IN_PROGRESS, call.status)
        self.assertIsNotNone(run.expires_on)

        # now expire our run
        mock_put.return_value = MagicMock(call_id='12345', request=request, status_code=200, content='response')
        run.expire()

        mock_put.assert_called()
        call = IVRCall.objects.filter(direction=IVRCall.OUTGOING).first()
        self.assertEqual(ChannelSession.INTERRUPTED, call.status)

        # call initiation, answer, and timeout should both be logged
        self.assertEqual(3, ChannelLog.objects.filter(connection=call).count())
        self.assertIsNotNone(call.ended_on)

    @patch('nexmo.Client.create_application')
    @patch('nexmo.Client.create_call')
    def test_ivr_subflow_with_nexmo(self, mock_create_call, mock_create_application):
        mock_create_application.return_value = dict(id='app-id', keys=dict(private_key='private-key'))
        mock_create_call.return_value = dict(uuid='12345')

        self.org.connect_nexmo('123', '456', self.admin)
        self.org.save()

        self.channel.channel_type = 'NX'
        self.channel.save()

        # import an ivr flow
        self.import_file('ivr_subflow')

        parent_flow = Flow.objects.filter(name='Parent Flow').first()
        # child_flow = Flow.objects.filter(name='Child Flow').first()

        eric = self.create_contact('Eric Newcomer', number='+13603621737')
        parent_flow.start([], [eric])
        call = IVRCall.objects.filter(direction=IVRCall.OUTGOING).first()

        callback_url = reverse('ivr.ivrcall_handle', args=[call.pk])

        # after a call is picked up, nexmo will send a get call back to our server
        response = self.client.post(callback_url, content_type='application/json',
                                    data=json.dumps(dict(status='ringing', duration=0)))

        response_json = response.json()
        callback_url = response_json[1]['eventUrl'][0]

        self.assertTrue(dict(action='talk', bargeIn=True, text="Hi there! This is my voice flow.") in response_json)

        response = self.client.post(callback_url, content_type='application/json',
                                    data=json.dumps(dict(status='ringing', duration=0)))

        response_json = response.json()
        callback_url = response_json[2]['eventUrl'][0]

        self.assertTrue(dict(action='talk', bargeIn=True,
                             text="What is your favorite color? 1 for Red, 2 for green or 3 for blue.")
                        in response_json)

        # press 1
        response = self.client.post(callback_url, content_type='application/json', data=json.dumps(dict(dtmf='1')))
        response_json = response.json()
        callback_url = response_json[1]['eventUrl'][0]

        self.assertTrue(dict(action='talk', bargeIn=True, text="Thanks, returning to the parent flow now.")
                        in response_json)

        response = self.client.post(callback_url, content_type='application/json',
                                    data=json.dumps(dict(dtmf='')))

        response_json = response.json()

        self.assertTrue(dict(action='talk', bargeIn=False,
                             text="In the child flow you picked Red. I think that is a fine choice.")
                        in response_json)

        # our flow should remain active until we get completion
        self.assertEqual(1, FlowRun.objects.filter(is_active=True).count())

        nexmo_uuid = self.org.config['NEXMO_UUID']
        post_data = dict()
        post_data['status'] = 'completed'
        post_data['duration'] = '0'
        post_data['uuid'] = call.external_id
        response = self.client.post(reverse('handlers.nexmo_call_handler', args=['event', nexmo_uuid]) + '?has_event=1',
                                    json.dumps(post_data), content_type="application/json")

        self.assertContains(response, 'Updated call status')

        # now that we got notfied from the provider, we have no active runs
        self.assertEqual(0, FlowRun.objects.filter(is_active=True).count())
        mock_create_call.assert_called_once()

    @patch('temba.ivr.clients.TwilioClient', MockTwilioClient)
    @patch('twilio.util.RequestValidator', MockRequestValidator)
    def test_ivr_flow(self):

        # should be able to create an ivr flow
        self.assertTrue(self.org.supports_ivr())
        self.assertTrue(self.admin.groups.filter(name="Beta"))
        self.assertContains(self.client.get(reverse('flows.flow_create')), 'Phone Call')

        # no twilio config yet
        self.assertFalse(self.org.is_connected_to_twilio())
        self.assertIsNone(self.org.get_twilio_client())

        # connect it and check our client is configured
        self.org.connect_twilio("TEST_SID", "TEST_TOKEN", self.admin)
        self.org.save()
        self.assertTrue(self.org.is_connected_to_twilio())
        self.assertIsNotNone(self.org.get_twilio_client())

        # no twiml api config yet
        self.assertIsNone(self.channel.get_twiml_client())

        # twiml api config
        config = {Channel.CONFIG_SEND_URL: 'https://api.twilio.com',
                  Channel.CONFIG_ACCOUNT_SID: 'TEST_SID',
                  Channel.CONFIG_AUTH_TOKEN: 'TEST_TOKEN'}
        channel = Channel.create(self.org, self.org.get_user(), 'BR', 'TW', '+558299990000', '+558299990000', config, 'AC')
        self.assertEqual(channel.org, self.org)
        self.assertEqual(channel.address, '+558299990000')

        # import an ivr flow
        self.import_file('call_me_maybe')

        # make sure our flow is there as expected
        flow = Flow.objects.filter(name='Call me maybe').first()
        self.assertEqual('callme', flow.triggers.filter(trigger_type='K').first().keyword)

        user_settings = self.admin.get_settings()
        user_settings.tel = '+18005551212'
        user_settings.save()

        # start our flow as a test contact
        test_contact = Contact.get_test_contact(self.admin)
        Contact.set_simulation(True)
        flow.start([], [test_contact])
        call = IVRCall.objects.filter(direction=IVRCall.OUTGOING).first()

        # should be using the usersettings number in test mode
        self.assertEqual('Placing test call to +1 800-555-1212', ActionLog.objects.all().first().text)

        # our twilio callback on pickup
        post_data = dict(CallSid='CallSid', CallStatus='in-progress', CallDuration=20)
        response = self.client.post(reverse('ivr.ivrcall_handle', args=[call.pk]), post_data)
        call.refresh_from_db()
        self.assertEqual(timedelta(seconds=20), call.get_duration())

        # force a duration calculation
        call.duration = None
        self.assertIsNotNone(call.get_duration())

        # simulate a button press and that our message is handled
        response = self.client.post(reverse('ivr.ivrcall_handle', args=[call.pk]), dict(Digits=4))
        msg = Msg.objects.filter(contact=test_contact, text="4", direction='I').first()
        self.assertIsNotNone(msg)
        self.assertEqual('H', msg.status)

        # explicitly hanging up on a test call should remove it
        call.update_status('in-progress', 0, 'T')
        call.save()
        IVRCall.hangup_test_call(flow)
        self.assertTrue(IVRCall.objects.filter(pk=call.pk).first())

        msgs = Msg.objects.filter(connection=call).order_by('created_on')
        self.assertEqual(3, msgs.count())
        self.assertIn('Would you like me to call you?', msgs[0].text)
        self.assertEqual('4', msgs[1].text)
        self.assertEqual('Press one, two, or three. Thanks.', msgs[2].text)

        ActionLog.objects.all().delete()
        IVRCall.objects.all().delete()
        Msg.objects.all().delete()

        # now pretend we are a normal caller
        eric = self.create_contact('Eric Newcomer', number='+13603621737')
        Contact.set_simulation(False)
        flow.start([], [eric], restart_participants=True)

        # we should have an outbound ivr call now
        call = IVRCall.objects.filter(direction=IVRCall.OUTGOING).first()

        self.assertEqual(timedelta(seconds=0), call.get_duration())
        self.assertIsNotNone(call)
        self.assertEqual('CallSid', call.external_id)

        # after a call is picked up, twilio will call back to our server
        post_data = dict(CallSid='CallSid', CallStatus='in-progress', CallDuration=20)
        response = self.client.post(reverse('ivr.ivrcall_handle', args=[call.pk]), post_data)

        self.assertContains(response, '<Say>Would you like me to call you? Press one for yes, two for no, or three for maybe.</Say>')
        self.assertEqual(1, Msg.objects.filter(msg_type=IVR).count())
        self.assertEqual(1, self.org.get_credits_used())

        # make sure a message from the person on the call goes to the
        # inbox since our flow doesn't handle text messages
        msg = self.create_msg(direction='I', contact=eric, text="message during phone call")
        self.assertFalse(Flow.find_and_handle(msg)[0])

        # updated our status and duration accordingly
        call = IVRCall.objects.get(pk=call.pk)
        self.assertEqual(20, call.duration)
        self.assertEqual(IVRCall.IN_PROGRESS, call.status)

        # don't press any numbers, but # instead
        response = self.client.post(reverse('ivr.ivrcall_handle', args=[call.pk]) + "?empty=1", dict())
        self.assertContains(response, '<Say>Press one, two, or three. Thanks.</Say>')
        self.assertEqual(3, self.org.get_credits_used())

        # press the number 4 (unexpected)
        response = self.client.post(reverse('ivr.ivrcall_handle', args=[call.pk]), dict(Digits=4))

        # our inbound message should be handled
        msg = Msg.objects.filter(text='4', msg_type=IVR).order_by('-created_on').first()
        self.assertEqual('H', msg.status)

        self.assertContains(response, '<Say>Press one, two, or three. Thanks.</Say>')
        self.assertEqual(5, self.org.get_credits_used())

        # two more messages, one inbound and it's response
        self.assertEqual(4, Msg.objects.filter(msg_type=IVR).count())

        # now let's have them press the number 3 (for maybe)
        response = self.client.post(reverse('ivr.ivrcall_handle', args=[call.pk]), dict(Digits=3))
        self.assertContains(response, '<Say>This might be crazy.</Say>')
        messages = Msg.objects.filter(msg_type=IVR).order_by('pk')
        self.assertEqual(6, messages.count())
        self.assertEqual(7, self.org.get_credits_used())

        for msg in messages:
            self.assertEqual(1, msg.steps.all().count(), msg="Message '%s' not attached to step" % msg.text)

        # twilio would then disconnect the user and notify us of a completed call
        self.client.post(reverse('ivr.ivrcall_handle', args=[call.pk]), dict(CallStatus='completed'))
        call = IVRCall.objects.get(pk=call.pk)
        self.assertEqual(IVRCall.COMPLETED, call.status)
        self.assertFalse(FlowRun.objects.filter(connection=call).first().is_active)
        self.assertIsNotNone(call.ended_on)

        # simulation gets flipped off by middleware, and this unhandled message doesn't flip it back on
        self.assertFalse(Contact.get_simulation())

        # also shouldn't have any ActionLogs for non-test users
        self.assertEqual(0, ActionLog.objects.all().count())
        self.assertEqual(flow.get_run_stats()['completed'], 1)

        # should still have no active runs
        self.assertEqual(0, FlowRun.objects.filter(is_active=True).count())

        # and we've exited the flow
        step = FlowStep.objects.all().order_by('-pk').first()
        self.assertTrue(step.left_on)

        # we shouldn't have any outbound pending messages, they are all considered delivered
        self.assertEqual(0, Msg.objects.filter(direction=OUTGOING, status=PENDING, msg_type=IVR).count())

        # test other our call status mappings
        def test_status_update(call_to_update, twilio_status, temba_status, channel_type):
            call_to_update.ended_on = None
            call_to_update.update_status(twilio_status, 0, channel_type)
            call_to_update.save()
            call_to_update.refresh_from_db()
            self.assertEqual(temba_status, IVRCall.objects.get(pk=call_to_update.pk).status)

            if temba_status in IVRCall.DONE:
                self.assertIsNotNone(call_to_update.ended_on)
            else:
                self.assertIsNone(call_to_update.ended_on)

        test_status_update(call, 'queued', IVRCall.QUEUED, 'T')
        test_status_update(call, 'ringing', IVRCall.RINGING, 'T')
        test_status_update(call, 'canceled', IVRCall.CANCELED, 'T')
        test_status_update(call, 'busy', IVRCall.BUSY, 'T')
        test_status_update(call, 'failed', IVRCall.FAILED, 'T')
        test_status_update(call, 'no-answer', IVRCall.NO_ANSWER, 'T')

        test_status_update(call, 'answered', IVRCall.IN_PROGRESS, 'NX')
        test_status_update(call, 'ringing', IVRCall.RINGING, 'NX')
        test_status_update(call, 'completed', IVRCall.COMPLETED, 'NX')
        test_status_update(call, 'failed', IVRCall.FAILED, 'NX')
        test_status_update(call, 'unanswered', IVRCall.NO_ANSWER, 'NX')
        test_status_update(call, 'timeout', IVRCall.NO_ANSWER, 'NX')
        test_status_update(call, 'busy', IVRCall.BUSY, 'NX')
        test_status_update(call, 'rejected', IVRCall.BUSY, 'NX')

        FlowStep.objects.all().delete()
        IVRCall.objects.all().delete()

        # try sending callme trigger
        from temba.msgs.models import INCOMING
        msg = self.create_msg(direction=INCOMING, contact=eric, text="callme")

        # make sure if we are started with a message we still create a normal voice run
        flow.start([], [eric], restart_participants=True, start_msg=msg)

        # we should have an outbound ivr call now, and no steps yet
        call = IVRCall.objects.filter(direction=IVRCall.OUTGOING).first()
        self.assertIsNotNone(call)
        self.assertEqual(0, FlowStep.objects.all().count())

        # after a call is picked up, twilio will call back to our server
        post_data = dict(CallSid='CallSid', CallStatus='in-progress', CallDuration=20)
        self.client.post(reverse('ivr.ivrcall_handle', args=[call.pk]), post_data)

        # should have two flow steps (the outgoing messages, and the step to handle the response)
        steps = FlowStep.objects.all().order_by('pk')

        # the first step has exactly one message which is an outgoing IVR message
        self.assertEqual(1, steps.first().messages.all().count())
        self.assertEqual(1, steps.first().messages.filter(direction=IVRCall.OUTGOING, msg_type=IVR).count())

        # the next step shouldn't have any messages yet since they haven't pressed anything
        self.assertEqual(0, steps[1].messages.all().count())

        # try updating our status to completed for a test contact
        Contact.set_simulation(True)
        flow.start([], [test_contact])
        call = IVRCall.objects.filter(direction=IVRCall.OUTGOING).order_by('-pk').first()
        call.update_status('completed', 30, 'T')
        call.save()
        call.refresh_from_db()

        self.assertEqual(ActionLog.objects.all().order_by('-pk').first().text, 'Call ended.')
        self.assertEqual(call.duration, 30)

        # now look at implied duration
        call.update_status('in-progress', None, 'T')
        call.save()
        call.refresh_from_db()
        self.assertIsNotNone(call.get_duration())
        self.assertEqual(timedelta(seconds=30), call.get_duration())

        # even if no duration is set with started_on
        call.duration = None
        call.started_on = timezone.now() - timedelta(seconds=23)
        call.save()
        call.refresh_from_db()
        self.assertIsNotNone(call.get_duration())
        self.assertEqual(timedelta(seconds=23), call.get_duration())

    @patch('temba.ivr.clients.TwilioClient', MockTwilioClient)
    @patch('twilio.util.RequestValidator', MockRequestValidator)
    def test_rule_first_ivr_flow(self):
        # connect it and check our client is configured
        self.org.connect_twilio("TEST_SID", "TEST_TOKEN", self.admin)
        self.org.save()

        # import an ivr flow
        flow = self.get_flow('rule_first_ivr')

        user_settings = self.admin.get_settings()
        user_settings.tel = '+18005551212'
        user_settings.save()

        # start our flow
        test_contact = Contact.get_test_contact(self.admin)
        Contact.set_simulation(True)
        flow.start([], [test_contact])

        # should be using the usersettings number in test mode
        self.assertEqual('Placing test call to +1 800-555-1212', ActionLog.objects.all().first().text)

        # we should have an outbound ivr call now
        call = IVRCall.objects.filter(direction=IVRCall.OUTGOING).first()

        self.assertEqual(timedelta(seconds=0), call.get_duration())
        self.assertIsNotNone(call)
        self.assertEqual('CallSid', call.external_id)

        # after a call is picked up, twilio will call back to our server
        post_data = dict(CallSid='CallSid', CallStatus='in-progress', CallDuration=20)
        response = self.client.post(reverse('ivr.ivrcall_handle', args=[call.pk]), post_data)
        self.assertContains(response, '<Say>Thanks for calling!</Say>')

        # make sure a message from the person on the call goes to the
        # inbox since our flow doesn't handle text messages
        msg = self.create_msg(direction='I', contact=test_contact, text="message during phone call")
        self.assertFalse(Flow.find_and_handle(msg)[0])

    @patch('temba.ivr.clients.TwilioClient', MockTwilioClient)
    @patch('twilio.util.RequestValidator', MockRequestValidator)
    def test_incoming_call(self):

        # connect it and check our client is configured
        self.org.connect_twilio("TEST_SID", "TEST_TOKEN", self.admin)
        self.org.save()

        # import an ivr flow
        flow = self.get_flow('call_me_maybe')
        flow.version_number = 3
        flow.save()

        # go back to our original version
        flow_json = self.get_flow_json('call_me_maybe')['definition']
        FlowRevision.objects.create(flow=flow, definition=flow_json,
                                    spec_version=3, revision=2, created_by=self.admin, modified_by=self.admin)

        # create an inbound call
        post_data = dict(CallSid='CallSid', CallStatus='ringing', Direction='inbound',
                         From='+250788382382', To=self.channel.address)
        response = self.client.post(reverse('handlers.twilio_handler', args=['voice', self.channel.uuid]), post_data)
        self.assertContains(response, '<Say>Would you like me to call you? Press one for yes, two for no, or three for maybe.</Say>')

        call = IVRCall.objects.all().first()
        self.assertEqual('+250788382382', call.contact_urn.path)
        self.assertEqual('CallSid', call.external_id)

        status_callback = dict(CallSid='CallSid', CallbackSource='call-progress-events',
                               CallStatus='completed', Direction='inbound',
                               From='+250788382382', To=self.channel.address)
        response = self.client.post(reverse('handlers.twilio_handler', args=['voice', self.channel.uuid]), status_callback)
        call.refresh_from_db()
        self.assertEqual('D', call.status)

        status_callback = dict(CallSid='NoCallMatches', CallbackSource='call-progress-events',
                               CallStatus='completed', Direction='inbound',
                               From='+250788382382', To=self.channel.address)
        response = self.client.post(reverse('handlers.twilio_handler', args=['voice', self.channel.uuid]), status_callback)
        self.assertContains(response, 'No call found')

        flow.refresh_from_db()
        self.assertEqual(get_current_export_version(), flow.version_number)

        # now try an inbound call after remove our channel
        self.channel.is_active = False
        self.channel.save()
        response = self.client.post(reverse('handlers.twilio_handler', args=['voice', self.channel.uuid]), post_data)
        self.assertContains(response, 'no channel configured to take this call')
        self.assertEqual(200, response.status_code)

        # no channel found for call handle
        response = self.client.post(reverse('ivr.ivrcall_handle', args=[call.pk]), dict())
        self.assertContains(response, 'No channel found', status_code=400)

    @patch('temba.ivr.clients.TwilioClient', MockTwilioClient)
    @patch('twilio.util.RequestValidator', MockRequestValidator)
    def test_incoming_start(self):
        self.org.connect_twilio("TEST_SID", "TEST_TOKEN", self.admin)
        self.org.save()

        self.get_flow('call_me_start')

        # create an inbound call
        post_data = dict(CallSid='CallSid', CallStatus='ringing', Direction='inbound',
                         From='+250788382382', To=self.channel.address)
        response = self.client.post(reverse('handlers.twilio_handler', args=['voice', self.channel.uuid]), post_data)

        # grab the redirect URL
        redirect_url = re.match(r'.*<Redirect>(.*)</Redirect>.*', force_text(response.content)).group(1)

        # get just the path and hit it
        response = self.client.post(urlparse(redirect_url).path, post_data)
        self.assertContains(response, "You are not part of group.")

    @patch('nexmo.Client.update_call')
    @patch('nexmo.Client.create_application')
    def test_incoming_start_nexmo(self, mock_create_application, mock_update_call):
        mock_create_application.return_value = dict(id='app-id', keys=dict(private_key='private-key'))
        mock_update_call.return_value = dict(uuid='12345')

        self.org.connect_nexmo('123', '456', self.admin)
        self.org.save()

        self.channel.channel_type = 'NX'
        self.channel.save()

        nexmo_uuid = self.org.config['NEXMO_UUID']

        self.get_flow('call_me_start')

        # create an inbound call
        post_data = dict()
        post_data['from'] = '250788382382'
        post_data['to'] = '250785551212'
        post_data['conversation_uuid'] = 'ext-id'
        response = self.client.post(reverse('handlers.nexmo_call_handler', args=['answer', nexmo_uuid]),
                                    json.dumps(post_data), content_type="application/json")

        # grab the redirect URL
        redirect_url = re.match(r'.*"eventUrl": \["(.*)"\].*', force_text(response.content)).group(1)

        # get just the path and hit it
        response = self.client.post("%s?%s" % (urlparse(redirect_url).path, urlparse(redirect_url).query),
                                    json.dumps(post_data), content_type='application/json')
        self.assertContains(response, "You are not part of group.")

        # we have an incoming call
        call = IVRCall.objects.all().first()
        self.assertIsNotNone(call)
        self.assertEqual(call.direction, IVRCall.INCOMING)
        self.assertEqual('+250788382382', call.contact_urn.path)
        self.assertEqual('ext-id', call.external_id)

        self.assertEqual(ChannelLog.objects.all().count(), 2)
        channel_log = ChannelLog.objects.first()
        self.assertEqual(channel_log.connection.id, call.id)
        self.assertEqual(channel_log.description, "Incoming request for call")

    @patch('nexmo.Client.create_application')
    def test_incoming_call_nexmo(self, mock_create_application):
        mock_create_application.return_value = dict(id='app-id', keys=dict(private_key='private-key'))

        self.org.connect_nexmo('123', '456', self.admin)
        self.org.save()

        self.channel.channel_type = 'NX'
        self.channel.save()

        nexmo_uuid = self.org.config['NEXMO_UUID']

        # import an ivr flow
        flow = self.get_flow('call_me_maybe')
        flow.version_number = 3
        flow.save()

        # go back to our original version
        flow_json = self.get_flow_json('call_me_maybe')['definition']

        from temba.flows.models import FlowRevision
        FlowRevision.objects.create(flow=flow, definition=flow_json,
                                    spec_version=3, revision=2, created_by=self.admin, modified_by=self.admin)

        # event for non-existing external_id call
        post_data = dict()
        post_data['status'] = 'ringing'
        post_data['duration'] = '0'
        post_data['conversation_uuid'] = 'ext-id'
        post_data['uuid'] = 'call-ext-id'

        response = self.client.post(reverse('handlers.nexmo_call_handler', args=['event', nexmo_uuid]),
                                    json.dumps(post_data), content_type="application/json")

        self.assertEqual(200, response.status_code)
        self.assertContains(response, 'Call not found for call-ext-id')

        # create an inbound call
        post_data = dict()
        post_data['from'] = '250788382382'
        post_data['to'] = '250785551212'
        post_data['conversation_uuid'] = 'ext-id'

        response = self.client.post(reverse('handlers.nexmo_call_handler', args=['answer', nexmo_uuid]),
                                    json.dumps(post_data), content_type="application/json")

        self.assertTrue(dict(action='talk',
                             bargeIn=True,
                             text='Would you like me to call you? Press one for yes, two for no, or three for maybe.')
                        in response.json())

        call = IVRCall.objects.get()
        self.assertIsNotNone(call)
        self.assertEqual('+250788382382', call.contact_urn.path)
        self.assertEqual(call.direction, IVRCall.INCOMING)
        self.assertEqual('ext-id', call.external_id)

        self.assertEqual(ChannelLog.objects.all().count(), 1)
        channel_log = ChannelLog.objects.first()
        self.assertEqual(channel_log.connection.id, call.id)
        self.assertEqual(channel_log.description, "Incoming request for call")

        flow.refresh_from_db()
        self.assertEqual(get_current_export_version(), flow.version_number)

        self.assertIsNot(call.status, IVRCall.COMPLETED)

        # event for non-existing external_id call
        post_data = dict()
        post_data['status'] = 'completed'
        post_data['duration'] = '0'
        post_data['conversation_uuid'] = 'ext-id'
        post_data['uuid'] = 'call-ext-id'

        response = self.client.post(reverse('handlers.nexmo_call_handler', args=['event', nexmo_uuid]) + '?has_event=1',
                                    json.dumps(post_data), content_type="application/json")

        call = IVRCall.objects.get()
        run = call.runs.all().first()
        self.assertEqual(200, response.status_code)
        self.assertContains(response, "Updated call status")
        self.assertEqual(call.status, IVRCall.COMPLETED)
        self.assertTrue(run.is_completed())

        self.assertEqual(ChannelLog.objects.all().count(), 2)
        channel_log = ChannelLog.objects.last()
        self.assertEqual(channel_log.connection.id, call.id)
        self.assertEqual(channel_log.description, "Updated call status")

    @patch('nexmo.Client.create_application')
    def test_nexmo_config_empty_callbacks(self, mock_create_application):
        mock_create_application.return_value = dict(id='app-id', keys=dict(private_key='private-key'))

        self.org.connect_nexmo('123', '456', self.admin)
        self.org.save()

        nexmo_uuid = self.org.config['NEXMO_UUID']

        response = self.client.post(reverse('handlers.nexmo_call_handler', args=['answer', nexmo_uuid]),
                                    '', content_type='application/json')
        self.assertEqual(200, response.status_code)

        response = self.client.post(reverse('handlers.nexmo_call_handler', args=['answer', nexmo_uuid]),
                                    json.dumps({}), content_type='application/json')
        self.assertEqual(200, response.status_code)

        response = self.client.post(reverse('handlers.nexmo_call_handler', args=['event', nexmo_uuid]),
                                    '', content_type='application/json')
        self.assertEqual(200, response.status_code)

        response = self.client.post(reverse('handlers.nexmo_call_handler', args=['event', nexmo_uuid]),
                                    json.dumps({}), content_type='application/json')
        self.assertEqual(200, response.status_code)

    @patch('nexmo.Client.create_application')
    def test_no_channel_for_call_nexmo(self, mock_create_application):
        mock_create_application.return_value = dict(id='app-id', keys=dict(private_key='private-key'))

        self.org.connect_nexmo('123', '456', self.admin)
        self.org.save()

        nexmo_uuid = self.org.config['NEXMO_UUID']

        # remove our channel
        self.channel.release()

        # create an inbound call
        post_data = dict()
        post_data['from'] = '250788382382'
        post_data['to'] = '250785551212'
        post_data['conversation_uuid'] = 'ext-id'

        response = self.client.post(reverse('handlers.nexmo_call_handler', args=['answer', nexmo_uuid]),
                                    json.dumps(post_data), content_type="application/json")

        self.assertContains(response, 'Channel not found for number: 250785551212', status_code=404)

        # no call object created
        self.assertFalse(IVRCall.objects.all())

    @patch('nexmo.Client.create_application')
    def test_no_flow_for_incoming_nexmo(self, mock_create_application):
        mock_create_application.return_value = dict(id='app-id', keys=dict(private_key='private-key'))

        self.org.connect_nexmo('123', '456', self.admin)
        self.org.save()

        self.channel.channel_type = 'NX'
        self.channel.save()

        nexmo_uuid = self.org.config['NEXMO_UUID']

        flow = self.get_flow('missed_call_flow')

        # create an inbound call
        post_data = dict()
        post_data['from'] = '250788382382'
        post_data['to'] = '250785551212'
        post_data['conversation_uuid'] = 'ext-id'
        response = self.client.post(reverse('handlers.nexmo_call_handler', args=['answer', nexmo_uuid]),
                                    json.dumps(post_data), content_type="application/json")

        self.assertEqual(response.json(), [dict(action='talk', bargeIn=False, text='')])
        # no call object created
        self.assertFalse(IVRCall.objects.all())

        # have a run in the missed call flow
        self.assertTrue(FlowRun.objects.filter(flow=flow))

    @patch('temba.ivr.clients.TwilioClient', MockTwilioClient)
    @patch('twilio.util.RequestValidator', MockRequestValidator)
    def test_no_channel_for_call(self):
        self.org.connect_twilio("TEST_SID", "TEST_TOKEN", self.admin)
        self.org.save()

        # remove our channel
        self.channel.release()

        # create an inbound call
        post_data = dict(CallSid='CallSid', CallStatus='ringing', Direction='inbound',
                         From='+250788382382', To=self.channel.address)
        response = self.client.post(reverse('handlers.twilio_handler', args=['voice', self.channel.uuid]), post_data)

        self.assertEqual(200, response.status_code)
        self.assertContains(response, 'no channel configured to take this call')

        # no call object created
        self.assertFalse(IVRCall.objects.all())

    @patch('temba.ivr.clients.TwilioClient', MockTwilioClient)
    @patch('twilio.util.RequestValidator', MockRequestValidator)
    def test_no_flow_for_incoming(self):
        self.org.connect_twilio("TEST_SID", "TEST_TOKEN", self.admin)
        self.org.save()

        flow = self.get_flow('missed_call_flow')

        # create an inbound call
        post_data = dict(CallSid='CallSid', CallStatus='ringing', Direction='inbound',
                         From='+250788382382', To=self.channel.address)
        response = self.client.post(reverse('handlers.twilio_handler', args=['voice', self.channel.uuid]), post_data)

        self.assertContains(response, 'Hangup')
        # no call object created
        self.assertFalse(IVRCall.objects.all())

        # have a run in the missed call flow
        self.assertTrue(FlowRun.objects.filter(flow=flow))

    @patch('temba.ivr.clients.TwilioClient', MockTwilioClient)
    @patch('twilio.util.RequestValidator', MockRequestValidator)
    def test_no_twilio_connected(self):
        # create an inbound call
        post_data = dict(CallSid='CallSid', CallStatus='ringing', Direction='inbound',
                         From='+250788382382', To=self.channel.address)
        response = self.client.post(reverse('handlers.twilio_handler', args=['voice', self.channel.uuid]), post_data)

        self.assertEqual(response.status_code, 400)

    @patch('temba.ivr.clients.TwilioClient', MockTwilioClient)
    @patch('twilio.util.RequestValidator', MockRequestValidator)
    def test_download_media_twilio(self):
        self.org.connect_twilio("TEST_SID", "TEST_TOKEN", self.admin)
        self.org.save()

        with patch('requests.get') as response:
            mock1 = MockResponse(404, 'No such file')
            mock2 = MockResponse(200, 'Fake VCF Bits')
            mock2.add_header('Content-Type', 'text/x-vcard')
            mock2.add_header('Content-Disposition', 'inline')
            response.side_effect = (mock1, mock2)

            twilio_client = self.org.get_twilio_client()

            with patch('temba.orgs.models.Org.save_media') as mock_save_media:
                mock_save_media.return_value = 'SAVED'

                output = twilio_client.download_media('http://api.twilio.com/ASID/Media/SID')
                self.assertIsNotNone(output)
                self.assertEqual(output, 'text/x-vcard:SAVED')
                # saved_media was called with a file as first argument and the guessed extension as second argument
                self.assertIsInstance(mock_save_media.call_args_list[0][0][0], File)
                self.assertEqual(mock_save_media.call_args_list[0][0][1], 'vcf')

    @patch('temba.utils.nexmo.NexmoClient.download_recording')
    @patch('nexmo.Client.create_application')
    @patch('nexmo.Client.create_call')
    def test_download_media_nexmo(self, mock_create_call, mock_create_application, mock_download_recording):
        mock_create_application.return_value = dict(id='app-id', keys=dict(private_key='private-key'))
        mock_create_call.return_value = dict(uuid='12345')
        mock_download_recording.side_effect = [
            MockResponse(200, "SOUND BITS"),

            MockResponse(400, "Error"),
            MockResponse(200, "SOUND_BITS", headers={"Content-Type": "audio/x-wav"}),

            MockResponse(200, "SOUND_BITS", headers={"Content-Type": "audio/x-wav", "Content-Disposition": "inline"}),

            MockResponse(200, "SOUND_BITS", headers={"Content-Type": "audio/x-wav",
                                                     "Content-Disposition": 'attachment; filename="playme.wav"'})
        ]

        self.org.connect_nexmo('123', '456', self.admin)
        self.org.save()

        self.channel.channel_type = 'NX'
        self.channel.save()

        # import an ivr flow
        self.import_file('gather_digits')

        # make sure our flow is there as expected
        flow = Flow.objects.filter(name='Gather Digits').first()

        # start our flow
        eric = self.create_contact('Eric Newcomer', number='+13603621737')
        flow.start([], [eric])
        call = IVRCall.objects.filter(direction=IVRCall.OUTGOING).first()
        call.external_id = 'ext-id'
        call.save()

        nexmo_client = self.org.get_nexmo_client()

        with patch('temba.orgs.models.Org.save_media') as mock_save_media:
            mock_save_media.return_value = 'SAVED'

            # without content-type
            output = nexmo_client.download_media(call, "http://nexmo.com/some_audio_link")
            self.assertIsNone(output)

            # with content-type and retry fetch
            output = nexmo_client.download_media(call, "http://nexmo.com/some_audio_link")
            self.assertIsNotNone(output)
            self.assertEqual(output, 'audio/x-wav:SAVED')

            # for content-disposition inline
            output = nexmo_client.download_media(call, "http://nexmo.com/some_audio_link")
            self.assertIsNotNone(output)
            self.assertEqual(output, 'audio/x-wav:SAVED')

            # for content disposition attachment
            output = nexmo_client.download_media(call, "http://nexmo.com/some_audio_link")
            self.assertIsNotNone(output)
            self.assertEqual(output, 'audio/x-wav:SAVED')

            self.assertEqual(3, len(mock_save_media.call_args_list))

            for i in range(len(mock_save_media.call_args_list)):
                self.assertIsInstance(mock_save_media.call_args_list[i][0][0], File)
                self.assertEqual(mock_save_media.call_args_list[i][0][1], 'wav')

    @patch('jwt.encode')
    @patch('nexmo.Client.create_application')
    def test_temba_utils_nexmo_methods(self, mock_create_application, mock_jwt_encode):
        mock_create_application.return_value = dict(id='app-id', keys=dict(private_key='private-key'))
        mock_jwt_encode.return_value = 'TOKEN'

        self.org.connect_nexmo('123', '456', self.admin)
        self.org.save()

        self.channel.channel_type = 'NX'
        self.channel.save()

        # import an ivr flow
        self.import_file('gather_digits')

        # make sure our flow is there as expected
        flow = Flow.objects.filter(name='Gather Digits').first()

        # start our flow
        eric = self.create_contact('Eric Newcomer', number='+13603621737')
        flow.start([], [eric])
        call = IVRCall.objects.filter(direction=IVRCall.OUTGOING).first()
        call.external_id = 'ext-id'
        call.save()

        nexmo_client = self.org.get_nexmo_client()

        user_agent = 'nexmo-python/{0}/{1}'.format(nexmo.__version__, python_version())

        self.assertEqual(nexmo_client.gen_headers(), {"User-Agent": user_agent, "Authorization": b'Bearer TOKEN'})

        with patch('requests.get') as mock_get:
            mock_get.return_value = MockResponse(200, "DONE")
            nexmo_client.download_media(call, 'http://example.com/file.txt')

            mock_get.assert_called_once_with('http://example.com/file.txt', params=None,
                                             headers={"User-Agent": user_agent, "Authorization": b'Bearer TOKEN'})
