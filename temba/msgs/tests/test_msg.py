from datetime import timedelta
from unittest.mock import patch

from django.utils import timezone

from temba.channels.models import ChannelLog
from temba.flows.models import Flow
from temba.msgs.models import Msg, SystemLabel
from temba.msgs.tasks import fail_old_android_messages
from temba.tests import CRUDLTestMixin, TembaTest
from temba.tickets.models import Ticket


class MsgTest(TembaTest, CRUDLTestMixin):
    def setUp(self):
        super().setUp()

        self.joe = self.create_contact("Joe Blow", urns=["tel:789", "tel:123"])
        self.frank = self.create_contact("Frank Blow", phone="321")
        self.kevin = self.create_contact("Kevin Durant", phone="987")

        self.just_joe = self.create_group("Just Joe", [self.joe])
        self.joe_and_frank = self.create_group("Joe and Frank", [self.joe, self.frank])

    def test_as_archive_json(self):
        flow = self.create_flow("Color Flow")
        msg1 = self.create_incoming_msg(self.joe, "i'm having a problem", flow=flow)
        self.assertEqual(
            {
                "id": msg1.id,
                "contact": {"uuid": str(self.joe.uuid), "name": "Joe Blow"},
                "channel": {"uuid": str(self.channel.uuid), "name": "Test Channel"},
                "flow": {"uuid": str(flow.uuid), "name": "Color Flow"},
                "urn": "tel:123",
                "direction": "in",
                "type": "text",
                "status": "handled",
                "visibility": "visible",
                "text": "i'm having a problem",
                "attachments": [],
                "labels": [],
                "created_on": msg1.created_on.isoformat(),
                "sent_on": None,
            },
            msg1.as_archive_json(),
        )

        # label first message
        label = self.create_label("la\02bel1")
        label.toggle_label([msg1], add=True)

        self.assertEqual(
            {
                "id": msg1.id,
                "contact": {"uuid": str(self.joe.uuid), "name": "Joe Blow"},
                "channel": {"uuid": str(self.channel.uuid), "name": "Test Channel"},
                "flow": {"uuid": str(flow.uuid), "name": "Color Flow"},
                "urn": "tel:123",
                "direction": "in",
                "type": "text",
                "status": "handled",
                "visibility": "visible",
                "text": "i'm having a problem",
                "attachments": [],
                "labels": [{"uuid": str(label.uuid), "name": "la\x02bel1"}],
                "created_on": msg1.created_on.isoformat(),
                "sent_on": None,
            },
            msg1.as_archive_json(),
        )

        msg2 = self.create_incoming_msg(
            self.joe, "Media message", attachments=["audio:http://rapidpro.io/audio/sound.mp3"]
        )

        self.assertEqual(
            {
                "id": msg2.id,
                "contact": {"uuid": str(self.joe.uuid), "name": "Joe Blow"},
                "channel": {"uuid": str(self.channel.uuid), "name": "Test Channel"},
                "flow": None,
                "urn": "tel:123",
                "direction": "in",
                "type": "text",
                "status": "handled",
                "visibility": "visible",
                "text": "Media message",
                "attachments": [{"url": "http://rapidpro.io/audio/sound.mp3", "content_type": "audio"}],
                "labels": [],
                "created_on": msg2.created_on.isoformat(),
                "sent_on": None,
            },
            msg2.as_archive_json(),
        )

    @patch("django.core.files.storage.default_storage.delete")
    def test_bulk_soft_delete(self, mock_storage_delete):
        # create some messages
        msg1 = self.create_incoming_msg(
            self.joe,
            "i'm having a problem",
            attachments=[
                r"audo/mp4:http://s3.com/attachments/1/a/b.jpg",
                r"image/jpeg:http://s3.com/attachments/1/c/d%20e.jpg",
            ],
        )
        msg2 = self.create_incoming_msg(self.frank, "ignore joe, he's a liar")
        out1 = self.create_outgoing_msg(self.frank, "hi")

        # can't soft delete outgoing messages
        with self.assertRaises(AssertionError):
            Msg.bulk_soft_delete([out1])

        Msg.bulk_soft_delete([msg1, msg2])

        # soft delete should clear text and attachments
        for msg in (msg1, msg2):
            msg.refresh_from_db()

            self.assertEqual("", msg.text)
            self.assertEqual([], msg.attachments)
            self.assertEqual(Msg.VISIBILITY_DELETED_BY_USER, msg1.visibility)

        mock_storage_delete.assert_any_call("/attachments/1/a/b.jpg")
        mock_storage_delete.assert_any_call("/attachments/1/c/d e.jpg")

    @patch("django.core.files.storage.default_storage.delete")
    def test_bulk_delete(self, mock_storage_delete):
        # create some messages
        msg1 = self.create_incoming_msg(
            self.joe,
            "i'm having a problem",
            attachments=[
                r"audo/mp4:http://s3.com/attachments/1/a/b.jpg",
                r"image/jpeg:http://s3.com/attachments/1/c/d%20e.jpg",
            ],
        )
        self.create_incoming_msg(self.frank, "ignore joe, he's a liar")
        out1 = self.create_outgoing_msg(self.frank, "hi")

        Msg.bulk_delete([msg1, out1])

        self.assertEqual(1, Msg.objects.all().count())

        mock_storage_delete.assert_any_call("/attachments/1/a/b.jpg")
        mock_storage_delete.assert_any_call("/attachments/1/c/d e.jpg")

    def test_archive_and_release(self):
        msg1 = self.create_incoming_msg(self.joe, "Incoming")
        label = self.create_label("Spam")
        label.toggle_label([msg1], add=True)

        msg1.archive()

        msg1 = Msg.objects.get(pk=msg1.pk)
        self.assertEqual(msg1.visibility, Msg.VISIBILITY_ARCHIVED)
        self.assertEqual(set(msg1.labels.all()), {label})  # don't remove labels

        msg1.restore()

        msg1 = Msg.objects.get(pk=msg1.id)
        self.assertEqual(msg1.visibility, Msg.VISIBILITY_VISIBLE)

        msg1.delete()
        self.assertFalse(Msg.objects.filter(pk=msg1.pk).exists())

        label.refresh_from_db()
        self.assertEqual(0, label.get_messages().count())  # do remove labels
        self.assertIsNotNone(label)

        # can't archive outgoing messages
        msg2 = self.create_outgoing_msg(self.joe, "Outgoing")
        self.assertRaises(AssertionError, msg2.archive)

    def test_release_counts(self):
        flow = self.create_flow("Test")

        def assertReleaseCount(direction, status, visibility, flow, label):
            if direction == Msg.DIRECTION_OUT:
                msg = self.create_outgoing_msg(self.joe, "Whattup Joe", flow=flow, status=status)
            else:
                msg = self.create_incoming_msg(self.joe, "Hey hey", flow=flow, status=status)

            Msg.objects.filter(id=msg.id).update(visibility=visibility)

            # assert our folder count is right
            counts = SystemLabel.get_counts(self.org)
            self.assertEqual(counts[label], 1)

            # delete the msg, count should now be 0
            msg.delete()
            counts = SystemLabel.get_counts(self.org)
            self.assertEqual(counts[label], 0)

        # outgoing labels
        assertReleaseCount("O", Msg.STATUS_SENT, Msg.VISIBILITY_VISIBLE, None, SystemLabel.TYPE_SENT)
        assertReleaseCount("O", Msg.STATUS_QUEUED, Msg.VISIBILITY_VISIBLE, None, SystemLabel.TYPE_OUTBOX)
        assertReleaseCount("O", Msg.STATUS_FAILED, Msg.VISIBILITY_VISIBLE, flow, SystemLabel.TYPE_FAILED)

        # incoming labels
        assertReleaseCount("I", Msg.STATUS_HANDLED, Msg.VISIBILITY_VISIBLE, None, SystemLabel.TYPE_INBOX)
        assertReleaseCount("I", Msg.STATUS_HANDLED, Msg.VISIBILITY_ARCHIVED, None, SystemLabel.TYPE_ARCHIVED)
        assertReleaseCount("I", Msg.STATUS_HANDLED, Msg.VISIBILITY_VISIBLE, flow, SystemLabel.TYPE_FLOWS)

    def test_fail_old_android_messages(self):
        msg1 = self.create_outgoing_msg(self.joe, "Hello", status=Msg.STATUS_QUEUED)
        msg2 = self.create_outgoing_msg(
            self.joe, "Hello", status=Msg.STATUS_QUEUED, created_on=timezone.now() - timedelta(days=8)
        )
        msg3 = self.create_outgoing_msg(
            self.joe, "Hello", status=Msg.STATUS_ERRORED, created_on=timezone.now() - timedelta(days=8)
        )
        msg4 = self.create_outgoing_msg(
            self.joe, "Hello", status=Msg.STATUS_SENT, created_on=timezone.now() - timedelta(days=8)
        )

        fail_old_android_messages()

        def assert_status(msg, status):
            msg.refresh_from_db()
            self.assertEqual(status, msg.status)

        assert_status(msg1, Msg.STATUS_QUEUED)
        assert_status(msg2, Msg.STATUS_FAILED)
        assert_status(msg3, Msg.STATUS_FAILED)
        assert_status(msg4, Msg.STATUS_SENT)

    def test_big_ids(self):
        # create an incoming message with big id
        log = ChannelLog.objects.create(
            id=3_000_000_000, channel=self.channel, is_error=True, log_type=ChannelLog.LOG_TYPE_MSG_RECEIVE
        )
        msg = Msg.objects.create(
            id=3_000_000_000,
            org=self.org,
            direction="I",
            contact=self.joe,
            contact_urn=self.joe.urns.first(),
            text="Hi there",
            channel=self.channel,
            status="H",
            msg_type="T",
            visibility="V",
            log_uuids=[log.uuid],
            created_on=timezone.now(),
            modified_on=timezone.now(),
        )
        spam = self.create_label("Spam")
        msg.labels.add(spam)

    def test_foreign_keys(self):
        # create a message which references a flow and a ticket
        flow = self.create_flow("Flow")
        contact = self.create_contact("Ann", phone="+250788000001")
        ticket = self.create_ticket(contact)
        msg = self.create_outgoing_msg(contact, "Hi", flow=flow, ticket=ticket)

        # both Msg.flow and Msg.ticket are unconstrained so we shuld be able to delete these
        flow.release(self.admin)
        flow.delete()
        ticket.delete()

        msg.refresh_from_db()

        # but then accessing them blows up
        with self.assertRaises(Flow.DoesNotExist):
            print(msg.flow)
        with self.assertRaises(Ticket.DoesNotExist):
            print(msg.ticket)
