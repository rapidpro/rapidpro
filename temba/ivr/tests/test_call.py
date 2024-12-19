from datetime import datetime, timedelta, timezone as tzone
from unittest.mock import patch

from django.utils import timezone

from temba.flows.models import FlowSession
from temba.ivr.models import Call
from temba.tests import TembaTest
from temba.utils.uuid import uuid4


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
            started_on=datetime(2022, 9, 20, 13, 46, 30, 0, tzone.utc),
        )

        with patch("django.utils.timezone.now", return_value=datetime(2022, 9, 20, 13, 46, 50, 0, tzone.utc)):
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

        call.release()

        self.assertEqual(0, FlowSession.objects.count())
        self.assertEqual(0, Call.objects.count())
