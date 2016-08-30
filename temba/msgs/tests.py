# -*- coding: utf-8 -*-
from __future__ import unicode_literals

import json
import pytz

from datetime import timedelta
from django.conf import settings
from django.core.urlresolvers import reverse
from django.utils import timezone
from mock import patch
from temba.contacts.models import Contact, ContactField, ContactURN, TEL_SCHEME
from temba.channels.models import Channel, ChannelEvent, ChannelLog
from temba.msgs.models import Msg, ExportMessagesTask, RESENT, FAILED, OUTGOING, PENDING, WIRED, DELIVERED, ERRORED
from temba.msgs.models import Broadcast, Label, SystemLabel, UnreachableException
from temba.msgs.models import HANDLED, QUEUED, SENT, INCOMING, INBOX, FLOW
from temba.msgs.tasks import purge_broadcasts_task
from temba.orgs.models import Language
from temba.schedules.models import Schedule
from temba.tests import TembaTest, AnonymousOrg
from temba.utils import dict_to_struct, datetime_to_str
from temba.utils.expressions import get_function_listing
from temba.values.models import Value
from redis_cache import get_redis_connection
from xlrd import open_workbook
from .management.commands.msg_console import MessageConsole
from .tasks import squash_systemlabels


class MsgTest(TembaTest):

    def setUp(self):
        super(MsgTest, self).setUp()

        self.joe = self.create_contact("Joe Blow", "123")
        self.frank = self.create_contact("Frank Blow", "321")
        self.kevin = self.create_contact("Kevin Durant", "987")

        self.just_joe = self.create_group("Just Joe", [self.joe])
        self.joe_and_frank = self.create_group("Joe and Frank", [self.joe, self.frank])

    def test_get_sync_commands(self):
        msg1 = Msg.create_outgoing(self.org, self.admin, self.joe, "Hello, we heard from you.")
        msg2 = Msg.create_outgoing(self.org, self.admin, self.frank, "Hello, we heard from you.")
        msg3 = Msg.create_outgoing(self.org, self.admin, self.kevin, "Hello, we heard from you.")

        commands = Msg.get_sync_commands(self.channel, [msg1, msg2, msg3])

        self.assertEquals(1, len(commands))
        self.assertEquals(3, len(commands[0]['to']))

        msg4 = Msg.create_outgoing(self.org, self.admin, self.kevin, "Hello, there")

        commands = Msg.get_sync_commands(self.channel, [msg1, msg2, msg4])

        self.assertEquals(2, len(commands))
        self.assertEquals(2, len(commands[0]['to']))
        self.assertEquals(1, len(commands[1]['to']))

        msg5 = Msg.create_outgoing(self.org, self.admin, self.frank, "Hello, we heard from you.")

        commands = Msg.get_sync_commands(self.channel, [msg1, msg4, msg5])

        self.assertEquals(3, len(commands))
        self.assertEquals(1, len(commands[0]['to']))
        self.assertEquals(1, len(commands[1]['to']))
        self.assertEquals(1, len(commands[2]['to']))

    def test_archive_and_release(self):
        msg1 = Msg.create_incoming(self.channel, 'tel:123', "Incoming")
        label = Label.get_or_create(self.org, self.admin, "Spam")
        label.toggle_label([msg1], add=True)

        msg1.archive()

        msg1 = Msg.all_messages.get(pk=msg1.pk)
        self.assertEqual(msg1.visibility, Msg.VISIBILITY_ARCHIVED)
        self.assertEqual(set(msg1.labels.all()), {label})  # don't remove labels

        msg1.restore()

        msg1 = Msg.all_messages.get(pk=msg1.pk)
        self.assertEqual(msg1.visibility, Msg.VISIBILITY_VISIBLE)

        msg1.release()

        msg1 = Msg.all_messages.get(pk=msg1.pk)
        self.assertEqual(msg1.visibility, Msg.VISIBILITY_DELETED)
        self.assertEqual(set(msg1.labels.all()), set())  # do remove labels
        self.assertTrue(Label.label_objects.filter(pk=label.pk).exists())  # though don't delete the label object

        # can't archive outgoing messages
        msg2 = Msg.create_outgoing(self.org, self.admin, self.joe, "Outgoing")
        self.assertRaises(ValueError, msg2.archive)

    def assertReleaseCount(self, direction, status, visibility, msg_type, label):
        if direction == OUTGOING:
            msg = Msg.create_outgoing(self.org, self.admin, self.joe, "Whattup Joe")
        else:
            msg = Msg.create_incoming(self.channel, "tel:+250788123123", "Hey hey")

        Msg.all_messages.filter(id=msg.id).update(status=status, direction=direction,
                                                  visibility=visibility, msg_type=msg_type)

        # assert our folder count is right
        counts = SystemLabel.get_counts(self.org)
        self.assertEqual(counts[label], 1)

        # recalculate, check the count again
        SystemLabel.recalculate_counts(self.org, label)
        counts = SystemLabel.get_counts(self.org)
        self.assertEqual(counts[label], 1)

        # release the msg, count should now be 0
        msg.release()
        counts = SystemLabel.get_counts(self.org)
        self.assertEqual(counts[label], 0)

        # more recalculations
        SystemLabel.recalculate_counts(self.org, label)
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
        msg = Msg.all_messages.get(pk=msg.id)
        self.assertEqual(msg.status, 'E')
        self.assertEqual(msg.error_count, 1)
        self.assertIsNotNone(msg.next_attempt)

        Msg.mark_error(r, self.channel, msg)
        msg = Msg.all_messages.get(pk=msg.id)
        self.assertEqual(msg.status, 'E')
        self.assertEqual(msg.error_count, 2)
        self.assertIsNotNone(msg.next_attempt)

        Msg.mark_error(r, self.channel, msg)
        msg = Msg.all_messages.get(pk=msg.id)
        self.assertEqual(msg.status, 'F')

        # test with mock message
        msg = dict_to_struct('MsgStruct', Msg.create_outgoing(self.org, self.admin, self.joe, "Test 2").as_task_json())

        Msg.mark_error(r, self.channel, msg)
        msg = Msg.all_messages.get(pk=msg.id)
        self.assertEqual(msg.status, 'E')
        self.assertEqual(msg.error_count, 1)
        self.assertIsNotNone(msg.next_attempt)

        Msg.mark_error(r, self.channel, msg)
        msg = Msg.all_messages.get(pk=msg.id)
        self.assertEqual(msg.status, 'E')
        self.assertEqual(msg.error_count, 2)
        self.assertIsNotNone(msg.next_attempt)

        Msg.mark_error(r, self.channel, msg)
        msg = Msg.all_messages.get(pk=msg.id)
        self.assertEqual(msg.status, 'F')

    def test_send_message_auto_completion_processor(self):
        outbox_url = reverse('msgs.msg_outbox')

        # login in as manager, with contacts but without extra contactfields yet
        self.login(self.admin)
        completions = [dict(name='contact', display="Contact Name"),
                       dict(name='contact.first_name', display="Contact First Name"),
                       dict(name='contact.groups', display="Contact Groups"),
                       dict(name='contact.language', display="Contact Language"),
                       dict(name='contact.name', display="Contact Name"),
                       dict(name='contact.tel', display="Contact Phone"),
                       dict(name='contact.tel_e164', display="Contact Phone - E164"),
                       dict(name='contact.uuid', display="Contact UUID"),
                       dict(name="date", display="Current Date and Time"),
                       dict(name="date.now", display="Current Date and Time"),
                       dict(name="date.today", display="Current Date"),
                       dict(name="date.tomorrow", display="Tomorrow's Date"),
                       dict(name="date.yesterday", display="Yesterday's Date")]

        response = self.client.get(outbox_url)

        # check our completions JSON and functions JSON
        self.assertEquals(response.context['completions'], json.dumps(completions))
        self.assertEquals(response.context['function_completions'], json.dumps(get_function_listing()))

        # add some contact fields
        field = ContactField.get_or_create(self.org, self.admin, 'cell', "Cell")
        completions.append(dict(name="contact.%s" % str(field.key), display="Contact Field: Cell"))

        field = ContactField.get_or_create(self.org, self.admin, 'sector', "Sector")
        completions.append(dict(name="contact.%s" % str(field.key), display="Contact Field: Sector"))

        response = self.client.get(outbox_url)

        # contact fields are included at the end in alphabetical order
        self.assertEquals(response.context['completions'], json.dumps(completions))

    def test_create_outgoing(self):
        tel_urn = "tel:250788382382"
        tel_contact = Contact.get_or_create(self.org, self.user, urns=[tel_urn])
        tel_urn_obj = tel_contact.urn_objects[tel_urn]
        twitter_urn = "twitter:joe"
        twitter_contact = Contact.get_or_create(self.org, self.user, urns=[twitter_urn])
        twitter_urn_obj = twitter_contact.urn_objects[twitter_urn]

        # check creating by URN string
        msg = Msg.create_outgoing(self.org, self.admin, tel_urn, "Extra spaces to remove    ")
        self.assertEquals(msg.contact, tel_contact)
        self.assertEquals(msg.contact_urn, tel_urn_obj)
        self.assertEquals(msg.text, "Extra spaces to remove")  # check message text is stripped

        # check creating by URN string and specific channel
        msg = Msg.create_outgoing(self.org, self.admin, tel_urn, "Hello 1", channel=self.channel)
        self.assertEquals(msg.contact, tel_contact)
        self.assertEquals(msg.contact_urn, tel_urn_obj)

        # try creating by URN string and specific channel with different scheme
        with self.assertRaises(UnreachableException):
            Msg.create_outgoing(self.org, self.admin, twitter_urn, "Hello 1", channel=self.channel)

        # check creating by URN object
        msg = Msg.create_outgoing(self.org, self.admin, tel_urn_obj, "Hello 1")
        self.assertEquals(msg.contact, tel_contact)
        self.assertEquals(msg.contact_urn, tel_urn_obj)

        # check creating by URN object and specific channel
        msg = Msg.create_outgoing(self.org, self.admin, tel_urn_obj, "Hello 1", channel=self.channel)
        self.assertEquals(msg.contact, tel_contact)
        self.assertEquals(msg.contact_urn, tel_urn_obj)

        # try creating by URN object and specific channel with different scheme
        with self.assertRaises(UnreachableException):
            Msg.create_outgoing(self.org, self.admin, twitter_urn_obj, "Hello 1", channel=self.channel)

        # check creating by contact
        msg = Msg.create_outgoing(self.org, self.admin, tel_contact, "Hello 1")
        self.assertEquals(msg.contact, tel_contact)
        self.assertEquals(msg.contact_urn, tel_urn_obj)

        # check creating by contact and specific channel
        msg = Msg.create_outgoing(self.org, self.admin, tel_contact, "Hello 1", channel=self.channel)
        self.assertEquals(msg.contact, tel_contact)
        self.assertEquals(msg.contact_urn, tel_urn_obj)

        # try creating by contact and specific channel with different scheme
        with self.assertRaises(UnreachableException):
            Msg.create_outgoing(self.org, self.admin, twitter_contact, "Hello 1", channel=self.channel)

        # Can't handle outgoing messages
        with self.assertRaises(ValueError):
            msg.handle()

        # can't create outgoing messages without org or user
        with self.assertRaises(ValueError):
            Msg.create_outgoing(None, self.admin, "tel:250783835665", "Hello World")
        with self.assertRaises(ValueError):
            Msg.create_outgoing(self.org, None, "tel:250783835665", "Hello World")

        # case where the channel number is amongst contact broadcasted to
        # cannot sent more than 10 same message in period of 5 minutes

        for number in range(0, 10):
            Msg.create_outgoing(self.org, self.admin, "tel:" + self.channel.address, 'Infinite Loop')

        # now that we have 10 same messages then,
        must_return_none = Msg.create_outgoing(self.org, self.admin, "tel:" + self.channel.address, 'Infinite Loop')
        self.assertIsNone(must_return_none)

    def test_create_incoming(self):
        Msg.create_incoming(self.channel, "tel:250788382382", "It's going well")
        Msg.create_incoming(self.channel, "tel:250788382382", "My name is Frank")
        msg = Msg.create_incoming(self.channel, "tel:250788382382", "Yes, 3.")

        self.assertEqual(msg.text, "Yes, 3.")
        self.assertEqual(unicode(msg), "Yes, 3.")

        # assert there are 3 unread msgs for this org
        self.assertEqual(Msg.get_unread_msg_count(self.admin), 3)

        # second go shouldn't hit DB
        with self.assertNumQueries(0):
            self.assertEqual(Msg.get_unread_msg_count(self.admin), 3)

        # Can't send incoming messages
        with self.assertRaises(Exception):
            msg.send()

        # can't create outgoing messages against an unassigned channel
        unassigned_channel = Channel.create(None, self.admin, None, 'A', None, secret="67890", gcm_id="456")

        with self.assertRaises(Exception):
            Msg.create_incoming(unassigned_channel, "tel:250788382382", "No dice")

        # test blocked contacts are skipped from inbox and are not handled by flows
        contact = self.create_contact("Blocked contact", "250728739305")
        contact.is_blocked = True
        contact.save()
        ignored_msg = Msg.create_incoming(self.channel, contact.get_urn().urn, "My msg should be archived")
        ignored_msg = Msg.all_messages.get(pk=ignored_msg.pk)
        self.assertEqual(ignored_msg.visibility, Msg.VISIBILITY_ARCHIVED)
        self.assertEqual(ignored_msg.status, HANDLED)

        # hit the inbox page, that should reset our unread count
        self.login(self.admin)
        self.client.get(reverse('msgs.msg_inbox'))

        self.assertEqual(Msg.get_unread_msg_count(self.admin), 3)

    def test_empty(self):
        broadcast = Broadcast.create(self.org, self.admin, "If a broadcast is sent and nobody receives it, does it still send?", [])
        broadcast.send(True)

        # should have no messages but marked as sent
        self.assertEquals(0, broadcast.msgs.all().count())
        self.assertEquals(SENT, broadcast.status)

    def test_update_contacts(self):
        broadcast = Broadcast.create(self.org, self.admin, "If a broadcast is sent and nobody receives it, does it still send?", [])

        # update the contacts using contact ids
        broadcast.update_contacts([self.joe.id])

        broadcast.refresh_from_db()
        self.assertEquals(1, broadcast.recipient_count)

        # send it
        broadcast.send()

        # assert that recipient is set
        self.assertEqual(set(broadcast.recipients.all()), {self.joe})

    def test_outbox(self):
        self.login(self.admin)

        contact = Contact.get_or_create(self.channel.org, self.admin, name=None, urns=['tel:250788382382'])
        broadcast1 = Broadcast.create(self.channel.org, self.admin, 'How is it going?', [contact])

        # now send the broadcast so we have messages
        broadcast1.send(trigger_send=False)
        (msg1,) = tuple(Msg.all_messages.filter(broadcast=broadcast1))

        response = self.client.get(reverse('msgs.msg_outbox'))
        self.assertContains(response, "Outbox (1)")
        self.assertEqual(set(response.context_data['object_list']), {msg1})

        broadcast2 = Broadcast.create(self.channel.org, self.admin, 'kLab is an awesome place for @contact.name',
                                      [self.kevin, self.joe_and_frank])

        # now send the broadcast so we have messages
        broadcast2.send(trigger_send=False)
        msg4, msg3, msg2 = tuple(Msg.all_messages.filter(broadcast=broadcast2))

        response = self.client.get(reverse('msgs.msg_outbox'))

        self.assertContains(response, "Outbox (4)")
        self.assertEqual(set(response.context_data['object_list']), {msg4, msg3, msg2, msg1})

        response = self.client.get("%s?search=kevin" % reverse('msgs.msg_outbox'))
        self.assertEqual(set(response.context_data['object_list']), {Msg.all_messages.get(contact=self.kevin)})

        response = self.client.get("%s?search=joe" % reverse('msgs.msg_outbox'))
        self.assertEqual(set(response.context_data['object_list']), {Msg.all_messages.get(contact=self.joe)})

        response = self.client.get("%s?search=frank" % reverse('msgs.msg_outbox'))
        self.assertEqual(set(response.context_data['object_list']), {Msg.all_messages.get(contact=self.frank)})

        response = self.client.get("%s?search=just" % reverse('msgs.msg_outbox'))
        self.assertEqual(set(response.context_data['object_list']), set())

        response = self.client.get("%s?search=is" % reverse('msgs.msg_outbox'))
        self.assertEqual(set(response.context_data['object_list']), {msg4, msg3, msg2, msg1})

        # make sure variables that are replaced in text messages match as well
        response = self.client.get("%s?search=durant" % reverse('msgs.msg_outbox'))
        self.assertEqual(set(response.context_data['object_list']), {Msg.all_messages.get(contact=self.kevin)})

    def do_msg_action(self, url, msgs, action, label=None, label_add=True):
        post_data = dict()
        post_data['action'] = action
        post_data['objects'] = [m.id for m in msgs]
        post_data['label'] = label.pk if label else None
        post_data['add'] = label_add
        return self.client.post(url, post_data, follow=True)

    def test_inbox(self):
        inbox_url = reverse('msgs.msg_inbox')

        joe_tel = self.joe.get_urn(TEL_SCHEME).urn
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
        self.assertEquals(302, response.status_code)

        # visit inbox page as a manager of the organization
        response = self.fetch_protected(inbox_url, self.admin)

        self.assertEquals(response.context['object_list'].count(), 5)
        self.assertEquals(response.context['folders'][0]['url'], '/msg/inbox/')
        self.assertEquals(response.context['folders'][0]['count'], 5)
        self.assertEquals(response.context['actions'], ['archive', 'label'])

        # visit inbox page as administrator
        response = self.fetch_protected(inbox_url, self.admin)

        self.assertEquals(response.context['object_list'].count(), 5)
        self.assertEquals(response.context['actions'], ['archive', 'label'])

        # let's add some labels
        folder = Label.get_or_create_folder(self.org, self.user, "folder")
        label1 = Label.get_or_create(self.org, self.user, "label1", folder)
        Label.get_or_create(self.org, self.user, "label2", folder)
        label3 = Label.get_or_create(self.org, self.user, "label3")

        # test labeling a messages
        self.do_msg_action(inbox_url, [msg1, msg2], 'label', label1)
        self.assertEqual(list(Msg.all_messages.filter(labels=label1)), [msg2, msg1])

        # test removing a label
        self.do_msg_action(inbox_url, [msg2], 'label', label1, label_add=False)
        self.assertEqual(list(Msg.all_messages.filter(labels=label1)), [msg1])

        # label more messages
        self.do_msg_action(inbox_url, [msg1, msg2, msg3], 'label', label3)
        self.assertEqual(list(Msg.all_messages.filter(labels=label1)), [msg1])
        self.assertEqual(list(Msg.all_messages.filter(labels=label3)), [msg3, msg2, msg1])

        # update our label name
        response = self.client.get(reverse('msgs.label_update', args=[label1.pk]))
        self.assertEquals(200, response.status_code)
        self.assertTrue('folder' in response.context['form'].fields)

        post_data = dict(name="Foo")
        response = self.client.post(reverse('msgs.label_update', args=[label1.pk]), post_data)
        self.assertEquals(302, response.status_code)
        label1 = Label.label_objects.get(pk=label1.pk)
        self.assertEquals("Foo", label1.name)

        # test deleting the label
        response = self.client.get(reverse('msgs.label_delete', args=[label1.pk]))
        self.assertEquals(200, response.status_code)

        response = self.client.post(reverse('msgs.label_delete', args=[label1.pk]))
        self.assertEquals(302, response.status_code)
        self.assertFalse(Label.label_objects.filter(pk=label1.id))

        # shouldn't have a remove on the update page

        # test archiving a msg
        self.assertEqual(set(msg1.labels.all()), {label3})
        post_data = dict(action='archive', objects=msg1.pk)

        response = self.client.post(inbox_url, post_data, follow=True)
        self.assertEqual(response.status_code, 200)

        # now one msg is archived
        self.assertEqual(list(Msg.all_messages.filter(visibility=Msg.VISIBILITY_ARCHIVED)), [msg1])

        # archiving doesn't remove labels
        msg1 = Msg.all_messages.get(pk=msg1.pk)
        self.assertEqual(set(msg1.labels.all()), {label3})

        # visit the the archived messages page
        archive_url = reverse('msgs.msg_archived')

        # visit archived page  as a user not in the organization
        self.login(self.non_org_user)
        response = self.client.get(archive_url)
        self.assertEquals(302, response.status_code)

        # visit archived page as a manager of the organization
        response = self.fetch_protected(archive_url, self.admin)

        self.assertEquals(response.context['object_list'].count(), 1)
        self.assertEquals(response.context['actions'], ['restore', 'label', 'delete'])

        # check that the inbox does not contains archived messages

        # visit inbox page as a user not in the organization
        self.login(self.non_org_user)
        response = self.client.get(inbox_url)
        self.assertEquals(302, response.status_code)

        # visit inbox page as an admin of the organization
        response = self.fetch_protected(inbox_url, self.admin)

        self.assertEquals(response.context['object_list'].count(), 4)
        self.assertEquals(response.context['actions'], ['archive', 'label'])

        # test restoring an archived message back to inbox
        post_data = dict(action='restore', objects=[msg1.pk])
        self.client.post(inbox_url, post_data, follow=True)
        self.assertEquals(Msg.all_messages.filter(visibility=Msg.VISIBILITY_ARCHIVED).count(), 0)

        # messages from test contact are not included in the inbox
        test_contact = Contact.get_test_contact(self.admin)
        Msg.create_incoming(self.channel, test_contact.get_urn().urn, 'Bla Blah')

        response = self.client.get(inbox_url)
        self.assertEqual(Msg.all_messages.all().count(), 7)
        self.assertEqual(response.context['object_list'].count(), 5)

        # archiving a message removes it from the inbox
        Msg.apply_action_archive(self.user, [msg1])

        response = self.client.get(inbox_url)
        self.assertEqual(response.context['object_list'].count(), 4)

        # and moves it to the Archived page
        response = self.client.get(archive_url)
        self.assertEqual(response.context['object_list'].count(), 1)

        # deleting it removes it from the Archived page
        response = self.client.post(archive_url, dict(action='delete', objects=[msg1.pk]), follow=True)
        self.assertEqual(response.context['object_list'].count(), 0)

        # now check inbox as viewer user
        response = self.fetch_protected(inbox_url, self.user)
        self.assertEqual(response.context['object_list'].count(), 4)

        # check that viewer user cannot label messages
        post_data = dict(action='label', objects=[msg5.pk], label=label1.pk, add=True)
        self.client.post(inbox_url, post_data, follow=True)
        self.assertEqual(msg5.labels.all().count(), 0)

        # or archive messages
        self.assertEqual(Msg.all_messages.get(pk=msg5.pk).visibility, Msg.VISIBILITY_VISIBLE)
        post_data = dict(action='archive', objects=[msg5.pk])
        self.client.post(inbox_url, post_data, follow=True)
        self.assertEqual(Msg.all_messages.get(pk=msg5.pk).visibility, Msg.VISIBILITY_VISIBLE)

        # search on inbox just on the message text
        response = self.client.get("%s?search=message" % inbox_url)
        self.assertEqual(len(response.context_data['object_list']), 4)

        response = self.client.get("%s?search=5" % inbox_url)
        self.assertEqual(len(response.context_data['object_list']), 1)

        # can search on contact field
        response = self.client.get("%s?search=joe" % inbox_url)
        self.assertEqual(len(response.context_data['object_list']), 4)

    def test_flows(self):
        url = reverse('msgs.msg_flow')

        msg1 = Msg.create_incoming(self.channel, self.joe.get_urn().urn, "test 1", msg_type='F')

        # user not in org can't access
        self.login(self.non_org_user)
        self.assertLoginRedirect(self.client.get(url))

        # org viewer can
        self.login(self.admin)
        response = self.client.get(url)

        self.assertEquals(set(response.context['object_list']), {msg1})
        self.assertEquals(response.context['actions'], ['label'])

    def test_failed(self):
        failed_url = reverse('msgs.msg_failed')

        msg1 = Msg.create_outgoing(self.org, self.admin, self.joe, "message number 1")
        msg1.status = 'F'
        msg1.save()

        # create a log for it
        log = ChannelLog.objects.create(channel=msg1.channel, msg=msg1, is_error=True, description="Failed")

        # create broadcast and fail the only message
        broadcast = Broadcast.create(self.org, self.admin, "message number 2", [self.joe])
        broadcast.send(trigger_send=False)
        broadcast.get_messages().update(status='F')
        broadcast.update()
        msg2 = broadcast.get_messages()[0]

        self.assertEquals(FAILED, broadcast.status)

        # message without a broadcast
        msg3 = Msg.create_outgoing(self.org, self.admin, self.joe, "messsage number 3")
        msg3.status = 'F'
        msg3.save()

        # visit fail page  as a user not in the organization
        self.login(self.non_org_user)
        response = self.client.get(failed_url)
        self.assertEquals(302, response.status_code)

        # visit inbox page as an administrator
        response = self.fetch_protected(failed_url, self.admin)

        self.assertEquals(response.context['object_list'].count(), 3)
        self.assertEquals(response.context['actions'], ['resend'])

        self.assertContains(response, reverse('channels.channellog_read', args=[log.id]))

        # make the org anonymous
        with AnonymousOrg(self.org):
            response = self.fetch_protected(failed_url, self.admin)
            self.assertNotContains(response, reverse('channels.channellog_read', args=[log.id]))

        # let's resend some messages
        self.client.post(failed_url, dict(action='resend', objects=msg2.pk), follow=True)

        # check for the resent message and the new one being resent
        self.assertEqual(set(Msg.all_messages.filter(status=RESENT)), {msg2})
        self.assertEqual(Msg.all_messages.filter(status=PENDING).count(), 1)

        # make sure there was a new outgoing message created that got attached to our broadcast
        self.assertEquals(1, broadcast.get_messages().count())

        resent_msg = broadcast.get_messages()[0]
        self.assertNotEquals(msg2, resent_msg)
        self.assertEquals(msg2.text, resent_msg.text)
        self.assertEquals(msg2.contact, resent_msg.contact)
        self.assertEquals(PENDING, resent_msg.status)

    @patch('temba.utils.email.send_temba_email')
    def test_message_export(self, mock_send_temba_email):
        self.clear_storage()
        self.login(self.admin)

        # create some messages...
        joe_urn = self.joe.get_urn(TEL_SCHEME).urn
        msg1 = Msg.create_incoming(self.channel, joe_urn, "hello 1")
        msg2 = Msg.create_incoming(self.channel, joe_urn, "hello 2")
        msg3 = Msg.create_incoming(self.channel, joe_urn, "hello 3")
        msg4 = Msg.create_incoming(None, None, "hello 4", org=self.org, contact=self.joe)  # like a surveyor message

        # inbound message with media attached, such as an ivr recording
        msg5 = Msg.create_incoming(self.channel, joe_urn, "Media message", media='audio:http://rapidpro.io/audio/sound.mp3')

        # outgoing message
        msg6 = Msg.create_outgoing(self.org, self.admin, self.joe, "Hey out 6")
        msg7 = Msg.create_outgoing(self.org, self.admin, self.joe, "Hey out 7")
        msg8 = Msg.create_outgoing(self.org, self.admin, self.joe, "Hey out 8")
        msg9 = Msg.create_outgoing(self.org, self.admin, self.joe, "Hey out 9")

        # mark msg as sent
        msg6.status = SENT
        msg6.save()

        # mark msg as delivered
        msg7.status = DELIVERED
        msg7.save()

        # mark msg as errored
        msg8.status = ERRORED
        msg8.save()

        # mark message as failed
        msg9.status = FAILED
        msg9.save()

        self.assertTrue(msg5.is_media_type_audio())
        self.assertEqual('http://rapidpro.io/audio/sound.mp3', msg5.get_media_path())

        # label first message
        label = Label.get_or_create(self.org, self.user, "label1")
        label.toggle_label([msg1], add=True)

        # archive last message
        msg3.visibility = Msg.VISIBILITY_ARCHIVED
        msg3.save()

        # create a dummy export task so that we won't be able to export
        blocking_export = ExportMessagesTask.objects.create(org=self.org, created_by=self.admin, modified_by=self.admin)
        response = self.client.post(reverse('msgs.msg_export'), follow=True)
        self.assertContains(response, "already an export in progress")

        # perform the export manually, assert how many queries
        self.assertNumQueries(7, lambda: blocking_export.do_export())

        self.client.post(reverse('msgs.msg_export'))
        task = ExportMessagesTask.objects.all().order_by('-id').first()

        filename = "%s/test_orgs/%d/message_exports/%s.xls" % (settings.MEDIA_ROOT, self.org.pk, task.uuid)
        workbook = open_workbook(filename, 'rb')
        sheet = workbook.sheets()[0]

        self.assertEquals(sheet.nrows, 9)  # msg3 not included as it's archived

        self.assertExcelRow(sheet, 0, ["Date", "Contact", "Contact Type", "Name", "Contact UUID", "Direction",
                                       "Text", "Labels", "Status"])

        self.assertExcelRow(sheet, 1,
                            [msg9.created_on, "123", "tel", "Joe Blow", msg9.contact.uuid, "Outgoing",
                             "Hey out 9", "", "Failed Sending"], pytz.UTC)

        self.assertExcelRow(sheet, 2,
                            [msg8.created_on, "123", "tel", "Joe Blow", msg8.contact.uuid, "Outgoing",
                             "Hey out 8", "", "Error Sending"], pytz.UTC)

        self.assertExcelRow(sheet, 3,
                            [msg7.created_on, "123", "tel", "Joe Blow", msg7.contact.uuid, "Outgoing",
                             "Hey out 7", "", "Delivered"], pytz.UTC)

        self.assertExcelRow(sheet, 4,
                            [msg6.created_on, "123", "tel", "Joe Blow", msg6.contact.uuid, "Outgoing",
                             "Hey out 6", "", "Sent"], pytz.UTC)

        self.assertExcelRow(sheet, 5, [msg5.created_on, "123", "tel", "Joe Blow", msg5.contact.uuid, "Incoming",
                                       "Media message", "", "Handled"], pytz.UTC)

        self.assertExcelRow(sheet, 6, [msg4.created_on, "", "", "Joe Blow", msg4.contact.uuid, "Incoming",
                                       "hello 4", "", "Handled"], pytz.UTC)

        self.assertExcelRow(sheet, 7, [msg2.created_on, "123", "tel", "Joe Blow", msg2.contact.uuid, "Incoming",
                                       "hello 2", "", "Handled"], pytz.UTC)

        self.assertExcelRow(sheet, 8, [msg1.created_on, "123", "tel", "Joe Blow", msg1.contact.uuid, "Incoming",
                                       "hello 1", "label1", "Handled"], pytz.UTC)

        email_args = mock_send_temba_email.call_args[0]  # all positional args

        self.assertEqual(email_args[0], "Your messages export is ready")
        self.assertIn('https://app.rapidpro.io/assets/download/message_export/%d/' % task.pk, email_args[1])
        self.assertNotIn('{{', email_args[1])
        self.assertIn('https://app.rapidpro.io/assets/download/message_export/%d/' % task.pk, email_args[2])
        self.assertNotIn('{{', email_args[2])

        ExportMessagesTask.objects.all().delete()

        # visit the filter page
        response = self.client.get(reverse('msgs.msg_filter', args=[label.pk]))
        self.assertContains(response, "Export Data")

        self.client.post("%s?label=%s" % (reverse('msgs.msg_export'), label.pk))
        task = ExportMessagesTask.objects.get()

        filename = "%s/test_orgs/%d/message_exports/%s.xls" % (settings.MEDIA_ROOT, self.org.pk, task.uuid)
        workbook = open_workbook(filename, 'rb')
        sheet = workbook.sheets()[0]

        self.assertEquals(sheet.nrows, 2)  # only header and msg1
        self.assertExcelRow(sheet, 1, [msg1.created_on, "123", "tel", "Joe Blow", msg1.contact.uuid, "Incoming", "hello 1", "label1", "Handled"], pytz.UTC)

        ExportMessagesTask.objects.all().delete()

        # test as anon org to check that URNs don't end up in exports
        with AnonymousOrg(self.org):
            self.client.post(reverse('msgs.msg_export'))
            task = ExportMessagesTask.objects.get()

            filename = "%s/test_orgs/%d/message_exports/%s.xls" % (settings.MEDIA_ROOT, self.org.pk, task.uuid)
            workbook = open_workbook(filename, 'rb')
            sheet = workbook.sheets()[0]

            self.assertEquals(sheet.nrows, 9)

            self.assertExcelRow(sheet, 1, [msg9.created_on, "%010d" % self.joe.pk, "tel", "Joe Blow", msg9.contact.uuid,
                                           "Outgoing", "Hey out 9", "", "Failed Sending"], pytz.UTC)

            self.assertExcelRow(sheet, 2, [msg8.created_on, "%010d" % self.joe.pk, "tel", "Joe Blow", msg8.contact.uuid,
                                           "Outgoing", "Hey out 8", "", "Error Sending"], pytz.UTC)

            self.assertExcelRow(sheet, 3, [msg7.created_on, "%010d" % self.joe.pk, "tel", "Joe Blow", msg7.contact.uuid,
                                           "Outgoing", "Hey out 7", "", "Delivered"], pytz.UTC)

            self.assertExcelRow(sheet, 4, [msg6.created_on, "%010d" % self.joe.pk, "tel", "Joe Blow", msg6.contact.uuid,
                                           "Outgoing", "Hey out 6", "", "Sent"], pytz.UTC)

            self.assertExcelRow(sheet, 5, [msg5.created_on, "%010d" % self.joe.pk, "tel", "Joe Blow", msg5.contact.uuid,
                                           "Incoming", "Media message", "", "Handled"], pytz.UTC)

            self.assertExcelRow(sheet, 6, [msg4.created_on, "%010d" % self.joe.pk, "", "Joe Blow", msg4.contact.uuid,
                                           "Incoming", "hello 4", "", "Handled"], pytz.UTC)

            self.assertExcelRow(sheet, 7, [msg2.created_on, "%010d" % self.joe.pk, "tel", "Joe Blow", msg2.contact.uuid,
                                           "Incoming", "hello 2", "", "Handled"], pytz.UTC)

            self.assertExcelRow(sheet, 8, [msg1.created_on, "%010d" % self.joe.pk, "tel", "Joe Blow", msg1.contact.uuid,
                                           "Incoming", "hello 1", "label1", "Handled"], pytz.UTC)

    def assertHasClass(self, text, clazz):
        self.assertTrue(text.find(clazz) >= 0)

    def test_templatetags(self):
        from .templatetags.sms import as_icon

        msg = Msg.create_outgoing(self.org, self.admin, "tel:250788382382", "How is it going?")
        now = timezone.now()
        two_hours_ago = now - timedelta(hours=2)

        self.assertHasClass(as_icon(msg), 'icon-bubble-dots-2 green')
        msg.created_on = two_hours_ago
        self.assertHasClass(as_icon(msg), 'icon-bubble-dots-2 green')
        msg.status = 'S'
        self.assertHasClass(as_icon(msg), 'icon-bubble-right green')
        msg.status = 'D'
        self.assertHasClass(as_icon(msg), 'icon-bubble-check green')
        msg.status = 'E'
        self.assertHasClass(as_icon(msg), 'icon-bubble-notification red')
        msg.direction = 'I'
        self.assertHasClass(as_icon(msg), 'icon-bubble-user primary')
        msg.msg_type = 'V'
        self.assertHasClass(as_icon(msg), 'icon-phone')

        # default cause is pending sent
        self.assertHasClass(as_icon(None), 'icon-bubble-dots-2 green')

        in_call = ChannelEvent.create(self.channel, self.joe.get_urn(TEL_SCHEME).urn,
                                      ChannelEvent.TYPE_CALL_IN, timezone.now(), 5)
        self.assertHasClass(as_icon(in_call), 'icon-call-incoming green')

        in_miss = ChannelEvent.create(self.channel, self.joe.get_urn(TEL_SCHEME).urn,
                                      ChannelEvent.TYPE_CALL_IN_MISSED, timezone.now(), 5)
        self.assertHasClass(as_icon(in_miss), 'icon-call-incoming red')

        out_call = ChannelEvent.create(self.channel, self.joe.get_urn(TEL_SCHEME).urn,
                                       ChannelEvent.TYPE_CALL_OUT, timezone.now(), 5)
        self.assertHasClass(as_icon(out_call), 'icon-call-outgoing green')

        out_miss = ChannelEvent.create(self.channel, self.joe.get_urn(TEL_SCHEME).urn,
                                       ChannelEvent.TYPE_CALL_OUT_MISSED, timezone.now(), 5)
        self.assertHasClass(as_icon(out_miss), 'icon-call-outgoing red')


class MsgCRUDLTest(TembaTest):
    def setUp(self):
        super(MsgCRUDLTest, self).setUp()

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
        msg1 = self.create_msg(direction='I', msg_type='I', contact=self.joe, text="test1")
        msg2 = self.create_msg(direction='I', msg_type='I', contact=self.frank, text="test2")
        msg3 = self.create_msg(direction='I', msg_type='I', contact=self.billy, text="test3")
        msg4 = self.create_msg(direction='I', msg_type='I', contact=self.joe, text="test4", visibility=Msg.VISIBILITY_ARCHIVED)
        msg5 = self.create_msg(direction='I', msg_type='I', contact=self.joe, text="test5", visibility=Msg.VISIBILITY_DELETED)
        msg6 = self.create_msg(direction='I', msg_type='F', contact=self.joe, text="flow test")

        # apply the labels
        label1.toggle_label([msg1, msg2], add=True)
        label2.toggle_label([msg2, msg3], add=True)
        label3.toggle_label([msg1, msg2, msg3, msg4, msg5, msg6], add=True)

        # can't visit a filter page as a non-org user
        self.login(self.non_org_user)
        response = self.client.get(reverse('msgs.msg_filter', args=[label3.pk]))
        self.assertLoginRedirect(response)

        # can as org viewer user
        self.login(self.user)
        response = self.client.get(reverse('msgs.msg_filter', args=[label3.pk]))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context['actions'], ['unlabel', 'label'])
        self.assertNotContains(response, reverse('msgs.label_update', args=[label3.pk]))  # can't update label
        self.assertNotContains(response, reverse('msgs.label_delete', args=[label3.pk]))  # can't delete label

        # check that test and non-visible messages are excluded, and messages and ordered newest to oldest
        self.assertEqual(list(response.context['object_list']), [msg6, msg3, msg2, msg1])

        # check viewing a folder
        response = self.client.get(reverse('msgs.msg_filter', args=[folder.pk]))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context['actions'], ['unlabel', 'label'])
        self.assertNotContains(response, reverse('msgs.label_update', args=[folder.pk]))  # can't update folder
        self.assertNotContains(response, reverse('msgs.label_delete', args=[folder.pk]))  # can't delete folder

        # messages from contained labels are rolled up without duplicates
        self.assertEqual(list(response.context['object_list']), [msg3, msg2, msg1])

        # search on folder by message text
        response = self.client.get("%s?search=test2" % reverse('msgs.msg_filter', args=[folder.pk]))
        self.assertEqual(set(response.context_data['object_list']), {msg2})

        # search on label by contact name
        response = self.client.get("%s?search=joe" % reverse('msgs.msg_filter', args=[label3.pk]))
        self.assertEqual(set(response.context_data['object_list']), {msg1, msg6})

        # check admin users see edit and delete options for labels and folders
        self.login(self.admin)
        response = self.client.get(reverse('msgs.msg_filter', args=[folder.pk]))
        self.assertContains(response, reverse('msgs.label_update', args=[folder.pk]))
        self.assertContains(response, reverse('msgs.label_delete', args=[folder.pk]))

        response = self.client.get(reverse('msgs.msg_filter', args=[label1.pk]))
        self.assertContains(response, reverse('msgs.label_update', args=[label1.pk]))
        self.assertContains(response, reverse('msgs.label_delete', args=[label1.pk]))


class BroadcastTest(TembaTest):
    def setUp(self):
        super(BroadcastTest, self).setUp()

        self.joe = self.create_contact("Joe Blow", "123")
        self.frank = self.create_contact("Frank Blow", "321")

        self.just_joe = self.create_group("Just Joe", [self.joe])

        self.joe_and_frank = self.create_group("Joe and Frank", [self.joe, self.frank])

        self.kevin = self.create_contact(name="Kevin Durant", number="987")
        self.lucy = self.create_contact(name="Lucy M", twitter="lucy")

        # a Twitter channel
        self.twitter = Channel.create(self.org, self.user, None, 'TT')

    def test_broadcast_batch(self):
        broadcast = Broadcast.create(self.org, self.user, "Like a tweet", [self.joe_and_frank, self.kevin])
        self.assertEquals(3, broadcast.recipient_count)

        # change our broadcast size to 2
        import temba.msgs.models as msgs_models
        orig_batch_size = msgs_models.BATCH_SIZE

        try:
            # downsize our batches and send it (this tests other code paths)
            msgs_models.BATCH_SIZE = 2
            broadcast.send()

            self.assertEquals(broadcast.get_message_count(), 3)
            self.assertEqual(set(broadcast.recipients.all()), {self.joe, self.frank, self.kevin})
        finally:
            msgs_models.BATCH_SIZE = orig_batch_size

    def test_broadcast_model(self):

        def assertBroadcastStatus(sms, new_sms_status, broadcast_status):
            sms.status = new_sms_status
            sms.save()
            sms.broadcast.update()
            self.assertEquals(sms.broadcast.status, broadcast_status)

        broadcast = Broadcast.create(self.org, self.user, "Like a tweet", [self.joe_and_frank, self.kevin, self.lucy])
        self.assertEquals('I', broadcast.status)
        self.assertEquals(4, broadcast.recipient_count)

        # no recipients created yet, done when we send
        self.assertEqual(set(broadcast.recipients.all()), set())

        broadcast.send(trigger_send=False)
        self.assertEqual('Q', broadcast.status)
        self.assertEqual(broadcast.get_message_count(), 4)
        self.assertEqual(set(broadcast.recipients.all()), {self.joe, self.frank, self.kevin, self.lucy})

        bcast_commands = broadcast.get_sync_commands(self.channel)
        self.assertEquals(1, len(bcast_commands))
        self.assertEquals(3, len(bcast_commands[0]['to']))

        # set our single message as sent
        broadcast.get_messages().update(status='S')
        self.assertEquals(0, len(broadcast.get_sync_commands(self.channel)))

        # back to Q
        broadcast.get_messages().update(status='Q')

        # after calling send, all messages are queued
        self.assertEquals(broadcast.status, 'Q')

        # test errored broadcast logic now that all sms status are queued
        msgs = broadcast.get_messages()
        assertBroadcastStatus(msgs[0], 'E', 'Q')
        assertBroadcastStatus(msgs[1], 'E', 'Q')
        assertBroadcastStatus(msgs[2], 'E', 'E')  # now more than half are errored
        assertBroadcastStatus(msgs[3], 'E', 'E')

        # test failed broadcast logic now that all sms status are errored
        assertBroadcastStatus(msgs[0], 'F', 'E')
        assertBroadcastStatus(msgs[1], 'F', 'E')
        assertBroadcastStatus(msgs[2], 'F', 'F')  # now more than half are failed
        assertBroadcastStatus(msgs[3], 'F', 'F')

        # first make sure there are no failed messages
        for msg in broadcast.get_messages():
            msg.status = 'S'
            msg.save()

        assertBroadcastStatus(broadcast.get_messages()[0], 'Q', 'Q')
        # test queued broadcast logic

        # test sent broadcast logic
        broadcast.get_messages().update(status='D')
        assertBroadcastStatus(broadcast.get_messages()[0], 'S', 'S')

        # test delivered broadcast logic
        assertBroadcastStatus(broadcast.get_messages()[0], 'D', 'D')

        self.assertEquals("Temba (%d)" % broadcast.id, str(broadcast))

    def test_send(self):
        # remove all channels first
        for channel in Channel.objects.all():
            channel.release(notify_mage=False)

        send_url = reverse('msgs.broadcast_send')
        self.login(self.admin)

        # try with no channel
        post_data = dict(text="some text", omnibox="c-%s" % self.joe.uuid)
        response = self.client.post(send_url, post_data, follow=True)
        self.assertContains(response, "You must add a phone number before sending messages", status_code=400)

        # test when we are simulating
        response = self.client.get(send_url + "?simulation=true")
        self.assertEquals(['omnibox', 'text', 'schedule'], response.context['fields'])

        test_contact = Contact.get_test_contact(self.admin)

        post_data = dict(text="you simulator display this", omnibox="c-%s,c-%s,c-%s" % (self.joe.uuid, self.frank.uuid, test_contact.uuid))
        self.client.post(send_url + "?simulation=true", post_data)
        self.assertEquals(Broadcast.objects.all().count(), 1)
        self.assertEquals(Broadcast.objects.all()[0].groups.all().count(), 0)
        self.assertEquals(Broadcast.objects.all()[0].contacts.all().count(), 1)
        self.assertEquals(Broadcast.objects.all()[0].contacts.all()[0], test_contact)

        # delete this broadcast to keep future test right
        Broadcast.objects.all()[0].delete()

        # test when we have many channels
        Channel.create(self.org, self.user, None, "A", secret="123456", gcm_id="1234")
        Channel.create(self.org, self.user, None, "A", secret="12345", gcm_id="123")
        Channel.create(self.org, self.user, None, "TT")

        response = self.client.get(send_url)
        self.assertEquals(['omnibox', 'text', 'schedule'], response.context['fields'])

        post_data = dict(text="message #1", omnibox="g-%s,c-%s,c-%s" % (self.joe_and_frank.uuid, self.joe.uuid, self.lucy.uuid))
        self.client.post(send_url, post_data, follow=True)
        broadcast = Broadcast.objects.get(text="message #1")
        self.assertEquals(1, broadcast.groups.count())
        self.assertEquals(2, broadcast.contacts.count())
        self.assertIsNotNone(Msg.all_messages.filter(contact=self.joe, text="message #1"))
        self.assertIsNotNone(Msg.all_messages.filter(contact=self.frank, text="message #1"))
        self.assertIsNotNone(Msg.all_messages.filter(contact=self.lucy, text="message #1"))

        # test with one channel now
        for channel in Channel.objects.all():
            channel.release(notify_mage=False)

        Channel.create(self.org, self.user, None, 'A', None, secret="12345", gcm_id="123")

        response = self.client.get(send_url)
        self.assertEquals(['omnibox', 'text', 'schedule'], response.context['fields'])

        post_data = dict(text="message #2", omnibox='g-%s,c-%s' % (self.joe_and_frank.uuid, self.kevin.uuid))
        self.client.post(send_url, post_data, follow=True)
        broadcast = Broadcast.objects.get(text="message #2")
        self.assertEquals(broadcast.groups.count(), 1)
        self.assertEquals(broadcast.contacts.count(), 1)

        # directly on user page
        post_data = dict(text="contact send", from_contact=True, omnibox="c-%s" % self.kevin.uuid)
        response = self.client.post(send_url, post_data)
        self.assertRedirect(response, reverse('contacts.contact_read', args=[self.kevin.uuid]))
        self.assertEquals(Broadcast.objects.all().count(), 3)

        # test sending to an arbitrary user
        post_data = dict(text="message content", omnibox='n-2065551212')
        self.client.post(send_url, post_data, follow=True)
        self.assertEquals(Broadcast.objects.all().count(), 4)
        self.assertEquals(1, Contact.objects.filter(urns__path='2065551212').count())

        # test missing senders
        post_data = dict(text="message content")
        response = self.client.post(send_url, post_data, follow=True)
        self.assertIn("At least one recipient is required", response.content)

        # Test AJAX sender
        post_data = dict(text="message content", omnibox='', _format="json")
        response = self.client.post(send_url, post_data, follow=True)
        self.assertIn("At least one recipient is required", response.content)
        self.assertEquals('application/json', response._headers.get('content-type')[1])

        post_data = dict(text="this is a test message", omnibox="c-%s" % self.kevin.uuid, _format="json")
        response = self.client.post(send_url, post_data, follow=True)
        self.assertIn("success", response.content)

        # send using our omnibox
        post_data = dict(text="this is a test message", omnibox="c-%s,g-%s,n-911" % (self.kevin.pk, self.joe_and_frank.pk), _format="json")
        response = self.client.post(send_url, post_data, follow=True)
        self.assertIn("success", response.content)

    def test_unreachable(self):
        no_urns = Contact.get_or_create(self.org, self.admin, name="Ben Haggerty", urns=[])
        tel_contact = self.create_contact("Ryan Lewis", number="+12067771234")
        twitter_contact = self.create_contact("Lucy", twitter='lucy')
        recipients = [no_urns, tel_contact, twitter_contact]

        # send a broadcast to all (org has a tel and a twitter channel)
        broadcast = Broadcast.create(self.org, self.admin, "Want to go thrift shopping?", recipients)
        broadcast.send(True)

        # should have only messages for Ryan and Lucy
        msgs = broadcast.msgs.all()
        self.assertEqual(len(msgs), 2)
        self.assertEqual(sorted([m.contact.name for m in msgs]), ["Lucy", "Ryan Lewis"])

        # send another broadcast to all and force use of the twitter channel
        broadcast = Broadcast.create(self.org, self.admin, "Want to go thrift shopping?", recipients, channel=self.twitter)
        broadcast.send(True)

        # should have only one message created to Lucy
        msgs = broadcast.msgs.all()
        self.assertEqual(len(msgs), 1)
        self.assertTrue(msgs[0].contact, twitter_contact)

        # remove twitter relayer
        self.twitter.release(trigger_sync=False, notify_mage=False)

        # send another broadcast to all
        broadcast = Broadcast.create(self.org, self.admin, "Want to go thrift shopping?", recipients)
        broadcast.send(True)

        # should have only one message created to Ryan
        msgs = broadcast.msgs.all()
        self.assertEqual(len(msgs), 1)
        self.assertTrue(msgs[0].contact, tel_contact)

    def test_message_parts(self):
        contact = self.create_contact("Matt", "+12067778811")

        sms = self.create_msg(contact=contact, text="Text", direction=OUTGOING)

        self.assertEquals(["Text"], Msg.get_text_parts(sms.text))
        sms.text = ""
        self.assertEquals([""], Msg.get_text_parts(sms.text))

        # 160 chars
        sms.text = "1234567890123456789012345678901234567890123456789012345678901234567890123456789012345678901234567890123456789012345678901234567890123456789012345678901234567890"
        self.assertEquals(1, len(Msg.get_text_parts(sms.text)))

        # 161 characters with space
        sms.text = "123456789012345678901234567890123456789012345678901234567890123456789012345678901234567890123456789012345678901234567890123456789012345678901234567890 1234567890"
        parts = Msg.get_text_parts(sms.text)
        self.assertEquals(2, len(parts))
        self.assertEquals(150, len(parts[0]))
        self.assertEquals(10, len(parts[1]))

        # 161 characters without space
        sms.text = "12345678901234567890123456789012345678901234567890123456789012345678901234567890123456789012345678901234567890123456789012345678901234567890123456789012345678901"
        parts = Msg.get_text_parts(sms.text)
        self.assertEquals(2, len(parts))
        self.assertEquals(160, len(parts[0]))
        self.assertEquals(1, len(parts[1]))

        # 160 characters with max length 40
        sms.text = "1234567890123456789012345678901234567890123456789012345678901234567890123456789012345678901234567890123456789012345678901234567890123456789012345678901234567890"
        parts = Msg.get_text_parts(sms.text, max_length=40)
        self.assertEquals(4, len(parts))
        self.assertEquals(40, len(parts[0]))
        self.assertEquals(40, len(parts[1]))
        self.assertEquals(40, len(parts[2]))
        self.assertEquals(40, len(parts[3]))

    def test_substitute_variables(self):
        ContactField.get_or_create(self.org, self.admin, 'goats', "Goats", False, Value.TYPE_DECIMAL)
        self.joe.set_field(self.user, 'goats', "3 ")
        ContactField.get_or_create(self.org, self.admin, 'dob', "Date of birth", False, Value.TYPE_DATETIME)
        self.joe.set_field(self.user, 'dob', "28/5/1981")

        self.assertEquals(("Hello World", []), Msg.substitute_variables("Hello World", self.joe, dict()))
        self.assertEquals(("Hello World Joe", []), Msg.substitute_variables("Hello World @contact.first_name", self.joe, dict()))
        self.assertEquals(("Hello World Joe Blow", []), Msg.substitute_variables("Hello World @contact", self.joe, dict()))
        self.assertEquals(("Hello World: Well", []), Msg.substitute_variables("Hello World: @flow.water_source", self.joe, dict(flow=dict(water_source="Well"))))
        self.assertEquals(("Hello World: Well  Boil: @flow.boil", ["Undefined variable: flow.boil"]), Msg.substitute_variables("Hello World: @flow.water_source  Boil: @flow.boil", self.joe, dict(flow=dict(water_source="Well"))))

        self.assertEquals(("Hello joe", []), Msg.substitute_variables("Hello @(LOWER(contact.first_name))", self.joe, dict()))
        self.assertEquals(("Hello Joe", []), Msg.substitute_variables("Hello @(PROPER(LOWER(contact.first_name)))", self.joe, dict()))
        self.assertEquals(("Hello Joe", []), Msg.substitute_variables("Hello @(first_word(contact))", self.joe, dict()))
        self.assertEquals(("Hello Blow", []), Msg.substitute_variables("Hello @(Proper(remove_first_word(contact)))", self.joe, dict()))
        self.assertEquals(("Hello Joe Blow", []), Msg.substitute_variables("Hello @(PROPER(contact))", self.joe, dict()))
        self.assertEquals(("Hello JOE", []), Msg.substitute_variables("Hello @(UPPER(contact.first_name))", self.joe, dict()))
        self.assertEquals(("Hello 3", []), Msg.substitute_variables("Hello @(contact.goats)", self.joe, dict()))

        self.assertEquals(("Email is: foo@bar.com", []),
                          Msg.substitute_variables("Email is: @(remove_first_word(flow.sms))", self.joe, dict(flow=dict(sms="Join foo@bar.com"))))
        self.assertEquals(("Email is: foo@@bar.com", []),
                          Msg.substitute_variables("Email is: @(remove_first_word(flow.sms))", self.joe, dict(flow=dict(sms="Join foo@@bar.com"))))

        # check date variables
        text, errors = Msg.substitute_variables("Today is @date.today", self.joe, dict())
        self.assertEquals(errors, [])
        self.assertRegexpMatches(text, "Today is \d\d-\d\d-\d\d\d\d")

        text, errors = Msg.substitute_variables("Today is @date.now", self.joe, dict())
        self.assertEquals(errors, [])
        self.assertRegexpMatches(text, "Today is \d\d-\d\d-\d\d\d\d \d\d:\d\d")

        text, errors = Msg.substitute_variables("Your DOB is @contact.dob", self.joe, dict())
        self.assertEquals(errors, [])
        # TODO clearly this is not ideal but unavoidable for now as we always add current time to parsed dates
        self.assertRegexpMatches(text, "Your DOB is 28-05-1981 \d\d:\d\d")

        # unicode tests
        self.joe.name = u"شاملیدل عمومی"
        self.joe.save()

        self.assertEquals((u"شاملیدل", []), Msg.substitute_variables("@(first_word(contact))", self.joe, dict()))
        self.assertEquals((u"عمومی", []), Msg.substitute_variables("@(proper(remove_first_word(contact)))", self.joe, dict()))

        # credit card
        self.joe.name = '1234567890123456'
        self.joe.save()
        self.assertEquals(("1 2 3 4 , 5 6 7 8 , 9 0 1 2 , 3 4 5 6", []), Msg.substitute_variables("@(read_digits(contact))", self.joe, dict()))

        # phone number
        self.joe.name = '123456789012'
        self.joe.save()
        self.assertEquals(("1 2 3 , 4 5 6 , 7 8 9 , 0 1 2", []), Msg.substitute_variables("@(read_digits(contact))", self.joe, dict()))

        # triplets
        self.joe.name = '123456'
        self.joe.save()
        self.assertEquals(("1 2 3 , 4 5 6", []), Msg.substitute_variables("@(read_digits(contact))", self.joe, dict()))

        # soc security
        self.joe.name = '123456789'
        self.joe.save()
        self.assertEquals(("1 2 3 , 4 5 , 6 7 8 9", []), Msg.substitute_variables("@(read_digits(contact))", self.joe, dict()))

        # regular number, street address, etc
        self.joe.name = '12345'
        self.joe.save()
        self.assertEquals(("1,2,3,4,5", []), Msg.substitute_variables("@(read_digits(contact))", self.joe, dict()))

        # regular number, street address, etc
        self.joe.name = '123'
        self.joe.save()
        self.assertEquals(("1,2,3", []), Msg.substitute_variables("@(read_digits(contact))", self.joe, dict()))

    def test_message_context(self):
        ContactField.get_or_create(self.org, self.admin, "superhero_name", "Superhero Name")

        self.joe.send("keyword remainder-remainder", self.admin)
        self.joe.set_field(self.user, 'superhero_name', 'batman')
        self.joe.save()

        msg = Msg.all_messages.get()
        context = msg.build_message_context()

        self.assertEqual(context['__default__'], "keyword remainder-remainder")
        self.assertEqual(context['value'], "keyword remainder-remainder")
        self.assertEqual(context['contact']['__default__'], "Joe Blow")
        self.assertEqual(context['contact']['superhero_name'], "batman")

        # time should be in org format and timezone
        msg_time = datetime_to_str(msg.created_on, '%d-%m-%Y %H:%M', tz=pytz.timezone(self.org.timezone))
        self.assertEqual(msg_time, context['time'])

    def test_variables_substitution(self):
        ContactField.get_or_create(self.org, self.admin, "sector", "sector")
        ContactField.get_or_create(self.org, self.admin, "team", "team")

        self.joe.set_field(self.user, "sector", "Kacyiru")
        self.frank.set_field(self.user, "sector", "Remera")
        self.kevin.set_field(self.user, "sector", "Kanombe")

        self.joe.set_field(self.user, "team", "Amavubi")
        self.kevin.set_field(self.user, "team", "Junior")

        self.broadcast = Broadcast.create(self.org, self.user,
                                          "Hi @contact.name, You live in @contact.sector and your team is @contact.team.",
                                          [self.joe_and_frank, self.kevin])
        self.broadcast.send(trigger_send=False)

        # there should be three broadcast objects
        broadcast_groups = self.broadcast.get_sync_commands(self.channel)
        self.assertEquals(3, len(broadcast_groups))

        # no message created for Frank because he misses some fields for variables substitution
        self.assertEquals(Msg.all_messages.all().count(), 3)

        sms_to_joe = Msg.all_messages.get(contact=self.joe)
        sms_to_frank = Msg.all_messages.get(contact=self.frank)
        sms_to_kevin = Msg.all_messages.get(contact=self.kevin)

        self.assertEquals(sms_to_joe.text, 'Hi Joe Blow, You live in Kacyiru and your team is Amavubi.')
        self.assertFalse(sms_to_joe.has_template_error)
        self.assertEquals(sms_to_frank.text, 'Hi Frank Blow, You live in Remera and your team is .')
        self.assertFalse(sms_to_frank.has_template_error)
        self.assertEquals(sms_to_kevin.text, 'Hi Kevin Durant, You live in Kanombe and your team is Junior.')
        self.assertFalse(sms_to_kevin.has_template_error)

    def test_purge(self):
        broadcast = Broadcast.create(self.org, self.user, "I think I'm going to purge",
                                     [self.joe_and_frank, self.kevin, self.lucy])

        broadcast.send(trigger_send=False)
        broadcast.created_on = timezone.now() - timedelta(days=100)
        broadcast.save()

        purge_broadcasts_task()

        broadcast.refresh_from_db()
        self.assertTrue(broadcast.purged)
        self.assertEquals(4, len(broadcast.msgs.filter(purged=True)))


class BroadcastCRUDLTest(TembaTest):
    def setUp(self):
        super(BroadcastCRUDLTest, self).setUp()

        self.joe = Contact.get_or_create(self.org, self.user, name="Joe Blow", urns=["tel:123"])
        self.frank = Contact.get_or_create(self.org, self.user, name="Frank Blow", urns=["tel:1234"])

    def test_send(self):
        url = reverse('msgs.broadcast_send')

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
        post_data = dict(omnibox="g-%s,c-%s,n-0780000001" % (just_joe.uuid, self.frank.uuid),
                         text="Hey Joe, where you goin' with that gun in your hand?")
        response = self.client.post(url + '?_format=json', post_data)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(json.loads(response.content)['status'], 'success')

        # raw number means a new contact created
        new_urn = ContactURN.objects.get(path='+250780000001')
        Contact.objects.get(urns=new_urn)

        broadcast = Broadcast.objects.get()
        self.assertEqual(broadcast.text, "Hey Joe, where you goin' with that gun in your hand?")
        self.assertEqual(set(broadcast.groups.all()), {just_joe})
        self.assertEqual(set(broadcast.contacts.all()), {self.frank})
        self.assertEqual(set(broadcast.urns.all()), {new_urn})

    def test_update(self):
        self.login(self.editor)
        self.client.post(reverse('msgs.broadcast_send'), dict(omnibox="c-%s" % self.joe.uuid,
                                                              text="Lunch reminder", schedule=True))
        broadcast = Broadcast.objects.get()
        url = reverse('msgs.broadcast_update', args=[broadcast.pk])

        response = self.client.get(url)
        self.assertEqual(response.context['form'].fields.keys(), ['message', 'omnibox', 'loc'])

        response = self.client.post(url, dict(message="Dinner reminder", omnibox="c-%s" % self.frank.uuid))
        self.assertEqual(response.status_code, 302)

        broadcast = Broadcast.objects.get()
        self.assertEqual(broadcast.text, "Dinner reminder")
        self.assertEqual(set(broadcast.contacts.all()), {self.frank})

    def test_schedule_list(self):
        url = reverse('msgs.broadcast_schedule_list')

        # can't view if you're not logged in
        response = self.client.get(url)
        self.assertLoginRedirect(response)

        self.login(self.editor)

        # send some messages - one immediately, one scheduled
        self.client.post(reverse('msgs.broadcast_send'), dict(omnibox="c-%s" % self.joe.uuid,
                                                              text="See you later"))
        self.client.post(reverse('msgs.broadcast_send'), dict(omnibox="c-%s" % self.joe.uuid,
                                                              text="Lunch reminder", schedule=True))

        scheduled = Broadcast.objects.exclude(schedule=None).first()

        response = self.client.get(url)
        self.assertEqual(set(response.context['object_list']), {scheduled})

    def test_schedule_read(self):
        self.login(self.editor)
        self.client.post(reverse('msgs.broadcast_send'), dict(omnibox="c-%s" % self.joe.uuid,
                                                              text="Lunch reminder", schedule=True))
        broadcast = Broadcast.objects.get()

        # view with empty Send History
        response = self.client.get(reverse('msgs.broadcast_schedule_read', args=[broadcast.pk]))
        self.assertEqual(response.context['object'], broadcast)

        self.assertEqual(response.context['object_list'].count(), 0)

        broadcast.fire()

        # view again with 1 item in Send History
        response = self.client.get(reverse('msgs.broadcast_schedule_read', args=[broadcast.pk]))
        self.assertEqual(response.context['object'], broadcast)
        self.assertEqual(response.context['object_list'].count(), 1)


class LabelTest(TembaTest):

    def setUp(self):
        super(LabelTest, self).setUp()

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
        self.assertTrue(Label.is_valid_name('x'))
        self.assertTrue(Label.is_valid_name('1'))
        self.assertTrue(Label.is_valid_name('x' * 64))
        self.assertFalse(Label.is_valid_name(' '))
        self.assertFalse(Label.is_valid_name(' x'))
        self.assertFalse(Label.is_valid_name('x '))
        self.assertFalse(Label.is_valid_name('+x'))
        self.assertFalse(Label.is_valid_name('@x'))
        self.assertFalse(Label.is_valid_name('x' * 65))

    def test_toggle_label(self):
        label = Label.get_or_create(self.org, self.user, "Spam")
        msg1 = self.create_msg(text="Message 1", contact=self.joe, direction='I')
        msg2 = self.create_msg(text="Message 2", contact=self.joe, direction='I')
        msg3 = self.create_msg(text="Message 3", contact=self.joe, direction='I')

        self.assertEqual(label.get_visible_count(), 0)

        label.toggle_label([msg1, msg2, msg3], add=True)  # add label to 3 messages

        label = Label.label_objects.get(pk=label.pk)
        self.assertEqual(label.get_visible_count(), 3)
        self.assertEqual(set(label.get_messages()), {msg1, msg2, msg3})

        label.toggle_label([msg3], add=False)  # remove label from a message

        label = Label.label_objects.get(pk=label.pk)
        self.assertEqual(label.get_visible_count(), 2)
        self.assertEqual(set(label.get_messages()), {msg1, msg2})

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

        # can't label test messages
        msg4 = self.create_msg(text="Message", contact=Contact.get_test_contact(self.user), direction='I')
        self.assertRaises(ValueError, label.toggle_label, [msg4], add=True)

        # can't label outgoing messages
        msg5 = self.create_msg(text="Message", contact=self.joe, direction='O')
        self.assertRaises(ValueError, label.toggle_label, [msg5], add=True)

        # can't get a count of a folder
        folder = Label.get_or_create_folder(self.org, self.user, "Folder")
        self.assertRaises(ValueError, folder.get_visible_count)

    def test_get_messages_and_hierarchy(self):
        folder1 = Label.get_or_create_folder(self.org, self.user, "Sorted")
        folder2 = Label.get_or_create_folder(self.org, self.user, "Todo")
        label1 = Label.get_or_create(self.org, self.user, "Spam", folder1)
        label2 = Label.get_or_create(self.org, self.user, "Social", folder1)
        label3 = Label.get_or_create(self.org, self.user, "Other")

        msg1 = self.create_msg(text="Message 1", contact=self.joe, direction='I')
        msg2 = self.create_msg(text="Message 2", contact=self.joe, direction='I')
        msg3 = self.create_msg(text="Message 3", contact=self.joe, direction='I')

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
            self.assertEqual(list(hierarchy), [label3, folder1, folder2])
            self.assertEqual(list(hierarchy[1].children.all()), [label2, label1])

    def test_delete_folder(self):
        folder1 = Label.get_or_create_folder(self.org, self.user, "Folder")
        label1 = Label.get_or_create(self.org, self.user, "Spam", folder1)
        label2 = Label.get_or_create(self.org, self.user, "Social", folder1)
        label3 = Label.get_or_create(self.org, self.user, "Other")

        msg1 = self.create_msg(text="Message 1", contact=self.joe, direction='I')
        msg2 = self.create_msg(text="Message 2", contact=self.joe, direction='I')
        msg3 = self.create_msg(text="Message 3", contact=self.joe, direction='I')

        label1.toggle_label([msg1, msg2], add=True)
        label2.toggle_label([msg1], add=True)
        label3.toggle_label([msg3], add=True)

        folder1.delete()

        self.assertFalse(Label.all_objects.filter(pk=folder1.pk).exists())

        # check that contained labels are also deleted
        self.assertEqual(Label.all_objects.filter(pk__in=[label1.pk, label2.pk]).count(), 0)
        self.assertEqual(set(Msg.all_messages.get(pk=msg1.pk).labels.all()), set())
        self.assertEqual(set(Msg.all_messages.get(pk=msg2.pk).labels.all()), set())
        self.assertEqual(set(Msg.all_messages.get(pk=msg3.pk).labels.all()), {label3})

        label3.delete()

        self.assertFalse(Label.all_objects.filter(pk=label3.pk).exists())
        self.assertEqual(set(Msg.all_messages.get(pk=msg3.pk).labels.all()), set())


class LabelCRUDLTest(TembaTest):

    def test_create_and_update(self):
        create_label_url = reverse('msgs.label_create')
        create_folder_url = reverse('msgs.label_create_folder')

        self.login(self.admin)

        # try to create label with invalid name
        response = self.client.post(create_label_url, dict(name="+label_one"))
        self.assertFormError(response, 'form', 'name', "Name must not be blank or begin with punctuation")

        # try again with valid name
        self.client.post(create_label_url, dict(name="label_one"), follow=True)

        label_one = Label.label_objects.get()
        self.assertEqual(label_one.name, "label_one")
        self.assertIsNone(label_one.folder)

        # check that we can't create another with same name
        response = self.client.post(create_label_url, dict(name="label_one"))
        self.assertFormError(response, 'form', 'name', "Name must be unique")

        # create a folder
        self.client.post(create_folder_url, dict(name="Folder"), follow=True)
        folder = Label.folder_objects.get(name="Folder")

        # and a label in it
        self.client.post(create_label_url, dict(name="label_two", folder=folder.pk), follow=True)
        label_two = Label.label_objects.get(name="label_two")
        self.assertEqual(label_two.folder, folder)

        # update label one
        self.client.post(reverse('msgs.label_update', args=[label_one.pk]), dict(name="label_1"))

        label_one = Label.label_objects.get(pk=label_one.pk)
        self.assertEqual(label_one.name, "label_1")
        self.assertIsNone(label_one.folder)

        # try to update to invalid label name
        response = self.client.post(reverse('msgs.label_update', args=[label_one.pk]), dict(name="+label_1"))
        self.assertFormError(response, 'form', 'name', "Name must not be blank or begin with punctuation")

    def test_label_delete(self):
        label_one = Label.get_or_create(self.org, self.user, "label1")

        delete_url = reverse('msgs.label_delete', args=[label_one.pk])

        self.login(self.user)
        response = self.client.get(delete_url)
        self.assertEquals(response.status_code, 302)

        self.login(self.admin)
        response = self.client.get(delete_url)
        self.assertEquals(response.status_code, 200)

    def test_list(self):
        folder = Label.get_or_create_folder(self.org, self.user, "Folder")
        Label.get_or_create(self.org, self.user, "Spam", folder=folder)
        Label.get_or_create(self.org, self.user, "Junk", folder=folder)
        Label.get_or_create(self.org, self.user, "Important")

        self.create_secondary_org()
        Label.get_or_create(self.org2, self.admin2, "Other Org")

        # viewers can't edit flows so don't have access to this JSON endpoint as that's only place it's used
        self.login(self.user)
        response = self.client.get(reverse('msgs.label_list'))
        self.assertLoginRedirect(response)

        # editors can though
        self.login(self.editor)
        response = self.client.get(reverse('msgs.label_list'))
        results = json.loads(response.content)

        # results should be A-Z and not include folders or labels from other orgs
        self.assertEqual(len(results), 3)
        self.assertEqual(results[0]['text'], "Important")
        self.assertEqual(results[1]['text'], "Junk")
        self.assertEqual(results[2]['text'], "Spam")


class ScheduleTest(TembaTest):

    def tearDown(self):
        from temba.channels import models as channel_models
        channel_models.SEND_QUEUE_DEPTH = 500
        channel_models.SEND_BATCH_SIZE = 100

        Broadcast.BULK_THRESHOLD = 50

    def test_batch(self):
        Broadcast.BULK_THRESHOLD = 10

        # broadcast out to 11 contacts to test our batching
        contacts = []
        for i in range(1, 12):
            contacts.append(self.create_contact("Contact %d" % i, "+250788123%d" % i))
        batch_group = self.create_group("Batch Group", contacts)

        # create our broadcast
        broadcast = Broadcast.create(self.org, self.admin, 'Many message but only 5 batches.', [batch_group])

        self.channel.channel_type = 'EX'
        self.channel.save()

        # create our messages, but don't sync
        broadcast.send(trigger_send=False)

        # get one of our messages, should be at bulk priority since it was in a broadcast over our bulk threshold
        sms = broadcast.get_messages()[0]
        self.assertEqual(sms.priority, Msg.PRIORITY_BULK)

        # we should now have 11 messages pending
        self.assertEquals(11, Msg.all_messages.filter(channel=self.channel, status=PENDING).count())

        # let's trigger a sending of the messages
        self.org.trigger_send()

        # we should now have 11 messages that have sent
        self.assertEquals(11, Msg.all_messages.filter(channel=self.channel, status=WIRED).count())


class ConsoleTest(TembaTest):

    def setUp(self):
        from temba.triggers.models import Trigger

        super(ConsoleTest, self).setUp()
        self.create_secondary_org()

        # create a new console
        self.console = MessageConsole(self.org, "tel:+250788123123")

        # a few test contacts
        self.john = self.create_contact("John Doe", "0788123123")

        # create a flow and set "color" as its trigger
        self.flow = self.create_flow()
        Trigger.objects.create(flow=self.flow, keyword="color", created_by=self.admin, modified_by=self.admin, org=self.org)

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
        self.assertEquals(self.console.org, self.org)

        # try changing it with something empty
        self.console.do_org("")
        self.assertEchoed("Select org", clear=False)
        self.assertEchoed("Temba")

        # shouldn't have changed current org
        self.assertEquals(self.console.org, self.org)

        # try changing entirely
        self.console.do_org("%d" % self.org2.id)
        self.assertEchoed("You are now sending messages for Trileet Inc.")
        self.assertEquals(self.console.org, self.org2)
        self.assertEquals(self.console.contact.org, self.org2)

        # back to temba
        self.console.do_org("%d" % self.org.id)
        self.assertEquals(self.console.org, self.org)
        self.assertEquals(self.console.contact.org, self.org)

        # contact help
        self.console.do_contact("")
        self.assertEchoed("Set contact by")

        # switch our contact
        self.console.do_contact("0788123123")
        self.assertEchoed("You are now sending as John")
        self.assertEquals(self.console.contact, self.john)

        # send a message
        self.console.default("Hello World")
        self.assertEchoed("Hello World")

        # make sure the message was created for our contact and handled
        msg = Msg.all_messages.get()
        self.assertEquals(msg.text, "Hello World")
        self.assertEquals(msg.contact, self.john)
        self.assertEquals(msg.status, HANDLED)

        # now trigger a flow
        self.console.default("Color")
        self.assertEchoed("What is your favorite color?")


class BroadcastLanguageTest(TembaTest):

    def setUp(self):
        super(BroadcastLanguageTest, self).setUp()

        self.francois = self.create_contact('Francois', '+12065551213')
        self.francois.language = 'fre'
        self.francois.save()

        self.greg = self.create_contact('Greg', '+12065551212')

        self.wilbert = self.create_contact('Wilbert', '+12065551214')
        self.wilbert.language = 'fre'
        self.wilbert.save()

    def test_multiple_language_broadcast(self):
        # set up our org to have a few different languages
        eng = Language.create(self.org, self.admin, "English", 'eng')
        Language.create(self.org, self.admin, "French", 'fre')
        self.org.primary_language = eng
        self.org.save()

        eng_msg = "This is my message"
        fre_msg = "Ceci est mon message"

        # now create a broadcast with a couple contacts, one with an explicit language, the other not
        bcast = Broadcast.create(self.org, self.admin, "This is my new message",
                                 [self.francois, self.greg, self.wilbert],
                                 language_dict=json.dumps(dict(eng=eng_msg, fre=fre_msg)))

        bcast.send()

        # assert the right language was used for each contact
        self.assertEquals(fre_msg, Msg.all_messages.get(contact=self.francois).text)
        self.assertEquals(eng_msg, Msg.all_messages.get(contact=self.greg).text)
        self.assertEquals(fre_msg, Msg.all_messages.get(contact=self.wilbert).text)


class SystemLabelTest(TembaTest):
    def test_get_counts(self):
        self.assertEqual(SystemLabel.get_counts(self.org), {SystemLabel.TYPE_INBOX: 0, SystemLabel.TYPE_FLOWS: 0,
                                                            SystemLabel.TYPE_ARCHIVED: 0, SystemLabel.TYPE_OUTBOX: 0,
                                                            SystemLabel.TYPE_SENT: 0, SystemLabel.TYPE_FAILED: 0,
                                                            SystemLabel.TYPE_SCHEDULED: 0, SystemLabel.TYPE_CALLS: 0})

        contact1 = self.create_contact("Bob", number="0783835001")
        contact2 = self.create_contact("Jim", number="0783835002")
        msg1 = Msg.create_incoming(self.channel, "tel:0783835001", text="Message 1")
        Msg.create_incoming(self.channel, "tel:0783835001", text="Message 2")
        msg3 = Msg.create_incoming(self.channel, "tel:0783835001", text="Message 3")
        msg4 = Msg.create_incoming(self.channel, "tel:0783835001", text="Message 4")
        call1 = ChannelEvent.create(self.channel, "tel:0783835001", ChannelEvent.TYPE_CALL_IN, timezone.now(), 10)
        bcast1 = Broadcast.create(self.org, self.user, "Broadcast 1", [contact1, contact2])
        Broadcast.create(self.org, self.user, "Broadcast 2", [contact1, contact2],
                         schedule=Schedule.create_schedule(timezone.now(), 'D', self.user))

        # create a broadcast with a test contact to make sure they aren't included
        test_bcast = Broadcast.create(self.org, self.user, "Test Broadcast", [Contact.get_test_contact(self.admin)])

        # this will create some test outgoing messages as well
        test_bcast.send()

        self.assertEqual(SystemLabel.get_counts(self.org), {SystemLabel.TYPE_INBOX: 4, SystemLabel.TYPE_FLOWS: 0,
                                                            SystemLabel.TYPE_ARCHIVED: 0, SystemLabel.TYPE_OUTBOX: 0,
                                                            SystemLabel.TYPE_SENT: 0, SystemLabel.TYPE_FAILED: 0,
                                                            SystemLabel.TYPE_SCHEDULED: 1, SystemLabel.TYPE_CALLS: 1})

        msg3.archive()
        bcast1.send(status=QUEUED)
        msg5, msg6 = tuple(Msg.all_messages.filter(broadcast=bcast1))
        ChannelEvent.create(self.channel, "tel:0783835002", ChannelEvent.TYPE_CALL_IN, timezone.now(), 10)
        Broadcast.create(self.org, self.user, "Broadcast 3", [contact1],
                         schedule=Schedule.create_schedule(timezone.now(), 'W', self.user))

        self.assertEqual(SystemLabel.get_counts(self.org), {SystemLabel.TYPE_INBOX: 3, SystemLabel.TYPE_FLOWS: 0,
                                                            SystemLabel.TYPE_ARCHIVED: 1, SystemLabel.TYPE_OUTBOX: 2,
                                                            SystemLabel.TYPE_SENT: 0, SystemLabel.TYPE_FAILED: 0,
                                                            SystemLabel.TYPE_SCHEDULED: 2, SystemLabel.TYPE_CALLS: 2})

        msg1.archive()
        msg3.release()  # deleting an archived msg
        msg4.release()  # deleting a visible msg
        msg5.fail()
        msg6.status_sent()
        call1.release()

        self.assertEqual(SystemLabel.get_counts(self.org), {SystemLabel.TYPE_INBOX: 1, SystemLabel.TYPE_FLOWS: 0,
                                                            SystemLabel.TYPE_ARCHIVED: 1, SystemLabel.TYPE_OUTBOX: 0,
                                                            SystemLabel.TYPE_SENT: 1, SystemLabel.TYPE_FAILED: 1,
                                                            SystemLabel.TYPE_SCHEDULED: 2, SystemLabel.TYPE_CALLS: 1})

        msg1.restore()
        msg3.release()  # already released
        msg5.fail()  # already failed
        msg6.status_delivered()

        self.assertEqual(SystemLabel.get_counts(self.org), {SystemLabel.TYPE_INBOX: 2, SystemLabel.TYPE_FLOWS: 0,
                                                            SystemLabel.TYPE_ARCHIVED: 0, SystemLabel.TYPE_OUTBOX: 0,
                                                            SystemLabel.TYPE_SENT: 1, SystemLabel.TYPE_FAILED: 1,
                                                            SystemLabel.TYPE_SCHEDULED: 2, SystemLabel.TYPE_CALLS: 1})

        msg5.resend()

        self.assertTrue(SystemLabel.objects.all().count() > 8)

        # squash our counts
        squash_systemlabels()

        self.assertEqual(SystemLabel.get_counts(self.org), {SystemLabel.TYPE_INBOX: 2, SystemLabel.TYPE_FLOWS: 0,
                                                            SystemLabel.TYPE_ARCHIVED: 0, SystemLabel.TYPE_OUTBOX: 1,
                                                            SystemLabel.TYPE_SENT: 1, SystemLabel.TYPE_FAILED: 0,
                                                            SystemLabel.TYPE_SCHEDULED: 2, SystemLabel.TYPE_CALLS: 1})

        # we should only have one system label per type
        self.assertEqual(SystemLabel.objects.all().count(), 8)
