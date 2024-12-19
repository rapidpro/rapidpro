from django.urls import reverse

from temba.msgs.models import Label, Msg

from . import APITest


class MessageActionsEndpointTest(APITest):
    def test_endpoint(self):
        endpoint_url = reverse("api.v2.message_actions") + ".json"

        self.assertGetNotAllowed(endpoint_url)
        self.assertPostNotPermitted(endpoint_url, [None, self.user, self.agent])
        self.assertDeleteNotAllowed(endpoint_url)

        # create some messages to act on
        joe = self.create_contact("Joe Blow", phone="+250788123123")
        msg1 = self.create_incoming_msg(joe, "Msg #1")
        msg2 = self.create_incoming_msg(joe, "Msg #2")
        msg3 = self.create_incoming_msg(joe, "Msg #3")
        label = self.create_label("Test")

        # add label by name to messages 1 and 2
        self.assertPost(
            endpoint_url, self.editor, {"messages": [msg1.id, msg2.id], "action": "label", "label": "Test"}, status=204
        )
        self.assertEqual(set(label.get_messages()), {msg1, msg2})

        # add label by its UUID to message 3
        self.assertPost(
            endpoint_url, self.admin, {"messages": [msg3.id], "action": "label", "label": str(label.uuid)}, status=204
        )
        self.assertEqual(set(label.get_messages()), {msg1, msg2, msg3})

        # try to label with an invalid UUID
        self.assertPost(
            endpoint_url,
            self.admin,
            {"messages": [msg1.id], "action": "label", "label": "nope"},
            errors={"label": "No such object: nope"},
        )

        # remove label from message 2 by name (which is case-insensitive)
        self.assertPost(
            endpoint_url,
            self.admin,
            {"messages": [msg2.id], "action": "unlabel", "label": "test"},
            status=204,
        )
        self.assertEqual(set(label.get_messages()), {msg1, msg3})

        # and remove from messages 1 and 3 by UUID
        self.assertPost(
            endpoint_url,
            self.admin,
            {"messages": [msg1.id, msg3.id], "action": "unlabel", "label": str(label.uuid)},
            status=204,
        )
        self.assertEqual(set(label.get_messages()), set())

        # add new label via label_name
        self.assertPost(
            endpoint_url,
            self.admin,
            {"messages": [msg2.id, msg3.id], "action": "label", "label_name": "New"},
            status=204,
        )
        new_label = Label.objects.get(org=self.org, name="New", is_active=True)
        self.assertEqual(set(new_label.get_messages()), {msg2, msg3})

        # no difference if label already exists as it does now
        self.assertPost(
            endpoint_url,
            self.admin,
            {"messages": [msg1.id], "action": "label", "label_name": "New"},
            status=204,
        )
        self.assertEqual(set(new_label.get_messages()), {msg1, msg2, msg3})

        # can also remove by label_name
        self.assertPost(
            endpoint_url,
            self.admin,
            {"messages": [msg3.id], "action": "unlabel", "label_name": "New"},
            status=204,
        )
        self.assertEqual(set(new_label.get_messages()), {msg1, msg2})

        # and no error if label doesn't exist
        self.assertPost(
            endpoint_url,
            self.admin,
            {"messages": [msg3.id], "action": "unlabel", "label_name": "XYZ"},
            status=204,
        )
        # and label not lazy created in this case
        self.assertIsNone(Label.objects.filter(name="XYZ").first())

        # try to use invalid label name
        self.assertPost(
            endpoint_url,
            self.admin,
            {"messages": [msg1.id, msg2.id], "action": "label", "label_name": '"Hi"'},
            errors={"label_name": 'Cannot contain the character: "'},
        )

        # try to label without specifying a label
        self.assertPost(
            endpoint_url,
            self.admin,
            {"messages": [msg1.id, msg2.id], "action": "label"},
            errors={"non_field_errors": 'For action "label" you should also specify a label'},
        )
        self.assertPost(
            endpoint_url,
            self.admin,
            {"messages": [msg1.id, msg2.id], "action": "label", "label": ""},
            errors={"label": "This field may not be null."},
        )

        # try to provide both label and label_name
        self.assertPost(
            endpoint_url,
            self.admin,
            {"messages": [msg1.id], "action": "label", "label": "Test", "label_name": "Test"},
            errors={"non_field_errors": "Can't specify both label and label_name."},
        )

        # archive all messages
        self.assertPost(
            endpoint_url, self.admin, {"messages": [msg1.id, msg2.id, msg3.id], "action": "archive"}, status=204
        )
        self.assertEqual(set(Msg.objects.filter(visibility=Msg.VISIBILITY_ARCHIVED)), {msg1, msg2, msg3})

        # restore message 1
        self.assertPost(endpoint_url, self.admin, {"messages": [msg1.id], "action": "restore"}, status=204)
        self.assertEqual(set(Msg.objects.filter(visibility=Msg.VISIBILITY_VISIBLE)), {msg1})
        self.assertEqual(set(Msg.objects.filter(visibility=Msg.VISIBILITY_ARCHIVED)), {msg2, msg3})

        # delete messages 2
        self.assertPost(endpoint_url, self.admin, {"messages": [msg2.id], "action": "delete"}, status=204)
        self.assertEqual(set(Msg.objects.filter(visibility=Msg.VISIBILITY_VISIBLE)), {msg1})
        self.assertEqual(set(Msg.objects.filter(visibility=Msg.VISIBILITY_ARCHIVED)), {msg3})
        self.assertEqual(set(Msg.objects.filter(visibility=Msg.VISIBILITY_DELETED_BY_USER)), {msg2})

        # try to act on a a valid message and a deleted message
        response = self.assertPost(
            endpoint_url, self.admin, {"messages": [msg2.id, msg3.id], "action": "restore"}, status=200
        )

        # should get a partial success
        self.assertEqual(response.json(), {"failures": [msg2.id]})
        self.assertEqual(set(Msg.objects.filter(visibility=Msg.VISIBILITY_VISIBLE)), {msg1, msg3})

        # try to act on an outgoing message
        msg4 = self.create_outgoing_msg(joe, "Hi Joe")
        self.assertPost(
            endpoint_url,
            self.admin,
            {"messages": [msg1.id, msg4.id], "action": "archive"},
            errors={"messages": f"Not an incoming message: {msg4.id}"},
        )

        # try to provide a label for a non-labelling action
        self.assertPost(
            endpoint_url,
            self.admin,
            {"messages": [msg1.id], "action": "archive", "label": "Test"},
            errors={"non_field_errors": 'For action "archive" you should not specify a label'},
        )

        # try to invoke an invalid action
        self.assertPost(
            endpoint_url,
            self.admin,
            {"messages": [msg1.id], "action": "like"},
            errors={"action": '"like" is not a valid choice.'},
        )
