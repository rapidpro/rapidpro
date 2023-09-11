from datetime import datetime, timedelta
from unittest.mock import patch

from django.urls import reverse
from django.utils import timezone

from temba.flows.models import FlowSession
from temba.msgs.models import SystemLabel
from temba.tests import CRUDLTestMixin, MigrationTest, TembaTest
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

        call.release()

        self.assertEqual(0, FlowSession.objects.count())
        self.assertEqual(0, Call.objects.count())


class CallCRUDLTest(CRUDLTestMixin, TembaTest):
    def test_list(self):
        list_url = reverse("ivr.call_list")

        contact = self.create_contact("Bob", phone="+123456789")

        call1 = Call.objects.create(
            org=self.org,
            channel=self.channel,
            direction=Call.DIRECTION_IN,
            contact=contact,
            contact_urn=contact.get_urn(),
            status=Call.STATUS_COMPLETED,
            duration=15,
        )
        call2 = Call.objects.create(
            org=self.org,
            channel=self.channel,
            direction=Call.DIRECTION_OUT,
            contact=contact,
            contact_urn=contact.get_urn(),
            status=Call.STATUS_IN_PROGRESS,
            duration=30,
        )
        Call.objects.create(
            org=self.org2,
            channel=self.channel,
            direction=Call.DIRECTION_IN,
            contact=contact,
            contact_urn=contact.get_urn(),
            status=Call.STATUS_IN_PROGRESS,
            duration=15,
        )

        # check query count
        self.login(self.admin)
        with self.assertNumQueries(11):
            self.client.get(list_url)

        self.assertListFetch(list_url, allow_viewers=True, allow_editors=True, context_objects=[call2, call1])


class IVRTest(TembaTest):
    def test_mailroom_urls(self):
        response = self.client.get(reverse("mailroom.ivr_handler", args=[self.channel.uuid, "incoming"]))
        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.content, b"this URL should be mapped to a Mailroom instance")


class BackfillCallCountsTest(MigrationTest):
    app = "ivr"
    migrate_from = "0026_backfill_call_counts"
    migrate_to = "0027_fix_call_counts"

    def setUpBeforeMigration(self, apps):
        contact = self.create_contact("Bob", phone="+123456789")

        self.create_incoming_msg(contact, "Hi")

        Call.objects.create(
            org=self.org,
            channel=self.channel,
            direction=Call.DIRECTION_IN,
            contact=contact,
            contact_urn=contact.get_urn(),
            status=Call.STATUS_COMPLETED,
            duration=15,
        )
        Call.objects.create(
            org=self.org,
            channel=self.channel,
            direction=Call.DIRECTION_IN,
            contact=contact,
            contact_urn=contact.get_urn(),
            status=Call.STATUS_IN_PROGRESS,
            duration=15,
        )

        Call.objects.create(
            org=self.org2,
            channel=self.channel,
            direction=Call.DIRECTION_IN,
            contact=contact,
            contact_urn=contact.get_urn(),
            status=Call.STATUS_IN_PROGRESS,
            duration=15,
        )

        self.org2.system_labels.all().delete()

    def test_migration(self):
        self.assertEqual(
            {
                SystemLabel.TYPE_INBOX: 1,
                SystemLabel.TYPE_FLOWS: 0,
                SystemLabel.TYPE_ARCHIVED: 0,
                SystemLabel.TYPE_OUTBOX: 0,
                SystemLabel.TYPE_SENT: 0,
                SystemLabel.TYPE_FAILED: 0,
                SystemLabel.TYPE_SCHEDULED: 0,
                SystemLabel.TYPE_CALLS: 2,
            },
            SystemLabel.get_counts(self.org),
        )
        self.assertEqual(
            {
                SystemLabel.TYPE_INBOX: 0,
                SystemLabel.TYPE_FLOWS: 0,
                SystemLabel.TYPE_ARCHIVED: 0,
                SystemLabel.TYPE_OUTBOX: 0,
                SystemLabel.TYPE_SENT: 0,
                SystemLabel.TYPE_FAILED: 0,
                SystemLabel.TYPE_SCHEDULED: 0,
                SystemLabel.TYPE_CALLS: 1,
            },
            SystemLabel.get_counts(self.org2),
        )
