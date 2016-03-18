from __future__ import unicode_literals

import json
import os

from django.conf import settings
from django.contrib.auth.models import Group
from django.core.urlresolvers import reverse
from mock import patch
from temba.channels.models import TWILIO, CALL, ANSWER, SEND
from temba.contacts.models import Contact
from temba.flows.models import Flow, FAILED, FlowRun, ActionLog, FlowStep
from temba.msgs.models import Msg, IVR
from temba.tests import FlowFileTest, MockTwilioClient, MockRequestValidator
from .models import IVRCall, OUTGOING, IN_PROGRESS, QUEUED, COMPLETED, BUSY, CANCELED, RINGING, NO_ANSWER


class IVRTests(FlowFileTest):

    def setUp(self):

        super(IVRTests, self).setUp()

        # configure our account to be IVR enabled
        self.channel.channel_type = TWILIO
        self.channel.role = CALL + ANSWER + SEND
        self.channel.save()
        self.admin.groups.add(Group.objects.get(name="Beta"))
        self.login(self.admin)

    @patch('temba.orgs.models.TwilioRestClient', MockTwilioClient)
    @patch('temba.ivr.clients.TwilioClient', MockTwilioClient)
    @patch('twilio.util.RequestValidator', MockRequestValidator)
    def test_twilio_failed_auth(self):

        def create(self, to=None, from_=None, url=None, status_callback=None):
            from twilio import TwilioRestException
            raise TwilioRestException(403, 'http://twilio.com', code=20003)
        MockTwilioClient.MockCalls.create = create

        # connect it and check our client is configured
        self.org.connect_twilio("TEST_SID", "TEST_TOKEN")
        self.org.save()

        # import an ivr flow
        self.import_file('call-me-maybe')
        flow = Flow.objects.filter(name='Call me maybe').first()

        user_settings = self.admin.get_settings()
        user_settings.tel = '+18005551212'
        user_settings.save()

        test_contact = Contact.get_test_contact(self.admin)
        Contact.set_simulation(True)
        flow.start([], [test_contact])

        log = ActionLog.objects.all().order_by('-pk').first()
        self.assertEquals(log.text, 'Call ended. Could not authenticate with your Twilio account. '
                                    'Check your token and try again.')

    @patch('temba.orgs.models.TwilioRestClient', MockTwilioClient)
    @patch('temba.ivr.clients.TwilioClient', MockTwilioClient)
    @patch('twilio.util.RequestValidator', MockRequestValidator)
    def test_ivr_recording(self):

        # create our ivr setup
        self.org.connect_twilio("TEST_SID", "TEST_TOKEN")
        self.org.save()
        self.import_file('capture-recording')
        flow = Flow.objects.filter(name='Capture Recording').first()

        # start our flow
        contact = self.create_contact('Chuck D', number='+13603621737')
        flow.start([], [contact])
        call = IVRCall.objects.filter(direction=OUTGOING).first()

        # after a call is picked up, twilio will call back to our server
        post_data = dict(CallSid='CallSid', CallStatus='in-progress', CallDuration=20)
        response = self.client.post(reverse('ivr.ivrcall_handle', args=[call.pk]), post_data)
        self.assertContains(response, '<Say>Please make a recording after the tone.</Say>')

        # simulate the caller making a recording and then hanging up, first they'll give us the
        # recording (they give us a call status of completed at the same time)
        from temba.tests import MockResponse

        # make sure our file isn't there to start
        run = contact.runs.all().first()
        recording_file = '%s/recordings/%d/%d/runs/%d/FAKESID.wav' % (settings.MEDIA_ROOT, flow.org.pk, flow.pk, run.pk)
        if os.path.isfile(recording_file):
            os.remove(recording_file)

        with patch('requests.get') as response:
            response.return_value = MockResponse(200, 'Fake Recording Bits')
            self.client.post(reverse('ivr.ivrcall_handle', args=[call.pk]),
                             dict(CallStatus='completed',
                                  Digits='hangup',
                                  RecordingUrl='http://api.twilio.com/ASID/Recordings/SID',
                                  RecordingSid='FAKESID'))

        # we should have captured the recording, and ended the call
        call = IVRCall.objects.get(pk=call.pk)
        self.assertTrue(os.path.isfile(recording_file))
        self.assertEquals(COMPLETED, call.status)

        # twilio will also send us a final completion message with the call duration (status of completed again)
        self.client.post(reverse('ivr.ivrcall_handle', args=[call.pk]),
                         dict(CallStatus='completed', CallDuration='15'))

        call = IVRCall.objects.get(pk=call.pk)
        self.assertEquals(COMPLETED, call.status)
        self.assertEquals(15, call.duration)

        messages = Msg.all_messages.filter(msg_type=IVR).order_by('pk')
        self.assertEquals(4, messages.count())
        self.assertEquals(4, self.org.get_credits_used())

        # we should have played a recording from the contact back to them
        self.assertTrue('FAKESID.wav' in messages[2].recording_url)

        from temba.flows.models import FlowStep
        steps = FlowStep.objects.all()
        self.assertEquals(4, steps.count())

        # each of our steps should have exactly one message
        for step in steps:
            self.assertEquals(1, step.messages.all().count(), msg="Step '%s' does not have excatly one message" % step)

        # each message should have exactly one step
        for msg in messages:
            self.assertEquals(1, msg.steps.all().count(), msg="Message '%s' is not attached to exaclty one step" % msg.text)

    @patch('temba.orgs.models.TwilioRestClient', MockTwilioClient)
    @patch('temba.ivr.clients.TwilioClient', MockTwilioClient)
    @patch('twilio.util.RequestValidator', MockRequestValidator)
    def test_ivr_child_flow(self):
        self.org.connect_twilio("TEST_SID", "TEST_TOKEN")
        self.org.save()

        msg_flow = self.get_flow('ivr_child_flow')
        ivr_flow = Flow.objects.get(name="Voice Flow")

        # start macklemore in the flow
        ben = self.create_contact('Ben', '+12345')
        ivr_flow.start(groups=[], contacts=[ben])
        call = IVRCall.objects.get(direction=OUTGOING)

        post_data = dict(CallSid='CallSid', CallStatus='in-progress', CallDuration=20)
        self.client.post(reverse('ivr.ivrcall_handle', args=[call.pk]), post_data)

        self.assertEquals(2, FlowStep.objects.all().count())

        # press 1
        response = self.client.post(reverse('ivr.ivrcall_handle', args=[call.pk]), dict(Digits=1))
        self.assertContains(response, '<Say>I just sent you a text.')

        # should have also started a new flow and received our text
        self.assertTrue(FlowRun.objects.filter(contact=ben, flow=msg_flow).first())
        self.assertTrue(Msg.all_messages.filter(direction=OUTGOING, contact=ben, text="You said foo!").first())

    @patch('temba.orgs.models.TwilioRestClient', MockTwilioClient)
    @patch('temba.ivr.clients.TwilioClient', MockTwilioClient)
    @patch('twilio.util.RequestValidator', MockRequestValidator)
    def test_non_blocking_rule_ivr(self):

        self.org.connect_twilio("TEST_SID", "TEST_TOKEN")
        self.org.save()

        # flow goes: passive -> recording -> msg
        flow = self.get_flow('non_blocking_rule_ivr')

        print json.dumps(flow.as_json(), indent=2)

        # start marshall in the flow
        eminem = self.create_contact('Eminem', '+12345')
        flow.start(groups=[], contacts=[eminem])
        call = IVRCall.objects.filter(direction=OUTGOING).first()
        self.assertNotEquals(call, None)

        # after a call is picked up, twilio will call back to our server
        post_data = dict(CallSid='CallSid', CallStatus='in-progress', CallDuration=20)
        self.client.post(reverse('ivr.ivrcall_handle', args=[call.pk]), post_data)

        # should have two steps so far, right up to the recording
        self.assertEquals(2, FlowStep.objects.all().count())

        # no outbound yet
        self.assertEquals(None, Msg.all_messages.filter(direction='O', contact=eminem).first())

        # now pretend we got a recording
        from temba.tests import MockResponse
        with patch('requests.get') as response:
            response.return_value = MockResponse(200, 'Fake Recording Bits')
            self.client.post(reverse('ivr.ivrcall_handle', args=[call.pk]),
                                     dict(CallStatus='in-progress',
                                     Digits='#',
                                     RecordingUrl='http://api.twilio.com/ASID/Recordings/SID',
                                     RecordingSid='FAKESID'))

        # now we should have an outbound message
        self.assertEquals('Hi there Eminem', Msg.all_messages.filter(direction='O', contact=eminem).first().text)

    @patch('temba.orgs.models.TwilioRestClient', MockTwilioClient)
    @patch('temba.ivr.clients.TwilioClient', MockTwilioClient)
    @patch('twilio.util.RequestValidator', MockRequestValidator)
    def test_ivr_digit_gather(self):

        self.org.connect_twilio("TEST_SID", "TEST_TOKEN")
        self.org.save()

        # import an ivr flow
        self.import_file('gather-digits')

        # make sure our flow is there as expected
        flow = Flow.objects.filter(name='Gather Digits').first()

        # start our flow
        eric = self.create_contact('Eric Newcomer', number='+13603621737')
        flow.start([], [eric])
        call = IVRCall.objects.filter(direction=OUTGOING).first()

        # after a call is picked up, twilio will call back to our server
        post_data = dict(CallSid='CallSid', CallStatus='in-progress', CallDuration=20)
        response = self.client.post(reverse('ivr.ivrcall_handle', args=[call.pk]), post_data)

        # make sure we send the finishOnKey attribute to twilio
        self.assertContains(response, 'finishOnKey="#"')

    @patch('temba.orgs.models.TwilioRestClient', MockTwilioClient)
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
        self.org.connect_twilio("TEST_SID", "TEST_TOKEN")
        self.org.save()
        self.assertTrue(self.org.is_connected_to_twilio())
        self.assertIsNotNone(self.org.get_twilio_client())

        # import an ivr flow
        self.import_file('call-me-maybe')

        # make sure our flow is there as expected
        flow = Flow.objects.filter(name='Call me maybe').first()
        self.assertEquals('callme', flow.triggers.filter(trigger_type='K').first().keyword)

        user_settings = self.admin.get_settings()
        user_settings.tel = '+18005551212'
        user_settings.save()

        # start our flow as a test contact
        test_contact = Contact.get_test_contact(self.admin)
        Contact.set_simulation(True)
        flow.start([], [test_contact])
        call = IVRCall.objects.filter(direction=OUTGOING).first()

        # should be using the usersettings number in test mode
        self.assertEquals('Placing test call to +1 800-555-1212', ActionLog.objects.all().first().text)

        # explicitly hanging up on a test call should remove it
        call.update_status('in-progress', 0)
        call.save()
        IVRCall.hangup_test_call(flow)
        self.assertIsNone(IVRCall.objects.filter(pk=call.pk).first())

        ActionLog.objects.all().delete()
        IVRCall.objects.all().delete()

        # now pretend we are a normal caller
        eric = self.create_contact('Eric Newcomer', number='+13603621737')
        Contact.set_simulation(False)
        flow.start([], [eric], restart_participants=True)

        # we should have an outbound ivr call now
        call = IVRCall.objects.filter(direction=OUTGOING).first()

        self.assertEquals(0, call.get_duration())
        self.assertIsNotNone(call)
        self.assertEquals('CallSid', call.external_id)

        # after a call is picked up, twilio will call back to our server
        post_data = dict(CallSid='CallSid', CallStatus='in-progress', CallDuration=20)
        response = self.client.post(reverse('ivr.ivrcall_handle', args=[call.pk]), post_data)

        self.assertContains(response, '<Say>Would you like me to call you? Press one for yes, two for no, or three for maybe.</Say>')
        self.assertEquals(1, Msg.all_messages.filter(msg_type=IVR).count())
        self.assertEquals(1, self.org.get_credits_used())

        # make sure a message from the person on the call goes to the
        # inbox since our flow doesn't handle text messages
        msg = self.create_msg(direction='I', contact=eric, text="message during phone call")
        self.assertFalse(Flow.find_and_handle(msg))

        # updated our status and duration accordingly
        call = IVRCall.objects.get(pk=call.pk)
        self.assertEquals(20, call.duration)
        self.assertEquals(IN_PROGRESS, call.status)

        # press the number 4 (unexpected)
        response = self.client.post(reverse('ivr.ivrcall_handle', args=[call.pk]), dict(Digits=4))
        self.assertContains(response, '<Say>Press one, two, or three. Thanks.</Say>')
        self.assertEquals(4, self.org.get_credits_used())

        # two more messages, one inbound and it's response
        self.assertEquals(3, Msg.all_messages.filter(msg_type=IVR).count())

        # now let's have them press the number 3 (for maybe)
        response = self.client.post(reverse('ivr.ivrcall_handle', args=[call.pk]), dict(Digits=3))
        self.assertContains(response, '<Say>This might be crazy.</Say>')
        messages = Msg.all_messages.filter(msg_type=IVR).order_by('pk')
        self.assertEquals(5, messages.count())
        self.assertEquals(6, self.org.get_credits_used())

        for msg in messages:
            self.assertEquals(1, msg.steps.all().count(), msg="Message '%s' not attached to step" % msg.text)

        # twilio would then disconnect the user and notify us of a completed call
        self.client.post(reverse('ivr.ivrcall_handle', args=[call.pk]), dict(CallStatus='completed'))
        call = IVRCall.objects.get(pk=call.pk)
        self.assertEquals(COMPLETED, call.status)
        self.assertFalse(FlowRun.objects.filter(call=call).first().is_active)

        # simulation gets flipped off by middleware, and this unhandled message doesn't flip it back on
        self.assertFalse(Contact.get_simulation())

        # also shouldn't have any ActionLogs for non-test users
        self.assertEquals(0, ActionLog.objects.all().count())
        self.assertEquals(1, flow.get_completed_runs())

        # should still have no active runs
        self.assertEquals(0, FlowRun.objects.filter(is_active=True).count())

        # and we've exited the flow
        step = FlowStep.objects.all().order_by('-pk').first()
        self.assertTrue(step.left_on)

        # test other our call status mappings with twilio
        def test_status_update(call_to_update, twilio_status, temba_status):
            call_to_update.update_status(twilio_status, 0)
            call_to_update.save()
            self.assertEquals(temba_status, IVRCall.objects.get(pk=call_to_update.pk).status)

        test_status_update(call, 'queued', QUEUED)
        test_status_update(call, 'ringing', RINGING)
        test_status_update(call, 'canceled', CANCELED)
        test_status_update(call, 'busy', BUSY)
        test_status_update(call, 'failed', FAILED)
        test_status_update(call, 'no-answer', NO_ANSWER)

        FlowStep.objects.all().delete()
        IVRCall.objects.all().delete()

        # try sending callme trigger
        from temba.msgs.models import INCOMING
        msg = self.create_msg(direction=INCOMING, contact=eric, text="callme")

        # make sure if we are started with a message we still create a normal voice run
        flow.start([], [eric], restart_participants=True, start_msg=msg)

        # we should have an outbound ivr call now, and no steps yet
        call = IVRCall.objects.filter(direction=OUTGOING).first()
        self.assertIsNotNone(call)
        self.assertEquals(0, FlowStep.objects.all().count())

        # after a call is picked up, twilio will call back to our server
        post_data = dict(CallSid='CallSid', CallStatus='in-progress', CallDuration=20)
        self.client.post(reverse('ivr.ivrcall_handle', args=[call.pk]), post_data)

        # should have two flow steps (the outgoing messages, and the step to handle the response)
        steps = FlowStep.objects.all().order_by('pk')

        # the first step has exactly one message which is an outgoing IVR message
        self.assertEquals(1, steps.first().messages.all().count())
        self.assertEquals(1, steps.first().messages.filter(direction=OUTGOING, msg_type=IVR).count())

        # the next step shouldn't have any messages yet since they haven't pressed anything
        self.assertEquals(0, steps[1].messages.all().count())

        # test invalid contact id
        with self.assertRaises(ValueError):
            IVRCall.create_outgoing(call.channel, 999, flow, self.admin)

        # test no valid urn
        with self.assertRaises(ValueError):
            call.contact.urns.all().delete()
            IVRCall.create_outgoing(call.channel, call.contact.pk, flow, self.admin)

        # try updating our status to completed for a test contact
        Contact.set_simulation(True)
        flow.start([], [test_contact])
        call = IVRCall.objects.filter(direction=OUTGOING).order_by('-pk').first()
        call.update_status('completed', 30)
        call.save()
        call.refresh_from_db()

        self.assertEqual(ActionLog.objects.all().order_by('-pk').first().text, 'Call ended.')
        self.assertEqual(call.duration, 30)

        # now look at implied duration
        call.update_status('in-progress', None)
        call.save()
        call.refresh_from_db()
        self.assertIsNotNone(call.get_duration())

    @patch('temba.orgs.models.TwilioRestClient', MockTwilioClient)
    @patch('temba.ivr.clients.TwilioClient', MockTwilioClient)
    @patch('twilio.util.RequestValidator', MockRequestValidator)
    def test_rule_first_ivr_flow(self):
        # connect it and check our client is configured
        self.org.connect_twilio("TEST_SID", "TEST_TOKEN")
        self.org.save()

        # import an ivr flow
        flow = self.get_flow('rule-first-ivr')

        user_settings = self.admin.get_settings()
        user_settings.tel = '+18005551212'
        user_settings.save()

        # start our flow
        test_contact = Contact.get_test_contact(self.admin)
        Contact.set_simulation(True)
        flow.start([], [test_contact])

        # should be using the usersettings number in test mode
        self.assertEquals('Placing test call to +1 800-555-1212', ActionLog.objects.all().first().text)

        # we should have an outbound ivr call now
        call = IVRCall.objects.filter(direction=OUTGOING).first()

        self.assertEquals(0, call.get_duration())
        self.assertIsNotNone(call)
        self.assertEquals('CallSid', call.external_id)

        # after a call is picked up, twilio will call back to our server
        post_data = dict(CallSid='CallSid', CallStatus='in-progress', CallDuration=20)
        response = self.client.post(reverse('ivr.ivrcall_handle', args=[call.pk]), post_data)
        self.assertContains(response, '<Say>Thanks for calling!</Say>')

        # make sure a message from the person on the call goes to the
        # inbox since our flow doesn't handle text messages
        msg = self.create_msg(direction='I', contact=test_contact, text="message during phone call")
        self.assertFalse(Flow.find_and_handle(msg))

    @patch('temba.orgs.models.TwilioRestClient', MockTwilioClient)
    @patch('temba.ivr.clients.TwilioClient', MockTwilioClient)
    @patch('twilio.util.RequestValidator', MockRequestValidator)
    def test_incoming_call(self):

        # connect it and check our client is configured
        self.org.connect_twilio("TEST_SID", "TEST_TOKEN")
        self.org.save()

        # import an ivr flow
        flow = self.get_flow('call-me-maybe')

        # create an inbound call
        post_data = dict(CallSid='CallSid', CallStatus='ringing', Direction='inbound',
                         From='+250788382382', To=self.channel.address)
        response = self.client.post(reverse('handlers.twilio_handler'), post_data)
        self.assertContains(response, '<Say>Would you like me to call you? Press one for yes, two for no, or three for maybe.</Say>')

        call = IVRCall.objects.all().first()
        self.assertEquals('+250788382382', call.contact_urn.path)
