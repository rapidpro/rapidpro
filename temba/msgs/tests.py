from datetime import datetime, timedelta
from unittest.mock import PropertyMock, patch
from uuid import uuid4

import pytz
from django_redis import get_redis_connection
from openpyxl import load_workbook

from django.conf import settings
from django.urls import reverse
from django.utils import timezone

from temba.archives.models import Archive
from temba.channels.models import Channel, ChannelCount, ChannelEvent, ChannelLog
from temba.contacts.models import TEL_SCHEME, Contact, ContactField, ContactURN
from temba.flows.models import RuleSet
from temba.msgs.models import (
    DELIVERED,
    ERRORED,
    FAILED,
    FLOW,
    HANDLED,
    INBOX,
    INCOMING,
    OUTGOING,
    PENDING,
    QUEUED,
    RESENT,
    SENT,
    WIRED,
    Attachment,
    Broadcast,
    ExportMessagesTask,
    Label,
    LabelCount,
    Msg,
    SystemLabel,
    SystemLabelCount,
    UnreachableException,
)
from temba.orgs.models import Language
from temba.schedules.models import Schedule
from temba.tests import AnonymousOrg, TembaTest
from temba.tests.s3 import MockS3Client
from temba.utils import dict_to_struct, json
from temba.utils.dates import datetime_to_str
from temba.utils.expressions import get_function_listing
from temba.values.constants import Value

from .management.commands.msg_console import MessageConsole
from .tasks import squash_msgcounts
from .templatetags.sms import as_icon


class MsgTest(TembaTest):
    def setUp(self):
        super().setUp()

        self.joe = self.create_contact("Joe Blow", "123")
        ContactURN.create(self.org, self.joe, "tel:789")

        self.frank = self.create_contact("Frank Blow", "321")
        self.kevin = self.create_contact("Kevin Durant", "987")

        self.just_joe = self.create_group("Just Joe", [self.joe])
        self.joe_and_frank = self.create_group("Joe and Frank", [self.joe, self.frank])

    def test_msg_as_archive_json(self):
        msg1 = Msg.create_incoming(self.channel, self.joe.get_urn().urn, "i'm having a problem")
        self.assertEqual(
            msg1.as_archive_json(),
            {
                "id": msg1.id,
                "contact": {"uuid": str(self.joe.uuid), "name": "Joe Blow"},
                "channel": {"uuid": str(self.channel.uuid), "name": "Test Channel"},
                "urn": "tel:123",
                "direction": "in",
                "type": "inbox",
                "status": "handled",
                "visibility": "visible",
                "text": "i'm having a problem",
                "attachments": [],
                "labels": [],
                "created_on": msg1.created_on.isoformat(),
                "sent_on": msg1.sent_on.isoformat(),
            },
        )

        # label first message
        folder = Label.get_or_create_folder(self.org, self.user, "Folder")
        label = Label.get_or_create(self.org, self.user, "la\02bel1", folder=folder)
        label.toggle_label([msg1], add=True)

        self.assertEqual(
            msg1.as_archive_json(),
            {
                "id": msg1.id,
                "contact": {"uuid": str(self.joe.uuid), "name": "Joe Blow"},
                "channel": {"uuid": str(self.channel.uuid), "name": "Test Channel"},
                "urn": "tel:123",
                "direction": "in",
                "type": "inbox",
                "status": "handled",
                "visibility": "visible",
                "text": "i'm having a problem",
                "attachments": [],
                "labels": [{"uuid": str(label.uuid), "name": "la\x02bel1"}],
                "created_on": msg1.created_on.isoformat(),
                "sent_on": msg1.sent_on.isoformat(),
            },
        )

        msg2 = self.create_msg(
            contact=self.joe,
            text="Media message",
            direction="I",
            status=HANDLED,
            msg_type="I",
            attachments=["audio:http://rapidpro.io/audio/sound.mp3"],
        )

        self.assertEqual(
            msg2.as_archive_json(),
            {
                "id": msg2.id,
                "contact": {"uuid": str(self.joe.uuid), "name": "Joe Blow"},
                "channel": {"uuid": str(self.channel.uuid), "name": "Test Channel"},
                "urn": "tel:123",
                "direction": "in",
                "type": "inbox",
                "status": "handled",
                "visibility": "visible",
                "text": "Media message",
                "attachments": [{"url": "http://rapidpro.io/audio/sound.mp3", "content_type": "audio"}],
                "labels": [],
                "created_on": msg2.created_on.isoformat(),
                "sent_on": None,
            },
        )

    def test_deletes(self):

        # create some incoming messages
        msg1 = Msg.create_incoming(self.channel, self.joe.get_urn().urn, "i'm having a problem")
        msg2 = Msg.create_incoming(self.channel, self.frank.get_urn().urn, "ignore joe, he's a liar")

        # create a channel log for msg2
        ChannelLog.objects.create(channel=self.channel, msg=msg2, is_error=False)

        # we've used two credits
        self.assertEqual(2, Msg.objects.all().count())
        self.assertEqual(self.org._calculate_credits_used()[0], 2)

        # a hard delete on a message should reduce credits used
        msg1.delete()
        self.assertEqual(1, Msg.objects.all().count())
        self.assertEqual(self.org._calculate_credits_used()[0], 1)

        # a purge delete on a message should keep credits the same
        msg2.release(Msg.DELETE_FOR_USER)
        self.assertEqual(0, Msg.objects.all().count())
        self.assertEqual(self.org._calculate_credits_used()[0], 1)

        # log should be gone
        self.assertEqual(0, ChannelLog.objects.filter(channel=self.channel).count())

    def test_get_sync_commands(self):
        msg1 = Msg.create_outgoing(self.org, self.admin, self.joe, "Hello, we heard from you.")
        msg2 = Msg.create_outgoing(self.org, self.admin, self.frank, "Hello, we heard from you.")
        msg3 = Msg.create_outgoing(self.org, self.admin, self.kevin, "Hello, we heard from you.")

        commands = Msg.get_sync_commands(Msg.objects.filter(id__in=(msg1.id, msg2.id, msg3.id)))

        self.assertEqual(
            commands,
            [
                {
                    "cmd": "mt_bcast",
                    "to": [
                        {"phone": "123", "id": msg1.id},
                        {"phone": "321", "id": msg2.id},
                        {"phone": "987", "id": msg3.id},
                    ],
                    "msg": "Hello, we heard from you.",
                }
            ],
        )

        msg4 = Msg.create_outgoing(self.org, self.admin, self.kevin, "Hello, there")

        commands = Msg.get_sync_commands(Msg.objects.filter(id__in=(msg1.id, msg2.id, msg4.id)))

        self.assertEqual(
            commands,
            [
                {
                    "cmd": "mt_bcast",
                    "to": [{"phone": "123", "id": msg1.id}, {"phone": "321", "id": msg2.id}],
                    "msg": "Hello, we heard from you.",
                },
                {"cmd": "mt_bcast", "to": [{"phone": "987", "id": msg4.id}], "msg": "Hello, there"},
            ],
        )

        msg5 = Msg.create_outgoing(self.org, self.admin, self.frank, "Hello, we heard from you.")

        commands = Msg.get_sync_commands(Msg.objects.filter(id__in=(msg1.id, msg4.id, msg5.id)))

        self.assertEqual(
            commands,
            [
                {"cmd": "mt_bcast", "to": [{"phone": "123", "id": msg1.id}], "msg": "Hello, we heard from you."},
                {"cmd": "mt_bcast", "to": [{"phone": "987", "id": msg4.id}], "msg": "Hello, there"},
                {"cmd": "mt_bcast", "to": [{"phone": "321", "id": msg5.id}], "msg": "Hello, we heard from you."},
            ],
        )

    def test_archive_and_release(self):
        msg1 = Msg.create_incoming(self.channel, "tel:123", "Incoming")
        label = Label.get_or_create(self.org, self.admin, "Spam")
        label.toggle_label([msg1], add=True)

        msg1.archive()

        msg1 = Msg.objects.get(pk=msg1.pk)
        self.assertEqual(msg1.visibility, Msg.VISIBILITY_ARCHIVED)
        self.assertEqual(set(msg1.labels.all()), {label})  # don't remove labels

        msg1.restore()

        msg1 = Msg.objects.get(pk=msg1.id)
        self.assertEqual(msg1.visibility, Msg.VISIBILITY_VISIBLE)

        msg1.release()
        self.assertFalse(Msg.objects.filter(pk=msg1.pk).exists())

        label = Label.label_objects.filter(pk=label.pk).first()
        self.assertEqual(0, label.get_messages().count())  # do remove labels
        self.assertIsNotNone(label)

        # can't archive outgoing messages
        msg2 = Msg.create_outgoing(self.org, self.admin, self.joe, "Outgoing")
        self.assertRaises(ValueError, msg2.archive)

    def assertReleaseCount(self, direction, status, visibility, msg_type, label):
        if direction == OUTGOING:
            msg = Msg.create_outgoing(self.org, self.admin, self.joe, "Whattup Joe")
        else:
            msg = Msg.create_incoming(self.channel, "tel:+250788123123", "Hey hey")

        Msg.objects.filter(id=msg.id).update(
            status=status, direction=direction, visibility=visibility, msg_type=msg_type
        )

        # assert our folder count is right
        counts = SystemLabel.get_counts(self.org)
        self.assertEqual(counts[label], 1)

        # release the msg, count should now be 0
        msg.release()
        counts = SystemLabel.get_counts(self.org)
        self.assertEqual(counts[label], 0)

    def test_release_counts(self):
        # outgoing labels
        self.assertReleaseCount(OUTGOING, SENT, Msg.VISIBILITY_VISIBLE, INBOX, SystemLabel.TYPE_SENT)
        self.assertReleaseCount(OUTGOING, QUEUED, Msg.VISIBILITY_VISIBLE, INBOX, SystemLabel.TYPE_OUTBOX)
        self.assertReleaseCount(OUTGOING, FAILED, Msg.VISIBILITY_VISIBLE, INBOX, SystemLabel.TYPE_FAILED)

        # incoming labels
        self.assertReleaseCount(INCOMING, HANDLED, Msg.VISIBILITY_VISIBLE, INBOX, SystemLabel.TYPE_INBOX)
        self.assertReleaseCount(INCOMING, HANDLED, Msg.VISIBILITY_ARCHIVED, INBOX, SystemLabel.TYPE_ARCHIVED)
        self.assertReleaseCount(INCOMING, HANDLED, Msg.VISIBILITY_VISIBLE, FLOW, SystemLabel.TYPE_FLOWS)

    def test_erroring(self):
        # test with real message
        msg = Msg.create_outgoing(self.org, self.admin, self.joe, "Test 1")
        r = get_redis_connection()

        Msg.mark_error(r, self.channel, msg)
        msg = Msg.objects.get(pk=msg.id)
        self.assertEqual(msg.status, "E")
        self.assertEqual(msg.error_count, 1)
        self.assertIsNotNone(msg.next_attempt)

        Msg.mark_error(r, self.channel, msg)
        msg = Msg.objects.get(pk=msg.id)
        self.assertEqual(msg.status, "E")
        self.assertEqual(msg.error_count, 2)
        self.assertIsNotNone(msg.next_attempt)

        Msg.mark_error(r, self.channel, msg)
        msg = Msg.objects.get(pk=msg.id)
        self.assertEqual(msg.status, "F")

        # test with mock message
        msg = dict_to_struct("MsgStruct", Msg.create_outgoing(self.org, self.admin, self.joe, "Test 2").as_task_json())

        Msg.mark_error(r, self.channel, msg)
        msg = Msg.objects.get(pk=msg.id)
        self.assertEqual(msg.status, "E")
        self.assertEqual(msg.error_count, 1)
        self.assertIsNotNone(msg.next_attempt)

        Msg.mark_error(r, self.channel, msg)
        msg = Msg.objects.get(pk=msg.id)
        self.assertEqual(msg.status, "E")
        self.assertEqual(msg.error_count, 2)
        self.assertIsNotNone(msg.next_attempt)

        Msg.mark_error(r, self.channel, msg)
        msg = Msg.objects.get(pk=msg.id)
        self.assertEqual(msg.status, "F")

    def test_send_message_auto_completion_processor(self):
        outbox_url = reverse("msgs.msg_outbox")

        # login in as manager, with contacts but without extra contactfields yet
        self.login(self.admin)
        completions = [
            dict(name="contact", display="Contact Name"),
            dict(name="contact.first_name", display="Contact First Name"),
            dict(name="contact.groups", display="Contact Groups"),
            dict(name="contact.language", display="Contact Language"),
            dict(name="contact.name", display="Contact Name"),
            dict(name="contact.tel", display="Contact Phone"),
            dict(name="contact.tel_e164", display="Contact Phone - E164"),
            dict(name="contact.uuid", display="Contact UUID"),
            dict(name="date", display="Current Date and Time"),
            dict(name="date.now", display="Current Date and Time"),
            dict(name="date.today", display="Current Date"),
            dict(name="date.tomorrow", display="Tomorrow's Date"),
            dict(name="date.yesterday", display="Yesterday's Date"),
        ]

        response = self.client.get(outbox_url)

        # check our completions JSON and functions JSON
        self.assertEqual(response.context["completions"], json.dumps(completions))
        self.assertEqual(response.context["function_completions"], json.dumps(get_function_listing()))

        # add some contact fields
        field = ContactField.get_or_create(self.org, self.admin, "cell", "Cell")
        completions.append(dict(name="contact.%s" % str(field.key), display="Contact Field: Cell"))

        field = ContactField.get_or_create(self.org, self.admin, "sector", "Sector")
        completions.append(dict(name="contact.%s" % str(field.key), display="Contact Field: Sector"))

        response = self.client.get(outbox_url)

        # contact fields are included at the end in alphabetical order
        self.assertEqual(response.context["completions"], json.dumps(completions))

        # a Twitter channel
        Channel.create(self.org, self.user, None, "TT")
        completions.insert(-2, dict(name="contact.%s" % "twitter", display="Contact %s" % "Twitter handle"))
        completions.insert(-2, dict(name="contact.%s" % "twitterid", display="Contact %s" % "Twitter ID"))

        response = self.client.get(outbox_url)
        # the Twitter URN scheme is included
        self.assertEqual(response.context["completions"], json.dumps(completions))

    def test_create_outgoing(self):
        tel_urn = "tel:250788382382"
        tel_contact, tel_urn_obj = Contact.get_or_create(self.org, tel_urn, user=self.user)
        twitter_urn = "twitter:joe"
        twitter_contact, twitter_urn_obj = Contact.get_or_create(self.org, twitter_urn, user=self.user)

        # check creating by URN string
        msg = Msg.create_outgoing(self.org, self.admin, tel_urn, "Extra spaces to remove    ")
        self.assertEqual(msg.contact, tel_contact)
        self.assertEqual(msg.contact_urn, tel_urn_obj)
        self.assertEqual(msg.text, "Extra spaces to remove")  # check message text is stripped

        # check creating by URN string and specific channel
        msg = Msg.create_outgoing(self.org, self.admin, tel_urn, "Hello 1", channel=self.channel)
        self.assertEqual(msg.contact, tel_contact)
        self.assertEqual(msg.contact_urn, tel_urn_obj)

        # try creating by URN string and specific channel with different scheme
        with self.assertRaises(UnreachableException):
            Msg.create_outgoing(self.org, self.admin, twitter_urn, "Hello 1", channel=self.channel)

        # check creating by URN object
        msg = Msg.create_outgoing(self.org, self.admin, tel_urn_obj, "Hello 1")
        self.assertEqual(msg.contact, tel_contact)
        self.assertEqual(msg.contact_urn, tel_urn_obj)

        # check creating by URN object and specific channel
        msg = Msg.create_outgoing(self.org, self.admin, tel_urn_obj, "Hello 1", channel=self.channel)
        self.assertEqual(msg.contact, tel_contact)
        self.assertEqual(msg.contact_urn, tel_urn_obj)

        # try creating by URN object and specific channel with different scheme
        with self.assertRaises(UnreachableException):
            Msg.create_outgoing(self.org, self.admin, twitter_urn_obj, "Hello 1", channel=self.channel)

        # check creating by contact
        msg = Msg.create_outgoing(self.org, self.admin, tel_contact, "Hello 1")
        self.assertEqual(msg.contact, tel_contact)
        self.assertEqual(msg.contact_urn, tel_urn_obj)

        # check creating by contact and specific channel
        msg = Msg.create_outgoing(self.org, self.admin, tel_contact, "Hello 1", channel=self.channel)
        self.assertEqual(msg.contact, tel_contact)
        self.assertEqual(msg.contact_urn, tel_urn_obj)

        # try creating by contact and specific channel with different scheme
        with self.assertRaises(UnreachableException):
            Msg.create_outgoing(self.org, self.admin, twitter_contact, "Hello 1", channel=self.channel)

        # can't create outgoing messages without org or user
        with self.assertRaises(ValueError):
            Msg.create_outgoing(None, self.admin, "tel:250783835665", "Hello World")
        with self.assertRaises(ValueError):
            Msg.create_outgoing(self.org, None, "tel:250783835665", "Hello World")

        # case where the channel number is amongst contact broadcasted to
        # cannot sent more than 10 same message in period of 5 minutes

        for number in range(0, 10):
            Msg.create_outgoing(self.org, self.admin, "tel:" + self.channel.address, "Infinite Loop")

        # now that we have 10 same messages then,
        must_return_none = Msg.create_outgoing(self.org, self.admin, "tel:" + self.channel.address, "Infinite Loop")
        self.assertIsNone(must_return_none)

        # test create_outgoing with sent_on in the past
        t = datetime(2018, 5, 17, 17, 29, 30, 0, pytz.UTC)
        msg = Msg.create_outgoing(self.org, self.admin, tel_contact, "Hello at time", channel=self.channel, sent_on=t)
        self.assertEqual(msg.sent_on, t)
        self.assertGreater(msg.created_on, msg.sent_on)

    def test_create_incoming(self):
        Msg.create_incoming(self.channel, "tel:250788382382", "It's going well")
        Msg.create_incoming(self.channel, "tel:250788382382", "My name is Frank")
        msg = Msg.create_incoming(self.channel, "tel:250788382382", "Yes, 3.")

        self.assertEqual(msg.text, "Yes, 3.")
        self.assertEqual(str(msg), "Yes, 3.")

        # Can't send incoming messages
        with self.assertRaises(Exception):
            msg.send()

        # can't create outgoing messages against an unassigned channel
        unassigned_channel = Channel.create(
            None, self.admin, None, "A", None, secret=Channel.generate_secret(), config={Channel.CONFIG_FCM_ID: "456"}
        )

        with self.assertRaises(Exception):
            Msg.create_incoming(unassigned_channel, "tel:250788382382", "No dice")

        # test blocked contacts are skipped from inbox and are not handled by flows
        contact = self.create_contact("Blocked contact", "250728739305")
        contact.is_blocked = True
        contact.save(update_fields=("is_blocked",), handle_update=False)
        ignored_msg = Msg.create_incoming(self.channel, str(contact.get_urn()), "My msg should be archived")
        ignored_msg = Msg.objects.get(pk=ignored_msg.pk)
        self.assertEqual(ignored_msg.visibility, Msg.VISIBILITY_ARCHIVED)
        self.assertEqual(ignored_msg.status, HANDLED)

        # hit the inbox page, that should reset our unread count
        self.login(self.admin)
        self.client.get(reverse("msgs.msg_inbox"))

        # test that invalid chars are stripped from message text
        msg5 = Msg.create_incoming(self.channel, "tel:250788382382", "Don't be null!\x00")
        self.assertEqual(msg5.text, "Don't be null!")

        # test create_incoming with a sent_on value
        t = datetime(2018, 5, 17, 17, 29, 30, 0, pytz.UTC)
        msg = Msg.create_incoming(self.channel, "tel:250788382382", "It's going well", sent_on=t)
        self.assertEqual(msg.sent_on, t)
        self.assertGreater(msg.created_on, msg.sent_on)

    def test_empty(self):
        broadcast = Broadcast.create(
            self.org, self.admin, "If a broadcast is sent and nobody receives it, does it still send?", contacts=[]
        )
        broadcast.send()

        # should have no messages but marked as sent
        self.assertEqual(0, broadcast.recipient_count)
        self.assertEqual(0, broadcast.get_message_count())
        self.assertEqual(SENT, broadcast.status)

    def test_send_all(self):
        contact = self.create_contact("Stephen", "+12078778899")
        ContactURN.get_or_create(self.org, contact, "tel:+12078778800")
        broadcast = Broadcast.create(
            self.org,
            self.admin,
            "If a broadcast is sent and nobody receives it, does it still send?",
            contacts=[contact],
            send_all=True,
        )
        broadcast.send()

        self.assertEqual(2, broadcast.msgs.all().count())
        self.assertEqual(1, broadcast.msgs.all().filter(contact_urn__path="+12078778899").count())
        self.assertEqual(1, broadcast.msgs.all().filter(contact_urn__path="+12078778800").count())

    def test_broadcast_metadata(self):
        Channel.create(self.org, self.admin, None, channel_type="TT")
        contact1 = self.create_contact("Stephen", "+12078778899", language="fra")
        contact2 = self.create_contact("Maaaarcos", number="+12078778888", twitter="marky65")

        # can't create quick replies if you don't include base translation
        with self.assertRaises(ValueError):
            Broadcast.create(
                self.org,
                self.admin,
                "If a broadcast is sent and nobody receives it, does it still send?",
                contacts=[contact1],
                quick_replies=[dict(eng="Yes"), dict(eng="No")],
            )

        eng = Language.create(self.org, self.admin, "English", "eng")
        Language.create(self.org, self.admin, "French", "fra")
        self.org.primary_language = eng
        self.org.save()

        broadcast = Broadcast.create(
            self.org,
            self.admin,
            "If a broadcast is sent and nobody receives it, does it still send?",
            contacts=[contact1, contact2],
            send_all=True,
            quick_replies=[dict(eng="Yes", fra="Oui"), dict(eng="No")],
        )

        # check metadata was set on the broadcast
        self.assertEqual(broadcast.metadata, {"quick_replies": [{"eng": "Yes", "fra": "Oui"}, {"eng": "No"}]})

        broadcast.send()
        msg1, msg2, msg3 = broadcast.msgs.order_by("contact", "id")

        # message quick_replies are translated according to contact language
        self.assertEqual(msg1.metadata, {"quick_replies": ["Oui", "No"]})
        self.assertEqual(msg2.metadata, {"quick_replies": ["Yes", "No"]})
        self.assertEqual(msg3.metadata, {"quick_replies": ["Yes", "No"]})

    def test_outbox(self):
        self.login(self.admin)

        contact, urn_obj = Contact.get_or_create(self.channel.org, "tel:250788382382", user=self.admin)
        broadcast1 = Broadcast.create(self.channel.org, self.admin, "How is it going?", contacts=[contact])

        # now send the broadcast so we have messages, but put them back into pending state
        broadcast1.send()
        Msg.objects.filter(broadcast=broadcast1).update(status=PENDING)

        (msg1,) = tuple(Msg.objects.filter(broadcast=broadcast1))

        with self.assertNumQueries(45):
            response = self.client.get(reverse("msgs.msg_outbox"))

        self.assertContains(response, "Outbox (1)")
        self.assertEqual(set(response.context_data["object_list"]), {msg1})

        broadcast2 = Broadcast.create(
            self.channel.org,
            self.admin,
            "kLab is an awesome place",
            contacts=[self.kevin],
            groups=[self.joe_and_frank],
        )

        # now send the broadcast so we have messages
        broadcast2.send()
        Msg.objects.filter(broadcast=broadcast2).update(status=PENDING)
        msg4, msg3, msg2 = tuple(Msg.objects.filter(broadcast=broadcast2).order_by("-created_on", "-id"))

        broadcast3 = Broadcast.create(
            self.channel.org, self.admin, "Pending broadcast", contacts=[self.kevin], status=QUEUED
        )

        broadcast4 = Broadcast.create(
            self.channel.org, self.admin, "Scheduled broadcast", contacts=[self.kevin], status=QUEUED
        )

        broadcast4.schedule = Schedule.create_schedule(timezone.now(), "D", self.admin)
        broadcast4.save(update_fields=["schedule"])

        with self.assertNumQueries(39):
            response = self.client.get(reverse("msgs.msg_outbox"))

        self.assertContains(response, "Outbox (5)")
        self.assertEqual(list(response.context_data["object_list"]), [msg4, msg3, msg2, msg1])
        self.assertEqual(list(response.context_data["pending_broadcasts"]), [broadcast3])

        response = self.client.get("%s?search=kevin" % reverse("msgs.msg_outbox"))
        self.assertEqual(list(response.context_data["object_list"]), [Msg.objects.get(contact=self.kevin)])

        response = self.client.get("%s?search=joe" % reverse("msgs.msg_outbox"))
        self.assertEqual(list(response.context_data["object_list"]), [Msg.objects.get(contact=self.joe)])

        response = self.client.get("%s?search=frank" % reverse("msgs.msg_outbox"))
        self.assertEqual(list(response.context_data["object_list"]), [Msg.objects.get(contact=self.frank)])

        response = self.client.get("%s?search=just" % reverse("msgs.msg_outbox"))
        self.assertEqual(list(response.context_data["object_list"]), list())

        response = self.client.get("%s?search=klab" % reverse("msgs.msg_outbox"))
        self.assertEqual(list(response.context_data["object_list"]), [msg4, msg3, msg2])

        # make sure variables that are replaced in text messages match as well
        response = self.client.get("%s?search=durant" % reverse("msgs.msg_outbox"))
        self.assertEqual(list(response.context_data["object_list"]), [Msg.objects.get(contact=self.kevin)])

    def do_msg_action(self, url, msgs, action, label=None, label_add=True):
        post_data = dict()
        post_data["action"] = action
        post_data["objects"] = [m.id for m in msgs]
        post_data["label"] = label.pk if label else None
        post_data["add"] = label_add
        return self.client.post(url, post_data, follow=True)

    def test_inbox(self):
        inbox_url = reverse("msgs.msg_inbox")

        joe_tel = str(self.joe.get_urn(TEL_SCHEME))
        msg1 = Msg.create_incoming(self.channel, joe_tel, "message number 1")
        msg2 = Msg.create_incoming(self.channel, joe_tel, "message number 2")
        msg3 = Msg.create_incoming(self.channel, joe_tel, "message number 3")
        Msg.create_incoming(self.channel, joe_tel, "message number 4")
        msg5 = Msg.create_incoming(self.channel, joe_tel, "message number 5")
        msg6 = Msg.create_incoming(self.channel, joe_tel, "message number 6")

        # msg6 is still pending
        msg6.status = PENDING
        msg6.msg_type = None
        msg6.save()

        # visit inbox page  as a user not in the organization
        self.login(self.non_org_user)
        response = self.client.get(inbox_url)
        self.assertEqual(302, response.status_code)

        # visit inbox page as a manager of the organization
        with self.assertNumQueries(62):
            response = self.fetch_protected(inbox_url + "?refresh=10000", self.admin)

        # make sure that we embed refresh script if View.refresh is set
        self.assertContains(response, "function refresh")

        self.assertEqual(response.context["refresh"], 20000)
        self.assertEqual(response.context["object_list"].count(), 5)
        self.assertEqual(response.context["folders"][0]["url"], "/msg/inbox/")
        self.assertEqual(response.context["folders"][0]["count"], 5)
        self.assertEqual(response.context["actions"], ["archive", "label"])

        # visit inbox page as administrator
        response = self.fetch_protected(inbox_url, self.admin)

        self.assertEqual(response.context["object_list"].count(), 5)
        self.assertEqual(response.context["actions"], ["archive", "label"])

        # let's add some labels
        folder = Label.get_or_create_folder(self.org, self.user, "folder")
        label1 = Label.get_or_create(self.org, self.user, "label1", folder)
        Label.get_or_create(self.org, self.user, "label2", folder)
        label3 = Label.get_or_create(self.org, self.user, "label3")

        # test labeling a messages
        self.do_msg_action(inbox_url, [msg1, msg2], "label", label1)
        self.assertEqual(set(label1.msgs.all()), {msg1, msg2})

        # test removing a label
        self.do_msg_action(inbox_url, [msg2], "label", label1, label_add=False)
        self.assertEqual(set(label1.msgs.all()), {msg1})

        # label more messages
        self.do_msg_action(inbox_url, [msg1, msg2, msg3], "label", label3)
        self.assertEqual(set(label1.msgs.all()), {msg1})
        self.assertEqual(set(label3.msgs.all()), {msg1, msg2, msg3})

        # update our label name
        response = self.client.get(reverse("msgs.label_update", args=[label1.pk]))
        self.assertEqual(200, response.status_code)
        self.assertIn("folder", response.context["form"].fields)

        post_data = dict(name="Foo")
        response = self.client.post(reverse("msgs.label_update", args=[label1.pk]), post_data)
        self.assertEqual(302, response.status_code)
        label1 = Label.label_objects.get(pk=label1.pk)
        self.assertEqual("Foo", label1.name)

        # test deleting the label
        response = self.client.get(reverse("msgs.label_delete", args=[label1.pk]))
        self.assertEqual(200, response.status_code)

        response = self.client.post(reverse("msgs.label_delete", args=[label1.pk]))
        self.assertEqual(302, response.status_code)
        self.assertFalse(Label.label_objects.filter(pk=label1.id))

        # shouldn't have a remove on the update page

        # test archiving a msg
        self.assertEqual(set(msg1.labels.all()), {label3})
        post_data = dict(action="archive", objects=msg1.pk)

        response = self.client.post(inbox_url, post_data, follow=True)
        self.assertEqual(response.status_code, 200)

        # now one msg is archived
        self.assertEqual(list(Msg.objects.filter(visibility=Msg.VISIBILITY_ARCHIVED)), [msg1])

        # archiving doesn't remove labels
        msg1 = Msg.objects.get(pk=msg1.pk)
        self.assertEqual(set(msg1.labels.all()), {label3})

        # visit the the archived messages page
        archive_url = reverse("msgs.msg_archived")

        # visit archived page  as a user not in the organization
        self.login(self.non_org_user)
        response = self.client.get(archive_url)
        self.assertEqual(302, response.status_code)

        # visit archived page as a manager of the organization
        with self.assertNumQueries(54):
            response = self.fetch_protected(archive_url, self.admin)

        self.assertEqual(response.context["object_list"].count(), 1)
        self.assertEqual(response.context["actions"], ["restore", "label", "delete"])

        # check that the inbox does not contains archived messages

        # visit inbox page as a user not in the organization
        self.login(self.non_org_user)
        response = self.client.get(inbox_url)
        self.assertEqual(302, response.status_code)

        # visit inbox page as an admin of the organization
        response = self.fetch_protected(inbox_url, self.admin)

        self.assertEqual(response.context["object_list"].count(), 4)
        self.assertEqual(response.context["actions"], ["archive", "label"])

        # test restoring an archived message back to inbox
        post_data = dict(action="restore", objects=[msg1.pk])
        self.client.post(inbox_url, post_data, follow=True)
        self.assertEqual(Msg.objects.filter(visibility=Msg.VISIBILITY_ARCHIVED).count(), 0)

        response = self.client.get(inbox_url)
        self.assertEqual(Msg.objects.all().count(), 6)
        self.assertEqual(response.context["object_list"].count(), 5)

        # archiving a message removes it from the inbox
        Msg.apply_action_archive(self.user, [msg1])

        response = self.client.get(inbox_url)
        self.assertEqual(response.context["object_list"].count(), 4)

        # and moves it to the Archived page
        response = self.client.get(archive_url)
        self.assertEqual(response.context["object_list"].count(), 1)

        # deleting it removes it from the Archived page
        response = self.client.post(archive_url, dict(action="delete", objects=[msg1.pk]), follow=True)
        self.assertEqual(response.context["object_list"].count(), 0)

        # now check inbox as viewer user
        response = self.fetch_protected(inbox_url, self.user)
        self.assertEqual(response.context["object_list"].count(), 4)

        # check that viewer user cannot label messages
        post_data = dict(action="label", objects=[msg5.pk], label=label1.pk, add=True)
        self.client.post(inbox_url, post_data, follow=True)
        self.assertEqual(msg5.labels.all().count(), 0)

        # or archive messages
        self.assertEqual(Msg.objects.get(pk=msg5.pk).visibility, Msg.VISIBILITY_VISIBLE)
        post_data = dict(action="archive", objects=[msg5.pk])
        self.client.post(inbox_url, post_data, follow=True)
        self.assertEqual(Msg.objects.get(pk=msg5.pk).visibility, Msg.VISIBILITY_VISIBLE)

        # search on inbox just on the message text
        response = self.client.get("%s?search=message" % inbox_url)
        self.assertEqual(len(response.context_data["object_list"]), 4)

        response = self.client.get("%s?search=5" % inbox_url)
        self.assertEqual(len(response.context_data["object_list"]), 1)

        # can search on contact field
        response = self.client.get("%s?search=joe" % inbox_url)
        self.assertEqual(len(response.context_data["object_list"]), 4)

    def test_flows(self):
        url = reverse("msgs.msg_flow")

        msg1 = Msg.create_incoming(self.channel, str(self.joe.get_urn()), "test 1", msg_type="F")
        msg2 = Msg.create_incoming(self.channel, str(self.joe.get_urn()), "test 2", msg_type="F")
        msg3 = Msg.create_incoming(self.channel, str(self.joe.get_urn()), "test 3", msg_type="F")

        # user not in org can't access
        self.login(self.non_org_user)
        self.assertRedirect(self.client.get(url), reverse("orgs.org_choose"))

        # org viewer can
        self.login(self.admin)

        with self.assertNumQueries(42):
            response = self.client.get(url)

        self.assertEqual(set(response.context["object_list"]), {msg3, msg2, msg1})
        self.assertEqual(response.context["actions"], ["label"])

    def test_failed(self):
        failed_url = reverse("msgs.msg_failed")

        msg1 = Msg.create_outgoing(self.org, self.admin, self.joe, "message number 1")
        msg1.status = "F"
        msg1.save()

        # create a log for it
        log = ChannelLog.objects.create(channel=msg1.channel, msg=msg1, is_error=True, description="Failed")

        # create broadcast and fail the only message
        broadcast = Broadcast.create(
            self.org,
            self.admin,
            "message number 2",
            contacts=[self.joe],
            quick_replies=[{"base": "Yes"}, {"base": "No"}],
        )
        broadcast.send()
        broadcast.get_messages().update(status="F")
        msg2 = broadcast.get_messages()[0]

        # message without a broadcast
        msg3 = Msg.create_outgoing(self.org, self.admin, self.joe, "messsage number 3")
        msg3.status = "F"
        msg3.save()

        # visit fail page  as a user not in the organization
        self.login(self.non_org_user)
        response = self.client.get(failed_url)
        self.assertEqual(302, response.status_code)

        # visit failed page as an administrator
        with self.assertNumQueries(65):
            response = self.fetch_protected(failed_url, self.admin)

        self.assertEqual(response.context["object_list"].count(), 3)
        self.assertEqual(response.context["actions"], ["resend"])
        self.assertContains(response, "Export")

        self.assertContains(response, reverse("channels.channellog_read", args=[log.id]))

        # make the org anonymous
        with AnonymousOrg(self.org):
            response = self.fetch_protected(failed_url, self.admin)
            self.assertNotContains(response, reverse("channels.channellog_read", args=[log.id]))

        # let's resend some messages
        self.client.post(failed_url, dict(action="resend", objects=msg2.id), follow=True)

        # check for the resent message and the new one being resent
        self.assertEqual(set(Msg.objects.filter(status=RESENT)), {msg2})
        self.assertEqual(Msg.objects.filter(status=WIRED).count(), 1)

        # make sure there was a new outgoing message created that got attached to our broadcast
        self.assertEqual(2, broadcast.get_message_count())

        resent_msg = broadcast.msgs.order_by("-pk")[0]
        self.assertNotEqual(msg2, resent_msg)
        self.assertEqual(resent_msg.text, msg2.text)
        self.assertEqual(resent_msg.contact, msg2.contact)
        self.assertEqual(resent_msg.status, WIRED)
        self.assertEqual(resent_msg.metadata, {"quick_replies": ["Yes", "No"]})

    @patch("temba.utils.email.send_temba_email")
    def test_message_export_from_archives(self, mock_send_temba_email):
        self.clear_storage()
        self.login(self.admin)

        self.joe.name = "Jo\02e Blow"
        self.joe.save(update_fields=("name",), handle_update=False)

        self.org.created_on = datetime(2017, 1, 1, 9, tzinfo=pytz.UTC)
        self.org.save()

        msg1 = self.create_msg(
            contact=self.joe,
            text="hello 1",
            direction="I",
            status=HANDLED,
            msg_type="I",
            created_on=datetime(2017, 1, 1, 10, tzinfo=pytz.UTC),
        )
        msg2 = self.create_msg(
            contact=self.frank,
            text="hello 2",
            direction="I",
            status=HANDLED,
            msg_type="F",
            created_on=datetime(2017, 1, 2, 10, tzinfo=pytz.UTC),
        )
        msg3 = self.create_msg(
            contact=self.joe,
            text="hello 3",
            direction="I",
            status=HANDLED,
            msg_type="I",
            created_on=datetime(2017, 1, 3, 10, tzinfo=pytz.UTC),
        )

        # inbound message that looks like a surveyor message
        msg4 = self.create_msg(
            contact=self.joe,
            contact_urn=None,
            text="hello 4",
            direction="I",
            status=HANDLED,
            channel=None,
            msg_type="I",
            created_on=datetime(2017, 1, 4, 10, tzinfo=pytz.UTC),
        )

        # inbound message with media attached, such as an ivr recording
        msg5 = self.create_msg(
            contact=self.joe,
            text="Media message",
            direction="I",
            status=HANDLED,
            msg_type="I",
            attachments=["audio:http://rapidpro.io/audio/sound.mp3"],
            created_on=datetime(2017, 1, 5, 10, tzinfo=pytz.UTC),
        )

        # create some outbound messages with different statuses
        msg6 = self.create_msg(
            contact=self.joe,
            text="Hey out 6",
            direction="O",
            status=SENT,
            created_on=datetime(2017, 1, 6, 10, tzinfo=pytz.UTC),
        )
        msg7 = self.create_msg(
            contact=self.joe,
            text="Hey out 7",
            direction="O",
            status=DELIVERED,
            created_on=datetime(2017, 1, 7, 10, tzinfo=pytz.UTC),
        )
        msg8 = self.create_msg(
            contact=self.joe,
            text="Hey out 8",
            direction="O",
            status=ERRORED,
            created_on=datetime(2017, 1, 8, 10, tzinfo=pytz.UTC),
        )
        msg9 = self.create_msg(
            contact=self.joe,
            text="Hey out 9",
            direction="O",
            status=FAILED,
            created_on=datetime(2017, 1, 9, 10, tzinfo=pytz.UTC),
        )

        self.assertEqual(msg5.get_attachments(), [Attachment("audio", "http://rapidpro.io/audio/sound.mp3")])

        # label first message
        folder = Label.get_or_create_folder(self.org, self.user, "Folder")
        label = Label.get_or_create(self.org, self.user, "la\02bel1", folder=folder)
        label.toggle_label([msg1], add=True)

        # archive last message
        msg3.visibility = Msg.VISIBILITY_ARCHIVED
        msg3.save()

        # archive 5 msgs
        Archive.objects.create(
            org=self.org,
            archive_type=Archive.TYPE_MSG,
            size=10,
            hash=uuid4().hex,
            url="http://test-bucket.aws.com/archive1.jsonl.gz",
            record_count=6,
            start_date=msg5.created_on.date(),
            period="D",
            build_time=23425,
        )
        mock_s3 = MockS3Client()
        mock_s3.put_jsonl(
            "test-bucket",
            "archive1.jsonl.gz",
            [
                msg1.as_archive_json(),
                msg2.as_archive_json(),
                msg3.as_archive_json(),
                msg4.as_archive_json(),
                msg5.as_archive_json(),
                msg6.as_archive_json(),
            ],
        )

        msg2.release()
        msg3.release()
        msg4.release()
        msg5.release()
        msg6.release()

        # create an archive earlier than our flow created date so we check that it isn't included
        Archive.objects.create(
            org=self.org,
            archive_type=Archive.TYPE_MSG,
            size=10,
            hash=uuid4().hex,
            url="http://test-bucket.aws.com/archive2.jsonl.gz",
            record_count=1,
            start_date=self.org.created_on - timedelta(days=2),
            period="D",
            build_time=5678,
        )
        mock_s3.put_jsonl("test-bucket", "archive2.jsonl.gz", [msg7.as_archive_json()])

        msg7.release()

        def request_export(query, data=None):
            response = self.client.post(reverse("msgs.msg_export") + query, data)
            self.assertEqual(response.status_code, 302)
            task = ExportMessagesTask.objects.order_by("-id").first()
            filename = "%s/test_orgs/%d/message_exports/%s.xlsx" % (settings.MEDIA_ROOT, self.org.id, task.uuid)
            return load_workbook(filename=filename)

        # export all visible messages (i.e. not msg3) using export_all param
        with self.assertNumQueries(31):
            with patch("temba.archives.models.Archive.s3_client", return_value=mock_s3):
                workbook = request_export("?l=I", {"export_all": 1})

        self.assertExcelSheet(
            workbook.worksheets[0],
            [
                [
                    "Date",
                    "Contact UUID",
                    "Name",
                    "URN",
                    "URN Type",
                    "Direction",
                    "Text",
                    "Attachments",
                    "Status",
                    "Channel",
                    "Labels",
                ],
                [
                    msg1.created_on,
                    msg1.contact.uuid,
                    "Joe Blow",
                    "123",
                    "tel",
                    "IN",
                    "hello 1",
                    "",
                    "handled",
                    "Test Channel",
                    "label1",
                ],
                [
                    msg2.created_on,
                    msg2.contact.uuid,
                    "Frank Blow",
                    "321",
                    "tel",
                    "IN",
                    "hello 2",
                    "",
                    "handled",
                    "Test Channel",
                    "",
                ],
                [msg4.created_on, msg1.contact.uuid, "Joe Blow", "", "", "IN", "hello 4", "", "handled", "", ""],
                [
                    msg5.created_on,
                    msg5.contact.uuid,
                    "Joe Blow",
                    "123",
                    "tel",
                    "IN",
                    "Media message",
                    "http://rapidpro.io/audio/sound.mp3",
                    "handled",
                    "Test Channel",
                    "",
                ],
                [
                    msg6.created_on,
                    msg6.contact.uuid,
                    "Joe Blow",
                    "123",
                    "tel",
                    "OUT",
                    "Hey out 6",
                    "",
                    "sent",
                    "Test Channel",
                    "",
                ],
                [
                    msg8.created_on,
                    msg8.contact.uuid,
                    "Joe Blow",
                    "123",
                    "tel",
                    "OUT",
                    "Hey out 8",
                    "",
                    "errored",
                    "Test Channel",
                    "",
                ],
                [
                    msg9.created_on,
                    msg9.contact.uuid,
                    "Joe Blow",
                    "123",
                    "tel",
                    "OUT",
                    "Hey out 9",
                    "",
                    "failed",
                    "Test Channel",
                    "",
                ],
            ],
            self.org.timezone,
        )

        with patch("temba.archives.models.Archive.s3_client", return_value=mock_s3):
            workbook = request_export(
                "?l=I",
                {
                    "export_all": 0,
                    "start_date": msg5.created_on.strftime("%B %d, %Y"),
                    "end_date": msg7.created_on.strftime("%B %d, %Y"),
                },
            )

        self.assertExcelSheet(
            workbook.worksheets[0],
            [
                [
                    "Date",
                    "Contact UUID",
                    "Name",
                    "URN",
                    "URN Type",
                    "Direction",
                    "Text",
                    "Attachments",
                    "Status",
                    "Channel",
                    "Labels",
                ],
                [
                    msg5.created_on,
                    msg5.contact.uuid,
                    "Joe Blow",
                    "123",
                    "tel",
                    "IN",
                    "Media message",
                    "http://rapidpro.io/audio/sound.mp3",
                    "handled",
                    "Test Channel",
                    "",
                ],
            ],
            self.org.timezone,
        )

        with patch("temba.archives.models.Archive.s3_client", return_value=mock_s3):
            workbook = request_export("?l=I", {"export_all": 1, "groups": [self.just_joe.id]})

        self.assertExcelSheet(
            workbook.worksheets[0],
            [
                [
                    "Date",
                    "Contact UUID",
                    "Name",
                    "URN",
                    "URN Type",
                    "Direction",
                    "Text",
                    "Attachments",
                    "Status",
                    "Channel",
                    "Labels",
                ],
                [
                    msg1.created_on,
                    msg1.contact.uuid,
                    "Joe Blow",
                    "123",
                    "tel",
                    "IN",
                    "hello 1",
                    "",
                    "handled",
                    "Test Channel",
                    "label1",
                ],
                [msg4.created_on, msg1.contact.uuid, "Joe Blow", "", "", "IN", "hello 4", "", "handled", "", ""],
                [
                    msg5.created_on,
                    msg5.contact.uuid,
                    "Joe Blow",
                    "123",
                    "tel",
                    "IN",
                    "Media message",
                    "http://rapidpro.io/audio/sound.mp3",
                    "handled",
                    "Test Channel",
                    "",
                ],
                [
                    msg6.created_on,
                    msg6.contact.uuid,
                    "Joe Blow",
                    "123",
                    "tel",
                    "OUT",
                    "Hey out 6",
                    "",
                    "sent",
                    "Test Channel",
                    "",
                ],
                [
                    msg8.created_on,
                    msg8.contact.uuid,
                    "Joe Blow",
                    "123",
                    "tel",
                    "OUT",
                    "Hey out 8",
                    "",
                    "errored",
                    "Test Channel",
                    "",
                ],
                [
                    msg9.created_on,
                    msg9.contact.uuid,
                    "Joe Blow",
                    "123",
                    "tel",
                    "OUT",
                    "Hey out 9",
                    "",
                    "failed",
                    "Test Channel",
                    "",
                ],
            ],
            self.org.timezone,
        )

        with patch("temba.archives.models.Archive.s3_client", return_value=mock_s3):
            workbook = request_export("?l=S", {"export_all": 0})

        self.assertExcelSheet(
            workbook.worksheets[0],
            [
                [
                    "Date",
                    "Contact UUID",
                    "Name",
                    "URN",
                    "URN Type",
                    "Direction",
                    "Text",
                    "Attachments",
                    "Status",
                    "Channel",
                    "Labels",
                ],
                [
                    msg6.created_on,
                    msg6.contact.uuid,
                    "Joe Blow",
                    "123",
                    "tel",
                    "OUT",
                    "Hey out 6",
                    "",
                    "sent",
                    "Test Channel",
                    "",
                ],
            ],
            self.org.timezone,
        )

        with patch("temba.archives.models.Archive.s3_client", return_value=mock_s3):
            workbook = request_export("?l=X", {"export_all": 0})

        self.assertExcelSheet(
            workbook.worksheets[0],
            [
                [
                    "Date",
                    "Contact UUID",
                    "Name",
                    "URN",
                    "URN Type",
                    "Direction",
                    "Text",
                    "Attachments",
                    "Status",
                    "Channel",
                    "Labels",
                ],
                [
                    msg9.created_on,
                    msg9.contact.uuid,
                    "Joe Blow",
                    "123",
                    "tel",
                    "OUT",
                    "Hey out 9",
                    "",
                    "failed",
                    "Test Channel",
                    "",
                ],
            ],
            self.org.timezone,
        )

        with patch("temba.archives.models.Archive.s3_client", return_value=mock_s3):
            workbook = request_export("?l=W", {"export_all": 0})

        self.assertExcelSheet(
            workbook.worksheets[0],
            [
                [
                    "Date",
                    "Contact UUID",
                    "Name",
                    "URN",
                    "URN Type",
                    "Direction",
                    "Text",
                    "Attachments",
                    "Status",
                    "Channel",
                    "Labels",
                ],
                [
                    msg2.created_on,
                    msg2.contact.uuid,
                    "Frank Blow",
                    "321",
                    "tel",
                    "IN",
                    "hello 2",
                    "",
                    "handled",
                    "Test Channel",
                    "",
                ],
            ],
            self.org.timezone,
        )

        with patch("temba.archives.models.Archive.s3_client", return_value=mock_s3):
            workbook = request_export(f"?l={label.uuid}", {"export_all": 0})

        self.assertExcelSheet(
            workbook.worksheets[0],
            [
                [
                    "Date",
                    "Contact UUID",
                    "Name",
                    "URN",
                    "URN Type",
                    "Direction",
                    "Text",
                    "Attachments",
                    "Status",
                    "Channel",
                    "Labels",
                ],
                [
                    msg1.created_on,
                    msg1.contact.uuid,
                    "Joe Blow",
                    "123",
                    "tel",
                    "IN",
                    "hello 1",
                    "",
                    "handled",
                    "Test Channel",
                    "label1",
                ],
            ],
            self.org.timezone,
        )

    @patch("temba.utils.email.send_temba_email")
    def test_message_export(self, mock_send_temba_email):
        self.clear_storage()
        self.login(self.admin)

        self.joe.name = "Jo\02e Blow"
        self.joe.save(update_fields=("name",), handle_update=False)

        msg1 = self.create_msg(
            contact=self.joe,
            text="hello 1",
            direction="I",
            status=HANDLED,
            msg_type="I",
            created_on=datetime(2017, 1, 1, 10, tzinfo=pytz.UTC),
        )
        msg2 = self.create_msg(
            contact=self.joe,
            text="hello 2",
            direction="I",
            status=HANDLED,
            msg_type="I",
            created_on=datetime(2017, 1, 2, 10, tzinfo=pytz.UTC),
        )
        msg3 = self.create_msg(
            contact=self.joe,
            text="hello 3",
            direction="I",
            status=HANDLED,
            msg_type="I",
            created_on=datetime(2017, 1, 3, 10, tzinfo=pytz.UTC),
        )

        # inbound message that looks like a surveyor message
        msg4 = self.create_msg(
            contact=self.joe,
            contact_urn=None,
            text="hello 4",
            direction="I",
            status=HANDLED,
            channel=None,
            msg_type="I",
            created_on=datetime(2017, 1, 4, 10, tzinfo=pytz.UTC),
        )

        # inbound message with media attached, such as an ivr recording
        msg5 = self.create_msg(
            contact=self.joe,
            text="Media message",
            direction="I",
            status=HANDLED,
            msg_type="I",
            attachments=["audio:http://rapidpro.io/audio/sound.mp3"],
            created_on=datetime(2017, 1, 5, 10, tzinfo=pytz.UTC),
        )

        # create some outbound messages with different statuses
        msg6 = self.create_msg(
            contact=self.joe,
            text="Hey out 6",
            direction="O",
            status=SENT,
            created_on=datetime(2017, 1, 6, 10, tzinfo=pytz.UTC),
        )
        msg7 = self.create_msg(
            contact=self.joe,
            text="Hey out 7",
            direction="O",
            status=DELIVERED,
            created_on=datetime(2017, 1, 7, 10, tzinfo=pytz.UTC),
        )
        msg8 = self.create_msg(
            contact=self.joe,
            text="Hey out 8",
            direction="O",
            status=ERRORED,
            created_on=datetime(2017, 1, 8, 10, tzinfo=pytz.UTC),
        )
        msg9 = self.create_msg(
            contact=self.joe,
            text="Hey out 9",
            direction="O",
            status=FAILED,
            created_on=datetime(2017, 1, 9, 10, tzinfo=pytz.UTC),
        )

        self.assertEqual(msg5.get_attachments(), [Attachment("audio", "http://rapidpro.io/audio/sound.mp3")])

        # label first message
        folder = Label.get_or_create_folder(self.org, self.user, "Folder")
        label = Label.get_or_create(self.org, self.user, "la\02bel1", folder=folder)
        label.toggle_label([msg1], add=True)

        # archive last message
        msg3.visibility = Msg.VISIBILITY_ARCHIVED
        msg3.save()

        # create a dummy export task so that we won't be able to export
        blocking_export = ExportMessagesTask.create(self.org, self.admin, SystemLabel.TYPE_INBOX)

        old_modified_on = blocking_export.modified_on

        response = self.client.post(reverse("msgs.msg_export") + "?l=I", {"export_all": 1}, follow=True)
        self.assertContains(response, "already an export in progress")

        # perform the export manually, assert how many queries
        self.assertNumQueries(11, lambda: blocking_export.perform())

        blocking_export.refresh_from_db()
        # after performing the export `modified_on` should be updated
        self.assertNotEqual(old_modified_on, blocking_export.modified_on)

        def request_export(query, data=None):
            response = self.client.post(reverse("msgs.msg_export") + query, data)
            self.assertEqual(response.status_code, 302)
            task = ExportMessagesTask.objects.order_by("-id").first()
            filename = "%s/test_orgs/%d/message_exports/%s.xlsx" % (settings.MEDIA_ROOT, self.org.id, task.uuid)
            workbook = load_workbook(filename=filename)
            return workbook.worksheets[0]

        # export all visible messages (i.e. not msg3) using export_all param
        with self.assertLogs("temba.msgs.models", level="INFO") as captured_logger:
            with patch(
                "temba.msgs.models.ExportMessagesTask.LOG_PROGRESS_PER_ROWS", new_callable=PropertyMock
            ) as log_info_threshold:
                # make sure that we trigger logger
                log_info_threshold.return_value = 5

                with self.assertNumQueries(29):
                    self.assertExcelSheet(
                        request_export("?l=I", {"export_all": 1}),
                        [
                            [
                                "Date",
                                "Contact UUID",
                                "Name",
                                "URN",
                                "URN Type",
                                "Direction",
                                "Text",
                                "Attachments",
                                "Status",
                                "Channel",
                                "Labels",
                            ],
                            [
                                msg1.created_on,
                                msg1.contact.uuid,
                                "Joe Blow",
                                "123",
                                "tel",
                                "IN",
                                "hello 1",
                                "",
                                "handled",
                                "Test Channel",
                                "label1",
                            ],
                            [
                                msg2.created_on,
                                msg2.contact.uuid,
                                "Joe Blow",
                                "123",
                                "tel",
                                "IN",
                                "hello 2",
                                "",
                                "handled",
                                "Test Channel",
                                "",
                            ],
                            [
                                msg4.created_on,
                                msg4.contact.uuid,
                                "Joe Blow",
                                "",
                                "",
                                "IN",
                                "hello 4",
                                "",
                                "handled",
                                "",
                                "",
                            ],
                            [
                                msg5.created_on,
                                msg5.contact.uuid,
                                "Joe Blow",
                                "123",
                                "tel",
                                "IN",
                                "Media message",
                                "http://rapidpro.io/audio/sound.mp3",
                                "handled",
                                "Test Channel",
                                "",
                            ],
                            [
                                msg6.created_on,
                                msg6.contact.uuid,
                                "Joe Blow",
                                "123",
                                "tel",
                                "OUT",
                                "Hey out 6",
                                "",
                                "sent",
                                "Test Channel",
                                "",
                            ],
                            [
                                msg7.created_on,
                                msg7.contact.uuid,
                                "Joe Blow",
                                "123",
                                "tel",
                                "OUT",
                                "Hey out 7",
                                "",
                                "delivered",
                                "Test Channel",
                                "",
                            ],
                            [
                                msg8.created_on,
                                msg8.contact.uuid,
                                "Joe Blow",
                                "123",
                                "tel",
                                "OUT",
                                "Hey out 8",
                                "",
                                "errored",
                                "Test Channel",
                                "",
                            ],
                            [
                                msg9.created_on,
                                msg9.contact.uuid,
                                "Joe Blow",
                                "123",
                                "tel",
                                "OUT",
                                "Hey out 9",
                                "",
                                "failed",
                                "Test Channel",
                                "",
                            ],
                        ],
                        self.org.timezone,
                    )

                self.assertEqual(len(captured_logger.output), 3)
                self.assertTrue("fetching msgs from archives to export" in captured_logger.output[0])
                self.assertTrue("found 8 msgs in database to export" in captured_logger.output[1])
                self.assertTrue("exported 8 in" in captured_logger.output[2])

        # check email was sent correctly
        email_args = mock_send_temba_email.call_args[0]  # all positional args
        export = ExportMessagesTask.objects.order_by("-id").first()
        self.assertEqual(email_args[0], "Your messages export from %s is ready" % self.org.name)
        self.assertIn("https://app.rapidpro.io/assets/download/message_export/%d/" % export.id, email_args[1])
        self.assertNotIn("{{", email_args[1])
        self.assertIn("https://app.rapidpro.io/assets/download/message_export/%d/" % export.id, email_args[2])
        self.assertNotIn("{{", email_args[2])

        # export just archived messages
        self.assertExcelSheet(
            request_export("?l=A", {"export_all": 0}),
            [
                [
                    "Date",
                    "Contact UUID",
                    "Name",
                    "URN",
                    "URN Type",
                    "Direction",
                    "Text",
                    "Attachments",
                    "Status",
                    "Channel",
                    "Labels",
                ],
                [
                    msg3.created_on,
                    msg3.contact.uuid,
                    "Joe Blow",
                    "123",
                    "tel",
                    "IN",
                    "hello 3",
                    "",
                    "handled",
                    "Test Channel",
                    "",
                ],
            ],
            self.org.timezone,
        )

        # filter page should have an export option
        response = self.client.get(reverse("msgs.msg_filter", args=[label.id]))
        self.assertContains(response, "Export")

        # try export with user label
        self.assertExcelSheet(
            request_export("?l=%s" % label.uuid, {"export_all": 0}),
            [
                [
                    "Date",
                    "Contact UUID",
                    "Name",
                    "URN",
                    "URN Type",
                    "Direction",
                    "Text",
                    "Attachments",
                    "Status",
                    "Channel",
                    "Labels",
                ],
                [
                    msg1.created_on,
                    msg1.contact.uuid,
                    "Joe Blow",
                    "123",
                    "tel",
                    "IN",
                    "hello 1",
                    "",
                    "handled",
                    "Test Channel",
                    "label1",
                ],
            ],
            self.org.timezone,
        )

        # try export with user label folder
        self.assertExcelSheet(
            request_export("?l=%s" % folder.uuid, {"export_all": 0}),
            [
                [
                    "Date",
                    "Contact UUID",
                    "Name",
                    "URN",
                    "URN Type",
                    "Direction",
                    "Text",
                    "Attachments",
                    "Status",
                    "Channel",
                    "Labels",
                ],
                [
                    msg1.created_on,
                    msg1.contact.uuid,
                    "Joe Blow",
                    "123",
                    "tel",
                    "IN",
                    "hello 1",
                    "",
                    "handled",
                    "Test Channel",
                    "label1",
                ],
            ],
            self.org.timezone,
        )

        # try export with groups and date range
        export_data = {
            "export_all": 1,
            "groups": [self.just_joe.id],
            "start_date": msg5.created_on.strftime("%B %d, %Y"),
            "end_date": msg7.created_on.strftime("%B %d, %Y"),
        }

        self.assertExcelSheet(
            request_export("?l=I", export_data),
            [
                [
                    "Date",
                    "Contact UUID",
                    "Name",
                    "URN",
                    "URN Type",
                    "Direction",
                    "Text",
                    "Attachments",
                    "Status",
                    "Channel",
                    "Labels",
                ],
                [
                    msg5.created_on,
                    msg5.contact.uuid,
                    "Joe Blow",
                    "123",
                    "tel",
                    "IN",
                    "Media message",
                    "http://rapidpro.io/audio/sound.mp3",
                    "handled",
                    "Test Channel",
                    "",
                ],
                [
                    msg6.created_on,
                    msg6.contact.uuid,
                    "Joe Blow",
                    "123",
                    "tel",
                    "OUT",
                    "Hey out 6",
                    "",
                    "sent",
                    "Test Channel",
                    "",
                ],
                [
                    msg7.created_on,
                    msg7.contact.uuid,
                    "Joe Blow",
                    "123",
                    "tel",
                    "OUT",
                    "Hey out 7",
                    "",
                    "delivered",
                    "Test Channel",
                    "",
                ],
            ],
            self.org.timezone,
        )

        # check sending an invalid date
        response = self.client.post(reverse("msgs.msg_export") + "?l=I", {"export_all": 1, "start_date": "xyz"})
        self.assertEqual(response.status_code, 200)
        self.assertFormError(response, "form", "start_date", "Enter a valid date.")

        # test as anon org to check that URNs don't end up in exports
        with AnonymousOrg(self.org):
            joe_anon_id = f"{self.joe.id:010d}"

            self.assertExcelSheet(
                request_export("?l=I", {"export_all": 1}),
                [
                    [
                        "Date",
                        "Contact UUID",
                        "Name",
                        "ID",
                        "URN Type",
                        "Direction",
                        "Text",
                        "Attachments",
                        "Status",
                        "Channel",
                        "Labels",
                    ],
                    [
                        msg1.created_on,
                        msg1.contact.uuid,
                        "Joe Blow",
                        joe_anon_id,
                        "",
                        "IN",
                        "hello 1",
                        "",
                        "handled",
                        "Test Channel",
                        "label1",
                    ],
                    [
                        msg2.created_on,
                        msg2.contact.uuid,
                        "Joe Blow",
                        joe_anon_id,
                        "",
                        "IN",
                        "hello 2",
                        "",
                        "handled",
                        "Test Channel",
                        "",
                    ],
                    [
                        msg4.created_on,
                        msg4.contact.uuid,
                        "Joe Blow",
                        joe_anon_id,
                        "",
                        "IN",
                        "hello 4",
                        "",
                        "handled",
                        "",
                        "",
                    ],
                    [
                        msg5.created_on,
                        msg5.contact.uuid,
                        "Joe Blow",
                        joe_anon_id,
                        "",
                        "IN",
                        "Media message",
                        "http://rapidpro.io/audio/sound.mp3",
                        "handled",
                        "Test Channel",
                        "",
                    ],
                    [
                        msg6.created_on,
                        msg6.contact.uuid,
                        "Joe Blow",
                        joe_anon_id,
                        "",
                        "OUT",
                        "Hey out 6",
                        "",
                        "sent",
                        "Test Channel",
                        "",
                    ],
                    [
                        msg7.created_on,
                        msg7.contact.uuid,
                        "Joe Blow",
                        joe_anon_id,
                        "",
                        "OUT",
                        "Hey out 7",
                        "",
                        "delivered",
                        "Test Channel",
                        "",
                    ],
                    [
                        msg8.created_on,
                        msg8.contact.uuid,
                        "Joe Blow",
                        joe_anon_id,
                        "",
                        "OUT",
                        "Hey out 8",
                        "",
                        "errored",
                        "Test Channel",
                        "",
                    ],
                    [
                        msg9.created_on,
                        msg9.contact.uuid,
                        "Joe Blow",
                        joe_anon_id,
                        "",
                        "OUT",
                        "Hey out 9",
                        "",
                        "failed",
                        "Test Channel",
                        "",
                    ],
                ],
                self.org.timezone,
            )


class MsgCRUDLTest(TembaTest):
    def setUp(self):
        super().setUp()

        self.joe = self.create_contact("Joe Blow", "+250788000001")
        self.frank = self.create_contact("Frank Blow", "250788000002")
        self.billy = self.create_contact("Billy Bob", twitter="billy_bob")

    def test_filter(self):
        # create some folders and labels
        folder = Label.get_or_create_folder(self.org, self.user, "folder")
        label1 = Label.get_or_create(self.org, self.user, "label1", folder)
        label2 = Label.get_or_create(self.org, self.user, "label2", folder)
        label3 = Label.get_or_create(self.org, self.user, "label3")

        # create some messages
        msg1 = self.create_msg(direction="I", msg_type="I", contact=self.joe, text="test1")
        msg2 = self.create_msg(direction="I", msg_type="I", contact=self.frank, text="test2")
        msg3 = self.create_msg(direction="I", msg_type="I", contact=self.billy, text="test3")
        msg4 = self.create_msg(
            direction="I", msg_type="I", contact=self.joe, text="test4", visibility=Msg.VISIBILITY_ARCHIVED
        )
        msg5 = self.create_msg(
            direction="I", msg_type="I", contact=self.joe, text="test5", visibility=Msg.VISIBILITY_DELETED
        )
        msg6 = self.create_msg(direction="I", msg_type="F", contact=self.joe, text="flow test")

        # apply the labels
        label1.toggle_label([msg1, msg2], add=True)
        label2.toggle_label([msg2, msg3], add=True)
        label3.toggle_label([msg1, msg2, msg3, msg4, msg5, msg6], add=True)

        # can't visit a filter page as a non-org user
        self.login(self.non_org_user)
        response = self.client.get(reverse("msgs.msg_filter", args=[label3.pk]))
        self.assertRedirect(response, reverse("orgs.org_choose"))

        # can as org viewer user
        self.login(self.user)
        response = self.client.get(reverse("msgs.msg_filter", args=[label3.pk]))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["actions"], ["unlabel", "label"])
        self.assertNotContains(response, reverse("msgs.label_update", args=[label3.pk]))  # can't update label
        self.assertNotContains(response, reverse("msgs.label_delete", args=[label3.pk]))  # can't delete label

        # check that test and non-visible messages are excluded, and messages and ordered newest to oldest
        self.assertEqual(list(response.context["object_list"]), [msg6, msg3, msg2, msg1])

        # check viewing a folder
        response = self.client.get(reverse("msgs.msg_filter", args=[folder.pk]))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["actions"], ["unlabel", "label"])
        self.assertNotContains(response, reverse("msgs.label_update", args=[folder.pk]))  # can't update folder
        self.assertNotContains(response, reverse("msgs.label_delete", args=[folder.pk]))  # can't delete folder

        # messages from contained labels are rolled up without duplicates
        self.assertEqual(list(response.context["object_list"]), [msg3, msg2, msg1])

        # search on folder by message text
        response = self.client.get("%s?search=test2" % reverse("msgs.msg_filter", args=[folder.pk]))
        self.assertEqual(set(response.context_data["object_list"]), {msg2})

        # search on label by contact name
        response = self.client.get("%s?search=joe" % reverse("msgs.msg_filter", args=[label3.pk]))
        self.assertEqual(set(response.context_data["object_list"]), {msg1, msg6})

        # check admin users see edit and delete options for labels and folders
        self.login(self.admin)
        response = self.client.get(reverse("msgs.msg_filter", args=[folder.pk]))
        self.assertContains(response, reverse("msgs.label_update", args=[folder.pk]))
        self.assertContains(response, reverse("msgs.label_delete", args=[folder.pk]))

        response = self.client.get(reverse("msgs.msg_filter", args=[label1.pk]))
        self.assertContains(response, reverse("msgs.label_update", args=[label1.pk]))
        self.assertContains(response, reverse("msgs.label_delete", args=[label1.pk]))


class BroadcastTest(TembaTest):
    def setUp(self):
        super().setUp()

        self.joe = self.create_contact("Joe Blow", "123")
        self.frank = self.create_contact("Frank Blow", "321")

        self.just_joe = self.create_group("Just Joe", [self.joe])

        self.joe_and_frank = self.create_group("Joe and Frank", [self.joe, self.frank])

        self.kevin = self.create_contact(name="Kevin Durant", number="987")
        self.lucy = self.create_contact(name="Lucy M", twitter="lucy")

        # a Twitter channel
        self.twitter = Channel.create(self.org, self.user, None, "TT")

    def run_msg_release_test(self, tc):
        favorites = self.get_flow("favorites")
        label = Label.get_or_create(self.org, self.user, "Labeled")

        # create some incoming messages
        msg_in1 = Msg.create_incoming(self.channel, self.joe.get_urn().urn, "Hello")
        Msg.create_incoming(self.channel, self.frank.get_urn().urn, "Bonjour")

        # create a broadcast which is a response to an incoming message
        broadcast1 = Broadcast.create(self.org, self.user, "Noted", contacts=[self.joe])
        broadcast1.send(response_to=msg_in1)

        # create a broadcast which is to several contacts
        broadcast2 = Broadcast.create(
            self.org, self.user, "Very old broadcast", groups=[self.joe_and_frank], contacts=[self.kevin, self.lucy]
        )
        broadcast2.send()

        # start joe in a flow
        favorites.start([], [self.joe])
        msg_in3 = Msg.create_incoming(self.channel, self.joe.get_urn().urn, "red!")

        # mark all outgoing messages as sent except broadcast #2 to Joe
        Msg.objects.filter(direction="O").update(status="S")
        broadcast2.msgs.filter(contact=self.joe).update(status="F")

        # label one of our messages
        msg_in1.labels.add(label)
        self.assertEqual(LabelCount.get_totals([label])[label], 1)

        self.assertEqual(SystemLabel.get_counts(self.org)[SystemLabel.TYPE_INBOX], 2)
        self.assertEqual(SystemLabel.get_counts(self.org)[SystemLabel.TYPE_FLOWS], 1)
        self.assertEqual(SystemLabel.get_counts(self.org)[SystemLabel.TYPE_SENT], 6)
        self.assertEqual(SystemLabel.get_counts(self.org)[SystemLabel.TYPE_FAILED], 1)

        today = timezone.now().date()
        self.assertEqual(ChannelCount.get_day_count(self.channel, ChannelCount.INCOMING_MSG_TYPE, today), 3)
        self.assertEqual(ChannelCount.get_day_count(self.channel, ChannelCount.OUTGOING_MSG_TYPE, today), 6)
        self.assertEqual(ChannelCount.get_day_count(self.twitter, ChannelCount.INCOMING_MSG_TYPE, today), 0)
        self.assertEqual(ChannelCount.get_day_count(self.twitter, ChannelCount.OUTGOING_MSG_TYPE, today), 1)

        self.org.clear_credit_cache()
        self.assertEqual(self.org.get_credits_used(), 10)
        self.assertEqual(self.org.get_credits_remaining(), 990)

        # archive all our messages save for our flow incoming message
        for m in Msg.objects.exclude(id=msg_in3.id):
            m.release(tc["delete_reason"])

        # broadcasts should be unaffected
        self.assertEqual(Broadcast.objects.count(), tc["broadcast_count"])

        # credit usage remains the same
        self.org.clear_credit_cache()
        self.assertEqual(self.org.get_credits_used(), tc["credits_used"])
        self.assertEqual(self.org.get_credits_remaining(), tc["credits_remaining"])

        # check system label counts have been updated
        self.assertEqual(SystemLabel.get_counts(self.org)[SystemLabel.TYPE_INBOX], tc["inbox_count"])
        self.assertEqual(SystemLabel.get_counts(self.org)[SystemLabel.TYPE_FLOWS], tc["flow_count"])
        self.assertEqual(SystemLabel.get_counts(self.org)[SystemLabel.TYPE_SENT], tc["sent_count"])
        self.assertEqual(SystemLabel.get_counts(self.org)[SystemLabel.TYPE_FAILED], tc["failed_count"])

        # check our archived counts as well
        self.assertEqual(
            SystemLabelCount.get_totals(self.org, True)[SystemLabel.TYPE_INBOX], tc["archived_inbox_count"]
        )
        self.assertEqual(
            SystemLabelCount.get_totals(self.org, True)[SystemLabel.TYPE_FLOWS], tc["archived_flow_count"]
        )
        self.assertEqual(SystemLabelCount.get_totals(self.org, True)[SystemLabel.TYPE_SENT], tc["archived_sent_count"])
        self.assertEqual(
            SystemLabelCount.get_totals(self.org, True)[SystemLabel.TYPE_FAILED], tc["archived_failed_count"]
        )

        # check user labels
        self.assertEqual(LabelCount.get_totals([label])[label], tc["label_count"])
        self.assertEqual(LabelCount.get_totals([label], True)[label], tc["archived_label_count"])

        # but daily channel counts should be unchanged
        self.assertEqual(
            ChannelCount.get_day_count(self.channel, ChannelCount.INCOMING_MSG_TYPE, today), tc["sms_incoming_count"]
        )
        self.assertEqual(
            ChannelCount.get_day_count(self.channel, ChannelCount.OUTGOING_MSG_TYPE, today), tc["sms_outgoing_count"]
        )
        self.assertEqual(
            ChannelCount.get_day_count(self.twitter, ChannelCount.INCOMING_MSG_TYPE, today),
            tc["twitter_incoming_count"],
        )
        self.assertEqual(
            ChannelCount.get_day_count(self.twitter, ChannelCount.OUTGOING_MSG_TYPE, today),
            tc["twitter_outgoing_count"],
        )

    def test_archive_release(self):
        self.run_msg_release_test(
            {
                "delete_reason": Msg.DELETE_FOR_ARCHIVE,
                "broadcast_count": 2,
                "label_count": 0,
                "archived_label_count": 1,
                "inbox_count": 0,
                "flow_count": 1,
                "sent_count": 0,
                "failed_count": 0,
                "archived_inbox_count": 2,
                "archived_flow_count": 0,
                "archived_sent_count": 6,
                "archived_failed_count": 1,
                "credits_used": 10,
                "credits_remaining": 990,
                "sms_incoming_count": 3,
                "sms_outgoing_count": 6,
                "twitter_incoming_count": 0,
                "twitter_outgoing_count": 1,
            }
        )

    def test_user_release(self):
        self.run_msg_release_test(
            {
                "delete_reason": Msg.DELETE_FOR_USER,
                "broadcast_count": 2,
                "label_count": 0,
                "archived_label_count": 0,
                "inbox_count": 0,
                "flow_count": 1,
                "sent_count": 0,
                "failed_count": 0,
                "archived_inbox_count": 0,
                "archived_flow_count": 0,
                "archived_sent_count": 0,
                "archived_failed_count": 0,
                "credits_used": 10,
                "credits_remaining": 990,
                "sms_incoming_count": 3,
                "sms_outgoing_count": 6,
                "twitter_incoming_count": 0,
                "twitter_outgoing_count": 1,
            }
        )

    def test_delete_release(self):
        self.run_msg_release_test(
            {
                "delete_reason": None,
                "broadcast_count": 2,
                "label_count": 0,
                "archived_label_count": 0,
                "inbox_count": 0,
                "flow_count": 1,
                "sent_count": 0,
                "failed_count": 0,
                "archived_inbox_count": 0,
                "archived_flow_count": 0,
                "archived_sent_count": 0,
                "archived_failed_count": 0,
                "credits_used": 1,
                "credits_remaining": 999,
                "sms_incoming_count": 1,
                "sms_outgoing_count": 0,
                "twitter_incoming_count": 0,
                "twitter_outgoing_count": 0,
            }
        )

    def test_broadcast_model(self):
        broadcast = Broadcast.create(
            self.org,
            self.user,
            "Like a tweet",
            groups=[self.joe_and_frank],
            contacts=[self.kevin, self.lucy],
            schedule=Schedule.create_schedule(timezone.now(), "M", self.admin),
        )
        self.assertEqual("I", broadcast.status)

        broadcast.send()
        self.assertEqual("S", broadcast.status)
        self.assertEqual(4, broadcast.recipient_count)
        self.assertEqual(broadcast.get_message_count(), 4)

        broadcast.release()

        self.assertEqual(Msg.objects.count(), 0)
        self.assertEqual(Broadcast.objects.count(), 0)
        self.assertEqual(Schedule.objects.count(), 0)

        with self.assertRaises(ValueError):
            Broadcast.create(self.org, self.user, "no recipients")

    def test_send(self):
        # remove all channels first
        for channel in Channel.objects.all():
            channel.release()

        send_url = reverse("msgs.broadcast_send")
        self.login(self.admin)

        # try with no channel
        post_data = dict(text="some text", omnibox="c-%s" % self.joe.uuid)
        response = self.client.post(send_url, post_data, follow=True)
        self.assertContains(response, "You must add a phone number before sending messages", status_code=400)

        # test when we have many channels
        Channel.create(
            self.org, self.user, None, "A", secret=Channel.generate_secret(), config={Channel.CONFIG_FCM_ID: "1234"}
        )
        Channel.create(
            self.org, self.user, None, "A", secret=Channel.generate_secret(), config={Channel.CONFIG_FCM_ID: "123"}
        )
        Channel.create(self.org, self.user, None, "TT")

        response = self.client.get(send_url)
        self.assertEqual(["omnibox", "text", "schedule", "step_node"], response.context["fields"])

        post_data = dict(
            text="message #1", omnibox="g-%s,c-%s,c-%s" % (self.joe_and_frank.uuid, self.joe.uuid, self.lucy.uuid)
        )
        self.client.post(send_url, post_data, follow=True)
        broadcast = Broadcast.objects.get()
        self.assertEqual(broadcast.text, {"base": "message #1"})
        self.assertEqual(broadcast.get_default_text(), "message #1")
        self.assertEqual(broadcast.groups.count(), 1)
        self.assertEqual(broadcast.contacts.count(), 2)
        self.assertIsNotNone(Msg.objects.filter(contact=self.joe, text="message #1"))
        self.assertIsNotNone(Msg.objects.filter(contact=self.frank, text="message #1"))
        self.assertIsNotNone(Msg.objects.filter(contact=self.lucy, text="message #1"))

        # test with one channel now
        for channel in Channel.objects.all():
            channel.release()

        Channel.create(
            self.org,
            self.user,
            None,
            "A",
            None,
            secret=Channel.generate_secret(),
            config={Channel.CONFIG_FCM_ID: "123"},
        )

        response = self.client.get(send_url)
        self.assertEqual(["omnibox", "text", "schedule", "step_node"], response.context["fields"])

        post_data = dict(text="message #2", omnibox="g-%s,c-%s" % (self.joe_and_frank.uuid, self.kevin.uuid))
        self.client.post(send_url, post_data, follow=True)
        broadcast = Broadcast.objects.order_by("-id").first()
        self.assertEqual(broadcast.text, {"base": "message #2"})
        self.assertEqual(broadcast.groups.count(), 1)
        self.assertEqual(broadcast.contacts.count(), 1)

        # directly on user page
        post_data = dict(text="contact send", from_contact=True, omnibox="c-%s" % self.kevin.uuid)
        response = self.client.post(send_url, post_data)
        self.assertRedirect(response, reverse("contacts.contact_read", args=[self.kevin.uuid]))
        self.assertEqual(Broadcast.objects.all().count(), 3)

        # test sending to an arbitrary user
        post_data = dict(text="message content", omnibox="n-2065551212")
        self.client.post(send_url, post_data, follow=True)
        self.assertEqual(Broadcast.objects.all().count(), 4)
        self.assertEqual(1, Contact.objects.filter(urns__path="2065551212").count())

        # test missing senders
        post_data = dict(text="message content")
        response = self.client.post(send_url, post_data, follow=True)
        self.assertContains(response, "At least one recipient is required")

        # Test AJAX sender
        post_data = dict(text="message content", omnibox="")
        response = self.client.post(send_url + "?_format=json", post_data, follow=True)
        self.assertContains(response, "At least one recipient is required", status_code=400)
        self.assertEqual("application/json", response._headers.get("content-type")[1])

        post_data = dict(text="this is a test message", omnibox="c-%s" % self.kevin.uuid, _format="json")
        response = self.client.post(send_url, post_data, follow=True)
        self.assertContains(response, "success")

        # send using our omnibox
        post_data = dict(
            text="this is a test message",
            omnibox="c-%s,g-%s,n-911" % (self.kevin.pk, self.joe_and_frank.pk),
            _format="json",
        )
        response = self.client.post(send_url, post_data, follow=True)
        self.assertContains(response, "success")

        # add flow steps
        flow = self.get_flow("favorites")
        flow.start([], [self.joe], restart_participants=True)

        step_uuid = RuleSet.objects.first().uuid

        # no error if we are sending from a flow node
        post_data = dict(text="message content", omnibox="", step_node=step_uuid)
        response = self.client.post(send_url + "?_format=json", post_data, follow=True)
        self.assertContains(response, "success")

        response = self.client.post(send_url, post_data)
        self.assertRedirect(response, reverse("msgs.msg_inbox"))

        response = self.client.post(send_url + "?_format=json", post_data, follow=True)
        self.assertContains(response, "success")
        broadcast = Broadcast.objects.order_by("-id").first()
        self.assertEqual(broadcast.text, {"base": "message content"})
        self.assertEqual(broadcast.groups.count(), 0)
        self.assertEqual(broadcast.contacts.count(), 1)
        self.assertTrue(self.joe in broadcast.contacts.all())

    def test_message_parts(self):
        contact = self.create_contact("Matt", "+12067778811")

        sms = self.create_msg(contact=contact, text="Text", direction=OUTGOING)

        self.assertEqual(["Text"], Msg.get_text_parts(sms.text))
        sms.text = ""
        self.assertEqual([""], Msg.get_text_parts(sms.text))

        # 160 chars
        sms.text = "1234567890123456789012345678901234567890123456789012345678901234567890123456789012345678901234567890123456789012345678901234567890123456789012345678901234567890"
        self.assertEqual(1, len(Msg.get_text_parts(sms.text)))

        # 161 characters with space
        sms.text = "123456789012345678901234567890123456789012345678901234567890123456789012345678901234567890123456789012345678901234567890123456789012345678901234567890 1234567890"
        parts = Msg.get_text_parts(sms.text)
        self.assertEqual(2, len(parts))
        self.assertEqual(150, len(parts[0]))
        self.assertEqual(10, len(parts[1]))

        # 161 characters without space
        sms.text = "12345678901234567890123456789012345678901234567890123456789012345678901234567890123456789012345678901234567890123456789012345678901234567890123456789012345678901"
        parts = Msg.get_text_parts(sms.text)
        self.assertEqual(2, len(parts))
        self.assertEqual(160, len(parts[0]))
        self.assertEqual(1, len(parts[1]))

        # 160 characters with max length 40
        sms.text = "1234567890123456789012345678901234567890123456789012345678901234567890123456789012345678901234567890123456789012345678901234567890123456789012345678901234567890"
        parts = Msg.get_text_parts(sms.text, max_length=40)
        self.assertEqual(4, len(parts))
        self.assertEqual(40, len(parts[0]))
        self.assertEqual(40, len(parts[1]))
        self.assertEqual(40, len(parts[2]))
        self.assertEqual(40, len(parts[3]))

    def test_substitute_variables(self):
        ContactField.get_or_create(self.org, self.admin, "goats", "Goats", False, Value.TYPE_NUMBER)
        self.joe.set_field(self.user, "goats", "3 ")
        ContactField.get_or_create(self.org, self.admin, "temp", "Temperature", False, Value.TYPE_NUMBER)
        self.joe.set_field(self.user, "temp", "37.45")
        ContactField.get_or_create(self.org, self.admin, "dob", "Date of birth", False, Value.TYPE_DATETIME)
        self.joe.set_field(self.user, "dob", "28/5/1981")

        def substitute(s, context):
            context["contact"] = self.joe.build_expressions_context()
            return Msg.evaluate_template(s, context)

        self.assertEqual(("Hello World", []), substitute("Hello World", dict()))
        self.assertEqual(("Hello World Joe", []), substitute("Hello World @contact.first_name", dict()))
        self.assertEqual(("Hello World Joe Blow", []), substitute("Hello World @contact", dict()))
        self.assertEqual(
            ("Hello World: Well", []),
            substitute("Hello World: @flow.water_source", dict(flow=dict(water_source="Well"))),
        )
        self.assertEqual(
            ("Hello World: Well  Boil: @flow.boil", ["Undefined variable: flow.boil"]),
            substitute("Hello World: @flow.water_source  Boil: @flow.boil", dict(flow=dict(water_source="Well"))),
        )

        self.assertEqual(("Hello joe", []), substitute("Hello @(LOWER(contact.first_name))", dict()))
        self.assertEqual(("Hello Joe", []), substitute("Hello @(PROPER(LOWER(contact.first_name)))", dict()))
        self.assertEqual(("Hello Joe", []), substitute("Hello @(first_word(contact))", dict()))
        self.assertEqual(("Hello Blow", []), substitute("Hello @(Proper(remove_first_word(contact)))", dict()))
        self.assertEqual(("Hello Joe Blow", []), substitute("Hello @(PROPER(contact))", dict()))
        self.assertEqual(("Hello JOE", []), substitute("Hello @(UPPER(contact.first_name))", dict()))
        self.assertEqual(("Hello 3", []), substitute("Hello @(contact.goats)", dict()))
        self.assertEqual(("Hello 37.45", []), substitute("Hello @(contact.temp)", dict()))
        self.assertEqual(("Hello 37", []), substitute("Hello @(INT(contact.temp))", dict()))
        self.assertEqual(("Hello 37.45", []), substitute("Hello @(FIXED(contact.temp))", dict()))

        self.assertEqual(
            ("Email is: foo@bar.com", []),
            substitute("Email is: @(remove_first_word(flow.sms))", dict(flow=dict(sms="Join foo@bar.com"))),
        )
        self.assertEqual(
            ("Email is: foo@@bar.com", []),
            substitute("Email is: @(remove_first_word(flow.sms))", dict(flow=dict(sms="Join foo@@bar.com"))),
        )

        # check date variables
        text, errors = substitute("Today is @date.today", dict())
        self.assertEqual(errors, [])
        self.assertRegex(text, r"Today is \d{2}-\d{2}-\d{4}")

        text, errors = substitute("Today is @date.now", dict())
        self.assertEqual(errors, [])
        self.assertRegex(text, r"Today is \d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{6}\+\d{2}:\d{2}")

        text, errors = substitute("Today is @(format_date(date.now))", dict())
        self.assertEqual(errors, [])
        self.assertRegex(text, r"Today is \d\d-\d\d-\d\d\d\d \d\d:\d\d")

        text, errors = substitute("Your DOB is @contact.dob", dict())
        self.assertEqual(errors, [])
        # TODO clearly this is not ideal but unavoidable for now as we always add current time to parsed dates
        self.assertRegex(text, r"Your DOB is 1981-05-28T\d{2}:\d{2}:\d{2}\.\d{6}\+\d{2}:\d{2}")

        # unicode tests
        self.joe.name = "شاملیدل عمومی"
        self.joe.save(update_fields=("name",), handle_update=False)

        self.assertEqual(("شاملیدل", []), substitute("@(first_word(contact))", dict()))
        self.assertEqual(("عمومی", []), substitute("@(proper(remove_first_word(contact)))", dict()))

        # credit card
        self.joe.name = "1234567890123456"
        self.joe.save(update_fields=("name",), handle_update=False)
        self.assertEqual(("1 2 3 4 , 5 6 7 8 , 9 0 1 2 , 3 4 5 6", []), substitute("@(read_digits(contact))", dict()))

        # phone number
        self.joe.name = "123456789012"
        self.joe.save(update_fields=("name",), handle_update=False)
        self.assertEqual(("1 2 3 , 4 5 6 , 7 8 9 , 0 1 2", []), substitute("@(read_digits(contact))", dict()))

        # triplets
        self.joe.name = "123456"
        self.joe.save(update_fields=("name",), handle_update=False)
        self.assertEqual(("1 2 3 , 4 5 6", []), substitute("@(read_digits(contact))", dict()))

        # soc security
        self.joe.name = "123456789"
        self.joe.save(update_fields=("name",), handle_update=False)
        self.assertEqual(("1 2 3 , 4 5 , 6 7 8 9", []), substitute("@(read_digits(contact))", dict()))

        # regular number, street address, etc
        self.joe.name = "12345"
        self.joe.save(update_fields=("name",), handle_update=False)
        self.assertEqual(("1,2,3,4,5", []), substitute("@(read_digits(contact))", dict()))

        # regular number, street address, etc
        self.joe.name = "123"
        self.joe.save(update_fields=("name",), handle_update=False)
        self.assertEqual(("1,2,3", []), substitute("@(read_digits(contact))", dict()))

    def test_expressions_context(self):
        ContactField.get_or_create(self.org, self.admin, "superhero_name", "Superhero Name")

        self.joe.send("keyword remainder-remainder", self.admin)
        self.joe.set_field(self.user, "superhero_name", "batman")

        msg = Msg.objects.get()
        context = msg.build_expressions_context()

        self.assertEqual(context["__default__"], "keyword remainder-remainder")
        self.assertEqual(context["value"], "keyword remainder-remainder")
        self.assertEqual(context["text"], "keyword remainder-remainder")
        self.assertEqual(context["attachments"], {})

        # time should be in org format and timezone
        self.assertEqual(context["time"], datetime_to_str(msg.created_on, "%d-%m-%Y %H:%M", tz=self.org.timezone))

        # add some attachments to this message
        msg.attachments = ["image/jpeg:http://e.com/test.jpg", "audio/mp3:http://e.com/test.mp3"]
        msg.save()
        context = msg.build_expressions_context()

        self.assertEqual(
            context["__default__"], "keyword remainder-remainder\nhttp://e.com/test.jpg\nhttp://e.com/test.mp3"
        )
        self.assertEqual(context["value"], "keyword remainder-remainder\nhttp://e.com/test.jpg\nhttp://e.com/test.mp3")
        self.assertEqual(context["text"], "keyword remainder-remainder")
        self.assertEqual(context["attachments"], {"0": "http://e.com/test.jpg", "1": "http://e.com/test.mp3"})

        # clear the text of the message
        msg.text = ""
        msg.save()
        context = msg.build_expressions_context()

        self.assertEqual(context["__default__"], "http://e.com/test.jpg\nhttp://e.com/test.mp3")
        self.assertEqual(context["value"], "http://e.com/test.jpg\nhttp://e.com/test.mp3")
        self.assertEqual(context["text"], "")
        self.assertEqual(context["attachments"], {"0": "http://e.com/test.jpg", "1": "http://e.com/test.mp3"})
        self.assertEqual(context["urn"]["scheme"], "tel")
        self.assertEqual(context["urn"]["path"], "123")

    def test_variables_substitution(self):
        ContactField.get_or_create(self.org, self.admin, "sector", "sector")
        ContactField.get_or_create(self.org, self.admin, "team", "team")

        self.joe.set_field(self.user, "sector", "Kacyiru")
        self.frank.set_field(self.user, "sector", "Remera")
        self.kevin.set_field(self.user, "sector", "Kanombe")

        self.joe.set_field(self.user, "team", "Amavubi")
        self.kevin.set_field(self.user, "team", "Junior")

        broadcast1 = Broadcast.create(
            self.org,
            self.user,
            "Hi @contact.name, You live in @contact.sector and your team is @contact.team.",
            groups=[self.joe_and_frank],
            contacts=[self.kevin],
        )
        broadcast1.send(expressions_context={})

        # no message created for Frank because he misses some fields for variables substitution
        self.assertEqual(Msg.objects.all().count(), 3)

        self.assertEqual(
            self.joe.msgs.get(broadcast=broadcast1).text, "Hi Joe Blow, You live in Kacyiru and your team is Amavubi."
        )
        self.assertEqual(
            self.frank.msgs.get(broadcast=broadcast1).text, "Hi Frank Blow, You live in Remera and your team is ."
        )
        self.assertEqual(
            self.kevin.msgs.get(broadcast=broadcast1).text,
            "Hi Kevin Durant, You live in Kanombe and your team is Junior.",
        )

        # if we don't provide a context then substitution isn't performed
        broadcast2 = Broadcast.create(
            self.org, self.user, "Hi @contact.name on @channel", groups=[self.joe_and_frank], contacts=[self.kevin]
        )
        broadcast2.send()

        self.assertEqual(self.joe.msgs.get(broadcast=broadcast2).text, "Hi @contact.name on @channel")
        self.assertEqual(self.frank.msgs.get(broadcast=broadcast2).text, "Hi @contact.name on @channel")


class BroadcastCRUDLTest(TembaTest):
    def setUp(self):
        super().setUp()

        self.joe, urn_obj = Contact.get_or_create(self.org, "tel:123", user=self.user, name="Joe Blow")
        self.frank, urn_obj = Contact.get_or_create(self.org, "tel:1234", user=self.user, name="Frank Blow")

    def test_send(self):
        url = reverse("msgs.broadcast_send")

        # can't send if you're not logged in
        response = self.client.post(url, dict(text="Test", omnibox="c-%s" % self.joe.uuid))
        self.assertLoginRedirect(response)

        # or just a viewer user
        self.login(self.user)
        response = self.client.post(url, dict(text="Test", omnibox="c-%s" % self.joe.uuid))
        self.assertLoginRedirect(response)

        # but editors can
        self.login(self.editor)

        just_joe = self.create_group("Just Joe")
        just_joe.contacts.add(self.joe)
        post_data = dict(
            omnibox="g-%s,c-%s,n-0780000001" % (just_joe.uuid, self.frank.uuid),
            text="Hey Joe, where you goin' with that gun in your hand?",
        )
        response = self.client.post(url + "?_format=json", post_data)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["status"], "success")

        # raw number means a new contact created
        new_urn = ContactURN.objects.get(path="+250780000001")
        Contact.objects.get(urns=new_urn)

        broadcast = Broadcast.objects.get()
        self.assertEqual(broadcast.text, {"base": "Hey Joe, where you goin' with that gun in your hand?"})
        self.assertEqual(set(broadcast.groups.all()), {just_joe})
        self.assertEqual(set(broadcast.contacts.all()), {self.frank})
        self.assertEqual(set(broadcast.urns.all()), {new_urn})

    def test_update(self):
        self.login(self.editor)
        self.client.post(
            reverse("msgs.broadcast_send"), dict(omnibox="c-%s" % self.joe.uuid, text="Lunch reminder", schedule=True)
        )
        broadcast = Broadcast.objects.get()
        url = reverse("msgs.broadcast_update", args=[broadcast.pk])

        response = self.client.get(url)
        self.assertEqual(list(response.context["form"].fields.keys()), ["message", "omnibox", "loc"])

        response = self.client.post(url, dict(message="Dinner reminder", omnibox="c-%s" % self.frank.uuid))
        self.assertEqual(response.status_code, 302)

        broadcast = Broadcast.objects.get()
        self.assertEqual(broadcast.text, {"base": "Dinner reminder"})
        self.assertEqual(broadcast.base_language, "base")
        self.assertEqual(set(broadcast.contacts.all()), {self.frank})

    def test_schedule_list(self):
        url = reverse("msgs.broadcast_schedule_list")

        # can't view if you're not logged in
        response = self.client.get(url)
        self.assertLoginRedirect(response)

        self.login(self.editor)

        # send some messages - one immediately, one scheduled
        self.client.post(reverse("msgs.broadcast_send"), dict(omnibox="c-%s" % self.joe.uuid, text="See you later"))
        self.client.post(
            reverse("msgs.broadcast_send"), dict(omnibox="c-%s" % self.joe.uuid, text="Lunch reminder", schedule=True)
        )

        scheduled = Broadcast.objects.exclude(schedule=None).first()

        response = self.client.get(url)
        self.assertEqual(set(response.context["object_list"]), {scheduled})

    def test_schedule_read(self):
        self.login(self.editor)
        self.client.post(
            reverse("msgs.broadcast_send"), dict(omnibox="c-%s" % self.joe.uuid, text="Lunch reminder", schedule=True)
        )
        broadcast = Broadcast.objects.get()

        # view with empty Send History
        response = self.client.get(reverse("msgs.broadcast_schedule_read", args=[broadcast.pk]))
        self.assertEqual(response.context["object"], broadcast)

        self.assertEqual(response.context["object_list"].count(), 0)

        broadcast.fire()

        # view again with 1 item in Send History
        response = self.client.get(reverse("msgs.broadcast_schedule_read", args=[broadcast.pk]))
        self.assertEqual(response.context["object"], broadcast)
        self.assertEqual(response.context["object_list"].count(), 1)


class LabelTest(TembaTest):
    def setUp(self):
        super().setUp()

        self.joe = self.create_contact("Joe Blow", number="073835001")
        self.frank = self.create_contact("Frank", number="073835002")

    def test_get_or_create(self):
        label1 = Label.get_or_create(self.org, self.user, "Spam")
        self.assertEqual(label1.name, "Spam")
        self.assertIsNone(label1.folder)

        followup = Label.get_or_create_folder(self.org, self.user, "Follow up")
        label2 = Label.get_or_create(self.org, self.user, "Complaints", followup)
        self.assertEqual(label2.name, "Complaints")
        self.assertEqual(label2.folder, followup)

        # don't allow invalid name
        self.assertRaises(ValueError, Label.get_or_create, self.org, self.user, "+Important")

    def test_is_valid_name(self):
        self.assertTrue(Label.is_valid_name("x"))
        self.assertTrue(Label.is_valid_name("1"))
        self.assertTrue(Label.is_valid_name("x" * 64))
        self.assertFalse(Label.is_valid_name(" "))
        self.assertFalse(Label.is_valid_name(" x"))
        self.assertFalse(Label.is_valid_name("x "))
        self.assertFalse(Label.is_valid_name("+x"))
        self.assertFalse(Label.is_valid_name("@x"))
        self.assertFalse(Label.is_valid_name("x" * 65))

    def test_toggle_label(self):
        label = Label.get_or_create(self.org, self.user, "Spam")
        msg1 = self.create_msg(text="Message 1", contact=self.joe, direction="I")
        msg2 = self.create_msg(text="Message 2", contact=self.joe, direction="I")
        msg3 = self.create_msg(text="Message 3", contact=self.joe, direction="I")

        self.assertEqual(label.get_visible_count(), 0)

        label.toggle_label([msg1, msg2, msg3], add=True)  # add label to 3 messages

        label = Label.label_objects.get(pk=label.pk)
        self.assertEqual(label.get_visible_count(), 3)
        self.assertEqual(set(label.get_messages()), {msg1, msg2, msg3})

        label.toggle_label([msg3], add=False)  # remove label from a message

        label = Label.label_objects.get(pk=label.pk)
        self.assertEqual(label.get_visible_count(), 2)
        self.assertEqual(set(label.get_messages()), {msg1, msg2})

        # check still correct after squashing
        squash_msgcounts()
        self.assertEqual(label.get_visible_count(), 2)

        msg2.archive()  # won't remove label from msg, but msg no longer counts toward visible count

        label = Label.label_objects.get(pk=label.pk)
        self.assertEqual(label.get_visible_count(), 1)
        self.assertEqual(set(label.get_messages()), {msg1, msg2})

        msg2.restore()  # msg back in visible count

        label = Label.label_objects.get(pk=label.pk)
        self.assertEqual(label.get_visible_count(), 2)
        self.assertEqual(set(label.get_messages()), {msg1, msg2})

        msg2.release()  # removes label message no longer visible

        label = Label.label_objects.get(pk=label.pk)
        self.assertEqual(label.get_visible_count(), 1)
        self.assertEqual(set(label.get_messages()), {msg1})

        msg3.archive()
        label.toggle_label([msg3], add=True)  # labelling an already archived message doesn't increment the count

        label = Label.label_objects.get(pk=label.pk)
        self.assertEqual(label.get_visible_count(), 1)
        self.assertEqual(set(label.get_messages()), {msg1, msg3})

        msg3.restore()  # but then restoring that message will

        label = Label.label_objects.get(pk=label.pk)
        self.assertEqual(label.get_visible_count(), 2)
        self.assertEqual(set(label.get_messages()), {msg1, msg3})

        # can't label outgoing messages
        msg5 = self.create_msg(text="Message", contact=self.joe, direction="O")
        self.assertRaises(ValueError, label.toggle_label, [msg5], add=True)

        # can't get a count of a folder
        folder = Label.get_or_create_folder(self.org, self.user, "Folder")
        self.assertRaises(ValueError, folder.get_visible_count)

        # archive one of our messages, should change count but keep an archived count as well
        self.assertEqual(LabelCount.get_totals([label], is_archived=True)[label], 0)
        self.assertEqual(LabelCount.get_totals([label], is_archived=False)[label], 2)

        msg1.release(Msg.DELETE_FOR_ARCHIVE)

        self.assertEqual(LabelCount.get_totals([label], is_archived=True)[label], 1)
        self.assertEqual(LabelCount.get_totals([label], is_archived=False)[label], 1)

        # squash and check once more
        squash_msgcounts()

        self.assertEqual(LabelCount.get_totals([label], is_archived=True)[label], 1)
        self.assertEqual(LabelCount.get_totals([label], is_archived=False)[label], 1)

        # do a user release
        msg3.release(Msg.DELETE_FOR_USER)

        self.assertEqual(LabelCount.get_totals([label], is_archived=True)[label], 1)
        self.assertEqual(LabelCount.get_totals([label], is_archived=False)[label], 0)
        squash_msgcounts()
        self.assertEqual(LabelCount.get_totals([label], is_archived=True)[label], 1)
        self.assertEqual(LabelCount.get_totals([label], is_archived=False)[label], 0)

    def test_get_messages_and_hierarchy(self):
        folder1 = Label.get_or_create_folder(self.org, self.user, "Sorted")
        folder2 = Label.get_or_create_folder(self.org, self.user, "Todo")
        label1 = Label.get_or_create(self.org, self.user, "Spam", folder1)
        label2 = Label.get_or_create(self.org, self.user, "Social", folder1)
        label3 = Label.get_or_create(self.org, self.user, "Other")

        msg1 = self.create_msg(text="Message 1", contact=self.joe, direction="I")
        msg2 = self.create_msg(text="Message 2", contact=self.joe, direction="I")
        msg3 = self.create_msg(text="Message 3", contact=self.joe, direction="I")

        label1.toggle_label([msg1, msg2], add=True)
        label2.toggle_label([msg2, msg3], add=True)
        label3.toggle_label([msg3], add=True)

        self.assertEqual(set(folder1.get_messages()), {msg1, msg2, msg3})
        self.assertEqual(set(folder2.get_messages()), set())
        self.assertEqual(set(label1.get_messages()), {msg1, msg2})
        self.assertEqual(set(label2.get_messages()), {msg2, msg3})
        self.assertEqual(set(label3.get_messages()), {msg3})

        with self.assertNumQueries(2):
            hierarchy = Label.get_hierarchy(self.org)
            self.assertEqual(
                hierarchy,
                [
                    {"obj": label3, "count": 1, "children": []},
                    {
                        "obj": folder1,
                        "count": None,
                        "children": [
                            {"obj": label2, "count": 2, "children": []},
                            {"obj": label1, "count": 2, "children": []},
                        ],
                    },
                    {"obj": folder2, "count": None, "children": []},
                ],
            )

    def test_delete_folder(self):
        folder1 = Label.get_or_create_folder(self.org, self.user, "Folder")
        label1 = Label.get_or_create(self.org, self.user, "Spam", folder1)
        label2 = Label.get_or_create(self.org, self.user, "Social", folder1)
        label3 = Label.get_or_create(self.org, self.user, "Other")

        msg1 = self.create_msg(text="Message 1", contact=self.joe, direction="I")
        msg2 = self.create_msg(text="Message 2", contact=self.joe, direction="I")
        msg3 = self.create_msg(text="Message 3", contact=self.joe, direction="I")

        label1.toggle_label([msg1, msg2], add=True)
        label2.toggle_label([msg1], add=True)
        label3.toggle_label([msg3], add=True)

        folder1.release()

        self.assertFalse(Label.all_objects.filter(pk=folder1.pk).exists())

        # check that contained labels are also deleted
        self.assertEqual(Label.all_objects.filter(pk__in=[label1.pk, label2.pk]).count(), 0)
        self.assertEqual(set(Msg.objects.get(pk=msg1.pk).labels.all()), set())
        self.assertEqual(set(Msg.objects.get(pk=msg2.pk).labels.all()), set())
        self.assertEqual(set(Msg.objects.get(pk=msg3.pk).labels.all()), {label3})

        label3.release()

        self.assertFalse(Label.all_objects.filter(pk=label3.pk).exists())
        self.assertEqual(set(Msg.objects.get(pk=msg3.pk).labels.all()), set())


class LabelCRUDLTest(TembaTest):
    @patch.object(Label, "MAX_ORG_LABELS", new=10)
    def test_create_and_update(self):
        create_label_url = reverse("msgs.label_create")
        create_folder_url = reverse("msgs.label_create_folder")

        self.login(self.admin)

        # try to create label with invalid name
        response = self.client.post(create_label_url, dict(name="+label_one"))
        self.assertFormError(response, "form", "name", "Name must not be blank or begin with punctuation")

        # try again with valid name
        self.client.post(create_label_url, dict(name="label_one"), follow=True)

        label_one = Label.label_objects.get()
        self.assertEqual(label_one.name, "label_one")
        self.assertIsNone(label_one.folder)

        # check that we can't create another with same name
        response = self.client.post(create_label_url, dict(name="label_one"))
        self.assertFormError(response, "form", "name", "Name must be unique")

        # create a folder
        self.client.post(create_folder_url, dict(name="Folder"), follow=True)
        folder = Label.folder_objects.get(name="Folder")

        # and a label in it
        self.client.post(create_label_url, dict(name="label_two", folder=folder.pk), follow=True)
        label_two = Label.label_objects.get(name="label_two")
        self.assertEqual(label_two.folder, folder)

        # update label one
        self.client.post(reverse("msgs.label_update", args=[label_one.pk]), dict(name="label_1"))

        label_one = Label.label_objects.get(pk=label_one.pk)
        self.assertEqual(label_one.name, "label_1")
        self.assertIsNone(label_one.folder)

        # try to update to invalid label name
        response = self.client.post(reverse("msgs.label_update", args=[label_one.pk]), dict(name="+label_1"))
        self.assertFormError(response, "form", "name", "Name must not be blank or begin with punctuation")

        self.release(Label.folder_objects.all())
        self.release(Label.label_objects.all())

        for i in range(Label.MAX_ORG_LABELS):
            Label.get_or_create(self.org, self.user, "label%d" % i)

        response = self.client.post(create_label_url, dict(name="Label"))
        self.assertFormError(
            response,
            "form",
            "name",
            "This org has 10 labels and the limit is 10. "
            "You must delete existing ones before you can create new ones.",
        )

    def test_label_delete(self):
        label_one = Label.get_or_create(self.org, self.user, "label1")

        delete_url = reverse("msgs.label_delete", args=[label_one.pk])

        self.login(self.user)
        response = self.client.get(delete_url)
        self.assertEqual(response.status_code, 302)

        self.login(self.admin)
        response = self.client.get(delete_url)
        self.assertEqual(response.status_code, 200)

    def test_label_delete_with_flow_dependency(self):

        label_one = Label.get_or_create(self.org, self.user, "label1")

        from temba.flows.models import Flow

        self.get_flow("dependencies")
        flow = Flow.objects.filter(name="Dependencies").first()

        flow.label_dependencies.add(label_one)

        # release method raises ValueError
        with self.assertRaises(ValueError) as release_error:
            label_one.release()

        self.assertEqual(str(release_error.exception), f"Cannot delete Label: {label_one.name}, used by 1 flows")

    def test_list(self):
        folder = Label.get_or_create_folder(self.org, self.user, "Folder")
        Label.get_or_create(self.org, self.user, "Spam", folder=folder)
        Label.get_or_create(self.org, self.user, "Junk", folder=folder)
        Label.get_or_create(self.org, self.user, "Important")

        self.create_secondary_org()
        Label.get_or_create(self.org2, self.admin2, "Other Org")

        # viewers can't edit flows so don't have access to this JSON endpoint as that's only place it's used
        self.login(self.user)
        response = self.client.get(reverse("msgs.label_list"))
        self.assertLoginRedirect(response)

        # editors can though
        self.login(self.editor)
        response = self.client.get(reverse("msgs.label_list"))
        results = response.json()

        # results should be A-Z and not include folders or labels from other orgs
        self.assertEqual(len(results), 3)
        self.assertEqual(results[0]["text"], "Important")
        self.assertEqual(results[1]["text"], "Junk")
        self.assertEqual(results[2]["text"], "Spam")


class ScheduleTest(TembaTest):
    def tearDown(self):
        from temba.channels import models as channel_models

        channel_models.SEND_QUEUE_DEPTH = 500
        channel_models.SEND_BATCH_SIZE = 100

    def test_batch(self):
        # broadcast out to 11 contacts to test our batching
        contacts = []
        for i in range(1, 12):
            contacts.append(self.create_contact("Contact %d" % i, "+250788123%d" % i))
        batch_group = self.create_group("Batch Group", contacts)

        # create our broadcast
        broadcast = Broadcast.create(self.org, self.admin, "Many message but only 5 batches.", groups=[batch_group])

        self.channel.channel_type = "EX"
        self.channel.save()

        # create our messages
        broadcast.send()

        # get one of our messages, should be at low priority since it was to more than one recipient
        sms = broadcast.get_messages()[0]
        self.assertFalse(sms.high_priority)

        # we should now have 11 messages wired
        self.assertEqual(11, Msg.objects.filter(channel=self.channel, status=WIRED).count())


class ConsoleTest(TembaTest):
    def setUp(self):
        from temba.triggers.models import Trigger

        super().setUp()
        self.create_secondary_org()

        # create a new console
        self.console = MessageConsole(self.org, "tel:+250788123123")

        # a few test contacts
        self.john = self.create_contact("John Doe", "0788123123", force_urn_update=True)

        # create a flow and set "color" as its trigger
        self.flow = self.get_flow("color")
        Trigger.objects.create(
            flow=self.flow, keyword="color", created_by=self.admin, modified_by=self.admin, org=self.org
        )

    def assertEchoed(self, needle, clear=True):
        found = False
        for line in self.console.echoed:
            if line.find(needle) >= 0:
                found = True

        self.assertTrue(found, "Did not find '%s' in '%s'" % (needle, ", ".join(self.console.echoed)))

        if clear:
            self.console.clear_echoed()

    def test_msg_console(self):
        # make sure our org is properly set
        self.assertEqual(self.console.org, self.org)

        # try changing it with something empty
        self.console.do_org("")
        self.assertEchoed("Select org", clear=False)
        self.assertEchoed("Temba")

        # shouldn't have changed current org
        self.assertEqual(self.console.org, self.org)

        # try changing entirely
        self.console.do_org("%d" % self.org2.id)
        self.assertEchoed("You are now sending messages for Trileet Inc.")
        self.assertEqual(self.console.org, self.org2)
        self.assertEqual(self.console.contact.org, self.org2)

        # back to temba
        self.console.do_org("%d" % self.org.id)
        self.assertEqual(self.console.org, self.org)
        self.assertEqual(self.console.contact.org, self.org)

        # contact help
        self.console.do_contact("")
        self.assertEchoed("Set contact by")

        # switch our contact
        self.console.do_contact("0788123123")
        self.assertEchoed("You are now sending as John")
        self.assertEqual(self.console.contact, self.john)

        # send a message
        self.console.default("Hello World")
        self.assertEchoed("Hello World")

        # make sure the message was created for our contact and handled
        msg = Msg.objects.get()
        self.assertEqual(msg.text, "Hello World")
        self.assertEqual(msg.contact, self.john)
        self.assertEqual(msg.status, HANDLED)

        # now trigger a flow
        self.console.default("Color")
        self.assertEchoed("What is your favorite color?")


class BroadcastLanguageTest(TembaTest):
    def setUp(self):
        super().setUp()

        self.francois = self.create_contact("Francois", "+12065551213", language="fra")
        self.greg = self.create_contact("Greg", "+12065551212")
        self.wilbert = self.create_contact("Wilbert", "+12065551214", language="fra")

    def test_multiple_language_broadcast(self):
        # set up our org to have a few different languages
        eng = Language.create(self.org, self.admin, "English", "eng")
        Language.create(self.org, self.admin, "French", "fra")
        self.org.primary_language = eng
        self.org.save()

        eng_msg = "This is my message"
        fra_msg = "Ceci est mon message"

        # now create a broadcast with a couple contacts, one with an explicit language, the other not
        bcast = Broadcast.create(
            self.org,
            self.admin,
            dict(eng=eng_msg, fra=fra_msg),
            contacts=[self.francois, self.greg, self.wilbert],
            base_language="eng",
        )

        bcast.send()

        # assert the right language was used for each contact
        self.assertEqual(fra_msg, Msg.objects.get(contact=self.francois).text)
        self.assertEqual(eng_msg, Msg.objects.get(contact=self.greg).text)
        self.assertEqual(fra_msg, Msg.objects.get(contact=self.wilbert).text)

        eng_msg = "Please see attachment"
        fra_msg = "SVP regardez l'attachement."

        eng_attachment = f"image/jpeg:{settings.STORAGE_URL}/attachments/eng_picture.jpg"
        fra_attachment = f"image/jpeg:{settings.STORAGE_URL}/attachments/fre_picture.jpg"

        # now create a broadcast with a couple contacts, one with an explicit language, the other not
        bcast = Broadcast.create(
            self.org,
            self.admin,
            dict(eng=eng_msg, fra=fra_msg),
            contacts=[self.francois, self.greg, self.wilbert],
            base_language="eng",
            media=dict(eng=eng_attachment, fra=fra_attachment),
        )

        bcast.send()

        francois_media = Msg.objects.filter(contact=self.francois).order_by("-created_on").first()
        greg_media = Msg.objects.filter(contact=self.greg).order_by("-created_on").first()
        wilbert_media = Msg.objects.filter(contact=self.wilbert).order_by("-created_on").first()

        # assert the right language was used for each contact on both text and media
        self.assertEqual(francois_media.text, fra_msg)
        self.assertEqual(francois_media.attachments, [fra_attachment])

        self.assertEqual(greg_media.text, eng_msg)
        self.assertEqual(greg_media.attachments, [eng_attachment])

        self.assertEqual(wilbert_media.text, fra_msg)
        self.assertEqual(wilbert_media.attachments, [fra_attachment])


class SystemLabelTest(TembaTest):
    def test_get_archive_attributes(self):
        self.assertEqual(("visible", "in", None, None), SystemLabel.get_archive_attributes(""))
        self.assertEqual(("visible", "in", "inbox", None), SystemLabel.get_archive_attributes(SystemLabel.TYPE_INBOX))
        self.assertEqual(("visible", "in", "flow", None), SystemLabel.get_archive_attributes(SystemLabel.TYPE_FLOWS))
        self.assertEqual(("archived", "in", None, None), SystemLabel.get_archive_attributes(SystemLabel.TYPE_ARCHIVED))
        self.assertEqual(
            ("visible", "out", None, ["pending", "queued"]),
            SystemLabel.get_archive_attributes(SystemLabel.TYPE_OUTBOX),
        )
        self.assertEqual(
            ("visible", "out", None, ["wired", "sent", "delivered"]),
            SystemLabel.get_archive_attributes(SystemLabel.TYPE_SENT),
        )
        self.assertEqual(
            ("visible", "out", None, ["failed"]), SystemLabel.get_archive_attributes(SystemLabel.TYPE_FAILED)
        )

        self.assertEqual(("visible", "in", None, None), SystemLabel.get_archive_attributes(SystemLabel.TYPE_SCHEDULED))
        self.assertEqual(("visible", "in", None, None), SystemLabel.get_archive_attributes(SystemLabel.TYPE_CALLS))

    def test_get_counts(self):
        self.assertEqual(
            SystemLabel.get_counts(self.org),
            {
                SystemLabel.TYPE_INBOX: 0,
                SystemLabel.TYPE_FLOWS: 0,
                SystemLabel.TYPE_ARCHIVED: 0,
                SystemLabel.TYPE_OUTBOX: 0,
                SystemLabel.TYPE_SENT: 0,
                SystemLabel.TYPE_FAILED: 0,
                SystemLabel.TYPE_SCHEDULED: 0,
                SystemLabel.TYPE_CALLS: 0,
            },
        )

        contact1 = self.create_contact("Bob", number="0783835001")
        contact2 = self.create_contact("Jim", number="0783835002")
        msg1 = Msg.create_incoming(self.channel, "tel:0783835001", text="Message 1")
        Msg.create_incoming(self.channel, "tel:0783835001", text="Message 2")
        msg3 = Msg.create_incoming(self.channel, "tel:0783835001", text="Message 3")
        msg4 = Msg.create_incoming(self.channel, "tel:0783835001", text="Message 4")
        call1 = ChannelEvent.create(self.channel, "tel:0783835001", ChannelEvent.TYPE_CALL_IN, timezone.now(), {})
        bcast1 = Broadcast.create(self.org, self.user, "Broadcast 1", contacts=[contact1, contact2], status=QUEUED)
        Broadcast.create(
            self.org,
            self.user,
            "Broadcast 2",
            contacts=[contact1, contact2],
            schedule=Schedule.create_schedule(timezone.now(), "D", self.user),
        )

        self.assertEqual(
            SystemLabel.get_counts(self.org),
            {
                SystemLabel.TYPE_INBOX: 4,
                SystemLabel.TYPE_FLOWS: 0,
                SystemLabel.TYPE_ARCHIVED: 0,
                SystemLabel.TYPE_OUTBOX: 1,
                SystemLabel.TYPE_SENT: 0,
                SystemLabel.TYPE_FAILED: 0,
                SystemLabel.TYPE_SCHEDULED: 1,
                SystemLabel.TYPE_CALLS: 1,
            },
        )

        msg3.archive()
        bcast1.send()
        Msg.objects.filter(broadcast=bcast1).update(status=PENDING)
        msg5, msg6 = tuple(Msg.objects.filter(broadcast=bcast1))
        ChannelEvent.create(self.channel, "tel:0783835002", ChannelEvent.TYPE_CALL_IN, timezone.now(), {})
        Broadcast.create(
            self.org,
            self.user,
            "Broadcast 3",
            contacts=[contact1],
            schedule=Schedule.create_schedule(timezone.now(), "W", self.user),
        )

        self.assertEqual(
            SystemLabel.get_counts(self.org),
            {
                SystemLabel.TYPE_INBOX: 3,
                SystemLabel.TYPE_FLOWS: 0,
                SystemLabel.TYPE_ARCHIVED: 1,
                SystemLabel.TYPE_OUTBOX: 2,
                SystemLabel.TYPE_SENT: 0,
                SystemLabel.TYPE_FAILED: 0,
                SystemLabel.TYPE_SCHEDULED: 2,
                SystemLabel.TYPE_CALLS: 2,
            },
        )

        msg1.archive()
        msg3.release()  # deleting an archived msg
        msg4.release()  # deleting a visible msg
        msg5.status_fail()
        msg6.status_sent()
        call1.release()

        self.assertEqual(
            SystemLabel.get_counts(self.org),
            {
                SystemLabel.TYPE_INBOX: 1,
                SystemLabel.TYPE_FLOWS: 0,
                SystemLabel.TYPE_ARCHIVED: 1,
                SystemLabel.TYPE_OUTBOX: 0,
                SystemLabel.TYPE_SENT: 1,
                SystemLabel.TYPE_FAILED: 1,
                SystemLabel.TYPE_SCHEDULED: 2,
                SystemLabel.TYPE_CALLS: 1,
            },
        )

        msg1.restore()
        msg5.status_fail()  # already failed
        msg6.status_delivered()

        self.assertEqual(
            SystemLabel.get_counts(self.org),
            {
                SystemLabel.TYPE_INBOX: 2,
                SystemLabel.TYPE_FLOWS: 0,
                SystemLabel.TYPE_ARCHIVED: 0,
                SystemLabel.TYPE_OUTBOX: 0,
                SystemLabel.TYPE_SENT: 1,
                SystemLabel.TYPE_FAILED: 1,
                SystemLabel.TYPE_SCHEDULED: 2,
                SystemLabel.TYPE_CALLS: 1,
            },
        )

        msg5.resend()

        self.assertEqual(SystemLabelCount.objects.all().count(), 37)

        # squash our counts
        squash_msgcounts()

        self.assertEqual(
            SystemLabel.get_counts(self.org),
            {
                SystemLabel.TYPE_INBOX: 2,
                SystemLabel.TYPE_FLOWS: 0,
                SystemLabel.TYPE_ARCHIVED: 0,
                SystemLabel.TYPE_OUTBOX: 0,
                SystemLabel.TYPE_SENT: 2,
                SystemLabel.TYPE_FAILED: 0,
                SystemLabel.TYPE_SCHEDULED: 2,
                SystemLabel.TYPE_CALLS: 1,
            },
        )

        # we should only have one system label per type
        self.assertEqual(SystemLabelCount.objects.all().count(), 7)

        # archive one of our inbox messages
        msg1.release(Msg.DELETE_FOR_ARCHIVE)

        self.assertEqual(
            SystemLabel.get_counts(self.org),
            {
                SystemLabel.TYPE_INBOX: 1,
                SystemLabel.TYPE_FLOWS: 0,
                SystemLabel.TYPE_ARCHIVED: 0,
                SystemLabel.TYPE_OUTBOX: 0,
                SystemLabel.TYPE_SENT: 2,
                SystemLabel.TYPE_FAILED: 0,
                SystemLabel.TYPE_SCHEDULED: 2,
                SystemLabel.TYPE_CALLS: 1,
            },
        )

        squash_msgcounts()

        # check our archived count
        self.assertEqual(
            SystemLabelCount.get_totals(self.org, True),
            {
                SystemLabel.TYPE_INBOX: 1,
                SystemLabel.TYPE_FLOWS: 0,
                SystemLabel.TYPE_ARCHIVED: 0,
                SystemLabel.TYPE_OUTBOX: 0,
                SystemLabel.TYPE_SENT: 0,
                SystemLabel.TYPE_FAILED: 0,
                SystemLabel.TYPE_SCHEDULED: 0,
                SystemLabel.TYPE_CALLS: 0,
            },
        )


class TagsTest(TembaTest):
    def setUp(self):
        super().setUp()

        self.joe = self.create_contact("Joe Blow", "123")

    def render_template(self, string, context=None):
        from django.template import Context, Template

        context = context or {}
        context = Context(context)
        return Template(string).render(context)

    def assertHasClass(self, text, clazz):
        self.assertTrue(text.find(clazz) >= 0)

    def test_as_icon(self):
        msg = Msg.create_outgoing(self.org, self.admin, "tel:250788382382", "How is it going?")
        now = timezone.now()
        two_hours_ago = now - timedelta(hours=2)

        self.assertHasClass(as_icon(msg), "icon-bubble-dots-2 green")
        msg.created_on = two_hours_ago
        self.assertHasClass(as_icon(msg), "icon-bubble-dots-2 green")
        msg.status = "S"
        self.assertHasClass(as_icon(msg), "icon-bubble-right green")
        msg.status = "D"
        self.assertHasClass(as_icon(msg), "icon-bubble-check green")
        msg.status = "E"
        self.assertHasClass(as_icon(msg), "icon-bubble-notification red")
        msg.direction = "I"
        self.assertHasClass(as_icon(msg), "icon-bubble-user primary")
        msg.msg_type = "V"
        self.assertHasClass(as_icon(msg), "icon-phone")

        # default cause is pending sent
        self.assertHasClass(as_icon(None), "icon-bubble-dots-2 green")

        in_call = ChannelEvent.create(
            self.channel, str(self.joe.get_urn(TEL_SCHEME)), ChannelEvent.TYPE_CALL_IN, timezone.now(), {}
        )
        self.assertHasClass(as_icon(in_call), "icon-call-incoming green")

        in_miss = ChannelEvent.create(
            self.channel, str(self.joe.get_urn(TEL_SCHEME)), ChannelEvent.TYPE_CALL_IN_MISSED, timezone.now(), {}
        )
        self.assertHasClass(as_icon(in_miss), "icon-call-incoming red")

        out_call = ChannelEvent.create(
            self.channel, str(self.joe.get_urn(TEL_SCHEME)), ChannelEvent.TYPE_CALL_OUT, timezone.now(), {}
        )
        self.assertHasClass(as_icon(out_call), "icon-call-outgoing green")

        out_miss = ChannelEvent.create(
            self.channel, str(self.joe.get_urn(TEL_SCHEME)), ChannelEvent.TYPE_CALL_OUT_MISSED, timezone.now(), {}
        )
        self.assertHasClass(as_icon(out_miss), "icon-call-outgoing red")

    def test_render(self):
        template_src = "{% load sms %}{% render as foo %}123<a>{{ bar }}{% endrender %}-{{ foo }}-"
        self.assertEqual(self.render_template(template_src, {"bar": "abc"}), "-123<a>abc-")

        # exception if tag not used correctly
        self.assertRaises(ValueError, self.render_template, "{% load sms %}{% render with bob %}{% endrender %}")
        self.assertRaises(ValueError, self.render_template, "{% load sms %}{% render as %}{% endrender %}")
