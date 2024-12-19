from datetime import date, timedelta
from unittest.mock import call

from django.urls import reverse
from django.utils import timezone

from temba.msgs.models import Broadcast, MessageExport, Msg
from temba.orgs.models import Export
from temba.tests import CRUDLTestMixin, TembaTest, mock_mailroom
from temba.utils.views.mixins import TEMBA_MENU_SELECTION


class MsgCRUDLTest(TembaTest, CRUDLTestMixin):
    def test_menu(self):
        menu_url = reverse("msgs.msg_menu")

        contact = self.create_contact("Joe Blow", phone="+250788000001")
        spam = self.create_label("Spam")
        msg1 = self.create_incoming_msg(contact, "Hi")
        spam.toggle_label([msg1], add=True)

        self.assertRequestDisallowed(menu_url, [None, self.agent])
        self.assertPageMenu(
            menu_url,
            self.admin,
            [
                "Inbox (1)",
                "Handled (0)",
                "Archived (0)",
                "Outbox (0)",
                "Sent (0)",
                "Failed (0)",
                "Scheduled (0)",
                "Broadcasts",
                "Templates",
                "Calls (0)",
                ("Labels", ["Spam (1)"]),
            ],
        )

    def test_inbox(self):
        contact1 = self.create_contact("Joe Blow", phone="+250788000001")
        contact2 = self.create_contact("Frank", phone="+250788000002")
        msg1 = self.create_incoming_msg(contact1, "message number 1")
        msg2 = self.create_incoming_msg(contact1, "message number 2")
        msg3 = self.create_incoming_msg(contact2, "message number 3")
        msg4 = self.create_incoming_msg(contact2, "message number 4")
        msg5 = self.create_incoming_msg(contact2, "message number 5", visibility="A")
        self.create_incoming_msg(contact2, "message number 6", status=Msg.STATUS_PENDING)

        inbox_url = reverse("msgs.msg_inbox")

        # check query count
        self.login(self.admin)
        with self.assertNumQueries(12):
            self.client.get(inbox_url)

        self.assertRequestDisallowed(inbox_url, [None, self.agent])
        response = self.assertListFetch(
            inbox_url + "?refresh=10000", [self.user, self.editor, self.admin], context_objects=[msg4, msg3, msg2, msg1]
        )

        # check that we have the appropriate bulk actions
        self.assertEqual(("archive", "label"), response.context["actions"])

        # test searching
        response = self.client.get(inbox_url + "?search=joe")
        self.assertEqual([msg2, msg1], list(response.context_data["object_list"]))

        # add some labels
        label1 = self.create_label("label1")
        self.create_label("label2")
        label3 = self.create_label("label3")

        # viewers can't label messages
        response = self.requestView(
            inbox_url, self.user, post_data={"action": "label", "objects": [msg1.id], "label": label1.id, "add": True}
        )
        self.assertEqual(403, response.status_code)

        # but editors can
        response = self.requestView(
            inbox_url,
            self.editor,
            post_data={"action": "label", "objects": [msg1.id, msg2.id], "label": label1.id, "add": True},
        )
        self.assertEqual(200, response.status_code)
        self.assertEqual({msg1, msg2}, set(label1.msgs.all()))

        # and remove labels
        self.requestView(
            inbox_url,
            self.editor,
            post_data={"action": "label", "objects": [msg2.id], "label": label1.id, "add": False},
        )
        self.assertEqual({msg1}, set(label1.msgs.all()))

        # can't label without a label object
        response = self.requestView(
            inbox_url,
            self.editor,
            post_data={"action": "label", "objects": [msg2.id], "add": False},
        )
        self.assertEqual({msg1}, set(label1.msgs.all()))

        # label more messages as admin
        self.requestView(
            inbox_url,
            self.admin,
            post_data={"action": "label", "objects": [msg1.id, msg2.id, msg3.id], "label": label3.id, "add": True},
        )
        self.assertEqual({msg1}, set(label1.msgs.all()))
        self.assertEqual({msg1, msg2, msg3}, set(label3.msgs.all()))

        # test archiving a msg
        self.client.post(inbox_url, {"action": "archive", "objects": msg1.id})
        self.assertEqual({msg1, msg5}, set(Msg.objects.filter(visibility=Msg.VISIBILITY_ARCHIVED)))

        # archiving doesn't remove labels
        msg1.refresh_from_db()
        self.assertEqual({label1, label3}, set(msg1.labels.all()))

        self.assertContentMenu(inbox_url, self.user, ["Export"])
        self.assertContentMenu(inbox_url, self.admin, ["Send", "New Label", "Export"])

    def test_flows(self):
        flow = self.create_flow("Test")
        contact1 = self.create_contact("Joe Blow", phone="+250788000001")
        msg1 = self.create_incoming_msg(contact1, "test 1", status="H", flow=flow)
        msg2 = self.create_incoming_msg(contact1, "test 2", status="H", flow=flow)
        self.create_incoming_msg(contact1, "test 3", status="H", flow=None)
        self.create_incoming_msg(contact1, "test 4", status="P", flow=None)

        flows_url = reverse("msgs.msg_flow")

        # check query count
        self.login(self.admin)
        with self.assertNumQueries(12):
            self.client.get(flows_url)

        self.assertRequestDisallowed(flows_url, [None, self.agent])
        response = self.assertListFetch(flows_url, [self.user, self.editor, self.admin], context_objects=[msg2, msg1])

        self.assertEqual(("archive", "label"), response.context["actions"])

    def test_archived(self):
        contact1 = self.create_contact("Joe Blow", phone="+250788000001")
        contact2 = self.create_contact("Frank", phone="+250788000002")
        msg1 = self.create_incoming_msg(contact1, "message number 1", visibility=Msg.VISIBILITY_ARCHIVED)
        msg2 = self.create_incoming_msg(contact1, "message number 2", visibility=Msg.VISIBILITY_ARCHIVED)
        msg3 = self.create_incoming_msg(contact2, "message number 3", visibility=Msg.VISIBILITY_ARCHIVED)
        msg4 = self.create_incoming_msg(contact2, "message number 4", visibility=Msg.VISIBILITY_DELETED_BY_USER)
        self.create_incoming_msg(contact2, "message number 5", status=Msg.STATUS_PENDING)

        archived_url = reverse("msgs.msg_archived")

        # check query count
        self.login(self.admin)
        with self.assertNumQueries(12):
            self.client.get(archived_url)

        self.assertRequestDisallowed(archived_url, [None, self.agent])
        response = self.assertListFetch(
            archived_url + "?refresh=10000", [self.user, self.editor, self.admin], context_objects=[msg3, msg2, msg1]
        )
        self.assertEqual(("restore", "label", "delete"), response.context["actions"])

        # test searching
        response = self.client.get(archived_url + "?search=joe")
        self.assertEqual([msg2, msg1], list(response.context_data["object_list"]))

        # viewers can't restore messages
        response = self.requestView(archived_url, self.user, post_data={"action": "restore", "objects": [msg1.id]})
        self.assertEqual(403, response.status_code)

        # but editors can
        response = self.requestView(archived_url, self.editor, post_data={"action": "restore", "objects": [msg1.id]})
        self.assertEqual(200, response.status_code)
        self.assertEqual({msg2, msg3}, set(Msg.objects.filter(visibility=Msg.VISIBILITY_ARCHIVED)))

        # can also permanently delete messages
        response = self.requestView(archived_url, self.admin, post_data={"action": "delete", "objects": [msg2.id]})
        self.assertEqual(200, response.status_code)
        self.assertEqual({msg2, msg4}, set(Msg.objects.filter(visibility=Msg.VISIBILITY_DELETED_BY_USER)))
        self.assertEqual({msg3}, set(Msg.objects.filter(visibility=Msg.VISIBILITY_ARCHIVED)))

    def test_outbox(self):
        contact1 = self.create_contact("", phone="+250788382382")
        contact2 = self.create_contact("Joe Blow", phone="+250788000001")
        contact3 = self.create_contact("Frank Blow", phone="+250788000002")

        # create a single message broadcast that's sent but it's message is still not sent
        broadcast1 = self.create_broadcast(
            self.admin,
            {"eng": {"text": "How is it going?"}},
            contacts=[contact1],
            status=Broadcast.STATUS_COMPLETED,
            msg_status=Msg.STATUS_INITIALIZING,
        )
        msg1 = broadcast1.msgs.get()

        outbox_url = reverse("msgs.msg_outbox")

        # check query count
        self.login(self.admin)
        with self.assertNumQueries(11):
            self.client.get(outbox_url)

        # messages sorted by created_on
        self.assertRequestDisallowed(outbox_url, [None, self.agent])
        response = self.assertListFetch(outbox_url, [self.user, self.editor, self.admin], context_objects=[msg1])
        self.assertEqual((), response.context["actions"])

        # create another broadcast this time with 3 messages
        contact4 = self.create_contact("Kevin", phone="+250788000003")
        group = self.create_group("Testers", contacts=[contact2, contact3])
        broadcast2 = self.create_broadcast(
            self.admin,
            {"eng": {"text": "kLab is awesome"}},
            contacts=[contact4],
            groups=[group],
            msg_status=Msg.STATUS_QUEUED,
        )
        msg4, msg3, msg2 = broadcast2.msgs.order_by("-id")

        response = self.assertListFetch(outbox_url, [self.admin], context_objects=[msg4, msg3, msg2, msg1])

        response = self.client.get(outbox_url + "?search=kevin")
        self.assertEqual([Msg.objects.get(contact=contact4)], list(response.context_data["object_list"]))

        response = self.client.get(outbox_url + "?search=joe")
        self.assertEqual([Msg.objects.get(contact=contact2)], list(response.context_data["object_list"]))

        response = self.client.get(outbox_url + "?search=frank")
        self.assertEqual([Msg.objects.get(contact=contact3)], list(response.context_data["object_list"]))

        response = self.client.get(outbox_url + "?search=just")
        self.assertEqual([], list(response.context_data["object_list"]))

        response = self.client.get(outbox_url + "?search=klab")
        self.assertEqual([msg4, msg3, msg2], list(response.context_data["object_list"]))

    def test_sent(self):
        contact1 = self.create_contact("Joe Blow", phone="+250788000001")
        contact2 = self.create_contact("Frank Blow", phone="+250788000002")
        msg1 = self.create_outgoing_msg(contact1, "Hi 1", status="W", sent_on=timezone.now() - timedelta(hours=1))
        msg2 = self.create_outgoing_msg(contact1, "Hi 2", status="S", sent_on=timezone.now() - timedelta(hours=3))
        msg3 = self.create_outgoing_msg(contact2, "Hi 3", status="D", sent_on=timezone.now() - timedelta(hours=2))

        sent_url = reverse("msgs.msg_sent")

        # check query count
        self.login(self.admin)
        with self.assertNumQueries(10):
            self.client.get(sent_url)

        # messages sorted by sent_on
        self.assertRequestDisallowed(sent_url, [None, self.agent])
        response = self.assertListFetch(
            sent_url, [self.user, self.editor, self.admin], context_objects=[msg1, msg3, msg2]
        )

        self.assertContains(response, reverse("channels.channellog_msg", args=[msg1.channel.uuid, msg1.id]))

        response = self.client.get(sent_url + "?search=joe")
        self.assertEqual([msg1, msg2], list(response.context_data["object_list"]))

    @mock_mailroom
    def test_failed(self, mr_mocks):
        contact1 = self.create_contact("Joe Blow", phone="+250788000001")
        msg1 = self.create_outgoing_msg(contact1, "message number 1", status="F")

        failed_url = reverse("msgs.msg_failed")

        # create broadcast and fail the only message
        broadcast = self.create_broadcast(self.admin, {"eng": {"text": "message number 2"}}, contacts=[contact1])
        broadcast.get_messages().update(status="F")
        msg2 = broadcast.get_messages()[0]

        # message without a broadcast
        msg3 = self.create_outgoing_msg(contact1, "messsage number 3", status="F")

        # check query count
        self.login(self.admin)
        with self.assertNumQueries(10):
            self.client.get(failed_url)

        self.assertRequestDisallowed(failed_url, [None, self.agent])
        response = self.assertListFetch(
            failed_url, [self.user, self.editor, self.admin], context_objects=[msg3, msg2, msg1]
        )

        self.assertEqual(("resend",), response.context["actions"])
        self.assertContains(response, reverse("channels.channellog_msg", args=[msg1.channel.uuid, msg1.id]))

        # resend some messages
        self.client.post(failed_url, {"action": "resend", "objects": [msg2.id]})

        self.assertEqual([call(self.org, [msg2])], mr_mocks.calls["msg_resend"])

        # suspended orgs don't see resend as option
        self.org.suspend()

        response = self.client.get(failed_url)
        self.assertNotIn("resend", response.context["actions"])

    def test_filter(self):
        flow = self.create_flow("Flow")
        joe = self.create_contact("Joe Blow", phone="+250788000001")
        frank = self.create_contact("Frank Blow", phone="+250788000002")

        # create labels
        label1 = self.create_label("label1")
        label2 = self.create_label("label2")
        label3 = self.create_label("label3")

        # create some messages
        msg1 = self.create_incoming_msg(joe, "test1")
        msg2 = self.create_incoming_msg(frank, "test2")
        msg3 = self.create_incoming_msg(frank, "test3")
        msg4 = self.create_incoming_msg(joe, "test4", visibility=Msg.VISIBILITY_ARCHIVED)
        msg5 = self.create_incoming_msg(joe, "test5", visibility=Msg.VISIBILITY_DELETED_BY_USER)
        msg6 = self.create_incoming_msg(joe, "IVR test", flow=flow)

        # apply the labels
        label1.toggle_label([msg1, msg2], add=True)
        label2.toggle_label([msg2, msg3], add=True)
        label3.toggle_label([msg1, msg2, msg3, msg4, msg5, msg6], add=True)

        label1_url = reverse("msgs.msg_filter", args=[label1.uuid])
        label3_url = reverse("msgs.msg_filter", args=[label3.uuid])

        # can't visit a filter page as a non-org user
        response = self.requestView(label3_url, self.non_org_user)
        self.assertRedirect(response, reverse("orgs.org_choose"))

        # can as org viewer user
        response = self.requestView(label3_url, self.user, HTTP_X_TEMBA_SPA=1)
        self.assertEqual(f"/msg/labels/{label3.uuid}", response.headers[TEMBA_MENU_SELECTION])
        self.assertEqual(200, response.status_code)
        self.assertEqual(("label",), response.context["actions"])
        self.assertContentMenu(label3_url, self.user, ["Export", "Usages"])  # no update or delete

        # check that non-visible messages are excluded, and messages and ordered newest to oldest
        self.assertEqual([msg6, msg3, msg2, msg1], list(response.context["object_list"]))

        # search on label by contact name
        response = self.client.get(f"{label3_url}?search=joe")
        self.assertEqual({msg1, msg6}, set(response.context_data["object_list"]))

        # check admin users see edit and delete options for labels
        self.assertContentMenu(label1_url, self.admin, ["Edit", "Delete", "-", "Export", "Usages"])

    def test_export(self):
        export_url = reverse("msgs.msg_export")

        label = self.create_label("Test")
        testers = self.create_group("Testers", contacts=[])
        gender = self.create_field("gender", "Gender")

        self.assertRequestDisallowed(export_url, [None, self.agent])
        response = self.assertUpdateFetch(
            export_url + "?l=I",
            [self.user, self.editor, self.admin],
            form_fields=(
                "start_date",
                "end_date",
                "with_fields",
                "with_groups",
                "export_all",
            ),
        )
        self.assertNotContains(response, "already an export in progress")

        # create a dummy export task so that we won't be able to export
        blocking_export = MessageExport.create(
            self.org, self.admin, start_date=date.today() - timedelta(days=7), end_date=date.today()
        )

        response = self.client.get(export_url + "?l=I")
        self.assertContains(response, "already an export in progress")

        # check we can't submit in case a user opens the form and whilst another user is starting an export
        response = self.client.post(
            export_url + "?l=I", {"start_date": "2022-06-28", "end_date": "2022-09-28", "export_all": 1}
        )
        self.assertContains(response, "already an export in progress")
        self.assertEqual(1, Export.objects.count())

        # mark that one as finished so it's no longer a blocker
        blocking_export.status = Export.STATUS_COMPLETE
        blocking_export.save(update_fields=("status",))

        # try to submit with no values
        response = self.client.post(export_url + "?l=I", {})
        self.assertFormError(response.context["form"], "start_date", "This field is required.")
        self.assertFormError(response.context["form"], "end_date", "This field is required.")
        self.assertFormError(response.context["form"], "export_all", "This field is required.")

        # submit for inbox export
        response = self.client.post(
            export_url + "?l=I",
            {
                "start_date": "2022-06-28",
                "end_date": "2022-09-28",
                "with_groups": [testers.id],
                "with_fields": [gender.id],
                "export_all": 0,
            },
        )
        self.assertEqual(200, response.status_code)

        export = Export.objects.exclude(id=blocking_export.id).get()
        self.assertEqual("message", export.export_type)
        self.assertEqual(date(2022, 6, 28), export.start_date)
        self.assertEqual(date(2022, 9, 28), export.end_date)
        self.assertEqual(
            {"with_groups": [testers.id], "with_fields": [gender.id], "label_uuid": None, "system_label": "I"},
            export.config,
        )

        # submit user label export
        response = self.client.post(
            export_url + f"?l={label.uuid}",
            {
                "start_date": "2022-06-28",
                "end_date": "2022-09-28",
                "with_groups": [testers.id],
                "with_fields": [gender.id],
                "export_all": 0,
            },
        )
        self.assertEqual(200, response.status_code)

        export = Export.objects.exclude(id=blocking_export.id).last()
        self.assertEqual(
            {
                "with_groups": [testers.id],
                "with_fields": [gender.id],
                "label_uuid": str(label.uuid),
                "system_label": None,
            },
            export.config,
        )
