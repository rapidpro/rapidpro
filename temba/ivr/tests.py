import os
import re
from datetime import timedelta
from platform import python_version
from unittest.mock import MagicMock, patch
from urllib.parse import urlparse

import nexmo
from django_redis import get_redis_connection

from django.conf import settings
from django.contrib.auth.models import Group
from django.core.files import File
from django.urls import reverse
from django.utils import timezone
from django.utils.encoding import force_text

import celery.exceptions
from celery import current_app

from temba.channels.models import Channel, ChannelConnection, ChannelLog
from temba.flows.models import Flow, FlowRevision, FlowRun
from temba.ivr.tasks import check_calls_task, start_call_task
from temba.msgs.models import IVR, OUTGOING, PENDING, Msg
from temba.tests import FlowFileTest, MockResponse
from temba.tests.twilio import MockRequestValidator, MockTwilioClient
from temba.utils import json
from temba.utils.locks import NonBlockingLock

from .clients import IVRException
from .models import IVRCall


class IVRTests(FlowFileTest):
    def setUp(self):
        super().setUp()
        settings.SEND_CALLS = True

        # configure our account to be IVR enabled
        self.channel.channel_type = "T"
        self.channel.role = Channel.ROLE_CALL + Channel.ROLE_ANSWER + Channel.ROLE_SEND
        self.channel.save()
        self.admin.groups.add(Group.objects.get(name="Beta"))
        self.login(self.admin)

    def tearDown(self):
        super().tearDown()
        settings.SEND_CALLS = False

    @patch("nexmo.Client.create_application")
    @patch("nexmo.Client.create_call")
    @patch("nexmo.Client.update_call")
    @patch("temba.ivr.clients.TwilioClient", MockTwilioClient)
    @patch("twilio.request_validator.RequestValidator", MockRequestValidator)
    def test_preferred_channel(self, mock_update_call, mock_create_call, mock_create_application):
        mock_create_application.return_value = dict(id="app-id", keys=dict(private_key="private-key\n"))
        mock_create_call.return_value = dict(uuid="12345")
        mock_update_call.return_value = dict(uuid="12345")

        # connect it and check our client is configured
        self.org.connect_twilio("TEST_SID", "TEST_TOKEN", self.admin)
        self.org.save()

        flow = self.get_flow("call_me_maybe")

        # start our flow
        contact = self.create_contact("Chuck D", number="+13603621737")
        flow.start([], [contact])

        call = IVRCall.objects.get()
        self.assertEqual(IVRCall.WIRED, call.status)

        # call should be on a Twilio channel since that's all we have
        self.assertEqual("T", call.channel.channel_type)

        # connect Nexmo instead
        self.org.connect_nexmo("123", "456", self.admin)
        self.org.save()

        # manually create a Nexmo channel
        nexmo = Channel.create(
            self.org,
            self.user,
            "RW",
            "NX",
            role=Channel.ROLE_CALL + Channel.ROLE_ANSWER + Channel.ROLE_SEND,
            name="Nexmo Channel",
            address="+250785551215",
        )

        # set the preferred channel on this contact to Twilio
        contact.set_preferred_channel(self.channel)

        # restart the flow
        flow.start([], [contact], restart_participants=True)

        call = IVRCall.objects.all().last()
        self.assertEqual(IVRCall.WIRED, call.status)
        self.assertEqual("T", call.channel.channel_type)

        # switch back to Nexmo being the preferred channel
        contact.set_preferred_channel(nexmo)

        # clear open calls and runs
        self.releaseIVRCalls()
        self.releaseRuns()

        # restart the flow
        flow.start([], [contact], restart_participants=True)

        call = IVRCall.objects.all().last()
        self.assertEqual(IVRCall.WIRED, call.status)
        self.assertEqual("NX", call.channel.channel_type)

    def test_twiml_client(self):
        # no twiml api config yet
        self.assertIsNone(self.channel.get_twiml_client())

        # twiml api config
        config = {
            Channel.CONFIG_SEND_URL: "https://api.twiml.com",
            Channel.CONFIG_ACCOUNT_SID: "TEST_SID",
            Channel.CONFIG_AUTH_TOKEN: "TEST_TOKEN",
        }
        channel = Channel.create(
            self.org, self.org.get_user(), "BR", "TW", "+558299990000", "+558299990000", config, "AC"
        )
        self.assertEqual(channel.org, self.org)
        self.assertEqual(channel.address, "+558299990000")

        twiml_client = channel.get_twiml_client()
        self.assertIsNotNone(twiml_client)
        self.assertEqual(twiml_client.api.base_url, "https://api.twiml.com")

    @patch("twilio.request_validator.RequestValidator", MockRequestValidator)
    @patch("twilio.rest.api.v2010.account.call.CallInstance", MockTwilioClient.MockCallInstance)
    def test_call_logging(self):
        # create our ivr setup
        self.org.connect_twilio("TEST_SID", "TEST_TOKEN", self.admin)
        self.org.save()

        with patch("twilio.rest.Client.request") as mock:
            mock.return_value = MockResponse(200, '{"sid": "CAa346467ca321c71dbd5e12f627deb854"}')
            self.import_file("capture_recording")
            flow = Flow.objects.filter(name="Capture Recording").first()

            # start our flow
            contact = self.create_contact("Chuck D", number="+13603621737")
            flow.start([], [contact])

            # should have a channel log for starting the call
            logs = ChannelLog.objects.filter(is_error=False).all()
            self.assertEqual([log.response for log in logs], ["None", '{"sid": "CAa346467ca321c71dbd5e12f627deb854"}'])

            # expire our flow, causing the call to hang up
            mock.return_value = MockResponse(200, '{"sid": "CAa346467ca321c71dbd5e12f627deb855"}')
            run = flow.runs.get()
            run.expire()

            # two channel logs now
            logs = ChannelLog.objects.filter(is_error=False).all()
            self.assertEqual(
                [log.response for log in logs],
                [
                    "None",
                    '{"sid": "CAa346467ca321c71dbd5e12f627deb854"}',
                    '{"sid": "CAa346467ca321c71dbd5e12f627deb855"}',
                ],
            )

    @patch("temba.ivr.clients.TwilioClient", MockTwilioClient)
    @patch("twilio.request_validator.RequestValidator", MockRequestValidator)
    def test_disable_calls_twilio(self):
        with self.settings(SEND_CALLS=False):
            self.org.connect_twilio("TEST_SID", "TEST_TOKEN", self.admin)
            self.org.save()

            with patch("twilio.rest.api.v2010.account.call.CallList.create") as mock:
                self.import_file("call_me_maybe")
                flow = Flow.objects.filter(name="Call me maybe").first()

                # start our flow
                contact = self.create_contact("Chuck D", number="+13603621737")
                flow.start([], [contact])

                self.assertEqual(mock.call_count, 0)
                call = IVRCall.objects.get()
                self.assertEqual(IVRCall.FAILED, call.status)

    @patch("nexmo.Client.create_application")
    @patch("nexmo.Client.create_call")
    def test_disable_calls_nexmo(self, mock_create_call, mock_create_application):
        mock_create_application.return_value = dict(id="app-id", keys=dict(private_key="private-key\n"))
        mock_create_call.return_value = dict(uuid="12345")

        with self.settings(SEND_CALLS=False):
            self.org.connect_nexmo("123", "456", self.admin)
            self.org.save()

            self.channel.channel_type = "NX"
            self.channel.save()

            # import an ivr flow
            self.import_file("gather_digits")

            # make sure our flow is there as expected
            flow = Flow.objects.filter(name="Gather Digits").first()

            # start our flow
            eric = self.create_contact("Eric Newcomer", number="+13603621737")
            flow.start([], [eric])

            self.assertEqual(mock_create_call.call_count, 0)
            call = IVRCall.objects.get()
            self.assertEqual(IVRCall.FAILED, call.status)

    @patch("temba.ivr.clients.TwilioClient", MockTwilioClient)
    @patch("twilio.request_validator.RequestValidator", MockRequestValidator)
    def test_bogus_call(self):
        # create our ivr setup
        self.org.connect_twilio("TEST_SID", "TEST_TOKEN", self.admin)
        self.org.save()
        self.import_file("capture_recording")

        # post to a bogus call id
        post_data = dict(CallSid="CallSid", CallStatus="in-progress", CallDuration=20)
        response = self.client.post(reverse("ivr.ivrcall_handle", args=[999_999_999]), post_data)
        self.assertEqual(404, response.status_code)

        # start a real call
        flow = Flow.objects.filter(name="Capture Recording").first()
        contact = self.create_contact("Chuck D", number="+13603621737")
        flow.start([], [contact])
        call = IVRCall.objects.filter(direction=IVRCall.OUTGOING).first()

        # now trigger a hangup
        post_data = dict(CallSid="CallSid", CallStatus="in-progress", CallDuration=20, hangup=1)
        response = self.client.post(reverse("ivr.ivrcall_handle", args=[call.pk]), post_data)
        self.assertEqual(200, response.status_code)

    @patch("temba.ivr.clients.TwilioClient", MockTwilioClient)
    @patch("twilio.request_validator.RequestValidator", MockRequestValidator)
    def test_ivr_recording(self):

        # create our ivr setup
        self.org.connect_twilio("TEST_SID", "TEST_TOKEN", self.admin)
        self.org.save()
        self.import_file("capture_recording")
        flow = Flow.objects.filter(name="Capture Recording").first()

        # start our flow
        contact = self.create_contact("Chuck D", number="+13603621737")
        run, = flow.start([], [contact])
        call = IVRCall.objects.get(direction=IVRCall.OUTGOING)

        # after a call is picked up, twilio will call back to our server
        post_data = dict(CallSid="CallSid", CallStatus="in-progress", CallDuration=20)
        response = self.client.post(reverse("ivr.ivrcall_handle", args=[call.pk]), post_data)
        self.assertContains(response, "<Say>Please make a recording after the tone.</Say>")
        self.assertEqual(response._headers["content-type"][1], "text/xml; charset=utf-8")

        self.assertEqual(ChannelLog.objects.all().count(), 2)
        channel_log = ChannelLog.objects.last()
        self.assertEqual(channel_log.connection.id, call.id)
        self.assertEqual(channel_log.description, "Incoming request for call")

        # simulate the caller making a recording and then hanging up, first they'll give us the
        # recording (they give us a call status of completed at the same time)
        from temba.tests import MockResponse

        with patch("requests.get") as response:
            mock1 = MockResponse(404, "No such file")
            mock2 = MockResponse(200, "Fake Recording Bits")
            mock2.add_header("Content-Type", "audio/x-wav")
            response.side_effect = (mock1, mock2)

            self.client.post(
                reverse("ivr.ivrcall_handle", args=[call.pk]),
                dict(
                    CallStatus="completed",
                    Digits="hangup",
                    RecordingUrl="http://api.twilio.com/ASID/Recordings/SID",
                    RecordingSid="FAKESID",
                ),
            )

        self.assertEqual(ChannelLog.objects.all().count(), 3)
        channel_log = ChannelLog.objects.last()
        self.assertEqual(channel_log.connection.id, call.id)
        self.assertEqual(channel_log.description, "Incoming request for call")

        # we should have captured the recording, and ended the call
        call = IVRCall.objects.get(pk=call.pk)
        self.assertEqual(IVRCall.COMPLETED, call.status)

        # twilio will also send us a final completion message with the call duration (status of completed again)
        self.client.post(
            reverse("ivr.ivrcall_handle", args=[call.pk]), dict(CallStatus="completed", CallDuration="15")
        )

        self.assertEqual(ChannelLog.objects.all().count(), 4)
        channel_log = ChannelLog.objects.last()
        self.assertEqual(channel_log.connection.id, call.id)
        self.assertEqual(channel_log.description, "Updated call status")

        call = IVRCall.objects.get(pk=call.pk)
        self.assertEqual(IVRCall.COMPLETED, call.status)
        self.assertEqual(15, call.duration)

        messages = Msg.objects.filter(msg_type=IVR).order_by("pk")
        self.assertEqual(4, messages.count())
        self.assertEqual(4, self.org.get_credits_used())

        # we should have played a recording from the contact back to them
        outbound_msg = messages[1]
        self.assertTrue(outbound_msg.attachments[0].startswith("audio/x-wav:http://"))
        self.assertTrue(outbound_msg.attachments[0].endswith(".wav"))
        self.assertTrue(outbound_msg.text.startswith("http://"))
        self.assertTrue(outbound_msg.text.endswith(".wav"))

        media_msg = messages[2]
        self.assertTrue(media_msg.attachments[0].startswith("audio/x-wav:http://"))
        self.assertTrue(media_msg.attachments[0].endswith(".wav"))
        self.assertEqual("Played contact recording", media_msg.text)

        (host, directory, filename) = media_msg.attachments[0].rsplit("/", 2)
        recording = "%s/%s/%s/media/%s/%s" % (
            settings.MEDIA_ROOT,
            settings.STORAGE_ROOT_DIR,
            self.org.pk,
            directory,
            filename,
        )
        self.assertTrue(os.path.isfile(recording))

        # should have 4 steps and 4 messages
        run.refresh_from_db()
        self.assertEqual(len(run.path), 4)
        self.assertEqual(len(run.get_messages()), 4)

    @patch("jwt.encode")
    @patch("nexmo.Client.create_application")
    def test_ivr_recording_with_nexmo(self, mock_create_application, mock_jwt):
        mock_create_application.return_value = dict(id="app-id", keys=dict(private_key="private-key\n"))
        mock_jwt.return_value = b"Encoded data"

        # connect Nexmo
        self.org.connect_nexmo("123", "456", self.admin)
        self.org.save()

        self.channel.channel_type = "NX"
        self.channel.save()

        self.import_file("capture_recording")
        flow = Flow.objects.filter(name="Capture Recording").first()

        # start our flow
        contact = self.create_contact("Chuck D", number="+13603621737")

        with patch("requests.post") as mock_create_call:
            mock_create_call.return_value = MockResponse(200, json.dumps(dict(uuid="12345")))

            run, = flow.start([], [contact])

        call = IVRCall.objects.filter(direction=IVRCall.OUTGOING).first()

        callback_url = reverse("ivr.ivrcall_handle", args=[call.pk])

        # after a call is picked up, nexmo will send a get call back to our server
        response = self.client.post(
            callback_url, content_type="application/json", data=json.dumps(dict(status="ringing", duration=0))
        )

        self.assertEqual(ChannelLog.objects.all().count(), 3)
        channel_logs = ChannelLog.objects.order_by("id").all()
        self.assertListEqual(
            [channel_log.description for channel_log in channel_logs],
            ["Call queued internally", "Started call", "Incoming request for call"],
        )

        channel_log = channel_logs[0]
        self.assertEqual(channel_log.connection.id, call.id)

        # we have a talk action
        self.assertContains(response, '"action": "talk",')
        self.assertContains(response, '"text": "Please make a recording after the tone."')

        # we have a record action
        self.assertContains(response, '"action": "record"')
        self.assertContains(response, '"eventUrl": ["https://%s%s"]' % (self.channel.callback_domain, callback_url))

        # we have an input to redirect so we save the recording
        # hack to make the recording look synchronous for our flows
        self.assertContains(response, '"action": "input"')
        self.assertContains(
            response, '"eventUrl": ["https://%s%s?save_media=1"]' % (self.channel.callback_domain, callback_url)
        )

        # any request with has_event params return empty content response
        response = self.client.post(f"{callback_url}?has_event=1", content_type="application/json")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json().get("description"), "Updated call status")
        self.assertEqual(response.json().get("call").get("status"), "Ringing")

        with patch("temba.utils.nexmo.NexmoClient.download_recording") as mock_download_recording:
            mock_download_recording.return_value = MockResponse(
                200, "SOUND_BITS", headers={"Content-Type": "audio/x-wav"}
            )

            # async callback to tell us the recording url
            response = self.client.post(
                callback_url,
                content_type="application/json",
                data=json.dumps(dict(recording_url="http://example.com/allo.wav")),
            )

            self.assertEqual(response.json().get("message"), "Saved media url")
            self.assertEqual(ChannelLog.objects.all().count(), 5)
            channel_log = ChannelLog.objects.last()
            self.assertEqual(channel_log.connection.id, call.id)
            self.assertEqual(channel_log.description, "Saved media url")

            # hack input call back to tell us to save the recording and an empty input submission
            self.client.post(
                "%s?save_media=1" % callback_url,
                content_type="application/json",
                data=json.dumps(dict(status="answered", duration=2, dtmf="")),
            )

            self.assertEqual(ChannelLog.objects.all().count(), 7)
            channel_log = ChannelLog.objects.last()
            self.assertEqual(channel_log.connection.id, call.id)
            self.assertEqual(channel_log.description, "Incoming request for call")

            log = ChannelLog.objects.filter(description="Downloaded media", connection_id=call.id).first()
            self.assertIsNotNone(log)

            # our log should be the newly saved url
            self.assertIn("http", log.response)

        # nexmo will also send us a final completion message with the call duration
        self.client.post(
            reverse("ivr.ivrcall_handle", args=[call.pk]),
            content_type="application/json",
            data=json.dumps({"status": "completed", "duration": "15"}),
        )

        self.assertEqual(ChannelLog.objects.all().count(), 8)
        channel_log = ChannelLog.objects.last()
        self.assertEqual(channel_log.connection.id, call.id)
        self.assertEqual(channel_log.description, "Updated call status")

        # we should have captured the recording, and ended the call
        call = IVRCall.objects.get(pk=call.pk)
        self.assertEqual(IVRCall.COMPLETED, call.status)
        self.assertEqual(15, call.duration)

        messages = Msg.objects.filter(msg_type=IVR).order_by("pk")
        self.assertEqual(4, messages.count())
        self.assertEqual(4, self.org.get_credits_used())

        # we should have played a recording from the contact back to them
        outbound_msg = messages[1]
        self.assertTrue(outbound_msg.attachments[0].startswith("audio/x-wav:http://"))
        self.assertTrue(outbound_msg.attachments[0].endswith(".wav"))
        self.assertTrue(outbound_msg.text.startswith("http://"))
        self.assertTrue(outbound_msg.text.endswith(".wav"))

        media_msg = messages[2]
        self.assertTrue(media_msg.attachments[0].startswith("audio/x-wav:http://"))
        self.assertTrue(media_msg.attachments[0].endswith(".wav"))
        self.assertEqual("Played contact recording", media_msg.text)

        (host, directory, filename) = media_msg.attachments[0].rsplit("/", 2)
        recording = "%s/%s/%s/media/%s/%s" % (
            settings.MEDIA_ROOT,
            settings.STORAGE_ROOT_DIR,
            self.org.pk,
            directory,
            filename,
        )
        self.assertTrue(os.path.isfile(recording))

        # should have 4 steps and 4 messages
        run.refresh_from_db()
        self.assertEqual(len(run.path), 4)
        self.assertEqual(len(run.get_messages()), 4)

        # create a valid call first
        with patch("requests.post") as mock_create_call:
            mock_create_call.return_value = MockResponse(200, json.dumps(dict(uuid="12345")))

            flow.start([], [contact], restart_participants=True)

        # now create an errored call
        with patch("requests.post") as mock_create_call:
            mock_create_call.side_effect = Exception("Kab00m!")
            nexmo_client = self.org.get_nexmo_client()
            with self.assertRaises(IVRException):
                nexmo_client.start_call(call, "+13603621737", self.channel.address, None)

        call.refresh_from_db()
        self.assertEqual(ChannelConnection.FAILED, call.status)

        # check that our channel logs are there
        response = self.client.get(reverse("channels.channellog_list", args=[self.channel.uuid]) + "?connections=1")
        self.assertContains(response, "15 seconds")
        self.assertContains(response, "2 results")

        # our channel logs with the error flag
        response = self.client.get(
            reverse("channels.channellog_list", args=[self.channel.uuid]) + "?connections=1&errors=1"
        )
        self.assertContains(response, "warning")
        self.assertContains(response, "1 result")

        # view the errored call read page
        response = self.client.get(reverse("channels.channellog_connection", args=[call.id]))
        self.assertContains(response, "https://api.nexmo.com/v1/calls")
        self.assertContains(response, "Kab00m!")

    @patch("temba.ivr.clients.TwilioClient", MockTwilioClient)
    @patch("twilio.request_validator.RequestValidator", MockRequestValidator)
    def test_ivr_subflow(self):

        with patch("temba.flows.models.current_app.send_task") as start_call:
            self.org.connect_twilio("TEST_SID", "TEST_TOKEN", self.admin)
            self.org.save()

            self.get_flow("ivr_subflow")
            parent_flow = Flow.objects.filter(name="Parent Flow").first()

            ben = self.create_contact("Ben", "+12345")
            parent_flow.start(groups=[], contacts=[ben])
            call = IVRCall.objects.get(direction=IVRCall.OUTGOING)

            post_data = dict(CallSid="CallSid", CallStatus="in-progress", CallDuration=20)
            response = self.client.post(reverse("ivr.ivrcall_handle", args=[call.pk]), post_data)

            # should have two runs, but still one call
            self.assertEqual(1, IVRCall.objects.all().count())
            self.assertEqual(2, FlowRun.objects.filter(is_active=True).count())

            parent_run, child_run = FlowRun.objects.order_by("id")
            self.assertEqual(len(parent_run.path), 2)
            self.assertEqual(len(child_run.path), 0)

            # should give us a redirect, but without the empty flag
            self.assertContains(response, "Redirect")
            self.assertNotContains(response, "empty=1")

            # they should call back to us
            response = self.client.post(reverse("ivr.ivrcall_handle", args=[call.pk]), post_data)

            # which should result in two more messages and a gather
            self.assertContains(response, "Gather")
            self.assertContains(response, "This is a child flow")
            self.assertContains(response, "What is your favorite color?")

            self.assertEqual(3, Msg.objects.all().count())

            parent_run, child_run = FlowRun.objects.order_by("id")
            self.assertEqual(len(parent_run.path), 2)
            self.assertEqual(len(child_run.path), 2)

            # answer back with red
            response = self.client.post(
                reverse("ivr.ivrcall_handle", args=[call.pk]), dict(Digits=1, CallStatus="in-progress")
            )

            self.assertContains(response, "Thanks, returning to the parent flow now.")
            self.assertContains(response, "Redirect")
            self.assertContains(response, "resume=1")

            # back down to our original run
            self.assertEqual(1, FlowRun.objects.filter(is_active=True).count())
            run = FlowRun.objects.filter(is_active=True).first()

            response = self.client.post(reverse("ivr.ivrcall_handle", args=[call.pk]) + "?resume=1", post_data)
            self.assertContains(response, "In the child flow you picked Red.")
            self.assertNotContains(response, "Redirect")

            # make sure we only called to start the call once
            self.assertEqual(1, start_call.call_count)

            # since we are an ivr flow, we aren't complete until the provider notifies us
            run.refresh_from_db()
            self.assertFalse(run.is_completed())

    @patch("temba.ivr.clients.TwilioClient", MockTwilioClient)
    @patch("twilio.request_validator.RequestValidator", MockRequestValidator)
    def test_ivr_start_flow(self):
        self.org.connect_twilio("TEST_SID", "TEST_TOKEN", self.admin)
        self.org.save()

        msg_flow = self.get_flow("ivr_child_flow")
        ivr_flow = Flow.objects.get(name="Voice Flow")

        # start macklemore in the flow
        ben = self.create_contact("Ben", "+12345")
        run, = ivr_flow.start(groups=[], contacts=[ben])
        call = IVRCall.objects.get(direction=IVRCall.OUTGOING)

        post_data = dict(CallSid="CallSid", CallStatus="in-progress", CallDuration=20)
        self.client.post(reverse("ivr.ivrcall_handle", args=[call.pk]), post_data)

        run.refresh_from_db()
        self.assertEqual(len(run.path), 2)

        # press 1
        response = self.client.post(
            reverse("ivr.ivrcall_handle", args=[call.pk]), dict(Digits=1, CallStatus="in-progress")
        )
        self.assertContains(response, "<Say>I just sent you a text.")

        # should have also started a new flow and received our text
        self.assertTrue(FlowRun.objects.filter(contact=ben, flow=msg_flow).first())
        self.assertTrue(Msg.objects.filter(direction=IVRCall.OUTGOING, contact=ben, text="You said foo!").first())

    @patch("temba.ivr.clients.TwilioClient", MockTwilioClient)
    @patch("twilio.request_validator.RequestValidator", MockRequestValidator)
    def test_ivr_call_redirect(self):
        self.org.connect_twilio("TEST_SID", "TEST_TOKEN", self.admin)
        self.org.save()

        # import our flows
        self.get_flow("ivr_call_redirect")

        flow_1 = Flow.objects.get(name="Call Number 1")
        Flow.objects.get(name="Call Number 2")

        shawn = self.create_contact("Marshawn", "+24")
        flow_1.start(groups=[], contacts=[shawn])

        # we should have one call now
        calls = IVRCall.objects.filter(direction=IVRCall.OUTGOING)
        self.assertEqual(1, calls.count())

        # once the first set of actions are processed, we'll initiate a second call
        post_data = dict(CallSid="CallSid", CallStatus="in-progress", CallDuration=20)
        self.client.post(reverse("ivr.ivrcall_handle", args=[calls[0].pk]), post_data)

        calls = IVRCall.objects.filter(direction=IVRCall.OUTGOING).order_by("created_on")
        self.assertEqual(1, calls.count())
        call = calls[0]

        # complete the call
        post_data = dict(CallSid="CallSid", CallStatus="completed", CallDuration=30)
        self.client.post(reverse("ivr.ivrcall_handle", args=[call.pk]), post_data)
        call.refresh_from_db()
        self.assertEqual(IVRCall.COMPLETED, call.status)

    @patch("temba.ivr.clients.TwilioClient", MockTwilioClient)
    @patch("twilio.request_validator.RequestValidator", MockRequestValidator)
    def test_text_trigger_ivr(self):
        self.org.connect_twilio("TEST_SID", "TEST_TOKEN", self.admin)
        self.org.save()

        # import our flows
        self.get_flow("text_trigger_ivr")

        msg_flow = Flow.objects.get(name="Message Flow - Parent")
        Flow.objects.get(name="IVR Flow - Child")

        shawn = self.create_contact("Marshawn", "+24")
        msg_flow.start(groups=[], contacts=[shawn])

        # our message flow triggers an ivr flow
        self.assertEqual(2, FlowRun.objects.all().count())
        self.assertEqual(1, IVRCall.objects.filter(direction=IVRCall.OUTGOING).count())

        # one text message
        self.assertEqual(1, Msg.objects.all().count())

        # now twilio calls back to initiate the triggered call
        call = IVRCall.objects.filter(direction=IVRCall.OUTGOING).first()
        post_data = dict(CallSid="CallSid", CallStatus="in-progress", CallDuration=20)
        self.client.post(reverse("ivr.ivrcall_handle", args=[call.pk]), post_data)

        # still same number of runs and calls, but one more (ivr) message
        self.assertEqual(2, FlowRun.objects.all().count())
        self.assertEqual(1, IVRCall.objects.filter(direction=IVRCall.OUTGOING).count())
        self.assertEqual(2, Msg.objects.all().count())

    @patch("temba.ivr.clients.TwilioClient", MockTwilioClient)
    @patch("twilio.request_validator.RequestValidator", MockRequestValidator)
    def test_non_blocking_rule_ivr(self):

        self.org.connect_twilio("TEST_SID", "TEST_TOKEN", self.admin)
        self.org.save()

        # flow goes: passive -> recording -> msg
        flow = self.get_flow("non_blocking_rule_ivr")

        print(json.dumps(flow.as_json(), indent=2))

        # start marshall in the flow
        eminem = self.create_contact("Eminem", "+12345")
        run, = flow.start(groups=[], contacts=[eminem])
        call = IVRCall.objects.filter(direction=IVRCall.OUTGOING).first()
        self.assertNotEqual(call, None)

        # after a call is picked up, twilio will call back to our server
        post_data = dict(CallSid="CallSid", CallStatus="in-progress", CallDuration=20)
        self.client.post(reverse("ivr.ivrcall_handle", args=[call.pk]), post_data)

        # should have two steps so far, right up to the recording
        run.refresh_from_db()
        self.assertEqual(len(run.path), 2)

        # no outbound yet
        self.assertEqual(None, Msg.objects.filter(direction="O", contact=eminem).first())

        # now pretend we got a recording
        from temba.tests import MockResponse

        with patch("requests.get") as response:
            mock = MockResponse(200, "Fake Recording Bits")
            mock.add_header("Content-Disposition", 'filename="audio0000.wav"')
            mock.add_header("Content-Type", "audio/x-wav")
            response.return_value = mock

            self.client.post(
                reverse("ivr.ivrcall_handle", args=[call.pk]),
                dict(
                    CallStatus="in-progress",
                    Digits="#",
                    RecordingUrl="http://api.twilio.com/ASID/Recordings/SID",
                    RecordingSid="FAKESID",
                ),
            )

        # now we should have an outbound message
        self.assertEqual("Hi there Eminem", Msg.objects.filter(direction="O", contact=eminem).first().text)

    @patch("temba.ivr.clients.TwilioClient", MockTwilioClient)
    @patch("twilio.request_validator.RequestValidator", MockRequestValidator)
    def test_ivr_digit_gather(self):

        self.org.connect_twilio("TEST_SID", "TEST_TOKEN", self.admin)
        self.org.save()

        # import an ivr flow
        self.import_file("gather_digits")

        # make sure our flow is there as expected
        flow = Flow.objects.filter(name="Gather Digits").first()

        # start our flow
        eric = self.create_contact("Eric Newcomer", number="+13603621737")
        flow.start([], [eric])
        call = IVRCall.objects.filter(direction=IVRCall.OUTGOING).first()

        expiration = call.runs.all().first().expires_on
        next3days = timezone.now() + timedelta(days=3)
        # our run should have a initial expiration for the max 7 days
        self.assertTrue(expiration > next3days)

        # after a call is picked up, twilio will call back to our server
        post_data = dict(CallSid="CallSid", CallStatus="in-progress", CallDuration=20)
        response = self.client.post(reverse("ivr.ivrcall_handle", args=[call.pk]), post_data)

        # once the call is handled, it should have an expiration as the configured for the IVR flow
        expiration = call.runs.all().first().expires_on
        self.assertTrue(expiration < next3days)

        # make sure we send the finishOnKey attribute to twilio
        self.assertContains(response, 'finishOnKey="#"')

        # make sure we have a redirect to deal with empty responses
        self.assertContains(response, "empty=1")

        # only have our initial outbound message
        self.assertEqual(1, Msg.objects.all().count())

        expiration = call.runs.all().first().expires_on

        # simulate a gather timeout
        post_data["Digits"] = ""
        response = self.client.post(reverse("ivr.ivrcall_handle", args=[call.pk]) + "?empty=1", post_data)

        # we should be routed through 'other' case
        self.assertContains(response, "Please enter a number")

        # should now only have two outbound messages and no inbound ones
        self.assertEqual(2, Msg.objects.filter(direction="O").count())
        self.assertEqual(0, Msg.objects.filter(direction="I").count())

        # verify that our expiration didn't change by way of the timeout
        self.assertEqual(expiration, call.runs.all().first().expires_on)

    @patch("nexmo.Client.create_application")
    @patch("nexmo.Client.create_call")
    def test_ivr_digital_gather_with_nexmo(self, mock_create_call, mock_create_application):
        mock_create_application.return_value = dict(id="app-id", keys=dict(private_key="private-key\n"))
        mock_create_call.return_value = dict(uuid="12345")

        self.org.connect_nexmo("123", "456", self.admin)
        self.org.save()

        self.channel.channel_type = "NX"
        self.channel.save()

        # import an ivr flow
        self.import_file("gather_digits")

        # make sure our flow is there as expected
        flow = Flow.objects.filter(name="Gather Digits").first()

        # start our flow
        eric = self.create_contact("Eric Newcomer", number="+13603621737")
        flow.start([], [eric])
        call = IVRCall.objects.filter(direction=IVRCall.OUTGOING).first()

        callback_url = reverse("ivr.ivrcall_handle", args=[call.pk])

        # after a call is picked up, nexmo will send a callback to our server
        response = self.client.post(
            callback_url, content_type="application/json", data=json.dumps(dict(status="answered", duration=0))
        )

        call.refresh_from_db()
        self.assertEqual(ChannelConnection.IN_PROGRESS, call.status)

        self.assertTrue(
            dict(action="talk", bargeIn=True, text="Enter your phone number followed by the pound sign.")
            in response.json()
        )

        # we have an input to collect the digits
        self.assertContains(response, '"action": "input",')

        # make sure we set submitOnHash to true nexmo
        self.assertContains(response, '"submitOnHash": true,')

        self.assertContains(response, '"eventUrl": ["https://%s%s"]' % (self.channel.callback_domain, callback_url))

    @patch("jwt.encode")
    @patch("requests.put")
    @patch("nexmo.Client.create_application")
    def test_expiration_hangup(self, mock_create_application, mock_put, mock_jwt):
        mock_create_application.return_value = dict(id="app-id", keys=dict(private_key="private-key\n"))
        mock_jwt.return_value = b"Encoded data"

        request = MagicMock()
        request.body = json.dumps(dict(call_id="12345"))
        request.url = "http://api.nexmo.com/../"
        request.method = "PUT"

        self.org.connect_nexmo("123", "456", self.admin)
        self.org.save()

        self.channel.channel_type = "NX"
        self.channel.save()

        # import an ivr flow
        self.import_file("gather_digits")

        # make sure our flow is there as expected
        flow = Flow.objects.filter(name="Gather Digits").first()

        # start our flow
        eric = self.create_contact("Eric Newcomer", number="+13603621737")

        with patch("requests.post") as mock_create_call:
            mock_create_call.return_value = MockResponse(200, json.dumps(dict(call=dict(uuid="12345"))))

            flow.start([], [eric])

        # since it hasn't started, our call should be pending and run should have an expiration for the max 7 days
        call = IVRCall.objects.filter(direction=IVRCall.OUTGOING).first()
        run = FlowRun.objects.get()
        self.assertEqual(ChannelConnection.WIRED, call.status)
        next3days = timezone.now() + timedelta(days=3)
        self.assertTrue(run.expires_on > next3days)

        # trigger a status update to show the call was answered
        callback_url = reverse("ivr.ivrcall_handle", args=[call.pk])
        self.client.post(
            callback_url, content_type="application/json", data=json.dumps(dict(status="answered", duration=0))
        )

        call.refresh_from_db()
        run.refresh_from_db()
        self.assertEqual(ChannelConnection.IN_PROGRESS, call.status)
        self.assertIsNotNone(run.expires_on)

        # now expire our run
        mock_put.return_value = MagicMock(call_id="12345", request=request, status_code=200, content="response")
        run.expire()

        mock_put.assert_called()
        call = IVRCall.objects.filter(direction=IVRCall.OUTGOING).first()
        self.assertEqual(ChannelConnection.INTERRUPTED, call.status)

        # call initiation, answer, and timeout should both be logged
        self.assertEqual(4, ChannelLog.objects.filter(connection=call).count())
        self.assertIsNotNone(call.ended_on)

    @patch("nexmo.Client.create_application")
    @patch("nexmo.Client.create_call")
    def test_ivr_subflow_with_nexmo(self, mock_create_call, mock_create_application):
        mock_create_application.return_value = dict(id="app-id", keys=dict(private_key="private-key\n"))
        mock_create_call.return_value = dict(uuid="12345")

        self.org.connect_nexmo("123", "456", self.admin)
        self.org.save()

        self.channel.channel_type = "NX"
        self.channel.save()

        # import an ivr flow
        self.import_file("ivr_subflow")

        parent_flow = Flow.objects.filter(name="Parent Flow").first()
        # child_flow = Flow.objects.filter(name='Child Flow').first()

        eric = self.create_contact("Eric Newcomer", number="+13603621737")
        parent_flow.start([], [eric])
        call = IVRCall.objects.filter(direction=IVRCall.OUTGOING).first()

        callback_url = reverse("ivr.ivrcall_handle", args=[call.pk])

        # after a call is picked up, nexmo will send a get call back to our server
        response = self.client.post(
            callback_url, content_type="application/json", data=json.dumps(dict(status="ringing", duration=0))
        )

        response_json = response.json()
        callback_url = response_json[1]["eventUrl"][0]

        self.assertTrue(dict(action="talk", bargeIn=True, text="Hi there! This is my voice flow.") in response_json)

        response = self.client.post(
            callback_url, content_type="application/json", data=json.dumps(dict(status="ringing", duration=0))
        )

        response_json = response.json()
        callback_url = response_json[2]["eventUrl"][0]

        self.assertTrue(
            dict(
                action="talk", bargeIn=True, text="What is your favorite color? 1 for Red, 2 for green or 3 for blue."
            )
            in response_json
        )

        # press 1
        response = self.client.post(callback_url, content_type="application/json", data=json.dumps(dict(dtmf="1")))
        response_json = response.json()
        callback_url = response_json[1]["eventUrl"][0]

        self.assertTrue(
            dict(action="talk", bargeIn=True, text="Thanks, returning to the parent flow now.") in response_json
        )

        response = self.client.post(callback_url, content_type="application/json", data=json.dumps(dict(dtmf="")))

        response_json = response.json()

        self.assertTrue(
            dict(action="talk", bargeIn=False, text="In the child flow you picked Red. I think that is a fine choice.")
            in response_json
        )

        # our flow should remain active until we get completion
        self.assertEqual(1, FlowRun.objects.filter(is_active=True).count())

        nexmo_uuid = self.org.config["NEXMO_UUID"]
        post_data = dict()
        post_data["status"] = "completed"
        post_data["duration"] = "0"
        post_data["uuid"] = call.external_id
        response = self.client.post(
            reverse("handlers.nexmo_call_handler", args=["event", nexmo_uuid]) + "?has_event=1",
            json.dumps(post_data),
            content_type="application/json",
        )

        self.assertContains(response, "Updated call status")

        # now that we got notfied from the provider, we have no active runs
        self.assertEqual(0, FlowRun.objects.filter(is_active=True).count())
        mock_create_call.assert_called_once()

    @patch("temba.ivr.clients.TwilioClient", MockTwilioClient)
    @patch("twilio.request_validator.RequestValidator", MockRequestValidator)
    def test_ivr_flow(self):

        # should be able to create an ivr flow
        self.assertTrue(self.org.supports_ivr())
        self.assertTrue(self.admin.groups.filter(name="Beta"))
        self.assertContains(self.client.get(reverse("flows.flow_create")), "Phone Call")

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
        config = {
            Channel.CONFIG_SEND_URL: "https://api.twilio.com",
            Channel.CONFIG_ACCOUNT_SID: "TEST_SID",
            Channel.CONFIG_AUTH_TOKEN: "TEST_TOKEN",
        }
        channel = Channel.create(
            self.org, self.org.get_user(), "BR", "TW", "+558299990000", "+558299990000", config, "AC"
        )
        self.assertEqual(channel.org, self.org)
        self.assertEqual(channel.address, "+558299990000")

        self.assertIsNotNone(channel.get_twiml_client())

        # import an ivr flow
        self.import_file("call_me_maybe")

        # make sure our flow is there as expected
        flow = Flow.objects.filter(name="Call me maybe").first()
        self.assertEqual("callme", flow.triggers.filter(trigger_type="K").first().keyword)

        eric = self.create_contact("Eric Newcomer", number="+13603621737")
        run, = flow.start([], [eric], restart_participants=True)

        # we should have an outbound ivr call now
        call = IVRCall.objects.filter(direction=IVRCall.OUTGOING).first()

        self.assertEqual(timedelta(seconds=0), call.get_duration())
        self.assertIsNotNone(call)
        self.assertEqual("CallSid", call.external_id)

        # after a call is picked up, twilio will call back to our server
        post_data = dict(CallSid="CallSid", CallStatus="in-progress", CallDuration=20)
        response = self.client.post(reverse("ivr.ivrcall_handle", args=[call.pk]), post_data)

        self.assertContains(
            response, "<Say>Would you like me to call you? Press one for yes, two for no, or three for maybe.</Say>"
        )
        self.assertEqual(1, Msg.objects.filter(msg_type=IVR).count())
        self.assertEqual(1, self.org.get_credits_used())

        # make sure a message from the person on the call goes to the
        # inbox since our flow doesn't handle text messages
        msg = self.create_msg(direction="I", contact=eric, text="message during phone call")
        self.assertFalse(Flow.find_and_handle(msg)[0])

        # updated our status and duration accordingly
        call = IVRCall.objects.get(pk=call.pk)
        self.assertEqual(20, call.duration)
        self.assertEqual(IVRCall.IN_PROGRESS, call.status)

        # don't press any numbers, but # instead
        response = self.client.post(
            reverse("ivr.ivrcall_handle", args=[call.pk]) + "?empty=1", dict(CallStatus="in-progress")
        )
        self.assertContains(response, "<Say>Press one, two, or three. Thanks.</Say>")
        self.assertEqual(3, self.org.get_credits_used())

        # press the number 4 (unexpected)
        response = self.client.post(
            reverse("ivr.ivrcall_handle", args=[call.pk]), dict(Digits=4, CallStatus="in-progress")
        )

        # our inbound message should be handled
        msg = Msg.objects.filter(text="4", msg_type=IVR).order_by("-created_on").first()
        self.assertEqual("H", msg.status)

        self.assertContains(response, "<Say>Press one, two, or three. Thanks.</Say>")
        self.assertEqual(5, self.org.get_credits_used())

        # two more messages, one inbound and it's response
        self.assertEqual(4, Msg.objects.filter(msg_type=IVR).count())

        # now let's have them press the number 3 (for maybe)
        response = self.client.post(
            reverse("ivr.ivrcall_handle", args=[call.pk]), dict(CallStatus="in-progress", Digits=3)
        )
        self.assertContains(response, "<Say>This might be crazy.</Say>")
        messages = Msg.objects.filter(msg_type=IVR).order_by("pk")
        self.assertEqual(6, messages.count())
        self.assertEqual(7, self.org.get_credits_used())

        run.refresh_from_db()
        for msg in messages:
            self.assertIn(msg, run.get_messages())

        # twilio would then disconnect the user and notify us of a completed call
        self.client.post(reverse("ivr.ivrcall_handle", args=[call.pk]), dict(CallStatus="completed"))
        call = IVRCall.objects.get(pk=call.pk)
        self.assertEqual(IVRCall.COMPLETED, call.status)
        self.assertFalse(FlowRun.objects.filter(connection=call).first().is_active)
        self.assertIsNotNone(call.ended_on)

        self.assertEqual(flow.get_run_stats()["completed"], 1)

        # should still have no active runs
        self.assertEqual(0, FlowRun.objects.filter(is_active=True).count())

        # we shouldn't have any outbound pending messages, they are all considered delivered
        self.assertEqual(0, Msg.objects.filter(direction=OUTGOING, status=PENDING, msg_type=IVR).count())

        self.releaseIVRCalls(delete=True)

        # try sending callme trigger
        from temba.msgs.models import INCOMING

        msg = self.create_msg(direction=INCOMING, contact=eric, text="callme")

        # make sure if we are started with a message we still create a normal voice run
        run, = flow.start([], [eric], restart_participants=True, start_msg=msg)
        run.refresh_from_db()

        # we should have an outbound ivr call now, and no steps yet
        call = IVRCall.objects.filter(direction=IVRCall.OUTGOING).first()
        self.assertIsNotNone(call)
        self.assertEqual(len(run.path), 0)

        # after a call is picked up, twilio will call back to our server
        post_data = dict(CallSid="CallSid", CallStatus="in-progress", CallDuration=20)
        self.client.post(reverse("ivr.ivrcall_handle", args=[call.pk]), post_data)

        # should have two flow steps (the outgoing messages, and the step to handle the response)
        out = Msg.objects.get(direction=IVRCall.OUTGOING, msg_type=IVR)
        run.refresh_from_db()
        self.assertEqual(len(run.path), 2)
        self.assertIn(out, run.get_messages())

        # try updating our status to completed
        flow.start([], [eric])
        call = IVRCall.objects.filter(direction=IVRCall.OUTGOING).order_by("-pk").first()
        call.update_status("completed", 30, "T")
        call.save()
        call.refresh_from_db()

        self.assertEqual(call.duration, 30)

        # now look at implied duration
        call.update_status("in-progress", None, "T")
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

    @patch("temba.ivr.clients.TwilioClient", MockTwilioClient)
    @patch("twilio.request_validator.RequestValidator", MockRequestValidator)
    def test_ivr_status_update(self):
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

        # connect it and check our client is configured
        self.org.connect_twilio("TEST_SID", "TEST_TOKEN", self.admin)
        self.org.save()

        # twiml api config
        config = {
            Channel.CONFIG_SEND_URL: "https://api.twilio.com",
            Channel.CONFIG_ACCOUNT_SID: "TEST_SID",
            Channel.CONFIG_AUTH_TOKEN: "TEST_TOKEN",
        }
        Channel.create(self.org, self.org.get_user(), "BR", "TW", "+558299990000", "+558299990000", config, "AC")

        # import an ivr flow
        self.import_file("call_me_maybe")

        # make sure our flow is there as expected
        flow = Flow.objects.filter(name="Call me maybe").first()

        # now pretend we are a normal caller
        eric = self.create_contact("Eric Newcomer", number="+13603621737")
        flow.start([], [eric], restart_participants=True)

        # we should have an outbound ivr call now
        call = IVRCall.objects.filter(direction=IVRCall.OUTGOING).first()

        test_status_update(call, "queued", IVRCall.WIRED, "T")
        test_status_update(call, "ringing", IVRCall.RINGING, "T")
        test_status_update(call, "canceled", IVRCall.CANCELED, "T")
        test_status_update(call, "busy", IVRCall.BUSY, "T")
        test_status_update(call, "failed", IVRCall.FAILED, "T")
        test_status_update(call, "no-answer", IVRCall.NO_ANSWER, "T")
        test_status_update(call, "in-progress", IVRCall.IN_PROGRESS, "T")
        test_status_update(call, "completed", IVRCall.COMPLETED, "T")

        test_status_update(call, "ringing", IVRCall.RINGING, "NX")
        test_status_update(call, "answered", IVRCall.IN_PROGRESS, "NX")
        test_status_update(call, "completed", IVRCall.COMPLETED, "NX")
        test_status_update(call, "failed", IVRCall.FAILED, "NX")
        test_status_update(call, "unanswered", IVRCall.NO_ANSWER, "NX")
        test_status_update(call, "timeout", IVRCall.NO_ANSWER, "NX")
        test_status_update(call, "busy", IVRCall.BUSY, "NX")
        test_status_update(call, "rejected", IVRCall.BUSY, "NX")
        test_status_update(call, "answered", IVRCall.IN_PROGRESS, "NX")
        test_status_update(call, "completed", IVRCall.COMPLETED, "NX")

        # set run as inactive and try to update status for ivr_retries
        self.client.post(reverse("ivr.ivrcall_handle", args=[call.pk]), dict(CallStatus="completed"))

        self.assertRaises(ValueError, test_status_update, call, "busy", IVRCall.BUSY, "T")

    @patch("temba.ivr.clients.TwilioClient", MockTwilioClient)
    @patch("twilio.request_validator.RequestValidator", MockRequestValidator)
    def test_rule_first_ivr_flow(self):
        # connect it and check our client is configured
        self.org.connect_twilio("TEST_SID", "TEST_TOKEN", self.admin)
        self.org.save()

        # import an ivr flow
        flow = self.get_flow("rule_first_ivr")

        user_settings = self.admin.get_settings()
        user_settings.tel = "+18005551212"
        user_settings.save()

        # start our flow
        eric = self.create_contact("Eric Newcomer", number="+13603621737")
        flow.start([], [eric])

        # we should have an outbound ivr call now
        call = IVRCall.objects.filter(direction=IVRCall.OUTGOING).first()

        self.assertEqual(timedelta(seconds=0), call.get_duration())
        self.assertIsNotNone(call)
        self.assertEqual("CallSid", call.external_id)

        # after a call is picked up, twilio will call back to our server
        post_data = dict(CallSid="CallSid", CallStatus="in-progress", CallDuration=20)
        response = self.client.post(reverse("ivr.ivrcall_handle", args=[call.pk]), post_data)
        self.assertContains(response, "<Say>Thanks for calling!</Say>")

        # make sure a message from the person on the call goes to the
        # inbox since our flow doesn't handle text messages
        msg = self.create_msg(direction="I", contact=eric, text="message during phone call")
        self.assertFalse(Flow.find_and_handle(msg)[0])

    @patch("temba.ivr.clients.TwilioClient", MockTwilioClient)
    @patch("twilio.request_validator.RequestValidator", MockRequestValidator)
    def test_incoming_call(self):

        # connect it and check our client is configured
        self.org.connect_twilio("TEST_SID", "TEST_TOKEN", self.admin)
        self.org.save()

        # import an ivr flow
        flow = self.get_flow("call_me_maybe")
        flow.version_number = 3
        flow.save()

        # go back to our original version
        flow_json = self.get_flow_json("call_me_maybe")["definition"]
        FlowRevision.objects.create(
            flow=flow, definition=flow_json, spec_version=3, revision=2, created_by=self.admin, modified_by=self.admin
        )

        # create an inbound call
        post_data = dict(
            CallSid="CallSid", CallStatus="ringing", Direction="inbound", From="+250788382382", To=self.channel.address
        )
        response = self.client.post(reverse("handlers.twilio_handler", args=["voice", self.channel.uuid]), post_data)
        self.assertContains(
            response, "<Say>Would you like me to call you? Press one for yes, two for no, or three for maybe.</Say>"
        )

        call = IVRCall.objects.all().first()
        self.assertEqual("+250788382382", call.contact_urn.path)
        self.assertEqual("CallSid", call.external_id)

        status_callback = dict(
            CallSid="CallSid",
            CallbackSource="call-progress-events",
            CallStatus="completed",
            Direction="inbound",
            From="+250788382382",
            To=self.channel.address,
        )
        response = self.client.post(
            reverse("handlers.twilio_handler", args=["voice", self.channel.uuid]), status_callback
        )
        call.refresh_from_db()
        self.assertEqual("D", call.status)

        status_callback = dict(
            CallSid="NoCallMatches",
            CallbackSource="call-progress-events",
            CallStatus="completed",
            Direction="inbound",
            From="+250788382382",
            To=self.channel.address,
        )
        response = self.client.post(
            reverse("handlers.twilio_handler", args=["voice", self.channel.uuid]), status_callback
        )
        self.assertContains(response, "No call found")

        flow.refresh_from_db()
        self.assertEqual(flow.version_number, Flow.FINAL_LEGACY_VERSION)

        # now try an inbound call after remove our channel
        self.channel.is_active = False
        self.channel.save()
        response = self.client.post(reverse("handlers.twilio_handler", args=["voice", self.channel.uuid]), post_data)
        self.assertContains(response, "no channel configured to take this call")
        self.assertEqual(200, response.status_code)

        # no channel found for call handle
        response = self.client.post(reverse("ivr.ivrcall_handle", args=[call.pk]), dict())
        self.assertContains(response, "No channel found", status_code=400)

    @patch("temba.ivr.clients.TwilioClient", MockTwilioClient)
    @patch("twilio.request_validator.RequestValidator", MockRequestValidator)
    def test_incoming_start(self):
        self.org.connect_twilio("TEST_SID", "TEST_TOKEN", self.admin)
        self.org.save()

        self.get_flow("call_me_start")

        # create an inbound call
        post_data = dict(
            CallSid="CallSid", CallStatus="ringing", Direction="inbound", From="+250788382382", To=self.channel.address
        )
        response = self.client.post(reverse("handlers.twilio_handler", args=["voice", self.channel.uuid]), post_data)

        # grab the redirect URL
        redirect_url = re.match(r".*<Redirect>(.*)</Redirect>.*", force_text(response.content)).group(1)

        # get just the path and hit it
        response = self.client.post(urlparse(redirect_url).path, post_data)
        self.assertContains(response, "You are not part of group.")

    @patch("nexmo.Client.update_call")
    @patch("nexmo.Client.create_application")
    def test_incoming_start_nexmo(self, mock_create_application, mock_update_call):
        mock_create_application.return_value = dict(id="app-id", keys=dict(private_key="private-key\n"))
        mock_update_call.return_value = dict(uuid="12345")

        self.org.connect_nexmo("123", "456", self.admin)
        self.org.save()

        self.channel.channel_type = "NX"
        self.channel.save()

        nexmo_uuid = self.org.config["NEXMO_UUID"]

        self.get_flow("call_me_start")

        # create an inbound call
        post_data = dict()
        post_data["from"] = "250788382382"
        post_data["to"] = "250785551212"
        post_data["conversation_uuid"] = "ext-id"
        response = self.client.post(
            reverse("handlers.nexmo_call_handler", args=["answer", nexmo_uuid]),
            json.dumps(post_data),
            content_type="application/json",
        )

        # grab the redirect URL
        redirect_url = re.match(r'.*"eventUrl": \["(.*)"\].*', force_text(response.content)).group(1)

        # get just the path and hit it
        response = self.client.post(
            "%s?%s" % (urlparse(redirect_url).path, urlparse(redirect_url).query),
            json.dumps(post_data),
            content_type="application/json",
        )
        self.assertContains(response, "You are not part of group.")

        # we have an incoming call
        call = IVRCall.objects.all().first()
        self.assertIsNotNone(call)
        self.assertEqual(call.direction, IVRCall.INCOMING)
        self.assertEqual("+250788382382", call.contact_urn.path)
        self.assertEqual("ext-id", call.external_id)

        self.assertEqual(ChannelLog.objects.all().count(), 2)
        channel_log = ChannelLog.objects.first()
        self.assertEqual(channel_log.connection.id, call.id)
        self.assertEqual(channel_log.description, "Incoming request for call")

    @patch("nexmo.Client.create_application")
    def test_incoming_call_nexmo(self, mock_create_application):
        mock_create_application.return_value = dict(id="app-id", keys=dict(private_key="private-key\n"))

        self.org.connect_nexmo("123", "456", self.admin)
        self.org.save()

        self.channel.channel_type = "NX"
        self.channel.save()

        nexmo_uuid = self.org.config["NEXMO_UUID"]

        # import an ivr flow
        flow = self.get_flow("call_me_maybe")
        flow.version_number = 3
        flow.save()

        # go back to our original version
        flow_json = self.get_flow_json("call_me_maybe")["definition"]

        from temba.flows.models import FlowRevision

        FlowRevision.objects.create(
            flow=flow, definition=flow_json, spec_version=3, revision=2, created_by=self.admin, modified_by=self.admin
        )

        # event for non-existing external_id call
        post_data = dict()
        post_data["status"] = "ringing"
        post_data["duration"] = "0"
        post_data["conversation_uuid"] = "ext-id"
        post_data["uuid"] = "call-ext-id"

        response = self.client.post(
            reverse("handlers.nexmo_call_handler", args=["event", nexmo_uuid]),
            json.dumps(post_data),
            content_type="application/json",
        )

        self.assertEqual(200, response.status_code)
        self.assertContains(response, "Call not found for call-ext-id")

        # create an inbound call
        post_data = dict()
        post_data["from"] = "250788382382"
        post_data["to"] = "250785551212"
        post_data["conversation_uuid"] = "ext-id"

        response = self.client.post(
            reverse("handlers.nexmo_call_handler", args=["answer", nexmo_uuid]),
            json.dumps(post_data),
            content_type="application/json",
        )

        self.assertTrue(
            dict(
                action="talk",
                bargeIn=True,
                text="Would you like me to call you? Press one for yes, two for no, or three for maybe.",
            )
            in response.json()
        )

        call = IVRCall.objects.get()
        self.assertIsNotNone(call)
        self.assertEqual("+250788382382", call.contact_urn.path)
        self.assertEqual(call.direction, IVRCall.INCOMING)
        self.assertEqual("ext-id", call.external_id)

        self.assertEqual(ChannelLog.objects.all().count(), 1)
        channel_log = ChannelLog.objects.first()
        self.assertEqual(channel_log.connection.id, call.id)
        self.assertEqual(channel_log.description, "Incoming request for call")
        self.assertIsNotNone(FlowRun.objects.filter(connection=call).first().expires_on)

        flow.refresh_from_db()
        self.assertEqual(flow.version_number, Flow.FINAL_LEGACY_VERSION)

        self.assertIsNot(call.status, IVRCall.COMPLETED)

        # set call to 'in-progress'
        post_data = dict()
        post_data["status"] = "answered"
        post_data["duration"] = "0"
        post_data["conversation_uuid"] = "ext-id"
        post_data["uuid"] = "call-ext-id"

        response = self.client.post(
            reverse("handlers.nexmo_call_handler", args=["event", nexmo_uuid]),
            json.dumps(post_data),
            content_type="application/json",
        )

        self.assertEqual(200, response.status_code)
        self.assertContains(response, "Updated call status")

        # event for non-existing external_id call
        post_data = dict()
        post_data["status"] = "completed"
        post_data["duration"] = "0"
        post_data["conversation_uuid"] = "ext-id"
        post_data["uuid"] = "call-ext-id"

        response = self.client.post(
            reverse("handlers.nexmo_call_handler", args=["event", nexmo_uuid]) + "?has_event=1",
            json.dumps(post_data),
            content_type="application/json",
        )

        call = IVRCall.objects.get()
        run = call.runs.all().first()
        self.assertEqual(200, response.status_code)
        self.assertContains(response, "Updated call status")
        self.assertEqual(call.status, IVRCall.COMPLETED)
        self.assertTrue(run.is_completed())

        self.assertEqual(ChannelLog.objects.all().count(), 3)
        channel_log = ChannelLog.objects.last()
        self.assertEqual(channel_log.connection.id, call.id)
        self.assertEqual(channel_log.description, "Updated call status")

    @patch("nexmo.Client.create_application")
    def test_nexmo_config_empty_callbacks(self, mock_create_application):
        mock_create_application.return_value = dict(id="app-id", keys=dict(private_key="private-key\n"))

        self.org.connect_nexmo("123", "456", self.admin)
        self.org.save()

        nexmo_uuid = self.org.config["NEXMO_UUID"]

        response = self.client.post(
            reverse("handlers.nexmo_call_handler", args=["answer", nexmo_uuid]), "", content_type="application/json"
        )
        self.assertEqual(200, response.status_code)

        response = self.client.post(
            reverse("handlers.nexmo_call_handler", args=["answer", nexmo_uuid]),
            json.dumps({}),
            content_type="application/json",
        )
        self.assertEqual(200, response.status_code)

        response = self.client.post(
            reverse("handlers.nexmo_call_handler", args=["event", nexmo_uuid]), "", content_type="application/json"
        )
        self.assertEqual(200, response.status_code)

        response = self.client.post(
            reverse("handlers.nexmo_call_handler", args=["event", nexmo_uuid]),
            json.dumps({}),
            content_type="application/json",
        )
        self.assertEqual(200, response.status_code)

    @patch("nexmo.Client.create_application")
    def test_no_channel_for_call_nexmo(self, mock_create_application):
        mock_create_application.return_value = dict(id="app-id", keys=dict(private_key="private-key\n"))

        self.org.connect_nexmo("123", "456", self.admin)
        self.org.save()

        nexmo_uuid = self.org.config["NEXMO_UUID"]

        # remove our channel
        self.channel.release()

        # create an inbound call
        post_data = dict()
        post_data["from"] = "250788382382"
        post_data["to"] = "250785551212"
        post_data["conversation_uuid"] = "ext-id"

        response = self.client.post(
            reverse("handlers.nexmo_call_handler", args=["answer", nexmo_uuid]),
            json.dumps(post_data),
            content_type="application/json",
        )

        self.assertContains(response, "Channel not found for number: 250785551212", status_code=404)

        # no call object created
        self.assertFalse(IVRCall.objects.all())

    @patch("nexmo.Client.create_application")
    def test_no_flow_for_incoming_nexmo(self, mock_create_application):
        mock_create_application.return_value = dict(id="app-id", keys=dict(private_key="private-key\n"))

        self.org.connect_nexmo("123", "456", self.admin)
        self.org.save()

        self.channel.channel_type = "NX"
        self.channel.save()

        nexmo_uuid = self.org.config["NEXMO_UUID"]

        flow = self.get_flow("missed_call_flow")

        # create an inbound call
        post_data = dict()
        post_data["from"] = "250788382382"
        post_data["to"] = "250785551212"
        post_data["conversation_uuid"] = "ext-id"
        response = self.client.post(
            reverse("handlers.nexmo_call_handler", args=["answer", nexmo_uuid]),
            json.dumps(post_data),
            content_type="application/json",
        )

        self.assertEqual(response.json(), [dict(action="talk", bargeIn=False, text="")])
        # no call object created
        self.assertFalse(IVRCall.objects.all())

        # have a run in the missed call flow
        self.assertTrue(FlowRun.objects.filter(flow=flow))

    @patch("temba.ivr.clients.TwilioClient", MockTwilioClient)
    @patch("twilio.request_validator.RequestValidator", MockRequestValidator)
    def test_no_channel_for_call(self):
        self.org.connect_twilio("TEST_SID", "TEST_TOKEN", self.admin)
        self.org.save()

        # remove our channel
        self.channel.release()

        # create an inbound call
        post_data = dict(
            CallSid="CallSid", CallStatus="ringing", Direction="inbound", From="+250788382382", To=self.channel.address
        )
        response = self.client.post(reverse("handlers.twilio_handler", args=["voice", self.channel.uuid]), post_data)

        self.assertEqual(200, response.status_code)
        self.assertContains(response, "no channel configured to take this call")

        # no call object created
        self.assertFalse(IVRCall.objects.all())

    @patch("temba.ivr.clients.TwilioClient", MockTwilioClient)
    @patch("twilio.request_validator.RequestValidator", MockRequestValidator)
    def test_no_flow_for_incoming(self):
        self.org.connect_twilio("TEST_SID", "TEST_TOKEN", self.admin)
        self.org.save()

        flow = self.get_flow("missed_call_flow")

        # create an inbound call
        post_data = dict(
            CallSid="CallSid", CallStatus="ringing", Direction="inbound", From="+250788382382", To=self.channel.address
        )
        response = self.client.post(reverse("handlers.twilio_handler", args=["voice", self.channel.uuid]), post_data)

        self.assertContains(response, "Hangup")
        # no call object created
        self.assertFalse(IVRCall.objects.all())

        # have a run in the missed call flow
        self.assertTrue(FlowRun.objects.filter(flow=flow))

    @patch("temba.ivr.clients.TwilioClient", MockTwilioClient)
    @patch("twilio.request_validator.RequestValidator", MockRequestValidator)
    def test_no_twilio_connected(self):
        # create an inbound call
        post_data = dict(
            CallSid="CallSid", CallStatus="ringing", Direction="inbound", From="+250788382382", To=self.channel.address
        )
        response = self.client.post(reverse("handlers.twilio_handler", args=["voice", self.channel.uuid]), post_data)

        self.assertEqual(response.status_code, 400)

    @patch("temba.ivr.clients.TwilioClient", MockTwilioClient)
    @patch("twilio.request_validator.RequestValidator", MockRequestValidator)
    def test_download_media_twilio(self):
        self.org.connect_twilio("TEST_SID", "TEST_TOKEN", self.admin)
        self.org.save()

        with patch("requests.get") as response:
            mock1 = MockResponse(404, "No such file")
            mock2 = MockResponse(200, "Fake VCF Bits")
            mock2.add_header("Content-Type", "text/x-vcard")
            mock2.add_header("Content-Disposition", "inline")
            response.side_effect = (mock1, mock2)

            twilio_client = self.org.get_twilio_client()

            with patch("temba.orgs.models.Org.save_media") as mock_save_media:
                mock_save_media.return_value = "SAVED"

                output = twilio_client.download_media("http://api.twilio.com/ASID/Media/SID")
                self.assertIsNotNone(output)
                self.assertEqual(output, "text/x-vcard:SAVED")
                # saved_media was called with a file as first argument and the guessed extension as second argument
                self.assertIsInstance(mock_save_media.call_args_list[0][0][0], File)
                self.assertEqual(mock_save_media.call_args_list[0][0][1], "vcf")

    @patch("temba.utils.nexmo.NexmoClient.download_recording")
    @patch("nexmo.Client.create_application")
    @patch("nexmo.Client.create_call")
    def test_download_media_nexmo(self, mock_create_call, mock_create_application, mock_download_recording):
        mock_create_application.return_value = dict(id="app-id", keys=dict(private_key="private-key\n"))

        mock_create_call.return_value = dict(uuid="12345")
        mock_download_recording.side_effect = [
            MockResponse(200, "SOUND BITS"),
            MockResponse(400, "Error"),
            MockResponse(200, "SOUND_BITS", headers={"Content-Type": "audio/x-wav"}),
            MockResponse(200, "SOUND_BITS", headers={"Content-Type": "audio/x-wav", "Content-Disposition": "inline"}),
            MockResponse(
                200,
                "SOUND_BITS",
                headers={"Content-Type": "audio/x-wav", "Content-Disposition": 'attachment; filename="playme.wav"'},
            ),
        ]

        self.org.connect_nexmo("123", "456", self.admin)
        self.org.save()

        self.channel.channel_type = "NX"
        self.channel.save()

        # import an ivr flow
        self.import_file("gather_digits")

        # make sure our flow is there as expected
        flow = Flow.objects.filter(name="Gather Digits").first()

        # start our flow
        eric = self.create_contact("Eric Newcomer", number="+13603621737")
        flow.start([], [eric])
        call = IVRCall.objects.filter(direction=IVRCall.OUTGOING).first()
        call.external_id = "ext-id"
        call.save()

        nexmo_client = self.org.get_nexmo_client()

        with patch("temba.orgs.models.Org.save_media") as mock_save_media:
            mock_save_media.return_value = "SAVED"

            # without content-type
            output = nexmo_client.download_media(call, "http://nexmo.com/some_audio_link")
            self.assertIsNone(output)

            # with content-type and retry fetch
            output = nexmo_client.download_media(call, "http://nexmo.com/some_audio_link")
            self.assertIsNotNone(output)
            self.assertEqual(output, "audio/x-wav:SAVED")

            # for content-disposition inline
            output = nexmo_client.download_media(call, "http://nexmo.com/some_audio_link")
            self.assertIsNotNone(output)
            self.assertEqual(output, "audio/x-wav:SAVED")

            # for content disposition attachment
            output = nexmo_client.download_media(call, "http://nexmo.com/some_audio_link")
            self.assertIsNotNone(output)
            self.assertEqual(output, "audio/x-wav:SAVED")

            self.assertEqual(3, len(mock_save_media.call_args_list))

            for i in range(len(mock_save_media.call_args_list)):
                self.assertIsInstance(mock_save_media.call_args_list[i][0][0], File)
                self.assertEqual(mock_save_media.call_args_list[i][0][1], "wav")

    @patch("jwt.encode")
    @patch("nexmo.Client.create_application")
    def test_temba_utils_nexmo_methods(self, mock_create_application, mock_jwt_encode):
        mock_create_application.return_value = dict(id="app-id", keys=dict(private_key="private-key\n"))

        mock_jwt_encode.return_value = b"TOKEN"

        self.org.connect_nexmo("123", "456", self.admin)
        self.org.save()

        self.channel.channel_type = "NX"
        self.channel.save()

        # import an ivr flow
        self.import_file("gather_digits")

        # make sure our flow is there as expected
        flow = Flow.objects.filter(name="Gather Digits").first()

        # start our flow
        eric = self.create_contact("Eric Newcomer", number="+13603621737")
        with patch("requests.post") as mock_post:
            mock_post.return_value = MockResponse(200, json.dumps(dict(uuid="12345")))
            flow.start([], [eric])
        call = IVRCall.objects.filter(direction=IVRCall.OUTGOING).first()
        call.external_id = "ext-id"
        call.save()

        nexmo_client = self.org.get_nexmo_client()

        user_agent = "nexmo-python/{0}/{1}".format(nexmo.__version__, python_version())

        self.assertEqual(nexmo_client.gen_headers(), {"User-Agent": user_agent, "Authorization": b"Bearer TOKEN"})

        with patch("requests.get") as mock_get:
            mock_get.return_value = MockResponse(200, "DONE")
            nexmo_client.download_media(call, "http://example.com/file.txt")

            mock_get.assert_called_once_with(
                "http://example.com/file.txt",
                params=None,
                headers={"User-Agent": user_agent, "Authorization": b"Bearer TOKEN"},
            )

    @patch("temba.ivr.tasks.start_call_task.apply_async")
    def test_check_calls_task(self, mock_start_call):
        a_contact = self.create_contact("Eric Newcomer", number="+13603621737")

        # calls that should be retried
        call1 = IVRCall.objects.create(
            channel=self.channel,
            org=self.org,
            contact=a_contact,
            contact_urn=a_contact.urns.first(),
            direction=IVRCall.OUTGOING,
            retry_count=0,
            next_attempt=timezone.now() - timedelta(days=180),
            status=IVRCall.NO_ANSWER,
            duration=10,
            started_on=timezone.now() - timedelta(minutes=1),
            ended_on=timezone.now(),
        )
        call2 = IVRCall.objects.create(
            channel=self.channel,
            org=self.org,
            contact=a_contact,
            contact_urn=a_contact.urns.first(),
            direction=IVRCall.OUTGOING,
            retry_count=IVRCall.MAX_RETRY_ATTEMPTS - 1,
            next_attempt=timezone.now() - timedelta(days=180),
            status=IVRCall.BUSY,
            duration=10,
            started_on=timezone.now() - timedelta(minutes=1),
            ended_on=timezone.now(),
        )

        # busy, but reached max retries
        call3 = IVRCall.objects.create(
            channel=self.channel,
            org=self.org,
            contact=a_contact,
            contact_urn=a_contact.urns.first(),
            direction=IVRCall.OUTGOING,
            retry_count=IVRCall.MAX_RETRY_ATTEMPTS + 1,
            next_attempt=timezone.now() - timedelta(days=180),
            status=IVRCall.BUSY,
            duration=10,
            started_on=timezone.now() - timedelta(minutes=1),
            ended_on=timezone.now(),
        )
        # call in progress
        call4 = IVRCall.objects.create(
            channel=self.channel,
            org=self.org,
            contact=a_contact,
            contact_urn=a_contact.urns.first(),
            direction=IVRCall.OUTGOING,
            retry_count=0,
            next_attempt=timezone.now() - timedelta(days=180),
            status=IVRCall.IN_PROGRESS,
            duration=0,
            started_on=timezone.now() - timedelta(minutes=1),
            ended_on=None,
        )

        # call that will be ignored because it was last modified before allowed period
        call5 = IVRCall.objects.create(
            channel=self.channel,
            org=self.org,
            contact=a_contact,
            contact_urn=a_contact.urns.first(),
            direction=IVRCall.OUTGOING,
            retry_count=0,
            next_attempt=timezone.now() - timedelta(days=180),
            status=IVRCall.NO_ANSWER,
            duration=10,
            started_on=timezone.now() - timedelta(minutes=1),
            ended_on=timezone.now(),
        )

        call5.modified_on = timezone.now() - timedelta(IVRCall.IGNORE_PENDING_CALLS_OLDER_THAN_DAYS + 14)
        call5.save(update_fields=("modified_on",))

        self.assertTrue(
            all((call1.next_attempt, call2.next_attempt, call3.next_attempt, call4.next_attempt, call5.next_attempt))
        )

        # expect only two new tasks, third one is over MAX_RETRY_ATTEMPTS
        check_calls_task()
        self.assertEqual(2, mock_start_call.call_count)

        call1.refresh_from_db()
        call2.refresh_from_db()
        call3.refresh_from_db()
        call4.refresh_from_db()
        call5.refresh_from_db()

        # these calls have been rescheduled
        self.assertIsNone(call1.next_attempt)
        self.assertIsNone(call1.started_on)
        self.assertIsNone(call1.ended_on)
        self.assertEqual(call1.duration, 0)
        self.assertEqual(call1.status, IVRCall.QUEUED)

        self.assertIsNone(call2.next_attempt)
        self.assertIsNone(call2.started_on)
        self.assertIsNone(call2.ended_on)
        self.assertEqual(call2.duration, 0)
        self.assertEqual(call2.status, IVRCall.QUEUED)

        # these calls should not be rescheduled
        self.assertIsNotNone(call3.next_attempt)
        self.assertEqual(call3.status, IVRCall.BUSY)

        self.assertIsNotNone(call4.next_attempt)
        self.assertEqual(call4.status, IVRCall.IN_PROGRESS)

        self.assertIsNotNone(call5.next_attempt)
        self.assertEqual(call5.status, IVRCall.NO_ANSWER)

    def test_schedule_call_retry(self):
        a_contact = self.create_contact("Eric Newcomer", number="+13603621737")

        call1 = IVRCall.objects.create(
            channel=self.channel, org=self.org, contact=a_contact, contact_urn=a_contact.urns.first()
        )

        self.assertIsNone(call1.next_attempt)
        self.assertEqual(call1.retry_count, 0)

        # schedule a call retry
        call1.schedule_call_retry(60)
        self.assertTrue(call1.next_attempt > timezone.now())
        self.assertEqual(call1.retry_count, 1)

        # schedule second call retry
        call1.schedule_call_retry(60)
        self.assertTrue(call1.next_attempt > timezone.now())
        self.assertEqual(call1.retry_count, 2)

        # call should not be retried if we are over IVRCall.MAX_RETRY_ATTEMPTS
        total_retries = IVRCall.MAX_RETRY_ATTEMPTS + 33
        call1.retry_count = total_retries
        call1.save()

        call1.schedule_call_retry(60)
        self.assertIsNone(call1.next_attempt)
        self.assertEqual(call1.retry_count, total_retries)

    def test_update_status_for_call_retry_twilio(self):
        a_contact = self.create_contact("Eric Newcomer", number="+13603621737")

        call1 = IVRCall.objects.create(
            channel=self.channel, org=self.org, contact=a_contact, contact_urn=a_contact.urns.first()
        )

        def _get_flow():
            class FlowStub:
                metadata = {"ivr_retry": 60}

            return FlowStub()

        call1.get_flow = _get_flow

        # twilio
        call1.update_status("busy", 0, "T")
        call1.save()
        self.assertTrue(call1.next_attempt > timezone.now())

        call1.next_attempt = None
        call1.retry_count = 0
        call1.update_status("in-progress", 0, "T")
        call1.save()

        call1.update_status("no-answer", 0, "T")
        call1.save()
        self.assertTrue(call1.next_attempt > timezone.now())

        call1.next_attempt = None
        call1.retry_count = 0
        call1.update_status("in-progress", 0, "T")
        call1.save()

        # we should not change call_retry if we got the same status
        call1.update_status("busy", 0, "T")
        call1.save()
        self.assertTrue(call1.next_attempt > timezone.now())
        self.assertEqual(call1.retry_count, 1)

        call1.update_status("no-answer", 0, "T")
        call1.save()
        self.assertEqual(call1.retry_count, 1)

    def test_update_status_for_call_retry_nexmo(self):
        a_contact = self.create_contact("Eric Newcomer", number="+13603621737")

        call1 = IVRCall.objects.create(
            channel=self.channel, org=self.org, contact=a_contact, contact_urn=a_contact.urns.first()
        )

        def _get_flow():
            class FlowStub:
                metadata = {"ivr_retry": 60}

            return FlowStub()

        call1.get_flow = _get_flow

        # nexmo
        call1.update_status("busy", 0, "NX")
        call1.save()
        self.assertTrue(call1.next_attempt > timezone.now())

        call1.next_attempt = None
        call1.retry_count = 0
        call1.update_status("answered", 0, "NX")
        call1.save()

        call1.update_status("rejected", 0, "NX")
        call1.save()
        self.assertTrue(call1.next_attempt > timezone.now())

        call1.next_attempt = None
        call1.retry_count = 0
        call1.update_status("answered", 0, "NX")
        call1.save()

        call1.update_status("unanswered", 0, "NX")
        call1.save()
        self.assertTrue(call1.next_attempt > timezone.now())

        call1.next_attempt = None
        call1.retry_count = 0
        call1.update_status("answered", 0, "NX")
        call1.save()

        call1.update_status("timeout", 0, "NX")
        call1.save()
        self.assertTrue(call1.next_attempt > timezone.now())

        call1.next_attempt = None
        call1.retry_count = 0
        call1.update_status("answered", 0, "NX")
        call1.save()

        call1.update_status("cancelled", 0, "NX")
        call1.save()
        self.assertTrue(call1.next_attempt > timezone.now())

        call1.next_attempt = None
        call1.retry_count = 0
        call1.update_status("answered", 0, "NX")
        call1.save()

        # we should not change call_retry if we got the same status
        call1.update_status("cancelled", 0, "NX")
        call1.save()
        self.assertTrue(call1.next_attempt > timezone.now())
        self.assertEqual(call1.retry_count, 1)

        call1.update_status("cancelled", 0, "NX")
        call1.save()
        self.assertEqual(call1.retry_count, 1)

    def test_IVR_view_request_handler(self):
        call_pk = 0
        callback_url = reverse("ivr.ivrcall_handle", args=[call_pk])

        self.assertRaises(ValueError, self.client.get, callback_url)

    def test_update_status_ValueError_input(self):
        a_contact = self.create_contact("Eric Newcomer", number="+13603621737")

        call1 = IVRCall.objects.create(
            channel=self.channel, org=self.org, contact=a_contact, contact_urn=a_contact.urns.first()
        )

        # status is None
        self.assertRaises(ValueError, call1.update_status, None, 0, "NX")

        # status is empty
        self.assertRaises(ValueError, call1.update_status, "", 0, "NX")

    def test_create_outgoing_implicit_values(self):
        a_contact = self.create_contact("Eric Newcomer", number="+13603621737")

        call = IVRCall.create_outgoing(channel=self.channel, contact=a_contact, contact_urn=a_contact.urns.first())

        self.assertEqual(call.direction, IVRCall.OUTGOING)
        self.assertEqual(call.org, self.org)
        self.assertEqual(call.status, IVRCall.PENDING)

    def test_create_incoming_implicit_values(self):
        a_contact = self.create_contact("Eric Newcomer", number="+13603621737")

        call = IVRCall.create_incoming(
            channel=self.channel, contact=a_contact, contact_urn=a_contact.urns.first(), external_id="an_external_id"
        )

        self.assertEqual(call.direction, IVRCall.INCOMING)
        self.assertEqual(call.org, self.org)
        self.assertEqual(call.status, IVRCall.PENDING)

    def test_nexmo_derive_ivr_status(self):

        self.assertEqual(IVRCall.derive_ivr_status_nexmo("ringing", None), IVRCall.RINGING)
        self.assertEqual(IVRCall.derive_ivr_status_nexmo("started", None), IVRCall.RINGING)
        self.assertEqual(IVRCall.derive_ivr_status_nexmo("answered", None), IVRCall.IN_PROGRESS)
        self.assertEqual(IVRCall.derive_ivr_status_nexmo("failed", None), IVRCall.FAILED)
        self.assertEqual(IVRCall.derive_ivr_status_nexmo("rejected", None), IVRCall.BUSY)
        self.assertEqual(IVRCall.derive_ivr_status_nexmo("busy", None), IVRCall.BUSY)
        self.assertEqual(IVRCall.derive_ivr_status_nexmo("unanswered", None), IVRCall.NO_ANSWER)
        self.assertEqual(IVRCall.derive_ivr_status_nexmo("timeout", None), IVRCall.NO_ANSWER)
        self.assertEqual(IVRCall.derive_ivr_status_nexmo("cancelled", None), IVRCall.NO_ANSWER)

        self.assertEqual(IVRCall.derive_ivr_status_nexmo("completed", None), None)
        self.assertEqual(IVRCall.derive_ivr_status_nexmo("completed", IVRCall.RINGING), IVRCall.RINGING)
        self.assertEqual(IVRCall.derive_ivr_status_nexmo("completed", IVRCall.IN_PROGRESS), IVRCall.COMPLETED)

        self.assertRaises(ValueError, IVRCall.derive_ivr_status_nexmo, None, None)
        self.assertRaises(ValueError, IVRCall.derive_ivr_status_nexmo, "cilantro", None)

    def test_twiml_derive_ivr_status(self):

        self.assertEqual(IVRCall.derive_ivr_status_twiml("queued", None), IVRCall.WIRED)
        self.assertEqual(IVRCall.derive_ivr_status_twiml("ringing", None), IVRCall.RINGING)
        self.assertEqual(IVRCall.derive_ivr_status_twiml("no-answer", None), IVRCall.NO_ANSWER)
        self.assertEqual(IVRCall.derive_ivr_status_twiml("in-progress", None), IVRCall.IN_PROGRESS)
        self.assertEqual(IVRCall.derive_ivr_status_twiml("completed", None), IVRCall.COMPLETED)
        self.assertEqual(IVRCall.derive_ivr_status_twiml("busy", None), IVRCall.BUSY)
        self.assertEqual(IVRCall.derive_ivr_status_twiml("failed", None), IVRCall.FAILED)
        self.assertEqual(IVRCall.derive_ivr_status_twiml("canceled", None), IVRCall.CANCELED)

        self.assertRaises(ValueError, IVRCall.derive_ivr_status_twiml, None, None)
        self.assertRaises(ValueError, IVRCall.derive_ivr_status_twiml, "potato", None)

    def test_ivr_call_task_retry(self):
        a_contact = self.create_contact("Eric Newcomer", number="+13603621737")

        call1 = IVRCall.objects.create(
            channel=self.channel,
            org=self.org,
            contact=a_contact,
            contact_urn=a_contact.urns.first(),
            direction=IVRCall.OUTGOING,
            retry_count=0,
            next_attempt=timezone.now() - timedelta(days=180),
            status=IVRCall.NO_ANSWER,
            duration=10,
            started_on=timezone.now() - timedelta(minutes=1),
            ended_on=timezone.now(),
        )

        lock_key = f"ivr_call_start_task_contact_{call1.contact_id}"

        # simulate that we are already executing a call task for this contact
        with NonBlockingLock(redis=get_redis_connection(), name=lock_key, timeout=60):

            self.assertRaises(celery.exceptions.Retry, start_call_task, call1.id)

    @patch("temba.ivr.clients.TwilioClient", MockTwilioClient)
    @patch("twilio.request_validator.RequestValidator", MockRequestValidator)
    def test_ivr_limit_max_concurrent_events(self):
        r = get_redis_connection()

        # connect it and check our client is configured
        self.org.connect_twilio("TEST_SID", "TEST_TOKEN", self.admin)
        self.org.save()

        # twiml api config
        config = {
            Channel.CONFIG_SEND_URL: "https://api.twilio.com",
            Channel.CONFIG_ACCOUNT_SID: "TEST_SID",
            Channel.CONFIG_AUTH_TOKEN: "TEST_TOKEN",
            Channel.CONFIG_MAX_CONCURRENT_EVENTS: 1,
        }
        channel = Channel.create(
            self.org, self.org.get_user(), "BR", "TW", "+558299990000", "+558299990000", config, "AC"
        )

        # import an ivr flow
        self.import_file("call_me_maybe")
        flow = Flow.objects.filter(name="Call me maybe").first()

        # create contacts
        eric = self.create_contact("Eric Newcomer", number="+13603621737")
        not_eric = self.create_contact("Not Eric Newcomer", number="+13603621738")
        also_not_eric = self.create_contact("Also Not Eric Newcomer", number="+13603621739")

        channel_key = Channel.redis_active_events_key(channel.id)

        tracked_active_calls = r.get(channel_key)
        self.assertIsNone(tracked_active_calls)

        # start the flow
        flow.start([], [eric, not_eric, also_not_eric])

        started_calls = IVRCall.objects.filter(direction=IVRCall.OUTGOING, status=ChannelConnection.WIRED)
        pending_calls = IVRCall.objects.filter(direction=IVRCall.OUTGOING, status=ChannelConnection.PENDING)

        # we should have an wired ivr call now
        self.assertEqual(started_calls.count(), 1)
        # and, we should have two calls in pending state
        self.assertEqual(pending_calls.count(), 2)

        tracked_active_calls = r.get(channel_key)
        self.assertEqual(int(tracked_active_calls), 1)

        # finish started call
        started_call = started_calls.first()
        self.client.post(reverse("ivr.ivrcall_handle", args=[started_call.pk]), dict(CallStatus="completed"))

        tracked_active_calls = r.get(channel_key)
        self.assertEqual(int(tracked_active_calls), 0)

        # simulate task_enqueue_call_events
        current_app.send_task("task_enqueue_call_events", args=[], kwargs={})

        started_calls = IVRCall.objects.filter(direction=IVRCall.OUTGOING, status=ChannelConnection.WIRED)
        pending_calls = IVRCall.objects.filter(direction=IVRCall.OUTGOING, status=ChannelConnection.PENDING)

        # one of the calls terminated, so we can enqueue another call
        self.assertEqual(started_calls.count(), 1)
        self.assertEqual(pending_calls.count(), 1)

        tracked_active_calls = r.get(channel_key)
        self.assertEqual(int(tracked_active_calls), 1)

        # move started call to ringing
        started_call = started_calls.first()
        self.client.post(reverse("ivr.ivrcall_handle", args=[started_call.pk]), dict(CallStatus="ringing"))

        # simulate task_enqueue_call_events
        current_app.send_task("task_enqueue_call_events", args=[], kwargs={})

        started_calls = IVRCall.objects.filter(direction=IVRCall.OUTGOING, status=ChannelConnection.WIRED)
        pending_calls = IVRCall.objects.filter(direction=IVRCall.OUTGOING, status=ChannelConnection.PENDING)

        # call is still in progress so we can't enqueue a new call
        self.assertEqual(started_calls.count(), 0)
        self.assertEqual(pending_calls.count(), 1)

        tracked_active_calls = int(r.get(channel_key))
        self.assertEqual(tracked_active_calls, 1)

        # close active call (interrupt)
        started_call.close()

        tracked_active_calls = int(r.get(channel_key))
        self.assertEqual(tracked_active_calls, 0)

        # simulate task_enqueue_call_events
        current_app.send_task("task_enqueue_call_events", args=[], kwargs={})

        started_calls = IVRCall.objects.filter(direction=IVRCall.OUTGOING, status=ChannelConnection.WIRED)
        pending_calls = IVRCall.objects.filter(direction=IVRCall.OUTGOING, status=ChannelConnection.PENDING)

        # enqueue a new call
        self.assertEqual(started_calls.count(), 1)
        self.assertEqual(pending_calls.count(), 0)

        tracked_active_calls = int(r.get(channel_key))
        self.assertEqual(tracked_active_calls, 1)

        # release the channel, removes active calls
        with self.settings(IS_PROD=True):
            channel.release()

        tracked_active_calls = int(r.get(channel_key))
        self.assertEqual(tracked_active_calls, 0)

    @patch("temba.ivr.clients.TwilioClient", MockTwilioClient)
    @patch("twilio.request_validator.RequestValidator", MockRequestValidator)
    def test_failed_call_retry(self):
        def _get_disabled_retry_flow():
            class FlowStub:
                metadata = {"ivr_retry_failed_events": False}

            return FlowStub()

        def _get_enabled_retry_flow():
            class FlowStub:
                metadata = {"ivr_retry_failed_events": True}

            return FlowStub()

        # connect it and check our client is configured
        self.org.connect_twilio("TEST_SID", "TEST_TOKEN", self.admin)
        self.org.save()

        a_contact = self.create_contact("Eric Newcomer", number="+13603621737")

        call1 = IVRCall.objects.create(
            channel=self.channel,
            org=self.org,
            contact=a_contact,
            contact_urn=a_contact.urns.first(),
            direction=IVRCall.OUTGOING,
        )
        call2 = IVRCall.objects.create(
            channel=self.channel,
            org=self.org,
            contact=a_contact,
            contact_urn=a_contact.urns.first(),
            direction=IVRCall.OUTGOING,
        )

        # flow does has not enabled failed call retry
        call1.get_flow = _get_disabled_retry_flow
        call2.get_flow = _get_disabled_retry_flow

        # a call failed
        call1.update_status("failed", 0, "T")
        call1.save()

        # there should be a failed call
        failed_calls = IVRCall.objects.filter(direction=IVRCall.OUTGOING, status=ChannelConnection.FAILED)
        self.assertEqual(failed_calls.count(), 1)
        pending_calls = IVRCall.objects.filter(direction=IVRCall.OUTGOING, status=ChannelConnection.PENDING)
        self.assertEqual(pending_calls.count(), 1)

        # but we are not trying to retry it
        self.assertEqual(call1.error_count, 0)

        # simulate async task to enqueue pending failed calls
        current_app.send_task("check_failed_calls_task", args=[], kwargs={})

        # failed call retry is not active, and there are no queued calls
        started_calls = IVRCall.objects.filter(direction=IVRCall.OUTGOING, status=ChannelConnection.QUEUED)
        self.assertEqual(started_calls.count(), 0)

        # enable failed call retry on the channel
        call1.get_flow = _get_enabled_retry_flow
        call2.get_flow = _get_enabled_retry_flow

        call1.update_status("failed", 0, "T")
        call1.save()
        call2.update_status("failed", 0, "T")
        call2.save()

        # failed retry count
        self.assertEqual(call1.error_count, 1)

        # there should be a failed call
        failed_calls = IVRCall.objects.filter(direction=IVRCall.OUTGOING, status=ChannelConnection.FAILED)
        self.assertEqual(failed_calls.count(), 2)

        # simulate async task to enqueue pending failed calls
        current_app.send_task("check_failed_calls_task", args=[], kwargs={})

        # there are no failed calls
        pending_calls = IVRCall.objects.filter(direction=IVRCall.OUTGOING, status=ChannelConnection.FAILED)
        self.assertEqual(pending_calls.count(), 0)

        # and there is one queued call
        started_calls = IVRCall.objects.filter(direction=IVRCall.OUTGOING, status=ChannelConnection.WIRED)
        self.assertEqual(started_calls.count(), 2)

        # should not be able to retry a call that has failed too many times
        call1.error_count = IVRCall.MAX_ERROR_COUNT + 1
        call1.save()

        call1.update_status("failed", 0, "T")
        call1.save()

        failed_calls = IVRCall.objects.filter(direction=IVRCall.OUTGOING, status=ChannelConnection.FAILED)
        self.assertEqual(failed_calls.count(), 1)

        # simulate async task to enqueue pending failed calls
        current_app.send_task("check_failed_calls_task", args=[], kwargs={})

        # the call is still in failed state because we are over the failed_call_retry limit
        pending_calls = IVRCall.objects.filter(direction=IVRCall.OUTGOING, status=ChannelConnection.FAILED)
        self.assertEqual(pending_calls.count(), 1)

        # and there is still one queued call
        started_calls = IVRCall.objects.filter(direction=IVRCall.OUTGOING, status=ChannelConnection.WIRED)
        self.assertEqual(started_calls.count(), 1)

    @patch("temba.ivr.clients.TwilioClient", MockTwilioClient)
    @patch("twilio.request_validator.RequestValidator", MockRequestValidator)
    def test_do_not_retry_calls_older_than_days(self):
        def _get_enabled_retry_flow():
            class FlowStub:
                metadata = {"ivr_retry_failed_events": True}

            return FlowStub()

        # connect it and check our client is configured
        self.org.connect_twilio("TEST_SID", "TEST_TOKEN", self.admin)
        self.org.save()

        a_contact = self.create_contact("Eric Newcomer", number="+13603621737")

        call1 = IVRCall.objects.create(
            channel=self.channel,
            org=self.org,
            contact=a_contact,
            contact_urn=a_contact.urns.first(),
            direction=IVRCall.OUTGOING,
        )
        call2 = IVRCall.objects.create(
            channel=self.channel,
            org=self.org,
            contact=a_contact,
            contact_urn=a_contact.urns.first(),
            direction=IVRCall.OUTGOING,
        )

        # enable failed call retry on the channel
        call1.get_flow = _get_enabled_retry_flow
        call2.get_flow = _get_enabled_retry_flow

        call1.update_status("failed", 0, "T")
        call1.save()
        call2.update_status("failed", 0, "T")
        call2.save()
        call2.modified_on = call2.modified_on - timedelta(days=IVRCall.IGNORE_PENDING_CALLS_OLDER_THAN_DAYS + 14)
        call2.save(update_fields=("modified_on",))

        # failed retry count
        self.assertEqual(call1.error_count, 1)
        self.assertEqual(call2.error_count, 1)

        # there should be a failed call
        failed_calls = IVRCall.objects.filter(direction=IVRCall.OUTGOING, status=ChannelConnection.FAILED)
        self.assertEqual(failed_calls.count(), 2)

        # simulate async task to enqueue pending failed calls
        current_app.send_task("check_failed_calls_task", args=[], kwargs={})

        # there is a failed call older than desired working window
        pending_calls = IVRCall.objects.filter(direction=IVRCall.OUTGOING, status=ChannelConnection.FAILED)
        self.assertEqual(pending_calls.count(), 1)

        call2.refresh_from_db()
        self.assertEqual(call2.status, IVRCall.FAILED)

        # and there is one queued call
        started_calls = IVRCall.objects.filter(direction=IVRCall.OUTGOING, status=ChannelConnection.WIRED)
        self.assertEqual(started_calls.count(), 1)

    @patch("temba.ivr.clients.TwilioClient", MockTwilioClient)
    @patch("twilio.request_validator.RequestValidator", MockRequestValidator)
    def test_pending_calls_on_inactive_channels_should_not_be_queued(self):
        r = get_redis_connection()

        # connect it and check our client is configured
        self.org.connect_twilio("TEST_SID", "TEST_TOKEN", self.admin)
        self.org.save()

        # twiml api config
        config = {
            Channel.CONFIG_SEND_URL: "https://api.twilio.com",
            Channel.CONFIG_ACCOUNT_SID: "TEST_SID",
            Channel.CONFIG_AUTH_TOKEN: "TEST_TOKEN",
            Channel.CONFIG_MAX_CONCURRENT_EVENTS: 1,
        }
        channel = Channel.create(
            self.org, self.org.get_user(), "BR", "TW", "+558299990000", "+558299990000", config, "AC"
        )

        # import an ivr flow
        self.import_file("call_me_maybe")
        flow = Flow.objects.filter(name="Call me maybe").first()

        # create contacts
        eric = self.create_contact("Eric Newcomer", number="+13603621737")
        not_eric = self.create_contact("Not Eric Newcomer", number="+13603621738")

        channel_key = Channel.redis_active_events_key(channel.id)

        tracked_active_calls = r.get(channel_key)
        self.assertIsNone(tracked_active_calls)

        # start the flow
        flow.start([], [eric, not_eric])

        started_calls = IVRCall.objects.filter(direction=IVRCall.OUTGOING, status=ChannelConnection.WIRED)
        pending_calls = IVRCall.objects.filter(direction=IVRCall.OUTGOING, status=ChannelConnection.PENDING)

        self.assertEqual(started_calls.count(), 1)
        self.assertEqual(pending_calls.count(), 1)

        tracked_active_calls = int(r.get(channel_key))
        self.assertEqual(tracked_active_calls, 1)

        started_call = started_calls.first()
        self.client.post(reverse("ivr.ivrcall_handle", args=[started_call.pk]), dict(CallStatus="completed"))

        # deactivate the channel
        channel.is_active = False
        channel.save(update_fields=("is_active",))

        current_app.send_task("task_enqueue_call_events", args=[], kwargs={})

        started_calls = IVRCall.objects.filter(direction=IVRCall.OUTGOING, status=ChannelConnection.WIRED)
        queued_calls = IVRCall.objects.filter(direction=IVRCall.OUTGOING, status=ChannelConnection.QUEUED)
        pending_calls = IVRCall.objects.filter(direction=IVRCall.OUTGOING, status=ChannelConnection.PENDING)

        # will not enqueue a call if the channel is not active
        self.assertEqual(started_calls.count(), 0)
        self.assertEqual(queued_calls.count(), 0)
        self.assertEqual(pending_calls.count(), 1)

        tracked_active_calls = int(r.get(channel_key))
        self.assertEqual(tracked_active_calls, 0)

    @patch("temba.ivr.clients.TwilioClient", MockTwilioClient)
    @patch("twilio.request_validator.RequestValidator", MockRequestValidator)
    def test_unknown_client(self):

        flow = self.get_flow("call_me_maybe")

        # start our flow
        contact = self.create_contact("Branko Brokula", number="+38521342513")
        flow.start([], [contact])

        call = IVRCall.objects.get()
        self.assertEqual(IVRCall.FAILED, call.status)

        self.assertEqual(ChannelLog.objects.all().count(), 2)
        channel_logs = ChannelLog.objects.order_by("id").all()
        self.assertListEqual(
            [channel_log.description for channel_log in channel_logs],
            ["Call queued internally", "Unknown client or domain"],
        )
        self.assertListEqual(
            [channel_log.response for channel_log in channel_logs], ["None", "client=None domain=app.rapidpro.io"]
        )

        call.refresh_from_db()
        self.assertEqual(call.status, IVRCall.FAILED)

    @patch("temba.ivr.clients.TwilioClient", MockTwilioClient)
    @patch("twilio.request_validator.RequestValidator", MockRequestValidator)
    def test_unknown_domain(self):
        # connect it and check our client is configured
        self.org.connect_twilio("TEST_SID", "TEST_TOKEN", self.admin)
        self.org.save()

        # import an ivr flow
        self.import_file("call_me_maybe")
        flow = Flow.objects.filter(name="Call me maybe").first()

        # create contact
        eric = self.create_contact("Eric Newcomer", number="+13603621737")

        # start the flow
        flow.start([], [eric])

        self.assertEqual(ChannelLog.objects.all().count(), 1)
        channel_logs = ChannelLog.objects.order_by("id").all()
        self.assertListEqual([channel_log.description for channel_log in channel_logs], ["Call queued internally"])

        call = IVRCall.objects.get()

        call.status = IVRCall.QUEUED
        call.save(update_fields=("status",))

        # while the call was queued, someone released the org
        self.channel.org = None
        self.channel.save(update_fields=("org",))

        current_app.send_task("start_call_task", args=[call.pk], kwargs={})

        self.assertEqual(ChannelLog.objects.all().count(), 2)
        channel_logs = ChannelLog.objects.order_by("id").all()
        self.assertListEqual(
            [channel_log.description for channel_log in channel_logs],
            ["Call queued internally", "Unknown client or domain"],
        )

        self.assertListEqual(
            [channel_log.response for channel_log in channel_logs], ["None", "client=None domain=None"]
        )

        call.refresh_from_db()

        self.assertEqual(call.status, IVRCall.FAILED)

    def test_mailroom_ivr_view(self):
        response = self.client.get(reverse("mailroom.ivr_handler", args=[self.channel.uuid, "incoming"]))
        self.assertEqual(500, response.status_code)
