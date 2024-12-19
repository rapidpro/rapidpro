from datetime import date

from temba.msgs.models import Label, LabelCount, MessageExport, Msg
from temba.msgs.tasks import squash_msg_counts
from temba.tests import TembaTest


class LabelTest(TembaTest):
    def setUp(self):
        super().setUp()

        self.joe = self.create_contact("Joe Blow", phone="073835001")
        self.frank = self.create_contact("Frank", phone="073835002")

    def test_create(self):
        label1 = Label.create(self.org, self.user, "Spam")
        self.assertEqual("Spam", label1.name)

        # don't allow invalid name
        self.assertRaises(AssertionError, Label.create, self.org, self.user, '"Hi"')

        # don't allow duplicate name
        self.assertRaises(AssertionError, Label.create, self.org, self.user, "Spam")

    def test_toggle_label(self):
        label = self.create_label("Spam")
        msg1 = self.create_incoming_msg(self.joe, "Message 1")
        msg2 = self.create_incoming_msg(self.joe, "Message 2")
        msg3 = self.create_incoming_msg(self.joe, "Message 3")

        self.assertEqual(label.get_visible_count(), 0)

        label.toggle_label([msg1, msg2, msg3], add=True)  # add label to 3 messages

        label.refresh_from_db()
        self.assertEqual(label.get_visible_count(), 3)
        self.assertEqual(set(label.get_messages()), {msg1, msg2, msg3})

        label.toggle_label([msg3], add=False)  # remove label from a message

        label.refresh_from_db()
        self.assertEqual(label.get_visible_count(), 2)
        self.assertEqual(set(label.get_messages()), {msg1, msg2})

        # check still correct after squashing
        squash_msg_counts()
        self.assertEqual(label.get_visible_count(), 2)

        msg2.archive()  # won't remove label from msg, but msg no longer counts toward visible count

        label.refresh_from_db()
        self.assertEqual(label.get_visible_count(), 1)
        self.assertEqual(set(label.get_messages()), {msg1, msg2})

        msg2.restore()  # msg back in visible count

        label.refresh_from_db()
        self.assertEqual(label.get_visible_count(), 2)
        self.assertEqual(set(label.get_messages()), {msg1, msg2})

        msg2.delete()  # removes label message no longer visible

        label.refresh_from_db()
        self.assertEqual(label.get_visible_count(), 1)
        self.assertEqual(set(label.get_messages()), {msg1})

        msg3.archive()
        label.toggle_label([msg3], add=True)  # labelling an already archived message doesn't increment the count

        label.refresh_from_db()
        self.assertEqual(label.get_visible_count(), 1)
        self.assertEqual(set(label.get_messages()), {msg1, msg3})

        msg3.restore()  # but then restoring that message will

        label.refresh_from_db()
        self.assertEqual(label.get_visible_count(), 2)
        self.assertEqual(set(label.get_messages()), {msg1, msg3})

        # can't label outgoing messages
        msg5 = self.create_outgoing_msg(self.joe, "Message")
        self.assertRaises(AssertionError, label.toggle_label, [msg5], add=True)

        # squashing shouldn't affect counts
        self.assertEqual(LabelCount.get_totals([label])[label], 2)

        squash_msg_counts()

        self.assertEqual(LabelCount.get_totals([label])[label], 2)

    def test_delete(self):
        label1 = self.create_label("Spam")
        label2 = self.create_label("Social")
        label3 = self.create_label("Other")

        msg1 = self.create_incoming_msg(self.joe, "Message 1")
        msg2 = self.create_incoming_msg(self.joe, "Message 2")
        msg3 = self.create_incoming_msg(self.joe, "Message 3")

        label1.toggle_label([msg1, msg2], add=True)
        label2.toggle_label([msg1], add=True)
        label3.toggle_label([msg3], add=True)

        MessageExport.create(self.org, self.admin, start_date=date.today(), end_date=date.today(), label=label1)

        label1.release(self.admin)
        label2.release(self.admin)

        # check that contained labels are also released
        self.assertEqual(0, Label.objects.filter(id__in=[label1.id, label2.id], is_active=True).count())
        self.assertEqual(set(), set(Msg.objects.get(id=msg1.id).labels.all()))
        self.assertEqual(set(), set(Msg.objects.get(id=msg2.id).labels.all()))
        self.assertEqual({label3}, set(Msg.objects.get(id=msg3.id).labels.all()))

        label3.release(self.admin)
        label3.refresh_from_db()

        self.assertFalse(label3.is_active)
        self.assertEqual(self.admin, label3.modified_by)
        self.assertEqual(set(), set(Msg.objects.get(id=msg3.id).labels.all()))
