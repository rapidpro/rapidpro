from datetime import datetime, timedelta
from unittest.mock import patch

from django.urls import reverse
from django.utils import timezone

from temba.channels.models import ChannelConnection, ChannelLog
from temba.flows.models import FlowSession, FlowStart
from temba.tests import MigrationTest, TembaTest
from temba.utils.uuid import uuid4

from .models import Call


class CallTest(TembaTest):
    def test_model(self):
        contact = self.create_contact("Bob", phone="+123456789")
        call = Call.objects.create(
            org=self.org,
            channel=self.channel,
            direction=Call.DIRECTION_IN,
            contact=contact,
            contact_urn=contact.get_urn(),
            status=Call.STATUS_IN_PROGRESS,
            started_on=datetime(2022, 9, 20, 13, 46, 30, 0, timezone.utc),
        )

        with patch("django.utils.timezone.now", return_value=datetime(2022, 9, 20, 13, 46, 50, 0, timezone.utc)):
            self.assertEqual(timedelta(seconds=20), call.get_duration())  # calculated
            self.assertEqual("In Progress", call.status_display)

        call.duration = 15
        call.status = Call.STATUS_ERRORED
        call.error_reason = Call.ERROR_NOANSWER
        call.save(update_fields=("status", "error_reason"))

        self.assertEqual(timedelta(seconds=15), call.get_duration())  # from duration field
        self.assertEqual("Errored (No Answer)", call.status_display)

    def test_release(self):
        contact = self.create_contact("Bob", phone="+123456789")

        call = Call.objects.create(
            org=self.org,
            channel=self.channel,
            direction=Call.DIRECTION_IN,
            contact=contact,
            contact_urn=contact.get_urn(),
            status=Call.STATUS_IN_PROGRESS,
            duration=15,
        )
        FlowSession.objects.create(
            uuid=uuid4(),
            org=self.org,
            contact=contact,
            call=call,
            status=FlowSession.STATUS_COMPLETED,
            output={"status": "waiting"},
            wait_resume_on_expire=False,
            ended_on=timezone.now(),
        )
        ChannelLog.objects.create(
            log_type=ChannelLog.LOG_TYPE_IVR_START, channel=self.channel, call=call, http_logs=[], errors=[]
        )
        ChannelLog.objects.create(
            log_type=ChannelLog.LOG_TYPE_IVR_HANGUP, channel=self.channel, call=call, http_logs=[], errors=[]
        )

        call.release()

        self.assertEqual(0, FlowSession.objects.count())
        self.assertEqual(0, ChannelLog.objects.count())
        self.assertEqual(0, Call.objects.count())


class IVRTest(TembaTest):
    def test_mailroom_urls(self):
        response = self.client.get(reverse("mailroom.ivr_handler", args=[self.channel.uuid, "incoming"]))
        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.content, b"this URL should be mapped to a Mailroom instance")


class ConvertConnectionsMigrationTest(MigrationTest):
    app = "ivr"
    migrate_from = "0020_add_call"
    migrate_to = "0021_convert_connections"

    def setUpBeforeMigration(self, apps):
        self.contact1 = self.create_contact("Bob", phone="+123456001")
        self.contact2 = self.create_contact("Jim", phone="+123456002")
        flow = self.create_flow("Test")

        # create an existing call
        self.call_kwargs = dict(
            org=self.org,
            direction=Call.DIRECTION_IN,
            status=Call.STATUS_IN_PROGRESS,
            channel=self.channel,
            contact=self.contact1,
            contact_urn=self.contact1.urns.first(),
            external_id="CA0001",
            duration=15,
        )
        call1 = Call.objects.create(**self.call_kwargs)
        ChannelLog.objects.create(channel=self.channel, call=call1, http_logs=[], errors=[])

        start1 = FlowStart.create(flow, self.admin, contacts=[self.contact1])
        start1.calls.add(call1)

        self.conn1_kwargs = dict(
            org=self.org,
            direction=Call.DIRECTION_IN,
            status=Call.STATUS_IN_PROGRESS,
            channel=self.channel,
            contact=self.contact1,
            contact_urn=self.contact1.urns.first(),
            external_id="CA0002",
            created_on=datetime(2022, 1, 1, 13, 0, 0, 0, timezone.utc),
            modified_on=datetime(2022, 1, 1, 14, 0, 0, 0, timezone.utc),
            started_on=datetime(2022, 1, 1, 15, 0, 0, 0, timezone.utc),
            ended_on=None,
            duration=None,
            error_reason=None,
            error_count=0,
            next_attempt=None,
        )
        conn1 = ChannelConnection.objects.create(**self.conn1_kwargs)
        ChannelLog.objects.create(channel=self.channel, connection=conn1, http_logs=[], errors=[])

        start2 = FlowStart.create(flow, self.admin, contacts=[self.contact1])
        start2.connections.add(conn1)

        FlowSession.objects.create(
            uuid=uuid4(),
            org=self.org,
            contact=self.contact1,
            current_flow=flow,
            status=FlowSession.STATUS_WAITING,
            output_url="http://sessions.com/123.json",
            wait_started_on=datetime(2022, 1, 1, 0, 0, 0, 0, timezone.utc),
            wait_expires_on=datetime(2022, 1, 2, 0, 0, 0, 0, timezone.utc),
            wait_resume_on_expire=False,
            connection=conn1,
        )

        self.conn2_kwargs = dict(
            org=self.org,
            direction=Call.DIRECTION_OUT,
            status=Call.STATUS_ERRORED,
            channel=self.channel,
            contact=self.contact2,
            contact_urn=self.contact2.urns.first(),
            external_id="",
            created_on=datetime(2022, 1, 2, 13, 0, 0, 0, timezone.utc),
            modified_on=datetime(2022, 1, 2, 14, 0, 0, 0, timezone.utc),
            started_on=datetime(2022, 1, 2, 15, 0, 0, 0, timezone.utc),
            ended_on=datetime(2022, 1, 2, 16, 0, 0, 0, timezone.utc),
            duration=15,
            error_reason=Call.ERROR_BUSY,
            error_count=1,
            next_attempt=datetime(2022, 1, 2, 16, 0, 0, 0, timezone.utc),
        )
        conn2 = ChannelConnection.objects.create(**self.conn2_kwargs)

        start3 = FlowStart.create(flow, self.admin, contacts=[self.contact1])
        start3.connections.add(conn2)

    def test_migration(self):
        # all connections gone
        self.assertEqual(0, ChannelConnection.objects.count())
        self.assertEqual(3, Call.objects.count())

        # channel logs and flow starts for connections gone, but for the call remain
        self.assertEqual(1, ChannelLog.objects.count())
        self.assertEqual(1, FlowStart.objects.count())

        self.assertTrue(Call.objects.filter(**self.call_kwargs).exists())
        self.assertTrue(Call.objects.filter(**self.conn1_kwargs).exists())
        self.assertTrue(Call.objects.filter(**self.conn2_kwargs).exists())
