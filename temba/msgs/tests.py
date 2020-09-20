from datetime import datetime, timedelta
from unittest.mock import PropertyMock, patch

import pytz
from openpyxl import load_workbook

from django.conf import settings
from django.urls import reverse
from django.utils import timezone

from temba.archives.models import Archive
from temba.channels.models import Channel, ChannelCount, ChannelEvent, ChannelLog
from temba.contacts.models import TEL_SCHEME, Contact, ContactField, ContactURN
from temba.contacts.omnibox import omnibox_serialize
from temba.flows.legacy.expressions import get_function_listing
from temba.flows.models import Flow
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
)
from temba.orgs.models import Language
from temba.schedules.models import Schedule
from temba.tests import AnonymousOrg, TembaTest
from temba.tests.engine import MockSessionWriter
from temba.tests.s3 import MockS3Client
from temba.utils import json
from temba.utils.uuid import uuid4

from .tasks import retry_errored_messages, squash_msgcounts
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
        msg1 = self.create_incoming_msg(self.joe, "i'm having a problem")
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
                "sent_on": None,
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
                "sent_on": None,
            },
        )

        msg2 = self.create_incoming_msg(
            self.joe, "Media message", attachments=["audio:http://rapidpro.io/audio/sound.mp3"]
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
        msg1 = self.create_incoming_msg(self.joe, "i'm having a problem")
        msg2 = self.create_incoming_msg(self.frank, "ignore joe, he's a liar")

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
        msg1 = self.create_outgoing_msg(self.joe, "Hello, we heard from you.")
        msg2 = self.create_outgoing_msg(self.frank, "Hello, we heard from you.")
        msg3 = self.create_outgoing_msg(self.kevin, "Hello, we heard from you.")

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

        msg4 = self.create_outgoing_msg(self.kevin, "Hello, there")

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

        msg5 = self.create_outgoing_msg(self.frank, "Hello, we heard from you.")

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
        msg1 = self.create_incoming_msg(self.joe, "Incoming")
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
        msg2 = self.create_outgoing_msg(self.joe, "Outgoing")
        self.assertRaises(ValueError, msg2.archive)

    def assertReleaseCount(self, direction, status, visibility, msg_type, label):
        if direction == OUTGOING:
            msg = self.create_outgoing_msg(self.joe, "Whattup Joe")
        else:
            msg = self.create_incoming_msg(self.joe, "Hey hey")

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
        self.assertEqual(
            broadcast.metadata,
            {"quick_replies": [{"eng": "Yes", "fra": "Oui"}, {"eng": "No"}], "template_state": "legacy"},
        )

    def test_outbox(self):
        self.login(self.admin)

        contact, urn_obj = Contact.get_or_create(self.channel.org, "tel:250788382382", user=self.admin)
        broadcast1 = self.create_broadcast(self.admin, "How is it going?", contacts=[contact])

        # put messages back into pending state
        Msg.objects.filter(broadcast=broadcast1).update(status=PENDING)

        (msg1,) = tuple(Msg.objects.filter(broadcast=broadcast1))

        with self.assertNumQueries(46):
            response = self.client.get(reverse("msgs.msg_outbox"))

        self.assertContains(response, "Outbox (1)")
        self.assertEqual(set(response.context_data["object_list"]), {msg1})

        broadcast2 = self.create_broadcast(
            self.admin, "kLab is an awesome place", contacts=[self.kevin], groups=[self.joe_and_frank]
        )

        Msg.objects.filter(broadcast=broadcast2).update(status=PENDING)
        msg4, msg3, msg2 = tuple(Msg.objects.filter(broadcast=broadcast2).order_by("-created_on", "-id"))

        broadcast3 = Broadcast.create(
            self.channel.org, self.admin, "Pending broadcast", contacts=[self.kevin], status=QUEUED
        )

        broadcast4 = Broadcast.create(
            self.channel.org, self.admin, "Scheduled broadcast", contacts=[self.kevin], status=QUEUED
        )

        broadcast4.schedule = Schedule.create_schedule(self.org, self.admin, timezone.now(), Schedule.REPEAT_DAILY)
        broadcast4.save(update_fields=["schedule"])

        with self.assertNumQueries(43):
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

        msg1 = self.create_incoming_msg(self.joe, "message number 1")
        msg2 = self.create_incoming_msg(self.joe, "message number 2")
        msg3 = self.create_incoming_msg(self.joe, "message number 3")
        self.create_incoming_msg(self.joe, "message number 4")
        msg5 = self.create_incoming_msg(self.joe, "message number 5")
        self.create_incoming_msg(self.joe, "message number 6", status=PENDING)

        # visit inbox page  as a user not in the organization
        self.login(self.non_org_user)
        response = self.client.get(inbox_url)
        self.assertEqual(302, response.status_code)

        # visit inbox page as a manager of the organization
        with self.assertNumQueries(63):
            response = self.fetch_protected(inbox_url + "?refresh=10000", self.admin)

        # make sure that we embed refresh script if View.refresh is set
        self.assertContains(response, "function refresh")

        self.assertEqual(response.context["refresh"], 20000)
        self.assertEqual(response.context["object_list"].count(), 5)
        self.assertEqual(response.context["folders"][0]["url"], "/msg/inbox/")
        self.assertEqual(response.context["folders"][0]["count"], 5)
        self.assertEqual(response.context["actions"], ("archive", "label"))

        # visit inbox page as administrator
        response = self.fetch_protected(inbox_url, self.admin)

        self.assertEqual(response.context["object_list"].count(), 5)
        self.assertEqual(response.context["actions"], ("archive", "label"))

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
        self.assertEqual({msg1}, set(label1.msgs.all()))

        # label more messages
        self.do_msg_action(inbox_url, [msg1, msg2, msg3], "label", label3)
        self.assertEqual({msg1}, set(label1.msgs.all()))
        self.assertEqual({msg1, msg2, msg3}, set(label3.msgs.all()))

        # update our label name
        response = self.client.get(reverse("msgs.label_update", args=[label1.id]))

        self.assertEqual(200, response.status_code)
        self.assertIn("folder", response.context["form"].fields)

        response = self.client.post(reverse("msgs.label_update", args=[label1.id]), {"name": "Foo"})
        label1.refresh_from_db()

        self.assertEqual(302, response.status_code)
        self.assertEqual("Foo", label1.name)

        # test deleting the label
        response = self.client.get(reverse("msgs.label_delete", args=[label1.id]))
        self.assertEqual(200, response.status_code)

        response = self.client.post(reverse("msgs.label_delete", args=[label1.id]))
        label1.refresh_from_db()

        self.assertEqual(302, response.status_code)
        self.assertFalse(label1.is_active)

        # shouldn't have a remove on the update page

        # test archiving a msg
        self.assertEqual({label3}, set(msg1.labels.all()))

        response = self.client.post(inbox_url, {"action": "archive", "objects": msg1.id}, follow=True)

        self.assertEqual(response.status_code, 200)

        # now one msg is archived
        self.assertEqual({msg1}, set(Msg.objects.filter(visibility=Msg.VISIBILITY_ARCHIVED)))

        # archiving doesn't remove labels
        msg1.refresh_from_db()
        self.assertEqual({label3}, set(msg1.labels.all()))

        # visit the the archived messages page
        archive_url = reverse("msgs.msg_archived")

        # visit archived page  as a user not in the organization
        self.login(self.non_org_user)
        response = self.client.get(archive_url)
        self.assertEqual(302, response.status_code)

        # visit archived page as a manager of the organization
        with self.assertNumQueries(55):
            response = self.fetch_protected(archive_url, self.admin)

        self.assertEqual(response.context["object_list"].count(), 1)
        self.assertEqual(response.context["actions"], ("restore", "label", "delete"))

        # check that the inbox does not contains archived messages

        # visit inbox page as a user not in the organization
        self.login(self.non_org_user)
        response = self.client.get(inbox_url)
        self.assertEqual(302, response.status_code)

        # visit inbox page as an admin of the organization
        response = self.fetch_protected(inbox_url, self.admin)

        self.assertEqual(response.context["object_list"].count(), 4)
        self.assertEqual(response.context["actions"], ("archive", "label"))

        # test restoring an archived message back to inbox
        post_data = dict(action="restore", objects=[msg1.pk])
        self.client.post(archive_url, post_data, follow=True)
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

        msg1 = self.create_incoming_msg(self.joe, "test 1", msg_type="F")
        msg2 = self.create_incoming_msg(self.joe, "test 2", msg_type="F")
        msg3 = self.create_incoming_msg(self.joe, "test 3", msg_type="F")

        # user not in org can't access
        self.login(self.non_org_user)
        self.assertRedirect(self.client.get(url), reverse("orgs.org_choose"))

        # org viewer can
        self.login(self.admin)

        with self.assertNumQueries(43):
            response = self.client.get(url)

        self.assertEqual(set(response.context["object_list"]), {msg3, msg2, msg1})
        self.assertEqual(response.context["actions"], ("label",))

    def test_retry_errored(self):
        # change our default channel to external
        self.channel.channel_type = "EX"
        self.channel.save()

        android_channel = Channel.create(
            self.org,
            self.user,
            "RW",
            "A",
            name="Android Channel",
            address="+250785551414",
            device="Nexus 5X",
            secret="12345678",
            config={Channel.CONFIG_FCM_ID: "123"},
        )

        msg1 = self.create_outgoing_msg(self.joe, "errored", status="E", channel=self.channel)
        msg1.next_attempt = timezone.now()
        msg1.save(update_fields=["next_attempt"])

        msg2 = self.create_outgoing_msg(self.joe, "android", status="E", channel=android_channel)
        msg2.next_attempt = timezone.now()
        msg2.save(update_fields=["next_attempt"])

        msg3 = self.create_outgoing_msg(self.joe, "failed", status="F", channel=self.channel)

        retry_errored_messages()

        msg1.refresh_from_db()
        msg2.refresh_from_db()
        msg3.refresh_from_db()

        self.assertEqual("W", msg1.status)
        self.assertEqual("E", msg2.status)
        self.assertEqual("F", msg3.status)

    def test_failed(self):
        failed_url = reverse("msgs.msg_failed")

        msg1 = self.create_outgoing_msg(self.joe, "message number 1", status="F")

        # create a log for it
        log = ChannelLog.objects.create(channel=msg1.channel, msg=msg1, is_error=True, description="Failed")

        # create broadcast and fail the only message
        broadcast = self.create_broadcast(self.admin, "message number 2", contacts=[self.joe])
        broadcast.get_messages().update(status="F")
        msg2 = broadcast.get_messages()[0]

        # message without a broadcast
        self.create_outgoing_msg(self.joe, "messsage number 3", status="F")

        # visit fail page  as a user not in the organization
        self.login(self.non_org_user)
        response = self.client.get(failed_url)
        self.assertEqual(302, response.status_code)

        # visit failed page as an administrator
        with self.assertNumQueries(66):
            response = self.fetch_protected(failed_url, self.admin)

        self.assertEqual(response.context["object_list"].count(), 3)
        self.assertEqual(response.context["actions"], ("resend",))
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

    @patch("temba.utils.email.send_temba_email")
    def test_message_export_from_archives(self, mock_send_temba_email):
        self.clear_storage()
        self.login(self.admin)

        self.joe.name = "Jo\02e Blow"
        self.joe.save(update_fields=("name",), handle_update=False)

        self.org.created_on = datetime(2017, 1, 1, 9, tzinfo=pytz.UTC)
        self.org.save()

        msg1 = self.create_incoming_msg(self.joe, "hello 1", created_on=datetime(2017, 1, 1, 10, tzinfo=pytz.UTC))
        msg2 = self.create_incoming_msg(
            self.frank, "hello 2", msg_type="F", created_on=datetime(2017, 1, 2, 10, tzinfo=pytz.UTC)
        )
        msg3 = self.create_incoming_msg(self.joe, "hello 3", created_on=datetime(2017, 1, 3, 10, tzinfo=pytz.UTC))

        # inbound message that looks like a surveyor message
        msg4 = self.create_incoming_msg(
            self.joe, "hello 4", surveyor=True, created_on=datetime(2017, 1, 4, 10, tzinfo=pytz.UTC)
        )

        # inbound message with media attached, such as an ivr recording
        msg5 = self.create_incoming_msg(
            self.joe,
            "Media message",
            attachments=["audio:http://rapidpro.io/audio/sound.mp3"],
            created_on=datetime(2017, 1, 5, 10, tzinfo=pytz.UTC),
        )

        # create some outbound messages with different statuses
        msg6 = self.create_outgoing_msg(
            self.joe, "Hey out 6", status=SENT, created_on=datetime(2017, 1, 6, 10, tzinfo=pytz.UTC)
        )
        msg7 = self.create_outgoing_msg(
            self.joe, "Hey out 7", status=DELIVERED, created_on=datetime(2017, 1, 7, 10, tzinfo=pytz.UTC)
        )
        msg8 = self.create_outgoing_msg(
            self.joe, "Hey out 8", status=ERRORED, created_on=datetime(2017, 1, 8, 10, tzinfo=pytz.UTC)
        )
        msg9 = self.create_outgoing_msg(
            self.joe, "Hey out 9", status=FAILED, created_on=datetime(2017, 1, 9, 10, tzinfo=pytz.UTC)
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

        msg1 = self.create_incoming_msg(self.joe, "hello 1", created_on=datetime(2017, 1, 1, 10, tzinfo=pytz.UTC))
        msg2 = self.create_incoming_msg(self.joe, "hello 2", created_on=datetime(2017, 1, 2, 10, tzinfo=pytz.UTC))
        msg3 = self.create_incoming_msg(self.joe, "hello 3", created_on=datetime(2017, 1, 3, 10, tzinfo=pytz.UTC))

        # inbound message that looks like a surveyor message
        msg4 = self.create_incoming_msg(
            self.joe, "hello 4", surveyor=True, created_on=datetime(2017, 1, 4, 10, tzinfo=pytz.UTC)
        )

        # inbound message with media attached, such as an ivr recording
        msg5 = self.create_incoming_msg(
            self.joe,
            "Media message",
            attachments=["audio:http://rapidpro.io/audio/sound.mp3"],
            created_on=datetime(2017, 1, 5, 10, tzinfo=pytz.UTC),
        )

        # create some outbound messages with different statuses
        msg6 = self.create_outgoing_msg(
            self.joe, "Hey out 6", status=SENT, created_on=datetime(2017, 1, 6, 10, tzinfo=pytz.UTC)
        )
        msg7 = self.create_outgoing_msg(
            self.joe, "Hey out 7", status=DELIVERED, created_on=datetime(2017, 1, 7, 10, tzinfo=pytz.UTC)
        )
        msg8 = self.create_outgoing_msg(
            self.joe, "Hey out 8", status=ERRORED, created_on=datetime(2017, 1, 8, 10, tzinfo=pytz.UTC)
        )
        msg9 = self.create_outgoing_msg(
            self.joe, "Hey out 9", status=FAILED, created_on=datetime(2017, 1, 9, 10, tzinfo=pytz.UTC)
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

                with self.assertNumQueries(30):
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
        response = self.client.get(reverse("msgs.msg_filter", args=[label.uuid]))
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

        response = self.client.post(reverse("msgs.msg_export") + "?l=I&redirect=http://foo.me", {"export_all": 1})
        self.assertEqual(302, response.status_code)
        self.assertEqual("/msg/inbox/", response.url)


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
        msg1 = self.create_incoming_msg(self.joe, "test1")
        msg2 = self.create_incoming_msg(self.frank, "test2")
        msg3 = self.create_incoming_msg(self.billy, "test3")
        msg4 = self.create_incoming_msg(self.joe, "test4", visibility=Msg.VISIBILITY_ARCHIVED)
        msg5 = self.create_incoming_msg(self.joe, "test5", visibility=Msg.VISIBILITY_DELETED)
        msg6 = self.create_incoming_msg(self.joe, "flow test", msg_type="F")

        # apply the labels
        label1.toggle_label([msg1, msg2], add=True)
        label2.toggle_label([msg2, msg3], add=True)
        label3.toggle_label([msg1, msg2, msg3, msg4, msg5, msg6], add=True)

        # can't visit a filter page as a non-org user
        self.login(self.non_org_user)
        response = self.client.get(reverse("msgs.msg_filter", args=[label3.uuid]))
        self.assertRedirect(response, reverse("orgs.org_choose"))

        # can as org viewer user
        self.login(self.user)
        response = self.client.get(reverse("msgs.msg_filter", args=[label3.uuid]))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["actions"], ("unlabel", "label"))
        self.assertNotContains(response, reverse("msgs.label_update", args=[label3.pk]))  # can't update label
        self.assertNotContains(response, reverse("msgs.label_delete", args=[label3.pk]))  # can't delete label

        # check that test and non-visible messages are excluded, and messages and ordered newest to oldest
        self.assertEqual(list(response.context["object_list"]), [msg6, msg3, msg2, msg1])

        # check viewing a folder
        response = self.client.get(reverse("msgs.msg_filter", args=[folder.uuid]))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["actions"], ("unlabel", "label"))
        self.assertNotContains(response, reverse("msgs.label_update", args=[folder.pk]))  # can't update folder
        self.assertNotContains(response, reverse("msgs.label_delete", args=[folder.pk]))  # can't delete folder

        # messages from contained labels are rolled up without duplicates
        self.assertEqual(list(response.context["object_list"]), [msg3, msg2, msg1])

        # search on folder by message text
        response = self.client.get("%s?search=test2" % reverse("msgs.msg_filter", args=[folder.uuid]))
        self.assertEqual(set(response.context_data["object_list"]), {msg2})

        # search on label by contact name
        response = self.client.get("%s?search=joe" % reverse("msgs.msg_filter", args=[label3.uuid]))
        self.assertEqual(set(response.context_data["object_list"]), {msg1, msg6})

        # check admin users see edit and delete options for labels and folders
        self.login(self.admin)
        response = self.client.get(reverse("msgs.msg_filter", args=[folder.uuid]))
        self.assertContains(response, reverse("msgs.label_update", args=[folder.pk]))
        self.assertContains(response, reverse("msgs.label_delete", args=[folder.pk]))

        response = self.client.get(reverse("msgs.msg_filter", args=[label1.uuid]))
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
        label = Label.get_or_create(self.org, self.user, "Labeled")

        # create some incoming messages
        msg_in1 = self.create_incoming_msg(self.joe, "Hello")
        self.create_incoming_msg(self.frank, "Bonjour")

        # create a broadcast which is a response to an incoming message
        self.create_broadcast(self.user, "Noted", contacts=[self.joe], response_to=msg_in1)

        # create a broadcast which is to several contacts
        broadcast2 = self.create_broadcast(
            self.user, "Very old broadcast", groups=[self.joe_and_frank], contacts=[self.kevin, self.lucy]
        )

        # give joe some flow messages
        self.create_outgoing_msg(self.joe, "what's your fav color?", msg_type="F")
        msg_in3 = self.create_incoming_msg(self.joe, "red!", msg_type="F")
        self.create_outgoing_msg(self.joe, "red is cool", msg_type="F")

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

    def test_get_recipient_counts(self):
        contact, urn_obj = Contact.get_or_create(self.channel.org, "tel:250788382382", user=self.admin)

        broadcast1 = self.create_broadcast(
            self.user, "Very old broadcast", groups=[self.joe_and_frank], contacts=[self.kevin, self.lucy]
        )
        self.assertEqual({"recipients": 4, "groups": 0, "contacts": 0, "urns": 0}, broadcast1.get_recipient_counts())

        broadcast2 = Broadcast.create(
            self.org,
            self.user,
            {"eng": "Hello everyone", "spa": "Hola a todos", "fra": "Salut à tous"},
            base_language="eng",
            groups=[self.joe_and_frank],
            contacts=[],
            schedule=Schedule.create_schedule(self.org, self.admin, timezone.now(), Schedule.REPEAT_MONTHLY),
        )
        self.assertEqual({"recipients": 2, "groups": 0, "contacts": 0, "urns": 0}, broadcast2.get_recipient_counts())

        broadcast3 = Broadcast.create(
            self.org,
            self.user,
            {"eng": "Hello everyone", "spa": "Hola a todos", "fra": "Salut à tous"},
            base_language="eng",
            groups=[],
            contacts=[self.kevin, self.lucy, self.joe],
            schedule=Schedule.create_schedule(self.org, self.admin, timezone.now(), Schedule.REPEAT_MONTHLY),
        )
        self.assertEqual({"recipients": 3, "groups": 0, "contacts": 0, "urns": 0}, broadcast3.get_recipient_counts())

        broadcast4 = Broadcast.create(
            self.org,
            self.user,
            {"eng": "Hello everyone", "spa": "Hola a todos", "fra": "Salut à tous"},
            base_language="eng",
            groups=[],
            contacts=[],
            urns=[urn_obj],
            schedule=Schedule.create_schedule(self.org, self.admin, timezone.now(), Schedule.REPEAT_MONTHLY),
        )
        self.assertEqual({"recipients": 1, "groups": 0, "contacts": 0, "urns": 0}, broadcast4.get_recipient_counts())

        broadcast5 = Broadcast.create(
            self.org,
            self.user,
            {"eng": "Hello everyone", "spa": "Hola a todos", "fra": "Salut à tous"},
            base_language="eng",
            groups=[self.joe_and_frank],
            contacts=[self.kevin, self.lucy],
            schedule=Schedule.create_schedule(self.org, self.admin, timezone.now(), Schedule.REPEAT_MONTHLY),
        )
        self.assertEqual({"recipients": 0, "groups": 1, "contacts": 2, "urns": 0}, broadcast5.get_recipient_counts())

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

    def test_model(self):
        broadcast1 = Broadcast.create(
            self.org,
            self.user,
            {"eng": "Hello everyone", "spa": "Hola a todos", "fra": "Salut à tous"},
            base_language="eng",
            groups=[self.joe_and_frank],
            contacts=[self.kevin, self.lucy],
            schedule=Schedule.create_schedule(self.org, self.admin, timezone.now(), Schedule.REPEAT_MONTHLY),
        )
        self.assertEqual("I", broadcast1.status)

        with patch("temba.mailroom.queue_broadcast") as mock_queue_broadcast:
            broadcast1.send()

            mock_queue_broadcast.assert_called_once_with(broadcast1)

        # test resolving the broadcast text in different languages (used to render scheduled ones)
        self.assertEqual("Hello everyone", broadcast1.get_translated_text(self.joe))  # uses broadcast base language

        self.org.set_languages(self.admin, ["eng", "spa", "fra"], "spa")

        self.assertEqual("Hola a todos", broadcast1.get_translated_text(self.joe))  # uses org primary language

        self.joe.language = "fra"
        self.joe.save(update_fields=("language",), handle_update=False)

        self.assertEqual("Salut à tous", broadcast1.get_translated_text(self.joe))  # uses contact language

        self.org.set_languages(self.admin, ["eng", "spa"], "spa")

        self.assertEqual("Hola a todos", broadcast1.get_translated_text(self.joe))  # but only if it's allowed

        # create a broadcast that looks like it has been sent
        broadcast2 = self.create_broadcast(self.admin, "Hi everyone", contacts=[self.kevin, self.lucy])

        self.assertEqual(2, broadcast2.msgs.count())

        broadcast1.release()
        broadcast2.release()

        self.assertEqual(0, Msg.objects.count())
        self.assertEqual(0, Broadcast.objects.count())
        self.assertEqual(0, Schedule.objects.count())

        with self.assertRaises(ValueError):
            Broadcast.create(self.org, self.user, "no recipients")

    @patch("temba.mailroom.queue_broadcast")
    def test_send(self, mock_queue_broadcast):
        # remove all channels first
        for channel in Channel.objects.all():
            channel.release()

        send_url = reverse("msgs.broadcast_send")
        self.login(self.admin)

        # initialize broadcast form based on a message
        msg = self.create_outgoing_msg(self.joe, "A test message to joe")
        response = self.client.get(f"{send_url}?m={msg.id}")
        omnibox = response.context["form"]["omnibox"]
        self.assertEqual("Joe Blow", omnibox.value()[0]["name"])

        # initialize a broadcast form based on a contact
        response = self.client.get(f"{send_url}?c={self.joe.uuid}")
        omnibox = response.context["form"]["omnibox"]
        self.assertEqual("Joe Blow", omnibox.value()[0]["name"])

        # initialize a broadcast form based on an urn
        response = self.client.get(f"{send_url}?u={msg.contact_urn.id}")
        omnibox = response.context["form"]["omnibox"]
        self.assertEqual("tel:123", omnibox.value()[0]["id"])

        # initialize a broadcast form based on a step uuid
        response = self.client.get(f"{send_url}?step_node=step")
        fields = response.context["form"].fields
        field_names = [_ for _ in fields]
        self.assertIn("step_node", field_names)
        self.assertNotIn("omnibox", field_names)

        # try with no channel
        omnibox = omnibox_serialize(self.org, [], [self.joe], True)
        response = self.client.post(send_url, {"text": "some text", "omnibox": omnibox}, follow=True)
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

        omnibox = omnibox_serialize(self.org, [self.joe_and_frank], [self.joe, self.lucy], True)
        self.client.post(send_url, {"text": "message #1", "omnibox": omnibox}, follow=True)

        broadcast = Broadcast.objects.get()
        self.assertEqual({"base": "message #1"}, broadcast.text)
        self.assertEqual("message #1", broadcast.get_default_text())
        self.assertEqual(1, broadcast.groups.count())
        self.assertEqual(2, broadcast.contacts.count())

        mock_queue_broadcast.assert_called_once_with(broadcast)

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

        omnibox = omnibox_serialize(self.org, [self.joe_and_frank], [self.kevin], True)
        self.client.post(send_url, {"text": "message #2", "omnibox": omnibox}, follow=True)

        broadcast = Broadcast.objects.order_by("id").last()
        self.assertEqual({"base": "message #2"}, broadcast.text)
        self.assertEqual(1, broadcast.groups.count())
        self.assertEqual(1, broadcast.contacts.count())

        # directly on user page
        omnibox = omnibox_serialize(self.org, [], [self.kevin], True)
        response = self.client.post(send_url, {"text": "contact send", "omnibox": omnibox})

        self.assertEqual(3, Broadcast.objects.all().count())

        # test sending to an arbitrary user
        self.client.post(
            send_url, {"text": "message content", "omnibox": '{"type": "urn", "id":"tel:2065551212"}'}, follow=True
        )

        self.assertEqual(4, Broadcast.objects.all().count())
        self.assertEqual(1, Contact.objects.filter(urns__path="2065551212").count())

        # test missing senders
        response = self.client.post(send_url, {"text": "message content"}, follow=True)

        self.assertContains(response, "At least one recipient is required")

        response = self.client.post(
            send_url, {"text": "message content", "omnibox": omnibox_serialize(self.org, [], [], True)}, follow=True
        )

        self.assertContains(response, "At least one recipient is required")

        omnibox = omnibox_serialize(self.org, [], [self.kevin], True)
        response = self.client.post(
            send_url, {"text": "this is a test message", "omnibox": omnibox, "_format": "json"}, follow=True
        )

        self.assertContains(response, "success")

        # give Joe a flow run that has stopped on a node
        flow = self.get_flow("color_v13")
        flow_nodes = flow.as_json()["nodes"]
        color_prompt = flow_nodes[0]
        color_split = flow_nodes[4]
        (
            MockSessionWriter(self.joe, flow)
            .visit(color_prompt)
            .send_msg("What is your favorite color?", self.channel)
            .visit(color_split)
            .wait()
            .save()
        ).session.runs.get()

        # no error if we are sending to a flow node
        omnibox = omnibox_serialize(self.org, [], [self.kevin], True)
        payload = {"text": "message content", "omnibox": omnibox, "step_node": color_split["uuid"]}
        response = self.client.post(send_url, payload, follow=True)

        self.assertContains(response, "success")

        response = self.client.post(send_url, payload)

        self.assertRedirect(response, reverse("msgs.msg_inbox"))

        response = self.client.post(send_url, payload, follow=True)

        self.assertContains(response, "success")

        broadcast = Broadcast.objects.order_by("id").last()
        self.assertEqual(broadcast.text, {"base": "message content"})
        self.assertEqual(broadcast.groups.count(), 0)
        self.assertEqual(broadcast.contacts.count(), 1)
        self.assertIn(self.joe, broadcast.contacts.all())

    def test_message_parts(self):
        contact = self.create_contact("Matt", "+12067778811")

        sms = self.create_outgoing_msg(contact, "Text")

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


class BroadcastCRUDLTest(TembaTest):
    def setUp(self):
        super().setUp()

        self.joe, urn_obj = Contact.get_or_create(self.org, "tel:123", user=self.user, name="Joe Blow")
        self.frank, urn_obj = Contact.get_or_create(self.org, "tel:1234", user=self.user, name="Frank Blow")
        self.joe_and_frank = self.create_group("Joe and Frank", [self.joe, self.frank])

    def test_send(self):
        url = reverse("msgs.broadcast_send")

        # can't send if you're not logged in
        omnibox = omnibox_serialize(self.org, [], [self.joe], True)
        response = self.client.post(url, dict(text="Test", omnibox=omnibox))
        self.assertLoginRedirect(response)

        # or just a viewer user
        self.login(self.user)
        response = self.client.post(url, dict(text="Test", omnibox=omnibox))
        self.assertLoginRedirect(response)

        # but editors can
        self.login(self.editor)

        just_joe = self.create_group("Just Joe")
        just_joe.contacts.add(self.joe)

        omnibox = omnibox_serialize(self.org, [just_joe], [self.frank], True)
        omnibox.append('{"type": "urn", "id": "tel:0780000001"}')

        post_data = dict(omnibox=omnibox, text="Hey Joe, where you goin' with that gun in your hand?")
        response = self.client.post(url, post_data)

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
        omnibox = omnibox_serialize(self.org, [], [self.joe], True)
        self.client.post(reverse("msgs.broadcast_send"), dict(omnibox=omnibox, text="Lunch reminder", schedule=True))
        broadcast = Broadcast.objects.get()
        url = reverse("msgs.broadcast_update", args=[broadcast.pk])

        response = self.client.get(url)
        self.assertEqual(list(response.context["form"].fields.keys()), ["message", "omnibox", "loc"])

        # updates still use select2 omnibox
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
        omnibox = omnibox_serialize(self.org, [], [self.joe], True)
        self.client.post(reverse("msgs.broadcast_send"), dict(omnibox=omnibox, text="See you later"))
        self.client.post(reverse("msgs.broadcast_send"), dict(omnibox=omnibox, text="Lunch reminder", schedule=True))

        scheduled = Broadcast.objects.exclude(schedule=None).first()

        response = self.client.get(url)
        self.assertEqual(set(response.context["object_list"]), {scheduled})

    def test_schedule_read(self):
        self.login(self.editor)

        omnibox = omnibox_serialize(self.org, [self.joe_and_frank], [self.joe], True)
        self.client.post(reverse("msgs.broadcast_send"), dict(omnibox=omnibox, text="Lunch reminder", schedule=True))
        broadcast = Broadcast.objects.get()

        # view with empty Send History
        response = self.client.get(reverse("msgs.broadcast_schedule_read", args=[broadcast.pk]))
        self.assertEqual(response.context["object"], broadcast)
        self.assertEqual(response.context["object_list"].count(), 0)

    def test_missing_contacts(self):
        self.login(self.editor)

        omnibox = omnibox_serialize(self.org, [self.joe_and_frank], [self.joe], True)
        self.client.post(reverse("msgs.broadcast_send"), dict(omnibox=omnibox, text="Lunch reminder", schedule=True))
        broadcast = Broadcast.objects.get()

        response = self.client.post(
            reverse("msgs.broadcast_update", args=[broadcast.pk]),
            dict(omnibox="", message="Empty contacts", schedule=True),
        )
        self.assertFormError(response, "form", "omnibox", "This field is required.")


class LabelTest(TembaTest):
    def setUp(self):
        super().setUp()

        self.joe = self.create_contact("Joe Blow", number="073835001")
        self.frank = self.create_contact("Frank", number="073835002")

    def test_get_or_create(self):
        label1 = Label.get_or_create(self.org, self.user, "Spam")
        self.assertEqual("Spam", label1.name)
        self.assertIsNone(label1.folder)

        followup = Label.get_or_create_folder(self.org, self.user, "Follow up")
        label2 = Label.get_or_create(self.org, self.user, "Complaints", followup)
        self.assertEqual("Complaints", label2.name)
        self.assertEqual(followup, label2.folder)

        label2.release(self.admin)

        # will return existing label by name and strip whitespace
        self.assertEqual(label1, Label.get_or_create(self.org, self.user, "Spam"))
        self.assertEqual(label1, Label.get_or_create(self.org, self.user, "  Spam   "))

        # but only if it's active
        self.assertNotEqual(label2, Label.get_or_create(self.org, self.user, "Complaints"))

        # don't allow invalid name
        self.assertRaises(ValueError, Label.get_or_create, self.org, self.user, "+Important")

        # can't use a non-folder as a folder
        self.assertRaises(AssertionError, Label.get_or_create, self.org, self.user, "Important", label1)

    def test_get_or_create_folder(self):
        folder1 = Label.get_or_create_folder(self.org, self.user, "Spam")
        self.assertEqual("Spam", folder1.name)
        self.assertIsNone(folder1.folder)

        # will return existing label by name and strip whitespace
        self.assertEqual(folder1, Label.get_or_create_folder(self.org, self.user, "Spam"))
        self.assertEqual(folder1, Label.get_or_create_folder(self.org, self.user, "  Spam   "))

        folder1.release(self.admin)

        # but only if it's active
        self.assertNotEqual(folder1, Label.get_or_create_folder(self.org, self.user, "Spam"))

        # don't allow invalid name
        self.assertRaises(ValueError, Label.get_or_create_folder, self.org, self.user, "+Important")

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
        msg1 = self.create_incoming_msg(self.joe, "Message 1")
        msg2 = self.create_incoming_msg(self.joe, "Message 2")
        msg3 = self.create_incoming_msg(self.joe, "Message 3")

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
        msg5 = self.create_outgoing_msg(self.joe, "Message")
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
        label4 = Label.get_or_create(self.org, self.user, "Deleted")

        label4.release(self.user)

        msg1 = self.create_incoming_msg(self.joe, "Message 1")
        msg2 = self.create_incoming_msg(self.joe, "Message 2")
        msg3 = self.create_incoming_msg(self.joe, "Message 3")

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

    def test_delete(self):
        folder1 = Label.get_or_create_folder(self.org, self.user, "Folder")
        label1 = Label.get_or_create(self.org, self.user, "Spam", folder1)
        label2 = Label.get_or_create(self.org, self.user, "Social", folder1)
        label3 = Label.get_or_create(self.org, self.user, "Other")

        msg1 = self.create_incoming_msg(self.joe, "Message 1")
        msg2 = self.create_incoming_msg(self.joe, "Message 2")
        msg3 = self.create_incoming_msg(self.joe, "Message 3")

        label1.toggle_label([msg1, msg2], add=True)
        label2.toggle_label([msg1], add=True)
        label3.toggle_label([msg3], add=True)

        ExportMessagesTask.create(self.org, self.admin, label=label1)

        folder1.release(self.admin)
        folder1.refresh_from_db()

        self.assertFalse(folder1.is_active)
        self.assertEqual(self.admin, folder1.modified_by)

        # check that contained labels are also released
        self.assertEqual(0, Label.all_objects.filter(id__in=[label1.id, label2.id], is_active=True).count())
        self.assertEqual(set(), set(Msg.objects.get(id=msg1.id).labels.all()))
        self.assertEqual(set(), set(Msg.objects.get(id=msg2.id).labels.all()))
        self.assertEqual({label3}, set(Msg.objects.get(id=msg3.id).labels.all()))

        label3.release(self.admin)
        label3.refresh_from_db()

        self.assertFalse(label3.is_active)
        self.assertEqual(self.admin, label3.modified_by)
        self.assertEqual(set(), set(Msg.objects.get(id=msg3.id).labels.all()))


class LabelCRUDLTest(TembaTest):
    def test_create_and_update(self):
        create_label_url = reverse("msgs.label_create")
        create_folder_url = reverse("msgs.label_create_folder")

        self.login(self.admin)

        # try to create label with invalid name
        response = self.client.post(create_label_url, {"name": "+Spam"})
        self.assertFormError(response, "form", "name", "Name must not be blank or begin with punctuation")

        # try again with valid name
        self.client.post(create_label_url, {"name": "Spam"}, follow=True)

        label1 = Label.label_objects.get()
        self.assertEqual("Spam", label1.name)
        self.assertIsNone(label1.folder)

        # check that we can't create another with same name
        response = self.client.post(create_label_url, {"name": "Spam"})
        self.assertFormError(response, "form", "name", "Name must be unique")

        # create a folder
        self.client.post(create_folder_url, {"name": "Folder"}, follow=True)
        folder = Label.folder_objects.get(name="Folder")

        # and a label in it
        self.client.post(create_label_url, {"name": "Spam2", "folder": folder.id}, follow=True)
        label2 = Label.label_objects.get(name="Spam2")
        self.assertEqual(folder, label2.folder)

        # update label one
        self.client.post(reverse("msgs.label_update", args=[label1.id]), {"name": "Spam1"})

        label1.refresh_from_db()

        self.assertEqual("Spam1", label1.name)
        self.assertIsNone(label1.folder)

        # try to update to invalid label name
        response = self.client.post(reverse("msgs.label_update", args=[label1.id]), {"name": "+Spam"})
        self.assertFormError(response, "form", "name", "Name must not be blank or begin with punctuation")

        # try creating a new label after reaching the limit on labels
        current_count = Label.all_objects.filter(org=self.org, is_active=True).count()
        with patch.object(Label, "MAX_ORG_LABELS", current_count):
            response = self.client.post(create_label_url, {"name": "CoolStuff"})
            self.assertFormError(
                response,
                "form",
                "name",
                "This org has 3 labels and the limit is 3. "
                "You must delete existing ones before you can create new ones.",
            )

    def test_label_delete(self):
        label1 = Label.get_or_create(self.org, self.user, "Spam")

        delete_url = reverse("msgs.label_delete", args=[label1.id])

        # regular users can't delete labels
        self.login(self.user)
        response = self.client.get(delete_url)

        self.assertEqual(302, response.status_code)

        label1.refresh_from_db()

        self.assertTrue(label1.is_active)

        # admin users can
        self.login(self.admin)
        response = self.client.post(delete_url)

        self.assertEqual(302, response.status_code)

        label1.refresh_from_db()

        self.assertFalse(label1.is_active)

    def test_label_delete_with_flow_dependency(self):
        label1 = Label.get_or_create(self.org, self.user, "Spam")

        self.get_flow("dependencies")
        flow = Flow.objects.filter(name="Dependencies").first()

        flow.label_dependencies.add(label1)

        # release method raises ValueError
        with self.assertRaises(ValueError) as release_error:
            label1.release(self.admin)

        self.assertEqual(str(release_error.exception), f"Cannot delete Label: {label1.name}, used by 1 flows")

    def test_list(self):
        folder = Label.get_or_create_folder(self.org, self.user, "Folder")
        Label.get_or_create(self.org, self.user, "Spam", folder=folder)
        Label.get_or_create(self.org, self.user, "Junk", folder=folder)
        Label.get_or_create(self.org, self.user, "Important")

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
        self.assertEqual(3, len(results))
        self.assertEqual("Important", results[0]["text"])
        self.assertEqual("Junk", results[1]["text"])
        self.assertEqual("Spam", results[2]["text"])


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
        msg1 = self.create_incoming_msg(contact1, "Message 1")
        self.create_incoming_msg(contact1, "Message 2")
        msg3 = self.create_incoming_msg(contact1, "Message 3")
        msg4 = self.create_incoming_msg(contact1, "Message 4")
        call1 = ChannelEvent.create(self.channel, "tel:0783835001", ChannelEvent.TYPE_CALL_IN, timezone.now(), {})
        Broadcast.create(self.org, self.user, "Broadcast 2", contacts=[contact1, contact2], status=QUEUED)
        Broadcast.create(
            self.org,
            self.user,
            "Broadcast 2",
            contacts=[contact1, contact2],
            schedule=Schedule.create_schedule(self.org, self.user, timezone.now(), Schedule.REPEAT_DAILY),
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

        bcast1 = self.create_broadcast(self.user, "Broadcast 1", contacts=[contact1, contact2])
        Msg.objects.filter(broadcast=bcast1).update(status=PENDING)

        msg5, msg6 = tuple(Msg.objects.filter(broadcast=bcast1))
        ChannelEvent.create(self.channel, "tel:0783835002", ChannelEvent.TYPE_CALL_IN, timezone.now(), {})
        Broadcast.create(
            self.org,
            self.user,
            "Broadcast 3",
            contacts=[contact1],
            schedule=Schedule.create_schedule(self.org, self.user, timezone.now(), Schedule.REPEAT_DAILY),
        )

        self.assertEqual(
            SystemLabel.get_counts(self.org),
            {
                SystemLabel.TYPE_INBOX: 3,
                SystemLabel.TYPE_FLOWS: 0,
                SystemLabel.TYPE_ARCHIVED: 1,
                SystemLabel.TYPE_OUTBOX: 3,
                SystemLabel.TYPE_SENT: 0,
                SystemLabel.TYPE_FAILED: 0,
                SystemLabel.TYPE_SCHEDULED: 2,
                SystemLabel.TYPE_CALLS: 2,
            },
        )

        msg1.archive()
        msg3.release()  # deleting an archived msg
        msg4.release()  # deleting a visible msg
        msg5.status = "F"
        msg5.save(update_fields=("status",))
        msg6.status = "S"
        msg6.save(update_fields=("status",))
        call1.release()

        self.assertEqual(
            SystemLabel.get_counts(self.org),
            {
                SystemLabel.TYPE_INBOX: 1,
                SystemLabel.TYPE_FLOWS: 0,
                SystemLabel.TYPE_ARCHIVED: 1,
                SystemLabel.TYPE_OUTBOX: 1,
                SystemLabel.TYPE_SENT: 1,
                SystemLabel.TYPE_FAILED: 1,
                SystemLabel.TYPE_SCHEDULED: 2,
                SystemLabel.TYPE_CALLS: 1,
            },
        )

        msg1.restore()
        msg5.status = "F"  # already failed
        msg5.save(update_fields=("status",))
        msg6.status = "D"
        msg6.save(update_fields=("status",))

        self.assertEqual(
            SystemLabel.get_counts(self.org),
            {
                SystemLabel.TYPE_INBOX: 2,
                SystemLabel.TYPE_FLOWS: 0,
                SystemLabel.TYPE_ARCHIVED: 0,
                SystemLabel.TYPE_OUTBOX: 1,
                SystemLabel.TYPE_SENT: 1,
                SystemLabel.TYPE_FAILED: 1,
                SystemLabel.TYPE_SCHEDULED: 2,
                SystemLabel.TYPE_CALLS: 1,
            },
        )

        msg5.resend()

        self.assertEqual(SystemLabelCount.objects.all().count(), 32)

        # squash our counts
        squash_msgcounts()

        self.assertEqual(
            SystemLabel.get_counts(self.org),
            {
                SystemLabel.TYPE_INBOX: 2,
                SystemLabel.TYPE_FLOWS: 0,
                SystemLabel.TYPE_ARCHIVED: 0,
                SystemLabel.TYPE_OUTBOX: 1,
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
                SystemLabel.TYPE_OUTBOX: 1,
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

        self.joe = self.create_contact("Joe Blow", number="+250788382382")

    def render_template(self, string, context=None):
        from django.template import Context, Template

        context = context or {}
        context = Context(context)
        return Template(string).render(context)

    def assertHasClass(self, text, clazz):
        self.assertTrue(text.find(clazz) >= 0)

    def test_as_icon(self):
        msg = self.create_outgoing_msg(self.joe, "How is it going?", status=QUEUED)
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
